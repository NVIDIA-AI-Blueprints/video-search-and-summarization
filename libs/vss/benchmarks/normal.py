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

"""Normal VSS client versus libraries: bounded loopback qualification only."""

from __future__ import annotations

import argparse
import asyncio
import importlib.metadata
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from .artifacts import publish_result, reserve
from .common import environment, software_hash
from .media import file_hash, generated, inspect
from .normal_adapters import (
    http_client,
    legacy_client,
    library_arm,
    normal_arm,
    source_identity,
)
from .normal_fixture import MAX_MEDIA, Fixture
from .normal_results import NormalConfiguration, NormalResult, NormalTrial, coverage
from .processing import preflight
from .resources import ResourceSampler
from .statistics import describe
from .supervisor import TrialCleanupError, isolated_trial
from .video_results import balanced_orders


def readiness(checkout: Path) -> dict:
    try:
        source = source_identity(checkout).model_dump()
    except Exception:
        source = None
    return {
        "scope": "normal_vss_client_component_only",
        "upstream_source": source,
        "requests_installed": importlib.util.find_spec("requests") is not None,
        "fixture_adapter_implemented": True,
        "configured": {
            name: bool(os.getenv(name))
            for name in (
                "M8_VSS_BASE_URL",
                "RTVI_VLM_URL",
                "VLM_BASE_URL",
                "VSS_SUMMARIZATION_BASE_URL",
                "VSS_SUMMARIZATION_MODEL",
            )
        },
        "coverage": [r.model_dump() for r in coverage()],
        "live_execution_qualified": False,
        "acceptance_eligible": False,
    }


async def worker(
    config: NormalConfiguration, checkout: Path, path: Path | None, order: str
) -> NormalTrial:
    root = Path(__file__).resolve().parents[1]
    env = environment()
    data = dict(
        process_id=os.getpid(),
        order=order,
        configuration=config,
        environment=env,
        software_sha256=software_hash(root),
        lockfile_sha256=file_hash(root / "uv.lock"),
    )
    if not importlib.util.find_spec("requests"):
        return NormalTrial(status="unsupported", failure="dependency", **data)
    try:
        data["upstream"] = source_identity(checkout)
        tools, builds = preflight()
        data["native_builds"] = builds
        data["environment"] = env.model_copy(
            update={
                "dependencies": env.dependencies
                | {
                    name: importlib.metadata.version(name)
                    for name in ("requests", "urllib3", "httpx-sse", "psutil")
                }
            }
        )
    except Exception:
        return NormalTrial(status="unsupported", failure="prerequisite", **data)
    with tempfile.TemporaryDirectory(prefix="m8-normal-media-") as temp:
        try:
            source = path if path else generated(Path(temp), "tiny", tools["ffmpeg"])
            if source.stat().st_size > MAX_MEDIA:
                raise ValueError("input admission")
            media, _ = inspect(source, tools)
            data["media"] = media
        except Exception:
            return NormalTrial(status="error", failure="input_or_oracle", **data)
        fixture = Fixture(source, media, config)
        periods, failure, client = [], None, None
        try:
            with fixture:
                data["expected"] = fixture.expected
                with legacy_client(checkout, fixture.url) as baseline:
                    client, transport = await http_client(fixture.url)
                    try:
                        for _ in range(config.warmups):
                            for arm in ("normal_vss", "library"):
                                period = (
                                    await asyncio.to_thread(
                                        normal_arm, baseline, source, config, fixture
                                    )
                                    if arm == "normal_vss"
                                    else await library_arm(
                                        transport, source, config, fixture
                                    )
                                )
                                if period.status != "ok":
                                    failure = "warmup"
                                    break
                            if failure:
                                break
                            fixture.reset()
                        if not failure:
                            with ResourceSampler(0.05) as sampler:
                                for arm in (
                                    ("normal_vss", "library")
                                    if order == "disabled_first"
                                    else ("library", "normal_vss")
                                ):
                                    period = (
                                        await asyncio.to_thread(
                                            normal_arm,
                                            baseline,
                                            source,
                                            config,
                                            fixture,
                                        )
                                        if arm == "normal_vss"
                                        else await library_arm(
                                            transport, source, config, fixture
                                        )
                                    )
                                    periods.append(period)
                                    if period.status != "ok":
                                        failure = "pair"
                            data["resources"] = sampler.trace
                    finally:
                        await client.aclose()
                        data["clients_closed"] = client.is_closed
                # Context exit closed the actual requests sessions as well.
                data["clients_closed"] = (
                    data.get("clients_closed", False) and baseline._m8_sessions_closed
                )
        except Exception:
            failure = "execution"
        data.update(periods=tuple(periods), server_closed=fixture.closed)
        try:
            if (
                source_identity(checkout) != data["upstream"]
                or file_hash(source) != media.sha256
                or software_hash(root) != data["software_sha256"]
                or file_hash(root / "uv.lock") != data["lockfile_sha256"]
            ):
                failure = "source_mutation"
            return NormalTrial(
                status="error" if failure else "ok", failure=failure, **data
            )
        except Exception:
            return NormalTrial(status="error", failure="evidence", **data)


