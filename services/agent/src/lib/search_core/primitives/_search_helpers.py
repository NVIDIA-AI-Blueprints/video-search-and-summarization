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
"""Internal helpers for Search.

Ported byte-for-byte from services/agent/src/vss_agents/tools/search.py (lines
61-1457) with these edits:
  - NAT and registered-function machinery removed (SearchConfig and the
    `search` entry point stay in the NAT shim).
  - Model class definitions removed; helpers use the library's models.
  - Upward imports rewritten to library-local paths.
  - `fastapi.HTTPException` replaced with library `BackendUnreachableError` so
    search_core has no web-framework dep. NAT shim translates back to HTTP.
  - `VSSESClient.get_es_client` calls replaced with injected `ElasticClient`
    instances or `ElasticClient.from_endpoint(...)`.
  - Query decomposition is performed entirely by the NAT layer before this
    library is called. The library consumes prepared SearchInput fields only.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from datetime import timedelta
import json
import logging
from typing import TYPE_CHECKING
from typing import Any
from typing import cast

from .._internal.time_convert import datetime_to_iso8601
from .._internal.time_convert import iso8601_to_datetime
from .._internal.time_measure import TimeMeasure
from ..agent_chunks import AgentMessageChunk
from ..agent_chunks import AgentMessageChunkType
from ..clients.elastic import ElasticClient
from ..clients.vst import get_sensor_id_from_stream_id
from ..errors import BackendUnreachableError
from ..models.attribute_search import AttributeSearchResult
from ..models.embed_search import EmbedSearchOutput
from ..models.search import CriticResult
from ..models.search import SearchInput
from ..models.search import SearchOutput
from ..models.search import SearchResult
from ._attribute_helpers import enrich_attribute_results
from ._attribute_helpers import resolve_index_by_source_type
from ._attribute_helpers import search_by_object_embedding

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

logger = logging.getLogger(__name__)


# ==========================================================================
# Constants
# ==========================================================================

_SIMILARITY_RATIO_THRESHOLD = 0.9


# ==========================================================================
# Attribute helpers (search.py L141-L257)
# ==========================================================================


async def _run_attribute_only_search(
    attribute_list: list[str],
    search_input: SearchInput,
    attribute_search_fn: Any,
    top_k: int,
    min_similarity: float | None,
    exclude_videos: list[dict[str, str]] | None = None,
) -> list[SearchResult]:
    """Run attribute-only search in append mode. Mirrors search.py:141-198."""
    logger.info(f"Running attribute-only search (append mode), input: {search_input.model_dump_json()}")
    exclude_videos = exclude_videos or []
    try:
        attr_params = {
            "query": attribute_list,
            "source_type": search_input.source_type,
            "video_sources": search_input.video_sources,
            "timestamp_start": search_input.timestamp_start,
            "timestamp_end": search_input.timestamp_end,
            "top_k": top_k,
            "min_similarity": min_similarity if min_similarity is not None else 0.3,
            "fuse_multi_attribute": False,
            "exclude_videos": exclude_videos,
        }

        attribute_results = await attribute_search_fn.ainvoke(attr_params)

        search_results: list[SearchResult] = []
        if attribute_results and isinstance(attribute_results, list):
            validated_results = [
                item if isinstance(item, AttributeSearchResult) else AttributeSearchResult.model_validate(item)
                for item in attribute_results
            ]
            for result in validated_results:
                try:
                    search_results.append(attribute_result_to_search_result(result))
                except Exception as e:
                    logger.warning(f"Failed to convert attribute result: {e}")
                    continue
            search_results.sort(key=lambda x: x.similarity, reverse=True)

        return search_results
    except Exception as e:
        logger.error(f"Attribute-only search failed: {e}", exc_info=True)
        return []


def attribute_result_to_search_result(
    attr_result: Any,
    video_name: str | None = None,
    description: str = "",
) -> SearchResult:
    """Convert AttributeSearchResult → SearchResult. Mirrors search.py:201-256."""
    if isinstance(attr_result, dict):
        validated_result = AttributeSearchResult.model_validate(attr_result)
    elif isinstance(attr_result, AttributeSearchResult):
        validated_result = attr_result
    else:
        validated_result = AttributeSearchResult.model_validate(attr_result)

    metadata = validated_result.metadata
    similarity = (
        float(metadata.frame_score)
        if (metadata.frame_score is not None and metadata.frame_score > 0.0)
        else float(metadata.behavior_score)
    )
    # frame_timestamp is nullable; fall back to "" (the "no timestamp" convention
    # used by embed results) so SearchResult's required str fields stay typed and
    # _merge_consecutive_results routes them to its no-timestamp bucket.
    start_time = metadata.start_time or metadata.frame_timestamp or ""
    end_time = metadata.end_time or metadata.frame_timestamp or ""
    result_video_name = video_name or metadata.video_name or metadata.sensor_id
    if not description:
        description = f"Attribute match at {metadata.frame_timestamp or 'unknown time'}"

    return SearchResult(
        video_name=result_video_name,
        description=description,
        start_time=start_time,
        end_time=end_time,
        sensor_id=metadata.sensor_id,
        screenshot_url=validated_result.screenshot_url or "",
        similarity=similarity,
        object_ids=[str(metadata.object_id)],
    )


# ==========================================================================
# Video sources resolution (search.py L374-413)
# ==========================================================================


def _resolve_video_sources_for_search(
    video_sources: list[str],
    name_to_uuid: dict[str, str],
    source_type: str | None,
) -> list[str]:
    """Resolve source names to the IDs expected by each ES source index."""
    if not video_sources or not name_to_uuid:
        return video_sources

    if source_type == "rtsp":
        uuid_to_name = {stream_id: name for name, stream_id in name_to_uuid.items()}
        resolved_sources: list[str] = []
        for video_source in video_sources:
            stream_id = name_to_uuid.get(video_source)
            if stream_id:
                resolved_sources.append(video_source)
            elif video_source in uuid_to_name:
                resolved_sources.append(uuid_to_name[video_source])
            else:
                resolved_sources.append(video_source)
        return resolved_sources

    resolved_sources = []
    for video_source in video_sources:
        stream_id = name_to_uuid.get(video_source)
        if stream_id:
            resolved_sources.append(stream_id)
        else:
            resolved_sources.append(video_source)
    return resolved_sources


# ==========================================================================
# Fusion math (search.py L416-543)
# ==========================================================================


def _apply_weighted_linear_fusion(
    video_data: list[dict[str, Any]],
    w_embed: float,
    w_attribute: float,
) -> list[SearchResult]:
    """Weighted linear fusion. Mirrors L416-452."""
    reranked_results = []
    for video in video_data:
        embed_score = video["embed_score"]
        attribute_score = video["normalised_attribute_score"]
        fusion_score = w_embed * embed_score + w_attribute * attribute_score
        reranked_result = SearchResult(
            video_name=video["embed_result"].video_name,
            description=video["embed_result"].description,
            start_time=video["embed_result"].start_time,
            end_time=video["embed_result"].end_time,
            sensor_id=video["embed_result"].sensor_id,
            screenshot_url=video["screenshot_url"],
            similarity=fusion_score,
            object_ids=video["object_ids"],
        )
        reranked_results.append((fusion_score, reranked_result))
    reranked_results.sort(key=lambda x: x[0], reverse=True)
    return [result for _, result in reranked_results]


def _apply_rrf_fusion(
    video_data: list[dict[str, Any]],
    rrf_k: int,
    rrf_w: float,
) -> list[SearchResult]:
    """Reciprocal Rank Fusion. Mirrors L455-493."""
    sorted_video_data = sorted(video_data, key=lambda x: x["embed_score"], reverse=True)
    reranked_results = []
    for rank, video in enumerate(sorted_video_data, start=1):
        rrf_score = 1.0 / (rank + rrf_k) + rrf_w * video["normalised_attribute_score"]
        reranked_result = SearchResult(
            video_name=video["embed_result"].video_name,
            description=video["embed_result"].description,
            start_time=video["embed_result"].start_time,
            end_time=video["embed_result"].end_time,
            sensor_id=video["embed_result"].sensor_id,
            screenshot_url=video["screenshot_url"],
            similarity=rrf_score,
            object_ids=video["object_ids"],
        )
        reranked_results.append((rrf_score, reranked_result))
    reranked_results.sort(key=lambda x: x[0], reverse=True)
    return [result for _, result in reranked_results]


def _apply_rrf_fusion_with_attribute_rank(
    video_data: list[dict[str, Any]],
    rrf_k: int,
    rrf_w: float,
) -> list[SearchResult]:
    """RRF using both embed and attribute ranks. Mirrors L496-543."""
    sorted_by_embed = sorted(video_data, key=lambda x: x["embed_score"], reverse=True)
    embed_ranks = {id(video): rank for rank, video in enumerate(sorted_by_embed, start=1)}
    sorted_by_attribute = sorted(video_data, key=lambda x: x["normalised_attribute_score"], reverse=True)
    attribute_ranks = {id(video): rank for rank, video in enumerate(sorted_by_attribute, start=1)}

    reranked_results = []
    for video in video_data:
        rank_embed = embed_ranks[id(video)]
        rank_attribute = attribute_ranks[id(video)]
        rrf_score = 1.0 / (rank_embed + rrf_k) + rrf_w * (1.0 / (rank_attribute + rrf_k))
        reranked_result = SearchResult(
            video_name=video["embed_result"].video_name,
            description=video["embed_result"].description,
            start_time=video["embed_result"].start_time,
            end_time=video["embed_result"].end_time,
            sensor_id=video["embed_result"].sensor_id,
            screenshot_url=video["screenshot_url"],
            similarity=rrf_score,
            object_ids=video["object_ids"],
        )
        reranked_results.append((rrf_score, reranked_result))
    reranked_results.sort(key=lambda x: x[0], reverse=True)
    return [result for _, result in reranked_results]


# ==========================================================================
# fusion_search_rerank (search.py L546-713)
# ==========================================================================


async def fusion_search_rerank(
    embed_results: list[SearchResult],
    attributes: list[str],
    attribute_search_fn: Any,
    vst_internal_url: str | None = None,
    source_type: str = "video_file",
    fusion_method: str = "rrf",
    rrf_k: int = 60,
    rrf_w: float = 0.5,
    w_attribute: float = 0.55,
    w_embed: float = 0.35,
) -> list[SearchResult]:
    """Rerank embed_search results using weighted linear or RRF fusion.

    For each video: run attribute_search; compute normalised attribute score;
    apply chosen fusion method.
    """
    logger.info(
        f"{fusion_method.upper()} fusion reranking {len(embed_results)} videos using {len(attributes)} attributes"
    )

    async def _get_attribute_results(embed_result: SearchResult) -> tuple[SearchResult, Any]:
        try:
            start_dt = iso8601_to_datetime(embed_result.start_time)
            end_dt = iso8601_to_datetime(embed_result.end_time)

            if end_dt <= start_dt:
                original_start = start_dt
                start_dt = original_start - timedelta(seconds=2.5)
                end_dt = original_start + timedelta(seconds=2.5)
                logger.info(
                    f"Extended 0-duration clip to ±2.5 seconds: {embed_result.start_time} -> "
                    f"[{datetime_to_iso8601(start_dt)}, {datetime_to_iso8601(end_dt)}]"
                )

            filter_sensor_id = ""

            if embed_result.sensor_id and vst_internal_url:
                try:
                    filter_sensor_id = await get_sensor_id_from_stream_id(embed_result.sensor_id, vst_internal_url)
                    if filter_sensor_id != embed_result.sensor_id:
                        logger.info(f"Converted stream_id '{embed_result.sensor_id}' to sensor_id '{filter_sensor_id}'")
                except Exception as e:
                    logger.warning(f"VST conversion failed: {e}. Using fallback")

            if not filter_sensor_id:
                filter_sensor_id = embed_result.video_name or embed_result.sensor_id or ""

            attr_params = {
                "query": attributes,
                "source_type": source_type,
                "video_sources": [filter_sensor_id] if filter_sensor_id else None,
                "timestamp_start": start_dt,
                "timestamp_end": end_dt,
                "top_k": 1,
                "min_similarity": 0.4,
                "fuse_multi_attribute": True,
            }

            try:
                attribute_results = await attribute_search_fn.ainvoke(attr_params)
            except Exception as e:
                logger.error(f"Attribute search failed for {embed_result.video_name}: {e}")
                attribute_results = None

            return embed_result, attribute_results
        except Exception as e:
            logger.error(f"Failed to process embed result {embed_result.video_name}: {e}")
            return embed_result, None

    results_list = await asyncio.gather(*[_get_attribute_results(er) for er in embed_results])

    video_data: list[dict[str, Any]] = []

    for embed_result, attribute_results in results_list:
        embed_score = embed_result.similarity
        attribute_scores: list[float] = []
        attribute_screenshot_url = None
        object_ids: list[str] = []

        if attribute_results and isinstance(attribute_results, list):
            validated_results = [
                item if isinstance(item, AttributeSearchResult) else AttributeSearchResult.model_validate(item)
                for item in attribute_results
            ]
        else:
            validated_results = []

        if validated_results:
            for result in validated_results:
                frame_score = result.metadata.frame_score
                behavior_score = result.metadata.behavior_score
                score = float(frame_score) if (frame_score is not None and frame_score > 0.0) else float(behavior_score)
                attribute_scores.append(score)
                object_id = result.metadata.object_id
                if object_id and str(object_id) not in object_ids:
                    object_ids.append(str(object_id))
            attribute_screenshot_url = validated_results[0].screenshot_url or ""

        normalised_attribute_score = sum(attribute_scores) / len(attributes) if len(attributes) > 0 else 0.0

        video_data.append(
            {
                "embed_result": embed_result,
                "embed_score": embed_score,
                "normalised_attribute_score": normalised_attribute_score,
                "screenshot_url": (
                    attribute_screenshot_url if attribute_screenshot_url else embed_result.screenshot_url
                ),
                "object_ids": object_ids,
            }
        )

    if fusion_method == "weighted_linear":
        final_results = _apply_weighted_linear_fusion(video_data, w_embed, w_attribute)
    elif fusion_method == "rrf":
        final_results = _apply_rrf_fusion(video_data, rrf_k, rrf_w)
    elif fusion_method == "rrf_with_attribute_rank":
        final_results = _apply_rrf_fusion_with_attribute_rank(video_data, rrf_k, rrf_w)
    else:
        raise ValueError(
            f"Unknown fusion_method: {fusion_method}. Must be 'weighted_linear', 'rrf', or 'rrf_with_attribute_rank'"
        )

    logger.info(f"{fusion_method.upper()} fusion reranking complete: {len(final_results)} videos reranked")
    return final_results


# ==========================================================================
# _merge_consecutive_results (search.py L719-816)
# ==========================================================================


def _merge_consecutive_results(results: list[SearchResult]) -> list[SearchResult]:
    """Merge consecutive/overlapping chunks from the same sensor. Mirrors L719-816."""
    if not results:
        return results

    timestamped: list[SearchResult] = []
    no_timestamp: list[SearchResult] = []
    for r in results:
        if not r.start_time or not r.end_time:
            no_timestamp.append(r)
            continue
        try:
            iso8601_to_datetime(r.start_time)
            iso8601_to_datetime(r.end_time)
            timestamped.append(r)
        except (ValueError, TypeError) as e:
            logger.warning(f"Skipping merge for result with malformed timestamp (sensor={r.sensor_id}): {e}")
            no_timestamp.append(r)

    merged: list[SearchResult] = list(no_timestamp)
    if not timestamped:
        merged.sort(key=lambda r: r.similarity, reverse=True)
        return merged

    by_sensor: dict[str, list[SearchResult]] = {}
    for result in timestamped:
        by_sensor.setdefault(result.sensor_id, []).append(result)

    for sensor_id, sensor_results in by_sensor.items():
        # Sort by parsed datetime, not the raw ISO string: mixed encodings
        # ('+00:00' vs 'Z', differing fractional-second widths) sort wrongly
        # lexically and would group the wrong consecutive chunks. These results
        # are all in `timestamped`, so start_time is guaranteed parseable.
        sorted_results = sorted(sensor_results, key=lambda r: iso8601_to_datetime(r.start_time))

        groups: list[list[SearchResult]] = []
        group_chunks: list[SearchResult] = [sorted_results[0]]
        group_end_dt = iso8601_to_datetime(sorted_results[0].end_time)

        for result in sorted_results[1:]:
            result_start_dt = iso8601_to_datetime(result.start_time)
            group_avg_sim = sum(c.similarity for c in group_chunks) / len(group_chunks)
            pair_max = max(group_avg_sim, result.similarity)
            pair_min = min(group_avg_sim, result.similarity)
            sim_compatible = pair_max == 0 or (pair_min / pair_max) >= _SIMILARITY_RATIO_THRESHOLD

            if result_start_dt <= group_end_dt and sim_compatible:
                result_end_dt = iso8601_to_datetime(result.end_time)
                if result_end_dt > group_end_dt:
                    group_end_dt = result_end_dt
                group_chunks.append(result)
            else:
                groups.append(group_chunks)
                group_chunks = [result]
                group_end_dt = iso8601_to_datetime(result.end_time)
        groups.append(group_chunks)

        for group in groups:
            first = group[0]
            end_dt = max(iso8601_to_datetime(g.end_time) for g in group)
            similarity = sum(g.similarity for g in group) / len(group)

            seen_ids: set[str] = set()
            merged_object_ids: list[str] = []
            for g in group:
                for oid in g.object_ids:
                    if oid not in seen_ids:
                        merged_object_ids.append(oid)
                        seen_ids.add(oid)

            merged.append(
                SearchResult(
                    video_name=first.video_name,
                    description=first.description,
                    start_time=first.start_time,
                    end_time=datetime_to_iso8601(end_dt),
                    sensor_id=sensor_id,
                    screenshot_url=first.screenshot_url,
                    similarity=similarity,
                    object_ids=merged_object_ids,
                    critic_result=None,
                )
            )

    merged.sort(key=lambda r: r.similarity, reverse=True)
    return merged


# ==========================================================================
# execute_core_search (search.py L824-1426)
# ==========================================================================


async def execute_core_search(
    search_input: SearchInput,
    embed_search: Any,
    config: Any,
    attribute_search_fn: Any | None = None,
    critic_agent: Any | None = None,
    behavior_es: Any | None = None,
) -> AsyncGenerator[AgentMessageChunk | SearchOutput]:
    """Core search execution: yields progress chunks, then final SearchOutput.

    Three execution paths:
      1. Attribute-only (has_action=False and attributes exist)
      2. Embed-only (no attributes)
      3. Fusion (has_action=True and attributes exist, embed score >= threshold)

    All function references (``attribute_search_fn``, ``critic_agent``) must be
    pre-loaded by the caller — the library does not depend on the NAT Builder.
    The library's Search primitive does this in its from_runtime wiring; the
    NAT shims (tools/search.py, agents/search_agent.py) do it at registration.
    """
    from ..models.common import VideoInfo
    from ..models.critic import CriticAgentResult
    from ..models.critic import VideoResult as CriticVideoResult

    original_query = search_input.original_query or search_input.query

    # ----- OBJECT_ID PATH: Direct behavior KNN -----
    if search_input.object_ids:
        if not getattr(config, "behavior_es_endpoint", None):
            raise ValueError("behavior_es_endpoint config is required for object_id re-search")

        top_k = search_input.top_k if search_input.top_k is not None else config.default_max_results

        yield AgentMessageChunk(
            type=AgentMessageChunkType.TOOL_CALL,
            content=f"Searching for similar objects to: {search_input.object_ids}",
        )

        es = behavior_es if behavior_es is not None else ElasticClient.from_endpoint(config.behavior_es_endpoint)

        behavior_index_wildcard = getattr(config, "behavior_index_wildcard", "mdx-behavior-*")
        object_search_index = resolve_index_by_source_type(
            base_index=config.behavior_index,
            source_type=search_input.source_type,
            wildcard_pattern=behavior_index_wildcard,
        )

        async def _safe_object_search(oid: int) -> list[AttributeSearchResult]:
            try:
                return await search_by_object_embedding(
                    object_id=str(oid),
                    behavior_index=object_search_index,
                    es=es,
                    top_k=top_k,
                    min_similarity=0.0,
                    video_sources=search_input.video_sources if search_input.video_sources else None,
                    timestamp_start=search_input.timestamp_start,
                    timestamp_end=search_input.timestamp_end,
                    source_type=search_input.source_type,
                )
            except BackendUnreachableError:
                raise
            except Exception as e:
                logger.warning(f"Object ID {oid} search failed: {e}")
                return []

        with TimeMeasure("search: object_ids behavior KNN"):
            results_list = await asyncio.gather(*[_safe_object_search(oid) for oid in search_input.object_ids])

        all_results: list[AttributeSearchResult] = []
        for obj_results in results_list:
            all_results.extend(obj_results)

        seen: dict = {}
        for r in all_results:
            key = str(r.metadata.object_id)
            if key not in seen or r.metadata.behavior_score > seen[key].metadata.behavior_score:
                seen[key] = r
        attr_results = sorted(seen.values(), key=lambda r: r.metadata.behavior_score, reverse=True)[:top_k]

        vst_internal_url = getattr(config, "vst_internal_url", None)
        vst_external_url = getattr(config, "vst_external_url", None)
        await enrich_attribute_results(attr_results, vst_internal_url, vst_external_url)

        search_results = [attribute_result_to_search_result(r) for r in attr_results]
        result_count = len(search_results)
        yield AgentMessageChunk(
            type=AgentMessageChunkType.THOUGHT,
            content=f"Found {result_count} similar object{'s' if result_count != 1 else ''}",
        )
        yield SearchOutput(data=search_results, search_messages=[])
        return

    # ----- SETUP COMMON QUERY PARAMETERS -----
    top_k = search_input.top_k if search_input.top_k is not None else config.default_max_results
    original_top_k = top_k
    top_k = top_k * 2

    query_params: dict[str, str] = {"query": search_input.query}

    if search_input.video_sources and len(search_input.video_sources) > 0:
        query_params["video_sources"] = json.dumps(search_input.video_sources)
    if search_input.description:
        query_params["description"] = search_input.description
    if search_input.timestamp_start:
        query_params["timestamp_start"] = search_input.timestamp_start.isoformat()
    if search_input.timestamp_end:
        query_params["timestamp_end"] = search_input.timestamp_end.isoformat()
    if not search_input.agent_mode:
        query_params["min_cosine_similarity"] = str(search_input.min_cosine_similarity)

    attribute_list: list[str] = []
    is_attribute_only = False
    if search_input.attributes:
        attribute_list = search_input.attributes

        def _is_single_word(attr: str) -> bool:
            attr = attr.strip()
            return " " not in attr and "-" not in attr and "." not in attr

        original_count = len(attribute_list)
        attribute_list = [attr for attr in attribute_list if not _is_single_word(attr)]
        if len(attribute_list) < original_count:
            pruned_count = original_count - len(attribute_list)
            logger.info(f"Pruned {pruned_count} single-word attribute(s). Remaining: {attribute_list}")

        if search_input.has_action is not None:
            is_attribute_only = not search_input.has_action
        elif attribute_list:
            is_attribute_only = True

    # ----- EXECUTION FLOW: Three paths -----
    # The object-id path above already assigned ``search_results`` and
    # returned before this point, so the reassignment here is dead code on
    # that branch — but mypy can't see it. Reusing the same name without a
    # fresh annotation lets the no-redef check pass.
    search_results = []
    do_search = True
    rejected_results: set = set()
    confirmed_results: set = set()
    iteration_num = 0
    search_messages: list[str] = []
    persistent_critic_results: dict = {}

    while do_search and iteration_num < config.search_max_iterations:
        iteration_num += 1
        do_search = False
        logger.info(f"[Search] Running embed search iteration {iteration_num}")

        query_params["top_k"] = str(top_k)
        query_input_json = json.dumps({"params": query_params, "source_type": search_input.source_type})

        # PATH 1: Attribute-only
        if is_attribute_only and attribute_list and getattr(config, "attribute_search_tool", None):
            logger.info("EXECUTION PATH: Attribute-only search (no embed, append mode)")
            yield AgentMessageChunk(
                type=AgentMessageChunkType.TOOL_CALL,
                content=f"Running attribute-only search with {len(attribute_list)} attributes",
            )

            if attribute_search_fn is None:
                raise ValueError(
                    "attribute_search_fn must be pre-loaded by the Search primitive; library does not use NAT Builder"
                )

            with TimeMeasure("search: attribute-only search"):
                search_results = await _run_attribute_only_search(
                    attribute_list=attribute_list,
                    search_input=search_input,
                    attribute_search_fn=attribute_search_fn,
                    top_k=original_top_k,
                    min_similarity=0.0,
                )

            yield AgentMessageChunk(
                type=AgentMessageChunkType.THOUGHT,
                content=f"Found {len(search_results)} results from attribute-only search",
            )

        # PATH 2 & 3: Embed search first
        else:
            logger.info("EXECUTION PATH: Embed search")
            yield AgentMessageChunk(
                type=AgentMessageChunkType.TOOL_CALL,
                content=f"Running embed search with query: '{search_input.query}'",
            )

            try:
                with TimeMeasure("search: embed search"):
                    embed_search_output = await embed_search.ainvoke(query_input_json)
            except ValueError as e:
                error_msg = str(e)
                logger.error(f"Embed search failed: {error_msg}")
                yield AgentMessageChunk(
                    type=AgentMessageChunkType.ERROR,
                    content=f"Embed search failed: {error_msg}",
                )
                # Library version: raise a library exception. NAT shim translates
                # to HTTPException(404) for /api/v1/search wire compatibility.
                raise BackendUnreachableError("embed_search", error_msg, e) from e
            except Exception as e:
                error_msg = str(e)
                logger.error(f"Unexpected error in embed search: {error_msg}", exc_info=True)
                yield AgentMessageChunk(
                    type=AgentMessageChunkType.ERROR,
                    content=f"Embed search failed: {error_msg}",
                )
                raise BackendUnreachableError("embed_search", error_msg, e) from e

            if isinstance(embed_search_output, str):
                embed_output = EmbedSearchOutput.model_validate_json(embed_search_output)
            elif isinstance(embed_search_output, EmbedSearchOutput):
                embed_output = embed_search_output
            else:
                embed_output = EmbedSearchOutput.model_validate(embed_search_output)

            search_results = []
            for item in embed_output.results:
                if not item.video_name:
                    logger.warning("Skipping result with empty video_name")
                    continue
                search_results.append(
                    SearchResult(
                        video_name=item.video_name,
                        description=item.description,
                        start_time=item.start_time,
                        end_time=item.end_time,
                        sensor_id=item.sensor_id,
                        screenshot_url=item.screenshot_url,
                        similarity=item.similarity_score,
                    )
                )

            yield AgentMessageChunk(
                type=AgentMessageChunkType.THOUGHT,
                content=f"Found {len(search_results)} results from embed search",
            )

            # Embed confidence fallback / fusion
            if search_results and attribute_list and getattr(config, "attribute_search_tool", None):
                max_embed_score = max((r.similarity for r in search_results), default=0.0)
                if max_embed_score < config.embed_confidence_threshold:
                    logger.info(
                        f"Embed confidence low (max={max_embed_score:.3f} < "
                        f"threshold={config.embed_confidence_threshold:.3f}). Falling back to attribute-only."
                    )
                    yield AgentMessageChunk(
                        type=AgentMessageChunkType.THOUGHT,
                        content=(
                            f"Embed confidence low ({max_embed_score:.3f}), falling back to attribute-only search"
                        ),
                    )

                    if attribute_search_fn is None:
                        raise ValueError("attribute_search_fn must be pre-loaded by the Search primitive")

                    with TimeMeasure("search: attribute-only fallback"):
                        search_results = await _run_attribute_only_search(
                            attribute_list=attribute_list,
                            search_input=search_input,
                            attribute_search_fn=attribute_search_fn,
                            top_k=top_k,
                            min_similarity=0.0,
                        )

                    yield AgentMessageChunk(
                        type=AgentMessageChunkType.THOUGHT,
                        content=f"Found {len(search_results)} results from attribute-only search",
                    )
                elif (
                    config.use_attribute_search
                    and len(search_results) > 0
                    and max_embed_score >= config.embed_confidence_threshold
                ):
                    try:
                        logger.info("EXECUTION PATH: Fusion Search")
                        yield AgentMessageChunk(
                            type=AgentMessageChunkType.TOOL_CALL,
                            content=f"Running fusion reranking with attributes: {attribute_list}",
                        )

                        if attribute_search_fn is None:
                            raise ValueError("attribute_search_fn must be pre-loaded by the Search primitive")

                        with TimeMeasure("search: fusion search rerank"):
                            reranked_results = await fusion_search_rerank(
                                embed_results=search_results,
                                attributes=attribute_list,
                                attribute_search_fn=attribute_search_fn,
                                vst_internal_url=config.vst_internal_url,
                                source_type=search_input.source_type,
                                fusion_method=config.fusion_method,
                                rrf_k=config.rrf_k,
                                rrf_w=config.rrf_w,
                                w_attribute=config.w_attribute,
                                w_embed=config.w_embed,
                            )

                        search_results = reranked_results
                        yield AgentMessageChunk(
                            type=AgentMessageChunkType.THOUGHT,
                            content="Fusion reranking complete",
                        )
                    except Exception as e:
                        logger.error(f"Error in fusion_search reranking: {e}", exc_info=True)
                        yield AgentMessageChunk(
                            type=AgentMessageChunkType.ERROR,
                            content=f"Fusion reranking failed, using embed results: {e!s}",
                        )

        # Percentage-based filtering
        top_pct = getattr(config, "top_percent_filter", None)
        if top_pct and 0 < top_pct < 1.0 and search_results:
            max_sim = max(r.similarity for r in search_results)
            sim_threshold = max_sim * top_pct
            before_count = len(search_results)
            search_results = [r for r in search_results if r.similarity >= sim_threshold]
            logger.info(
                f"Top-percent filter: kept {len(search_results)}/{before_count} results (>= {sim_threshold:.4f})"
            )

        search_results = _merge_consecutive_results(search_results)

        # Critic verification
        if config.enable_critic and search_input.use_critic and critic_agent and search_results:
            try:
                critic_results: dict[VideoInfo, CriticVideoResult] = {}

                yield AgentMessageChunk(
                    type=AgentMessageChunkType.THOUGHT,
                    content=f"Verifying {len(search_results)} results with critic agent",
                )

                search_videos: list[VideoInfo] = []
                for result in search_results:
                    # SearchResult timestamps are wire-shape ISO 8601 strings;
                    # Pydantic v2 coerces them to datetime on VideoInfo construction.
                    info = VideoInfo(
                        sensor_id=result.sensor_id,
                        start_timestamp=cast("datetime", result.start_time),
                        end_timestamp=cast("datetime", result.end_time),
                    )
                    if info not in confirmed_results and info not in rejected_results:
                        search_videos.append(info)

                if len(search_videos) > 0:
                    critic_input = {"query": original_query, "videos": search_videos}
                    with TimeMeasure("search: critic agent verification"):
                        critic_output = await critic_agent.ainvoke(critic_input)
                    critic_results = {r.video_info: r for r in critic_output.video_results}

                    for info, video_result in critic_results.items():
                        match video_result.result:
                            case CriticAgentResult.CONFIRMED:
                                confirmed_results.add(info)
                            case CriticAgentResult.REJECTED:
                                rejected_results.add(info)
                                top_k += 1
                                do_search = True
                            case CriticAgentResult.UNVERIFIED:
                                logger.warning(f"[Search] Unverified for {info.sensor_id}")

                    if critic_results and all(
                        r.result == CriticAgentResult.UNVERIFIED for r in critic_results.values()
                    ):
                        msg = "VLM verification unavailable. Returning search results without critic verification."
                        search_messages.append(msg)
                        yield AgentMessageChunk(type=AgentMessageChunkType.THOUGHT, content=msg)
                        do_search = False

                    for info, vr in critic_results.items():
                        persistent_critic_results[info] = CriticResult(
                            result=vr.result.value,
                            criteria_met=vr.criteria_met or {},
                        )

                for result in search_results:
                    # SearchResult timestamps are wire-shape ISO 8601 strings;
                    # Pydantic v2 coerces them to datetime on VideoInfo construction.
                    info = VideoInfo(
                        sensor_id=result.sensor_id,
                        start_timestamp=cast("datetime", result.start_time),
                        end_timestamp=cast("datetime", result.end_time),
                    )
                    if info in persistent_critic_results:
                        result.critic_result = persistent_critic_results[info]

                verified_count = sum(1 for vr in critic_results.values() if vr.result == CriticAgentResult.CONFIRMED)
                unverified_count = sum(1 for vr in critic_results.values() if vr.result == CriticAgentResult.UNVERIFIED)

                yield AgentMessageChunk(
                    type=AgentMessageChunkType.THOUGHT,
                    content=(
                        f"Critic verification complete: {verified_count}/{len(critic_results)} verified, "
                        f"{unverified_count}/{len(critic_results)} unverified"
                    ),
                )

            except Exception as e:
                logger.error(f"[Search] Error calling critic agent: {e}", exc_info=True)
                msg = "Critic verification unavailable. Returning results without verification."
                search_messages.append(msg)
                yield AgentMessageChunk(type=AgentMessageChunkType.THOUGHT, content=msg)

    result_count = len(search_results)
    yield AgentMessageChunk(
        type=AgentMessageChunkType.THOUGHT,
        content=f"Found {result_count} result{'s' if result_count != 1 else ''}",
    )

    if original_top_k is not None:
        search_results = search_results[:original_top_k]

    yield SearchOutput(data=search_results, search_messages=search_messages)


async def execute_core_search_wrapper(
    search_input: SearchInput,
    embed_search: Any,
    config: Any,
    attribute_search_fn: Any | None = None,
    critic_agent: Any | None = None,
    behavior_es: Any | None = None,
) -> SearchOutput:
    """Non-streaming wrapper: collects chunks, returns final SearchOutput."""
    async for update in execute_core_search(
        search_input=search_input,
        embed_search=embed_search,
        config=config,
        attribute_search_fn=attribute_search_fn,
        critic_agent=critic_agent,
        behavior_es=behavior_es,
    ):
        if isinstance(update, SearchOutput):
            return update
    return SearchOutput(data=[])
