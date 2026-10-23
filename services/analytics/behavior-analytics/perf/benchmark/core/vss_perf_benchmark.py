#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Jenkins entrypoint for the Behavior Analytics playback benchmark."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

PERF_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = PERF_ROOT.parents[3]
SHARED_REPORTING_SRC = REPO_ROOT / "libs" / "vss-perf-reporting" / "src"
for path in (PERF_ROOT / "benchmark", SHARED_REPORTING_SRC):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from latency import build_result as build_native_result  # noqa: E402
from minio_upload import minio_settings  # noqa: E402
from vss_perf_ba_adapter import failure_payload, to_test_cases  # noqa: E402
from vss_perf_reporting import (  # noqa: E402
    build_and_save,
    discover_platform,
    publish_result_file,
)

SERVICE = "behavior-analytics"
RESULTS_DIR_NAME = "vss-perf-results"
NATIVE_RESULT_NAME = "execution_summary.json"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=PERF_ROOT / "configs/config.yaml")
    parser.add_argument(
        "--config-id",
        default=os.environ.get("PERF_CONFIG_ID", "warehouse_2d-kafka-playback"),
    )
    parser.add_argument("--scenario", default=os.environ.get("PERF_SCENARIO", "quick_test"))
    parser.add_argument("--output-json", type=Path)
    parser.add_argument("--compose-file")
    parser.add_argument("--gpu-model")
    parser.add_argument("--vlm-gpus")
    parser.add_argument("--llm-gpus")
    parser.add_argument("--vlm-model")
    parser.add_argument("--llm-model")
    parser.add_argument("--vision-input-tokens")
    parser.add_argument("--release", default=os.environ.get("RELEASE", ""))
    parser.add_argument("--pipeline-url")
    parser.add_argument("--triggered-by", default=os.environ.get("TRIGGERED_BY", "manual"))
    parser.add_argument(
        "--stage-failure",
        choices=["pre_flight", "deploy", "post_deploy", "benchmark", "teardown"],
    )
    parser.add_argument("--failure-message", default="stage failed")
    parser.add_argument("--upload", action="store_true")
    parser.add_argument("--report", type=Path)
    args, _unknown = parser.parse_known_args(argv)
    return args


def result_target(args: argparse.Namespace) -> Path:
    """Return an explicit JSON path or a directory for the canonical JSON."""
    if args.output_json:
        return Path(args.output_json)
    env_output = os.environ.get("OUTPUT_DIR")
    if env_output:
        return Path(env_output) / RESULTS_DIR_NAME
    run_dir = PERF_ROOT / "results" / f"{SERVICE}_{args.scenario}_{args.config_id}"
    return run_dir / RESULTS_DIR_NAME


def result_dir(args: argparse.Namespace) -> Path:
    target = result_target(args)
    return target.parent if target.suffix.lower() == ".json" else target


def result_file(args: argparse.Namespace) -> Path:
    """Compatibility helper: explicit files remain exact; directory output is canonicalized later."""
    return result_target(args)


def measurement_dir(args: argparse.Namespace) -> Path:
    directory = result_dir(args)
    return directory.parent if directory.name == RESULTS_DIR_NAME else directory


def _write_canonical_result(payload: dict[str, Any], args: argparse.Namespace) -> Path:
    test_cases = to_test_cases(payload)
    success = bool(payload.get("success"))
    platform = discover_platform(config_id=args.config_id)
    if args.gpu_model:
        platform.setdefault("gpu", {})["model"] = args.gpu_model
    result_config = {
        "config_id": args.config_id,
        "artifact_ref": payload.get("artifact_ref", os.environ.get("ARTIFACT_REF", "")),
        "setup": payload.get("setup", "warehouse_2d_kafka_playback"),
        "warmup_seconds": payload.get("warmup_seconds"),
        "correction": payload.get("correction", "topic_summary_minus_raw"),
        "app_overlay": payload.get("app_overlay", {}),
    }
    return build_and_save(
        result_target(args),
        SERVICE,
        test_cases,
        config_id=args.config_id,
        platform=platform,
        config=result_config,
        benchmark_name=args.scenario,
        benchmark_mode="kafka_playback",
        triggered_by=args.triggered_by,
        pipeline_url=(args.pipeline_url or ""),
        duration_seconds=payload.get("duration_seconds"),
        release=args.release,
        passed=1 if success else 0,
        failed=0 if success else 1,
    )