def summary(result: NormalResult) -> dict:
    if result.status != "ok":
        return {
            "status": result.status,
            "failure": result.failure,
            "complete_pairs": sum(t.status == "ok" for t in result.trials),
            "statistics": None,
            "acceptance_eligible": False,
        }
    baseline = [
        next(p.wall_s for p in t.periods if p.arm == "normal_vss")
        for t in result.trials
    ]
    candidate = [
        next(p.wall_s for p in t.periods if p.arm == "library") for t in result.trials
    ]
    return {
        "status": "descriptive_client_fixture_only",
        "scope": "actual_upstream_client_vs_library_over_loopback",
        "normal_vss_wall_s": describe(baseline).model_dump(),
        "library_wall_s": describe(candidate).model_dump(),
        "paired_relative_library_change": describe(
            [c / b - 1 for c, b in zip(candidate, baseline, strict=True)]
        ).model_dump(),
        "model_performance_measured": False,
        "full_lvs_measured": False,
        "resource_scope": "client_and_loopback_fixture_process",
        "acceptance_eligible": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--upstream",
        type=Path,
        default=Path(__file__).resolve().parents[2] / "video-search-and-summarization",
    )
    parser.add_argument("--video", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--preflight", action="store_true")
    parser.add_argument("--report", type=Path)
    parser.add_argument("--repetitions", type=int, default=10)
    parser.add_argument("--warmups", type=int, default=2)
    parser.add_argument("--seed", type=int, default=1729)
    parser.add_argument("--timeout", type=float, default=120)
    parser.add_argument("--fixture-delay-ms", type=int, default=0)
    parser.add_argument(
        "--fault",
        choices=[
            "none",
            "malformed",
            "missing_done",
            "duplicate",
            "wrong_model",
            "wrong_content",
            "delete_failure",
            "delete_conflict",
            "truncated",
        ],
        default="none",
    )
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument(
        "--order", choices=["disabled_first", "enabled_first"], help=argparse.SUPPRESS
    )
    args = parser.parse_args()
    if args.preflight:
        print(json.dumps(readiness(args.upstream), indent=2))
        return 0
    if args.report:
        if args.report.stat().st_size > 8 * 1024**2:
            parser.error("result byte limit")
        result = NormalResult.model_validate_json(args.report.read_bytes())
        print(json.dumps(summary(result), indent=2))
        return 0
    try:
        config = NormalConfiguration(
            repetitions=args.repetitions,
            warmups=args.warmups,
            seed=args.seed,
            fixture_delay_ms=args.fixture_delay_ms,
            fault=args.fault,
            timeout_s=args.timeout,
        )
    except ValueError:
        parser.error("invalid workload limits")
    checkout = args.upstream.resolve()
    source = args.video.resolve() if args.video else None
    if args.worker:
        if args.order is None:
            parser.error("worker order required")
        print(
            asyncio.run(worker(config, checkout, source, args.order)).model_dump_json()
        )
        return 0
    if args.output is None:
        parser.error("--output is required")
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        parser.error("immutable output; select a new name")
    trials, failure = [], "platform" if os.name != "posix" else None
    with reserve(output):
        if not failure:
            for order in balanced_orders(config.repetitions, config.seed):
                command = [
                    sys.executable,
                    "-m",
                    "benchmarks.normal",
                    "--worker",
                    "--upstream",
                    str(checkout),
                    "--order",
                    order,
                    "--repetitions",
                    str(config.repetitions),
                    "--warmups",
                    str(config.warmups),
                    "--seed",
                    str(config.seed),
                    "--fixture-delay-ms",
                    str(config.fixture_delay_ms),
                    "--fault",
                    config.fault,
                    "--timeout",
                    str(config.timeout_s),
                ]
                if source:
                    command += ["--video", str(source)]
                try:
                    raw = isolated_trial(
                        command,
                        cwd=Path(__file__).resolve().parents[1],
                        timeout=config.timeout_s,
                    )
                    trial = NormalTrial.model_validate_json(raw)
                    if trial.configuration != config or trial.order != order:
                        raise ValueError("worker configuration/order mismatch")
                    bounded = NormalResult(
                        status="incomplete",
                        failure="output_budget",
                        configuration=config,
                        trials=tuple(trials + [trial]),
                    )
                    if (
                        len(bounded.model_dump_json(indent=2).encode()) + 1
                        > 8 * 1024**2
                    ):
                        failure = "output_budget"
                        break
                    trials.append(trial)
                    if trial.status != "ok":
                        failure = "worker_failure"
                        break
                except subprocess.TimeoutExpired:
                    failure = "timeout"
                    break
                except TrialCleanupError:
                    failure = "cleanup_failure"
                    break
                except KeyboardInterrupt:
                    failure = "cancelled"
                    break
                except Exception:
                    failure = "invalid_child"
                    break
        try:
            result = NormalResult(
                status="unsupported"
                if failure == "platform"
                or (trials and trials[-1].status == "unsupported")
                else "incomplete"
                if failure
                else "ok",
                failure=failure,
                configuration=config,
                trials=tuple(trials),
                coverage=coverage(completed=failure is None),
            )
        except ValueError:
            result = NormalResult(
                status="incomplete",
                failure="identity",
                configuration=config,
                trials=tuple(trials),
            )
        publish_result(output, result, reserved=True)
    print(json.dumps(summary(result)))
    return 0 if result.status == "ok" else 3


if __name__ == "__main__":
    raise SystemExit(main())
