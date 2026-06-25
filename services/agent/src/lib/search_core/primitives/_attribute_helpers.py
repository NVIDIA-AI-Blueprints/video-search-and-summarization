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
"""Internal helpers for AttributeSearch.

Ported byte-for-byte from services/agent/src/vss_agents/tools/attribute_search.py
(lines 57-1452). Only edits made during the port:
  - NAT and `@register_function` machinery removed (`AttributeSearchConfig` and
    `build_attribute_search` stay in the NAT shim, not here).
  - Model class definitions removed; helpers use the library's models from
    ``..models.attribute_search``.
  - Upward imports rewritten to library-local paths: ``_internal.time_convert``,
    ``_internal.uuid_string``, ``_internal.time_measure``, ``clients.vst``.
  - Inline ``from vss_agents.tools.vst...`` imports lifted to top-level (now
    that they live inside the library).

Logic is preserved verbatim. Same ES query shapes, same filter logic, same
dedup/fusion semantics, same exception handling.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from datetime import timedelta
import logging
import re
from typing import TYPE_CHECKING
from typing import Any
from typing import Literal
from typing import cast

from elasticsearch import AsyncElasticsearch
from elasticsearch import NotFoundError as ESNotFoundError

from .._internal.time_convert import datetime_to_iso8601
from .._internal.time_convert import iso8601_to_datetime
from .._internal.time_measure import TimeMeasure
from .._internal.uuid_string import is_standard_uuid_string
from ..clients.vst import build_screenshot_url
from ..clients.vst import get_stream_id
from ..clients.vst import get_timeline
from ..models.attribute_search import AttributeSearchInput
from ..models.attribute_search import AttributeSearchMetadata
from ..models.attribute_search import AttributeSearchResult

if TYPE_CHECKING:
    from ..clients.embed_base import EmbedClient

logger = logging.getLogger(__name__)

# Mirrors tools/attribute_search.py:51
MIN_CLIP_DURATION_SECONDS = 1.0


# ----------------------------------------------------------- resolve_index_by_source_type


def resolve_index_by_source_type(
    base_index: str,
    source_type: Literal["video_file", "rtsp"],
    wildcard_pattern: str,
) -> str | list[str]:
    """Resolve ES index(es) by source_type. Mirrors L57-94.

    - ``video_file`` -> ``base_index`` unchanged.
    - ``rtsp``       -> ``[wildcard_pattern, "-" + base_index]``.
    """
    if source_type == "video_file":
        return base_index
    elif source_type == "rtsp":
        return [wildcard_pattern, "-" + base_index]
    else:
        raise ValueError(f"Unsupported source_type {source_type!r}; expected 'video_file' or 'rtsp'.")


# ----------------------------------------------------------- frame lookups


async def _perform_frame_lookups(
    candidates: list[dict[str, Any]],
    query_embedding: list[float],
    frames_index: str | list[str],
    timestamp_start: datetime | None,
    timestamp_end: datetime | None,
    es: AsyncElasticsearch,
) -> list[tuple[int | None, dict | None, float | None, str | None] | None]:
    """Per-candidate frame-level lookup; mirrors L270-334."""
    frame_lookup_tasks: list[Any] = []

    if not timestamp_start or not timestamp_end:
        logger.warning("Frame lookup requires timestamp_start and timestamp_end - skipping frame lookups")
        return [None] * len(candidates)

    start_time = timestamp_start.isoformat().replace("+00:00", "Z")
    end_time = timestamp_end.isoformat().replace("+00:00", "Z")

    for candidate in candidates:
        source = candidate["_source"]
        sensor = source.get("sensor", {})
        obj = source.get("object", {})
        object_id = obj.get("id", "")
        sensor_id = sensor.get("id", "")

        if object_id and sensor_id:
            task = _get_frame_from_behavior(
                frames_index=frames_index,
                sensor_id=sensor_id,
                object_id=object_id,
                start_time=start_time,
                end_time=end_time,
                query_embedding=query_embedding,
                es=es,
            )
            frame_lookup_tasks.append(task)
        else:
            frame_lookup_tasks.append(None)

    if not frame_lookup_tasks:
        return []

    tasks_to_run = [task if task is not None else asyncio.sleep(0) for task in frame_lookup_tasks]
    if any(task is not None for task in frame_lookup_tasks):
        logger.debug(f"Running {sum(1 for t in frame_lookup_tasks if t is not None)} frame lookups in parallel")
    frame_results = await asyncio.gather(*tasks_to_run, return_exceptions=True)

    filtered_results: list[tuple[int | None, dict | None, float | None, str | None] | None] = []
    for result in frame_results:
        if isinstance(result, Exception | BaseException):
            filtered_results.append(None)
        elif isinstance(result, tuple):
            filtered_results.append(result)
        else:
            filtered_results.append(None)
    return filtered_results


async def _get_frame_from_behavior(
    frames_index: str | list[str],
    sensor_id: str,
    object_id: str,
    start_time: str,
    end_time: str | None,
    query_embedding: list[float],
    es: AsyncElasticsearch,
) -> tuple[int | None, dict | None, float | None, str | None]:
    """Painless cosine-similarity lookup for the best frame. Mirrors L337-464."""
    try:
        logger.debug(
            f"Frame search: sensor={sensor_id}, object={object_id}, time=[{start_time} to {end_time or start_time}]"
        )
        search_frames_index_str = frames_index if isinstance(frames_index, str) else ",".join(frames_index)

        painless_script = (
            "double maxScore = -2.0; "
            "if (params._source.containsKey('objects')) { "
            "  for (int i = 0; i < params._source.objects.size(); i++) { "
            "    def obj = params._source.objects[i]; "
            "    if (obj.id == params.target_id && obj.containsKey('embedding') && obj.embedding.containsKey('vector')) { "
            "      def vec = obj.embedding.vector; "
            "      double dotProduct = 0.0; "
            "      double normA = 0.0; "
            "      double normB = 0.0; "
            "      for (int j = 0; j < Math.min(params.query_vector.size(), vec.size()); j++) { "
            "        dotProduct += params.query_vector[j] * vec[j]; "
            "        normA += params.query_vector[j] * params.query_vector[j]; "
            "        normB += vec[j] * vec[j]; "
            "      } "
            "      if (normA > 0 && normB > 0) { "
            "        double similarity = dotProduct / (Math.sqrt(normA) * Math.sqrt(normB)); "
            "        maxScore = Math.max(maxScore, similarity); "
            "      } "
            "      break; "
            "    } "
            "  } "
            "} "
            "return maxScore > -2.0 ? maxScore : 0.0;"
        )

        search_query = {
            "query": {
                "function_score": {
                    "query": {
                        "bool": {
                            "filter": [
                                {"term": {"sensorId.keyword": sensor_id}},
                                {
                                    "range": {
                                        "timestamp": (
                                            {"gte": start_time, "lte": end_time} if end_time else {"gte": start_time}
                                        )
                                    }
                                },
                            ],
                            "must": [
                                {
                                    "nested": {
                                        "path": "objects",
                                        "query": {"term": {"objects.id.keyword": object_id}},
                                    }
                                }
                            ],
                        }
                    },
                    "script_score": {
                        "script": {
                            "source": painless_script,
                            "params": {"query_vector": query_embedding, "target_id": object_id},
                        }
                    },
                    "boost_mode": "replace",
                }
            },
            "size": 1,
            "_source": ["id", "timestamp", "sensorId", "objects"],
        }

        response = await es.search(index=search_frames_index_str, body=search_query)
        hits = response.get("hits", {}).get("hits", [])

        if not hits:
            logger.warning(
                f"No frame hits for object={object_id} on sensor={sensor_id} in [{start_time} to {end_time or start_time}]"
            )
            return None, None, None, None

        best_hit = hits[0]
        frame_source = best_hit["_source"]
        raw_score = best_hit["_score"]
        best_score = (raw_score + 1.0) / 2.0 if raw_score > 0.0 else 0.0
        best_frame_id = frame_source.get("id")
        best_timestamp = frame_source.get("timestamp", "")

        logger.debug(
            f"Frame found: id={best_frame_id}, raw_score={raw_score:.4f}, normalized={best_score:.4f}, ts={best_timestamp}"
        )

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

    except Exception as e:
        logger.warning(f"Failed to find frame for object={object_id}: {e}", exc_info=True)
        return None, None, None, None


# ----------------------------------------------------------- object embedding re-search


async def _fetch_object_embedding(
    object_id: str,
    behavior_index: str | list[str],
    es: AsyncElasticsearch,
) -> list[float]:
    """Fetch a behavior-index embedding for object_id. Mirrors L467-505."""
    search_index_str = behavior_index if isinstance(behavior_index, str) else ",".join(behavior_index)
    query = {
        "query": {"term": {"object.id.keyword": object_id}},
        "size": 1,
        "sort": [{"timestamp": {"order": "desc"}}],
        "_source": ["embeddings.vector"],
    }
    response = await es.search(index=search_index_str, body=query)
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


async def search_by_object_embedding(
    object_id: str,
    behavior_index: str | list[str],
    es: AsyncElasticsearch,
    top_k: int = 5,
    min_similarity: float = 0.0,
    video_sources: list[str] | None = None,
    timestamp_start: datetime | None = None,
    timestamp_end: datetime | None = None,
    source_type: str = "video_file",
) -> list[AttributeSearchResult]:
    """Object re-search: fetch the object's embedding, then KNN. Mirrors L508-550."""
    embedding = await _fetch_object_embedding(object_id, behavior_index, es)
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


