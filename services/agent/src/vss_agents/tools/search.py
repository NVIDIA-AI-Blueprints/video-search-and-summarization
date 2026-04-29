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
import json
import logging
from typing import Any
from typing import Literal
from typing import Union

import aiohttp
from fastapi import HTTPException
from langchain_core.messages import HumanMessage
from langchain_core.messages import SystemMessage
from nat.builder.builder import Builder
from nat.builder.framework_enum import LLMFrameworkEnum
from nat.builder.function_info import FunctionInfo
from nat.cli.register_workflow import register_function
from nat.data_models.api_server import ChatRequest
from nat.data_models.api_server import ChatResponse
from nat.data_models.api_server import ChatResponseChunk
from nat.data_models.api_server import Usage
from nat.data_models.component_ref import FunctionRef
from nat.data_models.component_ref import LLMRef
from nat.data_models.function import FunctionBaseConfig
from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field

from vss_agents.agents.data_models import AgentMessageChunk
from vss_agents.agents.data_models import AgentMessageChunkType
from vss_agents.tools.attribute_search import DEFAULT_BEHAVIOR_INDEX
from vss_agents.tools.embed_search import EmbedSearchOutput
from vss_agents.tools.vst.utils import get_streams_info
from vss_agents.utils.es_client import VSSESClient
from vss_agents.utils.reasoning_utils import get_llm_reasoning_bind_kwargs
from vss_agents.utils.reasoning_utils import get_thinking_tag
from vss_agents.utils.time_convert import datetime_to_iso8601
from vss_agents.utils.time_convert import iso8601_to_datetime
from vss_agents.utils.time_measure import TimeMeasure

logger = logging.getLogger(__name__)

# Prompt template for query decomposition with placeholders
QUERY_DECOMPOSITION_PROMPT = """You are a search query analyzer. Extract structured search parameters from natural language queries.

Available video sources:
{video_sources}

Extract the following parameters from the user query:
- query: The main search description including actions AND attributes (e.g., "person moving with white pants")
- video_sources: List of video source names mentioned (from available sources above, empty list if none mentioned)
- source_type: "rtsp" if referring to live/camera streams, "video_file" if referring to uploaded video files (default: "video_file")
- timestamp_start: Start time in ISO format (e.g., "2025-01-01T13:00:00Z"). Use 2025-01-01 as the base date.
- timestamp_end: End time in ISO format (e.g., "2025-01-01T14:00:00Z"). Use 2025-01-01 as the base date.
- attributes: List of person with attributes, ONLY. Don't include other objects, don't just put "person".
- has_action: REQUIRED boolean. Set to True if the query explicitly mentions an action/event/activity (e.g., running, walking, carrying, pushing, entering, leaving, moving). Set to False if the query only describes visual/physical attributes (what someone/something LOOKS LIKE) without any action. Examples: "person" → false, "person walking" → true, "red car" → false, "person carrying box" → true, "forklift" → false.
- object_ids: List of integer object IDs if explicitly mentioned in the query (e.g., "find object 5" → [5], "search for objects 10, 20" → [10, 20]). null if no object IDs are mentioned.
- top_k: Number of results to return (integer, only if explicitly mentioned, e.g., "top 5", "first 10")
- min_cosine_similarity: Minimum similarity threshold between -1.0 and 1.0 (e.g., "highly similar" = 0.8, "somewhat similar" = 0.5, "exact match" = 0.9, "any match" = -1.0)

Examples:
{few_shot_examples}

Return ONLY a valid JSON object with the extracted parameters. If a parameter cannot be determined, omit it or use null.

User query: {user_query}"""

# Default few-shot examples for query decomposition
DEFAULT_FEW_SHOT_EXAMPLES = """Example 1:
User query: "Find a man pushing a cart wearing a beige shirt between 1 pm and 2 pm at Endeavor heart"
Output: {{"query": "man pushing cart wearing beige shirt", "video_sources": ["Endeavor heart"], "source_type": "rtsp", "timestamp_start": "2025-01-01T13:00:00Z", "timestamp_end": "2025-01-01T14:00:00Z", "attributes": ["person wearing beige shirt"], "has_action": true}}

Example 2:
User query: "Find people running near Building A camera from 9am to 10am"
Output: {{"query": "people running", "video_sources": ["Building A"], "source_type": "rtsp", "timestamp_start": "2025-01-01T09:00:00Z", "timestamp_end": "2025-01-01T10:00:00Z", "has_action": true}}

Example 3:
User query: "Search for a woman with a blue backpack walking"
Output: {{"query": "woman walking with blue backpack", "video_sources": [], "source_type": "video_file", "attributes": ["woman with blue backpack"], "has_action": true}}

Example 4:
User query: "Find delivery truck at warehouse entrance between 2pm and 4pm"
Output: {{"query": "delivery truck at warehouse entrance", "video_sources": ["warehouse entrance"], "source_type": "rtsp", "timestamp_start": "2025-01-01T14:00:00Z", "timestamp_end": "2025-01-01T16:00:00Z", "has_action": false}}

Example 5:
User query: "Person wearing red jacket and blue jeans carrying a box"
Output: {{"query": "person wearing red jacket and blue jeans carrying box", "video_sources": [], "source_type": "video_file", "attributes": ["person wearing red jacket and blue jeans"], "has_action": true}}

Example 7:
User query: "person with long wavy hair wearing white sneakers"
Output: {{"query": "person with long wavy hair wearing white sneakers", "video_sources": [], "source_type": "video_file", "attributes": ["person with long wavy hair wearing white sneakers"], "has_action": false}}

Example 8:
User query: "Person in white t-shirt and black leggings running out of store with stolen items"
Output: {{"query": "person in white t-shirt and black leggings running out of store with stolen items", "video_sources": [], "source_type": "video_file", "attributes": ["person in white t-shirt and black leggings"], "has_action": true}}

Example 9:
User query: "search for object ids 5, 6"
Output: {{"query": "object ids 5, 6", "object_ids": [5, 6], "has_action": false}}

Example 10:
User query: "find more objects like object 42 near warehouse entrance"
Output: {{"query": "objects like object 42 near warehouse entrance", "object_ids": [42], "video_sources": ["warehouse entrance"], "has_action": false}}"""


class DecomposedQuery(BaseModel):
    """Result of query decomposition."""

    query: str = Field(default="", description="The main search query")
    video_sources: list[str] = Field(default_factory=list, description="List of video source names")
    source_type: str = Field(default="video_file", description="Type of source: 'rtsp' or 'video_file'")
    timestamp_start: str | None = Field(default=None, description="Start timestamp in ISO format")
    timestamp_end: str | None = Field(default=None, description="End timestamp in ISO format")
    attributes: list[str] = Field(default_factory=list, description="List of attributes to filter by")
    has_action: bool | None = Field(
        default=None,
        description="True if query contains an action/event/activity, False if only visual/physical attributes",
    )
    object_ids: list[int] | None = Field(
        default=None, description="List of integer object IDs if explicitly mentioned in the query"
    )
    top_k: int | None = Field(default=None, description="Number of results to return")
    min_cosine_similarity: float | None = Field(default=None, description="Minimum similarity threshold (-1.0 to 1.0)")


