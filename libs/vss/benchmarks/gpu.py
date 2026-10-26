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

"""Actual PyNv GPU decode, paired collection cost, copied host RGB evidence."""

from __future__ import annotations

import argparse
import asyncio
import importlib.metadata
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from .artifacts import bundle_bytes, publish_bundle, verify_trace
from .common import environment, software_hash
from .diagnostics import tool_diagnostics
from .gpu_resources import GPUSampler, idle_check
from .gpu_results import (
    ArtifactReference,
    GPUConfiguration,
    GPUResult,
    GPUTrial,
    identity,
)
from .media import MediaError, file_hash, generated, inspect
from .processing import preflight
from .resources import ResourceSampler
from .statistics import describe
from .supervisor import TrialCleanupError, isolated_trial
from .video import decode_period
from .video_results import WarmupDiagnostic, balanced_orders


def verify(trial: GPUTrial, artifacts: Path) -> set[str]:
    ids = set()
    for period in trial.periods:
        if period.trace is None:
            continue
        records = verify_trace(artifacts, period.trace, period.metrics)
        groups = {r.run_id for r in records}
        if len(groups) != 1 or ids & groups:
            raise ValueError("GPU collection identity")
        ids |= groups
        if trial.status == "ok":
            if (
                any(r.status != "ok" for r in records)
                or len({r.request_id for r in records}) != 1
                or len({r.source_id for r in records}) != 1
                or any(r.source_id == "unknown" for r in records)
                or len({r.span_id for r in records}) != len(records)
                or {(r.schema_version, r.catalog_version) for r in records}
                not in ({("1.1", "1.1")}, {("1.1", "1.2")})
            ):
                raise ValueError("GPU trace lineage incomplete")
            opened = [r for r in records if r.name == "processing.open"]
            if len(opened) != 1 or opened[0].backend == "unknown":
                raise ValueError("GPU resolved backend lineage missing")
            # Backend identity is deliberately opaque in scalar observations.
            # Frame checks assert actual pynv, then native roles must bind the
            # same resolved backend as open, without decoding its HMAC label.
            if any(
                r.device != "gpu" or r.backend != opened[0].backend
                for r in records
                if r.name.startswith("processing.native.")
            ):
                raise ValueError("GPU trace contains fallback/device mismatch")
            pending = [
                r for r in records if r.name == "processing.native.pending_frames"
            ]
            if not pending or pending[-1].value != 0:
                raise ValueError("GPU pending output remains")
    return ids


def verify_bundle(result: GPUResult, artifacts: Path):
    if artifacts.is_symlink():
        raise ValueError("GPU bundle root symlink")
    bundle_bytes(artifacts)
    expected = set()
    for ref in result.artifacts:
        path = artifacts / ref.name
        if path.stat().st_size != ref.bytes or file_hash(path) != ref.sha256:
            raise ValueError("GPU diagnostic identity mismatch")
        expected.add(ref.name)
    actual = {
        p.relative_to(artifacts).as_posix() for p in artifacts.rglob("*") if p.is_file()
    }
    if actual != expected:
        raise ValueError("GPU bundle inventory mismatch")
    ids = set()
    for trial in result.trials:
        groups = verify(trial, artifacts)
        if groups & ids:
            raise ValueError("GPU run identity reused")
        ids |= groups


def inventory(root: Path):
    bundle_bytes(root)
    return tuple(
        ArtifactReference(
            name=p.relative_to(root).as_posix(),
            bytes=p.stat().st_size,
            sha256=file_hash(p),
        )
        for p in sorted(root.rglob("*"))
        if p.is_file()
    )