# ----------------------------------------------------------- result enrichment


async def enrich_attribute_results(
    results: list[AttributeSearchResult],
    vst_internal_url: str | None,
    vst_external_url: str | None = None,
) -> None:
    """Resolve stream_ids and build screenshot URLs in-place. Mirrors L553-582."""
    resolution_base_url = vst_internal_url or vst_external_url
    screenshot_base_url = vst_external_url or vst_internal_url
    if not resolution_base_url or not screenshot_base_url:
        return

    async def _enrich_result(r: AttributeSearchResult) -> None:
        if r.metadata and r.metadata.sensor_id and not r.screenshot_url:
            try:
                ts = r.metadata.start_time or r.metadata.frame_timestamp
                stream_id = await get_stream_id(r.metadata.sensor_id, resolution_base_url)
                if stream_id:
                    if ts:
                        r.screenshot_url = build_screenshot_url(screenshot_base_url, stream_id, ts)
                    r.metadata.sensor_id = stream_id
            except Exception as e:
                logger.warning(f"Failed to enrich result for sensor {r.metadata.sensor_id}: {e}")

    await asyncio.gather(*(_enrich_result(r) for r in results))


# ----------------------------------------------------------- behavior search


async def _search_behavior(
    index: str | list[str],
    query_embedding: list[float],
    top_k: int,
    min_similarity: float,
    es: AsyncElasticsearch,
    timestamp_start: datetime | None = None,
    timestamp_end: datetime | None = None,
    video_sources: list[str] | None = None,
    source_type: str = "video_file",
) -> list[dict[str, Any]]:
    """KNN search the behavior embeddings. Mirrors L585-736."""
    filter_clauses: list[dict[str, Any]] = []
    if timestamp_start or timestamp_end:
        overlap_filter: dict[str, Any] = {"bool": {"must": []}}
        if timestamp_start:
            overlap_filter["bool"]["must"].append({"range": {"end": {"gte": timestamp_start.isoformat()}}})
        if timestamp_end:
            overlap_filter["bool"]["must"].append({"range": {"timestamp": {"lte": timestamp_end.isoformat()}}})
        if overlap_filter["bool"]["must"]:
            filter_clauses.append(overlap_filter)

    if video_sources:
        if source_type == "rtsp":
            uuid_sources: list[str] = []
            non_uuid_sources = list(video_sources)
        else:
            uuid_sources = [v for v in video_sources if is_standard_uuid_string(v)]
            non_uuid_sources = [v for v in video_sources if not is_standard_uuid_string(v)]

        if uuid_sources and not non_uuid_sources:
            filter_clauses.append({"terms": {"sensor.id.keyword": uuid_sources}})
        else:
            should_clauses: list[dict[str, Any]] = []
            if uuid_sources:
                should_clauses.append({"terms": {"sensor.id.keyword": uuid_sources}})
            for vname in non_uuid_sources:
                escaped_vname = vname.replace("\\", "\\\\").replace("*", "\\*").replace("?", "\\?")
                regex_escaped = re.escape(vname)
                should_clauses.extend(
                    [
                        {"term": {"sensor.id.keyword": vname}},
                        {"wildcard": {"sensor.id.keyword": f"*{escaped_vname}*"}},
                        {"wildcard": {"sensor.info.url.keyword": f"*{escaped_vname}"}},
                        {"wildcard": {"sensor.info.url.keyword": f"*{escaped_vname}*"}},
                        {"wildcard": {"sensor.info.path.keyword": f"*{escaped_vname}*"}},
                        {"regexp": {"sensor.info.url": f".*{regex_escaped}"}},
                        {"regexp": {"sensor.info.path": f".*{regex_escaped}"}},
                    ]
                )
            filter_clauses.append({"bool": {"should": should_clauses, "minimum_should_match": 1}})

    if top_k == 1:
        fetch_k = 10
    else:
        fetch_k = max(top_k * 10, 200)

    knn_query: dict[str, Any] = {
        "field": "embeddings.vector",
        "query_vector": query_embedding,
        "k": fetch_k,
        "num_candidates": max(fetch_k * 2, 100),
    }
    if filter_clauses:
        if len(filter_clauses) > 1:
            knn_query["filter"] = {"bool": {"must": filter_clauses}}
        else:
            knn_query["filter"] = filter_clauses[0]

    logger.debug(f"Query embedding: dim={len(query_embedding)}")
    logger.debug(
        f"KNN search: top_k={top_k}, fetch_k={fetch_k}, k={knn_query['k']}, "
        f"num_candidates={knn_query['num_candidates']}, filters={len(filter_clauses)}"
    )

    search_query: dict[str, Any] = {
        "knn": knn_query,
        "size": fetch_k,
        "min_score": min_similarity,
        "_source": [
            "object.id",
            "object.type",
            "object.bbox",
            "sensor.id",
            "sensor.stream_id",
            "timestamp",
            "end",
        ],
    }
    search_index_str = index if isinstance(index, str) else ",".join(index)
    logger.debug(f"Searching index: {search_index_str}")

    try:
        response = await es.search(index=search_index_str, body=search_query)
    except ESNotFoundError as e:
        logger.error(f"Elasticsearch index '{search_index_str}' not found: {e}")
        raise ValueError(
            f"Search index '{search_index_str}' does not exist. "
            "Please ensure videos have been ingested before searching."
        ) from e

    total_hits = response["hits"]["total"]["value"]
    raw_hits = len(response["hits"]["hits"])
    logger.info(f"Found {raw_hits} candidates (total: {total_hits})")
    if raw_hits > 1:
        top_score = response["hits"]["hits"][0]["_score"]
        bottom_score = response["hits"]["hits"][-1]["_score"]
        logger.debug(f"Score range: {top_score:.4f} (best) to {bottom_score:.4f} (worst)")

    return list(response["hits"]["hits"])


