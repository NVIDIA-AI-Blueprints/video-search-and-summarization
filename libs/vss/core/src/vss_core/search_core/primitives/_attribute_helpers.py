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
"""Helpers for AttributeSearch.

Two layers live here:
  - pure, synchronous, dependency-free builders/mappers (index selection, the
    behavior kNN body, ES filters, hit -> result mapping, dedup) that unit-test
    with plain dicts; and
  - thin async orchestration over an injected Elasticsearch surface and text
    embedder that composes those builders and performs the IO.

Backend/index failures surface as the library error hierarchy
(``IndexNotFoundError`` / ``BackendUnreachableError``); a single corrupt stored
document is skipped rather than failing the whole search.
"""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime
from datetime import timedelta
import logging
import math
from typing import TYPE_CHECKING
from typing import Any
from typing import Literal

from vss_core._foundation.errors import LibraryError
from vss_core._foundation.sanitize import scrub_log
from vss_core._foundation.time import datetime_to_iso8601
from vss_core._foundation.time import iso8601_instants_match
from vss_core._foundation.time import iso8601_to_datetime
from vss_core._foundation.time import safe_iso8601_to_datetime
from vss_core.vios import VSTError
from vss_core.vios import build_screenshot_url
from vss_core.vios import get_stream_id
from vss_core.vios import get_timeline
from vss_core.vios import get_timelines_map
from vss_core.vios import map_timestamp_to_timeline

from .._internal.coerce import _coerce_str
from .._internal.es_filters import build_video_sources_filter
from .._internal.time_measure import TimeMeasure
from ..clients.protocols import MultiSearchIndex
from ..errors import BackendUnreachableError
from ..errors import ConfigurationError
from ..errors import IndexNotFoundError
from ..errors import InvalidInputError
from ..models.attribute_search import AttributeSearchInput
from ..models.attribute_search import AttributeSearchMetadata
from ..models.attribute_search import AttributeSearchResult
from ..runtime import BEHAVIOR_INDEX_ANCHOR

if TYPE_CHECKING:
    from ..clients.protocols import ElasticIndex
    from ..clients.protocols import TextEmbedder

logger = logging.getLogger(__name__)

# Clips shorter than this are widened around their midpoint for playback.
MIN_CLIP_DURATION_SECONDS = 1.0

# Behavior-index fields the kNN search needs back on each hit.
_BEHAVIOR_SOURCE_FIELDS = [
    "object.id",
    "object.type",
    "object.bbox",
    "sensor.id",
    "sensor.stream_id",
    "timestamp",
    "end",
]

# Sentinel used by :func:`hit_to_result` when an object/sensor id is absent. Rows
# carrying it are never merged during dedup (a missing id is not evidence that
# two detections are the same object).
_UNKNOWN_ID = "unknown"

# Result of a per-object best-frame lookup: (frame_id, bbox, frame_score,
# frame_timestamp). Any element may be None when the enhancement misses.
FrameLookupResult = tuple[int | None, dict[str, Any] | None, float | None, str | None]


# =============================================================================
# Pure builders / mappers
# =============================================================================


def resolve_index_by_source_type(
    base_index: str,
    source_type: Literal["video_file", "rtsp"],
    wildcard_pattern: str,
) -> str | list[str]:
    """Resolve ES index(es) by source_type for the behavior/raw families.

    - ``video_file`` -> ``base_index`` unchanged.
    - ``rtsp``       -> ``[wildcard_pattern, "-" + base_index]``.

    The embed path uses the identical subtraction
    (:func:`_embed_helpers.select_search_index`); behavior and raw documents carry
    only ``sensor.id`` (no media-kind field), so source partitioning relies on
    index-name subtraction here too. For that to be
    correct, ``base_index`` MUST be the pinned uploads anchor
    (``mdx-behavior-2025-01-01`` / ``mdx-raw-2025-01-01``: the write-side contract
    in ``video_delete.py`` and the ``SearchRuntime`` defaults), never a value
    discovered from the live index inventory: an ``rtsp`` query subtracts exactly
    the base, so a live-dated base would exclude the very data being searched. A
    ``video_file`` query against an absent anchor is treated downstream as an empty
    uploads partition (see :func:`_search_behavior`).
    """
    if source_type == "video_file":
        return base_index
    if source_type == "rtsp":
        return [wildcard_pattern, "-" + base_index]
    raise ValueError(f"Unsupported source_type {source_type!r}; expected 'video_file' or 'rtsp'.")


def compute_fetch_k(top_k: int) -> int:
    """Overfetch factor for behavior kNN.

    A single requested result still fetches a small pool (dedup/exclusion may
    drop hits); larger requests overfetch by 10x, floored at 200.
    """
    return 10 if top_k == 1 else max(top_k * 10, 200)


def build_behavior_overlap_filter(
    timestamp_start: datetime | None,
    timestamp_end: datetime | None,
) -> dict[str, Any] | None:
    """Build the time-overlap filter for behavior hits (or None).

    A behavior span [timestamp, end] overlaps the requested window when its
    ``end >= start`` and its ``timestamp <= end``.
    """
    must: list[dict[str, Any]] = []
    if timestamp_start:
        must.append({"range": {"end": {"gte": timestamp_start.isoformat()}}})
    if timestamp_end:
        must.append({"range": {"timestamp": {"lte": timestamp_end.isoformat()}}})
    return {"bool": {"must": must}} if must else None


