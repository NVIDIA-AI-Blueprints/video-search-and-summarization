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

"""Logging utilities for safe/compact payload rendering.

Redacts heavy geo fields and collapses large arrays before serializing to JSON
so we can log a single compact line without dumping huge content.
"""

from __future__ import annotations

import copy
import json
from typing import Any, Iterable, Mapping


def _collapse_value(value: Any) -> str:
    if isinstance(value, list):
        return f"<omitted {len(value)} entries>"
    if isinstance(value, dict):
        return "<omitted object>"
    return "<omitted>"


def redact_payload_for_log(
    document: Mapping[str, Any],
    *,
    collapse_fields: Iterable[str] = ("edges", "embeddings", "frames"),
    mask_coordinate_nodes: Iterable[str] = ("locations", "smoothLocations"),
) -> str:
    """Return a compact JSON string with heavy fields redacted.

    - For nodes listed in ``mask_coordinate_nodes``: if they are dicts with a
      ``coordinates`` member, replace it with a size marker string.
    - For ``collapse_fields``: if present, replace the entire value with a
      size marker string.
    """

    safe = copy.deepcopy(document)

    # Mask coordinates for known geo nodes
    for node_key in mask_coordinate_nodes:
        node = safe.get(node_key)
        if isinstance(node, dict) and "coordinates" in node:
            coords = node.get("coordinates")
            if isinstance(coords, list):
                node["coordinates"] = f"<omitted {len(coords)} entries>"
            else:
                node["coordinates"] = "<omitted>"

    # Collapse heavy fields entirely
    for key in collapse_fields:
        if key in safe:
            safe[key] = _collapse_value(safe[key])

    return json.dumps(safe, separators=(",", ":"), ensure_ascii=False)