# ----------------------------------------------------------- result building / dedup


async def _build_result(
    hit: dict[str, Any],
    frame_result: Any,
    input_timestamp_start: datetime | None = None,
    input_timestamp_end: datetime | None = None,
) -> AttributeSearchResult:
    """Mirrors L739-859."""
    score = hit["_score"]
    source = hit["_source"]
    obj = source.get("object", {})
    sensor = source.get("sensor", {})
    object_id = obj.get("id", "unknown")
    sensor_id = sensor.get("id", "unknown")

    logger.debug(f"Processing: sensor={sensor_id}, object={object_id}, score={score:.4f}")

    frame_bbox = None
    query_to_frame_score = None
    best_frame_timestamp = None

    if frame_result is not None and not isinstance(frame_result, Exception):
        _, frame_bbox, query_to_frame_score, best_frame_timestamp = frame_result
        if best_frame_timestamp:
            logger.debug(f"Frame score={query_to_frame_score:.4f}")
    elif isinstance(frame_result, Exception):
        logger.debug(f"Frame lookup failed for object {object_id}: {frame_result}")

    if frame_bbox is not None:
        final_bbox = frame_bbox
    else:
        behavior_bbox = obj.get("bbox", {})
        final_bbox = (
            {
                "leftX": behavior_bbox.get("leftX"),
                "rightX": behavior_bbox.get("rightX"),
                "topY": behavior_bbox.get("topY"),
                "bottomY": behavior_bbox.get("bottomY"),
            }
            if behavior_bbox
            else None
        )

    behavior_end_raw = source.get("end", "")
    behavior_start_raw = source.get("timestamp", "")
    behavior_end = cast("str | None", behavior_end_raw if behavior_end_raw else None)
    behavior_start = cast("str | None", behavior_start_raw if behavior_start_raw else None)

    if best_frame_timestamp:
        final_timestamp = best_frame_timestamp
    else:
        if behavior_start and behavior_end:
            start_dt = datetime.fromisoformat(behavior_start.replace("Z", "+00:00"))
            end_dt = datetime.fromisoformat(behavior_end.replace("Z", "+00:00"))
            midpoint_dt = start_dt + (end_dt - start_dt) / 2
            final_timestamp = midpoint_dt.isoformat().replace("+00:00", "Z")
        else:
            final_timestamp = behavior_end if behavior_end else behavior_start

    if query_to_frame_score is not None:
        logger.debug(f"Object {object_id}: behavior_score={score:.4f}, frame_score={query_to_frame_score:.4f}")
    else:
        logger.debug(f"Object {object_id}: behavior_score={score:.4f} (no frame score)")

    output_start_time: str | None
    output_end_time: str | None
    if input_timestamp_start is not None:
        output_start_time = datetime_to_iso8601(input_timestamp_start)
        output_end_time = (
            datetime_to_iso8601(input_timestamp_end) if input_timestamp_end is not None else output_start_time
        )
    else:
        output_start_time = behavior_start if behavior_start else None
        output_end_time = behavior_end if behavior_end else (behavior_start if behavior_start else None)

    metadata = AttributeSearchMetadata(
        sensor_id=sensor_id,
        object_id=object_id,
        object_type=obj.get("type", "unknown"),
        frame_timestamp=final_timestamp,
        start_time=output_start_time,
        end_time=output_end_time,
        bbox=final_bbox,
        behavior_score=score,
        frame_score=query_to_frame_score,
        video_name=None,
    )

    return AttributeSearchResult(screenshot_url=None, metadata=metadata)


