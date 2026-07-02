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
from typing import Literal
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
    """Full embedding surface used by embed_search (text + image + video)."""


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

    Matches the subset of elasticsearch.AsyncElasticsearch that primitives
    actually use today — raw .search(index=..., body=...) calls. Keeping
    the surface minimal makes primitives mockable without spinning up ES.
    The concrete ElasticClient (clients/elastic.py) wraps the existing
    VSSESClient registry and forwards .search() through to its underlying
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

    async def aclose(self) -> None: ...

    @property
    def endpoint(self) -> str:
        """Backing endpoint URL — the orchestrator's config object needs to
        forward it to helpers that re-resolve clients (e.g. the object-id
        re-search path), so the protocol exposes it."""
        ...


@runtime_checkable
class VSTSnapshot(Protocol):
    """VST surface used by primitives: snapshot URL + sensor/stream resolution.

    The library uses an object-oriented shape; today's free function
    `build_screenshot_url` (tools/vst/snapshot.py:49) is wrapped by the
    concrete VSTClient that implements this protocol.
    """

    def build_screenshot_url(
        self,
        *,
        sensor_id: str,
        timestamp: str,
        internal: bool = False,
    ) -> str: ...

    # Concrete implementations resolve or raise (VSTError) on a miss; they never
    # return None, so the protocol is typed ``str`` to match.
    async def resolve_stream_id(self, sensor_id: str) -> str: ...

    async def get_timeline(self, sensor_id: str) -> tuple[str, str]: ...

    async def get_video_clip_url(
        self,
        *,
        sensor_id: str,
        start_timestamp: str,
        end_timestamp: str,
        time_format: Literal["iso", "offset"],
        internal: bool = True,
        disable_audio: bool = True,
    ) -> str: ...


@runtime_checkable
class VLMAnalyzer(Protocol):
    """VLM caller protocol — CriticAgent's only VLM dependency.

    Today's CriticAgent obtains this via NAT's builder.get_function("video_understanding")
    (agents/critic_agent.py:211). The library makes it an injectable protocol so the
    primitive is NAT-free. The NAT adapter wires the existing tool; the host facade
    requires the caller to inject a concrete VLMAnalyzer.

    The `time_format` parameter mirrors today's CriticAgentConfig.time_format.
    """

    async def analyze(
        self,
        *,
        sensor_id: str,
        start_timestamp: str,
        end_timestamp: str,
        prompt: str,
        time_format: Literal["iso", "offset"] = "iso",
    ) -> str: ...
