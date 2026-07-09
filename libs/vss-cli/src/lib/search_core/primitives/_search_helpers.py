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
    embed/attribute/critic/behavior-ES adapters to those pure helpers and yields
    progress chunks then a final :class:`SearchOutput`.

Error policy is hybrid: systemic failures (any :class:`SearchError` —
``IndexNotFoundError`` / ``BackendUnreachableError`` / ``InvalidInputError``)
propagate so callers get precise errors and exit codes; only genuinely
best-effort work degrades softly (a single video's attribute lookup during
fusion, and the additive critic-verification pass).
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import timedelta
from typing import TYPE_CHECKING, Any, Protocol

from lib._foundation.sanitize import scrub_log
from lib._foundation.time import datetime_to_iso8601, safe_iso8601_to_datetime
from lib.vst import get_sensor_id_from_stream_id

from .._internal.coerce import _coerce_float, _coerce_str
from .._internal.time_measure import TimeMeasure
from ..agent_chunks import AgentMessageChunk, AgentMessageChunkType
from ..clients.elastic import ElasticClient
from ..errors import BackendUnreachableError, ConfigurationError, NoFinalResultError, SearchError
from ..models.attribute_search import AttributeSearchResult
from ..models.common import VideoInfo
from ..models.critic import CriticAgentResult
from ..models.critic import VideoResult as CriticVideoResult
from ..models.embed_search import EmbedSearchOutput
from ..models.search import CriticResult, SearchInput, SearchOutput, SearchResult
from . import _fusion
from ._attribute_helpers import enrich_attribute_results, resolve_index_by_source_type, search_by_object_embedding

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from ..clients.protocols import ElasticIndex

logger = logging.getLogger(__name__)

# Downstream input models (``EmbedSearchInput`` / ``AttributeSearchInput``) cap
# ``top_k`` at this value (``Field(le=1000)``). Overfetching / critic-driven
# growth is clamped to it so a large user ``top_k`` never trips a Pydantic
# ``ValidationError`` deep in a primitive.
_DOWNSTREAM_MAX_TOP_K = 1000

# A stable per-result identity for critic bookkeeping. Merging consecutive chunks
# extends a result's ``end_time`` between iterations, so keying verdicts by the
# full (sensor, start, end) window would let a merged result look "new" and get
# re-sent to the VLM. Sensor id + the chunk's start_time is stable across merges
# because :func:`_fusion.merge_consecutive_results` keeps the earliest start.
CriticKey = tuple[str, str]