async def _extend_clip_to_one_second(
    result: AttributeSearchResult,
    vst_internal_url: str | None,
    vst_external_url: str,
) -> None:
    """Extend short clips to MIN_CLIP_DURATION_SECONDS. Mirrors L862-941."""
    if not result.metadata or not result.metadata.start_time or not result.metadata.end_time:
        return
    if not result.metadata.sensor_id:
        return

    try:
        start_dt = iso8601_to_datetime(result.metadata.start_time)
        end_dt = iso8601_to_datetime(result.metadata.end_time)
        duration = (end_dt - start_dt).total_seconds()

        if duration >= MIN_CLIP_DURATION_SECONDS:
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
        new_start = midpoint - timedelta(seconds=half_duration)
        new_end = midpoint + timedelta(seconds=half_duration)
        new_start = max(new_start, timeline_start)
        new_end = min(new_end, timeline_end)

        if (new_end - new_start).total_seconds() < MIN_CLIP_DURATION_SECONDS:
            if new_end < timeline_end:
                new_end = min(new_start + timedelta(seconds=MIN_CLIP_DURATION_SECONDS), timeline_end)
            elif new_start > timeline_start:
                new_start = max(new_end - timedelta(seconds=MIN_CLIP_DURATION_SECONDS), timeline_start)

        result.metadata.start_time = datetime_to_iso8601(new_start)
        result.metadata.end_time = datetime_to_iso8601(new_end)
        logger.info(
            f"Extended clip < {MIN_CLIP_DURATION_SECONDS}s to {MIN_CLIP_DURATION_SECONDS}s: "
            f"{result.metadata.sensor_id} ({duration:.3f}s -> {(new_end - new_start).total_seconds():.3f}s)"
        )
    except Exception as e:
        logger.warning(f"Failed to extend clip for {result.metadata.sensor_id if result.metadata else 'unknown'}: {e}.")


