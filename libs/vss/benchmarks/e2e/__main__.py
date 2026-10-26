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

"""Bounded Q0 experiments: python -m benchmarks.e2e --help."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
import time
from fractions import Fraction
from pathlib import Path
from statistics import mean, median

from ..artifacts import publish_result
from ..common import environment, software_hash
from ..media import MAX_INPUT_BYTES, bounded_tool, file_hash
from ..results import Evidence
from ..supervisor import isolated_trial
from .config import Recipe, endpoint, fingerprint, key, load
from .results import WorkerResult

ROOT = Path(__file__).resolve().parents[2]


class Report(Evidence):
    schema_version: str = "q0.1"
    status: str
    evidence_kind: str
    config_sha256: str
    source_sha256: str
    software_sha256: str
    lockfile_sha256: str
    environment: dict
    protocol: dict
    media_admission: dict | None
    trials: list[dict]
    statistics: dict | None
    comparison: dict
    qualification: str = "descriptive_only_unapproved_budgets"


def preflight(recipe: Recipe) -> dict:
    import importlib.util

    video = recipe.video
    if not video.is_file() or not 0 < video.stat().st_size <= MAX_INPUT_BYTES:
        raise ValueError("source file/size")
    if file_hash(video) != recipe.video_sha256:
        raise ValueError("source hash")
    if os.name != "posix":
        raise ValueError("POSIX worker supervision required")
    if any(arm.captioning == "rtvi" for arm in recipe.arms):
        raise ValueError("RTVI asset/finish/usage/sampling contract is unqualified")
    ffprobe = shutil.which("ffprobe")
    if ffprobe is None:
        raise ValueError("FFprobe required for independent media admission")
    raw = bounded_tool(
        [
            ffprobe,
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-count_frames",
            "-show_frames",
            "-show_entries",
            "stream=width,height,codec_name,duration,time_base,nb_frames,nb_read_frames:"
            "format=duration:frame=best_effort_timestamp",
            "-of",
            "json",
            str(video),
        ],
        limit=4 * 1024**2,
        timeout=60,
    )
    media = json.loads(raw)
    stream = media["streams"][0]
    duration = float(stream.get("duration") or media["format"]["duration"])
    if (
        not 0 < stream["width"] <= 1920
        or not 0 < stream["height"] <= 1080
        or not 0 < duration <= 1800
        or abs(duration - recipe.duration_s) > 0.1
    ):
        raise ValueError("media dimensions/duration do not match profile")
    frame_facts = frame_admission(media, recipe)
    modules = {"openai", "httpx", "psutil", "cv2"}
    for arm in recipe.arms:
        modules.add("PyNvVideoCodec" if arm.captioning == "gpu_vlm" else "cv2")
        if arm.store == "elasticsearch":
            modules.add("elasticsearch")
            endpoint(arm.es_url_env)
            key(arm.es_api_key_env)
        if recipe.evidence_kind == "live_provider":
            for provider in (arm.vlm, arm.llm):
                endpoint(provider.base_url_env)
                key(provider.api_key_env)
                if provider.revision.lower() in {
                    "unknown",
                    "unresolved",
                    "todo",
                    "latest",
                }:
                    raise ValueError("provider revision unresolved")
    if any(importlib.util.find_spec(module) is None for module in modules):
        raise ValueError("required optional dependency unavailable")
    return {
        "status": "local_preflight_ok",
        "inference_executed": False,
        "runtime_and_service_readiness": "requires_smoke",
        "source_sha256": recipe.video_sha256,
        "evidence_kind": recipe.evidence_kind,
        "width": stream["width"],
        "height": stream["height"],
        "duration_s": duration,
        "codec": stream["codec_name"],
        "method": "bounded_count_checked_ffprobe_integer_pts.v1_untimed",
        **frame_facts,
        "ffprobe_build_sha256": hashlib.sha256(
            bounded_tool([ffprobe, "-version"], limit=65536, timeout=10)
        ).hexdigest(),
        "native_dependencies": {
            name: value
            for name in (
                "psutil",
                "opencv-python-headless",
                "opencv-python",
                "PyNvVideoCodec",
            )
            if (value := _installed(name)) is not None
        },
    }


def _installed(name):
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None


def frame_admission(media, recipe):
    stream = media["streams"][0]
    time_base = Fraction(stream["time_base"])
    frames = media["frames"]
    if not 1 <= len(frames) <= 12000 or time_base <= 0:
        raise ValueError("admission frame bound/time base")
    pts = [int(f["best_effort_timestamp"]) for f in frames]
    if any(b <= a for a, b in zip(pts, pts[1:], strict=False)):
        raise ValueError("admission PTS ordering")
    if stream.get("nb_read_frames") in (None, "N/A"):
        raise ValueError("independent frame count was not reported")
    for field in ("nb_frames", "nb_read_frames"):
        value = stream.get(field)
        if value not in (None, "N/A") and int(value) != len(pts):
            raise ValueError("admission frame count disagrees with timestamp count")
    normalized = [(p - pts[0]) * time_base for p in pts]
    if normalized[-1] >= Fraction(str(recipe.duration_s)):
        raise ValueError("admission duration excludes frames")
    windows = sorted({int(t // recipe.chunk_duration_s) for t in normalized})
    if windows != list(range(len(windows))) or len(windows) > 64:
        raise ValueError("noncontiguous or excessive occupied windows unsupported")
    nominal = math.ceil(recipe.duration_s / recipe.chunk_duration_s)
    if len(windows) != nominal and not (
        len(windows) == nominal - 1
        and 0 < recipe.duration_s - len(windows) * recipe.chunk_duration_s <= 0.1
        and len(windows) * recipe.chunk_duration_s - float(normalized[-1]) <= 0.1
    ):
        raise ValueError("unexplained trailing empty duration windows")
    return {
        "frames": len(pts),
        "occupied_windows": windows,
        "normalized_pts_sha256": fingerprint([str(t) for t in normalized]),
        "last_frame_pts_s": float(normalized[-1]),
        "window_policy": "PTS_occupied_windows_sub_100ms_empty_tail_only",
    }


def schedule(recipe: Recipe, smoke: bool) -> list[tuple[str, int, int]]:
    warmups = (
        []
        if smoke
        else [("warmup", n, arm) for n in range(recipe.warmups) for arm in (0, 1)]
    )
    samples = [
        ("smoke" if smoke else "sample", n, arm)
        for n in range(1 if smoke else recipe.pairs)
        for arm in ((0, 1) if n % 2 == 0 else (1, 0))
    ]
    return warmups + samples


def statistics(recipe: Recipe, trials: list[dict], smoke: bool):
    if smoke or any(t["status"] != "ok" for t in trials):
        return None
    samples = [t for t in trials if t["phase"] == "sample"]
    if len(samples) != 2 * recipe.pairs:
        return None
    return {
        arm.label: {
            "result_ready_mean_s": mean(
                t["result_ready_s"] for t in samples if t["arm"] == arm.label
            ),
            "result_ready_median_s": median(
                t["result_ready_s"] for t in samples if t["arm"] == arm.label
            ),
            "lifecycle_mean_s": mean(
                t["lifecycle_s"] for t in samples if t["arm"] == arm.label
            ),
        }
        for arm in recipe.arms
    }


def run(recipe: Recipe, config: Path, smoke: bool) -> Report:
    admission = preflight(recipe)
    # Freeze the already validated private profile once for all child processes.
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json") as frozen:
        frozen.write(recipe.model_dump_json())
        frozen.flush()
        return _run(recipe, Path(frozen.name), smoke, admission)


def _run(recipe: Recipe, config: Path, smoke: bool, admission: dict | None) -> Report:
    trials = []
    deadline = time.monotonic() + recipe.run_timeout_s
    for phase, pair, arm_index in schedule(recipe, smoke):
        arm = recipe.arms[arm_index]
        remaining = deadline - time.monotonic()
        failure = "run_deadline" if remaining < recipe.worker_timeout_s else None
        data = None
        if failure is None:
            try:
                raw = isolated_trial(
                    [
                        sys.executable,
                        "-m",
                        "benchmarks.e2e",
                        "--config",
                        str(config.resolve()),
                        "--worker-arm",
                        str(arm_index),
                        "--expected-chunks",
                        str(len(admission["occupied_windows"]))
                        if admission
                        else str(
                            math.ceil(recipe.duration_s / recipe.chunk_duration_s)
                        ),
                    ],
                    cwd=ROOT,
                    timeout=recipe.worker_timeout_s,
                )
                data = WorkerResult.model_validate_json(raw).model_dump(mode="json")
            except subprocess.TimeoutExpired:
                failure = "worker_timeout"
            except Exception:
                failure = "worker_or_protocol"
        if data is None:
            data = {"status": "failed", "failure": failure}
        data.update(
            phase=phase,
            pair=pair,
            arm=arm.label,
            pipeline=arm.captioning,
            store=arm.store,
            provider_identity_sha256=fingerprint(
                [arm.vlm.model_dump(), arm.llm.model_dump()]
            ),
        )
        trials.append(data)
        if data["status"] != "ok":
            # Preserve failure, reject survivor summaries and stop after failure.
            break
    expected = len(schedule(recipe, smoke))
    okay = len(trials) == expected and all(t["status"] == "ok" for t in trials)
    if (
        okay
        and recipe.evidence_kind == "controlled_wiring"
        and recipe.swap == "storage"
    ):
        if (
            len({t["captions_sha256"] for t in trials}) != 1
            or len({t["summary_sha256"] for t in trials}) != 1
        ):
            okay = False
            trials[-1].update(status="failed", failure="controlled_output_mismatch")
    return Report(
        status="ok" if okay else "incomplete",
        evidence_kind=recipe.evidence_kind,
        config_sha256=fingerprint(recipe.model_dump(mode="json")),
        source_sha256=recipe.video_sha256,
        software_sha256=software_hash(ROOT),
        lockfile_sha256=file_hash(ROOT / "uv.lock"),
        environment=environment().model_dump(mode="json"),
        protocol={
            "operation": "Q0.complete_captions_store_read_aggregate_final_summary",
            "swap": recipe.swap,
            "smoke": smoke,
            "expected_trials": expected,
            "pairs": 1 if smoke else recipe.pairs,
            "settings": {
                "duration_s": recipe.duration_s,
                "chunk_duration_s": recipe.chunk_duration_s,
                "frames_per_chunk": recipe.frames_per_chunk,
                "overlap_s": recipe.overlap_s,
                "caption_max_tokens": recipe.caption_max_tokens,
                "summary_max_tokens": recipe.summary_max_tokens,
                "caption_temperature": 0,
                "summary_temperature": "provider_default_unpinned",
                "caption_prompt_sha256": fingerprint(recipe.caption_prompt),
                "summary_prompt_sha256": fingerprint(recipe.summary_prompt),
            },
            "warmups": 0 if smoke else recipe.warmups,
            "isolation": "fresh_process_memory_file_store_ES_shared_accumulating_index",
            "warmup_scope": "separate_process_provider_OS_cache_only",
            "timing_start": "after_client_store_construction_before_ingest",
            "result_ready_end": "summary_returned_before_adequacy_validation",
            "lifecycle_end": "validation_and_job_store_client_cleanup_complete",
            "resource_scope": "worker_setup_and_operation_excludes_remote_services",
            "witness_scope": "symmetric_incremental_validation_overhead_uncalibrated",
            "durability": "fresh_file_adapter_readback_only",
            "provider_tokens": "reported_usage_not_tokenizer_estimates",
            "normal_vss_baseline": "unsupported_complete_Q0_adapter_unqualified",
            "gpu_telemetry": "not_collected",
            "tail_acceptance": "not_qualified",
            "retry_policy": "SDK_and_HTTP_transport_retries_disabled_RTVI_blocked",
            "remote_settlement": "not_observable_from_client_cleanup",
            "frame_identity": "encoded_messages_hash_for_frame_VLM_RTVI_unavailable",
        },
        media_admission=admission,
        trials=trials,
        statistics=statistics(recipe, trials, smoke)
        if okay and recipe.evidence_kind == "controlled_wiring"
        else None,
        comparison={
            "status": "controlled_wiring_only"
            if recipe.evidence_kind == "controlled_wiring"
            else "unqualified_live_workload",
            "live_gates": [
                "effective sampling/frame input equivalence",
                "model/runtime/retry/generation and output adequacy qualification",
                "RTVI completion/usage/asset binding and remote settlement",
                "instrumentation overhead calibration and approved budgets",
            ],
        },
    )


def fixture_config(video: Path, duration: float) -> dict:
    provider = {
        "model": "controlled-model",
        "revision": "owned-loopback.v1",
        "base_url_env": "M8_CONTROLLED_UNUSED",
    }
    return {
        "schema_version": "q0.1",
        "evidence_kind": "controlled_wiring",
        "swap": "storage",
        "video": str(video.resolve()),
        "video_sha256": file_hash(video),
        "duration_s": duration,
        "required_summary_terms": ["test pattern"],
        "arms": [
            {"label": store, "store": store, "vlm": provider, "llm": provider}
            for store in ("memory", "file")
        ],
    }


def baseline_preparation() -> dict:
    return {
        "status": "blocked_comparison_qualification",
        "deployable": False,
        "deployment_scope": "qualified_normal_VSS_comparison",
        "image": None,
        "platform": None,
        "reported_source_version": None,
        "runtime_product_version": None,
        "observed_release_line": None,
        "blueprint_release": None,
        "identity_scope": "operator_supplied_deployment_required",
        "live_readiness_checked_by_this_command": False,
        "missing": [
            "stable blueprint release identity and runtime source-build verification",
            "matched effective chunk/frame/resolution/prompt/generation settings",
            "ES retention/index/storage mapping and Q0 operation equivalence",
            "raw provider completion/token accounting and reviewed output adequacy",
            "qualified complete-operation adapter and symmetric timing/telemetry",
            "paired performance runs and observation-overhead calibration",
        ],
        "credentials": "reuse private runner environment; never embed keys",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--preflight", action="store_true")
    parser.add_argument("--smoke", action="store_true")
    parser.add_argument("--prepare-baseline", action="store_true")
    parser.add_argument("--fixture-config", type=Path)
    parser.add_argument("--video", type=Path)
    parser.add_argument("--duration-s", type=float, default=2)
    parser.add_argument(
        "--worker-arm", type=int, choices=[0, 1], help=argparse.SUPPRESS
    )
    parser.add_argument(
        "--expected-chunks", type=int, choices=range(1, 65), help=argparse.SUPPRESS
    )
    args = parser.parse_args()
    try:
        if args.prepare_baseline:
            print(json.dumps(baseline_preparation(), indent=2))
            return 2  # Explicitly not deployment-ready.
        if args.fixture_config:
            data = fixture_config(args.video, args.duration_s)
            Recipe.model_validate_json(json.dumps(data), strict=True)
            with args.fixture_config.open("x") as stream:
                json.dump(data, stream, indent=2)
            return 0
        recipe = load(args.config)
        if args.worker_arm is not None:
            from .worker import run_worker

            print(
                json.dumps(
                    run_worker(
                        recipe, recipe.arms[args.worker_arm], args.expected_chunks
                    ),
                    allow_nan=False,
                )
            )
            return 0
        if args.preflight:
            print(json.dumps(preflight(recipe)))
            return 0
        if args.output is None:
            raise ValueError("output required")
        if args.output.exists():
            raise FileExistsError("evidence already exists")
        report = run(recipe, args.config, args.smoke)
        publish_result(args.output, report)
        print(
            json.dumps({"status": report.status, "evidence_kind": report.evidence_kind})
        )
        return 0 if report.status == "ok" else 1
    except Exception:
        # Never echo private configuration or provider error bodies.
        print(
            '{"status":"rejected","detail":"config, preflight or publication failed"}',
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
