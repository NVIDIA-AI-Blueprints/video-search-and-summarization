# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

PERF_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = PERF_ROOT.parents[3]
sys.path.insert(0, str(REPO_ROOT / "libs" / "vss-perf-reporting" / "src"))
sys.path.insert(0, str(PERF_ROOT / "benchmark"))

from vss_perf_reporting import VSSBenchmarkResult, build_and_save  # noqa: E402
from vss_perf_va_api_adapter import failure_payload, to_test_cases  # noqa: E402


def endpoint_payload(*, success: bool = True) -> dict:
    violations = [] if success else ["frames.p99_s 0.124 > 0.10"]
    return {
        "schema_version": "0.1",
        "service": "video-analytics-api",
        "artifact_ref": "nvcr.io/example/video-analytics-api:3.4.0",
        "scenario": "quick_test",
        "config_id": "warehouse-es-dump",
        "request_count": 20,
        "warmup_requests": 2,
        "query_window_s": 60.0,
        "success": success,
        "endpoints": {
            "frames": {
                "present": True,
                "count": 20,
                "ok_count": 20,
                "ok_ratio": 1.0,
                "min_s": 0.02,
                "max_s": 0.13,
                "avg_s": 0.052,
                "p50_s": 0.047,
                "p95_s": 0.091,
                "p99_s": 0.124 if not success else 0.084,
                "method": "GET",
                "path": "/frames",
                "query": {"query_window_s": 60.0, "maxResultSize": 25},
            }
        },
        "kpis": {
            "passed": success,
            "violations": violations,
            "error": None if success else f"KPI violations: {violations[0]}",
        },
    }


def test_adapter_exposes_dashboard_latency_metrics(tmp_path: Path) -> None:
    cases = to_test_cases(endpoint_payload())
    path = build_and_save(
        tmp_path,
        "Video-Analytics-API",
        cases,
        config_id="warehouse-es-dump",
        run_id="video-analytics-api_warehouse-es-dump_20261029_120000",
        benchmark_name="quick_test",
        benchmark_mode="dump_backed_http",
        release="3.4.0",
        passed=1,
        failed=0,
    )
    result = json.loads(path.read_text())
    VSSBenchmarkResult.model_validate(result)
    assert result["metadata"]["service"] == "Video-Analytics-API"
    assert result["metadata"]["run_info"]["release"] == "3.4.0"
    assert result["test_cases"][0]["metrics"]["latency"]["p99_s"] == 0.084
    assert result["test_cases"][0]["metrics"]["requests"]["ok_ratio"] == 1.0


def test_option_one_latency_violation_is_a_failed_test_case() -> None:
    case = to_test_cases(endpoint_payload(success=False))[0]
    assert case["status"] == "FAIL"
    assert case["metrics"]["latency"]["p99_s"] == 0.124
    assert case["metrics"]["kpi"]["violations"] == ["frames.p99_s 0.124 > 0.10"]


def test_stage_failure_is_canonical() -> None:
    case = to_test_cases(
        failure_payload(
            scenario="quick_test",
            config_id="warehouse-es-dump",
            stage="deploy",
            message="compose failed",
        )
    )[0]
    assert case["test_case_id"] == "benchmark_execution"
    assert case["status"] == "FAIL"
    assert case["error"] == {"stage": "deploy", "message": "compose failed"}


def test_core_writes_failed_kpi_report_for_central_publication(tmp_path: Path) -> None:
    native = tmp_path / "native.json"
    native.write_text(json.dumps(endpoint_payload(success=False)))
    core_path = PERF_ROOT / "benchmark" / "core" / "vss_perf_benchmark.py"
    spec = importlib.util.spec_from_file_location("va_api_perf_core", core_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    rc = module.main(
        [
            "--output-json",
            str(tmp_path / "run"),
            "--report",
            str(native),
            "--release",
            "3.4.0",
            "--triggered-by",
            "ci_pipeline",
        ]
    )
    assert rc == 1
    reports = list((tmp_path / "run" / "vss-perf-results").glob("*.json"))
    assert len(reports) == 1
    result = json.loads(reports[0].read_text())
    assert result["metadata"]["service"] == "Video-Analytics-API"
    assert result["metadata"]["run_info"]["release"] == "3.4.0"
    assert result["summary"]["overall_status"] == "FAIL"
    assert result["test_cases"][0]["metrics"]["latency"]["p99_s"] == 0.124