def _deduplicate_by_object(
    results: list[AttributeSearchResult],
    candidates: list[dict[str, Any]] | None = None,
) -> list[AttributeSearchResult]:
    """Merge duplicate (sensor_id, object_id) results. Mirrors L944-1051."""
    merged: dict[tuple[str, str], tuple[AttributeSearchResult, int]] = {}
    duplicate_count = 0
    merge_count = 0

    for idx, result in enumerate(results):
        if not result.metadata:
            continue
        key = (result.metadata.sensor_id, result.metadata.object_id)

        if key not in merged:
            merged[key] = (result, idx)
        else:
            existing_result, existing_idx = merged[key]
            duplicate_count += 1

            if candidates and existing_idx < len(candidates) and idx < len(candidates):
                existing_source = candidates[existing_idx].get("_source", {})
                new_source = candidates[idx].get("_source", {})

                existing_start = existing_result.metadata.start_time or existing_source.get("timestamp")
                existing_end = existing_result.metadata.end_time or existing_source.get("end")
                new_start = new_source.get("timestamp")
                new_end = new_source.get("end")

                earliest_start = existing_start
                latest_end = existing_end

                if new_start and existing_start:
                    try:
                        if datetime.fromisoformat(new_start.replace("Z", "+00:00")) < datetime.fromisoformat(
                            existing_start.replace("Z", "+00:00")
                        ):
                            earliest_start = new_start
                    except (ValueError, AttributeError):
                        pass
                elif new_start:
                    earliest_start = new_start

                if new_end and existing_end:
                    try:
                        if datetime.fromisoformat(new_end.replace("Z", "+00:00")) > datetime.fromisoformat(
                            existing_end.replace("Z", "+00:00")
                        ):
                            latest_end = new_end
                    except (ValueError, AttributeError):
                        pass
                elif new_end:
                    latest_end = new_end

                if earliest_start != existing_start or latest_end != existing_end:
                    merge_count += 1
                    existing_result.metadata.start_time = earliest_start
                    existing_result.metadata.end_time = latest_end

    if duplicate_count > 0:
        logger.info(
            f"Deduplication: Found {duplicate_count} duplicate(s), merged {merge_count} time range(s). "
            f"Kept {len(merged)} unique result(s) from {len(results)} total result(s)."
        )

    return [result for result, _ in merged.values()]


