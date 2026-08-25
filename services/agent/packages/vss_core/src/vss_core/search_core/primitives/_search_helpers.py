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
"""Search orchestration: thin async IO over pure helpers.

Two layers cooperate here:
  - pure, synchronous helpers (attribute->result mapping, video-source
    resolution) that unit-test with plain data; the fusion math, chunk merging,
    top-percent filtering, and embed->result coercion live in :mod:`._fusion`; and
  - :func:`execute_core_search`, a thin async generator that wires the injected
    embed/attribute/behavior-ES adapters to those pure helpers and yields
    progress chunks then a final :class:`SearchOutput`.

Error policy is hybrid: systemic failures (any :class:`LibraryError` —
``IndexNotFoundError`` / ``BackendUnreachableError`` / ``InvalidInputError``)
propagate so callers get precise errors and exit codes; only genuinely
best-effort work degrades softly (a single video's attribute lookup during fusion).
"""

from __future__ import annotations

import asyncio
from contextlib import aclosing
from datetime import timedelta
import json
import logging
from typing import TYPE_CHECKING
from typing import Any
from typing import Protocol

from vss_core._foundation.errors import LibraryError
from vss_core._foundation.sanitize import scrub_log
from vss_core._foundation.time import datetime_to_iso8601
from vss_core._foundation.time import safe_iso8601_to_datetime
from vss_core.vios import get_sensor_id_from_stream_id

from .._internal.coerce import _coerce_float
from .._internal.coerce import _coerce_str
from .._internal.time_measure import TimeMeasure
from ..agent_chunks import AgentMessageChunk
from ..agent_chunks import AgentMessageChunkType
from ..clients.elastic import ElasticClient
from ..errors import BackendUnreachableError
from ..errors import ConfigurationError
from ..errors import NoFinalResultError
from ..models.attribute_search import AttributeSearchResult
from ..models.embed_search import EmbedSearchOutput
from ..models.search import SearchInput
from ..models.search import SearchOutput
from ..models.search import SearchResult
from ..models.tag_search import TagSearchOutput
from . import _fusion
from ._attribute_helpers import enrich_attribute_results
from ._attribute_helpers import resolve_index_by_source_type
from ._attribute_helpers import search_by_object_embedding

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from ..clients.protocols import ElasticIndex

logger = logging.getLogger(__name__)

# Downstream input models (``EmbedSearchInput`` / ``AttributeSearchInput``) cap
# ``top_k`` at this value (``Field(le=1000)``). Internal fetch growth is
# clamped to it so a large user ``top_k`` never trips a Pydantic
# ``ValidationError`` deep in a primitive.
_DOWNSTREAM_MAX_TOP_K = 1000


class SupportsAinvoke(Protocol):
    """The single-method async adapter surface the orchestrator invokes.

    ``embed_search`` / ``attribute_search_fn`` are wrapped
    by ``search.py``'s ``_PrimitiveAdapter`` (or a test double) exposing exactly
    this method, so the orchestrator can stay ignorant of the concrete primitive.
    """

    async def ainvoke(self, payload: Any) -> Any: ...


class SearchConfig(Protocol):
    """The config surface :func:`execute_core_search` reads by attribute.

    ``search.py`` builds this as a ``SimpleNamespace``; the Protocol documents
    (and type-checks) exactly which fields the orchestrator consumes. Fields that
    the orchestrator reads defensively via ``getattr`` (``behavior_es_endpoint``,
    ``behavior_index_wildcard``, ``attribute_search_tool``, ``top_percent_filter``)
    are intentionally omitted so an incomplete config still satisfies the type.
    """

    default_max_results: int
    embed_confidence_threshold: float
    merge_adjacent: bool = True
    fusion_method: str
    w_attribute: float
    w_embed: float
    w_tag: float
    rrf_k: int
    rrf_w: float
    vst_internal_url: str
    vst_external_url: str
    behavior_index: str


# ==========================================================================
# Attribute helpers
# ==========================================================================


