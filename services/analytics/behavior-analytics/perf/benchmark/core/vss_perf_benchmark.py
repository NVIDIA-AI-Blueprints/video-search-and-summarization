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
from minio_upload import publish_result  # noqa: E402
from safe_fs import safe_write_text  # noqa: E402


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
    return result_file(args).parent


def result_file(args: argparse.Namespace) -> Path:
    if args.output_json:
        path = Path(args.output_json)
        if path.suffix.lower() != ".json":
            return path / "result.json"
        return path
    env_output = os.environ.get("OUTPUT_DIR")
    if env_output:
        return Path(env_output) / "result.json"
    return PERF_ROOT / "results" / f"behavior-analytics_{args.scenario}_{args.config_id}" / "result.json"


def write_result(dest: Path, payload: dict[str, object]) -> Path:
    return safe_write_text(dest, json.dumps(payload, indent=2) + "\n")


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
    output_file = result_file(args)
    output = output_file.parent
    output.mkdir(parents=True, exist_ok=True)

    env = os.environ.copy()
    env["PERF_SCENARIO"] = args.scenario
    env["PERF_CONFIG_ID"] = args.config_id
    env["PERF_CONFIG_FILE"] = str(args.config.resolve())
    env["OUTPUT_DIR"] = str(output.resolve())
    if args.release:
        env["RELEASE"] = args.release
    if args.triggered_by:
        env["TRIGGERED_BY"] = args.triggered_by
    should_upload = bool(args.upload or env.get("UPLOAD_TO_MINIO") == "true")
    if should_upload:
        env["UPLOAD_TO_MINIO"] = "true"

    measured = output / "result.json"
    for stale in {output_file, measured}:
        stale.unlink(missing_ok=True)

    if args.stage_failure:
        dest = write_result(
            output_file,
            {
                "schema_version": "0.1",
                "service": "behavior-analytics",
                "scenario": args.scenario,
                "config_id": args.config_id,
                "success": False,
                "error": f"{args.stage_failure}: {args.failure_message}",
            },
        )
        return finish_result(
            dest,
            success=False,
            should_upload=should_upload,
            args=args,
        )

    if args.report:
        payload = build_result(
            args.report.read_text(),
            artifact_ref=env.get("ARTIFACT_REF", ""),
            scenario=args.scenario,
            config_id=args.config_id,
            duration_seconds=int(env.get("PERF_DURATION_SECONDS", "60")),
            warmup_seconds=int(env.get("PERF_WARMUP_SECONDS", "10")),
        )
        dest = write_result(output_file, payload)
        return finish_result(
            dest,
            success=bool(payload["success"]),
            should_upload=should_upload,
            args=args,
        )

    # Jenkins already ran perf/scripts/{pre_flight,deploy,post_deploy}.sh.
    # Only measure here so run_benchmark.sh does not deploy twice or tear down
    # before the job archives results.
    if env.get("PERF_PRE_FLIGHT"):
        cmd = ["bash", str(PERF_ROOT / "scripts" / "measure.sh")]
    else:
        cmd = ["bash", str(PERF_ROOT / "run_benchmark.sh")]
    completed = subprocess.run(cmd, env=env, check=False)
    dest = output_file
    if measured.is_file() and measured.resolve() != dest.resolve():
        safe_write_text(dest, measured.read_text())
    elif measured.is_file():
        dest = measured
    success = completed.returncode == 0 and dest.is_file()
    if not dest.is_file():
        dest = write_result(
            output_file,
            {
                "schema_version": "0.1",
                "service": "behavior-analytics",
                "scenario": args.scenario,
                "config_id": args.config_id,
                "success": False,
                "error": f"run_benchmark.sh exited {completed.returncode}",
            },
        )
    return finish_result(
        dest,
        success=success,
        should_upload=should_upload,
        args=args,
    )


def _upload(dest: Path, args: argparse.Namespace) -> dict[str, str]:
    published = publish_result(dest, config_path=args.config, config_id=args.config_id)
    print(f"Uploaded {published['uri']}", flush=True)
    return published


if __name__ == "__main__":
    raise SystemExit(main())