class SupportsAinvoke(Protocol):
    """The single-method async adapter surface the orchestrator invokes.

    ``embed_search`` / ``attribute_search_fn`` / ``critic_agent`` are all wrapped
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
    search_max_iterations: int
    embed_confidence_threshold: float
    use_attribute_search: bool
    enable_critic: bool
    fusion_method: str
    w_attribute: float
    w_embed: float
    rrf_k: int
    rrf_w: float
    vst_internal_url: str
    vst_external_url: str
    behavior_index: str


def _critic_key(result: SearchResult) -> CriticKey:
    """Stable identity for a result across re-search/merge iterations."""
    return (result.sensor_id, result.start_time)


def _exclude_entry(result: SearchResult) -> dict[str, str]:
    """Build an ``exclude_videos`` entry matching the embed/attribute filters.

    The embed and attribute paths both match an exclusion on the raw
    ``(sensor_id, start_timestamp, end_timestamp)`` strings, so a rejected
    result's own fields are the correct keys to suppress it on re-search.
    """
    return {
        "sensor_id": result.sensor_id,
        "start_timestamp": result.start_time,
        "end_timestamp": result.end_time,
    }


def _video_info_for_critic(result: SearchResult) -> VideoInfo | None:
    """Build a :class:`VideoInfo` for critic verification, or None.

    A result whose start/end timestamps do not parse cannot be clip-verified, so
    it is skipped (returns None) rather than crashing the critic pass.
    """
    start_dt = safe_iso8601_to_datetime(result.start_time)
    end_dt = safe_iso8601_to_datetime(result.end_time)
    if start_dt is None or end_dt is None:
        return None
    return VideoInfo(sensor_id=result.sensor_id, start_timestamp=start_dt, end_timestamp=end_dt)


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
    except SearchError:
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
    score. Systemic failures (any :class:`SearchError`) propagate and abort the
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
        except SearchError:
            # Systemic failure (missing index, backend unreachable, invalid input)
            # affects every video equally — propagate rather than degrade.
            raise
        except Exception as e:
            logger.warning(
                f"Fusion attribute lookup failed for {scrub_log(embed_result.video_name)}: {scrub_log(str(e))}",
                exc_info=True,
            )
            return embed_result, None

    # No return_exceptions: a systemic SearchError from any video propagates.
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
    critic_agent: SupportsAinvoke | None = None,
    behavior_es: ElasticIndex | None = None,
) -> AsyncGenerator[AgentMessageChunk | SearchOutput]:
    """Core search execution: yields progress chunks, then a final SearchOutput.

    Routes to one of four paths:
      1. object_id re-search (direct behavior kNN by an existing object's vector)
      2. attribute-only (attributes present and ``has_action`` is false)
      3. embed-only (no attributes)
      4. fusion (attributes present and embed confidence is high enough)

    The injected adapters (``embed_search``, ``attribute_search_fn``,
    ``critic_agent``) each expose an async ``.ainvoke``; ``behavior_es`` is an
    Elasticsearch surface used only by the object_id path. All are supplied by
    the caller; this generator only wires them to the pure helpers.
    """
    if search_input.use_critic is True and not config.enable_critic:
        raise ConfigurationError("critic verification was requested but the runtime disables critic wiring")
    critic_requested = config.enable_critic if search_input.use_critic is None else search_input.use_critic
    if critic_requested and critic_agent is None:
        raise ConfigurationError(
            "critic verification is enabled but no VLM analyzer is configured; "
            "inject a critic agent or explicitly set use_critic=false"
        )
    search_input = search_input.model_copy(update={"use_critic": critic_requested})
    original_query = search_input.original_query or search_input.query

    # ----- OBJECT_ID PATH: Direct behavior KNN -----
    if search_input.object_ids:
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
            except SearchError:
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
    # Overfetch 2x so critic-driven replacement has spare candidates to promote,
    # but clamp to the downstream models' ``le=1000`` bound — a large user top_k
    # (e.g. 900) would otherwise double to 1800 and trip a Pydantic
    # ValidationError deep in the embed/attribute primitive.
    top_k = min(top_k * 2, _DOWNSTREAM_MAX_TOP_K)

    # Collected here (before routing) so a routing-affecting decision like
    # single-word attribute pruning can surface an observable note.
    search_messages: list[str] = []

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
            if not attribute_list:
                # Pruning emptied the list, which silently flips routing from
                # attribute-only/fusion to embed-only. Surface it so the change
                # is observable rather than buried in an INFO log.
                msg = f"All {original_count} attribute(s) were single-word and pruned; routing to embed-only search."
                search_messages.append(msg)
                yield AgentMessageChunk(type=AgentMessageChunkType.THOUGHT, content=msg)

        if search_input.has_action is not None:
            is_attribute_only = not search_input.has_action
        elif attribute_list:
            is_attribute_only = True

    # ----- EXECUTION FLOW: embed / attribute-only / fusion -----
    # The object_id path above returns before reaching here, so this
    # ``search_results`` init is only hit on the remaining paths. Reusing the
    # name without a fresh annotation keeps mypy's no-redef check happy.
    search_results = []
    do_search = True
    # Critic bookkeeping is keyed by a STABLE per-result identity (see CriticKey)
    # so a verdict survives the end_time drift that merge_consecutive_results
    # introduces across re-search iterations.
    rejected_keys: set[CriticKey] = set()
    confirmed_keys: set[CriticKey] = set()
    # Running exclusion list built from rejected results, threaded into each
    # re-search so rejected candidates are not simply re-fetched.
    exclude_videos: list[dict[str, str]] = []
    iteration_num = 0
    persistent_critic_results: dict[CriticKey, CriticResult] = {}

    while do_search and iteration_num < config.search_max_iterations:
        iteration_num += 1
        do_search = False
        logger.info(f"[Search] Running embed search iteration {iteration_num}")

        # ``top_k`` may have grown via critic-driven re-search; clamp to the
        # downstream models' bound so the fetch size never trips a ValidationError.
        top_k = min(top_k, _DOWNSTREAM_MAX_TOP_K)
        query_params["top_k"] = str(top_k)
        # Thread the running exclusion list into the embed envelope so a re-search
        # surfaces NEW candidates instead of re-fetching rejected ones.
        query_input_json = json.dumps(
            {
                "params": query_params,
                "source_type": search_input.source_type,
                "exclude_videos": exclude_videos,
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
                    exclude_videos=exclude_videos,
                    search_messages=search_messages,
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
            except SearchError as e:
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
                        raise ConfigurationError("attribute_search_fn must be pre-loaded by the Search primitive")

                    with TimeMeasure("search: attribute-only fallback"):
                        search_results = await _run_attribute_only_search(
                            attribute_list=attribute_list,
                            search_input=search_input,
                            attribute_search_fn=attribute_search_fn,
                            top_k=top_k,
                            min_similarity=0.0,
                            exclude_videos=exclude_videos,
                            search_messages=search_messages,
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
                            raise ConfigurationError("attribute_search_fn must be pre-loaded by the Search primitive")

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
                    except SearchError as e:
                        # Hybrid policy: a systemic fusion failure is fatal.
                        # Per-video attribute degradation is handled inside
                        # fusion_search_rerank and never reaches here.
                        logger.error(f"Fusion reranking failed: {e}", exc_info=True)
                        yield AgentMessageChunk(
                            type=AgentMessageChunkType.ERROR,
                            content=f"Fusion reranking failed: {e}",
                        )
                        raise

        # Percentage-based filtering
        search_results = _fusion.apply_top_percent_filter(search_results, getattr(config, "top_percent_filter", None))

        search_results = _fusion.merge_consecutive_results(search_results)

        # Critic verification.
        #
        # DELIBERATE DEVIATION from the hybrid error policy used elsewhere in
        # this module: a typed ``BackendUnreachableError`` from the experimental,
        # additive critic soft-fails with a ``search_messages`` note so a
        # transient VLM/VST outage does not discard otherwise-valid search
        # results. Configuration, input, and unexpected implementation errors
        # propagate; they must not be mislabeled as optional backend downtime.
        if config.enable_critic and search_input.use_critic and critic_agent and search_results:
            try:
                critic_results: dict[VideoInfo, CriticVideoResult] = {}

                yield AgentMessageChunk(
                    type=AgentMessageChunkType.THOUGHT,
                    content=f"Verifying {len(search_results)} results with critic agent",
                )

                # Map each candidate VideoInfo back to its stable key and result
                # so verdicts (returned keyed by VideoInfo) can be recorded under
                # the merge-stable identity.
                search_videos: list[VideoInfo] = []
                info_to_key: dict[VideoInfo, CriticKey] = {}
                info_to_result: dict[VideoInfo, SearchResult] = {}
                for result in search_results:
                    info = _video_info_for_critic(result)
                    if info is None:
                        # No parseable timestamps -> cannot be clip-verified.
                        continue
                    crit_key = _critic_key(result)
                    if crit_key in confirmed_keys or crit_key in rejected_keys:
                        continue
                    search_videos.append(info)
                    info_to_key[info] = crit_key
                    info_to_result[info] = result

                if len(search_videos) > 0:
                    critic_input = {"query": original_query, "videos": search_videos}
                    with TimeMeasure("search: critic agent verification"):
                        critic_output = await critic_agent.ainvoke(critic_input)
                    critic_results = {r.video_info: r for r in critic_output.video_results}

                    for info, video_result in critic_results.items():
                        recorded_key = info_to_key.get(info)
                        if recorded_key is None:
                            continue
                        match video_result.result:
                            case CriticAgentResult.CONFIRMED:
                                confirmed_keys.add(recorded_key)
                            case CriticAgentResult.REJECTED:
                                rejected_keys.add(recorded_key)
                                # Suppress the rejected clip on re-search and grow
                                # the fetch so a replacement can take its slot.
                                exclude_videos.append(_exclude_entry(info_to_result[info]))
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
                        recorded_key = info_to_key.get(info)
                        if recorded_key is not None:
                            persistent_critic_results[recorded_key] = CriticResult(
                                result=vr.result.value,
                                criteria_met=vr.criteria_met or {},
                            )

                for result in search_results:
                    crit_key = _critic_key(result)
                    if crit_key in persistent_critic_results:
                        result.critic_result = persistent_critic_results[crit_key]

                verified_count = sum(1 for vr in critic_results.values() if vr.result == CriticAgentResult.CONFIRMED)
                unverified_count = sum(1 for vr in critic_results.values() if vr.result == CriticAgentResult.UNVERIFIED)

                yield AgentMessageChunk(
                    type=AgentMessageChunkType.THOUGHT,
                    content=(
                        f"Critic verification complete: {verified_count}/{len(critic_results)} verified, "
                        f"{unverified_count}/{len(critic_results)} unverified"
                    ),
                )

            except BackendUnreachableError as e:
                logger.error(f"[Search] Error calling critic agent: {e}", exc_info=True)
                msg = "Critic verification unavailable. Returning results without verification."
                search_messages.append(msg)
                yield AgentMessageChunk(type=AgentMessageChunkType.THOUGHT, content=msg)

    # TRUE reject-replacement semantics: drop every result the critic REJECTED so
    # a rejected clip cannot keep its slot (and squeeze out a replacement) after
    # the final top_k cap. This is applied unconditionally — even at the default
    # search_max_iterations=1, where no re-search happens, the rejected result is
    # still removed from the output.
    search_results = [r for r in search_results if _critic_key(r) not in rejected_keys]

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
    critic_agent: SupportsAinvoke | None = None,
    behavior_es: ElasticIndex | None = None,
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
    raise NoFinalResultError("execute_core_search exited without yielding SearchOutput")
