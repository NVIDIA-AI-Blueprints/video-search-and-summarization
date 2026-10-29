# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from vss_perf_reporting import (
    DEFAULT_MINIO_ACCESS_KEY,
    DEFAULT_MINIO_SECRET_KEY,
    VSSBenchmarkResult,
    MinIOUploader,
    TriggerSource,
    build_and_save,
    build_result,
    generate_run_id,
    get_minio_endpoint,
    publish_result_directory,
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



def test_minio_credentials_are_required(monkeypatch) -> None:
    assert DEFAULT_MINIO_ACCESS_KEY == ""
    assert DEFAULT_MINIO_SECRET_KEY == ""
    monkeypatch.delenv("MINIO_ACCESS_KEY", raising=False)
    monkeypatch.delenv("MINIO_SECRET_KEY", raising=False)
    with pytest.raises(ValueError, match="MINIO_ACCESS_KEY and MINIO_SECRET_KEY required"):
        MinIOUploader(endpoint="minio.example.internal:9443")


def test_minio_explicit_credentials_are_accepted(monkeypatch) -> None:
    monkeypatch.delenv("MINIO_ACCESS_KEY", raising=False)
    monkeypatch.delenv("MINIO_SECRET_KEY", raising=False)
    uploader = MinIOUploader(
        endpoint="minio.example.internal:9443",
        access_key="explicit-access",
        secret_key="explicit-secret",
    )
    assert uploader.access_key == "explicit-access"
    assert uploader.secret_key == "explicit-secret"


def test_summary_totals_are_validated() -> None:
    with pytest.raises(ValueError):
        build_result(
            service="LVS",
            run_id="invalid",
            test_cases=[],
            passed=-1,
            failed=0,
        )


def test_publish_directory_writes_jenkins_compatible_receipt(monkeypatch, tmp_path) -> None:
    result = build_and_save(
        tmp_path / "nested" / "vss-perf-results",
        "Behavior-Analytics",
        [{"test_case_id": "playback", "status": "PASS", "metrics": {}}],
        config_id="warehouse-2d",
        run_id="behavior-analytics_warehouse-2d_20261023_121500",
        passed=1,
        failed=0,
    )
    (tmp_path / "execution_summary.json").write_text(
        '{"success": true, "test_cases": []}\n'
    )
    uploads = []

    class FakeUploader:
        def __init__(self, endpoint, access_key, secret_key, secure):
            uploads.append((endpoint, access_key, secret_key, secure))

        def upload_benchmark_result(self, local_file, service, bucket):
            uploads.append((local_file, service, bucket))
            return True

    monkeypatch.setattr("vss_perf_reporting.storage.MinIOUploader", FakeUploader)
    monkeypatch.setenv("BUILD_URL", "https://jenkins.example/job/42/")
    receipts = publish_result_directory(
        tmp_path,
        endpoint="minio.example:9000",
        bucket="perf-results-dev",
        access_key="access",
        secret_key="secret",
    )

    assert len(receipts) == 1
    assert uploads[1] == (str(result), "Behavior-Analytics", "perf-results-dev")
    assert receipts[0]["object"] == f"Behavior-Analytics/{result.name}"
    assert receipts[0]["pipeline_url"] == "https://jenkins.example/job/42/"
    assert len(receipts[0]["sha256"]) == 64
    receipt_path = result.with_name(f"{result.name}.upload.json")
    assert json.loads(receipt_path.read_text()) == receipts[0]


def test_publish_directory_rejects_unsafe_service(tmp_path) -> None:
    payload = build_result(
        service="unsafe/service",
        run_id="unsafe",
        test_cases=[],
        passed=0,
        failed=0,
    )
    canonical_dir = tmp_path / "vss-perf-results"
    canonical_dir.mkdir()
    (canonical_dir / "unsafe.json").write_text(json.dumps(payload))

    with pytest.raises(ValueError, match="unsafe or missing metadata.service"):
        publish_result_directory(tmp_path)


def test_publish_directory_rejects_malformed_canonical_report(tmp_path) -> None:
    canonical_dir = tmp_path / "vss-perf-results"
    canonical_dir.mkdir()
    (canonical_dir / "malformed.json").write_text('{"test_cases": []}\n')

    with pytest.raises(ValueError, match="Canonical report .* is missing"):
        publish_result_directory(tmp_path)


def test_artifact_promotion_trigger_source_is_supported() -> None:
    assert TriggerSource.ARTIFACT_PROMOTION.value == "artifact_promotion"
