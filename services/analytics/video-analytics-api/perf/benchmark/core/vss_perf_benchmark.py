#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Canonical Jenkins entrypoint for the Video Analytics API HTTP benchmark."""

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

from vss_perf_reporting import build_and_save, discover_platform  # noqa: E402
from vss_perf_va_api_adapter import failure_payload, to_test_cases  # noqa: E402

REPORT_SERVICE = "Video-Analytics-API"
SERVICE_SLUG = "video-analytics-api"
RESULTS_DIR_NAME = "vss-perf-results"
NATIVE_RESULT_NAME = "execution_summary.json"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=PERF_ROOT / "configs/config.yaml")
    parser.add_argument(
        "--config-id",
        default=os.environ.get("PERF_CONFIG_ID", "warehouse-es-dump"),
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
    parser.add_argument("--report", type=Path)
    args, _unknown = parser.parse_known_args(argv)
    return args


def result_target(args: argparse.Namespace) -> Path:
    if args.output_json:
        target = Path(args.output_json)
        return target if target.suffix.lower() == ".json" else target / RESULTS_DIR_NAME
    env_output = os.environ.get("OUTPUT_DIR")
    if env_output:
        return Path(env_output) / RESULTS_DIR_NAME
    return (
        PERF_ROOT
        / "results"
        / f"{SERVICE_SLUG}_{args.scenario}_{args.config_id}"
        / RESULTS_DIR_NAME
    )


def result_dir(args: argparse.Namespace) -> Path:
    target = result_target(args)
    return target.parent if target.suffix.lower() == ".json" else target


def measurement_dir(args: argparse.Namespace) -> Path:
    directory = result_dir(args)
    return directory.parent if directory.name == RESULTS_DIR_NAME else directory


def _write_canonical_result(payload: dict[str, Any], args: argparse.Namespace) -> Path:
    test_cases = to_test_cases(payload)
    passed = sum(case.get("status") == "PASS" for case in test_cases)
    failed = len(test_cases) - passed
    platform = discover_platform(config_id=args.config_id)
    if args.gpu_model:
        platform.setdefault("gpu", {})["model"] = args.gpu_model
    result_config = {
        "config_id": args.config_id,
        "artifact_ref": payload.get("artifact_ref", os.environ.get("ARTIFACT_REF", "")),
        "profile": "warehouse_2d",
        "setup": "warehouse_2d_es_dump",
        "request_count": payload.get("request_count"),
        "warmup_requests": payload.get("warmup_requests"),
        "query_window_s": payload.get("query_window_s"),
    }
    return build_and_save(
        result_target(args),
        REPORT_SERVICE,
        test_cases,
        config_id=args.config_id,
        platform=platform,
        config=result_config,
        benchmark_name=args.scenario,
        benchmark_mode="dump_backed_http",
        triggered_by=args.triggered_by,
        pipeline_url=(args.pipeline_url or ""),
        release=args.release,
        passed=passed,
        failed=failed,
    )


def _finish(dest: Path, success: bool) -> int:
    print(json.dumps({"result_file": str(dest), "status": "PASS" if success else "FAIL"}))
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
        for stale in target.glob(f"{SERVICE_SLUG}_*.json"):
            stale.unlink()

    env = os.environ.copy()
    env["PERF_SCENARIO"] = args.scenario
    env["PERF_CONFIG_ID"] = args.config_id
    env["PERF_CONFIG_PATH"] = str(args.config.resolve())
    env["OUTPUT_DIR"] = str(work_dir.resolve())
    if args.release:
        env["RELEASE"] = args.release
    if args.triggered_by:
        env["TRIGGERED_BY"] = args.triggered_by

    native_path = work_dir / NATIVE_RESULT_NAME
    native_path.unlink(missing_ok=True)

    if args.stage_failure:
        payload = failure_payload(
            scenario=args.scenario,
            config_id=args.config_id,
            stage=args.stage_failure,
            message=args.failure_message,
        )
        return _finish(_write_canonical_result(payload, args), False)

    if args.report:
        try:
            payload = json.loads(args.report.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            payload = failure_payload(
                scenario=args.scenario,
                config_id=args.config_id,
                stage="benchmark",
                message=f"invalid native result: {error}",
            )
        success = bool(payload.get("success"))
        return _finish(_write_canonical_result(payload, args), success)

    env["PERF_NATIVE_ONLY"] = "true"
    completed = subprocess.run(
        ["bash", str(PERF_ROOT / "scripts" / "run_benchmark.sh")],
        env=env,
        check=False,
    )

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
            message=(
                f"run_benchmark.sh exited {completed.returncode} without "
                f"{NATIVE_RESULT_NAME}"
            ),
        )

    success = completed.returncode == 0 and bool(payload.get("success"))
    if not success and payload.get("success"):
        payload = failure_payload(
            scenario=args.scenario,
            config_id=args.config_id,
            stage="benchmark",
            message=f"run_benchmark.sh exited {completed.returncode}",
        )
    return _finish(_write_canonical_result(payload, args), success)


if __name__ == "__main__":
    raise SystemExit(main())