async def _run_attribute_only_search(
    attribute_list: list[str],
    search_input: "SearchInput",
    attribute_search_fn: Any,
    top_k: int,
    min_similarity: float | None,
    exclude_videos: list[dict[str, str]] | None = None,
) -> list["SearchResult"]:
    """
    Modular helper function to run attribute-only search.

    Returns list of SearchResult from attribute search in append mode.
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
            "fuse_multi_attribute": False,  # Append mode - no fusion
            "exclude_videos": exclude_videos,
        }

        attribute_results = await attribute_search_fn.ainvoke(attr_params)

        # Convert AttributeSearchResult to SearchResult
        search_results = []
        if attribute_results and isinstance(attribute_results, list):
            from vss_agents.tools.attribute_search import AttributeSearchResult

            validated_results = [
                item if isinstance(item, AttributeSearchResult) else AttributeSearchResult.model_validate(item)
                for item in attribute_results
            ]

            for result in validated_results:
                try:
                    search_result = attribute_result_to_search_result(
                        result,
                    )
                    search_results.append(search_result)
                except Exception as e:
                    logger.warning(f"Failed to convert attribute result: {e}")
                    continue

            # Sort by similarity (descending)
            search_results.sort(key=lambda x: x.similarity, reverse=True)

        return search_results

    except Exception as e:
        logger.error(f"Attribute-only search failed: {e}", exc_info=True)
        return []


def attribute_result_to_search_result(
    attr_result: Any,
    video_name: str | None = None,
    description: str = "",
) -> "SearchResult":
    """
    Convert AttributeSearchResult to SearchResult.

    Args:
        attr_result: AttributeSearchResult instance or dict
        video_name: Optional video name (defaults to sensor_id)
        description: Optional description
    """
    from vss_agents.tools.attribute_search import AttributeSearchResult

    # Validate and convert to AttributeSearchResult if needed
    if isinstance(attr_result, dict):
        validated_result = AttributeSearchResult.model_validate(attr_result)
    elif isinstance(attr_result, AttributeSearchResult):
        validated_result = attr_result
    else:
        validated_result = AttributeSearchResult.model_validate(attr_result)

    metadata = validated_result.metadata

    # Use frame_score if available, otherwise behavior_score
    similarity = (
        float(metadata.frame_score)
        if (metadata.frame_score is not None and metadata.frame_score > 0.0)
        else float(metadata.behavior_score)
    )

    # Use start_time and end_time from metadata (set from behavior embedding timestamps in _build_result).
    # For pure attribute search, these are always from behavior embedding source (timestamp and end fields).
    # When duplicates are merged, they reflect the earliest start and latest end from all duplicates.
    # Fallback to frame_timestamp only if somehow missing (shouldn't happen if source has timestamps).
    start_time = metadata.start_time if metadata.start_time else metadata.frame_timestamp
    end_time = metadata.end_time if metadata.end_time else metadata.frame_timestamp

    # Use video_name from metadata (set to original sensor name before converting sensor_id to UUID)
    result_video_name = video_name or metadata.video_name or metadata.sensor_id

    # Build description with timestamp if not provided
    if not description:
        description = f"Attribute match at {metadata.frame_timestamp}"

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


async def decompose_query(
    user_query: str,
    llm: Any,
    video_file_names: list[str] | None = None,
    video_stream_names: list[str] | None = None,
    few_shot_examples: str | None = None,
) -> DecomposedQuery:
    """
    Decompose a natural language query into structured search parameters using an LLM.

    Args:
        user_query: The natural language query from the user
        llm: The LLM instance to use for decomposition
        video_file_names: Optional list of available video file names
        video_stream_names: Optional list of available video stream names
        few_shot_examples: Optional custom few-shot examples for the prompt

    Returns:
        DecomposedQuery with extracted parameters
    """
    # Build video sources string
    video_sources_parts = []
    if video_file_names:
        video_sources_parts.append(f"Video files: {', '.join(video_file_names)}")
    if video_stream_names:
        video_sources_parts.append(f"Video streams: {', '.join(video_stream_names)}")
    video_sources_str = "\n".join(video_sources_parts) if video_sources_parts else "No specific sources available"
    logger.info(f"Video sources: {video_sources_str}")

    # Use default examples if not provided
    examples = few_shot_examples or DEFAULT_FEW_SHOT_EXAMPLES

    # Format the prompt
    prompt = QUERY_DECOMPOSITION_PROMPT.format(
        video_sources=video_sources_str,
        few_shot_examples=examples,
        user_query=user_query,
    )

    # Add thinking tag to disable reasoning if applicable to llm
    thinking_tag = get_thinking_tag(llm, False)
    system_content = "You are a helpful assistant that extracts search parameters from natural language queries. Return only valid JSON."
    if thinking_tag:
        system_content += f"\n{thinking_tag}"
        logger.debug(f"Added thinking tag to system message: {thinking_tag}")

    # Build messages
    messages = [
        SystemMessage(content=system_content),
        HumanMessage(content=prompt),
    ]

    # Bind LLM with reasoning kwargs if the model supports it
    llm_kwargs = get_llm_reasoning_bind_kwargs(llm, False)
    llm_to_use = llm.bind(**llm_kwargs) if llm_kwargs else llm

    try:
        llm_response = await llm_to_use.ainvoke(messages)
        response_content = llm_response.content if hasattr(llm_response, "content") else str(llm_response)

        # Parse JSON response (handle markdown code blocks)
        response_text = response_content.strip()
        if "```json" in response_text:
            start = response_text.find("```json") + 7
            end = response_text.find("```", start)
            response_text = response_text[start:end].strip() if end != -1 else response_text[start:].strip()
        elif "```" in response_text:
            start = response_text.find("```") + 3
            end = response_text.find("```", start)
            response_text = response_text[start:end].strip() if end != -1 else response_text[start:].strip()

        extracted = json.loads(response_text)

        # Parse top_k if present
        top_k = None
        if extracted.get("top_k") is not None:
            try:
                top_k = int(extracted["top_k"])
            except (ValueError, TypeError):
                logger.debug("Failed to parse top_k value: %s", extracted["top_k"])

        # Parse min_cosine_similarity if present
        min_cosine_similarity = None
        if extracted.get("min_cosine_similarity") is not None:
            try:
                min_cosine_similarity = float(extracted["min_cosine_similarity"])
            except (ValueError, TypeError):
                logger.debug("Failed to parse min_cosine_similarity value: %s", extracted["min_cosine_similarity"])

        # Parse has_action if present
        has_action = None
        if extracted.get("has_action") is not None:
            try:
                has_action = bool(extracted["has_action"])
            except (ValueError, TypeError):
                logger.debug("Failed to parse has_action value: %s", extracted["has_action"])

        # Parse object_ids if present
        object_ids = None
        raw_object_ids = extracted.get("object_ids")
        if raw_object_ids and isinstance(raw_object_ids, list):
            try:
                parsed = [int(oid) for oid in raw_object_ids]
                object_ids = [oid for oid in parsed if oid > 0] or None
            except (ValueError, TypeError):
                logger.debug("Failed to parse object_ids: %s", raw_object_ids)

        return DecomposedQuery(
            query=extracted.get("query", user_query),
            video_sources=extracted.get("video_sources", []) or [],
            source_type=extracted.get("source_type", "video_file") or "video_file",
            timestamp_start=extracted.get("timestamp_start"),
            timestamp_end=extracted.get("timestamp_end"),
            attributes=extracted.get("attributes", []) or [],
            has_action=has_action,
            object_ids=object_ids,
            top_k=top_k,
            min_cosine_similarity=min_cosine_similarity,
        )
    except Exception as e:
        logger.warning(f"Failed to decompose query, using original: {e}")
        return DecomposedQuery(query=user_query)


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
        resolved_sources = []
        for video_source in video_sources:
            stream_id = name_to_uuid.get(video_source)
            if stream_id:
                resolved_sources.append(video_source)
                logger.debug(
                    "Keeping RTSP video source '%s' as sensor name; VST stream UUID is '%s'",
                    video_source,
                    stream_id,
                )
            elif video_source in uuid_to_name:
                sensor_name = uuid_to_name[video_source]
                resolved_sources.append(sensor_name)
                logger.debug("Resolved RTSP stream UUID '%s' to sensor name '%s'", video_source, sensor_name)
            else:
                resolved_sources.append(video_source)
                logger.debug("RTSP video source '%s' not resolved in VST map; keeping original name", video_source)
        return resolved_sources

    resolved_sources = []
    for video_source in video_sources:
        stream_id = name_to_uuid.get(video_source)
        if stream_id:
            resolved_sources.append(stream_id)
            logger.debug("Resolved video source '%s' to UUID '%s'", video_source, stream_id)
        else:
            resolved_sources.append(video_source)
            logger.debug("Video source '%s' not resolved; will use wildcard filter", video_source)
    return resolved_sources


def _apply_weighted_linear_fusion(
    video_data: list[dict[str, Any]],
    w_embed: float,
    w_attribute: float,
) -> list["SearchResult"]:
    """
    Apply weighted linear fusion: (w_embed x embed_score) + (w_attribute x normalised_attribute_score).

    returns list of SearchResult sorted by fusion score (descending)
    """
    reranked_results = []
    for video in video_data:
        embed_score = video["embed_score"]
        attribute_score = video["normalised_attribute_score"]
        fusion_score = w_embed * embed_score + w_attribute * attribute_score

        logger.debug(
            f"Weighted Linear: {video['embed_result'].video_name} - "
            f"embed={embed_score:.3f} (w={w_embed:.2f}), "
            f"attribute={attribute_score:.3f} (w={w_attribute:.2f}), fusion_score={fusion_score:.3f}"
        )

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

    # Sort by fusion score (descending)
    reranked_results.sort(key=lambda x: x[0], reverse=True)
    return [result for _, result in reranked_results]


def _apply_rrf_fusion(
    video_data: list[dict[str, Any]],
    rrf_k: int,
    rrf_w: float,
) -> list["SearchResult"]:
    """
    Apply Reciprocal Rank Fusion (RRF): 1/(rank_action + k) + w*normalised_attribute_score.

    returns list of SearchResult sorted by RRF score (descending)
    """
    # Sort by embed_score (descending) to determine rank_action
    sorted_video_data = sorted(video_data, key=lambda x: x["embed_score"], reverse=True)

    reranked_results = []
    for rank, video in enumerate(sorted_video_data, start=1):
        rank_action = rank
        rrf_score = 1.0 / (rank_action + rrf_k) + rrf_w * video["normalised_attribute_score"]

        logger.debug(
            f"RRF: {video['embed_result'].video_name} - "
            f"rank_action={rank_action}, normalised_attribute_score={video['normalised_attribute_score']:.3f}, "
            f"rrf_score={rrf_score:.6f}"
        )

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

    # Sort by RRF score (descending)
    reranked_results.sort(key=lambda x: x[0], reverse=True)
    return [result for _, result in reranked_results]


def _apply_rrf_fusion_with_attribute_rank(
    video_data: list[dict[str, Any]],
    rrf_k: int,
    rrf_w: float,
) -> list["SearchResult"]:
    """
    Apply Reciprocal Rank Fusion (RRF) using both embed and attribute ranks: 1/(rank_embed + k) + w * 1/(rank_attribute + k).

    Sorts videos by both embed_score and attribute_score to determine ranks, then applies RRF formula with both reciprocal ranks.
    The rrf_w parameter weights the attribute rank component.

    returns list of SearchResult sorted by RRF score (descending)
    """
    # Sort by embed_score to determine rank_embed
    sorted_by_embed = sorted(video_data, key=lambda x: x["embed_score"], reverse=True)
    embed_ranks = {id(video): rank for rank, video in enumerate(sorted_by_embed, start=1)}

    # Sort by normalised_attribute_score to determine rank_attribute
    sorted_by_attribute = sorted(video_data, key=lambda x: x["normalised_attribute_score"], reverse=True)
    attribute_ranks = {id(video): rank for rank, video in enumerate(sorted_by_attribute, start=1)}

    reranked_results = []
    for video in video_data:
        rank_embed = embed_ranks[id(video)]
        rank_attribute = attribute_ranks[id(video)]
        rrf_score = 1.0 / (rank_embed + rrf_k) + rrf_w * (1.0 / (rank_attribute + rrf_k))

        logger.debug(
            f"RRF (both ranks): {video['embed_result'].video_name} - "
            f"rank_embed={rank_embed}, rank_attribute={rank_attribute}, "
            f"rrf_score={rrf_score:.6f}"
        )

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

    # Sort by RRF score (descending)
    reranked_results.sort(key=lambda x: x[0], reverse=True)
    return [result for _, result in reranked_results]


async def fusion_search_rerank(
    embed_results: list["SearchResult"],
    attributes: list[str],
    attribute_search_fn: Any,
    vst_internal_url: str | None = None,
    source_type: str = "video_file",
    fusion_method: str = "rrf",
    rrf_k: int = 60,
    rrf_w: float = 0.5,
    w_attribute: float = 0.55,
    w_embed: float = 0.35,
) -> list["SearchResult"]:
    """
    Rerank embed_search results using either Weighted Linear Fusion or Reciprocal Rank Fusion (RRF).

    For each video:
    1. Run attribute_search for each attribute
    2. Compute normalized attribute score (sum of attribute scores / number of attributes searched)
    3. Apply fusion method:
       - Weighted Linear: weighted sum of scores
       - RRF: rank by embed_score, then apply RRF formula

    returns reranked list of SearchResult with fused scores
    """

    logger.info(
        f"{fusion_method.upper()} fusion reranking {len(embed_results)} videos using {len(attributes)} attributes"
    )

    # Prepare attribute search tasks for all embed results (run in parallel)
    async def _get_attribute_results(embed_result: "SearchResult") -> tuple["SearchResult", Any]:
        """Prepare and call attribute search for one embed result."""
        try:
            # Convert ISO timestamp strings to datetime objects
            start_dt = iso8601_to_datetime(embed_result.start_time)
            end_dt = iso8601_to_datetime(embed_result.end_time)

            # If start and end times are the same or end is before/at start (single timestamp or 0-duration clip),
            # expand to ±2.5 seconds for attribute search
            if end_dt <= start_dt:
                original_start = start_dt
                start_dt = original_start - timedelta(seconds=2.5)
                end_dt = original_start + timedelta(seconds=2.5)
                logger.info(
                    f"Extended 0-duration clip to ±2.5 seconds: {embed_result.start_time} -> [{datetime_to_iso8601(start_dt)}, {datetime_to_iso8601(end_dt)}]"
                )

            # Convert stream_id (from embed_result.sensor_id) to sensor_id (sensor name) for attribute_search
            # attribute_search filters by sensor.id.keyword which expects camera names like "warehouse_sample_test"
            filter_sensor_id = ""

            # Try VST conversion if sensor_id exists
            if embed_result.sensor_id and vst_internal_url:
                try:
                    from vss_agents.tools.vst.utils import get_sensor_id_from_stream_id

                    filter_sensor_id = await get_sensor_id_from_stream_id(embed_result.sensor_id, vst_internal_url)
                    if filter_sensor_id != embed_result.sensor_id:
                        logger.info(f"Converted stream_id '{embed_result.sensor_id}' to sensor_id '{filter_sensor_id}'")
                except Exception as e:
                    logger.warning(f"VST conversion failed: {e}. Using fallback")

            # Fallback chain: video_name -> sensor_id -> ""
            if not filter_sensor_id:
                filter_sensor_id = embed_result.video_name or embed_result.sensor_id or ""

            # Call attribute_search once with all attributes (will generate one video with all overlays)
            # Use fuse_multi_attribute=True for fusion path (combines object IDs)
            # Convert sensor_id to video_sources format (supports wildcard matching)
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

    # Run all attribute searches in parallel
    results_list = await asyncio.gather(*[_get_attribute_results(er) for er in embed_results])

    # First pass: collect all scores
    video_data: list[dict[str, Any]] = []

    for embed_result, attribute_results in results_list:
        embed_score = embed_result.similarity

        # Collect similarity scores, screenshot URL, and object IDs from attribute search results
        attribute_scores = []
        attribute_screenshot_url = None
        object_ids = []

        # Process and validate the attribute search result
        if attribute_results and isinstance(attribute_results, list):
            from vss_agents.tools.attribute_search import AttributeSearchResult

            validated_results = [
                item if isinstance(item, AttributeSearchResult) else AttributeSearchResult.model_validate(item)
                for item in attribute_results
            ]
        else:
            validated_results = []

        # Iterate over all returned results (fuse mode may return fewer results than attributes
        # when some attributes have no matches, so we must NOT zip with attributes).
        if validated_results:
            for result in validated_results:
                # Prioritize frame_score, fall back to behavior_score
                frame_score = result.metadata.frame_score
                behavior_score = result.metadata.behavior_score
                score = float(frame_score) if (frame_score is not None and frame_score > 0.0) else float(behavior_score)
                attribute_scores.append(score)

                # Extract object_id from metadata
                object_id = result.metadata.object_id
                if object_id and str(object_id) not in object_ids:
                    object_ids.append(str(object_id))

            # Extract screenshot URL from first result (all results have the same URL)
            attribute_screenshot_url = validated_results[0].screenshot_url or ""

        # Compute normalized attribute score (normalised_attribute_score)
        # Divide by number of attributes searched (not matched) to penalize videos that don't match all attributes
        normalised_attribute_score = sum(attribute_scores) / len(attributes) if len(attributes) > 0 else 0.0

        video_data.append(
            {
                "embed_result": embed_result,
                "embed_score": embed_score,
                "normalised_attribute_score": normalised_attribute_score,
                "screenshot_url": attribute_screenshot_url if attribute_screenshot_url else embed_result.screenshot_url,
                "object_ids": object_ids,
            }
        )

        logger.debug(
            f"Collecting scores: {embed_result.video_name} ({embed_result.start_time} to {embed_result.end_time}), "
            f"embed={embed_score:.3f}, normalised_attribute_score={normalised_attribute_score:.3f} "
            f"({len(attribute_scores)}/{len(attributes)} matched)"
        )

    # Second pass: Apply fusion method
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


_SIMILARITY_RATIO_THRESHOLD = 0.9


def _merge_consecutive_results(results: list["SearchResult"]) -> list["SearchResult"]:
    """Merge consecutive/overlapping SearchResult chunks from the same sensor.

    Merging rules:
    - video_name, sensor_id, description, screenshot_url: taken from the first chunk
    - start_time / end_time: span of the merged window
    - similarity: mean across merged chunks
    - object_ids: concatenated (preserving order, deduplicating)
    - critic_result: always None (merge runs before the critic)
    """
    if not results:
        return results

    # Results without timestamps (or with malformed ones) cannot be time-merged; pass them through as-is
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

    # Group by sensor_id
    by_sensor: dict[str, list[SearchResult]] = {}
    for result in timestamped:
        by_sensor.setdefault(result.sensor_id, []).append(result)

    for sensor_id, sensor_results in by_sensor.items():
        # Sort by start_time within each sensor group
        sorted_results = sorted(sensor_results, key=lambda r: r.start_time)

        # Build contiguous groups of overlapping/adjacent chunks
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
                # Overlapping or adjacent with compatible similarity — extend the current group
                result_end_dt = iso8601_to_datetime(result.end_time)
                if result_end_dt > group_end_dt:
                    group_end_dt = result_end_dt
                group_chunks.append(result)
            else:
                groups.append(group_chunks)
                group_chunks = [result]
                group_end_dt = iso8601_to_datetime(result.end_time)
        groups.append(group_chunks)

        # Collapse each group into a single SearchResult
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

    # Sort by descending similarity so best matches come first
    merged.sort(key=lambda r: r.similarity, reverse=True)
    return merged


# ===== SHARED CORE SEARCH LOGIC =====
# This function contains the core search logic used by both search.py and search_agent.py
# Uses async generator pattern for real-time streaming support


async def execute_core_search(
    search_input: "SearchInput",
    embed_search: Any,  # Function reference for embed search
    agent_llm: Any | None,  # LLM for query decomposition
    config: Any,  # SearchConfig or similar config object
    builder: Builder,  # Builder for getting tools
    attribute_search_fn: Any
    | None = None,  # Function reference for attribute search (can be loaded from builder if None)
    critic_agent: Any | None = None,  # Optional critic agent
) -> AsyncGenerator[Union[AgentMessageChunk, "SearchOutput"]]:
    """
    Core search execution logic shared by search.py and search_agent.py.

    This is an async generator that yields progress updates, then the final SearchOutput.
    For non-streaming use, use execute_core_search_wrapper() wrapper.

    This function implements the three-path architecture:
    1. Attribute-only search (if has_action=False and attributes exist)
    2. Embed-only search (if no attributes)
    3. Fusion search (if has_action=True and attributes exist, with confidence threshold check)

    Args:
        search_input: SearchInput with query and filters
        embed_search: Function reference for embed search
        agent_llm: LLM for query decomposition (if agent_mode=True)
        config: Config object with search settings (must have: attribute_search_tool, use_attribute_search,
                embed_confidence_threshold, vst_internal_url, fusion_method, rrf_k, rrf_w, w_attribute, w_embed)
        builder: Builder instance for loading tools
        attribute_search_fn: Optional pre-loaded attribute search function (will be loaded from config if None)
        critic_agent: Optional critic agent for result verification

    Yields:
        AgentMessageChunk for progress updates, then SearchOutput as final result
    """
    decomposed: DecomposedQuery | None = None
    original_query = search_input.query
    if search_input.agent_mode and agent_llm:
        try:
            yield AgentMessageChunk(
                type=AgentMessageChunkType.TOOL_CALL, content=f"Decomposing query: '{search_input.query}'"
            )

            # Fetch sensor names from VST based on source_type
            video_file_names: list[str] = []
            video_stream_names: list[str] = []
            name_to_uuid: dict[str, str] = {}
            try:
                vst_url = getattr(config, "vst_internal_url", None)
                if vst_url:
                    streams_info = await get_streams_info(vst_url)
                    source_type = getattr(search_input, "source_type", None)
                    # Classify streams by source_type AND build the name→UUID mapping
                    # used for two-tier video source filtering. Only include names
                    # matching the query's source_type so names that collide across
                    # RTSP and video_file sources don't overwrite each other.
                    for stream_id, stream_info in streams_info.items():
                        name = stream_info.get("name", "")
                        url = stream_info.get("url", "")
                        if not name:
                            continue
                        is_rtsp = url and url.startswith("rtsp://")
                        if source_type == "rtsp" and is_rtsp:
                            video_stream_names.append(name)
                            name_to_uuid[name] = stream_id
                        elif source_type == "video_file" and not is_rtsp:
                            video_file_names.append(name)
                            name_to_uuid[name] = stream_id
                        elif source_type is None:
                            # source_type unspecified — include all, matching existing
                            # behavior for the name lists
                            name_to_uuid[name] = stream_id
                            if is_rtsp:
                                video_stream_names.append(name)
                            else:
                                video_file_names.append(name)
                    logger.info(
                        f"Fetched sensor names from VST (source_type={source_type}): "
                        f"{len(video_file_names)} video files, {len(video_stream_names)} streams"
                    )
            except (aiohttp.ClientError, TimeoutError) as e:
                logger.warning(f"Network error fetching sensor names from VST ({vst_url}): {e}")
            except (ValueError, KeyError, TypeError) as e:
                logger.warning(f"Failed to parse VST streams response: {e}")
            except Exception as e:
                logger.exception(f"Unexpected error fetching sensor names from VST: {e}")

            with TimeMeasure("search: query decomposition"):
                decomposed = await decompose_query(
                    user_query=search_input.query,
                    llm=agent_llm,
                    video_file_names=video_file_names or None,
                    video_stream_names=video_stream_names or None,
                )

            if decomposed.query:
                search_input.query = decomposed.query
            if decomposed.video_sources:
                search_input.video_sources = decomposed.video_sources
            # Resolve video sources to the identifier expected by the selected source index.
            # Video files filter by UUID; RTSP indices filter by camera/sensor name.
            if search_input.video_sources and name_to_uuid:
                search_input.video_sources = _resolve_video_sources_for_search(
                    video_sources=search_input.video_sources,
                    name_to_uuid=name_to_uuid,
                    source_type=search_input.source_type,
                )
            if decomposed.timestamp_start:
                try:
                    search_input.timestamp_start = iso8601_to_datetime(decomposed.timestamp_start)
                except Exception as e:
                    logger.warning(f"Failed to parse decomposed timestamp_start: {e}")
            if decomposed.timestamp_end:
                try:
                    search_input.timestamp_end = iso8601_to_datetime(decomposed.timestamp_end)
                except Exception as e:
                    logger.warning(f"Failed to parse decomposed timestamp_end: {e}")
            if decomposed.top_k is not None:
                search_input.top_k = decomposed.top_k
            if decomposed.min_cosine_similarity is not None:
                search_input.min_cosine_similarity = decomposed.min_cosine_similarity

            # Yield decomposition summary
            decomp_summary: dict[str, Any] = {
                "refined_query": decomposed.query or search_input.query,
                "attributes": decomposed.attributes or [],
            }
            if decomposed.timestamp_start:
                decomp_summary["timestamp_start"] = decomposed.timestamp_start
            if decomposed.timestamp_end:
                decomp_summary["timestamp_end"] = decomposed.timestamp_end
            if decomposed.top_k is not None:
                decomp_summary["top_k"] = decomposed.top_k
            if decomposed.video_sources:
                decomp_summary["video_sources"] = decomposed.video_sources
            if decomposed.object_ids:
                decomp_summary["object_ids"] = decomposed.object_ids

            yield AgentMessageChunk(
                type=AgentMessageChunkType.THOUGHT,
                content=f"Query decomposed: {json.dumps(decomp_summary)}",
            )

            logger.debug(f"Query decomposed: {decomposed.model_dump()}")
        except Exception as e:
            logger.warning(f"Query decomposition failed, using original query: {e}")
            yield AgentMessageChunk(
                type=AgentMessageChunkType.ERROR,
                content=f"Decomposition failed, using original query: {e!s}",
            )

    # ===== OBJECT_ID PATH: Direct behavior KNN (bypasses embed_search + fusion) =====
    if decomposed and decomposed.object_ids:
        if not getattr(config, "behavior_es_endpoint", None):
            raise ValueError("behavior_es_endpoint config is required for object_id re-search")

        top_k = search_input.top_k if search_input.top_k is not None else config.default_max_results

        yield AgentMessageChunk(
            type=AgentMessageChunkType.TOOL_CALL,
            content=f"Searching for similar objects to: {decomposed.object_ids}",
        )

        from vss_agents.tools.attribute_search import AttributeSearchResult
        from vss_agents.tools.attribute_search import enrich_attribute_results
        from vss_agents.tools.attribute_search import search_by_object_embedding

        es = await VSSESClient.get_es_client(es_endpoint=config.behavior_es_endpoint)

        async def _safe_object_search(oid: int) -> list[AttributeSearchResult]:
            try:
                return await search_by_object_embedding(
                    object_id=str(oid),
                    behavior_index=config.behavior_index,
                    es=es,
                    top_k=top_k,
                    min_similarity=search_input.min_cosine_similarity or 0.0,
                    video_sources=search_input.video_sources if search_input.video_sources else None,
                    timestamp_start=search_input.timestamp_start,
                    timestamp_end=search_input.timestamp_end,
                    source_type=search_input.source_type,
                )
            except Exception as e:
                logger.warning(f"Object ID {oid} search failed: {e}")
                return []

        with TimeMeasure("search: object_ids behavior KNN"):
            results_list = await asyncio.gather(*[_safe_object_search(oid) for oid in decomposed.object_ids])

        all_results: list[AttributeSearchResult] = []
        for obj_results in results_list:
            all_results.extend(obj_results)

        # Deduplicate by object_id, keep highest similarity
        seen: dict = {}
        for r in all_results:
            key = str(r.metadata.object_id)
            if key not in seen or r.metadata.behavior_score > seen[key].metadata.behavior_score:
                seen[key] = r
        attr_results = sorted(seen.values(), key=lambda r: r.metadata.behavior_score, reverse=True)[:top_k]

        vst_url = getattr(config, "vst_internal_url", None)
        await enrich_attribute_results(attr_results, vst_url)

        search_results = [attribute_result_to_search_result(r) for r in attr_results]
        result_count = len(search_results)
        yield AgentMessageChunk(
            type=AgentMessageChunkType.THOUGHT,
            content=f"Found {result_count} similar object{'s' if result_count != 1 else ''}",
        )
        yield SearchOutput(data=search_results, search_messages=[])
        return

    # ===== SETUP COMMON QUERY PARAMETERS (used by all execution paths) =====
    top_k = search_input.top_k if search_input.top_k is not None else config.default_max_results
    original_top_k = top_k
    top_k = top_k * 2
    min_similarity = search_input.min_cosine_similarity

    # Build query_params for embed_search (used by embed-only and fusion paths)
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

    # Extract attributes list and check if attribute-only (used by both attribute-only and fusion paths)
    attribute_list = []
    is_attribute_only = False
    if search_input.agent_mode and agent_llm and decomposed and decomposed.attributes:
        attribute_list = decomposed.attributes

        # Prune single-word attributes (keep multi-word attributes even if connected with hyphens or dots)
        def _is_single_word(attr: str) -> bool:
            """Check if attribute is a single word (no spaces, hyphens, or dots)."""
            # Remove leading/trailing whitespace
            attr = attr.strip()
            # If it contains spaces, hyphens, or dots, it's multi-word
            return " " not in attr and "-" not in attr and "." not in attr

        original_count = len(attribute_list)
        attribute_list = [attr for attr in attribute_list if not _is_single_word(attr)]
        if len(attribute_list) < original_count:
            pruned_count = original_count - len(attribute_list)
            logger.info(f"Pruned {pruned_count} single-word attribute(s). Remaining attributes: {attribute_list}")

        logger.info(f"Extracted attributes: {attribute_list}")
        # Check if attribute-only: has_action=False means attribute-only, otherwise use fusion path
        # If has_action is None, and attributes exist, default to attribute-only
        if decomposed.has_action is not None:
            is_attribute_only = not decomposed.has_action
        elif attribute_list:  # If has_action is None but attributes exist, treat as attribute-only
            is_attribute_only = True

    # ===== EXECUTION FLOW: Three distinct paths =====
    search_results = []
    do_search = True
    # Keep track of confirmed and rejected results to avoid re-running the critic agent on the known results
    rejected_results = set()
    confirmed_results = set()
    iteration_num = 0
    # Collect non-fatal messages from pipeline steps to surface in the final response
    search_messages: list[str] = []
    # Persist critic verdicts across search iterations so re-appearing results keep their annotations
    persistent_critic_results: dict = {}

    while do_search and iteration_num < config.search_max_iterations:
        iteration_num += 1
        do_search = False
        logger.info(f"[Search] Running embed search iteration {iteration_num}")

        # Use computed top_k (already defaults to config.default_max_results if None)
        query_params["top_k"] = str(top_k)

        query_input_json = json.dumps({"params": query_params, "source_type": search_input.source_type})
        # PATH 1: Attribute-only search (attribute_list not empty AND is_attribute_only=True)
        logger.debug(
            f"is_attribute_only: {is_attribute_only}, attribute_list: {attribute_list}, config.attribute_search_tool: {config.attribute_search_tool}"
        )
        if is_attribute_only and attribute_list and config.attribute_search_tool:
            logger.info("EXECUTION PATH: Attribute-only search (no embed, append mode)")

            yield AgentMessageChunk(
                type=AgentMessageChunkType.TOOL_CALL,
                content=f"Running attribute-only search with {len(attribute_list)} attributes",
            )

            # Load attribute_search tool if not provided
            if attribute_search_fn is None:
                attribute_search_fn = await builder.get_function(config.attribute_search_tool)

            # Use modular helper function
            with TimeMeasure("search: attribute-only search"):
                search_results = await _run_attribute_only_search(
                    attribute_list=attribute_list,
                    search_input=search_input,
                    attribute_search_fn=attribute_search_fn,
                    top_k=original_top_k,
                    min_similarity=min_similarity,
                )

            yield AgentMessageChunk(
                type=AgentMessageChunkType.THOUGHT,
                content=f"Found {len(search_results)} results from attribute-only search",
            )

        # PATH 2 & 3: Embed search first
        else:
            # Step 1: Run embed_search using query_input_json set up above (common for both paths)
            logger.info("EXECUTION PATH: Embed search")

            yield AgentMessageChunk(
                type=AgentMessageChunkType.TOOL_CALL, content=f"Running embed search with query: '{search_input.query}'"
            )

            try:
                with TimeMeasure("search: embed search"):
                    embed_search_output = await embed_search.ainvoke(query_input_json)
            except ValueError as e:
                error_msg = str(e)
                logger.error(f"Embed search failed: {error_msg}")
                yield AgentMessageChunk(type=AgentMessageChunkType.ERROR, content=f"Embed search failed: {error_msg}")
                raise HTTPException(status_code=404, detail=error_msg) from e
            except Exception as e:
                error_msg = str(e)
                status_code = 500
                if hasattr(e, "status_code"):
                    status_code = e.status_code
                elif hasattr(e, "meta") and hasattr(e.meta, "status"):
                    status_code = e.meta.status
                elif len(e.args) > 0 and isinstance(e.args[0], int):
                    status_code = e.args[0]
                logger.error(f"Unexpected error in embed search: {error_msg}", exc_info=True)
                yield AgentMessageChunk(type=AgentMessageChunkType.ERROR, content=f"Embed search failed: {error_msg}")
                raise HTTPException(status_code=status_code, detail=f"Search error: {error_msg}") from e

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

            # Check embed confidence threshold: if all results below threshold, fallback to pure attribute search (like PATH 1)
            if search_results and attribute_list and config.attribute_search_tool:
                max_embed_score = max((r.similarity for r in search_results), default=0.0)
                if max_embed_score < config.embed_confidence_threshold:
                    logger.info(
                        f"Embed search confidence low (max_score={max_embed_score:.3f} < threshold={config.embed_confidence_threshold:.3f}). "
                        f"Falling back to pure attribute-only search (like PATH 1)."
                    )

                    yield AgentMessageChunk(
                        type=AgentMessageChunkType.THOUGHT,
                        content=f"Embed confidence low ({max_embed_score:.3f}), falling back to attribute-only search",
                    )

                    # Load attribute_search tool if not provided
                    if attribute_search_fn is None:
                        attribute_search_fn = await builder.get_function(config.attribute_search_tool)

                    # Fallback to pure attribute-only search (same as PATH 1)
                    with TimeMeasure("search: attribute-only fallback"):
                        search_results = await _run_attribute_only_search(
                            attribute_list=attribute_list,
                            search_input=search_input,
                            attribute_search_fn=attribute_search_fn,
                            top_k=top_k,
                            min_similarity=min_similarity,
                        )

                    yield AgentMessageChunk(
                        type=AgentMessageChunkType.THOUGHT,
                        content=f"Found {len(search_results)} results from attribute-only search",
                    )
                # PATH 3 : If fusion search (embed confidence is HIGH and attribute_list exists), rerank results using fusion_search
                elif (
                    config.use_attribute_search
                    and len(search_results) > 0
                    and max_embed_score >= config.embed_confidence_threshold  # Only fuse if embed confidence is high
                ):
                    try:
                        logger.info("EXECUTION PATH: Fusion Search - Attribute search followed by Embed search")

                        yield AgentMessageChunk(
                            type=AgentMessageChunkType.TOOL_CALL,
                            content=f"Running fusion reranking with attributes: {attribute_list}",
                        )

                        # Load attribute_search tool if not provided
                        if attribute_search_fn is None:
                            attribute_search_fn = await builder.get_function(config.attribute_search_tool)

                        # Call fusion_search utility to rerank results
                        logger.info(
                            f"Using {len(attribute_list)} LLM-extracted attributes for reranking: {attribute_list}"
                        )

                        with TimeMeasure("search: fusion search rerank"):
                            reranked_results = await fusion_search_rerank(
                                embed_results=search_results,
                                attributes=attribute_list,
                                attribute_search_fn=attribute_search_fn,
                                vst_internal_url=config.vst_internal_url,
                                source_type=search_input.source_type,  # Pass source_type for index selection
                                fusion_method=config.fusion_method,
                                rrf_k=config.rrf_k,
                                rrf_w=config.rrf_w,
                                w_attribute=config.w_attribute,
                                w_embed=config.w_embed,
                            )

                        # Use reranked results for critic verification if enabled
                        search_results = reranked_results

                        # Yield fusion completion message (success)
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
                        # Fall through to return original embed_search results

        # Percentage-based filtering: keep results with similarity >= max_similarity * top_percent_filter
        top_pct = getattr(config, "top_percent_filter", None)
        if top_pct and 0 < top_pct < 1.0 and search_results:
            max_sim = max(r.similarity for r in search_results)
            sim_threshold = max_sim * top_pct
            before_count = len(search_results)
            search_results = [r for r in search_results if r.similarity >= sim_threshold]
            logger.info(
                f"Top-percent filter: kept {len(search_results)}/{before_count} results "
                f"(similarity >= {sim_threshold:.4f}, i.e. {top_pct * 100:.0f}% of max {max_sim:.4f})"
            )

        # Merge consecutive chunks from the same sensor into single results
        search_results = _merge_consecutive_results(search_results)

        # Step 3: If critic enabled and configured, verify results with VLM
        if (
            config.enable_critic
            and search_input.agent_mode
            and search_input.use_critic
            and critic_agent
            and search_results
        ):
            try:
                from vss_agents.agents.critic_agent import CriticAgentResult
                from vss_agents.agents.critic_agent import VideoInfo
                from vss_agents.agents.critic_agent import VideoResult as CriticVideoResult

                critic_results: dict[VideoInfo, CriticVideoResult] = {}

                yield AgentMessageChunk(
                    type=AgentMessageChunkType.THOUGHT,
                    content=f"Verifying {len(search_results)} results with critic agent",
                )

                logger.info(f"[Search] Calling critic agent to verify {len(search_results)} results")

                # Call critic agent - use screenshot_url as video_url for critic
                search_videos: list[VideoInfo] = []
                for result in search_results:
                    info = VideoInfo(
                        sensor_id=result.sensor_id,
                        start_timestamp=result.start_time,
                        end_timestamp=result.end_time,
                    )
                    if info not in confirmed_results and info not in rejected_results:
                        search_videos.append(info)
                if len(search_videos) > 0:
                    critic_input = {"query": original_query, "videos": search_videos}
                    logger.debug(f"[Search] Critic agent input: {critic_input}")
                    with TimeMeasure("search: critic agent verification"):
                        critic_output = await critic_agent.ainvoke(critic_input)
                    logger.debug(f"[Search] Critic output: {critic_output}")
                    critic_results = {result.video_info: result for result in critic_output.video_results}

                    for info, video_result in critic_results.items():
                        match video_result.result:
                            case CriticAgentResult.CONFIRMED:
                                confirmed_results.add(info)
                            case CriticAgentResult.REJECTED:
                                rejected_results.add(info)
                                top_k += 1
                                do_search = True
                            case CriticAgentResult.UNVERIFIED:
                                logger.warning(f"[Search] Unverified result for video {info.sensor_id}")

                    # If all results are unverified (e.g. VLM is down), stop re-searching
                    # and return results without critic verification.
                    if critic_results and all(
                        r.result == CriticAgentResult.UNVERIFIED for r in critic_results.values()
                    ):
                        logger.warning(
                            "[Search] All critic results unverified (VLM may be down). "
                            "Returning results without verification."
                        )
                        msg = "VLM verification unavailable. Returning search results without critic verification."
                        search_messages.append(msg)
                        yield AgentMessageChunk(
                            type=AgentMessageChunkType.THOUGHT,
                            content=msg,
                        )
                        do_search = False

                    logger.info(f"[Search] rejected_results: {rejected_results}")

                    for info, vr in critic_results.items():
                        persistent_critic_results[info] = CriticResult(
                            result=vr.result.value,
                            criteria_met=vr.criteria_met or {},
                        )

                # Annotate each search result with its critic verdict
                for result in search_results:
                    info = VideoInfo(
                        sensor_id=result.sensor_id,
                        start_timestamp=result.start_time,
                        end_timestamp=result.end_time,
                    )
                    if info in persistent_critic_results:
                        result.critic_result = persistent_critic_results[info]

                # Yield critic results summary
                verified_count = sum(1 for vr in critic_results.values() if vr.result == CriticAgentResult.CONFIRMED)
                unverified_count = sum(1 for vr in critic_results.values() if vr.result == CriticAgentResult.UNVERIFIED)

                yield AgentMessageChunk(
                    type=AgentMessageChunkType.THOUGHT,
                    content=f"Critic verification complete: {verified_count}/{len(critic_results)} results verified, {unverified_count}/{len(critic_results)} results unverified",
                )

            except Exception as e:
                logger.error(f"[Search] Error calling critic agent: {e}", exc_info=True)
                msg = "Critic verification unavailable (VLM may be down). Returning results without verification."
                search_messages.append(msg)
                yield AgentMessageChunk(
                    type=AgentMessageChunkType.THOUGHT,
                    content=msg,
                )

    # Yield final results summary
    result_count = len(search_results)
    yield AgentMessageChunk(
        type=AgentMessageChunkType.THOUGHT,
        content=f"Found {result_count} result{'s' if result_count != 1 else ''}",
    )

    # Yield final result, truncated to original top_k to undo any critic-loop inflation
    if original_top_k is not None:
        search_results = search_results[:original_top_k]

    yield SearchOutput(data=search_results, search_messages=search_messages)


async def execute_core_search_wrapper(
    search_input: "SearchInput",
    embed_search: Any,
    agent_llm: Any | None,
    config: Any,
    builder: Builder,
    attribute_search_fn: Any | None = None,
    critic_agent: Any | None = None,
) -> "SearchOutput":
    """
    Wrapper for execute_core_search that collects all progress updates and returns only the final result.
    Used by search.py for non-streaming search.
    """
    async for update in execute_core_search(
        search_input=search_input,
        embed_search=embed_search,
        agent_llm=agent_llm,
        config=config,
        builder=builder,
        attribute_search_fn=attribute_search_fn,
        critic_agent=critic_agent,
    ):
        if isinstance(update, SearchOutput):
            return update
        # Ignore AgentMessageChunk updates (progress updates) for non-streaming mode
    # Should never reach here, but return empty result if we do
    return SearchOutput(data=[])


class SearchConfig(FunctionBaseConfig, name="search"):
    """Configuration for the Search tool."""

    embed_search_tool: FunctionRef = Field(
        ...,
        description="The function reference of the embed search tool to use.",
    )

    attribute_search_tool: FunctionRef | None = Field(
        default=None,
        description="Optional: The function reference of the attribute search tool. Used for fusion reranking when use_attribute_search is enabled.",
    )

    embed_confidence_threshold: float = Field(
        default=0.2,
        description="Minimum embed search similarity threshold. If all embed results are below this threshold, fallback to attribute-only search (if attributes exist).",
    )

    agent_mode_llm: LLMRef = Field(
        ...,
        description="The name of the LLM to use for the search tool to analyze/decompose the input query and fill in parameters if agent_mode is True",
    )

    agent_mode_prompt: str = Field(
        default=QUERY_DECOMPOSITION_PROMPT,
        description="Prompt for the agent(LLM) to analyze/decompose the input query and fill in parameters if agent_mode is True",
    )

    use_attribute_search: bool = Field(
        default=False,
        description="If True and attribute_search_tool is configured, performs multi-attribute object-level search using extracted attributes from query decomposition. Requires agent_mode=True. (internal config, not exposed to user)",
    )

    vst_internal_url: str = Field(
        ...,
        description="The internal VST URL for stream_id to sensor_id conversion in fusion reranking.",
    )

    critic_agent: FunctionRef | None = Field(
        default=None,
        description="""Optional critic agent to verify search results with VLM.
        The critic agent will remove any results that do not match the query. Requires agent_mode=True.""",
    )

    default_max_results: int = Field(
        default=10,
        description="Maximum number of results to return. Used as the default top_k when not specified and as a cap when top_k is too high.",
    )

    enable_critic: bool = Field(
        default=False,
        description="Configuration flag to enable/disable critic agent at a global level.",
    )

    search_max_iterations: int = Field(
        default=1,
        ge=1,
        description="""Maximum number of search iterations when refining search results with critic agent.
        Note, high max iterations can run for a long time. Default is 1.""",
    )

    fusion_method: Literal["weighted_linear", "rrf"] = Field(
        default="rrf",
        description="Fusion method: 'weighted_linear' for weighted linear fusion, 'rrf' for Reciprocal Rank Fusion",
    )

    w_attribute: float = Field(
        default=0.55,
        description="Weight for attribute score in weighted linear fusion (default: 0.55)",
    )

    w_embed: float = Field(
        default=0.35,
        description="Weight for embed score in weighted linear fusion (default: 0.35)",
    )

    rrf_k: int = Field(
        default=60,
        description="RRF constant k for Reciprocal Rank Fusion (default: 60, only used for RRF)",
    )

    rrf_w: float = Field(
        default=0.5,
        description="RRF weight w for attribute cosine similarity in Reciprocal Rank Fusion (default: 0.5, only used for RRF)",
    )

    top_percent_filter: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Score-based filter applied before merging consecutive segments. "
        "Value between 0 and 1.0 — keeps results with similarity >= max_similarity * top_percent_filter. "
        "E.g., 0.9 with max similarity 0.5 keeps results >= 0.45. None or 0 disables filtering.",
    )

    behavior_es_endpoint: str | None = Field(
        default=None,
        description="Elasticsearch endpoint for behavior index (needed for object_id re-search).",
    )

    behavior_index: str = Field(
        default=DEFAULT_BEHAVIOR_INDEX,
        description="Behavior index name for object embedding lookup.",
    )


class SearchInput(BaseModel):
    """Input for the Search tool"""

    model_config = ConfigDict(extra="forbid")

    query: str = Field(
        ...,
        description="Description of the item to search from",
    )

    source_type: Literal["rtsp", "video_file"] = Field(
        ...,
        description="Type of video source: 'rtsp' for live streams or 'video_file' for uploaded video files.",
    )

    video_sources: list[str] | None = Field(
        default=None,
        description="A list of video names to search from. In DevEx, these are VST sensor-names. Defaults to search from all videos.",
    )

    description: str | None = Field(
        default=None,
        description="Description of video's metadata data, for example, the location of the camera, the category of videos. Defaults to match all descriptions.",
    )

    timestamp_start: datetime | None = Field(
        default=None,
        description="Start time of the video, ISO timestamp. Note for uploaded videos, as a convention, we use 2025-01-01T00:00:00 as the start time.",
    )

    timestamp_end: datetime | None = Field(
        default=None,
        description="End time of the video, ISO timestamp. Note for uploaded videos, as a convention, we use 2025-01-01T00:00:00 as the start time.",
    )

    top_k: int | None = Field(
        default=None,
        description="Number of returned videos. If not provided, returns all matching results.",
    )

    min_cosine_similarity: float = Field(
        default=0.0,
        description="Minimum cosine similarity to filter the results. Default is 0.",
    )

    agent_mode: bool = Field(
        ...,
        description="Whether or not backend shall use an agent(LLM) to analyze/decompose the input query and fill in parameters",
    )

    use_critic: bool = Field(
        default=True,
        description="""Request-level flag to enable/disable critic agent for this search request.
        `critic_agent` must be set and `enable_critic` must be True in the config.""",
    )


class CriticResult(BaseModel):
    """Structured verdict from the critic agent for a single search result."""

    result: str = Field(description="Critic verdict: 'confirmed', 'rejected', or 'unverified'.")
    criteria_met: dict[str, bool] = Field(
        default_factory=dict,
        description="Per-criterion evaluation from the critic (e.g. {'person': true, 'walking': false}).",
    )


# FIXME: sensor_id is not the same as stream_id, but for now they have the same value.
# We'll need to revisit this code once we begin to differentiate between them.
class SearchResult(BaseModel):
    """A single search result item"""

    video_name: str = Field(..., description="Name of the video")
    description: str = Field(..., description="Description of the video")
    start_time: str = Field(..., description="Start time of the video in ISO timestamp format")
    end_time: str = Field(..., description="End time of the video in ISO timestamp format")
    sensor_id: str = Field(..., description="Sensor ID (e.g., 21908c9a-bd40-4941-8a2e-79bc0880fb5a)")
    screenshot_url: str = Field(..., description="URL to access the screenshot")
    similarity: float = Field(..., description="Cosine similarity score")
    object_ids: list[str] = Field(
        default_factory=list, description="List of object IDs for video generation (from attribute search)"
    )
    critic_result: CriticResult | None = Field(
        default=None,
        description="Critic agent verdict for this result. None if the critic was not run.",
    )


class SearchOutput(BaseModel):
    """Output for the Search tool"""

    model_config = ConfigDict(extra="forbid")

    data: list[SearchResult] = Field(
        default_factory=list,
        description="List of search results matching the query",
    )
    search_messages: list[str] = Field(
        default_factory=list,
        description="Non-fatal messages from the search pipeline to surface in the response.",
    )


@register_function(config_type=SearchConfig, framework_wrappers=[LLMFrameworkEnum.LANGCHAIN])
async def search(config: SearchConfig, _builder: Builder) -> AsyncGenerator[FunctionInfo]:
    embed_search = await _builder.get_function(config.embed_search_tool)

    agent_llm = None
    if config.agent_mode_prompt:
        agent_llm = await _builder.get_llm(config.agent_mode_llm, wrapper_type=LLMFrameworkEnum.LANGCHAIN)

    # Get critic agent if configured
    critic_agent = None
    if config.critic_agent:
        critic_agent = await _builder.get_function(config.critic_agent)

    async def _search(search_input: SearchInput) -> SearchOutput:
        """
        Search for videos based on a query with optional filters.
        Input:
            search_input: SearchInput

        Returns:
            SearchOutput: Search results matching the query.
        """
        # Use shared core search function (wrapper that collects results)
        return await execute_core_search_wrapper(
            search_input=search_input,
            embed_search=embed_search,
            agent_llm=agent_llm,
            config=config,
            builder=_builder,
            attribute_search_fn=None,  # Will be loaded from config if needed
            critic_agent=critic_agent,
        )

    def _str_input_converter(input: str) -> SearchInput:
        logger.info(f"String input: {input}")
        return SearchInput.model_validate_json(input)

    def _chat_request_input_converter(request: ChatRequest) -> SearchInput:
        logger.info(f"Chat request input content: {request.messages[-1].content}")
        logger.info(f"Chat request input content type: {type(request.messages[-1].content)}")
        return SearchInput.model_validate_json(request.messages[-1].content)

    def _output_converter(output: SearchOutput) -> str:
        logger.info(f"Output: {output}")
        return output.model_dump_json()

    def _chat_response_output_converter(response: SearchOutput) -> ChatResponse:
        logger.info(f"Chat response output: {response}")
        return ChatResponse.from_string(_output_converter(response), usage=Usage())

    def _chat_response_chunk_output_converter(response: SearchOutput) -> ChatResponseChunk:
        logger.info(f"Chat response chunk output: {response}")
        return ChatResponseChunk.from_string(_output_converter(response))

    try:
        yield FunctionInfo.create(
            single_fn=_search,
            description=_search.__doc__,
            input_schema=SearchInput,
            single_output_schema=SearchOutput,
            converters=[
                _str_input_converter,
                _chat_request_input_converter,
                _output_converter,
                _chat_response_output_converter,
                _chat_response_chunk_output_converter,
            ],
        )
    finally:
        try:
            await VSSESClient.close_all()
        except Exception as e:
            logger.warning(f"Error closing ES clients: {e}")
