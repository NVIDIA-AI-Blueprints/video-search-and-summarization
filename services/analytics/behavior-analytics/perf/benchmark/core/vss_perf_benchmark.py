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

PERF_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PERF_ROOT / "benchmark"))

from latency import build_result  # noqa: E402


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
    # Jenkins runServicePerfSequence may add more LVS flags; ignore unknowns.
    args, _unknown = parser.parse_known_args(argv)
    return args


def result_dir(args: argparse.Namespace) -> Path:
    if args.output_json:
        return args.output_json.parent if args.output_json.suffix == ".json" else args.output_json
    env_output = os.environ.get("OUTPUT_DIR")
    if env_output:
        return Path(env_output)
    return PERF_ROOT / "results" / f"behavior-analytics_{args.scenario}_{args.config_id}"


def write_result(directory: Path, payload: dict[str, object]) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    dest = directory / "result.json"
    dest.write_text(json.dumps(payload, indent=2) + "\n")
    return dest


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    output = result_dir(args)
    output.mkdir(parents=True, exist_ok=True)

    env = os.environ.copy()
    env["PERF_SCENARIO"] = args.scenario
    env["PERF_CONFIG_ID"] = args.config_id
    env["OUTPUT_DIR"] = str(output.resolve())
    if args.release:
        env["RELEASE"] = args.release
    if args.triggered_by:
        env["TRIGGERED_BY"] = args.triggered_by
    if args.upload or env.get("UPLOAD_TO_MINIO") == "true":
        env["UPLOAD_TO_MINIO"] = "true"

    if args.stage_failure:
        dest = write_result(
            output,
            {
                "schema_version": "0.1",
                "service": "behavior-analytics",
                "scenario": args.scenario,
                "config_id": args.config_id,
                "success": False,
                "error": f"{args.stage_failure}: {args.failure_message}",
            },
        )
        print(json.dumps({"result_file": str(dest), "status": "FAIL"}))
        return 1

    if args.report:
        payload = build_result(
            args.report.read_text(),
            artifact_ref=env.get("ARTIFACT_REF", ""),
            scenario=args.scenario,
            config_id=args.config_id,
            duration_seconds=int(env.get("PERF_DURATION_SECONDS", "60")),
            warmup_seconds=int(env.get("PERF_WARMUP_SECONDS", "10")),
        )
        dest = write_result(output, payload)
        status = "PASS" if payload["success"] else "FAIL"
        print(json.dumps({"result_file": str(dest), "status": status}))
        return 0 if payload["success"] else 1

    # Jenkins already ran perf/scripts/{pre_flight,deploy,post_deploy}.sh.
    # Only measure here so run_benchmark.sh does not deploy twice or tear down
    # before the job archives results.
    if env.get("PERF_PRE_FLIGHT"):
        cmd = ["bash", str(PERF_ROOT / "scripts" / "measure.sh")]
    else:
        cmd = ["bash", str(PERF_ROOT / "run_benchmark.sh")]
    completed = subprocess.run(cmd, env=env, check=False)
    dest = output / "result.json"
    if completed.returncode == 0 and dest.is_file():
        print(json.dumps({"result_file": str(dest), "status": "PASS"}))
        return 0
    if not dest.is_file():
        write_result(
            output,
            {
                "schema_version": "0.1",
                "service": "behavior-analytics",
                "scenario": args.scenario,
                "config_id": args.config_id,
                "success": False,
                "error": f"run_benchmark.sh exited {completed.returncode}",
            },
        )
    print(json.dumps({"result_file": str(dest), "status": "FAIL"}))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
