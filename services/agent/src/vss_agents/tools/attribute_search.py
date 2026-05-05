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

import asyncio
from collections.abc import AsyncGenerator
from datetime import datetime
from datetime import timedelta
import logging
import re
from typing import Any
from typing import cast

from elasticsearch import AsyncElasticsearch
from elasticsearch import NotFoundError as ESNotFoundError
from nat.builder.builder import Builder
from nat.builder.function_info import FunctionInfo
from nat.cli.register_workflow import register_function

# from nat.data_models.component_ref import FunctionRef  # type: ignore[import-untyped]  # NOTE: Unused - video_clip_tool removed
from nat.data_models.function import FunctionBaseConfig
from pydantic import BaseModel
from pydantic import Field

from vss_agents.data_models.ranking import DEFAULT_CHUNK_SECONDS
from vss_agents.data_models.ranking import ChunkKey
from vss_agents.data_models.ranking import RankedChunk
from vss_agents.data_models.ranking import RankedList
from vss_agents.data_models.search import DecomposedQuery
from vss_agents.data_models.search import SearchInput
from vss_agents.embed.embed import EmbedClient
from vss_agents.embed.rtvi_cv_embed import RTVICVEmbedClient
from vss_agents.tools.vst.snapshot import build_screenshot_url
from vss_agents.utils.es_client import VSSESClient
from vss_agents.utils.time_measure import TimeMeasure
from vss_agents.utils.uuid_string import is_standard_uuid_string

logger = logging.getLogger(__name__)

# Base timestamps for video offset conversion (same as embed_search)
BASE_2025 = datetime(2025, 1, 1, tzinfo=datetime.now().astimezone().tzinfo)

# Minimum clip duration in seconds (for attribute-only search results)
# Clips shorter than this will be extended to this duration
MIN_CLIP_DURATION_SECONDS = 1.0

# Default behavior index name — shared across SearchConfig, SearchAgentConfig, AttributeSearchConfig
DEFAULT_BEHAVIOR_INDEX = "mdx-behavior-2025-01-01"


class AttributeSearchInput(BaseModel):
    """Input for attribute-based search"""

    query: str | list[str] = Field(
        ...,
        description="Attribute query or list of queries (e.g., 'person with red hat' or ['person', 'red hat'])",
    )

    source_type: str = Field(
        default="video_file",
        description="Type of video source: 'video_file' for uploaded videos, 'rtsp' for live/camera streams.",
    )

    timestamp_start: datetime | None = Field(
        default=None,
        description="Start time filter",
    )

    timestamp_end: datetime | None = Field(
        default=None,
        description="End time filter",
    )

    video_sources: list[str] | None = Field(
        default=None,
        description="Filter by video source names (supports wildcard matching). Can be used for both video source names and sensor IDs.",
    )

    top_k: int = Field(
        default=1,
        description="Number of results to return",
    )

    min_similarity: float = Field(
        default=0.3,
        description="Minimum cosine similarity threshold",
    )

    fuse_multi_attribute: bool = Field(
        default=True,
        description="If True, fuse multiple attributes (combine object IDs for single screenshot). If False, append top_k results per attribute independently (no fusion).",
    )

    exclude_videos: list[dict[str, str]] = Field(
        default_factory=list, description="List of videos to exclude from results"
    )


class AttributeSearchMetadata(BaseModel):
    """Metadata for attribute search result"""

    sensor_id: str = Field(..., description="Sensor/camera ID")
    object_id: str = Field(..., description="Object ID")
    object_type: str = Field(..., description="Object type")
    frame_timestamp: str = Field(..., description="Best frame timestamp")
    start_time: str | None = Field(None, description="Start time of the time range (earliest from duplicates)")
    end_time: str | None = Field(None, description="End time of the time range (latest from duplicates)")
    bbox: dict[str, Any] | None = Field(None, description="Bounding box dimensions")
    behavior_score: float = Field(..., description="Behavior-level similarity score")
    frame_score: float | None = Field(None, description="Frame-level similarity score")
    video_name: str | None = Field(None, description="Video name (sensor name for RTSP, filename for video_file)")


class AttributeSearchResult(BaseModel):
    """Single attribute search result with URLs and metadata"""

    screenshot_url: str | None = Field(None, description="Screenshot URL")
    metadata: AttributeSearchMetadata = Field(..., description="Search result metadata")


