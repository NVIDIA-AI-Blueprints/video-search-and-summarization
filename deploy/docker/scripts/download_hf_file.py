#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES.
# SPDX-License-Identifier: Apache-2.0
"""Download one immutable Hugging Face Hub file for a Compose init service."""

from __future__ import annotations

import argparse
import os
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


def _hf_token_from_environment() -> str | None:
    for name in HF_AUTH_ENV_VARS[:3]:
        if token := os.environ.get(name):
            return token
    return None


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
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-id", required=True)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--filename", required=True)
    parser.add_argument("--local-dir", required=True)
    args = parser.parse_args()

    if len(args.revision) not in range(40, 65) or any(
        character not in "0123456789abcdef" for character in args.revision
    ):
        parser.error(
            "--revision must be a 40-64 character lowercase hexadecimal commit"
        )
    try:
        installed = version("huggingface_hub")
    except PackageNotFoundError:
        parser.error(f"huggingface_hub=={SUPPORTED_HF_HUB_VERSION} is required")
    if installed != SUPPORTED_HF_HUB_VERSION:
        parser.error(
            f"unsupported huggingface_hub version {installed}; "
            f"expected {SUPPORTED_HF_HUB_VERSION}"
        )

    endpoint = os.environ.get("HF_ENDPOINT") or None
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
            parser.error(
                "HF_ENDPOINT must be an HTTP(S) origin without credentials or query data"
            )
        endpoint = endpoint.rstrip("/")
        if parsed.scheme == "http":
            if not _http_endpoint_is_approved(endpoint):
                parser.error(
                    "HTTP HF_ENDPOINT requires an explicitly approved host-cache origin"
                )
            if _has_hf_auth_configuration():
                parser.error(
                    "Hugging Face authentication is not permitted with HTTP HF_ENDPOINT"
                )
            os.environ["HF_HUB_DISABLE_IMPLICIT_TOKEN"] = "1"
        os.environ["HF_ENDPOINT"] = endpoint
    else:
        # An empty Compose expansion overrides huggingface_hub's official
        # default during import even when endpoint=None is passed below.
        os.environ.pop("HF_ENDPOINT", None)

    os.environ["HF_HUB_DISABLE_XET"] = "1"
    os.environ["HF_HUB_ENABLE_HF_TRANSFER"] = "0"
    from huggingface_hub import constants, hf_hub_download

    if not constants.HF_HUB_DISABLE_XET:
        parser.error(
            "huggingface_hub initialized before HF_HUB_DISABLE_XET=1; "
            "set it in the container environment"
        )

    local_dir = Path(args.local_dir)
    local_dir.mkdir(parents=True, exist_ok=True)
    downloaded = hf_hub_download(
        repo_id=args.repo_id,
        revision=args.revision,
        filename=args.filename,
        endpoint=endpoint,
        token=(
            False
            if endpoint and endpoint.startswith("http://")
            else _hf_token_from_environment()
        ),
        cache_dir=os.environ["HF_HOME"],
        local_dir=local_dir,
    )
    if Path(downloaded).resolve() != (local_dir / args.filename).resolve():
        parser.error("Hub client returned an unexpected local destination")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