# ----------------------------------------------------------- top-level search API


async def search_by_attributes(
    query_embedding: list[float],
    index: str | list[str],
    es: AsyncElasticsearch,
    timestamp_start: datetime | None = None,
    timestamp_end: datetime | None = None,
    video_sources: list[str] | None = None,
    top_k: int = 1,
    min_similarity: float = 0.7,
    frames_index: str | list[str] | None = None,
    enable_frame_lookup: bool = True,
    exclude_videos: list[dict[str, str]] | None = None,
    source_type: str = "video_file",
) -> list[AttributeSearchResult]:
    """Single-query search pipeline. Mirrors L1054-1158."""
    exclude_videos = exclude_videos or []
    try:
        with TimeMeasure("attribute_search: search behavior embeddings"):
            candidates = await _search_behavior(
                index=index,
                query_embedding=query_embedding,
                top_k=top_k,
                min_similarity=min_similarity,
                es=es,
                timestamp_start=timestamp_start,
                timestamp_end=timestamp_end,
                video_sources=video_sources,
                source_type=source_type,
            )

        if candidates:
            if len(candidates) > 1:
                scores = [c["_score"] for c in candidates]
                logger.info(
                    f"Processing {len(candidates)} candidate(s). Score range: {max(scores):.4f} to {min(scores):.4f}"
                )
            else:
                logger.info(f"Processing {len(candidates)} candidate(s).")
        else:
            logger.info(f"No candidates passed min_similarity threshold ({min_similarity})")

        results: list[AttributeSearchResult] = []
        if enable_frame_lookup and frames_index:
            with TimeMeasure("attribute_search: frame lookups"):
                frame_results = await _perform_frame_lookups(
                    candidates=candidates,
                    query_embedding=query_embedding,
                    frames_index=frames_index,
                    timestamp_start=timestamp_start,
                    timestamp_end=timestamp_end,
                    es=es,
                )
            for idx, hit in enumerate(candidates):
                frame_result = frame_results[idx] if idx < len(frame_results) else None
                results.append(await _build_result(hit=hit, frame_result=frame_result))
        else:
            if not enable_frame_lookup:
                logger.debug("Frame lookup disabled - using behavior-level embeddings only")
            for hit in candidates:
                results.append(await _build_result(hit=hit, frame_result=None))

        logger.info(f"Matched {len(results)} object-video pairs")

        with TimeMeasure("attribute_search: deduplication"):
            results = _deduplicate_by_object(results, candidates)
        logger.info(f"After deduplication: {len(results)} unique object-video pairs")

        exclude_set = {
            (ev.get("sensor_id", ""), ev.get("start_timestamp", ""), ev.get("end_timestamp", ""))
            for ev in exclude_videos
        }
        results = [
            r for r in results if (r.metadata.sensor_id, r.metadata.start_time, r.metadata.end_time) not in exclude_set
        ]

        if 0 < top_k < len(results):
            results = results[:top_k]
            logger.info(f"Returning top {top_k} results after deduplication")

        return results

    except Exception as e:
        logger.error(f"Attribute search failed: {e}", exc_info=True)
        return []


