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
"""Pydantic input/output models for the search primitives."""

from __future__ import annotations

from .attribute_search import (
    AttributeSearchInput,
    AttributeSearchMetadata,
    AttributeSearchOutput,
    AttributeSearchResult,
)
from .common import SourceType, VideoInfo
from .critic import CriticAgentInput, CriticAgentOutput, CriticAgentResult, VideoResult
from .embed_search import EmbedSearchInput, EmbedSearchOutput, EmbedSearchResultItem
from .search import CriticResult, SearchInput, SearchOutput, SearchResult

__all__ = [
    "AttributeSearchInput",
    "AttributeSearchMetadata",
    "AttributeSearchOutput",
    "AttributeSearchResult",
    "CriticAgentInput",
    "CriticAgentOutput",
    "CriticAgentResult",
    "CriticResult",
    "EmbedSearchInput",
    "EmbedSearchOutput",
    "EmbedSearchResultItem",
    "SearchInput",
    "SearchOutput",
    "SearchResult",
    "SourceType",
    "VideoInfo",
    "VideoResult",
]
