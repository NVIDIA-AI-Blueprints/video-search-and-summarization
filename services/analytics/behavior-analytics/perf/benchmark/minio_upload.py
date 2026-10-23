# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Publish a canonical Behavior Analytics result to the shared MinIO bucket."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Mapping

REPO_ROOT = Path(__file__).resolve().parents[5]
SHARED_REPORTING_SRC = REPO_ROOT / "libs" / "vss-perf-reporting" / "src"
if str(SHARED_REPORTING_SRC) not in sys.path:
    sys.path.insert(0, str(SHARED_REPORTING_SRC))

from vss_perf_reporting import publish_result_file  # noqa: E402

DEFAULT_ENDPOINT = "10.111.52.248:9000"
DEFAULT_BUCKET = "perf-results-dev"
DEFAULT_SERVICE = "behavior-analytics"


def minio_settings(config_path: Path) -> dict[str, Any]:
    """Read global.minio from config.yaml; environment variables override."""
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
    service = os.environ.get("MINIO_UPLOAD_SERVICE") or settings.get("service") or DEFAULT_SERVICE
    return {
        "endpoint": str(endpoint).strip(),
        "bucket": str(bucket).strip(),
        "service": str(service).strip(),
        "secure": bool(settings.get("secure", False)),
    }


def publish_result(result_path: Path, *, config_path: Path, config_id: str) -> dict[str, str]:
    """Upload the canonical result without copying or renaming it."""
    del config_id  # retained for compatibility with existing callers
    settings = minio_settings(config_path)
    access_key = os.environ.get("MINIO_ACCESS_KEY", "")
    secret_key = os.environ.get("MINIO_SECRET_KEY", "")
    if not access_key or not secret_key:
        raise ValueError("MinIO credentials are required for upload")
    return publish_result_file(
        result_path,
        settings["service"],
        endpoint=settings["endpoint"],
        bucket=settings["bucket"],
        access_key=access_key,
        secret_key=secret_key,
        secure=settings["secure"],
    )


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
