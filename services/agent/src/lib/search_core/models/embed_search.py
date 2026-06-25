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

The library's EmbedSearchInput is a v1 redesign of today's wire-shape QueryInput
(see services/agent/src/vss_agents/tools/embed_search.py:106). The NAT shim
translates QueryInput → EmbedSearchInput so /api/v1/embed_search keeps the
existing wire format. See DESIGN.md §5.2 and §9.1.
"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003  Pydantic field annotation; resolved at runtime

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field

# ``SourceType`` and ``datetime`` appear as Pydantic field annotations on
# this model; Pydantic v2 resolves the (stringified) annotations at
# model_build time, so they must be importable at runtime. Don't move them
# into a ``TYPE_CHECKING`` block.
from .common import SourceType  # noqa: TC001  Pydantic-resolved at runtime


class EmbedSearchInput(BaseModel):
    """Flat, typed input for EmbedSearch.run().

    Today's NAT input (QueryInput) uses a free-form params dict. This model
    surfaces the same parameters as explicit, typed fields.
    """

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
            "embed_default_max_results (from SearchRuntime) wins — letting the "
            "deployed embed_search config default override the model default."
        ),
    )
    # Cosine similarity is in [-1, 1]; the UI sends negative thresholds for
    # low-confidence searches, so don't clamp the lower bound to 0.
    min_cosine_similarity: float = Field(default=0.0, ge=-1.0, le=1.0)
    exclude_videos: list[dict[str, str]] = Field(default_factory=list)
    # Bypass the embed-client call when the caller already has the vector.
    # Replaces today's `embeddings: list[dict]` slot in QueryInput.
    precomputed_embedding: list[float] | None = None


class EmbedSearchResultItem(BaseModel):
    """A single embed-search result with all fields flattened.

    Mirrors today's EmbedSearchResultItem at tools/embed_search.py around line 85.
    """

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