async def worker(
    config: GPUConfiguration, source: Path | None, artifacts: Path, order: str
) -> GPUTrial:
    root = Path(__file__).resolve().parents[1]
    data = dict(
        process_id=os.getpid(),
        order=order,
        configuration=config,
        environment=environment(),
        native_builds={},
        software_sha256=software_hash(root),
        lockfile_sha256=file_hash(root / "uv.lock"),
    )
    sampler = None
    try:
        tools, builds = preflight()
        data["native_builds"] = builds
        dependencies = data["environment"].dependencies | {
            name: importlib.metadata.version(name)
            for name in (
                "pynvvideocodec",
                "nvidia-ml-py",
                "opencv-python-headless",
                "psutil",
            )
        }
        data["environment"] = data["environment"].model_copy(
            update={"dependencies": dependencies}
        )

        distribution = importlib.metadata.distribution("pynvvideocodec")
        binaries = sorted(
            Path(distribution.locate_file(f)).resolve()
            for f in distribution.files or ()
            if ".so" in Path(f).name
        )
        if not binaries or len(binaries) > 64:
            raise ValueError("native binary identity")
        import hashlib

        data["native_builds"]["pynv_binary_sha256"] = hashlib.sha256(
            "".join(file_hash(p) for p in binaries).encode()
        ).hexdigest()
        sampler = GPUSampler()
        data["gpu"] = sampler.identity
    except Exception:
        return GPUTrial(status="unsupported", failure="prerequisite", **data)
    with tempfile.TemporaryDirectory(prefix="m8-gpu-") as temporary:
        periods = []
        failure = None
        try:
            with tool_diagnostics(artifacts / "diagnostics" / str(os.getpid())):
                path = (
                    source
                    if source
                    else generated(Path(temporary), config.recipe, tools["ffmpeg"])
                )
                media, oracle = inspect(path, tools)
            data["media"] = media
            with tempfile.TemporaryDirectory(prefix="m8-gpu-warmup-") as warm:
                for _ in range(config.warmups):
                    for enabled in (False, True):
                        period = await decode_period(
                            path,
                            media,
                            oracle,
                            enabled=enabled,
                            artifacts=Path(warm),
                            decoder="pynv",
                        )
                        if period.status != "ok":
                            data["warmup_diagnostic"] = WarmupDiagnostic.model_validate(
                                period.model_dump(
                                    include=set(WarmupDiagnostic.model_fields)
                                )
                            )
                            failure = "warmup"
                            raise ValueError("GPU warmup correctness")
            with sampler, ResourceSampler(0.05) as resources:
                for enabled in (
                    (False, True) if order == "disabled_first" else (True, False)
                ):
                    period = await decode_period(
                        path,
                        media,
                        oracle,
                        enabled=enabled,
                        artifacts=artifacts,
                        decoder="pynv",
                    )
                    periods.append(period)
                    if period.status != "ok":
                        failure = "decode_pair"
            data.update(resources=resources.trace, gpu_trace=sampler.trace)
            if (
                file_hash(path) != media.sha256
                or software_hash(root) != data["software_sha256"]
                or file_hash(root / "uv.lock") != data["lockfile_sha256"]
            ):
                failure = "source_mutation"
        except Exception as error:
            failure = failure or (
                error.reason
                if isinstance(error, MediaError)
                else "execution_or_evidence"
            )
        finally:
            # Constructor initialized NVML even if admission/warmup failed.
            if sampler is not None:
                sampler.close_unused()
        data["periods"] = tuple(periods)
        try:
            trial = GPUTrial(
                status="error" if failure else "ok", failure=failure, **data
            )
            verify(trial, artifacts)
            return trial
        except Exception:
            return GPUTrial(status="error", failure="evidence", **data)


