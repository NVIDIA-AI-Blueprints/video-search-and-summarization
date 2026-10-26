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

"""Real-media CPU development profile; independent oracles and isolated trials."""

from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import json
import os
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from fractions import Fraction
from pathlib import Path
from typing import Any, Literal

import numpy as np
from pydantic import ValidationError

from vss import video_processing as processing
from vss.observability import collect_vss_metrics, metric_scope

from .common import environment, import_mode, save, software_hash
from .processing_results import (
    FixtureManifest,
    ProcessingConfiguration,
    ProcessingResult,
    ProcessingTrial,
    identity,
)
from .resources import ResourceSampler
from .supervisor import TrialCleanupError, isolated_trial


def _tool(command: list[str], timeout: float = 30) -> bytes:
    return subprocess.run(
        command, capture_output=True, check=True, timeout=timeout
    ).stdout


def preflight() -> tuple[dict[str, str], dict[str, str]]:
    import cv2
    import psutil

    # Force the actual deployment-owned decoder readiness check, including its
    # version policy. Merely finding an importable cv2 module is insufficient.
    from vss.video_processing.decoders.opencv import _opencv

    _opencv()
    tools = {}
    builds = {
        "opencv": cv2.__version__,
        "opencv_build_sha256": hashlib.sha256(
            cv2.getBuildInformation().encode()
        ).hexdigest(),
        "psutil": psutil.__version__,
    }
    for name in ("ffmpeg", "ffprobe"):
        executable = shutil.which(name)
        if executable is None:
            raise RuntimeError("missing external tool")
        tools[name] = executable
        builds[name + "_build_sha256"] = hashlib.sha256(
            _tool([executable, "-version"], timeout=5)
        ).hexdigest()
    return tools, builds


def fixtures(
    directory: Path, tools: dict[str, str]
) -> list[tuple[Path, FixtureManifest, np.ndarray]]:
    result = []
    for recipe in ("cfr", "vfr", "nonzero_origin"):
        path = directory / (recipe + ".mp4")
        command = [
            tools["ffmpeg"],
            "-v",
            "error",
            "-nostdin",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=160x96:rate=5:duration=2",
            "-an",
        ]
        if recipe == "vfr":
            command += [
                "-vf",
                r"setpts=if(lt(N\,3)\,N/(5*TB)\,(N+2)/(5*TB))",
                "-fps_mode",
                "vfr",
            ]
        command += [
            "-c:v",
            "libx264",
            "-threads",
            "1",
            "-pix_fmt",
            "yuv420p",
            "-g",
            "5",
            "-bf",
            "2",
            "-color_range",
            "tv",
            "-colorspace",
            "smpte170m",
            "-color_primaries",
            "smpte170m",
            "-color_trc",
            "smpte170m",
        ]
        if recipe == "nonzero_origin":
            command += ["-output_ts_offset", "5"]
        _tool(command + [str(path)])
        document = json.loads(
            _tool(
                [
                    tools["ffprobe"],
                    "-v",
                    "error",
                    "-select_streams",
                    "v:0",
                    "-show_frames",
                    "-show_streams",
                    "-show_entries",
                    "frame=best_effort_timestamp:stream=time_base,width,height",
                    "-of",
                    "json",
                    str(path),
                ]
            )
        )
        stream = document["streams"][0]
        if (stream["width"], stream["height"]) != (160, 96):
            raise ValueError("fixture dimensions changed")
        base = Fraction(stream["time_base"])
        pts = tuple(
            round(int(f["best_effort_timestamp"]) * base * 1_000_000_000)
            for f in document["frames"]
        )
        raw = _tool(
            [
                tools["ffmpeg"],
                "-v",
                "error",
                "-nostdin",
                "-i",
                str(path),
                "-an",
                "-fps_mode",
                "passthrough",
                "-f",
                "rawvideo",
                "-pix_fmt",
                "rgb24",
                "-threads",
                "1",
                "pipe:1",
            ]
        )
        oracle = np.frombuffer(raw, dtype=np.uint8).reshape(10, 96, 160, 3)
        manifest = FixtureManifest(
            recipe=recipe,
            sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
            oracle_rgb_sha256=hashlib.sha256(raw).hexdigest(),
            raw_pts_ns=pts,
        )
        result.append((path, manifest, oracle))
    return result


def _check(condition: bool, reason: str) -> None:
    if not condition:
        raise ValueError(reason)


