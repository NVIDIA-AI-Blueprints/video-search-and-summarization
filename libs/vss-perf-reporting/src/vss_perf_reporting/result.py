# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Build, validate, and atomically write canonical VSS performance results."""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .naming import generate_run_id
from .schema import (
    DEFAULT_PLATFORM_PLACEHOLDER,
    DEFAULT_SCHEMA_VERSION,
    DEFAULT_TRIGGERED_BY,
    BenchmarkStatus,
    VSSBenchmarkResult,
)


def build_result(
    service: str,
    run_id: str,
    test_cases: list[dict[str, Any]],
    *,
    timestamp: str | None = None,
    platform: dict[str, Any] | None = None,
    config: dict[str, Any] | None = None,
    benchmark_name: str = "",
    benchmark_mode: str = "",
    triggered_by: str = DEFAULT_TRIGGERED_BY,
    pipeline_url: str = "",
    duration_seconds: float | None = None,
    release: str = "",
    passed: int | None = None,
    failed: int | None = None,
) -> dict[str, Any]:
    now = timestamp or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    total = len(test_cases)
    passed_count = passed if passed is not None else total
    failed_count = failed if failed is not None else 0
    total_tests = passed_count + failed_count
    pass_rate = round(passed_count / total_tests * 100, 1) if total_tests else 0
    if failed_count == 0 and total_tests > 0:
        status = BenchmarkStatus.PASS.value
    elif passed_count == 0:
        status = BenchmarkStatus.FAIL.value
    else:
        status = BenchmarkStatus.PARTIAL.value
    run_info: dict[str, Any] = {
        "run_id": run_id,
        "timestamp": now,
        "duration_seconds": duration_seconds,
        "triggered_by": triggered_by,
        "release": release,
    }
    if pipeline_url:
        run_info["pipeline_url"] = pipeline_url
    result = {
        "schema_version": DEFAULT_SCHEMA_VERSION,
        "metadata": {
            "service": service,
            "run_info": run_info,
            "platform": platform or DEFAULT_PLATFORM_PLACEHOLDER,
            "config": config or {},
            "benchmark_name": benchmark_name,
            "benchmark_mode": benchmark_mode,
        },
        "summary": {
            "total_tests": total_tests,
            "passed": passed_count,
            "failed": failed_count,
            "pass_rate": pass_rate,
            "overall_status": status,
        },
        "test_cases": test_cases,
    }
    VSSBenchmarkResult.model_validate(result)
    return result


def _atomic_json_write(path: Path, result: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(result, stream, indent=2)
            stream.write("\n")
        os.replace(temporary_name, path)
    except Exception:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


def build_and_save(
    output_path: str | Path,
    service: str,
    test_cases: list[dict[str, Any]],
    *,
    config_id: str = "",
    platform: dict[str, Any] | None = None,
    config: dict[str, Any] | None = None,
    run_id: str | None = None,
    benchmark_name: str = "",
    benchmark_mode: str = "",
    triggered_by: str = DEFAULT_TRIGGERED_BY,
    pipeline_url: str = "",
    duration_seconds: float | None = None,
    release: str = "",
    passed: int | None = None,
    failed: int | None = None,
) -> Path:
    path = Path(output_path)
    resolved_run_id = run_id or generate_run_id(service, config_id)
    if path.suffix != ".json":
        path = path / f"{resolved_run_id}.json"
    result = build_result(
        service=service,
        run_id=resolved_run_id,
        test_cases=test_cases,
        platform=platform,
        config=config,
        benchmark_name=benchmark_name,
        benchmark_mode=benchmark_mode,
        triggered_by=triggered_by,
        pipeline_url=pipeline_url,
        duration_seconds=duration_seconds,
        release=release,
        passed=passed,
        failed=failed,
    )
    _atomic_json_write(path, result)
    return path


def validate_result(data: dict[str, Any]) -> VSSBenchmarkResult:
    return VSSBenchmarkResult.model_validate(data)
