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
"""Transitional executors: drive the pipeline through the async primitives.

The pipeline's IO seam is a set of synchronous executors
(:class:`~.facade.SearchDeps`). Until the clients are natively synchronous,
this module adapts the existing async primitives to that seam with per-call
event loops. It is the strangler bridge: when the sync clients land, this
module is deleted and the executors are built directly — nothing above the
seam changes.

``deps_from_runtime`` is the one-stop builder the CLI uses:

    rt = SearchRuntime(...)          # CLI-owned endpoint/config resolution
    output = run_search(search_input, deps_from_runtime(rt))
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING
from typing import Any

from lib.vst import get_sensor_id_from_stream_id

from .facade import SearchDeps

if TYPE_CHECKING:
    from collections.abc import Callable
    from collections.abc import Coroutine

    from ..models.attribute_search import AttributeSearchInput
    from ..models.attribute_search import AttributeSearchOutput
    from ..models.attribute_search import AttributeSearchResult
    from ..models.common import SourceType
    from ..models.embed_search import EmbedSearchInput
    from ..models.embed_search import EmbedSearchOutput
    from ..runtime import SearchRuntime


def _run_sync[T](coro: Coroutine[Any, Any, T]) -> T:
    """Run one coroutine to completion on a fresh event loop.

    The pipeline is synchronous end-to-end, so there is never a running loop
    to conflict with; a fresh loop per call keeps the bridge stateless.
    """
    return asyncio.run(coro)


def embed_exec_from_runtime(rt: SearchRuntime) -> Callable[[EmbedSearchInput], EmbedSearchOutput]:
    """Synchronous embed executor over the async ``EmbedSearch`` primitive."""
    from ..primitives.embed_search import EmbedSearch

    def _exec(request: EmbedSearchInput) -> EmbedSearchOutput:
        async def _go() -> EmbedSearchOutput:
            primitive = EmbedSearch.from_runtime(rt)
            try:
                return await primitive.run(request)
            finally:
                await primitive.aclose()

        return _run_sync(_go())

    return _exec


def attribute_exec_from_runtime(rt: SearchRuntime) -> Callable[[AttributeSearchInput], list[AttributeSearchResult]]:
    """Synchronous attribute executor over the async ``AttributeSearch`` primitive."""
    from ..primitives.attribute_search import AttributeSearch

    def _exec(request: AttributeSearchInput) -> list[AttributeSearchResult]:
        async def _go() -> AttributeSearchOutput:
            primitive = AttributeSearch.from_runtime(rt)
            try:
                return await primitive.run(request)
            finally:
                await primitive.aclose()

        output = _run_sync(_go())
        return list(output.results)

    return _exec


def object_exec_from_runtime(
    rt: SearchRuntime,
    *,
    source_type: SourceType,
    top_k: int,
    video_sources: list[str] | None = None,
    timestamp_start: Any = None,
    timestamp_end: Any = None,
) -> Callable[[str], list[AttributeSearchResult]]:
    """Synchronous per-object behavior-kNN executor."""
    from ..clients.elastic import ElasticClient
    from ..primitives._attribute_helpers import resolve_index_by_source_type
    from ..primitives._attribute_helpers import search_by_object_embedding

    index = resolve_index_by_source_type(
        base_index=rt.behavior_index,
        source_type=source_type,
        wildcard_pattern=rt.behavior_index_wildcard,
    )

    def _exec(object_id: str) -> list[AttributeSearchResult]:
        async def _go() -> list[AttributeSearchResult]:
            es = ElasticClient.from_runtime_behavior(rt)
            try:
                return await search_by_object_embedding(
                    object_id=object_id,
                    behavior_index=index,
                    es=es,
                    top_k=top_k,
                    min_similarity=0.0,
                    video_sources=video_sources,
                    timestamp_start=timestamp_start,
                    timestamp_end=timestamp_end,
                    source_type=source_type,
                )
            finally:
                await es.aclose()

        return _run_sync(_go())

    return _exec


def object_enrich_from_runtime(
    rt: SearchRuntime,
) -> Callable[[list[AttributeSearchResult]], list[AttributeSearchResult]]:
    """VST screenshot enrichment for deduplicated object results."""
    from ..primitives._attribute_helpers import enrich_attribute_results

    def _enrich(results: list[AttributeSearchResult]) -> list[AttributeSearchResult]:
        if not rt.vst_internal_url or not rt.vst_external_url:
            return results
        _run_sync(enrich_attribute_results(results, rt.vst_internal_url, rt.vst_external_url))
        return results

    return _enrich


def sensor_resolver_from_runtime(rt: SearchRuntime) -> Callable[[str], str]:
    """Stream-id -> sensor-id resolution via VST (best-effort, sync)."""

    def _resolve(stream_id: str) -> str:
        if not rt.vst_internal_url:
            return stream_id
        return str(_run_sync(get_sensor_id_from_stream_id(stream_id, rt.vst_internal_url)))

    return _resolve


def deps_from_runtime(
    rt: SearchRuntime,
    *,
    source_type: SourceType = "video_file",
    top_k: int = 10,
    video_sources: list[str] | None = None,
    timestamp_start: Any = None,
    timestamp_end: Any = None,
) -> SearchDeps:
    """Build the full synchronous executor set from a resolved runtime."""
    return SearchDeps(
        embed_exec=embed_exec_from_runtime(rt),
        attribute_exec=attribute_exec_from_runtime(rt),
        object_exec=object_exec_from_runtime(
            rt,
            source_type=source_type,
            top_k=top_k,
            video_sources=video_sources,
            timestamp_start=timestamp_start,
            timestamp_end=timestamp_end,
        ),
        object_enrich=object_enrich_from_runtime(rt),
        sensor_resolver=sensor_resolver_from_runtime(rt),
    )
