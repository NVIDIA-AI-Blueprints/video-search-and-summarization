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
from datetime import UTC
from datetime import datetime
import json
import logging
import re
from typing import TYPE_CHECKING
from typing import Any
from typing import Literal

from elasticsearch import AsyncElasticsearch
from elasticsearch import NotFoundError as ESNotFoundError
from nat.builder.builder import Builder
from nat.builder.framework_enum import LLMFrameworkEnum
from nat.builder.function_info import FunctionInfo
from nat.cli.register_workflow import register_function
from nat.data_models.api_server import ChatRequest
from nat.data_models.function import FunctionBaseConfig
from pydantic import BaseModel
from pydantic import Field

from vss_agents.embed.cosmos_embed import CosmosEmbedClient
from vss_agents.tools.vst.snapshot import build_screenshot_url
from vss_agents.utils.time_convert import datetime_to_iso8601
from vss_agents.utils.time_convert import iso8601_to_datetime

if TYPE_CHECKING:
    from vss_agents.embed.embed import EmbedClient

# Base timestamp
BASE_2025 = datetime(2025, 1, 1, tzinfo=UTC)

logger = logging.getLogger(__name__)


def _sanitize_for_logging(obj: Any) -> Any:
    """Remove embedding vectors from objects for logging purposes.

    Recursively traverses dictionaries and lists to remove 'vector' fields
    and 'query_vector' fields while preserving all other data.

    Args:
        obj: Object to sanitize (dict, list, or other)

    Returns:
        Sanitized object with embeddings removed
    """
    if isinstance(obj, dict):
        sanitized = {}
        for key, value in obj.items():
            if key in ("vector", "query_vector"):
                # Replace embedding vectors with a placeholder
                if isinstance(value, list) and len(value) > 0:
                    sanitized[key] = f"<embedding_vector(length={len(value)})>"
                else:
                    sanitized[key] = "<embedding_vector>"
            elif key == "embeddings" and isinstance(value, list):
                # Replace embeddings list with summary
                sanitized[key] = f"<embeddings_list(length={len(value)})>"
            else:
                sanitized[key] = _sanitize_for_logging(value)
        return sanitized
    elif isinstance(obj, list):
        return [_sanitize_for_logging(item) for item in obj]
    else:
        return obj


# Flat output models (replacing nested VisionLLM hierarchy)
class EmbedSearchResultItem(BaseModel):
    """A single embed search result with all fields extracted."""

    video_name: str = Field(default="", description="Video filename")
    description: str = Field(default="", description="Video/sensor description")
    start_time: str = Field(default="", description="Start time (ISO format)")
    end_time: str = Field(default="", description="End time (ISO format)")
    sensor_id: str = Field(default="", description="Sensor/stream UUID")
    screenshot_url: str = Field(default="", description="Screenshot URL")
    similarity_score: float = Field(default=0.0, description="Cosine similarity score")


class EmbedSearchOutput(BaseModel):
    """Output of embed search."""

    query_embedding: list[float] = Field(default_factory=list, description="Query embedding vector")
    results: list[EmbedSearchResultItem] = Field(default_factory=list, description="Search results")


class QueryInput(BaseModel):
    """Query input model for schema validation."""

    id: str = Field(default="", description="Query ID")
    params: dict[str, str] = Field(default_factory=dict, description="Query parameters")
    prompts: dict[str, str] = Field(default_factory=dict, description="Query prompts")
    response: str = Field(default="", description="Query response")
    embeddings: list[dict[str, Any]] = Field(default_factory=list, description="Query embeddings")
    source_type: Literal["video_file", "rtsp"] = Field(
        ...,
        description="Type of video source: 'video_file' for uploaded videos, 'rtsp' for live/camera streams.",
    )
    exclude_videos: list[dict[str, str]] = Field(
        default_factory=list, description="List of videos to exclude from results"
    )