class AttributeSearchOutput(BaseModel):
    """Output of attribute search with adapter wrappers."""

    results: list[AttributeSearchResult] = Field(default_factory=list)

    @classmethod
    def from_raw(cls, raw: Any) -> "AttributeSearchOutput":
        """Coerce NAT's loose return shape into a typed wrapper.

        ``ainvoke`` may surface a ``list``, a wrapped instance, ``None``, or a
        dict-shaped representation. Normalize all of them here so the dispatcher
        in ``search.py`` can stay branch-free downstream.
        """
        if raw is None:
            return cls(results=[])
        if isinstance(raw, cls):
            return raw
        if isinstance(raw, list):
            results = [
                item if isinstance(item, AttributeSearchResult) else AttributeSearchResult.model_validate(item)
                for item in raw
            ]
            return cls(results=results)
        return cls.model_validate(raw)

    def to_ranked_list(self, *, chunk_seconds: int = DEFAULT_CHUNK_SECONDS) -> RankedList:
        """Bucketize attribute hits onto the chunk grid.

        Per-result score follows legacy ``fusion_search_rerank`` semantics:
        prefer ``frame_score`` when > 0, else fall back to ``behavior_score``.
        Multiple raw hits inside the same window collapse to one
        :class:`RankedChunk` (max-score wins).
        """
        bucketed: dict[ChunkKey, float] = {}
        for r in self.results:
            m = r.metadata
            # Prefer explicit start; fall back to the frame timestamp.
            start_iso = m.start_time or m.frame_timestamp
            try:
                key = ChunkKey.from_iso(m.sensor_id, start_iso, chunk_seconds)
            except Exception as e:
                logger.warning(f"Skipping attribute result with unparseable timestamps: {e}")
                continue
            score = (
                float(m.frame_score) if (m.frame_score is not None and m.frame_score > 0.0) else float(m.behavior_score)
            )
            existing = bucketed.get(key)
            if existing is None or score > existing:
                bucketed[key] = score

        sorted_keys = sorted(bucketed.keys(), key=lambda k: -bucketed[k])
        chunks = [RankedChunk(key=k, score=bucketed[k], rank=i + 1) for i, k in enumerate(sorted_keys)]
        return RankedList(space="attribute", chunks=chunks)

    def to_payload_index(
        self,
        *,
        chunk_seconds: int = DEFAULT_CHUNK_SECONDS,
    ) -> dict[ChunkKey, dict[str, Any]]:
        """Per-:class:`ChunkKey` payload sidecar.

        Object IDs are propagated so they survive into ``SearchResult.object_ids``.
        When multiple object hits land in the same chunk window, the highest-scoring
        hit's payload is kept and the additional ``object_id``s are merged onto it.
        """
        seen_score: dict[ChunkKey, float] = {}
        payloads: dict[ChunkKey, dict[str, Any]] = {}
        for r in self.results:
            m = r.metadata
            start_iso = m.start_time or m.frame_timestamp
            try:
                key = ChunkKey.from_iso(m.sensor_id, start_iso, chunk_seconds)
            except Exception:
                continue
            score = (
                float(m.frame_score) if (m.frame_score is not None and m.frame_score > 0.0) else float(m.behavior_score)
            )
            existing_score = seen_score.get(key)
            if existing_score is None or score > existing_score:
                seen_score[key] = score
                payloads[key] = {
                    "video_name": m.video_name or "",
                    "description": "",
                    "screenshot_url": r.screenshot_url or "",
                    "object_ids": [str(m.object_id)] if m.object_id else [],
                    "_source_space": "attribute",
                }
            elif m.object_id:
                # Same chunk hit by multiple object_ids - merge the IDs onto the existing payload.
                ids = payloads[key].setdefault("object_ids", [])
                if str(m.object_id) not in ids:
                    ids.append(str(m.object_id))
        return payloads


def build_attribute_request(
    decomposed: DecomposedQuery | None,
    search_input: SearchInput,
    top_k: int,
) -> "AttributeSearchInput | None":
    """Build the per-call :class:`AttributeSearchInput`, or return None to skip the space.

    Returns None when there are no attributes to search on - that's the
    "fusion path skipped when LLM returns no attributes" gate.
    """
    attrs = list(decomposed.attributes) if (decomposed and decomposed.attributes) else []
    if not attrs:
        return None
    return AttributeSearchInput(
        query=attrs,
        source_type=search_input.source_type,
        top_k=top_k,
        fuse_multi_attribute=True,
    )


class AttributeSearchConfig(FunctionBaseConfig, name="attribute_search"):
    """Configuration for attribute search function"""

    rtvi_cv_endpoint: str = Field(
        ...,
        description="RTVI CV endpoint URL (e.g., http://localhost:9000)",
    )

    es_endpoint: str = Field(
        ...,
        description="Elasticsearch endpoint URL",
    )

    behavior_index: str = Field(
        default=DEFAULT_BEHAVIOR_INDEX,
        description="Elasticsearch index with object embeddings",
    )

    frames_index: str | None = Field(
        default=None,
        description="Elasticsearch frames index for exact frame ID lookup (e.g., mdx-raw-2026-01-09)",
    )

    enable_frame_lookup: bool = Field(
        default=True,
        description="Whether to perform frame-level lookup for more accurate bbox and frame_score. If False, only uses behavior-level embeddings.",
    )

    vst_external_url: str = Field(
        ...,
        description="The external VST URL for client-facing URLs.",
    )
    vst_internal_url: str | None = Field(
        default=None,
        description="The internal VST URL for validation requests. If not provided, uses vst_external_url.",
    )

    # video_clip_tool: FunctionRef | None = Field(
    #     default=None,
    #     description="Optional reference to vst_video_clip tool for generating video URLs with overlays",
    # )
    # NOTE: video_clip_tool removed - UI calls VST API directly for video overlays


