# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Upload an API result JSON to the shared VSS perf MinIO bucket."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

DEFAULT_ENDPOINT = "10.111.52.248:9000"
DEFAULT_BUCKET = "perf-results-dev"
DEFAULT_SERVICE = "video-analytics-api"


def minio_settings(config_path: Path) -> dict[str, Any]:
    """Read global.minio from config.yaml; env vars override."""
    settings: dict[str, Any] = {}
    if config_path.is_file():
        try:
            import yaml  # type: ignore
        except ImportError:
            settings = _parse_minio_block(config_path.read_text())
        else:
            data = yaml.safe_load(config_path.read_text()) or {}
            raw = ((data.get("global") or {}).get("minio") or {})
            if isinstance(raw, Mapping):
                settings = dict(raw)
    endpoint = os.environ.get("MINIO_ENDPOINT") or settings.get("endpoint") or DEFAULT_ENDPOINT
    bucket = os.environ.get("MINIO_BUCKET") or settings.get("bucket") or DEFAULT_BUCKET
    service = (
        os.environ.get("MINIO_UPLOAD_SERVICE")
        or settings.get("service")
        or DEFAULT_SERVICE
    )
    return {
        "endpoint": str(endpoint).strip(),
        "bucket": str(bucket).strip(),
        "service": str(service).strip(),
        "secure": bool(settings.get("secure", False)),
    }


def timestamped_copy(result_path: Path, *, service: str, config_id: str) -> Path:
    """Copy result.json to a unique name so MinIO keys do not overwrite."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dest = result_path.with_name(f"{service}_{config_id}_{stamp}.json")
    dest.write_bytes(result_path.read_bytes())
    return dest


def upload_result_file(
    local_file: Path,
    *,
    service: str,
    endpoint: str,
    bucket: str,
    secure: bool = False,
) -> str:
    """PUT the JSON to s3://bucket/service/filename. Returns the object name."""
    access_key = os.environ.get("MINIO_ACCESS_KEY", "")
    secret_key = os.environ.get("MINIO_SECRET_KEY", "")
    if not access_key or not secret_key:
        raise ValueError("MinIO credentials are required for upload")
    from minio import Minio
    from minio.error import S3Error

    client = Minio(endpoint, access_key=access_key, secret_key=secret_key, secure=secure)
    object_name = f"{service}/{local_file.name}"
    try:
        if not client.bucket_exists(bucket):
            client.make_bucket(bucket)
        client.fput_object(
            bucket_name=bucket,
            object_name=object_name,
            file_path=str(local_file),
            content_type="application/json",
        )
    except S3Error as error:
        raise RuntimeError(f"MinIO S3 error: {error}") from error
    return object_name


def publish_result(result_path: Path, *, config_path: Path, config_id: str) -> dict[str, str]:
    """Copy and upload result.json. Returns bucket, object, and local copy path."""
    settings = minio_settings(config_path)
    uploaded = timestamped_copy(result_path, service=settings["service"], config_id=config_id)
    object_name = upload_result_file(
        uploaded,
        service=settings["service"],
        endpoint=settings["endpoint"],
        bucket=settings["bucket"],
        secure=settings["secure"],
    )
    return {
        "bucket": settings["bucket"],
        "object": object_name,
        "file": str(uploaded),
        "uri": f"s3://{settings['bucket']}/{object_name}",
    }


def _parse_minio_block(text: str) -> dict[str, str]:
    settings: dict[str, str] = {}
    in_minio = False
    for raw in text.splitlines():
        stripped = raw.split("#", 1)[0].rstrip()
        if not stripped.strip():
            continue
        if stripped.strip() == "minio:":
            in_minio = True
            continue
        if in_minio:
            indent = len(stripped) - len(stripped.lstrip(" "))
            if indent == 0:
                break
            if ":" not in stripped:
                continue
            key, _, value = stripped.strip().partition(":")
            settings[key.strip()] = value.strip().strip("\"'")
    return settings