class EmbedSearchConfig(FunctionBaseConfig, name="embed_search"):
    """Configuration for the Embed Search tool."""

    cosmos_embed_endpoint: str = Field(
        ...,
        description="The URL of the backend to use for video ingestion.",
    )
    es_endpoint: str = Field(
        ...,
        description="The URL of the Elasticsearch endpoint to use for video ingestion.",
    )
    es_index: str = Field(
        default="video_embeddings",
        description="The index of the Elasticsearch to use for video ingestion.",
    )
    vst_external_url: str = Field(
        ...,
        description="The external VST URL for client-facing URLs.",
    )
    vst_internal_url: str | None = Field(
        default=None,
        description="The internal VST URL for validation requests. If not provided, uses vst_external_url.",
    )
    default_max_results: int = Field(
        default=100,
        description="Maximum number of results to return when top_k is not specified.",
    )
    # NOTE: video_clip_tool removed - UI calls VST API directly for video overlays


def _str_input_converter(input: str) -> QueryInput:
    """Convert string input to QueryInput Pydantic model."""
    try:
        input_dict = json.loads(input)
        logger.info(f"Input dict: {input_dict}")
        # If it's already a Query JSON format, create QueryInput directly
        if "params" in input_dict or "prompts" in input_dict:
            return QueryInput(**input_dict)
        else:
            # Not in Query format, treat entire input as query string
            logger.warning(f"Input not in Query format, treating as query string: {input}")
            return QueryInput(id="", params={"query": input}, source_type="video_file")
    except Exception as e:
        logger.exception(f"Error parsing input to QueryInput, using as query string: {input}, error: {e}")
        return QueryInput(id="", params={"query": input}, source_type="video_file")


def _chat_request_input_converter(request: ChatRequest) -> QueryInput:
    """Convert ChatRequest to QueryInput Pydantic model."""
    try:
        content = request.messages[-1].content
        input_dict = json.loads(content)
        logger.info(f"Input dict: {input_dict}")
        # If it's already a Query JSON format, create QueryInput directly
        if "params" in input_dict or "prompts" in input_dict:
            return QueryInput(**input_dict)
        else:
            # Not in Query format, treat entire content as query string
            logger.warning(f"Input not in Query format, treating as query string: {content}")
            return QueryInput(id="", params={"query": content}, source_type="video_file")
    except Exception as e:
        logger.exception(
            f"Error parsing input to QueryInput, using as query string: {request.messages[-1].content}, error: {e}"
        )
        return QueryInput(id="", params={"query": request.messages[-1].content}, source_type="video_file")


def _to_str_output(output: EmbedSearchOutput) -> str:
    """Convert EmbedSearchOutput to JSON string."""
    return output.model_dump_json()


async def _generate_query_embedding(query_input: QueryInput, embed_client: "EmbedClient") -> list[float]:
    """Step 1: Generate query embedding from the appropriate source.

    Args:
        query_input: The query input containing text, image_url, video_url, or pre-computed embeddings
        embed_client: The embedding client to use

    Returns:
        Query embedding vector as list of floats
    """
    if query_input.embeddings:
        # Use pre-computed embedding if provided
        vector = query_input.embeddings[0].get("vector", [])
        if isinstance(vector, list):
            return [float(v) for v in vector]
        return []

    image_url = query_input.params.get("image_url", "")
    query_text = query_input.params.get("query", "")
    video_url = query_input.params.get("video_url", "")

    if image_url:
        return await embed_client.get_image_embedding(image_url)
    elif query_text:
        return await embed_client.get_text_embedding(query_text.strip())
    elif video_url:
        return await embed_client.get_video_embedding(video_url)
    else:
        raise ValueError("Either query, image_url, video_url, or embeddings must be provided in Query params.")