async def workload(corpus: list[tuple[Path, FixtureManifest, np.ndarray]]) -> int:
    import cv2

    options = processing.DecodeOptions(decoder="opencv", batch_size=2)
    decoded = 0
    for path, manifest, oracle in corpus:
        source_id = "cpu-fixture-" + manifest.recipe
        expected = [p - manifest.raw_pts_ns[0] for p in manifest.raw_pts_ns]
        with metric_scope(source_id=source_id, pipeline="processing"):
            facts = await processing.probe_video(
                path, source_id=source_id, options=options
            )
            _check((facts.width, facts.height) == (160, 96), "probe dimensions")
            frames = [
                f
                async for f in processing.decode_frames(
                    path,
                    processing.DecodePlan(),
                    source_id=source_id,
                    options=options,
                )
            ]
            _check(len(frames) == 10, "decode count")
            decoded += len(frames)
            for index, frame in enumerate(frames):
                _check(
                    frame.index == index
                    and frame.decoder == "opencv"
                    and frame.device == "cpu",
                    "decoder/index",
                )
                _check(
                    abs(frame.timestamp_ns - expected[index]) <= 2_000_000, "PTS oracle"
                )
                _check(frame.timestamp_quality != "estimated", "estimated PTS")
                _check(
                    frame.tensor.shape == (96, 160, 3)
                    and frame.tensor.dtype == np.uint8,
                    "RGB shape/type",
                )
                _check(
                    frame.tensor.flags.c_contiguous
                    and not frame.tensor.flags.writeable,
                    "owned read-only output",
                )
                error = np.abs(
                    frame.tensor.astype(np.int16) - oracle[index].astype(np.int16)
                ).mean()
                _check(error <= 3, "FFmpeg RGB oracle")
            _check(
                not np.shares_memory(frames[0].tensor, frames[-1].tensor),
                "frame ownership",
            )
            targets = [50_000_000, 550_000_000, 1_050_000_000]
            extracted = await processing.extract_frames(
                path,
                [t / 1e9 for t in targets],
                source_id=source_id,
                options=options,
            )
            for frame, target in zip(extracted, targets, strict=True):
                selected = next(t for t in expected if t >= target)
                _check(
                    abs(frame.timestamp_ns - selected) <= 2_000_000
                    and frame.requested_timestamp_ns == target,
                    "extraction PTS",
                )
            sampled = [
                f
                async for f in processing.subsample_frames(
                    path,
                    processing.SamplingPolicy(mode="all"),
                    source_id=source_id,
                    options=options,
                )
            ]
            _check(len(sampled) == 10, "sampling count")
            _check(
                all(
                    abs(f.timestamp_ns - t) <= 2_000_000
                    for f, t in zip(sampled, expected, strict=True)
                ),
                "sampling PTS",
            )
            encoded = frames[0].to_encoded(format="png")
            pixels = cv2.imdecode(
                np.frombuffer(base64.b64decode(encoded.data_base64), np.uint8),
                cv2.IMREAD_COLOR,
            )
            _check(
                pixels is not None
                and np.array_equal(
                    cv2.cvtColor(pixels, cv2.COLOR_BGR2RGB), frames[0].tensor
                ),
                "lossless encoding",
            )
            artifact = await processing.materialize_clip(
                path, 0.4, 1.2, source_id=source_id
            )
            try:
                _check(
                    artifact.path.is_file() and artifact.duration_s > 0, "verified clip"
                )
            finally:
                artifact.cleanup()
            _check(not artifact.path.exists(), "clip cleanup")
    return decoded


