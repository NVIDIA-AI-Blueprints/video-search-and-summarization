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
"""Data models for retrieval results."""
from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel
from pydantic import Field


class ContentType(StrEnum):
    TEXT = "text"
    TABLE = "table"
    CHART = "chart"
    IMAGE = "image"


class Chunk(BaseModel):
    chunk_id: str
    content: str
    score: float
    metadata: dict[str, Any] = Field(default_factory=dict)


class RetrievalResult(BaseModel):
    chunks: list[Chunk] = Field(default_factory=list)
    query: str = ""
    backend: str = ""
    success: bool = True
    error_message: str | None = None
    total_tokens: int = 0
    summary: str | None = None