def _build_es_query(query_input: QueryInput, query_embedding: list[float], config: EmbedSearchConfig) -> dict[str, Any]:
    """Build Elasticsearch query body.

    Args:
        query_input: The query input with filter parameters
        query_embedding: The query embedding vector
        config: Embed search configuration

    Returns:
        The search query body.
    """
    # Extract parameters from QueryInput
    video_sources_str = query_input.params.get("video_sources", "")
    top_k_str = query_input.params.get("top_k", "")
    top_k: int | None = int(top_k_str) if top_k_str else None
    min_cosine_similarity = float(query_input.params.get("min_cosine_similarity", "0.0"))
    description = query_input.params.get("description", "")
    timestamp_start_str = query_input.params.get("timestamp_start", "")
    timestamp_end_str = query_input.params.get("timestamp_end", "")

    # Parse video_sources if provided (can be JSON string or comma-separated)
    video_sources: list[str] = []
    if video_sources_str:
        try:
            # Try parsing as JSON array
            parsed = json.loads(video_sources_str)
            if isinstance(parsed, list):
                video_sources = [str(v) for v in parsed]
            else:
                # If JSON parsing succeeded but result is not a list, treat as comma-separated
                video_sources = [v.strip() for v in video_sources_str.split(",") if v.strip()]
        except Exception:
            # Try comma-separated string
            video_sources = [v.strip() for v in video_sources_str.split(",") if v.strip()]

    # Parse timestamps if provided
    timestamp_start: datetime | None = None
    timestamp_end: datetime | None = None
    if timestamp_start_str:
        try:
            user_ts = iso8601_to_datetime(timestamp_start_str)
            timestamp_start = user_ts
        except Exception as e:
            logger.warning(f"Failed to parse timestamp_start: {e}")
    if timestamp_end_str:
        try:
            user_ts = iso8601_to_datetime(timestamp_end_str)
            timestamp_end = user_ts
        except Exception as e:
            logger.warning(f"Failed to parse timestamp_end: {e}")

    # Build filter conditions
    filters: list[dict[str, Any]] = []

    # Add video_sources filter if provided
    if video_sources:
        should_clauses = []
        for vname in video_sources:
            escaped_vname = vname.replace("\\", "\\\\").replace("*", "\\*").replace("?", "\\?")
            # Check sensor.id (for RTSP streams and video files)
            should_clauses.append({"term": {"sensor.id.keyword": vname}})
            should_clauses.append({"wildcard": {"sensor.id.keyword": f"*{escaped_vname}*"}})
            # Check sensor.info.url (for uploaded video files)
            should_clauses.append({"wildcard": {"sensor.info.url.keyword": f"*{escaped_vname}"}})
            should_clauses.append({"wildcard": {"sensor.info.url.keyword": f"*{escaped_vname}*"}})
            # Check sensor.info.path (for RTSP streams - contains UUID)
            should_clauses.append({"wildcard": {"sensor.info.path.keyword": f"*{escaped_vname}*"}})
            regex_escaped = re.escape(vname)
            should_clauses.append({"regexp": {"sensor.info.url": f".*{regex_escaped}"}})
            should_clauses.append({"regexp": {"sensor.info.path": f".*{regex_escaped}"}})

        filters.append(
            {
                "bool": {
                    "should": should_clauses,
                    "minimum_should_match": 1,
                }
            }
        )

    # Add description filter
    if description:
        escaped_desc = description.replace("\\", "\\\\").replace("*", "\\*").replace("?", "\\?")
        regex_escaped_desc = re.escape(description)

        description_should_clauses = [
            {"match": {"sensor.description": description}},
            {"wildcard": {"sensor.description.keyword": f"*{escaped_desc}*"}},
            {"wildcard": {"sensor.description.keyword": f"*{escaped_desc}"}},
            {"regexp": {"sensor.description": f".*{regex_escaped_desc}.*"}},
            {"regexp": {"sensor.description.keyword": f".*{regex_escaped_desc}.*"}},
        ]

        filters.append(
            {
                "bool": {
                    "should": description_should_clauses,
                    "minimum_should_match": 1,
                }
            }
        )

    # Add timestamp range filter
    if timestamp_start or timestamp_end:
        must_clauses = []

        if timestamp_start:
            must_clauses.append({"range": {"timestamp": {"gte": timestamp_start.isoformat()}}})

        if timestamp_end:
            must_clauses.append({"range": {"end": {"lte": timestamp_end.isoformat()}}})

        if len(must_clauses) > 1:
            filters.append({"bool": {"must": must_clauses}})
        else:
            filters.append(must_clauses[0])

    # Adjust k based on filters and similarity threshold
    if top_k is None:
        k_value = config.default_max_results
    elif min_cosine_similarity >= -1.0 or filters:
        k_value = top_k * 5
    else:
        k_value = top_k
    num_candidates = k_value * 2

    # Build nested KNN query
    knn_query: dict[str, Any] = {
        "field": "llm.visionEmbeddings.vector",
        "query_vector": query_embedding,
        "k": k_value,
        "num_candidates": num_candidates,
    }

    # Build nested query wrapping the KNN query
    nested_query: dict[str, Any] = {
        "nested": {
            "path": "llm.visionEmbeddings",
            "query": {
                "knn": knn_query,
            },
            "inner_hits": {
                "size": 1,
            },
        }
    }

    # Build search query with filters
    if filters:
        if len(filters) > 1:
            filter_clause = {"bool": {"must": filters}}
        else:
            filter_clause = filters[0]

        search_query = {
            "query": {
                "bool": {
                    "must": [nested_query],
                    "filter": [filter_clause],
                }
            },
            "size": k_value,
        }
    else:
        search_query = {
            "query": nested_query,
            "size": k_value,
        }

    logger.debug(f"ES search_query:\n{json.dumps(search_query, indent=2)}")
    logger.info(f"Search query: {_sanitize_for_logging(search_query)}")

    return search_query


