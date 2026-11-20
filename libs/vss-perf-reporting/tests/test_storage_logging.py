# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Storage failures retain their causes while the boolean API remains compatible."""
import logging
from unittest.mock import Mock

import pytest
from minio.error import S3Error

from vss_perf_reporting.storage import MinIOUploader, upload_result_file


@pytest.mark.parametrize("operation,description", [
    ("bucket_exists", "check bucket existence"),
    ("make_bucket", "create bucket"),
    ("fput_object", "upload object"),
])
def test_upload_logs_failing_operation_traceback_and_redacts_credentials(
    monkeypatch, tmp_path, caplog, operation, description,
):
    client = Mock()
    client.bucket_exists.return_value = operation != "make_bucket"
    client.configure_mock(**{operation + ".side_effect": ConnectionError(
        "Connection reset: explicit-access explicit-secret environment-access environment-secret"
    )})
    monkeypatch.setattr("minio.Minio", lambda **kwargs: client)
    monkeypatch.setenv("MINIO_ACCESS_KEY", "environment-access")
    monkeypatch.setenv("MINIO_SECRET_KEY", "environment-secret")
    uploader = MinIOUploader("minio.example:9000", "explicit-access", "explicit-secret")
    report = tmp_path / "report.json"
    report.write_text("{}")
    with caplog.at_level(logging.ERROR):
        assert uploader.upload_file(str(report), "perf-results", "RTVI-Embed/report.json") is False
    assert f"MinIO {description} failed" in caplog.text
    assert "endpoint=minio.example:9000" in caplog.text
    assert "bucket=perf-results" in caplog.text
    assert "object=RTVI-Embed/report.json" in caplog.text
    assert str(report) in caplog.text
    assert "Traceback (most recent call last)" in caplog.text
    assert "ConnectionError: Connection reset" in caplog.text
    for secret in ["explicit-access", "explicit-secret", "environment-access", "environment-secret"]:
        assert secret not in caplog.text
    assert "[REDACTED]" in caplog.text


def test_constructor_failure_logs_cause_and_keeps_false_return(monkeypatch, caplog):
    def fail(**kwargs):
        raise ValueError("Invalid endpoint with configured-secret")
    monkeypatch.setattr("minio.Minio", fail)
    assert upload_result_file("report.json", "RTVI-Embed", endpoint="bad-endpoint",
                              access_key="configured-access", secret_key="configured-secret") is False
    assert "ValueError: Invalid endpoint" in caplog.text
    assert "configured-secret" not in caplog.text
    assert "service=RTVI-Embed" in caplog.text


def test_s3_error_code_and_request_id_remain_visible(monkeypatch, tmp_path, caplog):
    client = Mock()
    client.bucket_exists.return_value = True
    client.fput_object.side_effect = S3Error(
        response=None, code="AccessDenied", message="permission denied",
        resource="/perf-results/report.json", request_id="request-123", host_id="host-456",
    )
    monkeypatch.setattr("minio.Minio", lambda **kwargs: client)
    report = tmp_path / "report.json"
    report.write_text("{}")
    assert MinIOUploader("minio.example:9000", "access-value", "secret-value").upload_file(
        str(report), "perf-results") is False
    assert "S3Error" in caplog.text
    assert "AccessDenied" in caplog.text
    assert "request-123" in caplog.text


def test_listing_failure_is_logged_and_retains_empty_result(monkeypatch, caplog):
    client = Mock()
    client.list_objects.side_effect = RuntimeError("listing unavailable")
    monkeypatch.setattr("minio.Minio", lambda **kwargs: client)
    assert MinIOUploader("minio.example:9000", "access-value", "secret-value").list_objects(
        "perf-results", "RTVI-Embed/") == []
    assert "MinIO list objects failed" in caplog.text
    assert "RuntimeError: listing unavailable" in caplog.text


def test_missing_file_is_logged_without_attempting_upload(monkeypatch, tmp_path, caplog):
    client = Mock()
    monkeypatch.setattr("minio.Minio", lambda **kwargs: client)
    assert MinIOUploader("minio.example:9000", "access-value", "secret-value").upload_file(
        str(tmp_path / "missing.json"), "perf-results") is False
    assert "source is not a file" in caplog.text
    client.bucket_exists.assert_not_called()
