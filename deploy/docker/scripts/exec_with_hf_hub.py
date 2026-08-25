#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES.
# SPDX-License-Identifier: Apache-2.0
"""Fail closed unless the reviewed Hugging Face client is active, then exec."""

from __future__ import annotations

import os
import sys
from importlib.metadata import PackageNotFoundError, version
from urllib.parse import urlsplit

SUPPORTED_HF_HUB_VERSION = "0.36.2"


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
        if parsed.scheme == "http":
            raise SystemExit("HF_ENDPOINT must use HTTPS")
        if (
            parsed.scheme != "https"
            or not parsed.netloc
            or parsed.username
            or parsed.password
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
        ):
            raise SystemExit(
                "HF_ENDPOINT must be an HTTPS origin without credentials or query data"
            )
        endpoint = endpoint.rstrip("/")
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