# NOTE: _generate_video_url removed - UI calls VST API directly for video overlays
# async def _generate_video_url(
#     video_clip_fn: Any,
#     sensor: dict[str, Any],
#     object_ids: list[int],
#     start_time: str,
#     end_time: str | None,
#     vst_internal_url: str,
# ) -> tuple[str | None, str | None]:
#     """Generate video URL with object overlays. Returns (video_url, stream_id)."""
#     try:
#         from vss_agents.tools.vst.timeline import get_timeline
#         from vss_agents.tools.vst.utils import get_stream_id
#         from vss_agents.tools.vst.video_clip import VSTVideoClipInput
#
#         sensor_id_val = sensor.get("id", "")
#         logger.info(f"Video generation: sensor_id={sensor_id_val}")
#         stream_id = await get_stream_id(sensor_id_val, vst_internal_url)
#         logger.info(f"Video generation: resolved stream_id={stream_id}")
#
#         timeline_start_str, _ = await get_timeline(stream_id, vst_internal_url)
#         logger.info(f"Video generation: timeline start={timeline_start_str}")
#         timeline_start_dt = datetime.fromisoformat(timeline_start_str.replace("Z", "+00:00"))
#
#         start_dt = datetime.fromisoformat(start_time.replace("Z", "+00:00"))
#         end_dt = datetime.fromisoformat(end_time.replace("Z", "+00:00")) if end_time else start_dt
#
#         # Add buffer for clip generation
#         clip_start_dt = start_dt - timedelta(seconds=1.5)
#         clip_end_dt = end_dt + timedelta(seconds=1.5)
#
#         clip_start_offset = (clip_start_dt - timeline_start_dt).total_seconds()
#         clip_end_offset = (clip_end_dt - timeline_start_dt).total_seconds()
#
#         # Generate video with or without overlays
#         video_input_dict = {
#             "sensor_id": sensor_id_val,
#             "start_time": clip_start_offset,
#             "end_time": clip_end_offset,
#         }
#
#         if object_ids:
#             # Add overlay parameters when object IDs are provided
#             video_input_dict["object_ids"] = object_ids
#             video_input_dict["overlay_color"] = "green"
#             video_input_dict["overlay_thickness"] = 5
#             logger.debug(f"Generating video with overlays for objects {object_ids}")
#         else:
#             logger.debug("Generating video without overlays")
#
#         video_input = VSTVideoClipInput(**video_input_dict)
#         video_output = await video_clip_fn.ainvoke(video_input)
#         return video_output.video_url, video_output.stream_id
#
#     except Exception as e:
#         logger.warning(f"Failed to generate video for objects {object_ids}: {e}")
#         return None, None


async def _perform_frame_lookups(
    candidates: list[dict[str, Any]],
    query_embedding: list[float],
    frames_index: str | list[str],
    timestamp_start: datetime | None,
    timestamp_end: datetime | None,
    es: AsyncElasticsearch,
) -> list[tuple[int | None, dict | None, float | None, str | None] | None]:
    """
    Perform frame-level lookups for all candidates to get more accurate bbox, timestamp, and frame_score.

    Returns a list of frame lookup results (or None) in the same order as candidates.
    Each result is a tuple: (frame_id, bbox, frame_score, timestamp)
    """
    frame_lookup_tasks: list[Any] = []

    # Use input timestamps directly - required for frame lookup
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

    # Execute frame lookups
    if not frame_lookup_tasks:
        return []

    tasks_to_run = [task if task is not None else asyncio.sleep(0) for task in frame_lookup_tasks]
    if any(task is not None for task in frame_lookup_tasks):
        logger.debug(f"Running {sum(1 for t in frame_lookup_tasks if t is not None)} frame lookups in parallel")
    frame_results = await asyncio.gather(*tasks_to_run, return_exceptions=True)

    # Filter out exceptions and convert to expected return type
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
    """Find the best matching frame for an object using server-side cosine similarity."""
    try:
        logger.debug(
            f"Frame search: sensor={sensor_id}, object={object_id}, time=[{start_time} to {end_time or start_time}]"
        )

        # Convert list index to comma-separated string for Elasticsearch (handles exclusion patterns)
        search_frames_index_str = frames_index if isinstance(frames_index, str) else ",".join(frames_index)

        # Painless script: iterate through objects array, calculate cosine similarity for matching object
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
                            "params": {
                                "query_vector": query_embedding,
                                "target_id": object_id,
                            },
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

        # Normalize cosine similarity from [-1, 1] to [0, 1]
        best_score = (raw_score + 1.0) / 2.0 if raw_score > 0.0 else 0.0

        best_frame_id = frame_source.get("id")
        best_timestamp = frame_source.get("timestamp", "")

        logger.debug(
            f"Frame found: id={best_frame_id}, raw_score={raw_score:.4f}, normalized={best_score:.4f}, ts={best_timestamp}"
        )

        # Extract bbox from the matching object
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


async def _fetch_object_embedding(
    object_id: str,
    behavior_index: str | list[str],
    es: AsyncElasticsearch,
) -> list[float]:
    """Fetch an object's embedding vector from the behavior index by object_id.

    Retrieves the most recent behavior embedding for the given object_id.
    Used for object re-search (Path B1): user clicks a detected bbox → find more similar objects.

    Args:
        object_id: The object ID to look up (from SearchResult.object_ids)
        behavior_index: Behavior index name(s) to search

    Returns:
        The embedding vector as list[float]

    Raises:
        ValueError: If object_id not found or has no embedding
    """
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
    # Handle both dict {"vector": [...]} and list [{"vector": [...]}] shapes
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
    """Search for similar objects using a known object's embedding from the behavior index.

    Fetches the object's embedding, then runs KNN on the behavior index to find
    visually similar objects.

    Args:
        object_id: The object ID whose embedding to use as query
        behavior_index: Behavior index name(s)
        es: Shared AsyncElasticsearch client
        top_k: Number of results to return
        min_similarity: Minimum similarity threshold
        video_sources: Optional video source filter
        timestamp_start: Optional start time filter
        timestamp_end: Optional end time filter
        source_type: Type of video source: video_file or rtsp

    Returns:
        List of AttributeSearchResult sorted by similarity
    """
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


