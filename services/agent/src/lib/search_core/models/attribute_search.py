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
"""Attribute-search input/output models.

Mirrors tools/attribute_search.py:97 (AttributeSearchInput) and L145-L171
(metadata + result), with two tightenings:
  - source_type uses SourceType Literal instead of plain str (DESIGN.md §5.3).
  - Output wrapped in AttributeSearchOutput (today returns bare list).
The NAT shim widens source_type back to str and unwraps the envelope so the
existing /api/v1/attribute_search response shape is preserved.
"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003  Pydantic field annotation; resolved at runtime
from typing import Any

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field

# ``SourceType`` and ``datetime`` appear in Pydantic field annotations on
# these models; Pydantic v2 needs them importable at runtime so it can
# resolve the stringified annotations at model_build time.
from .common import SourceType  # noqa: TC001  Pydantic-resolved at runtime


class AttributeSearchInput(BaseModel):
    """Input for attribute-based search."""

    model_config = ConfigDict(extra="forbid")

    query: str | list[str]  # single attribute or list (e.g. ["person", "red hat"])
    source_type: SourceType = "video_file"
    timestamp_start: datetime | None = None
    timestamp_end: datetime | None = None
    video_sources: list[str] | None = None
    top_k: int = Field(default=1, ge=1, le=1000)
    min_similarity: float = Field(default=0.3, ge=0.0, le=1.0)
    fuse_multi_attribute: bool = True
    exclude_videos: list[dict[str, str]] = Field(default_factory=list)


class AttributeSearchMetadata(BaseModel):
    """Per-result metadata produced by AttributeSearch.

    Nullable string fields and the open-shape ``bbox`` dict mirror the legacy
    NAT model (tools/attribute_search.py:145-157 in HEAD). The behavior data
    upstream genuinely produces ``None`` for any of the timestamp/name fields
    when a hit lacks the corresponding source field, and the bbox payload uses
    ``leftX/rightX/topY/bottomY`` keys — a strict typed model here would
    reject real, valid documents at the ``_build_result`` boundary.

    ``frame_timestamp`` is nullable too: ``_build_result`` produces ``None`` when
    a hit has no best-frame timestamp and its ``_source`` carries neither
    ``timestamp`` nor ``end``. The one consumer (``enrich_attribute_results``)
    already guards ``if ts``, so ``None`` is safe.
    """

    model_config = ConfigDict(extra="forbid")
    sensor_id: str
    object_id: str
    object_type: str
    frame_timestamp: str | None = None
    start_time: str | None = None
    end_time: str | None = None
    bbox: dict[str, Any] | None = None
    behavior_score: float = 0.0
    frame_score: float | None = None
    video_name: str | None = None


class AttributeSearchResult(BaseModel):
    """A single attribute-search result with URL + metadata."""

    model_config = ConfigDict(extra="forbid")
    screenshot_url: str | None = None
    metadata: AttributeSearchMetadata


class AttributeSearchOutput(BaseModel):
    """Output envelope. NAT shim unwraps to a bare list for wire compatibility."""

    model_config = ConfigDict(extra="forbid")
    results: list[AttributeSearchResult] = Field(default_factory=list)
