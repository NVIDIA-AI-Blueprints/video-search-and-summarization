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

"""
Search Agent - Streaming search with agent-think visibility.

This agent implements the full search workflow with streaming and three execution paths:
- Path 1: Attribute-only search (if has_action=False and attributes exist) - Query decomposition → Attribute search
- Path 2: Embed-only search (if no attributes) - Query decomposition → Embed search
- Path 3: Fusion search (if has_action=True and attributes exist) - Query decomposition → Embed search → Fusion reranking (with confidence threshold check)

All paths yield AgentMessageChunk for real-time visibility.
"""

from collections.abc import AsyncGenerator
import json
import logging
from typing import Any
from typing import Literal

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
from pydantic import Field

from vss_agents.agents.data_models import AgentMessageChunk
from vss_agents.agents.data_models import AgentMessageChunkType
from vss_agents.agents.data_models import AgentOutput
from vss_agents.tools.search import SearchInput
from vss_agents.tools.search import SearchOutput
from vss_agents.tools.search import SearchResult
from vss_agents.tools.search import execute_core_search

logger = logging.getLogger(__name__)


def _to_search_results(raw: list) -> list[SearchResult]:
    """Convert raw results (embed/attribute) to SearchResult schema. Used by both sync and streaming."""
    out = []
    for r in raw:
        if isinstance(r, SearchResult):
            out.append(r)
        elif hasattr(r, "model_dump"):
            d = r.model_dump()
            d.setdefault("similarity", d.pop("similarity_score", 0.0))
            d.setdefault("object_ids", [])
            out.append(SearchResult(**d))
        elif isinstance(r, dict):
            d = dict(r)
            d.setdefault("similarity", d.pop("similarity_score", 0.0))
            d.setdefault("object_ids", [])
            out.append(SearchResult(**d))
        else:
            continue
    return out


class SearchAgentInput(BaseModel):
    """Input for search agent."""

    query: str = Field(description="Natural language search query")
    agent_mode: bool = Field(default=True, description="Enable query decomposition")
    use_attribute_search: bool | None = Field(
        default=None, description="Enable fusion reranking with attribute search (overrides config if provided)"
    )
    max_results: int = Field(default=5, description="Maximum number of results to return")
    top_k: int | None = Field(default=None, description="Override top_k for embed search")
    start_time: str | None = Field(default=None, description="Start time filter (ISO format)")
    end_time: str | None = Field(default=None, description="End time filter (ISO format)")
    source_type: Literal["video_file", "rtsp"] = Field(
        default="video_file",
        description="Type of video source: 'video_file' for uploaded videos, 'rtsp' for live/camera streams",
    )
    use_critic: bool = Field(default=True, description="Whether to verify search results with VLM critic agent")