async def search_single_attribute(
    query_text: str,
    search_input: AttributeSearchInput,
    embed_client: EmbedClient,
    index: str | list[str],
    frames_index: str | list[str] | None,
    es: AsyncElasticsearch,
    enable_frame_lookup: bool = True,
) -> list[AttributeSearchResult]:
    """Mirrors L1161-1186."""
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
        min_similarity=search_input.min_similarity,
        frames_index=frames_index,
        enable_frame_lookup=enable_frame_lookup,
        source_type=search_input.source_type,
        exclude_videos=search_input.exclude_videos,
    )


async def search_attributes(
    search_input: AttributeSearchInput,
    embed_client: EmbedClient,
    index: str,
    vst_external_url: str,
    es: AsyncElasticsearch,
    vst_internal_url: str | None = None,
    frames_index: str | None = None,
    enable_frame_lookup: bool = True,
    behavior_index_wildcard: str = "mdx-behavior-*",
    frames_index_wildcard: str = "mdx-raw-*",
) -> list[AttributeSearchResult]:
    """Entry-point for attribute search. Mirrors L1189-1254.

    Additional kwargs ``behavior_index_wildcard`` / ``frames_index_wildcard``
    let the caller (AttributeSearch) pass values from SearchRuntime instead
    of relying on the hardcoded ``mdx-behavior-*`` / ``mdx-raw-*`` literals.
    """
    queries = [search_input.query] if isinstance(search_input.query, str) else search_input.query
    logger.info(f"Searching {len(queries)} attribute(s) (fuse_multi_attribute={search_input.fuse_multi_attribute})")

    source_type = search_input.source_type
    search_index: str | list[str]
    search_frames_index: str | list[str] | None
    if source_type == "video_file":
        search_index = index
        search_frames_index = frames_index
    else:
        search_index = [behavior_index_wildcard, "-" + index]
        if frames_index:
            search_frames_index = [frames_index_wildcard, "-" + frames_index]
        else:
            search_frames_index = frames_index_wildcard

    logger.info(f"Search index(es): {search_index} (source_type={source_type})")
    if search_frames_index:
        logger.info(f"Frames index(es): {search_frames_index} (source_type={source_type})")

    if search_input.fuse_multi_attribute:
        return await _fuse_multi_attribute(
            queries=queries,
            search_input=search_input,
            embed_client=embed_client,
            search_index=search_index,
            search_frames_index=search_frames_index,
            enable_frame_lookup=enable_frame_lookup,
            vst_external_url=vst_external_url,
            vst_internal_url=vst_internal_url,
            es=es,
        )
    else:
        return await _append_multi_attribute(
            queries=queries,
            search_input=search_input,
            embed_client=embed_client,
            search_index=search_index,
            search_frames_index=search_frames_index,
            enable_frame_lookup=enable_frame_lookup,
            vst_external_url=vst_external_url,
            vst_internal_url=vst_internal_url,
            es=es,
        )


async def _fuse_multi_attribute(
    queries: list[str],
    search_input: AttributeSearchInput,
    embed_client: EmbedClient,
    search_index: str | list[str],
    search_frames_index: str | list[str] | None,
    enable_frame_lookup: bool,
    vst_external_url: str,
    vst_internal_url: str | None,
    es: AsyncElasticsearch,
) -> list[AttributeSearchResult]:
    """Fuse mode: combine object IDs from all attributes for one screenshot. Mirrors L1257-1355."""
    search_input_single = AttributeSearchInput(
        query=search_input.query,
        source_type=search_input.source_type,
        timestamp_start=search_input.timestamp_start,
        timestamp_end=search_input.timestamp_end,
        video_sources=search_input.video_sources,
        top_k=1,
        min_similarity=search_input.min_similarity,
        fuse_multi_attribute=True,
        exclude_videos=search_input.exclude_videos,
    )

    tasks = [
        search_single_attribute(
            query_text=q,
            search_input=search_input_single,
            embed_client=embed_client,
            index=search_index,
            frames_index=search_frames_index,
            es=es,
            enable_frame_lookup=enable_frame_lookup,
        )
        for q in queries
    ]

    results_list = await asyncio.gather(*tasks)
    all_results = [result for results in results_list for result in results]
    logger.info(f"Found {len(all_results)} results from {len(queries)} attribute(s)")

    object_ids: list[int] = []
    sensor_id = None
    frame_timestamps: list[str] = []

    for result in all_results:
        if result.metadata:
            try:
                object_ids.append(int(result.metadata.object_id))
                if sensor_id is None:
                    sensor_id = result.metadata.sensor_id
                if result.metadata.frame_timestamp:
                    frame_timestamps.append(result.metadata.frame_timestamp)
            except (ValueError, TypeError):
                pass

    if sensor_id and vst_external_url and search_input.timestamp_start and search_input.timestamp_end:
        try:
            start_time = search_input.timestamp_start.isoformat().replace("+00:00", "Z")
            vst_internal_for_resolution = vst_internal_url if vst_internal_url else vst_external_url
            stream_id = await get_stream_id(sensor_id, vst_internal_for_resolution)

            screenshot_url = None
            if stream_id:
                screenshot_timestamp = start_time
                if frame_timestamps:
                    sorted_timestamps = sorted(frame_timestamps)
                    mid_idx = len(sorted_timestamps) // 2
                    screenshot_timestamp = sorted_timestamps[mid_idx]
                screenshot_url = build_screenshot_url(vst_external_url, stream_id, screenshot_timestamp)

            if stream_id:
                for result in all_results:
                    if screenshot_url and not result.screenshot_url:
                        result.screenshot_url = screenshot_url
                    if result.metadata:
                        result.metadata.sensor_id = stream_id

            logger.info(f"Generated screenshot for {len(object_ids)} objects at stream {stream_id}")
        except Exception as e:
            logger.warning(f"Failed to generate screenshot: {e}", exc_info=True)

    return all_results