def finish_result(
    dest: Path,
    *,
    success: bool,
    should_upload: bool,
    args: argparse.Namespace,
) -> int:
    extra: dict[str, object] = {}
    if should_upload:
        extra["minio"] = _upload(dest, args)
    status = "PASS" if success else "FAIL"
    print(json.dumps({"result_file": str(dest), "status": status, **extra}))
    return 0 if success else 1


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    canonical_dir = result_dir(args)
    work_dir = measurement_dir(args)
    canonical_dir.mkdir(parents=True, exist_ok=True)
    work_dir.mkdir(parents=True, exist_ok=True)

    target = result_target(args)
    if target.suffix.lower() == ".json":
        target.unlink(missing_ok=True)
    else:
        for stale in target.glob(f"{SERVICE}_*.json"):
            stale.unlink()

    env = os.environ.copy()
    env["PERF_SCENARIO"] = args.scenario
    env["PERF_CONFIG_ID"] = args.config_id
    env["PERF_CONFIG_FILE"] = str(args.config.resolve())
    env["OUTPUT_DIR"] = str(work_dir.resolve())
    if args.release:
        env["RELEASE"] = args.release
    if args.triggered_by:
        env["TRIGGERED_BY"] = args.triggered_by
    should_upload = bool(args.upload or env.get("UPLOAD_TO_MINIO") == "true")
    if should_upload:
        env["UPLOAD_TO_MINIO"] = "true"

    native_path = work_dir / NATIVE_RESULT_NAME
    native_path.unlink(missing_ok=True)

    if args.stage_failure:
        payload = failure_payload(
            scenario=args.scenario,
            config_id=args.config_id,
            stage=args.stage_failure,
            message=args.failure_message,
        )
        dest = _write_canonical_result(payload, args)
        return finish_result(dest, success=False, should_upload=should_upload, args=args)

    if args.report:
        payload = build_native_result(
            args.report.read_text(),
            artifact_ref=env.get("ARTIFACT_REF", ""),
            scenario=args.scenario,
            config_id=args.config_id,
            duration_seconds=int(env.get("PERF_DURATION_SECONDS", "60")),
            warmup_seconds=int(env.get("PERF_WARMUP_SECONDS", "10")),
        )
        dest = _write_canonical_result(payload, args)
        return finish_result(
            dest,
            success=bool(payload["success"]),
            should_upload=should_upload,
            args=args,
        )

    if env.get("PERF_PRE_FLIGHT"):
        cmd = ["bash", str(PERF_ROOT / "scripts" / "measure.sh")]
    else:
        cmd = ["bash", str(PERF_ROOT / "run_benchmark.sh")]
    completed = subprocess.run(cmd, env=env, check=False)

    if native_path.is_file():
        try:
            payload = json.loads(native_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            payload = failure_payload(
                scenario=args.scenario,
                config_id=args.config_id,
                stage="benchmark",
                message=f"invalid native result: {error}",
            )
    else:
        payload = failure_payload(
            scenario=args.scenario,
            config_id=args.config_id,
            stage="benchmark",
            message=f"run_benchmark.sh exited {completed.returncode} without {NATIVE_RESULT_NAME}",
        )

    success = completed.returncode == 0 and bool(payload.get("success"))
    if not success and payload.get("success"):
        payload["success"] = False
        payload["failure_stage"] = "benchmark"
        payload["error"] = f"run_benchmark.sh exited {completed.returncode}"
    dest = _write_canonical_result(payload, args)
    return finish_result(dest, success=success, should_upload=should_upload, args=args)


def _upload(dest: Path, args: argparse.Namespace) -> dict[str, str]:
    settings = minio_settings(args.config)
    access_key = os.environ.get("MINIO_ACCESS_KEY", "")
    secret_key = os.environ.get("MINIO_SECRET_KEY", "")
    if not access_key or not secret_key:
        raise ValueError("MinIO credentials are required for upload")
    published = publish_result_file(
        dest,
        settings["service"],
        endpoint=settings["endpoint"],
        bucket=settings["bucket"],
        access_key=access_key,
        secret_key=secret_key,
        secure=settings["secure"],
    )
    print(f"Uploaded {published['uri']}", flush=True)
    return published


if __name__ == "__main__":
    raise SystemExit(main())
