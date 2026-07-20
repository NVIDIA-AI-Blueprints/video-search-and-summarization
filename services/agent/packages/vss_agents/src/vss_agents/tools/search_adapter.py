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

"""NAT orchestration adapter for the framework-independent search library."""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING
from typing import Any
from typing import Literal

from fastapi import HTTPException

from vss_agents.agents.critic_agent import CriticAgentResult
from vss_agents.agents.critic_agent import VideoInfo
from vss_agents.agents.data_models import AgentMessageChunk
from vss_agents.agents.data_models import AgentMessageChunkType
from vss_agents.tools.search import CriticResult
from vss_agents.tools.search import SearchInput
from vss_agents.tools.search import SearchOutput
from vss_agents.tools.search import SearchResult
from vss_agents.tools.search import _resolve_video_sources_for_search
from vss_agents.tools.search import decompose_query
from vss_agents.utils.time_convert import iso8601_to_datetime
from vss_core.search_core import ErrorEvent
from vss_core.search_core import FinalResultEvent
from vss_core.search_core import PartialResultEvent
from vss_core.search_core import SearchRuntime
from vss_core.search_core import StatusEvent
from vss_core.search_core import VSSSearch
from vss_core.search_core.models.search import SearchInput as CoreSearchInput
from vss_core.vst import get_streams_info

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

_MAX_TOP_K = 1000
logger = logging.getLogger(__name__)

_ERROR_STATUS_CODES = {
    "ValidationError": 400,
    "InvalidInputError": 400,
    "IndexNotFoundError": 404,
    "BackendUnreachableError": 502,
    "VSTError": 502,
    "ConfigurationError": 500,
}


def build_search_runtime(config: Any) -> SearchRuntime | None:
    """Build the library runtime when the NAT config opts into the new path."""
    es_endpoint = getattr(config, "es_endpoint", None)
    cosmos_embed_endpoint = getattr(config, "cosmos_embed_endpoint", None)
    rtvi_cv_endpoint = getattr(config, "rtvi_cv_endpoint", None)
    vst_internal_url = getattr(config, "vst_internal_url", None)
    if not all((es_endpoint, cosmos_embed_endpoint, rtvi_cv_endpoint, vst_internal_url)):
        return None

    vst_external_url = getattr(config, "vst_external_url", None) or vst_internal_url
    return SearchRuntime.from_kwargs(
        es_endpoint=es_endpoint,
        behavior_es_endpoint=getattr(config, "behavior_es_endpoint", None) or es_endpoint,
        cosmos_embed_endpoint=cosmos_embed_endpoint,
        cosmos_embed_model=getattr(config, "cosmos_embed_model", "cosmos-embed1-448p"),
        rtvi_cv_endpoint=rtvi_cv_endpoint,
        vst_internal_url=vst_internal_url,
        vst_external_url=vst_external_url,
        behavior_index=getattr(config, "behavior_index", "mdx-behavior-2025-01-01"),
        behavior_index_wildcard=getattr(config, "behavior_index_wildcard", "mdx-behavior-*"),
        video_embed_index=getattr(config, "video_embed_index", "mdx-embed-filtered-2025-01-01"),
        video_embed_index_wildcard=getattr(config, "video_embed_index_wildcard", "mdx-embed-filtered-*"),
        frames_index=getattr(config, "frames_index", None),
        frames_index_wildcard=getattr(config, "frames_index_wildcard", "mdx-raw-*"),
        enable_frame_lookup=getattr(config, "enable_frame_lookup", True),
        default_max_results=getattr(config, "default_max_results", 10),
        embed_confidence_threshold=getattr(config, "embed_confidence_threshold", 0.1),
        fusion_method=getattr(config, "fusion_method", "rrf"),
        w_attribute=getattr(config, "w_attribute", 0.55),
        w_embed=getattr(config, "w_embed", 0.35),
        rrf_k=getattr(config, "rrf_k", 60),
        rrf_w=getattr(config, "rrf_w", 0.5),
        top_percent_filter=getattr(config, "top_percent_filter", None),
        request_timeout_seconds=getattr(config, "request_timeout_seconds", 30),
    )