async def _run_attribute_only_search(
    attribute_list: list[str],
    search_input: SearchInput,
    attribute_search_fn: SupportsAinvoke,
    top_k: int,
    min_similarity: float | None,
    exclude_videos: list[dict[str, str]] | None = None,
    search_messages: list[str] | None = None,
) -> list[SearchResult]:
    """Run attribute-only search in append mode.

    Systemic failures (missing index, backend unreachable, invalid input) are
    re-raised so callers get precise errors; any other unexpected error degrades
    to an empty result. When it degrades, a note is appended to ``search_messages``
    (when provided) so an empty result is distinguishable from a genuine
    no-matches outcome.
    """
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
    except LibraryError:
        # Surface real failures (missing index, backend unreachable, invalid
        # input) on the primary attribute-only path so callers get precise
        # errors/exit codes instead of a misleading empty result.
        raise
    except Exception as e:
        logger.error(f"Attribute-only search failed: {e}", exc_info=True)
        if search_messages is not None:
            search_messages.append("Attribute search degraded; returning partial/empty results.")
        return []


def attribute_result_to_search_result(
    attr_result: Any,
    video_name: str | None = None,
    description: str = "",
) -> SearchResult:
    """Convert an ``AttributeSearchResult`` (or raw payload) to a ``SearchResult``.

    Every field read from the (untrusted) attribute payload is coerced so a null
    or odd-typed value degrades gracefully instead of raising. ``object_id == 0``
    is preserved rather than collapsed to an empty id.
    """
    validated_result = (
        attr_result
        if isinstance(attr_result, AttributeSearchResult)
        else AttributeSearchResult.model_validate(attr_result)
    )

    metadata = validated_result.metadata
    frame_score = metadata.frame_score
    if frame_score is not None and _coerce_float(frame_score) > 0.0:
        similarity = _coerce_float(frame_score)
    else:
        similarity = _coerce_float(metadata.behavior_score)
    # frame_timestamp is nullable; fall back to "" (the "no timestamp" convention
    # used by embed results) so SearchResult's required str fields stay typed and
    # merge_consecutive_results routes them to its no-timestamp bucket.
    start_time = _coerce_str(metadata.start_time) or _coerce_str(metadata.frame_timestamp)
    end_time = _coerce_str(metadata.end_time) or _coerce_str(metadata.frame_timestamp)
    result_video_name = _coerce_str(video_name) or _coerce_str(metadata.video_name) or _coerce_str(metadata.sensor_id)
    if not description:
        description = f"Attribute match at {metadata.frame_timestamp or 'unknown time'}"
    object_id = _coerce_str(metadata.object_id)

    return SearchResult(
        video_name=result_video_name,
        description=description,
        start_time=start_time,
        end_time=end_time,
        sensor_id=_coerce_str(metadata.sensor_id),
        screenshot_url=_coerce_str(validated_result.screenshot_url),
        similarity=similarity,
        # A missing object id is meaningful: attribute-only hits need not be
        # associated with a tracked object. Keep the list empty instead of
        # serializing a misleading blank identifier. ``"0"`` remains truthy
        # and is therefore preserved.
        object_ids=[object_id] if object_id else [],
    )


# ==========================================================================
# Video sources resolution
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
# fusion_search_rerank
# ==========================================================================


