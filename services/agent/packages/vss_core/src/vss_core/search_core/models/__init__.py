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

from .attribute_search import AttributeSearchInput
from .attribute_search import AttributeSearchMetadata
from .attribute_search import AttributeSearchOutput
from .attribute_search import AttributeSearchResult
from .common import SourceType
from .embed_search import EmbedSearchInput
from .embed_search import EmbedSearchOutput
from .embed_search import EmbedSearchResultItem
from .search import SearchInput
from .search import SearchOutput
from .search import SearchResult
from .search import SearchVerification
from .tag_search import TagSearchInput
from .tag_search import TagSearchOutput
from .tag_search import TagSearchResultItem

__all__ = [
    "AttributeSearchInput",
    "AttributeSearchMetadata",
    "AttributeSearchOutput",
    "AttributeSearchResult",
    "EmbedSearchInput",
    "EmbedSearchOutput",
    "EmbedSearchResultItem",
    "SearchInput",
    "SearchOutput",
    "SearchResult",
    "SearchVerification",
    "SourceType",
    "TagSearchInput",
    "TagSearchOutput",
    "TagSearchResultItem",
]