async def enrich_attribute_results(
    results: list[AttributeSearchResult],
    vst_url: str | None,
) -> None:
    """Enrich attribute search results with screenshot URLs and resolved stream IDs.

    Mutates results in-place: resolves sensor_id → stream_id (UUID) and builds
    screenshot URLs via VST.
    """
    if not vst_url:
        return
    from vss_agents.tools.vst.utils import get_stream_id

    async def _enrich_result(r: AttributeSearchResult) -> None:
        if r.metadata and r.metadata.sensor_id and not r.screenshot_url:
            try:
                stream_id = await get_stream_id(r.metadata.sensor_id, vst_url)
                if stream_id:
                    ts = r.metadata.start_time or r.metadata.frame_timestamp
                    if ts:
                        r.screenshot_url = build_screenshot_url(vst_url, stream_id, ts)
                    r.metadata.sensor_id = stream_id
            except Exception as e:
                logger.warning(f"Failed to enrich result for sensor {r.metadata.sensor_id}: {e}")

    await asyncio.gather(*(_enrich_result(r) for r in results))


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
    """Search behavior embeddings and return candidates."""

    # Build filters FIRST
    filter_clauses = []
    if timestamp_start or timestamp_end:
        # Check for OVERLAP between behavior embedding time range and search time range
        # Behavior embedding overlaps if: behavior_start <= search_end AND behavior_end >= search_start
        # We need to find behavior embeddings where:
        #   - behavior.timestamp (start) <= timestamp_end (behavior starts before/at search end)
        #   - behavior.end >= timestamp_start (behavior ends after/at search start)
        # This ensures we catch behavior embeddings that overlap with the search window, even if
        # they start before or end after the window.
        overlap_filter: dict[str, Any] = {"bool": {"must": []}}
        if timestamp_start:
            # Behavior must end at or after search start (behavior.end >= timestamp_start)
            overlap_filter["bool"]["must"].append({"range": {"end": {"gte": timestamp_start.isoformat()}}})
        if timestamp_end:
            # Behavior must start at or before search end (behavior.timestamp <= timestamp_end)
            overlap_filter["bool"]["must"].append({"range": {"timestamp": {"lte": timestamp_end.isoformat()}}})

        # Only add filter if we have at least one condition
        if overlap_filter["bool"]["must"]:
            filter_clauses.append(overlap_filter)

    # Add video_sources filter if provided (same two-tier logic as embed_search)
    # Resolved UUIDs get a single `terms` clause; unresolved names use wildcard/regexp fallback
    if video_sources:
        if source_type == "rtsp":
            uuid_sources = []
            non_uuid_sources = video_sources
        else:
            uuid_sources = [v for v in video_sources if is_standard_uuid_string(v)]
            non_uuid_sources = [v for v in video_sources if not is_standard_uuid_string(v)]

        if uuid_sources and not non_uuid_sources:
            # All sources are UUIDs — single terms clause (fastest)
            filter_clauses.append({"terms": {"sensor.id.keyword": uuid_sources}})
        else:
            # Mixed or all non-UUID — build should clauses
            should_clauses: list[dict[str, Any]] = []
            if uuid_sources:
                should_clauses.append({"terms": {"sensor.id.keyword": uuid_sources}})
            for vname in non_uuid_sources:
                escaped_vname = vname.replace("\\", "\\\\").replace("*", "\\*").replace("?", "\\?")
                should_clauses.append({"term": {"sensor.id.keyword": vname}})
                should_clauses.append({"wildcard": {"sensor.id.keyword": f"*{escaped_vname}*"}})
                should_clauses.append({"wildcard": {"sensor.info.url.keyword": f"*{escaped_vname}"}})
                should_clauses.append({"wildcard": {"sensor.info.url.keyword": f"*{escaped_vname}*"}})
                should_clauses.append({"wildcard": {"sensor.info.path.keyword": f"*{escaped_vname}*"}})
                regex_escaped = re.escape(vname)
                should_clauses.append({"regexp": {"sensor.info.url": f".*{regex_escaped}"}})
                should_clauses.append({"regexp": {"sensor.info.path": f".*{regex_escaped}"}})
            filter_clauses.append(
                {
                    "bool": {
                        "should": should_clauses,
                        "minimum_should_match": 1,
                    }
                }
            )

    # Build KNN query with filters INSIDE (so filters are applied during KNN search, not after)
    # Fetch more candidates to account for duplicates - we'll deduplicate and return top_k later
    # Use a multiplier to ensure we have enough unique results after deduplication
    # For top_k=1 (e.g., fusion reranking), fetch fewer candidates since we only need 1 result
    if top_k == 1:
        fetch_k = 10  # For fusion, we only need 1 result after deduplication
    else:
        # Increase overfetching for better diversity: 10x multiplier, minimum 200 candidates
        # This helps when many detections are of the same object (e.g., same person in different frames)
        fetch_k = max(top_k * 10, 200)  # Fetch 10x top_k to account for duplicates and ensure diversity

    knn_query: dict[str, Any] = {
        "field": "embeddings.vector",
        "query_vector": query_embedding,
        "k": fetch_k,
        "num_candidates": max(fetch_k * 2, 100),  # HNSW exploration pool
    }

    # Add filter to KNN query if present (Elasticsearch will filter DURING vector search)
    # When multiple filters, combine them in a bool.must query
    if filter_clauses:
        if len(filter_clauses) > 1:
            knn_query["filter"] = {"bool": {"must": filter_clauses}}
        else:
            knn_query["filter"] = filter_clauses[0]

    logger.debug(f"Query embedding: dim={len(query_embedding)}")
    logger.debug(
        f"KNN search: top_k={top_k}, fetch_k={fetch_k}, k={knn_query['k']}, num_candidates={knn_query['num_candidates']}, filters={len(filter_clauses)}"
    )

    # Construct search query
    # Fetch more results initially to account for duplicates (will deduplicate and return top_k later)
    search_query: dict[str, Any] = {
        "knn": knn_query,
        "size": fetch_k,  # Fetch more to account for duplicates
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

    logger.debug(f"Searching objects: top_k={top_k}, fetching {fetch_k} candidates to account for duplicates")

    # Convert list index to comma-separated string for Elasticsearch (handles exclusion patterns)
    search_index_str = index if isinstance(index, str) else ",".join(index)
    logger.debug(f"Searching index: {search_index_str}")

    try:
        response = await es.search(index=search_index_str, body=search_query)
    except ESNotFoundError as e:
        # Index doesn't exist - return empty result with informative error
        logger.error(f"Elasticsearch index '{search_index_str}' not found: {e}")
        raise ValueError(
            f"Search index '{search_index_str}' does not exist. "
            "Please ensure videos have been ingested before searching."
        ) from e

    # Log ES response
    total_hits = response["hits"]["total"]["value"]
    raw_hits = len(response["hits"]["hits"])
    logger.info(f"Found {raw_hits} candidates (total: {total_hits})")
    if raw_hits > 1:
        # ES returns results sorted by score descending (highest score first)
        # Only log score range when there are multiple results
        top_score = response["hits"]["hits"][0]["_score"]
        bottom_score = response["hits"]["hits"][-1]["_score"]
        logger.debug(f"Score range: {top_score:.4f} (best) to {bottom_score:.4f} (worst)")

    # Collect candidates (already sorted by score descending from ES)
    candidates = list(response["hits"]["hits"])

    logger.debug(f"Collected {len(candidates)} candidates for processing")
    return candidates


async def _build_result(
    hit: dict[str, Any],
    frame_result: Any,
    input_timestamp_start: datetime | None = None,
    input_timestamp_end: datetime | None = None,
) -> AttributeSearchResult:
    """
    Build an AttributeSearchResult from a behavior hit and frame lookup result.

    If input_timestamp_start and input_timestamp_end are provided, they will be used
    for the output start_time and end_time. Otherwise, behavior embedding timestamps
    are used.
    """
    score = hit["_score"]
    source = hit["_source"]
    obj = source.get("object", {})
    sensor = source.get("sensor", {})
    object_id = obj.get("id", "unknown")
    sensor_id = sensor.get("id", "unknown")

    logger.debug(f"Processing: sensor={sensor_id}, object={object_id}, score={score:.4f}")

    # Extract frame lookup results
    frame_bbox = None
    query_to_frame_score = None
    best_frame_timestamp = None

    if frame_result is not None and not isinstance(frame_result, Exception):
        _, frame_bbox, query_to_frame_score, best_frame_timestamp = frame_result
        if best_frame_timestamp:
            logger.debug(f"Frame score={query_to_frame_score:.4f}")
    elif isinstance(frame_result, Exception):
        logger.debug(f"Frame lookup failed for object {object_id}: {frame_result}")

    # Use frame bbox if available, otherwise fall back to behavior bbox
    # Clean up bbox to only include relevant fields (remove embeddings, info, etc.)
    if frame_bbox is not None:
        final_bbox = frame_bbox
    else:
        behavior_bbox = obj.get("bbox", {})
        # Extract only relevant bbox fields, excluding embeddings, info, and confidence
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

    # Extract behavior embedding timestamps (start and end)
    # Convert empty strings to None for proper type handling
    behavior_end_raw = source.get("end", "")
    behavior_start_raw = source.get("timestamp", "")
    behavior_end = cast("str | None", behavior_end_raw if behavior_end_raw else None)
    behavior_start = cast("str | None", behavior_start_raw if behavior_start_raw else None)

    # Use best frame timestamp if available, otherwise fall back to behavior timestamp
    # For behavior data, use midpoint between start and end timestamps
    if best_frame_timestamp:
        final_timestamp = best_frame_timestamp
    else:
        if behavior_start and behavior_end:
            # Calculate midpoint between start and end
            start_dt = datetime.fromisoformat(behavior_start.replace("Z", "+00:00"))
            end_dt = datetime.fromisoformat(behavior_end.replace("Z", "+00:00"))
            midpoint_dt = start_dt + (end_dt - start_dt) / 2
            final_timestamp = midpoint_dt.isoformat().replace("+00:00", "Z")
        else:
            final_timestamp = behavior_end if behavior_end else behavior_start

    # Log scores
    if query_to_frame_score is not None:
        logger.debug(f"Object {object_id}: behavior_score={score:.4f}, frame_score={query_to_frame_score:.4f}")
    else:
        logger.debug(f"Object {object_id}: behavior_score={score:.4f} (no frame score)")

    # Determine start_time and end_time for output:
    # - If input timestamps were provided to attribute search, use those
    # - Otherwise, use behavior embedding timestamps
    # - If behavior_end is missing, use behavior_start for both
    output_start_time: str | None
    output_end_time: str | None
    if input_timestamp_start is not None:
        # Convert datetime to ISO string
        from vss_agents.utils.time_convert import datetime_to_iso8601

        output_start_time = datetime_to_iso8601(input_timestamp_start)
        output_end_time = (
            datetime_to_iso8601(input_timestamp_end) if input_timestamp_end is not None else output_start_time
        )
        logger.debug(f"Object {object_id}: Using input timestamps: start={output_start_time}, end={output_end_time}")
    else:
        # Use behavior embedding timestamps
        output_start_time = behavior_start if behavior_start else None
        # If end is missing, use start for both (single timestamp case)
        output_end_time = behavior_end if behavior_end else (behavior_start if behavior_start else None)
        logger.debug(
            f"Object {object_id}: Using behavior embedding timestamps: start={output_start_time}, end={output_end_time}"
        )

    # Build metadata
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
        video_name=None,  # Will be set later when converting sensor_id to UUID
    )

    return AttributeSearchResult(
        screenshot_url=None,  # Will be set later in search_attributes
        metadata=metadata,
    )


