#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import fnmatch
import json
import os
import re
from pathlib import Path
from typing import Any

MODEL_PATH_ALLOWLIST_ENV = "RTVI_MODEL_PATH_ALLOWLIST"
ENFORCE_MODEL_PATH_ALLOWLIST_ENV = "RTVI_ENFORCE_MODEL_PATH_ALLOWLIST"
VLM_TRUST_REMOTE_CODE_ENV = "VLM_TRUST_REMOTE_CODE"
ALLOW_UNSAFE_MODEL_CONFIG_ENV = "RTVI_ALLOW_UNSAFE_MODEL_CONFIG"
BLOCKED_CONFIG_KEYS = frozenset(
    {
        "_attn_implementation_internal",
        "_experts_implementation_internal",
    }
)


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in ("1", "true", "yes", "on")


def _allowlist_patterns() -> list[str]:
    raw_value = os.environ.get(MODEL_PATH_ALLOWLIST_ENV, "")
    return [item.strip() for item in re.split(r"[\n,]", raw_value) if item.strip()]


def _trust_remote_code_enabled() -> bool:
    return _truthy(os.environ.get(VLM_TRUST_REMOTE_CODE_ENV))


def _unsafe_model_config_allowed() -> bool:
    return _trust_remote_code_enabled() and _truthy(os.environ.get(ALLOW_UNSAFE_MODEL_CONFIG_ENV))


def validate_model_path_source(model_path: str) -> None:
    """Fail closed when MODEL_PATH allowlist enforcement is enabled."""
    if not model_path or not (
        _truthy(os.environ.get(ENFORCE_MODEL_PATH_ALLOWLIST_ENV))
        or os.environ.get(MODEL_PATH_ALLOWLIST_ENV)
        or _trust_remote_code_enabled()
    ):
        return

    patterns = _allowlist_patterns()
    if not patterns:
        raise ValueError(f"{MODEL_PATH_ALLOWLIST_ENV} must be set when allowlist enforcement is on")

    if not any(fnmatch.fnmatchcase(model_path, pattern) for pattern in patterns):
        raise ValueError("MODEL_PATH is not allowlisted")


def _find_blocked_config_keys(value: Any, prefix: str = "") -> list[str]:
    found: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            if key in BLOCKED_CONFIG_KEYS:
                found.append(path)
            found.extend(_find_blocked_config_keys(child, path))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            found.extend(_find_blocked_config_keys(child, f"{prefix}[{index}]"))
    return found


def validate_model_config(model_path: str, model_source: str | None = None) -> None:
    """Reject known unsafe Transformers config hooks before any model loader runs."""
    config_path = Path(model_path) / "config.json"
    if not config_path.exists():
        return

    config = json.loads(config_path.read_text(encoding="utf-8"))
    blocked_keys = _find_blocked_config_keys(config)
    if not blocked_keys:
        return

    if _unsafe_model_config_allowed() and model_source:
        validate_model_path_source(model_source)
        return

    raise ValueError("Blocked unsafe model config field(s): " + ", ".join(sorted(blocked_keys)))