def _result_key(result: SearchResult) -> tuple[str, str, str]:
    return result.sensor_id, result.start_time, result.end_time


def _to_nat_result(result: Any) -> SearchResult:
    return SearchResult(
        video_name=result.video_name,
        description=result.description,
        start_time=result.start_time,
        end_time=result.end_time,
        sensor_id=result.sensor_id,
        screenshot_url=result.screenshot_url,
        similarity=result.similarity,
        object_ids=result.object_ids,
    )


def _status_chunk(event: StatusEvent) -> AgentMessageChunk:
    chunk_type = AgentMessageChunkType.TOOL_CALL if event.stage == "tool_call" else AgentMessageChunkType.THOUGHT
    return AgentMessageChunk(type=chunk_type, content=event.message)


def _raise_search_error(event: ErrorEvent) -> None:
    status_code = _ERROR_STATUS_CODES.get(event.error_code, 500)
    detail = event.message if status_code < 500 else f"Search error: {event.message}"
    raise HTTPException(status_code=status_code, detail=detail)


class NATSearchAdapter:
    """Bridge NAT decomposition/critic behavior to ``lib.search_core`` retrieval."""

    def __init__(self, runtime: SearchRuntime, config: Any, agent_llm: Any | None, critic_agent: Any | None) -> None:
        self._runtime = runtime
        self._config = config
        self._agent_llm = agent_llm
        self._critic_agent = critic_agent
        self._search = VSSSearch.from_runtime(runtime)

    async def aclose(self) -> None:
        await self._search.aclose()

    async def _prepare_input(
        self,
        search_input: SearchInput,
        use_attribute_search: bool,
    ) -> AsyncGenerator[AgentMessageChunk | CoreSearchInput]:
        query = search_input.query
        video_sources = list(search_input.video_sources or [])
        timestamp_start = search_input.timestamp_start
        timestamp_end = search_input.timestamp_end
        top_k = search_input.top_k or self._runtime.default_max_results
        attributes: list[str] = []
        has_action: bool | None = None
        object_ids: list[int] | None = None

        if search_input.agent_mode and self._agent_llm is not None:
            yield AgentMessageChunk(
                type=AgentMessageChunkType.TOOL_CALL,
                content=f"Decomposing query: '{search_input.query}'",
            )
            video_file_names: list[str] = []
            video_stream_names: list[str] = []
            name_to_uuid: dict[str, str] = {}
            try:
                vst_internal_url = self._runtime.vst_internal_url
                if not vst_internal_url:
                    raise ValueError("VST internal URL is required for stream discovery")
                streams_info = await get_streams_info(vst_internal_url)
                for stream_id, stream_info in streams_info.items():
                    name = stream_info.get("name", "")
                    url = stream_info.get("url", "")
                    if not name:
                        continue
                    is_rtsp = url.startswith("rtsp://")
                    if search_input.source_type == "rtsp" and is_rtsp:
                        video_stream_names.append(name)
                        name_to_uuid[name] = stream_id
                    elif search_input.source_type == "video_file" and not is_rtsp:
                        video_file_names.append(name)
                        name_to_uuid[name] = stream_id
            except Exception as exc:
                # Stream discovery only enriches the decomposition prompt. Search
                # remains usable when VST discovery is temporarily unavailable.
                logger.warning("Could not discover VST streams for query decomposition: %s", exc)
                name_to_uuid = {}

            decomposed = await decompose_query(
                user_query=search_input.query,
                llm=self._agent_llm,
                video_file_names=video_file_names or None,
                video_stream_names=video_stream_names or None,
            )
            query = decomposed.query or query
            if decomposed.video_sources:
                video_sources = _resolve_video_sources_for_search(
                    decomposed.video_sources,
                    name_to_uuid,
                    search_input.source_type,
                )
            if decomposed.timestamp_start:
                try:
                    timestamp_start = iso8601_to_datetime(decomposed.timestamp_start)
                except Exception as exc:
                    logger.warning("Ignoring invalid decomposed start timestamp: %s", exc)
            if decomposed.timestamp_end:
                try:
                    timestamp_end = iso8601_to_datetime(decomposed.timestamp_end)
                except Exception as exc:
                    logger.warning("Ignoring invalid decomposed end timestamp: %s", exc)
            if decomposed.top_k is not None:
                top_k = decomposed.top_k
            attributes = [
                attribute.strip()
                for attribute in decomposed.attributes
                if attribute.strip() and any(separator in attribute.strip() for separator in (" ", "-", "."))
            ]
            has_action = decomposed.has_action
            object_ids = decomposed.object_ids
            summary: dict[str, Any] = {"refined_query": query, "attributes": attributes}
            if video_sources:
                summary["video_sources"] = video_sources
            if object_ids:
                summary["object_ids"] = object_ids
            yield AgentMessageChunk(
                type=AgentMessageChunkType.THOUGHT,
                content=f"Query decomposed: {json.dumps(summary)}",
            )

        if object_ids:
            search_mode: Literal["embed", "attribute", "fusion", "object"] = "object"
            attributes = []
        elif attributes and (has_action is False or has_action is None):
            search_mode = "attribute"
        elif attributes and use_attribute_search:
            search_mode = "fusion"
        else:
            search_mode = "embed"
            attributes = []

        yield CoreSearchInput(
            query=query,
            original_query=search_input.query,
            source_type=search_input.source_type,
            video_sources=video_sources or None,
            description=search_input.description,
            timestamp_start=timestamp_start,
            timestamp_end=timestamp_end,
            top_k=max(1, min(top_k, _MAX_TOP_K)),
            search_mode=search_mode,
            attributes=attributes,
            object_ids=object_ids,
            min_cosine_similarity=search_input.min_cosine_similarity,
        )

    async def stream(
        self,
        search_input: SearchInput,
        *,
        use_attribute_search: bool | None = None,
    ) -> AsyncGenerator[AgentMessageChunk | SearchOutput]:
        prepared: CoreSearchInput | None = None
        effective_attribute_search = (
            getattr(self._config, "use_attribute_search", False)
            if use_attribute_search is None
            else use_attribute_search
        )
        async for update in self._prepare_input(search_input, effective_attribute_search):
            if isinstance(update, AgentMessageChunk):
                yield update
            else:
                prepared = update
        if prepared is None:
            raise RuntimeError("query preparation did not produce a search input")

        original_top_k = prepared.top_k or self._runtime.default_max_results
        candidate_top_k = original_top_k
        accumulated: dict[tuple[str, str, str], SearchResult] = {}
        confirmed: set[tuple[str, str, str]] = set()
        rejected: set[tuple[str, str, str]] = set()
        verdicts: dict[tuple[str, str, str], CriticResult] = {}
        search_messages: list[str] = []
        critic_agent = self._critic_agent
        critic_enabled = bool(
            getattr(self._config, "enable_critic", False)
            and search_input.agent_mode
            and search_input.use_critic
            and critic_agent is not None
            and prepared.search_mode != "object"
        )

        for _iteration in range(getattr(self._config, "search_max_iterations", 1)):
            iteration_input = prepared.model_copy(update={"top_k": min(candidate_top_k, _MAX_TOP_K)})
            final_output = None
            async for event in self._search.search_stream(**iteration_input.model_dump()):
                if isinstance(event, StatusEvent):
                    yield _status_chunk(event)
                elif isinstance(event, PartialResultEvent):
                    yield AgentMessageChunk(
                        type=AgentMessageChunkType.THOUGHT,
                        content=f"Search produced {len(event.results)} partial results",
                    )
                elif isinstance(event, ErrorEvent):
                    _raise_search_error(event)
                elif isinstance(event, FinalResultEvent):
                    final_output = event.output

            if final_output is None:
                raise RuntimeError("lib.search_core returned no final search result")
            search_messages.extend(
                message for message in final_output.search_messages if message not in search_messages
            )
            current_results = [_to_nat_result(result) for result in final_output.data]
            unseen_keys: set[tuple[str, str, str]] = set()
            for result in current_results:
                key = _result_key(result)
                previous = accumulated.get(key)
                if previous is None:
                    unseen_keys.add(key)
                if previous is None or result.similarity > previous.similarity:
                    accumulated[key] = result

            if not critic_enabled or not current_results:
                break

            critic_candidates = [
                result for result in current_results if _result_key(result) not in confirmed | rejected
            ]
            if not critic_candidates or not unseen_keys:
                break

            yield AgentMessageChunk(
                type=AgentMessageChunkType.THOUGHT,
                content=f"Verifying {len(critic_candidates)} results with critic agent",
            )
            try:
                assert critic_agent is not None
                critic_output = await critic_agent.ainvoke(
                    {
                        "query": search_input.query,
                        "videos": [
                            VideoInfo(
                                sensor_id=result.sensor_id,
                                start_timestamp=result.start_time,
                                end_timestamp=result.end_time,
                            )
                            for result in critic_candidates
                        ],
                    }
                )
            except Exception as exc:
                logger.error("Critic verification failed: %s", exc, exc_info=True)
                message = "VLM verification unavailable (VLM may be down). Returning results without verification."
                search_messages.append(message)
                yield AgentMessageChunk(type=AgentMessageChunkType.THOUGHT, content=message)
                break

            video_results = critic_output.video_results
            rejected_this_iteration = 0
            for critic_result in video_results:
                key = (
                    critic_result.video_info.sensor_id,
                    critic_result.video_info.start_timestamp,
                    critic_result.video_info.end_timestamp,
                )
                verdicts[key] = CriticResult(
                    result=critic_result.result.value,
                    criteria_met=critic_result.criteria_met or {},
                )
                if critic_result.result == CriticAgentResult.CONFIRMED:
                    confirmed.add(key)
                elif critic_result.result == CriticAgentResult.REJECTED:
                    rejected.add(key)
                    rejected_this_iteration += 1

            if video_results and all(result.result == CriticAgentResult.UNVERIFIED for result in video_results):
                message = "VLM verification unavailable. Returning search results without critic verification."
                search_messages.append(message)
                yield AgentMessageChunk(type=AgentMessageChunkType.THOUGHT, content=message)
                break

            verified_count = sum(result.result == CriticAgentResult.CONFIRMED for result in video_results)
            unverified_count = sum(result.result == CriticAgentResult.UNVERIFIED for result in video_results)
            yield AgentMessageChunk(
                type=AgentMessageChunkType.THOUGHT,
                content=(
                    f"Critic verification complete: {verified_count}/{len(video_results)} results verified, "
                    f"{unverified_count}/{len(video_results)} results unverified"
                ),
            )
            if rejected_this_iteration == 0:
                break
            candidate_top_k += rejected_this_iteration

        visible_candidates = [result for key, result in accumulated.items() if key not in rejected]
        if not visible_candidates:
            visible_candidates = list(accumulated.values())

        results = sorted(visible_candidates, key=lambda result: result.similarity, reverse=True)
        for result in results:
            result.critic_result = verdicts.get(_result_key(result))
        results = results[:original_top_k]
        yield AgentMessageChunk(
            type=AgentMessageChunkType.THOUGHT,
            content=f"Found {len(results)} result{'s' if len(results) != 1 else ''}",
        )
        yield SearchOutput(data=results, search_messages=search_messages)

    async def run(
        self,
        search_input: SearchInput,
        *,
        use_attribute_search: bool | None = None,
    ) -> SearchOutput:
        async for update in self.stream(search_input, use_attribute_search=use_attribute_search):
            if isinstance(update, SearchOutput):
                return update
        raise RuntimeError("library search adapter returned no final output")
