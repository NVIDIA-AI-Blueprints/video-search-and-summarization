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

Thin orchestrator over an injected behavior Elasticsearch surface and RTVI CV
text embedder. Screenshot/clip enrichment uses the VST URLs (passed as strings);
all query/mapping logic lives in ``_attribute_helpers.py``.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from ..models.attribute_search import AttributeSearchInput
from ..models.attribute_search import AttributeSearchOutput
from . import _attribute_helpers

if TYPE_CHECKING:
    from ..clients.protocols import CVTextEmbedder
    from ..clients.protocols import ElasticIndex
    from ..runtime import SearchRuntime

logger = logging.getLogger(__name__)


class AttributeSearch:
    """Object-attribute search using RTVI CV text embeddings + behavior index KNN."""

    def __init__(
        self,
        *,
        es: ElasticIndex,
        embed: CVTextEmbedder,
        behavior_index: str,
        behavior_index_wildcard: str,
        frames_index: str | None,
        frames_index_wildcard: str = "mdx-raw-*",
        enable_frame_lookup: bool = True,
        default_max_results: int = 10,
        vst_external_url: str = "",
        vst_internal_url: str | None = None,
        owns_es: bool = False,
        owns_embed: bool = False,
    ) -> None:
        self._es = es
        self._embed = embed
        self._behavior_index = behavior_index
        self._behavior_index_wildcard = behavior_index_wildcard
        self._frames_index = frames_index
        self._frames_index_wildcard = frames_index_wildcard
        self._enable_frame_lookup = enable_frame_lookup
        self._default_k = default_max_results
        self._vst_external_url = vst_external_url
        self._vst_internal_url = vst_internal_url
        self._owns_es = owns_es
        self._owns_embed = owns_embed

    async def run(self, inp: AttributeSearchInput) -> AttributeSearchOutput:
        """Execute attribute search and return the ranked results."""
        inp.validate_semantics()
        if inp.top_k is None:
            inp = inp.model_copy(update={"top_k": self._default_k})
        assert inp.top_k is not None
        results = await _attribute_helpers.search_attributes(
            search_input=inp,
            embed_client=self._embed,
            index=self._behavior_index,
            vst_external_url=self._vst_external_url,
            es=self._es,
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
    ) -> AttributeSearch:
        from ..clients.elastic import ElasticClient  # local — avoid cycle
        from ..clients.rtvi_cv_embed import RTVICVEmbedClient

        # Attribute search reads the mdx-behavior-* indices, which may live on
        # a separate Elasticsearch cluster from the video-embedding endpoint.
        # ``from_runtime_behavior`` honors ``rt.behavior_es_endpoint`` and
        # falls back to ``rt.es_endpoint`` when not separately configured.
        owns_es = es is None
        owns_embed = embed is None
        return cls(
            es=es if es is not None else ElasticClient.from_runtime_behavior(rt),
            embed=embed if embed is not None else RTVICVEmbedClient.from_runtime(rt),
            behavior_index=rt.behavior_index,
            behavior_index_wildcard=rt.behavior_index_wildcard,
            frames_index=rt.frames_index,
            frames_index_wildcard=rt.frames_index_wildcard,
            enable_frame_lookup=rt.enable_frame_lookup,
            default_max_results=rt.default_max_results,
            vst_external_url=rt.require("vst_external_url"),
            vst_internal_url=rt.require("vst_internal_url"),
            owns_es=owns_es,
            owns_embed=owns_embed,
        )

    async def aclose(self) -> None:
        coros = []
        if self._owns_es:
            coros.append(self._es.aclose())
        if self._owns_embed:
            coros.append(self._embed.aclose())
        if coros:
            await asyncio.gather(*coros, return_exceptions=True)
