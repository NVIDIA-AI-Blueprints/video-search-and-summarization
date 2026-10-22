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
"""Protocols (dependency-injection seams) for lib.search_core.

Primitives depend on these abstract surfaces; concrete client classes implement
them; tests substitute mocks. This is the only file in clients/ that may be
imported by primitives/ — concrete client classes are constructed via the
runtime, not imported directly.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from typing import Any
from typing import Protocol
from typing import runtime_checkable

if TYPE_CHECKING:
    from collections.abc import Mapping


@runtime_checkable
class TextEmbedder(Protocol):
    async def get_text_embedding(self, text: str) -> list[float]: ...
    async def aclose(self) -> None: ...


@runtime_checkable
class ImageEmbedder(Protocol):
    async def get_image_embedding(self, image_url: str) -> list[float]: ...


@runtime_checkable
class VideoEmbedder(Protocol):
    async def get_video_embedding(self, video_url: str) -> list[float]: ...


@runtime_checkable
class CosmosEmbedder(TextEmbedder, ImageEmbedder, VideoEmbedder, Protocol):
    """Full Cosmos client surface; search primitives currently use text only."""


@runtime_checkable
class CVTextEmbedder(TextEmbedder, Protocol):
    """RTVI CV — text-only embeddings used by attribute_search.

    The underlying service only exposes text-embedding endpoints today;
    image/video methods would raise NotImplementedError if anyone called
    them, which is why we model this surface separately from CosmosEmbedder.
    """


@runtime_checkable
class ElasticIndex(Protocol):
    """Elasticsearch surface used by primitives.

    Matches the subset of elasticsearch.AsyncElasticsearch that search and
    tag ingestion use today — raw search and single-document index calls. Keeping
    the surface minimal makes primitives mockable without spinning up ES.
    The concrete ElasticClient (clients/elastic.py) wraps the existing
    endpoint registry and forwards calls through to its underlying
    AsyncElasticsearch.

    NOTE: an earlier design draft proposed higher-level knn_search/term_search
    methods. That was aspirational — the actual NAT code builds raw queries
    inline, and rewriting them for a tighter protocol is out of scope for the
    refactor. We may revisit this in a follow-up.
    """

    async def search(
        self,
        *,
        index: str | list[str],
        body: Mapping[str, Any] | None = None,
        **kwargs: Any,
    ) -> Any: ...

    async def index(
        self,
        *,
        index: str,
        document: Mapping[str, Any],
        id: str | None = None,
        **kwargs: Any,
    ) -> Any: ...

    async def aclose(self) -> None: ...

    @property
    def endpoint(self) -> str:
        """Backing endpoint URL — the orchestrator's config object needs to
        forward it to helpers that re-resolve clients (e.g. the object-id
        re-search path), so the protocol exposes it."""
        ...