async def _process_search_hit(
    hit: dict[str, Any], config: EmbedSearchConfig, min_cosine_similarity: float, exclude_videos: list[dict[str, str]]
) -> EmbedSearchResultItem | None:
    """Step 3: Process a single ES search hit into an EmbedSearchResultItem.

    Args:
        hit: A single Elasticsearch search hit
        config: Embed search configuration
        min_cosine_similarity: Minimum cosine similarity threshold
        exclude_videos: List of videos to exclude from results (sensor_id, start_timestamp, end_timestamp)
    Returns:
        EmbedSearchResultItem if hit passes filters, None otherwise
    """
    try:
        # ES score is normalized to [0, 1] range, UI sends min_cosine_similarity in [-1, 1] range
        # Convert ES score to cosine: cosine = (2 * _score) - 1
        # Round to 2 decimal places before comparing to avoid floating-point precision issues
        # (e.g., 2 * 0.60 - 1 = 0.19999... which would incorrectly fail a 0.20 threshold check)
        similarity_score = round(2 * hit["_score"] - 1, 2)
        if similarity_score < min_cosine_similarity:
            return None

        source = hit["_source"]

        # Only process results with "llm" field
        if "llm" not in source:
            logger.warning(f"Skipping result without 'llm' field: {hit.get('_id', 'unknown')}")
            return None

        # Parse the stored VisionLLM structure
        stored_llm_data = source.get("llm", {}) or {}
        queries_data = stored_llm_data.get("queries", [])
        if not isinstance(queries_data, list):
            queries_data = []

        # Extract fields from stored data
        sensor_data = source.get("sensor", {}) or {}
        sensor_info = sensor_data.get("info", {}) or {}
        video_path = sensor_info.get("path", "") or sensor_info.get("url", "")
        sensor_id_raw = sensor_data.get("id", "")  # Could be sensor name (RTSP) or UUID (video_file)

        # ============================================================================================
        # Extract stream_id (UUID) - ALWAYS return UUID when available
        # ============================================================================================
        # RTSP stream: sensor.id = sensor name (e.g., "warehouse_sample_test")
        #              sensor.info.path = "rtsp://.../live/ea965db6-a8d4-4108-9917-bf820eeb8a98"
        #              sensor.stream_id = UUID (if present)
        #              → Extract UUID from path (always available for RTSP)
        # Video file:  sensor.id = UUID (e.g., "8fce43a6-1c35-4d6a-b6e3-391c42090a87")
        #              sensor.info.path = "/tmp/assets/8fce43a6-.../boxcart_1.mp4"
        #              sensor.stream_id = UUID (if present)
        #              → Extract UUID from path, or use sensor.id/sensor.stream_id if it's a UUID
        # ============================================================================================
        stream_id = None

        # Priority 1: Check sensor.stream_id field (if present, it's the UUID)
        sensor_stream_id = sensor_data.get("stream_id", "")
        if sensor_stream_id:
            is_uuid = len(sensor_stream_id) == 36 and sensor_stream_id.count("-") == 4
            if is_uuid:
                stream_id = sensor_stream_id
                logger.debug(f"Found UUID in sensor.stream_id: {stream_id}")

        # Priority 2: Extract UUID from sensor.info.path (works for both RTSP and video files)
        if not stream_id and video_path:
            uuid_pattern = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
            uuid_match = re.search(uuid_pattern, video_path, re.IGNORECASE)
            if uuid_match:
                stream_id = uuid_match.group(0)  # UUID found in path
                logger.debug(f"Extracted UUID from path: {stream_id}")

        # Priority 3: If no UUID in path, check if sensor.id is a UUID (video file case)
        if not stream_id:
            is_uuid = len(sensor_id_raw) == 36 and sensor_id_raw.count("-") == 4
            if is_uuid:
                # Video file: sensor.id IS the UUID
                stream_id = sensor_id_raw
                logger.debug(f"Using sensor.id as UUID: {stream_id}")
            else:
                # RTSP stream: sensor.id is sensor name, but UUID should be in path
                # If we reach here, UUID extraction from path failed - log warning
                logger.warning(
                    f"Could not extract UUID from path '{video_path}' or sensor.stream_id for sensor '{sensor_id_raw}'. "
                    "Using sensor.id as stream_id."
                )
                stream_id = (
                    sensor_id_raw  # Fallback: sensor name (fusion_search_rerank will use as-is for attribute_search)
                )

        # Start with response_data from stored query
        response_data: dict[str, Any] = {}
        if queries_data and len(queries_data) > 0:
            stored_query_data = queries_data[0] if isinstance(queries_data[0], dict) else {}
            response_str = stored_query_data.get("response", "{}")
            if response_str:
                try:
                    parsed = json.loads(response_str)
                    if isinstance(parsed, dict):
                        response_data = parsed
                except Exception:
                    pass

        # ============================================================================================
        # Extract video_name - different logic for RTSP vs video_file
        # ============================================================================================
        # RTSP stream: video_name = sensor.id (sensor name, e.g., "warehouse_sample_test")
        # Video file:  video_name = filename from path (e.g., "boxcart_1_20250101_000000_c9b20.mp4")
        # ============================================================================================
        video_name = response_data.get("video_name", "")
        if not video_name:
            is_uuid = len(sensor_id_raw) == 36 and sensor_id_raw.count("-") == 4
            if is_uuid:
                # Video file: extract filename from path
                if video_path:
                    video_name = video_path.split("/")[-1]  # e.g., "boxcart_1_20250101_000000_c9b20.mp4"
                else:
                    video_name = sensor_id_raw  # Fallback to UUID if no path
            else:
                # RTSP stream: use sensor name as video_name
                video_name = sensor_id_raw if sensor_id_raw else ""

        # 2. Extract description from sensor.description only
        description = response_data.get("description", "")
        if not description:
            description = sensor_data.get("description", "")

        # 3. Extract timestamps
        # Extract start_time from source.timestamp
        start_time = response_data.get("start_time", "")
        if not start_time:
            es_timestamp = source.get("timestamp", "")
            if es_timestamp:
                try:
                    es_start_dt = iso8601_to_datetime(str(es_timestamp))
                    start_time = datetime_to_iso8601(es_start_dt)
                except Exception as e:
                    logger.warning(f"Failed to parse timestamp: {e}")
                    start_time = datetime_to_iso8601(BASE_2025)
            else:
                start_time = datetime_to_iso8601(BASE_2025)

        # Extract end_time from source.end
        end_time = response_data.get("end_time", "")
        if not end_time:
            es_end = source.get("end", "")
            if es_end:
                try:
                    es_end_dt = iso8601_to_datetime(str(es_end))
                    end_time = datetime_to_iso8601(es_end_dt)
                except Exception as e:
                    logger.warning(f"Failed to parse end timestamp: {e}")
                    end_time = datetime_to_iso8601(BASE_2025)
            else:
                end_time = datetime_to_iso8601(BASE_2025)

        logger.debug(f"Final timestamps - start_time: {start_time}, end_time: {end_time}, stream_id: {stream_id}")

        # Check if this result is in the exclude_videos list
        # TODO: make this more efficient
        for exclude_video in exclude_videos:
            if (
                sensor_id_raw == exclude_video.get("sensor_id", "")
                and start_time == exclude_video.get("start_timestamp", "")
                and end_time == exclude_video.get("end_timestamp", "")
            ):
                return None

        # 4. Build screenshot URL if stream_id is available
        screenshot_url = ""
        if stream_id:
            screenshot_url = build_screenshot_url(
                config.vst_external_url,
                stream_id,
                start_time,
            )

        return EmbedSearchResultItem(
            video_name=video_name,
            description=description,
            start_time=start_time,
            end_time=end_time,
            sensor_id=stream_id,
            screenshot_url=screenshot_url,
            similarity_score=similarity_score,
        )

    except Exception as e:
        logger.warning(f"Error processing search hit: {e}")
        return None


