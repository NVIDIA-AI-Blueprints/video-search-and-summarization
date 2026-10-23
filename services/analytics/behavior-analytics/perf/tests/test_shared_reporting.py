# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
import sys
from pathlib import Path

PERF_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = PERF_ROOT.parents[3]
sys.path.insert(0, str(REPO_ROOT / "libs" / "vss-perf-reporting" / "src"))
sys.path.insert(0, str(PERF_ROOT / "benchmark"))

from vss_perf_ba_adapter import failure_payload, to_test_cases  # noqa: E402
from vss_perf_reporting import VSSBenchmarkResult, build_and_save  # noqa: E402


def test_ba_adapter_preserves_native_metrics(tmp_path) -> None:
    native = {
        "success": True,
        "setup": "warehouse_2d_kafka_playback",
        "correction": "topic_summary_minus_raw",
        "duration_seconds": 60,
        "warmup_seconds": 10,
        "raw": {"count": 100},
        "topics": {"behavior": {"corrected": {"p99_s": 0.12}}},
        "kpis": {"passed": True, "violations": []},
    }
    test_cases = to_test_cases(native)
    path = build_and_save(
        tmp_path,
        "Behavior-Analytics",
        test_cases,
        config_id="warehouse-2d",
        run_id="behavior-analytics_warehouse-2d_20261023_121500",
        benchmark_name="quick_test",
        benchmark_mode="kafka_playback",
        passed=1,
        failed=0,
    )
    result = json.loads(path.read_text())
    VSSBenchmarkResult.model_validate(result)
    assert path.name == "behavior-analytics_warehouse-2d_20261023_121500.json"
    assert result["metadata"]["service"] == "Behavior-Analytics"
    assert result["test_cases"][0]["metrics"]["topics"] == native["topics"]


def test_ba_failure_is_a_canonical_failed_test_case() -> None:
    native = failure_payload(
        scenario="quick_test",
        config_id="warehouse-2d",
        stage="deploy",
        message="boom",
    )
    test_case = to_test_cases(native)[0]
    assert test_case["status"] == "FAIL"
    assert test_case["error"] == {"stage": "deploy", "message": "boom"}
