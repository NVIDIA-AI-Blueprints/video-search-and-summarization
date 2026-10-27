# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from vss_perf_reporting import (
    VSSBenchmarkResult,
    build_and_save,
    build_result,
    generate_run_id,
    get_minio_endpoint,
    publish_result_file,
)


def test_lvs_compatible_run_id() -> None:
    assert generate_run_id(
        "LVS",
        "h100-2-1x1",
        now=datetime(2026, 10, 23, 12, 15, tzinfo=timezone.utc),
    ) == "lvs_h100-2-1x1_20261023_121500"


def test_build_and_save_uses_canonical_schema_and_filename(tmp_path) -> None:
    path = build_and_save(
        tmp_path,
        "behavior-analytics",
        [{"test_case_id": "playback", "status": "PASS", "config": {}, "metrics": {}}],
        config_id="warehouse-2d",
        run_id="behavior-analytics_warehouse-2d_20261023_121500",
        benchmark_name="quick_test",
        benchmark_mode="kafka_playback",
        passed=1,
        failed=0,
    )
    assert path.name == "behavior-analytics_warehouse-2d_20261023_121500.json"
    result = json.loads(path.read_text())
    VSSBenchmarkResult.model_validate(result)
    assert list(result) == ["schema_version", "metadata", "summary", "test_cases"]
    assert result["summary"]["overall_status"] == "PASS"


def test_failure_uses_same_schema() -> None:
    result = build_result(
        service="behavior-analytics",
        run_id="failed",
        test_cases=[{"test_case_id": "playback", "status": "FAIL", "metrics": {}}],
        passed=0,
        failed=1,
    )
    assert result["summary"]["overall_status"] == "FAIL"
    VSSBenchmarkResult.model_validate(result)


def test_publish_uses_exact_local_filename(monkeypatch, tmp_path) -> None:
    result = tmp_path / "behavior-analytics_cfg_20261023_121500.json"
    result.write_text("{}\n")
    captured = {}

    def fake_upload(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return True

    monkeypatch.setattr("vss_perf_reporting.storage.upload_result_file", fake_upload)
    receipt = publish_result_file(result, "behavior-analytics", bucket="perf-results-dev")
    assert receipt["file"] == str(result)
    assert receipt["object"] == f"behavior-analytics/{result.name}"
    assert captured["args"][0] == str(result)


def test_minio_endpoint_uses_environment_override(monkeypatch) -> None:
    monkeypatch.setenv("MINIO_ENDPOINT", "minio.example.internal:9443")
    assert get_minio_endpoint() == "minio.example.internal:9443"


def test_minio_endpoint_is_required(monkeypatch) -> None:
    monkeypatch.delenv("MINIO_ENDPOINT", raising=False)
    with pytest.raises(ValueError, match="MINIO_ENDPOINT is required"):
        get_minio_endpoint()


def test_summary_totals_are_validated() -> None:
    with pytest.raises(ValueError):
        build_result(
            service="LVS",
            run_id="invalid",
            test_cases=[],
            passed=-1,
            failed=0,
        )
