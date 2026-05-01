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
"""Universal data models for retrieval results.

Backend-agnostic. Adapters normalise their native response format into
these types so callers see a consistent shape regardless of backend.
"""
from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel
from pydantic import Field


class ContentType(StrEnum):
    """Content modality of a retrieved chunk."""

    TEXT = "text"
    TABLE = "table"
    CHART = "chart"
    IMAGE = "image"


class Chunk(BaseModel):
    """A single retrieved excerpt with citation metadata."""

    chunk_id: str
    content: str
    score: float = 0.0
    file_name: str = "unknown"
    page_number: int | None = None
    display_citation: str = ""
    content_type: ContentType = ContentType.TEXT
    metadata: dict[str, Any] = Field(default_factory=dict)


class RetrievalResult(BaseModel):
    """Result of a single retrieve() call."""

    chunks: list[Chunk] = Field(default_factory=list)
    query: str = ""
    backend: str = ""
    success: bool = True
    error_message: str | None = None
    total_tokens: int = 0
    summary: str | None = None