@register_function(config_type=EmbedSearchConfig, framework_wrappers=[LLMFrameworkEnum.LANGCHAIN])
async def embed_search(config: EmbedSearchConfig, _builder: Builder) -> AsyncGenerator[FunctionInfo]:
    logger.info(f"Embed search config: {config}")
    es_client = AsyncElasticsearch(config.es_endpoint)
    embed_client: EmbedClient = CosmosEmbedClient(config.cosmos_embed_endpoint)

    async def _embed_search(query_input: QueryInput) -> EmbedSearchOutput:
        """Perform embedding search using QueryInput and return EmbedSearchOutput."""

        # Index check and search_index by source_type (before generating embedding)
        es_index_exists = await es_client.indices.exists(index=config.es_index)
        source_type = query_input.source_type
        if source_type == "video_file":
            if not es_index_exists:
                raise ValueError(
                    f"Search index '{config.es_index}' does not exist. "
                    "Please ensure videos have been ingested before searching."
                )
            search_index: str | list[str] = config.es_index
        else:
            # rtsp: if index does not exist, exclude es_index from search_index list
            if es_index_exists:
                search_index = ["mdx-embed-filtered-*", "-" + config.es_index]
            else:
                search_index = ["mdx-embed-filtered-*"]
        logger.info(f"Search index(es): {search_index} (source_type={source_type})")

        # Step 1: Generate embedding
        query_embedding = await _generate_query_embedding(query_input, embed_client)

        # Step 2: Build ES query
        search_query = _build_es_query(query_input, query_embedding, config)

        # Execute ES search
        try:
            response = await es_client.search(index=search_index, body=search_query)
        except ESNotFoundError as e:
            logger.error(f"Elasticsearch index '{search_index}' not found: {e}")
            raise ValueError(
                f"Search index '{search_index}' does not exist. "
                "Please ensure videos have been ingested before searching."
            ) from e

        # Log response
        response_dict = response.body
        logger.info(
            f"ES search response (before processing): {json.dumps(_sanitize_for_logging(response_dict), indent=2)}"
        )

        # Step 3: Process hits in parallel
        hits = response["hits"]["hits"]
        min_sim = float(query_input.params.get("min_cosine_similarity", "0.0"))
        tasks = [_process_search_hit(hit, config, min_sim, query_input.exclude_videos) for hit in hits]
        processed = await asyncio.gather(*tasks)
        results = [r for r in processed if r is not None]

        # Apply top_k limit
        top_k_str = query_input.params.get("top_k", "")
        if top_k_str:
            results = results[: int(top_k_str)]

        logger.info(f"Found {len(results)} videos matching the query")
        logger.info(
            f"Embed search result (after processing): {json.dumps(_sanitize_for_logging(EmbedSearchOutput(query_embedding=query_embedding, results=results).model_dump()), indent=2)}"
        )

        return EmbedSearchOutput(query_embedding=query_embedding, results=results)

    yield FunctionInfo.create(
        single_fn=_embed_search,
        description=_embed_search.__doc__,
        input_schema=QueryInput,
        single_output_schema=EmbedSearchOutput,
        converters=[
            _str_input_converter,
            _chat_request_input_converter,
            _to_str_output,
        ],
    )
