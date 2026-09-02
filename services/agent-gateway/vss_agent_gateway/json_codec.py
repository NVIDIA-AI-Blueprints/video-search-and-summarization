# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Strict JSON decoding shared across trust boundaries."""

from __future__ import annotations

import json


def _reject_non_finite(value: str) -> None:
    raise ValueError(f"non-finite JSON number is not allowed: {value}")


def strict_json_loads(value: str | bytes) -> object:
    """Decode standards-compliant JSON and normalize excessive nesting errors."""

    try:
        return json.loads(value, parse_constant=_reject_non_finite)
    except RecursionError as error:
        raise ValueError("JSON nesting is too deep") from error