def summary(result: GPUResult) -> dict:
    if result.status != "ok":
        return {"status": result.status, "failure": result.failure, "statistics": None}
    disabled = [
        next(p.wall_s for p in t.periods if p.mode == "disabled") for t in result.trials
    ]
    enabled = [
        next(p.wall_s for p in t.periods if p.mode == "enabled") for t in result.trials
    ]
    return {
        "status": "descriptive_gpu_host_rgb_decode",
        "enabled_fps": describe(
            [
                len(t.media.pts_ns) / e
                for t, e in zip(result.trials, enabled, strict=True)
            ]
        ).model_dump(),
        "collection_overhead": describe(
            [e / d - 1 for d, e in zip(disabled, enabled, strict=True)]
        ).model_dump(),
        "acceptance_eligible": False,
        "model_performance_measured": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sources = parser.add_mutually_exclusive_group()
    sources.add_argument("--video", type=Path)
    sources.add_argument("--recipe", choices=["tiny", "720p", "1080p", "timeline30m"])
    parser.add_argument("--output", type=Path)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--repetitions", type=int, default=10)
    parser.add_argument("--warmups", type=int, default=2)
    parser.add_argument("--seed", type=int, default=1729)
    parser.add_argument("--timeout", type=float, default=180)
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--artifacts", type=Path, help=argparse.SUPPRESS)
    parser.add_argument(
        "--order", choices=["disabled_first", "enabled_first"], help=argparse.SUPPRESS
    )
    args = parser.parse_args()
    if args.report:
        if args.report.stat().st_size > 8 * 1024**2:
            parser.error("result budget")
        result = GPUResult.model_validate_json(args.report.read_bytes())
        verify_bundle(result, args.report.with_suffix(".artifacts"))
        print(json.dumps(summary(result)))
        return 0
    if not args.video and not args.recipe:
        parser.error("media required")
    try:
        config = GPUConfiguration(
            recipe=args.recipe or "local",
            repetitions=args.repetitions,
            warmups=args.warmups,
            seed=args.seed,
            timeout_s=args.timeout,
        )
    except ValueError:
        parser.error("invalid admission")
    if args.worker:
        if args.order is None or args.artifacts is None:
            parser.error("worker protocol required")
        print(
            asyncio.run(
                worker(config, args.video, args.artifacts, args.order)
            ).model_dump_json()
        )
        return 0
    if args.output is None:
        parser.error("output required")
    output = args.output.resolve()
    bundle = output.with_suffix(".artifacts")
    if output.exists() or bundle.exists():
        parser.error("immutable output")
    output.parent.mkdir(parents=True, exist_ok=True)
    trials, ids, failure = [], set(), None if os.name == "posix" else "platform"
    idle_checks = []
    with tempfile.TemporaryDirectory(
        prefix=".m8-gpu-staging-", dir=output.parent
    ) as temporary:
        artifacts = Path(temporary) / "bundle"
        artifacts.mkdir()
        if not failure:
            for pair, order in enumerate(
                balanced_orders(config.repetitions, config.seed)
            ):
                command = [
                    sys.executable,
                    "-m",
                    "benchmarks.gpu",
                    "--worker",
                    "--artifacts",
                    str(artifacts),
                    "--order",
                    order,
                    "--repetitions",
                    str(config.repetitions),
                    "--warmups",
                    str(config.warmups),
                    "--seed",
                    str(config.seed),
                    "--timeout",
                    str(config.timeout_s),
                ]
                command += (
                    ["--video", str(args.video.resolve())]
                    if args.video
                    else ["--recipe", config.recipe]
                )
                try:
                    check = idle_check(pair, "before")
                    idle_checks.append(check)
                    if not check.quiescent:
                        failure = "gpu_interference"
                        break
                    try:
                        raw = isolated_trial(
                            command,
                            cwd=Path(__file__).resolve().parents[1],
                            timeout=config.timeout_s,
                            diagnostics=artifacts / "diagnostics" / "parent",
                        )
                    finally:
                        check = idle_check(pair, "after")
                        idle_checks.append(check)
                    trial = GPUTrial.model_validate_json(raw)
                    if trial.configuration != config or trial.order != order:
                        raise ValueError("worker echo mismatch")
                    groups = verify(trial, artifacts)
                    if groups & ids:
                        raise ValueError("collection reused")
                    ids |= groups
                    bounded = GPUResult(
                        status="incomplete",
                        failure="output_budget",
                        configuration=config,
                        trials=tuple(trials + [trial]),
                        artifacts=inventory(artifacts),
                        idle_checks=tuple(idle_checks),
                    )
                    if (
                        len(bounded.model_dump_json(indent=2).encode()) + 1
                        > 8 * 1024**2
                    ):
                        failure = "output_budget"
                        break
                    trials.append(trial)
                    if not check.quiescent:
                        failure = "gpu_cleanup"
                    elif trial.status != "ok":
                        failure = "worker_failure"
                    elif identity(trial) != identity(trials[0]):
                        failure = "identity_mismatch"
                    if failure:
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
        data = dict(
            status="unsupported"
            if failure == "platform" or (trials and trials[-1].status == "unsupported")
            else "incomplete"
            if failure
            else "ok",
            failure=failure,
            configuration=config,
            trials=tuple(trials),
            artifacts=inventory(artifacts),
            idle_checks=tuple(idle_checks),
        )
        try:
            result = GPUResult(**data)
        except ValueError:
            data.update(status="incomplete", failure="result_coherence")
            result = GPUResult(**data)
        verify_bundle(result, artifacts)
        publish_bundle(output, artifacts, result)
    print(json.dumps(summary(result)))
    return 0 if result.status == "ok" else 3


if __name__ == "__main__":
    raise SystemExit(main())