async def worker(config: ProcessingConfiguration) -> ProcessingTrial:
    root = Path(__file__).resolve().parents[1]
    metadata: dict[str, Any] = dict(
        process_id=os.getpid(),
        environment=environment(),
        native_builds={},
        software_sha256=software_hash(root),
        lockfile_sha256=hashlib.sha256((root / "uv.lock").read_bytes()).hexdigest(),
        import_origin=import_mode(root),
        fixtures=(),
    )
    try:
        tools, builds = preflight()
        metadata["native_builds"] = builds
    except (
        ImportError,
        OSError,
        RuntimeError,
        processing.ProcessingError,
        subprocess.SubprocessError,
    ):
        return ProcessingTrial(status="unsupported", failure="prerequisite", **metadata)
    with tempfile.TemporaryDirectory(prefix="vss-m8-cpu-") as temporary:
        try:
            corpus = fixtures(Path(temporary), tools)
            metadata["fixtures"] = tuple(m for _, m, _ in corpus)
        except (OSError, ValueError, KeyError, subprocess.SubprocessError):
            return ProcessingTrial(status="error", failure="fixture", **metadata)
        try:
            for _ in range(config.warmups):
                await workload(corpus)
        except Exception:
            return ProcessingTrial(status="error", failure="workload", **metadata)
        sampler = ResourceSampler(config.sample_interval_s)
        checks, decoded, failure = False, 0, None
        with collect_vss_metrics(max_events=8192) as metrics:
            started = time.perf_counter()
            try:
                with sampler:
                    decoded = await workload(corpus)
                checks = True
            except Exception:
                failure = "workload"
            latency = time.perf_counter() - started
        data = dict(
            **metadata,
            latency_s=latency,
            resources=getattr(sampler, "trace", None),
            decoded_frames=decoded,
            checks_passed=checks,
            collection_complete=metrics.complete,
            observations=metrics.observations,
        )
        try:
            return ProcessingTrial(
                status="error" if failure else "ok", failure=failure, **data
            )
        except ValidationError:
            return ProcessingTrial(status="error", failure="evidence", **data)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--warmups", type=int, default=2)
    parser.add_argument("--repetitions", type=int, default=10)
    parser.add_argument("--timeout", type=float, default=60)
    parser.add_argument("--sample-interval", type=float, default=0.01)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--preflight", action="store_true")
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    try:
        config = ProcessingConfiguration(
            warmups=args.warmups,
            repetitions=args.repetitions,
            timeout_s=args.timeout,
            sample_interval_s=args.sample_interval,
        )
    except ValidationError:
        parser.error("invalid workload limits or sampling interval")
    if args.worker:
        print(asyncio.run(worker(config)).model_dump_json())
        return 0
    if args.preflight:
        try:
            _, builds = preflight()
        except Exception:
            print(json.dumps({"status": "unsupported", "reason": "prerequisite"}))
            return 3
        print(json.dumps({"status": "ready", "native_builds": builds}, indent=2))
        return 0
    if args.output is None:
        parser.error("--output is required")
    trials = []
    failure: (
        Literal[
            "timeout",
            "cancelled",
            "child_failure",
            "invalid_child_evidence",
            "worker_failure",
            "identity_mismatch",
            "platform",
            "cleanup_failure",
        ]
        | None
    ) = None
    if os.name != "posix":
        failure = "platform"
    else:
        for _ in range(config.repetitions):
            try:
                raw = isolated_trial(
                    [
                        sys.executable,
                        "-m",
                        "benchmarks.processing",
                        "--worker",
                        "--warmups",
                        str(config.warmups),
                        "--sample-interval",
                        str(config.sample_interval_s),
                    ],
                    cwd=Path(__file__).resolve().parents[1],
                    timeout=config.timeout_s,
                )
                trial = ProcessingTrial.model_validate_json(raw)
                trials.append(trial)
                if trial.status != "ok":
                    failure = "worker_failure"
                    break
                if identity(trial) != identity(trials[0]):
                    failure = "identity_mismatch"
                    break
            except subprocess.TimeoutExpired:
                failure = "timeout"
                break
            except KeyboardInterrupt:
                failure = "cancelled"
                break
            except TrialCleanupError:
                failure = "cleanup_failure"
                break
            except (OSError, subprocess.CalledProcessError):
                failure = "child_failure"
                break
            except ValueError:
                failure = "invalid_child_evidence"
                break
    result = ProcessingResult(
        status="unsupported"
        if failure == "platform" or (trials and trials[-1].status == "unsupported")
        else "incomplete"
        if failure
        else "ok",
        configuration=config,
        trials=tuple(trials),
        failure=failure,
    )
    save(args.output, result)
    print(
        json.dumps(
            {
                "status": result.status,
                "completed_trials": len(trials),
                "median_latency_s": statistics.median(
                    t.latency_s for t in trials if t.latency_s is not None
                )
                if any(t.latency_s is not None for t in trials)
                else None,
                "acceptance_eligible": False,
            }
        )
    )
    return 0 if result.status == "ok" else 3


if __name__ == "__main__":
    raise SystemExit(main())
