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
"""AttributeSearch — object-attribute video search primitive.

Self-contained: all helper logic lives in `_attribute_helpers.py` under this
package; no upward dependency on `vss_agents.tools.attribute_search`. The NAT
shim (after Gap F) imports from here, not the reverse.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING
from typing import cast

from ..models.attribute_search import AttributeSearchInput
from ..models.attribute_search import AttributeSearchOutput
from . import _attribute_helpers

if TYPE_CHECKING:
    from elasticsearch import AsyncElasticsearch

    from ..clients.embed_base import EmbedClient
    from ..clients.protocols import CVTextEmbedder
    from ..clients.protocols import ElasticIndex
    from ..clients.protocols import VSTSnapshot
    from ..runtime import SearchRuntime

logger = logging.getLogger(__name__)


class AttributeSearch:
    """Object-attribute search using RTVI CV text embeddings + behavior index KNN."""

    def __init__(
        self,
        *,
        es: ElasticIndex,
        embed: CVTextEmbedder,
        vst: VSTSnapshot,
        behavior_index: str,
        behavior_index_wildcard: str,
        frames_index: str | None,
        frames_index_wildcard: str = "mdx-raw-*",
        enable_frame_lookup: bool = True,
        default_max_results: int = 10,
        vst_external_url: str = "",
        vst_internal_url: str | None = None,
    ) -> None:
        self._es = es
        self._embed = embed
        self._vst = vst
        self._behavior_index = behavior_index
        self._behavior_index_wildcard = behavior_index_wildcard
        self._frames_index = frames_index
        self._frames_index_wildcard = frames_index_wildcard
        self._enable_frame_lookup = enable_frame_lookup
        self._default_k = default_max_results
        self._vst_external_url = vst_external_url
        self._vst_internal_url = vst_internal_url

    async def run(self, inp: AttributeSearchInput) -> AttributeSearchOutput:
        """Execute attribute search; mirrors tools/attribute_search.py:1465-1476
        delegating into the ported `search_attributes` helper.
        """
        # The helpers only require an async `.search(index=..., body=...)`
        # surface. Passing the ElasticClient wrapper keeps backend errors
        # normalized while still allowing tests to inject lightweight mocks.
        results = await _attribute_helpers.search_attributes(
            search_input=inp,
            # CVTextEmbedder duck-types as EmbedClient for the text-only path
            # the helper exercises; ditto the ElasticIndex duck-type below.
            embed_client=cast("EmbedClient", self._embed),
            index=self._behavior_index,
            vst_external_url=self._vst_external_url,
            es=cast("AsyncElasticsearch", self._es),
            vst_internal_url=self._vst_internal_url,
            frames_index=self._frames_index,
            enable_frame_lookup=self._enable_frame_lookup,
            behavior_index_wildcard=self._behavior_index_wildcard,
            frames_index_wildcard=self._frames_index_wildcard,
        )

        logger.info(f"AttributeSearch returned {len(results)} result(s)")
        return AttributeSearchOutput(results=results)

    @classmethod
    def from_runtime(
        cls,
        rt: SearchRuntime,
        *,
        es: ElasticIndex | None = None,
        embed: CVTextEmbedder | None = None,
        vst: VSTSnapshot | None = None,
    ) -> AttributeSearch:
        from ..clients.elastic import ElasticClient  # local — avoid cycle
        from ..clients.rtvi_cv_embed import RTVICVEmbedClient
        from ..clients.vst import VSTClient

        # Attribute search reads the mdx-behavior-* indices, which may live on
        # a separate Elasticsearch cluster from the video-embedding endpoint.
        # ``from_runtime_behavior`` honors ``rt.behavior_es_endpoint`` and
        # falls back to ``rt.es_endpoint`` when not separately configured.
        return cls(
            es=es or ElasticClient.from_runtime_behavior(rt),
            embed=embed or RTVICVEmbedClient.from_runtime(rt),
            vst=vst or VSTClient.from_runtime(rt),
            behavior_index=rt.behavior_index,
            behavior_index_wildcard=rt.behavior_index_wildcard,
            frames_index=rt.frames_index,
            frames_index_wildcard=rt.frames_index_wildcard,
            enable_frame_lookup=rt.enable_frame_lookup,
            default_max_results=rt.default_max_results,
            vst_external_url=rt.vst_external_url,
            vst_internal_url=rt.vst_internal_url,
        )

    async def aclose(self) -> None:
        await asyncio.gather(
            self._es.aclose(),
            self._embed.aclose(),
            return_exceptions=True,
        )