async def _extend_clip_to_one_second(
    result: AttributeSearchResult,
    vst_internal_url: str | None,
    vst_external_url: str,
) -> None:
    """
    Extend clip duration to at least MIN_CLIP_DURATION_SECONDS while respecting VST timeline bounds.

    If the clip duration is < MIN_CLIP_DURATION_SECONDS, extends it to MIN_CLIP_DURATION_SECONDS
    centered on the midpoint, clipped to VST timeline bounds. Modifies the result's start_time
    and end_time in place.

    Args:
        result: AttributeSearchResult to extend
        vst_internal_url: Internal VST URL for timeline lookups
        vst_external_url: External VST URL (fallback for resolution)
    """
    if not result.metadata or not result.metadata.start_time or not result.metadata.end_time:
        return

    if not result.metadata.sensor_id:
        return

    try:
        from vss_agents.tools.vst.timeline import get_timeline
        from vss_agents.tools.vst.utils import get_stream_id
        from vss_agents.utils.time_convert import datetime_to_iso8601
        from vss_agents.utils.time_convert import iso8601_to_datetime

        start_dt = iso8601_to_datetime(result.metadata.start_time)
        end_dt = iso8601_to_datetime(result.metadata.end_time)
        duration = (end_dt - start_dt).total_seconds()

        if duration >= MIN_CLIP_DURATION_SECONDS:
            return  # Already >= minimum duration, no extension needed

        # Get stream_id from sensor_id (may be sensor name or UUID)
        vst_internal_for_resolution = vst_internal_url if vst_internal_url else vst_external_url
        stream_id = await get_stream_id(result.metadata.sensor_id, vst_internal_for_resolution)

        if not stream_id:
            logger.warning(f"Could not resolve stream_id for sensor_id={result.metadata.sensor_id}")
            return

        # Get VST timeline bounds
        timeline_start_iso, timeline_end_iso = await get_timeline(stream_id, vst_internal_for_resolution)
        timeline_start = iso8601_to_datetime(timeline_start_iso)
        timeline_end = iso8601_to_datetime(timeline_end_iso)

        # Calculate midpoint of current range
        midpoint = start_dt + (end_dt - start_dt) / 2
        # Extend to minimum duration centered on midpoint
        half_duration = MIN_CLIP_DURATION_SECONDS / 2.0
        new_start = midpoint - timedelta(seconds=half_duration)
        new_end = midpoint + timedelta(seconds=half_duration)

        # Clip to VST timeline bounds
        new_start = max(new_start, timeline_start)
        new_end = min(new_end, timeline_end)

        # Ensure we still have at least minimum duration if possible
        if (new_end - new_start).total_seconds() < MIN_CLIP_DURATION_SECONDS:
            # Try to extend from the end if there's room
            if new_end < timeline_end:
                new_end = min(new_start + timedelta(seconds=MIN_CLIP_DURATION_SECONDS), timeline_end)
            # Or extend from the start if there's room
            elif new_start > timeline_start:
                new_start = max(new_end - timedelta(seconds=MIN_CLIP_DURATION_SECONDS), timeline_start)

        result.metadata.start_time = datetime_to_iso8601(new_start)
        result.metadata.end_time = datetime_to_iso8601(new_end)
        logger.info(
            f"Extended clip < {MIN_CLIP_DURATION_SECONDS}s to {MIN_CLIP_DURATION_SECONDS}s: {result.metadata.sensor_id} "
            f"({duration:.3f}s -> {(new_end - new_start).total_seconds():.3f}s)"
        )
    except Exception as e:
        logger.warning(
            f"Failed to extend clip for {result.metadata.sensor_id if result.metadata else 'unknown'}: {e}. "
            f"Using original timestamps."
        )


