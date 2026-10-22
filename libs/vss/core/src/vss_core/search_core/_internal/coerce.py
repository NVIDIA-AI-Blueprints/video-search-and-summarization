# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Shared value-coercion helpers for hit-mapping code.

Elasticsearch/behavior documents are loosely typed: a field may be missing,
``None``, an ``int``, a ``float`` or a ``str``. These helpers normalize such a
value to a clean ``str``/``float`` while preserving the important edge cases
(``object_id == 0`` survives; empty/absent values fall back to the default).

Provided as a single source of truth so the per-primitive copies can converge on
one implementation.
"""

from __future__ import annotations

from typing import Any


def _coerce_str(value: Any, default: str = "") -> str:
    """Coerce a possibly-missing / odd-typed value to a clean string.

    Distinguishes a genuinely absent value (``None`` or ``""``) from falsy-but-
    valid values like ``0`` so that, for example, object id ``0`` is preserved
    rather than collapsed to the default.
    """
    if value is None or value == "":
        return default
    return str(value)


def _coerce_float(value: Any, default: float = 0.0) -> float:
    """Coerce a possibly-missing / odd-typed value to a float, or the default."""
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
