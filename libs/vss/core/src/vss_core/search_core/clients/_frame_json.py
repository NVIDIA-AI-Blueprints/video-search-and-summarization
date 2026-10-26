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
"""Fast finite builtin JSON for frame msearch; native fallback for other inputs."""

from __future__ import annotations

import math
from typing import Any

from elasticsearch.serializer import NdjsonSerializer
import orjson


def encode_frame_searches(searches: list[dict[str, Any]]) -> bytes:
    """Preserve native handling of special values without NumPy conversion.

    Validation is memoized for shared parameter arrays. The input list keeps
    those containers alive throughout encoding, preventing identity reuse.
    No transport-wide serializer or bulk indexing behavior is changed.
    """
    native = NdjsonSerializer()
    memo: dict[int, bool] = {}
    visiting: set[int] = set()

    def safe(value: Any) -> bool:
        typ = type(value)
        if typ in (str, bool, type(None)):
            return True
        if typ is int:
            return -(2**63) <= value < 2**64
        if typ is float:
            return math.isfinite(value)
        if typ not in (dict, list, tuple):
            return False
        key = id(value)
        if key in memo:
            return memo[key]
        if key in visiting:
            return False
        visiting.add(key)
        result = (
            all(type(k) is str and safe(v) for k, v in value.items()) if typ is dict else all(safe(v) for v in value)
        )
        visiting.remove(key)
        memo[key] = result
        return result

    parts: list[bytes] = []
    for line in searches:
        try:
            compatible = safe(line)
        except RecursionError:
            compatible = False
        if compatible:
            try:
                parts.extend((orjson.dumps(line), b"\n"))
                continue
            except (TypeError, ValueError):
                pass
        parts.append(native.dumps([line]))
    return b"".join(parts)