async def _append_multi_attribute(
    queries: list[str],
    search_input: AttributeSearchInput,
    embed_client: EmbedClient,
    search_index: str | list[str],
    search_frames_index: str | list[str] | None,
    enable_frame_lookup: bool,
    vst_external_url: str,
    vst_internal_url: str | None,
    es: AsyncElasticsearch,
) -> list[AttributeSearchResult]:
    """Append mode: top_k per attribute independently. Mirrors L1358-1452."""
    search_input_per_attr = AttributeSearchInput(
        query=search_input.query,
        source_type=search_input.source_type,
        timestamp_start=search_input.timestamp_start,
        timestamp_end=search_input.timestamp_end,
        video_sources=search_input.video_sources,
        top_k=search_input.top_k,
        min_similarity=search_input.min_similarity,
        fuse_multi_attribute=False,
        exclude_videos=search_input.exclude_videos,
    )

    all_results: list[AttributeSearchResult] = []
    for attr_query in queries:
        try:
            attr_results = await search_single_attribute(
                query_text=attr_query,
                search_input=search_input_per_attr,
                embed_client=embed_client,
                index=search_index,
                frames_index=search_frames_index,
                es=es,
                enable_frame_lookup=enable_frame_lookup,
            )

            if attr_results and vst_internal_url:
                for result in attr_results:
                    await _extend_clip_to_one_second(result, vst_internal_url, vst_external_url)

            valid_results: list[AttributeSearchResult] = []
            if attr_results and vst_external_url:
                for result in attr_results:
                    if result.metadata and result.metadata.sensor_id and result.metadata.frame_timestamp:
                        try:
                            result.metadata.video_name = result.metadata.sensor_id
                            vst_internal_for_resolution = vst_internal_url if vst_internal_url else vst_external_url
                            stream_id = await get_stream_id(result.metadata.sensor_id, vst_internal_for_resolution)
                            if stream_id:
                                result.metadata.sensor_id = stream_id
                            if stream_id and not result.screenshot_url:
                                result.screenshot_url = build_screenshot_url(
                                    vst_external_url, stream_id, result.metadata.frame_timestamp
                                )
                            valid_results.append(result)
                        except Exception as e:
                            logger.debug(f"Failed to generate screenshot for attribute '{attr_query}': {e}")
                            continue
            else:
                valid_results = attr_results

            all_results.extend(valid_results)
            logger.info(f"Attribute '{attr_query}': found {len(attr_results)} results")
        except Exception as e:
            logger.warning(f"Attribute search failed for '{attr_query}': {e}")
            continue

    logger.info(f"Append mode: found {len(all_results)} total results from {len(queries)} attribute(s)")

    all_results = _deduplicate_by_object(all_results)
    logger.info(f"After deduplication: {len(all_results)} unique object-video pairs")

    top_k = search_input.top_k
    if top_k > 0 and len(all_results) > top_k:
        all_results = all_results[:top_k]
        logger.info(f"Returning top {top_k} results after deduplication")

    return all_results
