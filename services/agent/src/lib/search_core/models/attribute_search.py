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

``AttributeSearchInput`` is the request model; ``AttributeSearchOutput`` wraps
the list of ``AttributeSearchResult`` items (each carrying an optional screenshot
URL plus flattened ``AttributeSearchMetadata``).
"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003  Pydantic field annotation; resolved at runtime
from typing import Any

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field

from ..errors import InvalidInputError

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
    top_k: int | None = Field(default=None, ge=1, le=1000)
    min_similarity: float = Field(default=0.3, ge=0.0, le=1.0)
    fuse_multi_attribute: bool = True
    exclude_videos: list[dict[str, str]] = Field(default_factory=list)

    def normalized_queries(self) -> list[str]:
        """Return the query as a list of non-blank, stripped attribute strings."""
        raw = [self.query] if isinstance(self.query, str) else list(self.query)
        return [q.strip() for q in raw if isinstance(q, str) and q.strip()]

    def validate_semantics(self) -> None:
        """Raise :class:`InvalidInputError` for cross-field problems.

        These are values that each pass their own field constraints but are
        invalid in combination; centralizing them keeps the primitive's ``run()``
        thin and gives callers one place to exercise input semantics.
        """
        if not self.normalized_queries():
            raise InvalidInputError("AttributeSearchInput.query must contain at least one non-empty attribute")
        if self.timestamp_start and self.timestamp_end and self.timestamp_start > self.timestamp_end:
            raise InvalidInputError(
                f"timestamp_start ({self.timestamp_start.isoformat()}) must not be after "
                f"timestamp_end ({self.timestamp_end.isoformat()})"
            )


class AttributeSearchMetadata(BaseModel):
    """Per-result metadata produced by AttributeSearch.

    Timestamp/name fields are nullable because a behavior document may lack the
    corresponding source field, and ``bbox`` is an open-shape dict
    (``leftX/rightX/topY/bottomY``) — a stricter model would reject real, valid
    documents during hit mapping. Consumers already guard for ``None``.
    """

    model_config = ConfigDict(extra="forbid")
    sensor_id: str
    object_id: str | None = None
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

    @property
    def similarity(self) -> float:
        """Unified score accessor using the ranked behavior similarity."""
        return self.metadata.behavior_score


class AttributeSearchOutput(BaseModel):
    """Output envelope wrapping the list of attribute-search results."""

    model_config = ConfigDict(extra="forbid")
    results: list[AttributeSearchResult] = Field(default_factory=list)

    @property
    def data(self) -> list[AttributeSearchResult]:
        """Compatibility accessor shared with ``SearchOutput``."""
        return self.results