class SearchAgentConfig(FunctionBaseConfig, name="search_agent"):
    """Config for search agent."""

    # Tool references - we'll call these directly
    embed_search_tool: FunctionRef = Field(description="Embed search tool reference")
    attribute_search_tool: FunctionRef | None = Field(
        default=None, description="Attribute search tool for fusion (optional)"
    )
    agent_mode_llm: LLMRef | None = Field(
        default=None, description="LLM for query decomposition (required if agent_mode=True)"
    )

    use_attribute_search: bool = Field(
        default=False,
        description="If True and attribute_search_tool is configured, performs multi-attribute object-level search using extracted attributes from query decomposition. Requires agent_mode=True. (internal config, not exposed to user)",
    )

    default_max_results: int = Field(
        default=10,
        description="Maximum number of results to return. Used as the default top_k when not specified and as a cap when top_k is too high.",
    )

    # Config fields needed for execute_core_search (matching SearchConfig)
    embed_confidence_threshold: float = Field(
        default=0.1,
        description="Minimum embed search similarity threshold. If all embed results are below this threshold, fallback to attribute-only search (if attributes exist).",
    )

    vst_internal_url: str = Field(
        ...,
        description="The internal VST URL for stream_id to sensor_id conversion in fusion reranking.",
    )

    fusion_method: Literal["weighted_linear", "rrf", "rrf_with_attribute_rank"] = Field(
        default="rrf",
        description="Fusion method: 'weighted_linear' for weighted linear fusion, 'rrf' for Reciprocal Rank Fusion using embed rank, 'rrf_with_attribute_rank' for RRF using both embed and attribute ranks",
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

    critic_agent: FunctionRef | None = Field(
        default=None, description="Optional critic agent to verify search results with VLM"
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


# ===== Presentation converters (moved from embed_search.py) =====
# These operate on SearchOutput (from search.py) instead of VisionLLM.


def _to_incidents_output(search_output: SearchOutput) -> str:
    """Format SearchOutput results as incidents JSON wrapped in <incidents> tags."""
    incidents = []

    for result in search_output.data:
        try:
            incident = {
                "Alert Details": {
                    "Alert Triggered": result.video_name,
                    "video_description": result.description,
                    "similarity_score": round(result.similarity, 2),
                    "description": result.description,
                },
                "Clip Information": {
                    "Timestamp": result.start_time,
                    "video_id": result.video_name,
                    "start_time": result.start_time,
                    "end_time": result.end_time,
                },
            }
            incidents.append(incident)
        except Exception as e:
            logger.error(f"Error parsing search result: {e}")
            continue

    incidents_json = {"incidents": incidents}
    json_string = json.dumps(incidents_json, indent=2)
    return f"<incidents>\n{json_string}\n</incidents>"


def _helper_markdown_bullet_list(search_output: SearchOutput) -> str:
    """Convert SearchOutput to markdown bullet list."""
    markdown = "```markdown\n"

    for result in search_output.data:
        try:
            markdown += (
                f"- **Video ID:** `{result.video_name}`\n"
                f"  * Similarity Score: **{result.similarity:.2f}**\n"
                f"  * Description: {result.description}\n"
                f"  * Start Time: {result.start_time}\n"
                f"  * End Time: {result.end_time}\n"
                f"  * Sensor ID: {result.sensor_id}\n"
                f"  * Timestamp: {result.start_time}\n\n"
            )
        except Exception as e:
            logger.error(f"Error formatting search result: {e}")
            continue

    markdown += "```"
    return markdown


def _to_chat_response(search_output: SearchOutput) -> ChatResponse:
    """Convert SearchOutput to ChatResponse."""
    incidents = _to_incidents_output(search_output)
    return ChatResponse.from_string(incidents, usage=Usage())


def _to_chat_response_chunk(search_output: SearchOutput) -> ChatResponseChunk:
    """Convert SearchOutput to ChatResponseChunk."""
    incidents = _to_incidents_output(search_output)
    return ChatResponseChunk.from_string(incidents)


@register_function(config_type=SearchAgentConfig, framework_wrappers=[LLMFrameworkEnum.LANGCHAIN])
async def search_agent(config: SearchAgentConfig, builder: Builder) -> AsyncGenerator[FunctionInfo]:
    """
    Search agent with streaming support - implements full search workflow.

    Calls search components directly (decompose_query, embed_search, attribute_search)
    and streams intermediate steps as AgentMessageChunk.
    """

    # Load function references (for execute_core_search)
    attribute_search_fn = None  # Function reference for fusion_search_rerank
    vst_internal_url = None  # For sensor-id conversion in fusion reranking
    if config.attribute_search_tool:
        # Get function reference for fusion reranker (reuses search.py logic)
        attribute_search_fn = await builder.get_function(config.attribute_search_tool)

        # Get VST URL from attribute_search config for stream_id to sensor_id conversion
        try:
            attr_search_config = await builder.get_config(config.attribute_search_tool)
            if hasattr(attr_search_config, "vst_internal_url"):
                vst_internal_url = attr_search_config.vst_internal_url
                logger.info(f"Retrieved vst_internal_url from attribute_search config: {vst_internal_url}")
            else:
                logger.warning("attribute_search config does not have vst_internal_url attribute")
        except Exception as e:
            logger.warning(f"Could not get VST URL from attribute_search config: {e}")

    agent_llm = None
    if config.agent_mode_llm:
        agent_llm = await builder.get_llm(config.agent_mode_llm, wrapper_type=LLMFrameworkEnum.LANGCHAIN)

    # Get critic agent if configured
    critic_agent = None
    if config.critic_agent:
        critic_agent = await builder.get_function(config.critic_agent)

    logger.info("Search agent initialized with direct tool references")

    async def _execute_search(search_agent_input: SearchAgentInput) -> SearchOutput:
        """Non-streaming search execution. Returns SearchOutput directly."""
        # Convert SearchAgentInput to SearchInput
        from vss_agents.utils.time_convert import iso8601_to_datetime

        timestamp_start = None
        timestamp_end = None
        if search_agent_input.start_time:
            try:
                timestamp_start = iso8601_to_datetime(search_agent_input.start_time)
            except Exception as e:
                logger.warning(f"Failed to parse start_time: {e}")
        if search_agent_input.end_time:
            try:
                timestamp_end = iso8601_to_datetime(search_agent_input.end_time)
            except Exception as e:
                logger.warning(f"Failed to parse end_time: {e}")

        # top_k = input.top_k if input.top_k else default_max_result
        # User's top_k overrides default_max_result (no capping)
        top_k = search_agent_input.top_k if search_agent_input.top_k is not None else config.default_max_results

        search_input = SearchInput(
            query=search_agent_input.query,
            source_type=search_agent_input.source_type,
            top_k=top_k,
            agent_mode=search_agent_input.agent_mode,
            timestamp_start=timestamp_start,
            timestamp_end=timestamp_end,
            use_critic=search_agent_input.use_critic,
        )

        # Get embed_search function reference
        embed_search_fn = await builder.get_function(config.embed_search_tool)

        # Use shared core search function (async generator, collect all progress and return final result)
        search_output = None
        async for update in execute_core_search(
            search_input=search_input,
            embed_search=embed_search_fn,
            agent_llm=agent_llm,
            config=config,
            builder=builder,
            attribute_search_fn=attribute_search_fn,
            critic_agent=critic_agent,
        ):
            if isinstance(update, SearchOutput):
                search_output = update
        search_output = search_output or SearchOutput(data=[])
        return search_output

    def _get_result_name(result: Any) -> str:
        """Helper to extract video name from result (dict or object)."""
        if isinstance(result, dict):
            name = result.get("video_name") or result.get("video_file")
            return str(name) if name is not None else "unknown"
        else:
            name = getattr(result, "video_name", None) or getattr(result, "video_file", None)
            return str(name) if name is not None else "unknown"

    async def _execute_search_stream(
        search_agent_input: SearchAgentInput,
    ) -> AsyncGenerator[AgentMessageChunk]:
        """
        Execute search with full streaming - implements three execution paths using shared core search function.

        Path 1: Attribute-only search (if has_action=False and attributes exist)
        Path 2: Embed-only search (if no attributes)
        Path 3: Fusion search (if has_action=True and attributes exist, with confidence threshold check)
        """
        query = search_agent_input.query
        agent_mode = search_agent_input.agent_mode
        # Use input value if provided, otherwise use config default
        use_attribute_search_flag = (
            search_agent_input.use_attribute_search
            if search_agent_input.use_attribute_search is not None
            else config.use_attribute_search
        )
        max_results = search_agent_input.max_results
        top_k = search_agent_input.top_k
        start_time = search_agent_input.start_time
        end_time = search_agent_input.end_time
        source_type = search_agent_input.source_type

        logger.info(f"Search agent executing: {search_agent_input.model_dump_json()}")

        # Convert SearchAgentInput to SearchInput
        from vss_agents.utils.time_convert import iso8601_to_datetime

        timestamp_start = None
        timestamp_end = None
        if start_time:
            try:
                timestamp_start = iso8601_to_datetime(start_time)
            except Exception as e:
                logger.warning(f"Failed to parse start_time: {e}")
        if end_time:
            try:
                timestamp_end = iso8601_to_datetime(end_time)
            except Exception as e:
                logger.warning(f"Failed to parse end_time: {e}")

        # top_k = input.top_k if input.top_k else default_max_result
        # User's top_k overrides default_max_result (no capping)
        top_k = top_k if top_k is not None else config.default_max_results

        search_input = SearchInput(
            query=query,
            source_type=source_type,
            top_k=top_k,
            agent_mode=agent_mode,
            timestamp_start=timestamp_start,
            timestamp_end=timestamp_end,
            use_critic=search_agent_input.use_critic,
        )

        # Get embed_search function reference
        embed_search_fn = await builder.get_function(config.embed_search_tool)

        try:
            # Use shared core search function (async generator) - yield progress updates in real-time
            search_output = None
            async for update in execute_core_search(
                search_input=search_input,
                embed_search=embed_search_fn,
                agent_llm=agent_llm,
                config=config,
                builder=builder,
                attribute_search_fn=attribute_search_fn,
                critic_agent=critic_agent,
            ):
                if isinstance(update, AgentMessageChunk):
                    # Forward progress updates directly
                    yield update
                elif isinstance(update, SearchOutput):
                    search_output = update

            if search_output is None:
                search_output = SearchOutput(data=[])

            # Note: execute_core_search already caps results to original_top_k, so no additional capping needed
            final_results = search_output.data
            result_count = len(final_results)

            # Build SearchOutput-compatible JSON
            results_dicts = [r.model_dump() for r in final_results]
            search_dict = {"data": results_dicts}

            # Format results for display
            if result_count > 0:
                summary = f"Found {result_count} matching video{'s' if result_count != 1 else ''}"
                search_result_json = json.dumps(search_dict, indent=2)
                messages = [summary, "\n\n**Search API result (JSON):**\n```json\n" + search_result_json + "\n```"]

                output = AgentOutput(
                    messages=messages,
                    side_effects={
                        "search_results": search_dict,
                        "result_count": result_count,
                    },
                    metadata={
                        "query": query,
                        "agent_mode": agent_mode,
                        "fusion_enabled": use_attribute_search_flag,
                        "max_results": max_results,
                        "filters": (
                            {
                                "start_time": start_time,
                                "end_time": end_time,
                            }
                            if (start_time or end_time)
                            else None
                        ),
                    },
                    status="success",
                )
            else:
                search_dict = {"data": []}
                search_result_json = json.dumps(search_dict, indent=2)
                output = AgentOutput(
                    messages=[
                        f"No videos found matching: '{query}'",
                        "\n\n**Search API result (JSON):**\n```json\n" + search_result_json + "\n```",
                    ],
                    side_effects={"search_results": search_dict},
                    metadata={"query": query},
                    status="success",
                )

            yield AgentMessageChunk(type=AgentMessageChunkType.FINAL, content=output.model_dump_json())

        except Exception as e:
            logger.error(f"Search failed: {e}", exc_info=True)
            yield AgentMessageChunk(type=AgentMessageChunkType.ERROR, content=f"Search failed: {e!s}")
            output = AgentOutput(
                messages=["Search failed due to an error"],
                status="error",
                error_message=str(e),
                metadata={"query": query},
            )
            yield AgentMessageChunk(type=AgentMessageChunkType.FINAL, content=output.model_dump_json())

    # Input converters for search_agent
    def _str_input_converter(input: str) -> SearchAgentInput:
        return SearchAgentInput.model_validate_json(input)

    def _chat_request_input_converter(request: ChatRequest) -> SearchAgentInput:
        return SearchAgentInput.model_validate_json(request.messages[-1].content)

    # Register the agent
    yield FunctionInfo.create(
        single_fn=_execute_search,
        stream_fn=_execute_search_stream,
        input_schema=SearchAgentInput,
        single_output_schema=SearchOutput,
        stream_output_schema=AgentMessageChunk,
        converters=[
            _str_input_converter,
            _chat_request_input_converter,
            _to_chat_response,
            _to_chat_response_chunk,
            _helper_markdown_bullet_list,
        ],
    )
