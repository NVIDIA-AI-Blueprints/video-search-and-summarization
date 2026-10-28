# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""MinIO publishing for canonical VSS performance results."""

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .schema import VSSBenchmarkResult

DEFAULT_MINIO_BUCKET = "perf-results"
DEFAULT_MINIO_ACCESS_KEY = "admin"
DEFAULT_MINIO_SECRET_KEY = "vss_automation"

_CANONICAL_FIELDS = {"schema_version", "metadata", "summary", "test_cases"}
_CANONICAL_MARKERS = {"metadata", "summary", "test_cases"}
_SAFE_SERVICE = re.compile(r"[A-Za-z0-9._-]+")


def get_minio_endpoint() -> str:
    endpoint = os.environ.get("MINIO_ENDPOINT", "").strip()
    if not endpoint:
        raise ValueError("MINIO_ENDPOINT is required for MinIO uploads")
    return endpoint


def get_minio_bucket() -> str:
    return os.environ.get("MINIO_BUCKET", DEFAULT_MINIO_BUCKET)


class MinIOUploader:
    def __init__(
        self,
        endpoint: str | None = None,
        access_key: str | None = None,
        secret_key: str | None = None,
        secure: bool = False,
    ) -> None:
        from minio import Minio

        self.endpoint = endpoint if endpoint is not None else get_minio_endpoint()
        self.access_key = access_key or os.getenv("MINIO_ACCESS_KEY", DEFAULT_MINIO_ACCESS_KEY)
        self.secret_key = secret_key or os.getenv("MINIO_SECRET_KEY", DEFAULT_MINIO_SECRET_KEY)
        self.secure = secure
        self.client = Minio(
            endpoint=self.endpoint,
            access_key=self.access_key,
            secret_key=self.secret_key,
            secure=secure,
        )

    def upload_file(
        self,
        local_file_path: str,
        bucket_name: str,
        object_name: str | None = None,
        content_type: str = "application/json",
    ) -> bool:
        local_path = Path(local_file_path)
        if not local_path.is_file():
            return False
        object_name = object_name or local_path.name
        try:
            if not self.client.bucket_exists(bucket_name):
                self.client.make_bucket(bucket_name)
            self.client.fput_object(
                bucket_name=bucket_name,
                object_name=object_name,
                file_path=str(local_path),
                content_type=content_type,
            )
        except Exception:
            return False
        return True

    def upload_benchmark_result(
        self,
        local_file_path: str,
        service: str,
        bucket_name: str | None = None,
    ) -> bool:
        path = Path(local_file_path)
        return self.upload_file(
            str(path),
            bucket_name or get_minio_bucket(),
            f"{service}/{path.name}",
        )

    def list_objects(self, bucket_name: str, prefix: str = "") -> list[Any]:
        try:
            return [
                item.object_name
                for item in self.client.list_objects(bucket_name, prefix=prefix, recursive=True)
            ]
        except Exception:
            return []


def upload_result_file(
    local_file_path: str,
    service: str,
    endpoint: str | None = None,
    bucket: str | None = None,
    access_key: str | None = None,
    secret_key: str | None = None,
    secure: bool = False,
) -> bool:
    try:
        uploader = MinIOUploader(endpoint, access_key, secret_key, secure)
        return uploader.upload_benchmark_result(local_file_path, service, bucket)
    except Exception:
        return False


def publish_result_file(
    local_file_path: str | Path,
    service: str,
    *,
    endpoint: str | None = None,
    bucket: str | None = None,
    access_key: str | None = None,
    secret_key: str | None = None,
    secure: bool = False,
) -> dict[str, str]:
    path = Path(local_file_path)
    resolved_bucket = bucket or get_minio_bucket()
    object_name = f"{service}/{path.name}"
    if not upload_result_file(
        str(path),
        service,
        endpoint=endpoint,
        bucket=resolved_bucket,
        access_key=access_key,
        secret_key=secret_key,
        secure=secure,
    ):
        raise RuntimeError("Failed to upload canonical performance result")
    return {
        "bucket": resolved_bucket,
        "object": object_name,
        "file": str(path),
        "uri": f"s3://{resolved_bucket}/{object_name}",
    }


def _canonical_reports(report_dir: str | Path) -> list[tuple[Path, str]]:
    root = Path(report_dir)
    if not root.is_dir():
        raise ValueError(f"Performance report directory does not exist: {root}")

    reports: list[tuple[Path, str]] = []
    for path in sorted(root.rglob("*.json")):
        if path.name.endswith(".upload.json"):
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"Invalid JSON report candidate {path}: {exc}") from exc
        if not isinstance(payload, dict) or not _CANONICAL_MARKERS.intersection(payload):
            continue
        missing = sorted(_CANONICAL_FIELDS.difference(payload))
        if missing:
            raise ValueError(
                f"Canonical report {path} is missing: {', '.join(missing)}"
            )
        result = VSSBenchmarkResult.model_validate(payload)
        service = result.metadata.service.strip()
        if not service or _SAFE_SERVICE.fullmatch(service) is None:
            raise ValueError(
                f"Canonical report {path} has unsafe or missing metadata.service"
            )
        reports.append((path, service))

    if not reports:
        raise ValueError(f"No canonical performance report JSON found under {root}")
    return reports


def publish_result_directory(
    report_dir: str | Path,
    *,
    endpoint: str | None = None,
    bucket: str | None = None,
    access_key: str | None = None,
    secret_key: str | None = None,
    secure: bool = False,
    pipeline_url: str | None = None,
) -> list[dict[str, str]]:
    """Validate and publish every canonical result below ``report_dir``.

    Native diagnostic JSON is ignored. Each uploaded result gets a neighboring
    ``.upload.json`` receipt containing the immutable object name and checksum.
    """

    reports = _canonical_reports(report_dir)
    resolved_bucket = bucket or get_minio_bucket()
    uploader = MinIOUploader(endpoint, access_key, secret_key, secure)
    receipts: list[dict[str, str]] = []

    for path, service in reports:
        object_name = f"{service}/{path.name}"
        if not uploader.upload_benchmark_result(str(path), service, resolved_bucket):
            raise RuntimeError(f"Failed to upload canonical performance result: {path}")
        checksum = hashlib.sha256(path.read_bytes()).hexdigest()
        receipt = {
            "bucket": resolved_bucket,
            "object": object_name,
            "sha256": checksum,
            "source": str(path),
            "uri": f"s3://{resolved_bucket}/{object_name}",
            "uploaded_at": datetime.now(timezone.utc).isoformat(),
            "pipeline_url": pipeline_url
            if pipeline_url is not None
            else os.environ.get("BUILD_URL", ""),
        }
        receipt_path = path.with_name(f"{path.name}.upload.json")
        receipt_path.write_text(
            json.dumps(receipt, indent=2) + "\n", encoding="utf-8"
        )
        receipts.append(receipt)

    return receipts
