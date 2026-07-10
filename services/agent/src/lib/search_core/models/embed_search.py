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
"""Embed-search input/output models.

``EmbedSearchInput`` is a flat, typed request model; ``EmbedSearchOutput`` and
``EmbedSearchResultItem`` are the flattened response shapes returned to callers.
"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003  Pydantic field annotation; resolved at runtime

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field

from ..errors import InvalidInputError

# ``SourceType`` and ``datetime`` appear as Pydantic field annotations on
# this model; Pydantic v2 resolves the (stringified) annotations at
# model_build time, so they must be importable at runtime. Don't move them
# into a ``TYPE_CHECKING`` block.
from .common import SourceType  # noqa: TC001  Pydantic-resolved at runtime


class EmbedSearchInput(BaseModel):
    """Flat, typed input for ``EmbedSearch.run()``."""

    model_config = ConfigDict(extra="forbid")

    query: str = ""
    image_url: str | None = None
    video_url: str | None = None
    description: str | None = None
    source_type: SourceType
    video_sources: list[str] | None = None
    timestamp_start: datetime | None = None
    timestamp_end: datetime | None = None
    top_k: int | None = Field(
        default=None,
        ge=1,
        le=1000,
        description=(
            "Cap on returned results. When None, the primitive's "
            "embed_default_max_results (from SearchRuntime) is used instead."
        ),
    )
    # Cosine similarity is in [-1, 1]; the UI sends negative thresholds for
    # low-confidence searches, so don't clamp the lower bound to 0.
    min_cosine_similarity: float = Field(default=0.0, ge=-1.0, le=1.0)
    exclude_videos: list[dict[str, str]] = Field(default_factory=list)
    # Bypass the embed-client call when the caller already has the vector.
    precomputed_embedding: list[float] | None = None

    def has_embedding_source(self) -> bool:
        """True when at least one usable embedding source is present.

        A whitespace-only ``query`` and an empty ``precomputed_embedding`` do
        not count — they cannot produce a meaningful query vector.
        """
        return bool(
            self.precomputed_embedding or self.image_url or self.video_url or (self.query and self.query.strip())
        )

    def validate_semantics(self) -> None:
        """Raise :class:`InvalidInputError` for cross-field problems.

        These checks are intentionally NOT Pydantic field validators: the values
        each pass their own field constraints but are invalid in combination.
        Centralizing them here keeps the primitive's ``run()`` thin and gives
        callers (and tests) one place to exercise input semantics.
        """
        if not self.has_embedding_source():
            raise InvalidInputError(
                "EmbedSearchInput needs at least one of: query, image_url, video_url, precomputed_embedding"
            )
        if self.timestamp_start and self.timestamp_end and self.timestamp_start > self.timestamp_end:
            raise InvalidInputError(
                f"timestamp_start ({self.timestamp_start.isoformat()}) must not be after "
                f"timestamp_end ({self.timestamp_end.isoformat()})"
            )


class EmbedSearchResultItem(BaseModel):
    """A single embed-search result with all fields flattened."""

    model_config = ConfigDict(extra="forbid")
    video_name: str = ""
    description: str = ""
    start_time: str = ""  # ISO-8601 string for stable JSON
    end_time: str = ""
    sensor_id: str = ""
    screenshot_url: str = ""
    similarity_score: float = 0.0


class EmbedSearchOutput(BaseModel):
    """Output of EmbedSearch.run()."""

    model_config = ConfigDict(extra="forbid")
    query_embedding: list[float] = Field(default_factory=list)
    results: list[EmbedSearchResultItem] = Field(default_factory=list)