def _deduplicate_by_object(
    results: list[AttributeSearchResult],
    candidates: list[dict[str, Any]] | None = None,
) -> list[AttributeSearchResult]:
    """
    Merge duplicate results for the same (sensor_id, object_id) pair.
    Keep the first occurrence (highest score) and merge time ranges by updating start_time and end_time.

    Args:
        results: List of AttributeSearchResult (already sorted by similarity descending)
        candidates: Optional list of original ES hits (must match results by index)

    Returns:
        Deduplicated list of AttributeSearchResult (maintains sort order)
    """
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
            # Merge: update start_time and end_time of existing result with earliest start and latest end
            existing_result, existing_idx = merged[key]
            duplicate_count += 1

            logger.debug(
                f"Deduplication: Found duplicate for sensor_id={result.metadata.sensor_id}, "
                f"object_id={result.metadata.object_id}, score={result.metadata.behavior_score:.4f}. "
                f"Existing score={existing_result.metadata.behavior_score:.4f}."
            )

            if candidates and existing_idx < len(candidates) and idx < len(candidates):
                existing_source = candidates[existing_idx].get("_source", {})
                new_source = candidates[idx].get("_source", {})

                # Use metadata's start_time/end_time (which may have already been merged) as the baseline
                # This ensures we accumulate the merged time range across multiple duplicates
                existing_start = existing_result.metadata.start_time or existing_source.get("timestamp")
                existing_end = existing_result.metadata.end_time or existing_source.get("end")
                new_start = new_source.get("timestamp")
                new_end = new_source.get("end")

                logger.debug(
                    f"Deduplication: Existing time range: [{existing_start} to {existing_end}], "
                    f"New time range: [{new_start} to {new_end}]"
                )

                # Find earliest start and latest end
                earliest_start = existing_start
                latest_end = existing_end

                if new_start and existing_start:
                    try:
                        if datetime.fromisoformat(new_start.replace("Z", "+00:00")) < datetime.fromisoformat(
                            existing_start.replace("Z", "+00:00")
                        ):
                            earliest_start = new_start
                            logger.debug(f"Deduplication: Updated earliest_start to {earliest_start}")
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
                            logger.debug(f"Deduplication: Updated latest_end to {latest_end}")
                    except (ValueError, AttributeError):
                        pass
                elif new_end:
                    latest_end = new_end

                # Update result's start_time and end_time directly
                if earliest_start != existing_start or latest_end != existing_end:
                    merge_count += 1

                    logger.debug(
                        f"Deduplication: Merging time ranges for sensor_id={result.metadata.sensor_id}, "
                        f"object_id={result.metadata.object_id}. "
                        f"Merged range: start_time={earliest_start}, end_time={latest_end}"
                    )

                    # Update the existing result's start_time and end_time directly
                    existing_result.metadata.start_time = earliest_start
                    existing_result.metadata.end_time = latest_end
            else:
                logger.debug(
                    f"Deduplication: Cannot merge timestamps (candidates not available) for "
                    f"sensor_id={result.metadata.sensor_id}, object_id={result.metadata.object_id}"
                )

    if duplicate_count > 0:
        logger.info(
            f"Deduplication: Found {duplicate_count} duplicate(s), merged {merge_count} time range(s). "
            f"Kept {len(merged)} unique result(s) from {len(results)} total result(s)."
        )

    return [result for result, _ in merged.values()]


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
    """Search for objects by attribute embeddings and return scores per object-video pair."""
    exclude_videos = exclude_videos or []
    try:
        # Phase 1: Search behavior embeddings
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

        # Phase 2: Perform frame lookups (if enabled) to get more accurate bbox, timestamp, and frame_score
        # When disabled, we use behavior embedding data directly (bbox, timestamp from behavior)
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

        # Phase 3: Build results
        results = []
        if enable_frame_lookup and frames_index:
            # Perform frame lookups to get more accurate bbox, timestamp, and frame_score
            with TimeMeasure("attribute_search: frame lookups"):
                frame_results = await _perform_frame_lookups(
                    candidates=candidates,
                    query_embedding=query_embedding,
                    frames_index=frames_index,
                    timestamp_start=timestamp_start,
                    timestamp_end=timestamp_end,
                    es=es,
                )
            # Build results with frame lookup data
            for idx, hit in enumerate(candidates):
                frame_result = frame_results[idx] if idx < len(frame_results) else None
                result = await _build_result(
                    hit=hit,
                    frame_result=frame_result,
                )
                results.append(result)
        else:
            # Frame lookup disabled - use behavior-level data only (bbox, timestamp from behavior embeddings)
            if not enable_frame_lookup:
                logger.debug(
                    "Frame lookup disabled - using behavior-level embeddings only (bbox, timestamp from behavior data)"
                )
            # Build results using behavior data directly
            for hit in candidates:
                result = await _build_result(
                    hit=hit,
                    frame_result=None,  # No frame lookup, use behavior data
                )
                results.append(result)

        logger.info(f"Matched {len(results)} object-video pairs")

        # Deduplicate: Keep only the best result per (sensor_id, object_id) pair, merge timestamps
        with TimeMeasure("attribute_search: deduplication"):
            results = _deduplicate_by_object(results, candidates)
        logger.info(f"After deduplication: {len(results)} unique object-video pairs")

        # Remove excluded videos before top_k truncation
        exclude_set = {
            (ev.get("sensor_id", ""), ev.get("start_timestamp", ""), ev.get("end_timestamp", ""))
            for ev in exclude_videos
        }
        results = [
            r for r in results if (r.metadata.sensor_id, r.metadata.start_time, r.metadata.end_time) not in exclude_set
        ]

        # Return top_k after deduplication
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
    """Search for a single attribute."""
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
) -> list[AttributeSearchResult]:
    """
    Search for objects by visual attributes.

    Two modes:
    - fuse_multi_attribute=True (default): Fuses multiple attributes (combines object IDs for single screenshot)
    - fuse_multi_attribute=False: Appends top_k results per attribute independently (no fusion)
    """
    queries = [search_input.query] if isinstance(search_input.query, str) else search_input.query
    logger.info(f"Searching {len(queries)} attribute(s) (fuse_multi_attribute={search_input.fuse_multi_attribute})")

    # Choose index(es) by source_type: video_file -> behavior_index; otherwise mdx-behavior-* excluding behavior_index
    source_type = search_input.source_type
    search_index: str | list[str]
    search_frames_index: str | list[str] | None
    if source_type == "video_file":
        search_index = index
        search_frames_index = frames_index
    else:
        # For rtsp/stream sources, search all mdx-behavior-* indexes except the video_file one
        search_index = ["mdx-behavior-*", "-" + index]
        # For frames, search all mdx-raw-* indexes except the video_file one (if frames_index is set)
        if frames_index:
            search_frames_index = ["mdx-raw-*", "-" + frames_index]
        else:
            search_frames_index = "mdx-raw-*"

    logger.info(f"Search index(es): {search_index} (source_type={source_type})")
    if search_frames_index:
        logger.info(f"Frames index(es): {search_frames_index} (source_type={source_type})")

    if search_input.fuse_multi_attribute:
        # FUSE MODE: Current behavior - fuse object IDs for single screenshot
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
        # APPEND MODE: Return top_k per attribute independently (no fusion)
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
    """Fuse mode: Combine object IDs from all attributes for single screenshot."""
    # Search all attributes with top_k=1
    search_input_single = AttributeSearchInput(
        query=search_input.query,
        source_type=search_input.source_type,
        timestamp_start=search_input.timestamp_start,
        timestamp_end=search_input.timestamp_end,
        video_sources=search_input.video_sources,
        top_k=1,
        min_similarity=search_input.min_similarity,
        fuse_multi_attribute=True,  # Preserve flag
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

    # Collect object IDs and sensor info from results
    object_ids = []
    sensor_id = None
    frame_timestamps = []

    for result in all_results:
        if result.metadata:
            try:
                object_ids.append(int(result.metadata.object_id))
                # Extract sensor_id from the first result (all should have the same sensor.id due to filtering)
                if sensor_id is None:
                    sensor_id = result.metadata.sensor_id
                if result.metadata.frame_timestamp:
                    frame_timestamps.append(result.metadata.frame_timestamp)
            except (ValueError, TypeError):
                pass

    # Generate screenshot (no video generation) - single screenshot for all fused objects
    if sensor_id and vst_external_url and search_input.timestamp_start and search_input.timestamp_end:
        try:
            from vss_agents.tools.vst.utils import get_stream_id

            start_time = search_input.timestamp_start.isoformat().replace("+00:00", "Z")

            # Get stream_id from sensor_id (accepts either camera name or UUID)
            # Use internal URL for stream resolution (agent needs internal access)
            vst_internal_for_resolution = vst_internal_url if vst_internal_url else vst_external_url
            stream_id = await get_stream_id(sensor_id, vst_internal_for_resolution)

            screenshot_url = None
            if stream_id:
                # Use midpoint of the time range for screenshot (most likely to show all objects)
                screenshot_timestamp = start_time
                if frame_timestamps:
                    # Sort timestamps and pick the middle one (median)
                    sorted_timestamps = sorted(frame_timestamps)
                    mid_idx = len(sorted_timestamps) // 2
                    screenshot_timestamp = sorted_timestamps[mid_idx]
                    logger.debug(f"Using median frame timestamp for screenshot: {screenshot_timestamp}")

                screenshot_url = build_screenshot_url(vst_external_url, stream_id, screenshot_timestamp)

            # Update all results with screenshot and convert sensor_id to stream_id (UUID)
            if stream_id:
                for result in all_results:
                    if screenshot_url and not result.screenshot_url:
                        result.screenshot_url = screenshot_url
                    # Update metadata.sensor_id to stream_id (UUID)
                    if result.metadata:
                        result.metadata.sensor_id = stream_id
                        logger.debug(f"Updated sensor_id to stream_id '{stream_id}' for fused results")

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
    """Append mode: Return top_k results per attribute independently (no fusion)."""
    # Search each attribute with top_k (not top_k=1)
    search_input_per_attr = AttributeSearchInput(
        query=search_input.query,
        source_type=search_input.source_type,
        timestamp_start=search_input.timestamp_start,
        timestamp_end=search_input.timestamp_end,
        video_sources=search_input.video_sources,
        top_k=search_input.top_k,  # Use the requested top_k per attribute
        min_similarity=search_input.min_similarity,
        fuse_multi_attribute=False,  # Preserve flag
        exclude_videos=search_input.exclude_videos,
    )

    # Search each attribute independently
    all_results = []
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

            # Extend clips < 1 second to 1 second while respecting VST bounds
            if attr_results and vst_internal_url:
                for result in attr_results:
                    await _extend_clip_to_one_second(result, vst_internal_url, vst_external_url)

            # Generate screenshot for each attribute's results independently
            # Filter out invalid sensor_ids from behavior index (e.g., "0" from garbage ES data) via VST validation
            valid_results = []
            if attr_results and vst_external_url:
                for result in attr_results:
                    if result.metadata and result.metadata.sensor_id and result.metadata.frame_timestamp:
                        try:
                            from vss_agents.tools.vst.utils import get_stream_id

                            # Set video_name to original sensor_id (sensor name) before converting to UUID
                            result.metadata.video_name = result.metadata.sensor_id

                            vst_internal_for_resolution = vst_internal_url if vst_internal_url else vst_external_url
                            stream_id = await get_stream_id(result.metadata.sensor_id, vst_internal_for_resolution)

                            # Update metadata.sensor_id to stream_id (UUID)
                            if stream_id:
                                result.metadata.sensor_id = stream_id

                            if stream_id and not result.screenshot_url:
                                result.screenshot_url = build_screenshot_url(
                                    vst_external_url, stream_id, result.metadata.frame_timestamp
                                )

                            valid_results.append(result)
                        except Exception as e:
                            # Skip result if VST conversion fails
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

    # Deduplicate: Keep only the best result per (sensor_id, object_id) pair
    all_results = _deduplicate_by_object(all_results)
    logger.info(f"After deduplication: {len(all_results)} unique object-video pairs")

    # Return top_k after deduplication
    top_k = search_input.top_k
    if top_k > 0 and len(all_results) > top_k:
        all_results = all_results[:top_k]
        logger.info(f"Returning top {top_k} results after deduplication")

    return all_results


@register_function(config_type=AttributeSearchConfig)
async def build_attribute_search(config: AttributeSearchConfig, _builder: Builder) -> AsyncGenerator[FunctionInfo]:
    """NAT function builder for attribute search."""
    # Always use RTVI CV for text embeddings
    embed_client: EmbedClient = RTVICVEmbedClient(config.rtvi_cv_endpoint)

    logger.info("Text embedding: rtvi_cv")

    es = await VSSESClient.get_es_client(es_endpoint=config.es_endpoint)

    async def attribute_search_fn(search_input: AttributeSearchInput) -> list[AttributeSearchResult]:
        return await search_attributes(
            search_input,
            embed_client,
            config.behavior_index,
            config.vst_external_url,
            es=es,
            vst_internal_url=config.vst_internal_url,
            frames_index=config.frames_index,
            enable_frame_lookup=config.enable_frame_lookup,
        )

    try:
        yield FunctionInfo.create(
            single_fn=attribute_search_fn,
            description="Search for objects by visual attributes",
            input_schema=AttributeSearchInput,
            # Note: single_output_schema removed to avoid Python 3.13 isinstance() issues with parameterized generics
        )
    finally:
        try:
            await embed_client.aclose()
        except Exception as e:
            logger.warning(f"Error closing embed client: {e}")
        try:
            await VSSESClient.close_all()
        except Exception as e:
            logger.warning(f"Error closing ES clients: {e}")
