# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Reproducible offline CLI with isolated, bounded summarization trials."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import statistics
import subprocess
import sys
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from vss.ingestion import ChunkInfo, VlmChunkResponse
from vss.observability import collect_vss_metrics
from vss.summarization import GeneratedSummary, SummaryGenerator, summarize_video

from .common import environment, import_mode, save, software_hash
from .results import Configuration, RunResult, Trial, compare


class FixtureGenerator(SummaryGenerator):
    """Controlled provider fixture, measuring library overhead without a network."""

    async def generate(
        self,
        events: list[dict[str, Any]],
        *,
        prompt: str | None = None,
        model: str | None = None,
    ) -> GeneratedSummary:
        return GeneratedSummary(
            text="Offline fixture summary.", model="offline-fixture"
        )


def corpus(chunks: int) -> list[VlmChunkResponse]:
    return [
        VlmChunkResponse(
            chunk=ChunkInfo(
                sourceId="offline-corpus-v1",
                chunkIdx=i,
                start_pts=i * 10_000_000_000,
                end_pts=(i * 10 + 5) * 1_000_000_000,
            ),
            vlm_response=json.dumps(
                {"events": [{"description": f"Fixture event {i}."}]}
            ),
        )
        for i in range(chunks)
    ]


async def worker(config: Configuration) -> Trial:
    chunks, generator = corpus(config.chunks), FixtureGenerator()
    for _ in range(config.warmups):
        await summarize_video(chunks, generator=generator)
    cpu_start = os.times()
    with collect_vss_metrics(max_events=256) as metrics:
        result = await summarize_video(chunks, generator=generator)
    cpu_end = os.times()
    content = json.loads(result.choices[0].message.content or "{}")
    latency = next(
        e.value for e in metrics.observations if e.name == "summarization.total"
    )
    rss = None
    if sys.platform in {"darwin", "linux"}:
        import resource

        raw = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        rss = int(raw if sys.platform == "darwin" else raw * 1024)
    return Trial(
        process_id=os.getpid(),
        latency_s=latency,  # type: ignore[arg-type]
        cpu_time_s=(cpu_end.user + cpu_end.system)
        - (cpu_start.user + cpu_start.system),
        rss_peak_bytes=rss,
        rss_scope="process_lifetime" if rss is not None else "unsupported",
        chunks=config.chunks,
        events=content["total_events"],
        collection_complete=metrics.complete,
        observations=metrics.observations,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", choices=["summarization"], default="summarization")
    parser.add_argument("--chunks", type=int, default=20)
    parser.add_argument("--warmups", type=int, default=2)
    parser.add_argument("--repetitions", type=int, default=10)
    parser.add_argument("--timeout", type=float, default=30)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--compare", type=Path)
    parser.add_argument("--preflight", action="store_true")
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    try:
        config = Configuration(
            chunks=args.chunks,
            warmups=args.warmups,
            repetitions=args.repetitions,
            timeout_s=args.timeout,
        )
    except ValidationError:
        parser.error("invalid workload limits, repetition count or timeout")
    if args.worker:
        print(asyncio.run(worker(config)).model_dump_json())
        return 0
    if args.preflight:
        print(environment().model_dump_json(indent=2))
        return 0
    if args.output is None:
        parser.error("--output is required")
    if args.compare is not None and args.output.resolve() == args.compare.resolve():
        parser.error("candidate output must not overwrite its comparison baseline")
    baseline = None
    if args.compare is not None:
        try:
            baseline = RunResult.model_validate_json(args.compare.read_bytes())
        except (OSError, ValidationError):
            parser.error("baseline is unreadable or invalid")
    root = Path(__file__).resolve().parents[1]
    trials: list[Trial] = []
    failure = None
    for _ in range(config.repetitions):
        try:
            child = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "benchmarks.run",
                    "--worker",
                    "--chunks",
                    str(config.chunks),
                    "--warmups",
                    str(config.warmups),
                ],
                cwd=root,
                capture_output=True,
                timeout=config.timeout_s,
                check=True,
            )
            trials.append(Trial.model_validate_json(child.stdout))
        except subprocess.TimeoutExpired:
            failure = "timeout"
            break
        except subprocess.CalledProcessError:
            failure = "child_failure"
            break
        except ValidationError:
            failure = "invalid_child_evidence"
            break
        except KeyboardInterrupt:
            failure = "cancelled"
            break
    source = json.dumps([c.model_dump() for c in corpus(config.chunks)], sort_keys=True)
    result = RunResult(
        status="incomplete" if failure is not None else "ok",
        environment=environment(),
        configuration=config,
        corpus_sha256=hashlib.sha256(source.encode()).hexdigest(),
        software_sha256=software_hash(root),
        lockfile_sha256=hashlib.sha256((root / "uv.lock").read_bytes()).hexdigest(),
        import_origin=import_mode(root),
        trials=tuple(trials),
        failure=failure,  # type: ignore[arg-type]
    )
    if baseline is not None:
        result = result.model_copy(update={"comparison": compare(result, baseline)})
    save(args.output, result)
    if result.trials:
        print(
            f"{result.profile}: {len(result.trials)} trials; "
            f"median {result.median_latency_s:.6f} s; "
            f"range {min(t.latency_s for t in trials):.6f}--"
            f"{max(t.latency_s for t in trials):.6f} s; "
            f"stdev {statistics.pstdev(t.latency_s for t in trials):.6f} s"
        )
    print("Development evidence only; no approved performance acceptance claim.")
    return (
        3
        if failure is not None
        or (result.comparison is not None and result.comparison.status != "descriptive")
        else 0
    )


if __name__ == "__main__":
    raise SystemExit(main())
