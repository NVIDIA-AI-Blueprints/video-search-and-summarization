#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES.
# SPDX-License-Identifier: Apache-2.0
"""Fail closed unless the reviewed Hugging Face client is active, then exec."""

from __future__ import annotations

import os
import sys
from importlib.metadata import PackageNotFoundError, version
from ipaddress import ip_address
from pathlib import Path
from urllib.parse import urlsplit

SUPPORTED_HF_HUB_VERSION = "0.36.2"
HF_AUTH_ENV_VARS = (
    "HF_TOKEN",
    "HUGGING_FACE_HUB_TOKEN",
    "HUGGINGFACE_TOKEN",
    "HF_TOKEN_PATH",
)
HF_HTTP_APPROVAL_ENV = "HF_HUB_APPROVED_HTTP_ORIGINS"


def _has_hf_auth_configuration() -> bool:
    if any(os.environ.get(name) for name in HF_AUTH_ENV_VARS):
        return True
    hf_home = Path(os.environ.get("HF_HOME") or Path.home() / ".cache" / "huggingface")
    return any((hf_home / name).is_file() for name in ("token", "stored_tokens"))


def _http_endpoint_is_approved(endpoint: str) -> bool:
    for candidate in os.environ.get(HF_HTTP_APPROVAL_ENV, "").split(","):
        candidate = candidate.strip().rstrip("/")
        if not candidate:
            continue
        parsed = urlsplit(candidate)
        try:
            address = ip_address(parsed.hostname)
        except ValueError:
            continue
        if (
            parsed.scheme == "http"
            and parsed.netloc
            and not parsed.username
            and not parsed.password
            and parsed.path in {"", "/"}
            and not parsed.query
            and not parsed.fragment
            and (address.is_private or address.is_loopback or address.is_link_local)
            and candidate == endpoint
        ):
            return True
    return False


def main() -> int:
    try:
        installed = version("huggingface_hub")
    except PackageNotFoundError:
        raise SystemExit(
            f"huggingface_hub=={SUPPORTED_HF_HUB_VERSION} is required"
        ) from None
    if installed != SUPPORTED_HF_HUB_VERSION:
        raise SystemExit(
            f"unsupported huggingface_hub version {installed}; "
            f"expected {SUPPORTED_HF_HUB_VERSION}"
        )
    if os.environ.get("HF_HUB_DISABLE_XET") != "1":
        raise SystemExit("HF_HUB_DISABLE_XET=1 is required")
    endpoint = os.environ.get("HF_ENDPOINT")
    if endpoint:
        parsed = urlsplit(endpoint)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or parsed.username
            or parsed.password
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
        ):
            raise SystemExit(
                "HF_ENDPOINT must be an HTTP(S) origin without credentials or query data"
            )
        endpoint = endpoint.rstrip("/")
        if parsed.scheme == "http":
            if not _http_endpoint_is_approved(endpoint):
                raise SystemExit(
                    "HTTP HF_ENDPOINT requires an explicitly approved host-cache origin"
                )
            if _has_hf_auth_configuration():
                raise SystemExit(
                    "Hugging Face authentication is not permitted with HTTP HF_ENDPOINT"
                )
            os.environ["HF_HUB_DISABLE_IMPLICIT_TOKEN"] = "1"
        os.environ["HF_ENDPOINT"] = endpoint
    else:
        # Compose expands an unset variable to an empty string. In
        # huggingface_hub==0.36.2 that overrides the official default with an
        # unusable empty endpoint, so remove it before the client is imported.
        os.environ.pop("HF_ENDPOINT", None)
    if len(sys.argv) < 2:
        raise SystemExit("a command is required")
    os.execvp(sys.argv[1], sys.argv[1:])
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
