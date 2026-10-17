# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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
"""Lexical VLM-tag search input and output models."""

from __future__ import annotations

from datetime import datetime  # noqa: TC003 - Pydantic resolves this annotation at runtime

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field

from ..errors import InvalidInputError
from .common import SourceType  # noqa: TC001 - Pydantic resolves this annotation at runtime


class TagSearchInput(BaseModel):
    """Flat request for BM25 retrieval over indexed RT-VLM tag documents."""

    model_config = ConfigDict(extra="forbid")

    query: str
    source_type: SourceType = "video_file"
    video_sources: list[str] | None = None
    timestamp_start: datetime | None = None
    timestamp_end: datetime | None = None
    top_k: int | None = Field(default=None, ge=1, le=1000)

    def validate_semantics(self) -> None:
        if not self.query.strip():
            raise InvalidInputError("TagSearchInput.query must be non-empty")
        if self.video_sources and not all(source.strip() for source in self.video_sources):
            raise InvalidInputError("TagSearchInput.video_sources must contain only non-empty source names or IDs")
        if self.timestamp_start and self.timestamp_end and self.timestamp_start > self.timestamp_end:
            raise InvalidInputError(
                f"timestamp_start ({self.timestamp_start.isoformat()}) must not be after "
                f"timestamp_end ({self.timestamp_end.isoformat()})"
            )


class TagSearchResultItem(BaseModel):
    """One normalized RT-VLM tag hit."""

    model_config = ConfigDict(extra="forbid")

    video_name: str = ""
    description: str = ""
    start_time: str = ""
    end_time: str = ""
    sensor_id: str = ""
    screenshot_url: str = ""
    lexical_score: float = 0.0
    tags: list[str] = Field(default_factory=list)

    @property
    def similarity(self) -> float:
        """Unified score accessor shared by all search result types."""
        return self.lexical_score


class TagSearchOutput(BaseModel):
    """Envelope returned by :class:`TagSearch`."""

    model_config = ConfigDict(extra="forbid")

    results: list[TagSearchResultItem] = Field(default_factory=list)
    malformed_documents: int = 0

    @property
    def data(self) -> list[TagSearchResultItem]:
        return self.results