def build_behavior_knn_body(
    query_embedding: list[float],
    top_k: int,
    min_similarity: float,
    filter_clauses: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build the behavior-index kNN search body.

    Score space: the behavior index stores cosine ``dense_vector`` fields, and
    Elasticsearch reports the kNN ``_score`` for a cosine vector as the shifted
    value ``(1 + cosine) / 2`` (mapped to ``[0, 1]``, never negative). We pass
    ``min_similarity`` straight through as the ES ``min_score``, so it is a
    threshold in that same ``(1 + cosine) / 2`` space — NOT raw cosine in
    ``[-1, 1]``. Callers that reason in raw cosine must shift first
    (``min_score = (1 + cosine) / 2``). This is intentionally consistent with the
    frame-lookup score (see :func:`_get_frame_from_behavior`), which also reports
    ``(1 + cosine) / 2``.
    """
    fetch_k = compute_fetch_k(top_k)
    knn_query: dict[str, Any] = {
        "field": "embeddings.vector",
        "query_vector": query_embedding,
        "k": fetch_k,
        "num_candidates": max(fetch_k * 2, 100),
    }
    if filter_clauses:
        knn_query["filter"] = {"bool": {"must": filter_clauses}} if len(filter_clauses) > 1 else filter_clauses[0]
    return {
        "knn": knn_query,
        "size": fetch_k,
        "min_score": min_similarity,
        "_source": list(_BEHAVIOR_SOURCE_FIELDS),
    }


def midpoint_iso(start_iso: str, end_iso: str) -> str | None:
    """Return the ISO-8601 midpoint between two ISO timestamps, or None if
    either fails to parse."""
    start_dt = safe_iso8601_to_datetime(start_iso)
    end_dt = safe_iso8601_to_datetime(end_iso)
    if start_dt is None or end_dt is None:
        return None
    return datetime_to_iso8601(start_dt + (end_dt - start_dt) / 2)


def _behavior_bbox(obj: dict[str, Any]) -> dict[str, Any] | None:
    bbox = obj.get("bbox") or {}
    if not bbox:
        return None
    return {
        "leftX": bbox.get("leftX"),
        "rightX": bbox.get("rightX"),
        "topY": bbox.get("topY"),
        "bottomY": bbox.get("bottomY"),
    }


def hit_to_result(
    hit: dict[str, Any],
    frame_result: FrameLookupResult | None,
    input_timestamp_start: datetime | None = None,
    input_timestamp_end: datetime | None = None,
) -> AttributeSearchResult:
    """Map one behavior hit (plus optional frame-lookup result) to a result item.

    Recoverable field-level quirks (null / odd-typed values) are coerced to clean
    strings so a usable document is never dropped. ``frame_result`` is the tuple
    from :func:`_get_frame_from_behavior` or None.
    """
    score = float(hit["_score"])
    source = hit.get("_source") or {}
    obj = source.get("object") or {}
    sensor = source.get("sensor") or {}
    object_id = _coerce_str(obj.get("id"), _UNKNOWN_ID)
    sensor_id = _coerce_str(sensor.get("id"), _UNKNOWN_ID)
    object_type = _coerce_str(obj.get("type"), _UNKNOWN_ID)

    frame_bbox = None
    query_to_frame_score = None
    best_frame_timestamp = None
    if isinstance(frame_result, tuple):
        _, frame_bbox, query_to_frame_score, best_frame_timestamp = frame_result

    final_bbox = frame_bbox if frame_bbox is not None else _behavior_bbox(obj)

    behavior_start = str(source["timestamp"]) if source.get("timestamp") else None
    behavior_end = str(source["end"]) if source.get("end") else None

    if best_frame_timestamp:
        final_timestamp: str | None = str(best_frame_timestamp)
    elif behavior_start and behavior_end:
        final_timestamp = midpoint_iso(behavior_start, behavior_end) or behavior_end
    else:
        final_timestamp = behavior_end or behavior_start

    if input_timestamp_start is not None:
        output_start_time: str | None = datetime_to_iso8601(input_timestamp_start)
        output_end_time: str | None = (
            datetime_to_iso8601(input_timestamp_end) if input_timestamp_end is not None else output_start_time
        )
    else:
        output_start_time = behavior_start
        output_end_time = behavior_end or behavior_start

    metadata = AttributeSearchMetadata(
        sensor_id=sensor_id,
        object_id=object_id,
        object_type=object_type,
        frame_timestamp=final_timestamp,
        start_time=output_start_time,
        end_time=output_end_time,
        bbox=final_bbox,
        behavior_score=score,
        frame_score=query_to_frame_score,
        video_name=None,
    )
    return AttributeSearchResult(screenshot_url=None, metadata=metadata)


def deduplicate_by_object(
    results: list[AttributeSearchResult],
    candidates: list[dict[str, Any]] | None = None,
) -> list[AttributeSearchResult]:
    """Merge duplicate ``(sensor_id, object_id)`` results, widening the time range.

    When ``candidates`` is provided it MUST be positionally aligned with
    ``results`` (``candidates[i]`` is the raw hit that produced ``results[i]``);
    callers build the two lists in lockstep so a skipped hit cannot desync the
    indices and make the time-widen read the wrong ``_source``.

    Rows whose sensor or object id is the ``"unknown"`` sentinel are never merged
    — a missing id is not evidence that two detections are the same object — so
    each such row is preserved as a distinct result.
    """
    # Output slots preserve first-seen order; each remembers the candidate index
    # that produced it so a later duplicate can widen its time range correctly.
    slots: list[tuple[AttributeSearchResult, int]] = []
    key_to_slot: dict[tuple[str, str], int] = {}
    duplicate_count = 0
    merge_count = 0

    for idx, result in enumerate(results):
        if not result.metadata:
            continue
        sensor_id = result.metadata.sensor_id
        object_id = result.metadata.object_id or _UNKNOWN_ID
        mergeable = sensor_id != _UNKNOWN_ID and object_id != _UNKNOWN_ID
        key = (sensor_id, object_id)

        if not mergeable or key not in key_to_slot:
            if mergeable:
                key_to_slot[key] = len(slots)
            slots.append((result, idx))
            continue

        slot_pos = key_to_slot[key]
        existing_result, existing_idx = slots[slot_pos]
        duplicate_count += 1
        if not (candidates and existing_idx < len(candidates) and idx < len(candidates)):
            continue
        if not existing_result.metadata:
            continue

        existing_source = candidates[existing_idx].get("_source", {})
        new_source = candidates[idx].get("_source", {})
        existing_start = existing_result.metadata.start_time or existing_source.get("timestamp")
        existing_end = existing_result.metadata.end_time or existing_source.get("end")
        new_start = new_source.get("timestamp")
        new_end = new_source.get("end")

        earliest_start = _earlier(new_start, existing_start)
        latest_end = _later(new_end, existing_end)
        if earliest_start != existing_start or latest_end != existing_end:
            merge_count += 1
            existing_result.metadata.start_time = earliest_start
            existing_result.metadata.end_time = latest_end

    if duplicate_count > 0:
        logger.info(
            f"Deduplication: found {duplicate_count} duplicate(s), merged {merge_count} time range(s). "
            f"Kept {len(slots)} unique result(s) from {len(results)} total."
        )
    return [result for result, _ in slots]


def _earlier(candidate: str | None, current: str | None) -> str | None:
    """Return whichever ISO timestamp is earlier, tolerating unparseable input."""
    if candidate and current:
        cand_dt = safe_iso8601_to_datetime(candidate)
        cur_dt = safe_iso8601_to_datetime(current)
        if cand_dt and cur_dt and cand_dt < cur_dt:
            return candidate
        return current
    return candidate or current


def _later(candidate: str | None, current: str | None) -> str | None:
    """Return whichever ISO timestamp is later, tolerating unparseable input."""
    if candidate and current:
        cand_dt = safe_iso8601_to_datetime(candidate)
        cur_dt = safe_iso8601_to_datetime(current)
        if cand_dt and cur_dt and cand_dt > cur_dt:
            return candidate
        return current
    return candidate or current


def _is_attribute_excluded(
    *,
    sensor_id_raw: str,
    stream_id: str | None,
    start_time: str | None,
    end_time: str | None,
    exclude_videos: list[dict[str, str]],
) -> bool:
    """Return True when this (sensor, window) is in ``exclude_videos``.

    Mirrors ``_embed_helpers.is_excluded``: an entry matches when its
    ``sensor_id`` equals either the raw behavior ``sensor.id`` or the resolved
    stream id, and its start/end match by instant (tolerating ``Z`` vs
    ``+00:00`` and differing fractional-second widths) via the shared
    :func:`iso8601_instants_match`. Matching the resolved id matters for RTSP,
    where callers build exclude lists from the returned (resolved) ``sensor_id``.
    """
    for ex in exclude_videos:
        ex_sensor = ex.get("sensor_id", "")
        sensor_matches = ex_sensor == sensor_id_raw or (stream_id is not None and ex_sensor == stream_id)
        if not sensor_matches:
            continue
        if iso8601_instants_match(start_time, ex.get("start_timestamp", "")) and iso8601_instants_match(
            end_time, ex.get("end_timestamp", "")
        ):
            return True
    return False


# =============================================================================
# Elasticsearch IO
# =============================================================================


def _is_absent_uploads_anchor(index: str | list[str]) -> bool:
    """Whether a NotFound target is the pinned uploads anchor (an empty uploads
    partition, no files ingested) rather than a real fault.

    Gated on equality with the anchor constant, not "is concrete": a customized
    or genuinely-broken base still raises, and an ``rtsp`` wildcard list is never
    equal. Overriding the base off the anchor therefore costs graceful-empty (see
    the ``SearchRuntime`` base fields).
    """
    return index == BEHAVIOR_INDEX_ANCHOR


async def _search_behavior(
    index: str | list[str],
    query_embedding: list[float],
    top_k: int,
    min_similarity: float,
    es: ElasticIndex,
    timestamp_start: datetime | None = None,
    timestamp_end: datetime | None = None,
    video_sources: list[str] | None = None,
    source_type: str = "video_file",
    candidate_limit: int | None = None,
    include_embeddings: bool = False,
) -> list[dict[str, Any]]:
    """kNN-search the behavior embeddings and return the raw hits."""
    filter_clauses: list[dict[str, Any]] = []
    overlap = build_behavior_overlap_filter(timestamp_start, timestamp_end)
    if overlap is not None:
        filter_clauses.append(overlap)
    video_sources_clause = build_video_sources_filter(video_sources, source_type)
    if video_sources_clause is not None:
        filter_clauses.append(video_sources_clause)

    search_query = build_behavior_knn_body(query_embedding, top_k, min_similarity, filter_clauses)
    search_index_str = index if isinstance(index, str) else ",".join(index)
    logger.debug(
        f"Behavior kNN: index={search_index_str} dim={len(query_embedding)} "
        f"k={search_query['knn']['k']} filters={len(filter_clauses)}"
    )

    try:
        if candidate_limit is not None:
            exact_search = getattr(es, "exhaustive_vector_search", None)
            if not callable(exact_search):
                raise ConfigurationError("Exact attribute scanning requires an exhaustive-vector backend")
            response = await exact_search(
                index=search_index_str,
                filters=filter_clauses,
                vector=query_embedding,
                field="embeddings.vector",
                nested_path="embeddings",
                source_includes=["object", "sensor", "timestamp", "end"]
                + (["embeddings.vector"] if include_embeddings else []),
                candidate_limit=candidate_limit,
                min_score=min_similarity,
            )
        else:
            if include_embeddings:
                search_query["_source"].append("embeddings.vector")
            response = await es.search(index=search_index_str, body=search_query)
    except IndexNotFoundError as e:
        if _is_absent_uploads_anchor(index):
            logger.warning(
                f"Uploads anchor index '{index}' does not exist (no files ingested); "
                f"returning no {source_type} candidates."
            )
            return []
        logger.error(f"Elasticsearch index '{index}' not found: {e}")
        raise

    hits = list(response["hits"]["hits"])
    logger.info(f"Behavior search found {len(hits)} candidate(s)")
    return hits


_frame_query_preparations: ContextVar[dict | None] = ContextVar("frame_query_preparations", default=None)


@contextmanager
def frame_query_scope():
    """Share immutable query parameters only within the current search request."""
    token = _frame_query_preparations.set({}) if _frame_query_preparations.get() is None else None
    try:
        yield
    finally:
        if token is not None:
            _frame_query_preparations.reset(token)


def _prepare_frame_query(query_embedding: list[float]) -> dict[str, Any]:
    cache = _frame_query_preparations.get()
    key = tuple(query_embedding)
    if cache is not None and key in cache:
        return cache[key]
    norm = math.hypot(*query_embedding)
    if not math.isfinite(norm):
        raise ValueError("Frame query vector must contain finite values")
    params = {"query_vector": query_embedding, "query_norm": norm, "zero_vector": [0.0] * len(query_embedding)}
    if cache is not None:
        cache[key] = params
    return params


def _frame_lookup_body(*args, prepared=None, **kwargs):
    """Score matching frame vectors with their indexed values and true norms."""
    return _indexed_frame_lookup_body(*args, prepared=prepared, **kwargs)


def _indexed_frame_lookup_body(
    sensor_id: str,
    object_id: str,
    start_time: str,
    end_time: str | None,
    query_embedding: list[float],
    *,
    prepared: dict[str, Any] | None = None,
    require_embedding: bool = False,
    include_vector: bool = False,
    frame_pool_size: int = 1,
) -> dict[str, Any]:
    """Score matching nested object vectors exactly with ES's built-in cosine.

    Sensor/time filters select parent frames; the nested object-id filter keeps
    another object's vector from contributing to the frame score. Missing vectors
    receive zero score instead of promoting the candidate through the behavior-score
    fallback. Compute the actual vector norm:
    cached cosine magnitudes can be 1.0 for vectors that are only nearly unit length.
    """
    if isinstance(frame_pool_size, bool) or not isinstance(frame_pool_size, int) or not 1 <= frame_pool_size <= 32:
        raise ValueError("frame_pool_size must be an integer between 1 and 32")
    object_filters = [{"term": {"objects.id.keyword": object_id}}]
    if frame_pool_size > 1 or require_embedding:
        # Missing embeddings are not zero-valued observations in an average.
        object_filters.append({"exists": {"field": "objects.embedding.vector"}})
    return {
        "query": {
            "bool": {
                "filter": [
                    {"term": {"sensorId.keyword": sensor_id}},
                    {"range": {"timestamp": {"gte": start_time, "lte": end_time} if end_time else {"gte": start_time}}},
                ],
                "must": [
                    {
                        "nested": {
                            "path": "objects",
                            "score_mode": "max",
                            "query": {
                                "script_score": {
                                    "query": {
                                        "bool": {
                                            "filter": object_filters,
                                        }
                                    },
                                    "script": {
                                        "source": (
                                            "if (doc['objects.embedding.vector'].size() == 0 || params.query_norm == 0.0) return 0.0; "
                                            "double norm = l2norm(params.zero_vector, 'objects.embedding.vector'); "
                                            "if (norm == 0.0) return 0.0; "
                                            "double cosine = dotProduct(params.query_vector, 'objects.embedding.vector') / (params.query_norm * norm); "
                                            "return (Math.max(-1.0, Math.min(1.0, cosine)) + 1.0) / 2.0;"
                                        ),
                                        "params": prepared
                                        if prepared is not None
                                        else _prepare_frame_query(query_embedding),
                                    },
                                }
                            },
                        }
                    }
                ],
            }
        },
        "size": frame_pool_size,
        "_source": ["id", "timestamp", "sensorId", "objects.id", "objects.bbox"]
        + (["objects.embedding.vector"] if include_vector else []),
    }


def _parse_frame_lookup(response: Any, object_id: str, frame_pool_size: int = 1) -> FrameLookupResult:
    """Pool requested frame scores; retain the strongest frame as visual evidence."""
    hits = response.get("hits", {}).get("hits", [])
    if not hits:
        return None, None, None, None

    best_hit = hits[0]
    frame_source = best_hit["_source"]
    # The script already normalized cosine to (1 + cosine) / 2 in [0, 1];
    # average in that score space without transforming a second time.
    scores = [float(hit["_score"]) for hit in hits[:frame_pool_size]]
    if not all(math.isfinite(score) for score in scores):
        raise ValueError("Frame scores must be finite")
    best_score = math.fsum(scores) / len(scores)
    best_frame_id = frame_source.get("id")
    best_timestamp = frame_source.get("timestamp", "")

    best_bbox = None
    for obj in frame_source.get("objects", []):
        if obj.get("id") == object_id:
            bbox_data = obj.get("bbox", {})
            if bbox_data and bbox_data.get("leftX") is not None:
                best_bbox = {
                    "leftX": bbox_data.get("leftX", 0),
                    "rightX": bbox_data.get("rightX", 0),
                    "topY": bbox_data.get("topY", 0),
                    "bottomY": bbox_data.get("bottomY", 0),
                }
            break

    return best_frame_id, best_bbox, best_score, best_timestamp


async def _get_frame_from_behavior(
    frames_index: str | list[str],
    sensor_id: str,
    object_id: str,
    start_time: str,
    end_time: str | None,
    query_embedding: list[float],
    es: ElasticIndex,
    prepared: dict[str, Any] | None = None,
    frame_pool_size: int = 1,
) -> FrameLookupResult:
    """Best-effort per-object best-frame lookup via a Painless cosine score.

    Malformed frame documents remain best effort. Typed backend/index failures
    propagate so failed infrastructure cannot masquerade as a frame miss.

    Score space: the Painless script shifts raw cosine ``[-1, 1]`` into
    ``(1 + cosine) / 2`` (``[0, 1]``) BEFORE returning it as ``_score``. This is
    required because Elasticsearch ``script_score`` rejects negative scores, so a
    negative cosine would otherwise throw and silently lose the frame. Because the
    script already normalizes, Python reports ``_score`` verbatim (no second
    transform) — the returned ``frame_score`` is in ``(1 + cosine) / 2`` space,
    matching the behavior-index score (see :func:`build_behavior_knn_body`).
    """
    try:
        search_frames_index_str = frames_index if isinstance(frames_index, str) else ",".join(frames_index)
        response = await es.search(
            index=search_frames_index_str,
            body=_frame_lookup_body(
                sensor_id,
                object_id,
                start_time,
                end_time,
                query_embedding,
                prepared=prepared,
                **({"frame_pool_size": frame_pool_size} if frame_pool_size != 1 else {}),
            ),
        )
        return _parse_frame_lookup(response, object_id, frame_pool_size)
    except LibraryError:
        raise
    except Exception as e:
        logger.warning(f"Frame lookup failed for object={object_id}: {e}", exc_info=True)
        return None, None, None, None


FRAME_LOOKUP_CONCURRENCY = 16
FRAME_MSEARCH_BATCH_SIZE = 256


async def _perform_frame_lookups(
    candidates: list[dict[str, Any]],
    query_embedding: list[float],
    frames_index: str | list[str],
    timestamp_start: datetime | None,
    timestamp_end: datetime | None,
    es: ElasticIndex,
    frame_pool_size: int = 1,
) -> list[FrameLookupResult | None]:
    """Run frame lookups in parallel; document misses map to None, backend failures raise.

    The returned list is positionally aligned with ``candidates``: element ``i``
    is the frame result for ``candidates[i]`` (``None`` when that candidate had no
    id, missed, or raised), so the caller can zip results back to hits by index.
    """
    if not timestamp_start or not timestamp_end:
        logger.warning("Frame lookup requires timestamp_start and timestamp_end - skipping frame lookups")
        return [None] * len(candidates)

    start_time = timestamp_start.isoformat().replace("+00:00", "Z")
    end_time = timestamp_end.isoformat().replace("+00:00", "Z")

    # Identical (sensor, object, interval) lookups share one result. Keep a
    # bounded worker pool busy without waiting at fixed batch boundaries.
    keys: list[tuple[str, str] | None] = []
    for candidate in candidates:
        source = candidate.get("_source") or {}
        object_id = (source.get("object") or {}).get("id")
        sensor_id = (source.get("sensor") or {}).get("id")
        keys.append(
            (str(sensor_id), str(object_id)) if object_id not in (None, "") and sensor_id not in (None, "") else None
        )
    unique_keys = list(dict.fromkeys(key for key in keys if key is not None))
    prepared = _prepare_frame_query(query_embedding)
    by_key: dict[tuple[str, str], FrameLookupResult | None] = {}
    if isinstance(es, MultiSearchIndex):
        index = frames_index if isinstance(frames_index, str) else ",".join(frames_index)
        # One batch in flight; ES runs at most 16 unchanged sub-searches.
        for offset in range(0, len(unique_keys), FRAME_MSEARCH_BATCH_SIZE):
            batch = unique_keys[offset : offset + FRAME_MSEARCH_BATCH_SIZE]
            searches: list[dict[str, Any]] = []
            for sensor_id, object_id in batch:
                searches.extend(
                    [
                        {},
                        _frame_lookup_body(
                            sensor_id,
                            object_id,
                            start_time,
                            end_time,
                            query_embedding,
                            prepared=prepared,
                            **({"frame_pool_size": frame_pool_size} if frame_pool_size != 1 else {}),
                        ),
                    ]
                )
            try:
                response = await es.msearch(
                    index=index, searches=searches, max_concurrent_searches=FRAME_LOOKUP_CONCURRENCY
                )
                responses = response.get("responses", [])
                if len(responses) != len(batch):
                    raise ValueError("Frame multi-search response count does not match request count")
            except LibraryError:
                raise
            except Exception:
                logger.warning("Frame multi-search batch failed", exc_info=True)
                responses = [{} for _ in batch]
            for key, response in zip(batch, responses, strict=True):
                try:
                    if "error" in response:
                        status = response.get("status")
                        if isinstance(status, int) and (status in {401, 403, 404, 408, 429} or status >= 500):
                            raise BackendUnreachableError(
                                "elasticsearch", f"Frame multi-search backend failed with status {status}"
                            )
                        raise ValueError("Frame multi-search item failed")
                    by_key[key] = _parse_frame_lookup(response, key[1], frame_pool_size)
                except LibraryError:
                    raise
                except Exception:
                    logger.warning("Frame multi-search item failed for object=%s", key[1], exc_info=True)
                    by_key[key] = None
        return [by_key.get(key) if key is not None else None for key in keys]

    pending = iter(unique_keys)

    async def lookup_worker() -> None:
        # Iteration advances before awaiting, so each key belongs to one worker.
        for sensor_id, object_id in pending:
            try:
                outcome = await _get_frame_from_behavior(
                    frames_index=frames_index,
                    sensor_id=sensor_id,
                    object_id=object_id,
                    start_time=start_time,
                    end_time=end_time,
                    query_embedding=query_embedding,
                    es=es,
                    prepared=prepared,
                    **({"frame_pool_size": frame_pool_size} if frame_pool_size != 1 else {}),
                )
            except LibraryError:
                raise
            except Exception:
                outcome = None
            by_key[(sensor_id, object_id)] = outcome if isinstance(outcome, tuple) else None

    workers = [asyncio.create_task(lookup_worker()) for _ in range(min(FRAME_LOOKUP_CONCURRENCY, len(unique_keys)))]
    try:
        await asyncio.gather(*workers)
    except BaseException:
        # Cancellation/fatal errors must not leave backend requests running.
        for worker in workers:
            worker.cancel()
        await asyncio.gather(*workers, return_exceptions=True)
        raise
    return [by_key.get(key) if key is not None else None for key in keys]


async def _fetch_object_embedding(
    object_id: str,
    behavior_index: str | list[str],
    es: ElasticIndex,
) -> list[float]:
    """Fetch the latest behavior-index embedding vector for ``object_id``.

    Returns ``[]`` (no seed vector) when the pinned uploads anchor is absent --
    a live-only deployment has no uploaded objects to re-search -- so the caller
    yields ``[]`` instead of exiting 5, matching the attribute path.
    """
    search_index_str = behavior_index if isinstance(behavior_index, str) else ",".join(behavior_index)
    query = {
        "query": {"term": {"object.id.keyword": object_id}},
        "size": 1,
        "sort": [{"timestamp": {"order": "desc"}}],
        "_source": ["embeddings.vector"],
    }
    try:
        response = await es.search(index=search_index_str, body=query)
    except IndexNotFoundError as e:
        if _is_absent_uploads_anchor(behavior_index):
            logger.warning(
                f"Uploads anchor index '{behavior_index}' does not exist (no files ingested); "
                f"no object embedding to fetch."
            )
            return []
        logger.error(f"Elasticsearch index '{behavior_index}' not found: {e}")
        raise

    hits = response["hits"]["hits"]
    if not hits:
        raise ValueError(f"Object ID '{object_id}' not found in behavior index '{search_index_str}'")
    embeddings = hits[0]["_source"].get("embeddings", {})
    if isinstance(embeddings, list):
        embeddings = embeddings[0] if embeddings else {}
    vector = embeddings.get("vector", [])
    if not vector:
        raise ValueError(f"Object ID '{object_id}' has no embedding vector")
    return [float(v) for v in vector]


# =============================================================================
# Enrichment (VST screenshot URLs, clip widening)
# =============================================================================


def _normalise_screenshot_bound(timestamp: datetime | None) -> datetime | None:
    """Return one optional screenshot bound as a timezone-aware UTC datetime."""
    if timestamp is None:
        return None
    return iso8601_to_datetime(datetime_to_iso8601(timestamp))


def _screenshot_anchor(
    metadata: AttributeSearchMetadata,
    clamp_start: datetime | None,
    clamp_end: datetime | None,
) -> str | None:
    """Choose the best screenshot timestamp and constrain it to the returned clip."""
    timestamp = metadata.frame_timestamp or metadata.start_time
    timestamp_dt = safe_iso8601_to_datetime(timestamp)
    if timestamp_dt is None:
        return timestamp
    reported_start = safe_iso8601_to_datetime(metadata.start_time)
    reported_end = safe_iso8601_to_datetime(metadata.end_time)
    effective_start = max(
        (bound for bound in (reported_start, clamp_start) if bound is not None),
        default=None,
    )
    effective_end = min(
        (bound for bound in (reported_end, clamp_end) if bound is not None),
        default=None,
    )
    if effective_start is not None and timestamp_dt < effective_start:
        return datetime_to_iso8601(effective_start)
    if effective_end is not None and timestamp_dt > effective_end:
        return datetime_to_iso8601(effective_end)
    return timestamp


async def _enrich_attribute_result(
    result: AttributeSearchResult,
    resolution_base_url: str,
    screenshot_base_url: str,
    timelines: dict[str, tuple[str, str]],
    clamp_start: datetime | None,
    clamp_end: datetime | None,
) -> None:
    """Resolve one result's stream id and attach its bounded screenshot URL."""
    if not (result.metadata and result.metadata.sensor_id and not result.screenshot_url):
        return
    try:
        timestamp = _screenshot_anchor(result.metadata, clamp_start, clamp_end)
        stream_id = await get_stream_id(result.metadata.sensor_id, resolution_base_url)
        if stream_id:
            if timestamp:
                mapped_timestamp = _map_to_timeline(timestamp, stream_id, timelines)
                if mapped_timestamp is not None:
                    result.screenshot_url = build_screenshot_url(
                        screenshot_base_url,
                        stream_id,
                        mapped_timestamp,
                    )
            result.metadata.sensor_id = stream_id
    except Exception as e:
        logger.warning(f"Failed to enrich result for sensor {result.metadata.sensor_id}: {e}")


async def enrich_attribute_results(
    results: list[AttributeSearchResult],
    vst_internal_url: str | None,
    vst_external_url: str | None = None,
    timestamp_start: datetime | None = None,
    timestamp_end: datetime | None = None,
) -> None:
    """Resolve stream ids and build screenshot URLs in place (best-effort).

    ``timestamp_start`` / ``timestamp_end`` constrain only the screenshot
    anchor. Attribute metadata keeps the matched behavior's real interval.
    """
    resolution_base_url = vst_internal_url or vst_external_url
    screenshot_base_url = vst_external_url or vst_internal_url
    if not resolution_base_url or not screenshot_base_url:
        return

    clamp_start = _normalise_screenshot_bound(timestamp_start)
    clamp_end = _normalise_screenshot_bound(timestamp_end)
    needs_screenshots = any(r.metadata and r.metadata.sensor_id and not r.screenshot_url for r in results)
    timelines = await _get_timelines_best_effort(resolution_base_url) if needs_screenshots else {}
    await asyncio.gather(
        *(
            _enrich_attribute_result(
                result,
                resolution_base_url,
                screenshot_base_url,
                timelines,
                clamp_start,
                clamp_end,
            )
            for result in results
        )
    )


async def _get_timelines_best_effort(vst_base_url: str) -> dict[str, tuple[str, str]]:
    """Fetch all VST replay timelines for screenshot-timestamp mapping.

    File-ingested sources are indexed on a synthetic epoch while VST records
    at ingest wall-clock; screenshot URLs built from raw ES timestamps point
    outside the recording and VST returns 500 (see map_timestamp_to_timeline).
    Best-effort: enrichment proceeds unmapped when the fetch fails.
    """
    try:
        return await get_timelines_map(vst_base_url, timeout_seconds=5, retries=1)
    except VSTError as e:
        logger.warning(f"Could not fetch VST timelines; screenshot timestamps left unmapped: {e}")
        return {}


def _map_to_timeline(ts: str, stream_id: str, timelines: dict[str, tuple[str, str]]) -> str | None:
    """Map one ES timestamp onto the stream's VST timeline.

    Returns None when VST reported its replayable streams (non-empty map) and
    this stream is not among them — a stale ES document from a prior
    registration; a picture URL for it is a guaranteed VST error. Identity
    when the timelines could not be fetched at all (best-effort).
    """
    timeline = timelines.get(stream_id)
    if timeline:
        return map_timestamp_to_timeline(ts, timeline[0], timeline[1])
    if timelines:
        return None
    return ts


async def _extend_clip_to_one_second(
    result: AttributeSearchResult,
    vst_internal_url: str | None,
    vst_external_url: str,
) -> None:
    """Widen sub-second clips to ``MIN_CLIP_DURATION_SECONDS`` around the midpoint."""
    if not result.metadata or not result.metadata.start_time or not result.metadata.end_time:
        return
    if not result.metadata.sensor_id:
        return

    try:
        start_dt = iso8601_to_datetime(result.metadata.start_time)
        end_dt = iso8601_to_datetime(result.metadata.end_time)
        if (end_dt - start_dt).total_seconds() >= MIN_CLIP_DURATION_SECONDS:
            return

        vst_internal_for_resolution = vst_internal_url if vst_internal_url else vst_external_url
        stream_id = await get_stream_id(result.metadata.sensor_id, vst_internal_for_resolution)
        if not stream_id:
            logger.warning(f"Could not resolve stream_id for sensor_id={result.metadata.sensor_id}")
            return

        timeline_start_iso, timeline_end_iso = await get_timeline(stream_id, vst_internal_for_resolution)
        timeline_start = iso8601_to_datetime(timeline_start_iso)
        timeline_end = iso8601_to_datetime(timeline_end_iso)

        midpoint = start_dt + (end_dt - start_dt) / 2
        half_duration = MIN_CLIP_DURATION_SECONDS / 2.0
        new_start = max(midpoint - timedelta(seconds=half_duration), timeline_start)
        new_end = min(midpoint + timedelta(seconds=half_duration), timeline_end)

        if (new_end - new_start).total_seconds() < MIN_CLIP_DURATION_SECONDS:
            if new_end < timeline_end:
                new_end = min(new_start + timedelta(seconds=MIN_CLIP_DURATION_SECONDS), timeline_end)
            elif new_start > timeline_start:
                new_start = max(new_end - timedelta(seconds=MIN_CLIP_DURATION_SECONDS), timeline_start)

        result.metadata.start_time = datetime_to_iso8601(new_start)
        result.metadata.end_time = datetime_to_iso8601(new_end)
    except Exception as e:
        sensor = result.metadata.sensor_id if result.metadata else "unknown"
        logger.warning(f"Failed to extend clip for {sensor}: {e}.")


# =============================================================================
# Search pipelines
# =============================================================================


def _safe_hit_to_result(
    hit: dict[str, Any],
    frame_result: FrameLookupResult | None,
) -> AttributeSearchResult | None:
    """Map a hit to a result, skipping (and logging) any single unprocessable hit."""
    try:
        return hit_to_result(hit, frame_result)
    except Exception:
        hit_id = hit.get("_id", "unknown") if isinstance(hit, dict) else "unknown"
        logger.warning(f"Skipping unprocessable behavior hit {scrub_log(hit_id)}", exc_info=True)
        return None


async def search_by_attributes(
    query_embedding: list[float],
    index: str | list[str],
    es: ElasticIndex,
    timestamp_start: datetime | None = None,
    timestamp_end: datetime | None = None,
    video_sources: list[str] | None = None,
    top_k: int = 1,
    min_similarity: float = 0.7,
    frames_index: str | list[str] | None = None,
    enable_frame_lookup: bool = True,
    exclude_videos: list[dict[str, str]] | None = None,
    source_type: str = "video_file",
    candidate_limit: int | None = None,
    complete_pool: bool = False,
    representative_weight: float = 0.0,
    frame_pool_size: int = 1,
) -> list[AttributeSearchResult]:
    """Single-embedding attribute search: behavior kNN -> frames -> dedup.

    Returns an empty list only when no candidate passes the similarity
    threshold. Backend/index failures propagate as library errors.
    """
    if representative_weight and frame_pool_size != 1:
        raise InvalidInputError("Representative weighting and frame pooling cannot be combined")
    exclude_videos = exclude_videos or []

    with TimeMeasure("attribute_search: search behavior embeddings"):
        candidates = await _search_behavior(
            index=index,
            query_embedding=query_embedding,
            top_k=top_k,
            min_similarity=0.0 if representative_weight else min_similarity,
            es=es,
            timestamp_start=timestamp_start,
            timestamp_end=timestamp_end,
            video_sources=video_sources,
            source_type=source_type,
            candidate_limit=candidate_limit,
            include_embeddings=representative_weight > 0,
        )

    representative_candidates = candidates
    if representative_weight:
        # The appearance center uses every scoped behavior for a track, rather
        # than changing with query-dependent score filtering. Preserve the
        # caller's threshold when selecting retrieval candidates.
        candidates = [hit for hit in candidates if hit["_score"] >= min_similarity]
    if not candidates:
        logger.info(f"No candidates passed min_similarity threshold ({min_similarity})")
        return []

    results: list[AttributeSearchResult] = []
    # Keep the candidate that produced each result positionally aligned with
    # ``results``: a skipped (unprocessable) hit must not desync the indices dedup
    # relies on to widen the correct hit's time range.
    aligned_candidates: list[dict[str, Any]] = []
    if enable_frame_lookup and frames_index:
        with TimeMeasure("attribute_search: frame lookups"):
            if representative_weight:
                from ._frame_aggregation import aggregate_frame_lookups

                assert timestamp_start is not None and timestamp_end is not None
                frame_results = await aggregate_frame_lookups(
                    candidates,
                    query_embedding,
                    frames_index,
                    timestamp_start,
                    timestamp_end,
                    es,
                    representative_weight,
                    representative_candidates=representative_candidates,
                )
            else:
                frame_results = await _perform_frame_lookups(
                    candidates=candidates,
                    query_embedding=query_embedding,
                    frames_index=frames_index,
                    timestamp_start=timestamp_start,
                    timestamp_end=timestamp_end,
                    es=es,
                    **({"frame_pool_size": frame_pool_size} if frame_pool_size != 1 else {}),
                )
        for idx, hit in enumerate(candidates):
            frame_result = frame_results[idx] if idx < len(frame_results) else None
            item = _safe_hit_to_result(hit, frame_result)
            if item is not None:
                results.append(item)
                aligned_candidates.append(hit)
    else:
        for hit in candidates:
            item = _safe_hit_to_result(hit, None)
            if item is not None:
                results.append(item)
                aligned_candidates.append(hit)

    with TimeMeasure("attribute_search: deduplication"):
        results = deduplicate_by_object(results, aligned_candidates)

    # Stream-id resolution happens later at the enrichment layer, so only the raw
    # behavior ``sensor.id`` is available here; the helper still matches when the
    # exclude entry uses that raw id and normalizes timestamps either way.
    results = [
        r
        for r in results
        if r.metadata
        and not _is_attribute_excluded(
            sensor_id_raw=r.metadata.sensor_id,
            stream_id=None,
            start_time=r.metadata.start_time,
            end_time=r.metadata.end_time,
            exclude_videos=exclude_videos,
        )
    ]

    if not (complete_pool and candidate_limit is not None) and 0 < top_k < len(results):
        results = results[:top_k]
    return results


async def search_by_object_embedding(
    object_id: str,
    behavior_index: str | list[str],
    es: ElasticIndex,
    top_k: int = 5,
    min_similarity: float = 0.0,
    video_sources: list[str] | None = None,
    timestamp_start: datetime | None = None,
    timestamp_end: datetime | None = None,
    source_type: str = "video_file",
) -> list[AttributeSearchResult]:
    """Re-search by an existing object's embedding (fetch its vector, then kNN)."""
    embedding = await _fetch_object_embedding(object_id, behavior_index, es)
    # An absent uploads anchor yields an empty seed vector (see
    # :func:`_fetch_object_embedding`); there is nothing to re-search against, so
    # return no results rather than issuing a kNN query with an empty vector.
    if not embedding:
        return []
    results = await search_by_attributes(
        query_embedding=embedding,
        index=behavior_index,
        es=es,
        timestamp_start=timestamp_start,
        timestamp_end=timestamp_end,
        video_sources=video_sources,
        top_k=top_k,
        min_similarity=min_similarity,
        source_type=source_type,
    )
    return results[:top_k]


async def search_single_attribute(
    query_text: str,
    search_input: AttributeSearchInput,
    embed_client: TextEmbedder,
    index: str | list[str],
    frames_index: str | list[str] | None,
    es: ElasticIndex,
    enable_frame_lookup: bool = True,
    query_embedding: list[float] | None = None,
    complete_pool: bool = False,
    representative_weight: float = 0.0,
) -> list[AttributeSearchResult]:
    """Embed a single attribute string and run the attribute search pipeline.

    When ``query_embedding`` is supplied (e.g. precomputed once by a fusion caller
    over every attribute), the embed round-trip is skipped so the same attribute
    is not re-embedded on each per-hit fan-out (NVBug 6781021).
    """
    assert search_input.top_k is not None
    if query_embedding is None:
        with TimeMeasure("attribute_search: generate text embedding"):
            query_embedding = await embed_client.get_text_embedding(query_text)
    return await search_by_attributes(
        query_embedding=query_embedding,
        index=index,
        es=es,
        timestamp_start=search_input.timestamp_start,
        timestamp_end=search_input.timestamp_end,
        video_sources=search_input.video_sources,
        top_k=search_input.top_k,
        candidate_limit=search_input.candidate_limit,
        complete_pool=complete_pool,
        min_similarity=search_input.min_similarity,
        frames_index=frames_index,
        enable_frame_lookup=enable_frame_lookup,
        source_type=search_input.source_type,
        exclude_videos=search_input.exclude_videos,
        representative_weight=representative_weight,
        **(
            {"frame_pool_size": search_input.attribute_frame_pool_size}
            if search_input.attribute_frame_pool_size != 1
            else {}
        ),
    )


async def search_attributes(
    search_input: AttributeSearchInput,
    embed_client: TextEmbedder,
    index: str,
    vst_external_url: str,
    es: ElasticIndex,
    vst_internal_url: str | None = None,
    frames_index: str | None = None,
    enable_frame_lookup: bool = True,
    behavior_index_wildcard: str = "mdx-behavior-*",
    frames_index_wildcard: str = "mdx-raw-*",
    representative_weight: float = 0.0,
) -> list[AttributeSearchResult]:
    """Entry point: resolve indices by source_type, then fuse or append per attribute."""
    queries = search_input.normalized_queries()
    embeddings = search_input.normalized_query_embeddings()
    logger.info(f"Searching {len(queries)} attribute(s) (fuse_multi_attribute={search_input.fuse_multi_attribute})")

    source_type = search_input.source_type
    search_index: str | list[str] = resolve_index_by_source_type(index, source_type, behavior_index_wildcard)
    search_frames_index: str | list[str] | None
    if frames_index:
        search_frames_index = resolve_index_by_source_type(frames_index, source_type, frames_index_wildcard)
    elif source_type == "video_file":
        search_frames_index = None
    else:
        search_frames_index = frames_index_wildcard

    logger.info(f"Search index(es): {search_index} (source_type={source_type})")

    if search_input.fuse_multi_attribute:
        return await _fuse_multi_attribute(
            queries=queries,
            embeddings=embeddings,
            search_input=search_input,
            embed_client=embed_client,
            search_index=search_index,
            search_frames_index=search_frames_index,
            enable_frame_lookup=enable_frame_lookup,
            vst_external_url=vst_external_url,
            vst_internal_url=vst_internal_url,
            es=es,
            representative_weight=representative_weight,
        )
    return await _append_multi_attribute(
        queries=queries,
        embeddings=embeddings,
        search_input=search_input,
        embed_client=embed_client,
        search_index=search_index,
        search_frames_index=search_frames_index,
        enable_frame_lookup=enable_frame_lookup,
        vst_external_url=vst_external_url,
        vst_internal_url=vst_internal_url,
        es=es,
        representative_weight=representative_weight,
    )


async def _fuse_multi_attribute(
    queries: list[str],
    embeddings: list[list[float]] | None,
    search_input: AttributeSearchInput,
    embed_client: TextEmbedder,
    search_index: str | list[str],
    search_frames_index: str | list[str] | None,
    enable_frame_lookup: bool,
    vst_external_url: str,
    vst_internal_url: str | None,
    es: ElasticIndex,
    representative_weight: float = 0.0,
) -> list[AttributeSearchResult]:
    """Fuse mode: run each attribute (top_k=1), then resolve one screenshot per result.

    Each attribute runs independently; a per-attribute failure is isolated the
    same way append mode isolates one — systemic ``LibraryError`` (missing index,
    backend unreachable) re-raises, while a non-systemic failure only drops that
    attribute's contribution instead of sinking the whole fuse request.
    """
    single = search_input.model_copy(update={"top_k": 1, "fuse_multi_attribute": True})
    tasks = [
        search_single_attribute(
            query_text=q,
            search_input=single,
            embed_client=embed_client,
            index=search_index,
            frames_index=search_frames_index,
            es=es,
            enable_frame_lookup=enable_frame_lookup,
            query_embedding=embeddings[i] if embeddings is not None else None,
            representative_weight=representative_weight,
        )
        for i, q in enumerate(queries)
    ]
    results_list = await asyncio.gather(*tasks, return_exceptions=True)

    all_results: list[AttributeSearchResult] = []
    for query, outcome in zip(queries, results_list, strict=True):
        if isinstance(outcome, LibraryError):
            raise outcome
        if isinstance(outcome, BaseException):
            if not isinstance(outcome, Exception):
                raise outcome  # never swallow CancelledError / KeyboardInterrupt
            logger.warning(f"Attribute search failed for '{scrub_log(query)}': {outcome}", exc_info=outcome)
            continue
        all_results.extend(outcome)
    logger.info(f"Found {len(all_results)} result(s) from {len(queries)} attribute(s)")

    # Resolve stream ids / screenshots per result: a fused result set can span
    # several sensors, so relabeling every result with one sensor's stream id (and
    # sharing one screenshot) would misattribute matches on other sensors.
    if vst_external_url:
        await enrich_attribute_results(
            all_results,
            vst_internal_url,
            vst_external_url,
            timestamp_start=search_input.timestamp_start,
            timestamp_end=search_input.timestamp_end,
        )

    return all_results


async def _append_multi_attribute(
    queries: list[str],
    embeddings: list[list[float]] | None,
    search_input: AttributeSearchInput,
    embed_client: TextEmbedder,
    search_index: str | list[str],
    search_frames_index: str | list[str] | None,
    enable_frame_lookup: bool,
    vst_external_url: str,
    vst_internal_url: str | None,
    es: ElasticIndex,
    representative_weight: float = 0.0,
) -> list[AttributeSearchResult]:
    """Append mode: independent top_k per attribute, then dedup across all."""
    per_attr = search_input.model_copy(update={"fuse_multi_attribute": False})

    all_results: list[AttributeSearchResult] = []
    for idx, attr_query in enumerate(queries):
        try:
            attr_results = await search_single_attribute(
                query_text=attr_query,
                search_input=per_attr,
                embed_client=embed_client,
                index=search_index,
                frames_index=search_frames_index,
                es=es,
                enable_frame_lookup=enable_frame_lookup,
                query_embedding=embeddings[idx] if embeddings is not None else None,
                complete_pool=search_input.candidate_limit is not None,
                representative_weight=representative_weight,
            )

            all_results.extend(
                await _enrich_append_results(attr_results, vst_internal_url, vst_external_url, attr_query)
            )
            logger.info(f"Attribute '{scrub_log(attr_query)}': found {len(attr_results)} result(s)")
        except LibraryError:
            # Systemic failures (missing index, backend unreachable, invalid input)
            # affect every attribute equally — fail fast rather than retrying.
            raise
        except Exception as e:
            # An unexpected error for a single attribute should not sink the whole
            # multi-attribute request; skip it and continue with the rest.
            logger.warning(f"Attribute search failed for '{scrub_log(attr_query)}': {e}", exc_info=True)
            continue

    logger.info(f"Append mode: {len(all_results)} total result(s) from {len(queries)} attribute(s)")

    all_results = deduplicate_by_object(all_results)
    # Results are concatenated in query order; rank globally by behavior_score
    # before the top_k slice so a higher-scoring later-attribute match is not
    # truncated in favor of a lower-scoring earlier one. Tiebreak deterministically
    # on (sensor_id, object_id).
    all_results.sort(key=_append_rank_key)
    if search_input.candidate_limit is not None:
        # In exact mode select the final frame-ranked head from the full pool.
        all_results.sort(
            key=lambda result: (
                result.metadata.frame_score
                if result.metadata.frame_score is not None
                else result.metadata.behavior_score
            ),
            reverse=True,
        )
    if search_input.max_hits_per_source is not None:
        counts = {}
        selected = []
        for result in all_results:
            source = result.metadata.sensor_id
            if counts.get(source, 0) < search_input.max_hits_per_source:
                selected.append(result)
                counts[source] = counts.get(source, 0) + 1
        all_results = selected
    top_k = search_input.top_k
    assert top_k is not None
    if top_k > 0 and len(all_results) > top_k:
        all_results = all_results[:top_k]
    return all_results


async def _enrich_append_results(
    attr_results: list[AttributeSearchResult],
    vst_internal_url: str | None,
    vst_external_url: str,
    attr_query: str,
) -> list[AttributeSearchResult]:
    """Extend clips and attach screenshots before collecting append-mode results."""
    if attr_results and vst_internal_url:
        for result in attr_results:
            await _extend_clip_to_one_second(result, vst_internal_url, vst_external_url)
    if attr_results and vst_external_url:
        return await _attach_screenshots(attr_results, vst_internal_url, vst_external_url, attr_query)
    return attr_results


def _append_rank_key(result: AttributeSearchResult) -> tuple[float, str, str]:
    """Sort key for append-mode ranking: highest ``behavior_score`` first."""
    metadata = result.metadata
    return (-metadata.behavior_score, metadata.sensor_id, metadata.object_id or "")


async def _attach_screenshots(
    attr_results: list[AttributeSearchResult],
    vst_internal_url: str | None,
    vst_external_url: str,
    attr_query: str,
) -> list[AttributeSearchResult]:
    """Resolve stream ids and screenshot URLs for append-mode results (best-effort).

    Enrichment must NEVER drop a valid result: a result missing a
    ``frame_timestamp`` or whose VST resolution fails is still returned (without a
    screenshot), matching the parity of :func:`enrich_attribute_results` and the
    fuse path. Every input result is present in the returned list.
    """
    vst_internal_for_resolution = vst_internal_url if vst_internal_url else vst_external_url
    needs_screenshots = any(
        r.metadata and r.metadata.sensor_id and r.metadata.frame_timestamp and not r.screenshot_url
        for r in attr_results
    )
    timelines = await _get_timelines_best_effort(vst_internal_for_resolution) if needs_screenshots else {}

    for result in attr_results:
        if not (result.metadata and result.metadata.sensor_id):
            continue
        # Capture the raw sensor id as a display name before it is overwritten by
        # the resolved stream id below.
        result.metadata.video_name = result.metadata.sensor_id
        if not result.metadata.frame_timestamp:
            logger.warning(
                f"Result for sensor {scrub_log(result.metadata.sensor_id)} lacks a frame_timestamp; "
                f"returning it without a screenshot for attribute '{scrub_log(attr_query)}'"
            )
            continue
        try:
            stream_id = await get_stream_id(result.metadata.sensor_id, vst_internal_for_resolution)
            if stream_id:
                result.metadata.sensor_id = stream_id
                if not result.screenshot_url:
                    screenshot_ts = _map_to_timeline(result.metadata.frame_timestamp, stream_id, timelines)
                    if screenshot_ts is not None:
                        result.screenshot_url = build_screenshot_url(vst_external_url, stream_id, screenshot_ts)
        except Exception as e:
            logger.warning(
                f"Failed to generate screenshot for attribute '{scrub_log(attr_query)}' "
                f"(sensor {scrub_log(result.metadata.sensor_id)}); returning without screenshot: {e}"
            )
    return attr_results
