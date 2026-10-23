# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""MinIO publishing for canonical VSS performance results."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

DEFAULT_MINIO_ENDPOINT = "10.111.52.248:9000"
DEFAULT_MINIO_BUCKET = "perf-results"
DEFAULT_MINIO_ACCESS_KEY = "admin"
DEFAULT_MINIO_SECRET_KEY = "vss_automation"


def get_minio_endpoint() -> str:
    return os.environ.get("MINIO_ENDPOINT", DEFAULT_MINIO_ENDPOINT)


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