async def fusion_search_rerank(
    embed_results: list[SearchResult],
    attributes: list[str],
    attribute_search_fn: SupportsAinvoke,
    vst_internal_url: str | None = None,
    source_type: str = "video_file",
    fusion_method: str = "rrf",
    rrf_k: int = 60,
    rrf_w: float = 0.5,
    w_attribute: float = 0.55,
    w_embed: float = 0.35,
) -> list[SearchResult]:
    """Rerank embed results by fusing each video's embed score with attribute matches.

    Per-video attribute lookups are best-effort: an unexpected failure (or an
    unparseable clip timestamp) degrades that single video to its embed-only
    score. Systemic failures (any :class:`LibraryError`) propagate and abort the
    whole rerank, so callers get a precise error instead of silently-degraded
    results. The assembled candidates are handed to :mod:`._fusion` for the
    chosen fusion method (an unknown method raises :class:`InvalidInputError`).
    """
    logger.info(
        f"{fusion_method.upper()} fusion reranking {len(embed_results)} videos using {len(attributes)} attributes"
    )

    async def _get_attribute_results(embed_result: SearchResult) -> tuple[SearchResult, Any]:
        try:
            start_dt = safe_iso8601_to_datetime(embed_result.start_time)
            end_dt = safe_iso8601_to_datetime(embed_result.end_time)
            if start_dt is None or end_dt is None:
                logger.warning(
                    f"Skipping fusion attribute lookup for {scrub_log(embed_result.video_name)}: "
                    "unparseable start/end timestamp"
                )
                return embed_result, None

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
                # Stream-id -> sensor-id resolution is best-effort enrichment with
                # a defined fallback (video_name / sensor_id), so it never aborts.
                try:
                    filter_sensor_id = await get_sensor_id_from_stream_id(embed_result.sensor_id, vst_internal_url)
                    if filter_sensor_id != embed_result.sensor_id:
                        logger.info(f"Converted stream_id '{embed_result.sensor_id}' to sensor_id '{filter_sensor_id}'")
                except Exception as e:
                    logger.warning(f"VST conversion failed: {scrub_log(str(e))}. Using fallback")

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
            attribute_results = await attribute_search_fn.ainvoke(attr_params)
            return embed_result, attribute_results
        except LibraryError:
            # Systemic failure (missing index, backend unreachable, invalid input)
            # affects every video equally — propagate rather than degrade.
            raise
        except Exception as e:
            logger.warning(
                f"Fusion attribute lookup failed for {scrub_log(embed_result.video_name)}: {scrub_log(str(e))}",
                exc_info=True,
            )
            return embed_result, None

    # No return_exceptions: a systemic LibraryError from any video propagates.
    results_list = await asyncio.gather(*[_get_attribute_results(er) for er in embed_results])

    candidates = _fusion.build_fusion_candidates(list(results_list), len(attributes))
    final_results = _fusion.apply_fusion(
        candidates,
        fusion_method,
        rrf_k=rrf_k,
        rrf_w=rrf_w,
        w_embed=w_embed,
        w_attribute=w_attribute,
    )
    logger.info(f"{fusion_method.upper()} fusion reranking complete: {len(final_results)} videos reranked")
    return final_results


# ==========================================================================
# execute_core_search
# ==========================================================================


async def execute_core_search(
    search_input: SearchInput,
    embed_search: SupportsAinvoke,
    config: SearchConfig,
    attribute_search_fn: SupportsAinvoke | None = None,
    tag_search: SupportsAinvoke | None = None,
    behavior_es: ElasticIndex | None = None,
) -> AsyncGenerator[AgentMessageChunk | SearchOutput]:
    """Core search execution: yields progress chunks, then a final SearchOutput.

    Routes to one of five paths:
      1. object_id re-search (direct behavior kNN by an existing object's vector)
      2. explicit attribute-only mode
      3. explicit embed-only mode
      4. explicit tag-only mode
      5. explicit fusion mode

    The injected adapters (``embed_search``, ``attribute_search_fn``) each expose
    an async ``.ainvoke``; ``behavior_es`` is an
    Elasticsearch surface used only by the object_id path. All are supplied by
    the caller; this generator only wires them to the pure helpers.
    """
    # ----- OBJECT_ID PATH: Direct behavior KNN -----
    if search_input.search_mode == "object":
        assert search_input.object_ids is not None
        behavior_es_endpoint = getattr(config, "behavior_es_endpoint", None)
        if not behavior_es_endpoint:
            raise ConfigurationError("behavior_es_endpoint config is required for object_id re-search")

        top_k = search_input.top_k if search_input.top_k is not None else config.default_max_results

        yield AgentMessageChunk(
            type=AgentMessageChunkType.TOOL_CALL,
            content=f"Searching for similar objects to: {search_input.object_ids}",
        )

        es = behavior_es if behavior_es is not None else ElasticClient.from_endpoint(behavior_es_endpoint)

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
            except LibraryError:
                # Any systemic library error (missing index, backend unreachable,
                # invalid input) affects every object equally — propagate it so the
                # caller gets a precise error/exit code, matching the hybrid policy
                # used on the attribute-only and fusion paths. A benign "object not
                # found" surfaces as a plain ValueError below and still degrades to
                # an empty result.
                raise
            except Exception as e:
                logger.warning(f"Object ID {oid} search failed: {e}", exc_info=True)
                return []

        with TimeMeasure("search: object_ids behavior KNN"):
            results_list = await asyncio.gather(*[_safe_object_search(oid) for oid in search_input.object_ids])

        all_results: list[AttributeSearchResult] = []
        for obj_results in results_list:
            all_results.extend(obj_results)

        seen: dict[str, AttributeSearchResult] = {}
        unmergeable: list[AttributeSearchResult] = []
        for r in all_results:
            key = _coerce_str(r.metadata.object_id)
            # ``hit_to_result`` uses "unknown" as the missing-id sentinel.
            # Those rows cannot identify the same object, so collapsing them
            # would silently discard otherwise-valid behavior hits.
            if not key or key == "unknown":
                unmergeable.append(r)
                continue
            if key not in seen or _coerce_float(r.metadata.behavior_score) > _coerce_float(
                seen[key].metadata.behavior_score
            ):
                seen[key] = r
        attr_results = sorted(
            [*seen.values(), *unmergeable], key=lambda r: _coerce_float(r.metadata.behavior_score), reverse=True
        )[:top_k]

        await enrich_attribute_results(attr_results, config.vst_internal_url, config.vst_external_url)

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
    # Merging collapses runs of adjacent windows into one result *after*
    # retrieval, so fetching exactly ``top_k`` guarantees returning fewer than
    # ``top_k`` whenever any two hits are contiguous -- ten hits covering five
    # adjacent pairs come back as five results. Fetch headroom so the count
    # survives the collapse. The agent's own search tool doubles here for the
    # same reason; matching it keeps the two implementations comparable.
    if getattr(config, "merge_adjacent", True):
        top_k = top_k * 2
    top_k = min(top_k, _DOWNSTREAM_MAX_TOP_K)

    # Collected here (before routing) so a routing-affecting decision like
    # single-word attribute pruning can surface an observable note.
    search_messages: list[str] = []

    tag_params = {
        "query": search_input.query,
        "source_type": search_input.source_type,
        "video_sources": search_input.video_sources,
        "timestamp_start": search_input.timestamp_start,
        "timestamp_end": search_input.timestamp_end,
        "top_k": min(top_k, _DOWNSTREAM_MAX_TOP_K),
    }

    if search_input.search_mode == "tag":
        if tag_search is None:
            raise ConfigurationError("tag_search must be pre-loaded by the Search primitive")
        yield AgentMessageChunk(
            type=AgentMessageChunkType.TOOL_CALL,
            content=f"Running VLM tag search with query: '{search_input.query}'",
        )
        raw_tag_output = await tag_search.ainvoke(tag_params)
        tag_output = (
            raw_tag_output
            if isinstance(raw_tag_output, TagSearchOutput)
            else TagSearchOutput.model_validate(raw_tag_output)
        )
        search_results = _fusion.tag_output_to_search_results(tag_output)
        if tag_output.malformed_documents:
            search_messages.append(
                f"Skipped {tag_output.malformed_documents} malformed VLM tag "
                f"document{'s' if tag_output.malformed_documents != 1 else ''}."
            )
        yield SearchOutput(data=search_results[:top_k], search_messages=search_messages)
        return

    query_params: dict[str, str] = {"query": search_input.query}

    if search_input.video_sources and len(search_input.video_sources) > 0:
        query_params["video_sources"] = json.dumps(search_input.video_sources)
    if search_input.description:
        query_params["description"] = search_input.description
    if search_input.timestamp_start:
        query_params["timestamp_start"] = search_input.timestamp_start.isoformat()
    if search_input.timestamp_end:
        query_params["timestamp_end"] = search_input.timestamp_end.isoformat()
    query_params["min_cosine_similarity"] = str(search_input.min_cosine_similarity)

    attribute_list: list[str] = []
    is_attribute_only = search_input.search_mode == "attribute"
    if search_input.attributes:
        attribute_list = search_input.attributes

        attribute_list = [attr.strip() for attr in attribute_list if attr.strip()]

    if search_input.search_mode == "fusion":
        if tag_search is None:
            raise ConfigurationError("tag_search must be pre-loaded by the Search primitive")
        query_params["top_k"] = str(min(top_k, _DOWNSTREAM_MAX_TOP_K))
        query_input_json = json.dumps(
            {
                "params": query_params,
                "source_type": search_input.source_type,
                "exclude_videos": [],
            }
        )

        async def _embed_provider() -> list[SearchResult]:
            output = await embed_search.ainvoke(query_input_json)
            validated = (
                output
                if isinstance(output, EmbedSearchOutput)
                else EmbedSearchOutput.model_validate_json(output)
                if isinstance(output, str)
                else EmbedSearchOutput.model_validate(output)
            )
            return _fusion.embed_output_to_search_results(validated)

        async def _tag_provider() -> tuple[list[SearchResult], int]:
            output = await tag_search.ainvoke(tag_params)
            validated = output if isinstance(output, TagSearchOutput) else TagSearchOutput.model_validate(output)
            return _fusion.tag_output_to_search_results(validated), validated.malformed_documents

        provider_names = ["embed", "tag"]
        provider_calls: list[Any] = [_embed_provider(), _tag_provider()]
        if attribute_list:
            if attribute_search_fn is None:
                raise ConfigurationError("attribute_search_fn must be pre-loaded by the Search primitive")
            provider_names.append("attribute")
            provider_calls.append(
                _run_attribute_only_search(
                    attribute_list=attribute_list,
                    search_input=search_input,
                    attribute_search_fn=attribute_search_fn,
                    top_k=min(top_k, _DOWNSTREAM_MAX_TOP_K),
                    min_similarity=0.0,
                    search_messages=search_messages,
                )
            )

        yield AgentMessageChunk(
            type=AgentMessageChunkType.TOOL_CALL,
            content="Running tag, embedding, and optional attribute retrieval for fusion",
        )
        outcomes = await asyncio.gather(*provider_calls, return_exceptions=True)
        provider_results: dict[str, list[SearchResult]] = {}
        failures: list[Exception] = []
        malformed_documents = 0
        for provider, outcome in zip(provider_names, outcomes, strict=True):
            if isinstance(outcome, BaseException):
                if not isinstance(outcome, Exception):
                    raise outcome
                if isinstance(outcome, BackendUnreachableError):
                    failures.append(outcome)
                    search_messages.append(f"{provider.capitalize()} provider degraded: {outcome}")
                    continue
                raise outcome
            if provider == "tag":
                tag_results, malformed_documents = outcome
                provider_results[provider] = tag_results
            else:
                provider_results[provider] = outcome

        if not provider_results:
            if failures:
                raise failures[0]
            raise BackendUnreachableError("search", "all fusion providers failed")
        if malformed_documents:
            search_messages.append(
                f"Skipped {malformed_documents} malformed VLM tag document{'s' if malformed_documents != 1 else ''}."
            )

        search_results = _fusion.fuse_ranked_union(
            provider_results,
            method=config.fusion_method,
            weights={"tag": config.w_tag, "embed": config.w_embed, "attribute": config.w_attribute},
            rrf_k=config.rrf_k,
        )
        if getattr(config, "merge_adjacent", True):
            search_results = _fusion.merge_consecutive_results(search_results)
        yield SearchOutput(data=search_results[:original_top_k], search_messages=search_messages)
        return

    # ----- EXECUTION FLOW: embed / attribute-only -----
    # The object_id path above returns before reaching here, so this
    # ``search_results`` init is only hit on the remaining paths. Reusing the
    # name without a fresh annotation keeps mypy's no-redef check happy.
    search_results = []
    # A one-element loop preserves the existing execution block's scope while
    # making the single-pass contract explicit. Search has no hidden re-search.
    for _search_pass in (None,):
        logger.info("[Search] Running search")

        # Clamp to the downstream models' bound so the fetch size never trips a ValidationError.
        top_k = min(top_k, _DOWNSTREAM_MAX_TOP_K)
        query_params["top_k"] = str(top_k)
        # Search is single-pass, so there are no orchestrator-generated exclusions.
        query_input_json = json.dumps(
            {
                "params": query_params,
                "source_type": search_input.source_type,
                "exclude_videos": [],
            }
        )

        # PATH 1: Attribute-only
        if is_attribute_only and attribute_list and getattr(config, "attribute_search_tool", None):
            logger.info("EXECUTION PATH: Attribute-only search (no embed, append mode)")
            yield AgentMessageChunk(
                type=AgentMessageChunkType.TOOL_CALL,
                content=f"Running attribute-only search with {len(attribute_list)} attributes",
            )

            if attribute_search_fn is None:
                raise ConfigurationError("attribute_search_fn must be pre-loaded by the Search primitive")

            with TimeMeasure("search: attribute-only search"):
                search_results = await _run_attribute_only_search(
                    attribute_list=attribute_list,
                    search_input=search_input,
                    attribute_search_fn=attribute_search_fn,
                    top_k=min(original_top_k, _DOWNSTREAM_MAX_TOP_K),
                    min_similarity=0.0,
                    exclude_videos=[],
                    search_messages=search_messages,
                )

            yield AgentMessageChunk(
                type=AgentMessageChunkType.THOUGHT,
                content=f"Found {len(search_results)} results from attribute-only search",
            )

        # PATH 2: Embed search
        else:
            logger.info("EXECUTION PATH: Embed search")
            yield AgentMessageChunk(
                type=AgentMessageChunkType.TOOL_CALL,
                content=f"Running embed search with query: '{search_input.query}'",
            )

            try:
                with TimeMeasure("search: embed search"):
                    embed_search_output = await embed_search.ainvoke(query_input_json)
            except LibraryError as e:
                # Already a library error (InvalidInputError, IndexNotFoundError,
                # BackendUnreachableError, ...). Surface it without re-wrapping so
                # CLI exit codes and caller handling stay precise (e.g. invalid
                # input keeps exit 2 rather than being masked as a backend fault).
                logger.error(f"Embed search failed: {e}")
                yield AgentMessageChunk(
                    type=AgentMessageChunkType.ERROR,
                    content=f"Embed search failed: {e}",
                )
                raise
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

            search_results = _fusion.embed_output_to_search_results(embed_output)

            yield AgentMessageChunk(
                type=AgentMessageChunkType.THOUGHT,
                content=f"Found {len(search_results)} results from embed search",
            )

        # Percentage-based filtering
        search_results = _fusion.apply_top_percent_filter(search_results, getattr(config, "top_percent_filter", None))

        if getattr(config, "merge_adjacent", True):
            search_results = _fusion.merge_consecutive_results(search_results)

    result_count = len(search_results)
    yield AgentMessageChunk(
        type=AgentMessageChunkType.THOUGHT,
        content=f"Found {result_count} result{'s' if result_count != 1 else ''}",
    )

    search_results = search_results[:original_top_k]

    yield SearchOutput(data=search_results, search_messages=search_messages)


async def execute_core_search_wrapper(
    search_input: SearchInput,
    embed_search: SupportsAinvoke,
    config: SearchConfig,
    attribute_search_fn: SupportsAinvoke | None = None,
    tag_search: SupportsAinvoke | None = None,
    behavior_es: ElasticIndex | None = None,
) -> SearchOutput:
    """Non-streaming wrapper: collects chunks, returns final SearchOutput."""
    updates = execute_core_search(
        search_input=search_input,
        embed_search=embed_search,
        config=config,
        attribute_search_fn=attribute_search_fn,
        tag_search=tag_search,
        behavior_es=behavior_es,
    )
    async with aclosing(updates):
        async for update in updates:
            if isinstance(update, SearchOutput):
                return update
    raise NoFinalResultError("execute_core_search exited without yielding SearchOutput")
