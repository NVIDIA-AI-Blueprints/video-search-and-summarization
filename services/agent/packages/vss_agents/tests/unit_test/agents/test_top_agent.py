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
"""Unit tests for top_agent module."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from unittest.mock import MagicMock

from langchain_core.messages import AIMessage
from langchain_core.messages import HumanMessage
from langchain_core.messages import ToolMessage
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.prompts import MessagesPlaceholder
from langchain_core.runnables import RunnableLambda
import pytest

from vss_agents.agents.data_models import AgentMessageChunk
from vss_agents.agents.data_models import AgentMessageChunkType
from vss_agents.agents.data_models import AgentRequestOptions
from vss_agents.agents.search_agent import SearchAgentInput
from vss_agents.agents.top_agent import EMPTY_MESSAGES_ERROR
from vss_agents.agents.top_agent import EMPTY_SCRATCHPAD_ERROR
from vss_agents.agents.top_agent import NO_INPUT_ERROR_MESSAGE
from vss_agents.agents.top_agent import TOOL_NOT_FOUND_ERROR_MESSAGE
from vss_agents.agents.top_agent import TopAgent
from vss_agents.agents.top_agent import TopAgentRequest
from vss_agents.agents.top_agent import TopAgentState
from vss_agents.agents.top_agent import _augment_context_clip_offsets
from vss_agents.agents.top_agent import strip_frontend_tags
from vss_agents.tools.lvs_config_media import LVS_CONFIG_MEDIA_BLOCKED_MESSAGE


class TestTopAgentConstants:
    """Test top_agent module constants."""

    def test_tool_not_found_error_message(self):
        assert "{tool_name}" in TOOL_NOT_FOUND_ERROR_MESSAGE
        assert "{tools}" in TOOL_NOT_FOUND_ERROR_MESSAGE

    def test_no_input_error_message(self):
        assert "No human input" in NO_INPUT_ERROR_MESSAGE

    def test_empty_messages_error(self):
        assert "current_message" in EMPTY_MESSAGES_ERROR

    def test_empty_scratchpad_error(self):
        assert "agent_scratchpad" in EMPTY_SCRATCHPAD_ERROR


class TestStripFrontendTags:
    """Test strip_frontend_tags function."""

    @pytest.mark.parametrize(
        "content,expected",
        [
            # HTML img with alt - should remain unchanged
            (
                'Check this <img src="http://example.com/img.jpg" alt="Snapshot at 00:05" width="400"> image',
                'Check this <img src="http://example.com/img.jpg" alt="Snapshot at 00:05" width="400"> image',
            ),
            # Self-closing img with alt - should remain unchanged
            (
                '<img src="http://example.com/chart.png" alt="Incident Chart" />',
                '<img src="http://example.com/chart.png" alt="Incident Chart" />',
            ),
            # Markdown image - should remain unchanged
            (
                "Here is ![Incident Snapshot](http://example.com/img.jpg) the image",
                "Here is ![Incident Snapshot](http://example.com/img.jpg) the image",
            ),
            # Markdown link - should remain unchanged
            (
                "Download [PDF Report](http://example.com/report.pdf) here",
                "Download [PDF Report](http://example.com/report.pdf) here",
            ),
            # Both markdown image and link - should remain unchanged
            (
                "![Snapshot](http://img.jpg) and [Video](http://video.mp4)",
                "![Snapshot](http://img.jpg) and [Video](http://video.mp4)",
            ),
            # Incidents tag - should be replaced
            (
                'Data: <incidents>{"incidents": [{"id": "123"}]}</incidents> end',
                "Data: [Incident data] end",
            ),
            # Multiline incidents tag - should be replaced
            (
                'Before\n<incidents>\n{\n  "incidents": [{"id": "123"}]\n}\n</incidents>\nAfter',
                "Before\n[Incident data]\nAfter",
            ),
            # No tags
            (
                "Plain text without any tags",
                "Plain text without any tags",
            ),
            # Empty content
            ("", ""),
            # Complex message with multiple elements - only incidents should be replaced
            (
                "Report generated successfully\n**Report Downloads:**\n- [Markdown Report](http://example.com/report.md)\n- [PDF Report](http://example.com/report.pdf)\n\n**Media:**\n- ![Incident Snapshot](http://example.com/snapshot.jpg)\n- [Incident Video](http://example.com/video.mp4)\n",
                "Report generated successfully\n**Report Downloads:**\n- [Markdown Report](http://example.com/report.md)\n- [PDF Report](http://example.com/report.pdf)\n\n**Media:**\n- ![Incident Snapshot](http://example.com/snapshot.jpg)\n- [Incident Video](http://example.com/video.mp4)\n",
            ),
        ],
    )
    def test_strip_frontend_tags(self, content, expected):
        assert strip_frontend_tags(content) == expected

    def test_none_content_returns_empty(self):
        assert strip_frontend_tags(None) == ""


class TestAgentRequestOptions:
    """Tests for the AgentRequestOptions model."""

    def test_defaults(self):
        opts = AgentRequestOptions()
        assert opts.use_critic is True
        assert opts.llm_reasoning is False
        assert opts.vlm_reasoning is None
        assert opts.search_source_type == "video_file"

    def test_use_critic_disabled(self):
        opts = AgentRequestOptions(use_critic=False)
        assert opts.use_critic is False

    def test_all_fields_overridden(self):
        opts = AgentRequestOptions(
            llm_reasoning=True,
            vlm_reasoning=True,
            search_source_type="rtsp",
            use_critic=False,
        )
        assert opts.llm_reasoning is True
        assert opts.vlm_reasoning is True
        assert opts.search_source_type == "rtsp"
        assert opts.use_critic is False


class TestRequestOptionsContext:
    """Tests for generic request option context."""

    def _agent_with_search_tool(self, request_options_context_enabled: bool = True):
        agent = TopAgent.__new__(TopAgent)
        agent.tools_dict = {"search_agent": MagicMock()}
        agent.request_options_context_enabled = request_options_context_enabled
        search_tool = agent.tools_dict["search_agent"]
        search_tool.name = "search_agent"
        search_tool.description = "Search videos"
        search_tool.args_schema = MagicMock()
        search_tool.args_schema.model_fields = {
            "request_options": MagicMock(),
            "use_critic": MagicMock(),
        }
        return agent

    def test_request_options_context_omits_without_previous_options(self):
        agent = self._agent_with_search_tool()
        state = TopAgentState(options=AgentRequestOptions(search_source_type="rtsp", use_critic=False))

        assert agent._request_options_context(state) == ""

    def test_request_options_context_omits_when_prompt_does_not_opt_in(self):
        agent = self._agent_with_search_tool(request_options_context_enabled=False)
        state = TopAgentState(options=AgentRequestOptions(search_source_type="rtsp", use_critic=False))

        assert agent._request_options_context(state) == ""

    @pytest.mark.parametrize(
        "prompt_parts,expected",
        [
            (("Main profile prompt uses current_request_options for runtime choices.",), True),
            (("Compare previous_request_options before reusing results.",), True),
            (("Generic assistant prompt.", "No runtime params here."), False),
            ((None, "", "Generic assistant prompt."), False),
        ],
    )
    def test_prompt_requests_request_options_context(self, prompt_parts, expected):
        assert TopAgent._prompt_requests_request_options_context(*prompt_parts) is expected

    def test_request_options_context_includes_current_and_previous_options(self):
        agent = self._agent_with_search_tool()
        state = TopAgentState(
            options=AgentRequestOptions(search_source_type="rtsp", use_critic=False),
            previous_options=AgentRequestOptions(search_source_type="video_file", use_critic=True),
        )

        context = agent._request_options_context(state)

        assert "Request options context" in context
        assert '"current_request_options"' in context
        assert '"previous_request_options"' in context
        assert '"search_source_type": "rtsp"' in context
        assert '"search_source_type": "video_file"' in context
        assert '"use_critic": false' in context
        assert '"use_critic": true' in context

    @pytest.mark.asyncio
    async def test_astream_restores_previous_options_from_checkpoint(self, monkeypatch):
        previous_options = AgentRequestOptions(search_source_type="video_file", use_critic=True)
        current_options = AgentRequestOptions(search_source_type="rtsp", use_critic=False)
        captured = {}

        class FakeGraph:
            def get_state(self, config):
                return SimpleNamespace(
                    values={
                        "conversation_history": [
                            HumanMessage(content="find a person"),
                            AIMessage(content="previous results"),
                        ],
                        "previous_conversation": "",
                        "options": previous_options.model_dump(mode="json"),
                    }
                )

            async def astream(self, input, config=None, stream_mode=None):
                captured["input_state"] = input
                yield AgentMessageChunk(type=AgentMessageChunkType.FINAL, content="done")

        monkeypatch.setattr(
            "vss_agents.agents.top_agent.ContextState.get",
            lambda: SimpleNamespace(conversation_id=SimpleNamespace(get=lambda: "thread-1")),
        )

        agent = TopAgent.__new__(TopAgent)
        agent.graph = FakeGraph()
        agent.max_history = 10
        agent.max_iterations = 10
        agent.llm = MagicMock()
        agent.callbacks = []

        chunks = [
            chunk
            async for chunk in agent.astream(
                [HumanMessage(content="same search on live streams")],
                options=current_options,
            )
        ]

        assert chunks == [AgentMessageChunk(type=AgentMessageChunkType.FINAL, content="done")]
        input_state = captured["input_state"]
        assert input_state.options == current_options
        assert input_state.previous_options == previous_options

    @pytest.mark.asyncio
    async def test_agent_node_passes_request_options_context_without_forcing_tool_call(self, monkeypatch):
        monkeypatch.setattr("vss_agents.agents.top_agent.get_stream_writer", lambda: lambda _chunk: None)

        captured = {}

        def _capture_prompt(prompt_value):
            captured["messages"] = prompt_value.to_messages()
            return AIMessage(content="Here are the previous results.")

        agent = self._agent_with_search_tool()
        agent.llm = MagicMock()
        agent.llm.model_name = "test-model"
        agent.llm_with_tools = RunnableLambda(_capture_prompt)
        agent.prompt = ChatPromptTemplate.from_messages(
            [
                ("system", "current time: {current_time}{request_options_context}{thinking_tag}"),
                MessagesPlaceholder(variable_name="conversation_history", optional=True),
                ("user", "{question}"),
                MessagesPlaceholder(variable_name="agent_scratchpad", optional=True),
            ]
        )
        agent.plan_exec_prompt = None
        agent.callbacks = []
        state = TopAgentState(
            current_message=HumanMessage(content="person carrying boxes"),
            options=AgentRequestOptions(search_source_type="rtsp", use_critic=False),
            previous_options=AgentRequestOptions(search_source_type="video_file", use_critic=True),
        )

        result = await agent.agent_node(state)

        assert result.final_answer == "Here are the previous results."
        assert len(result.agent_scratchpad) == 1
        ai_message = result.agent_scratchpad[0]
        assert isinstance(ai_message, AIMessage)
        assert not ai_message.tool_calls
        assert "Request options context" in captured["messages"][0].content

    @pytest.mark.asyncio
    async def test_agent_node_passes_request_options_context_to_plan_exec_prompt(self, monkeypatch):
        monkeypatch.setattr("vss_agents.agents.top_agent.get_stream_writer", lambda: lambda _chunk: None)

        captured = {}

        def _capture_prompt(prompt_value):
            captured["messages"] = prompt_value.to_messages()
            return AIMessage(content="Here are the previous results.")

        agent = self._agent_with_search_tool()
        agent.llm = MagicMock()
        agent.llm.model_name = "test-model"
        agent.llm_with_tools = RunnableLambda(_capture_prompt)
        agent.prompt = ChatPromptTemplate.from_messages([("user", "{question}")])
        agent.plan_exec_prompt = ChatPromptTemplate.from_messages(
            [
                ("system", "{request_options_context}{thinking_tag}"),
                ("user", "User Question: {question}\n\nExecution Plan:\n{plan_section}\n\n"),
            ]
        )
        agent.callbacks = []
        state = TopAgentState(
            current_message=HumanMessage(content="person carrying boxes"),
            plan="1. Answer from the current plan.",
            options=AgentRequestOptions(search_source_type="rtsp", use_critic=False),
            previous_options=AgentRequestOptions(search_source_type="video_file", use_critic=True),
        )

        result = await agent.agent_node(state)

        assert result.final_answer == "Here are the previous results."
        assert "Request options context" in captured["messages"][0].content

    @pytest.mark.asyncio
    async def test_plan_node_includes_request_options_context(self, monkeypatch):
        monkeypatch.setattr("vss_agents.agents.top_agent.get_stream_writer", lambda: lambda _chunk: None)

        captured = {}

        async def _capture_plan(messages, config=None):
            captured["system"] = messages[0].content
            return AIMessage(content="1. Call `search_agent` with the user's query.")

        agent = self._agent_with_search_tool()
        agent.llm = MagicMock()
        agent.llm.model_name = "test-model"
        agent.llm.ainvoke = AsyncMock(side_effect=_capture_plan)
        agent.callbacks = []
        agent.plan_prompt = None
        agent.plan_system_prompt = "System prompt."
        state = TopAgentState(
            current_message=HumanMessage(content="person carrying boxes"),
            options=AgentRequestOptions(search_source_type="rtsp", use_critic=False),
            previous_options=AgentRequestOptions(search_source_type="video_file", use_critic=True),
        )

        result = await agent._plan_node(state)

        assert result.plan == "1. Call `search_agent` with the user's query."
        assert "Request options context" in captured["system"]

    @pytest.mark.asyncio
    async def test_tool_node_forwards_request_options_to_accepting_tool(self, monkeypatch):
        chunks = []
        monkeypatch.setattr("vss_agents.agents.top_agent.get_stream_writer", lambda: chunks.append)

        class SearchTool:
            def __init__(self):
                self.args_schema = MagicMock()
                self.args_schema.model_fields = {
                    "request_options": MagicMock(),
                    "use_critic": MagicMock(),
                }
                self.received_input = None

            async def astream(self, input, config=None):
                self.received_input = input
                yield "search ok"

        search_tool = SearchTool()
        agent = TopAgent.__new__(TopAgent)
        agent.tools_dict = {"search_agent": search_tool}
        agent.subagent_names = set()
        agent.callbacks = []
        state = TopAgentState(
            agent_scratchpad=[
                AIMessage(
                    content="calling search",
                    tool_calls=[{"name": "search_agent", "args": {"query": "boxes"}, "id": "call_1"}],
                )
            ],
            options=AgentRequestOptions(search_source_type="rtsp", use_critic=False),
        )

        await agent.tool_or_subagent_node(state)

        assert search_tool.received_input["request_options"]["search_source_type"] == "rtsp"
        assert search_tool.received_input["request_options"]["use_critic"] is False
        assert search_tool.received_input["use_critic"] is False
        assert any(
            chunk.type == AgentMessageChunkType.TOOL_CALL
            and "'request_options':" in chunk.content
            and "'search_source_type': 'rtsp'" in chunk.content
            and "'use_critic': False" in chunk.content
            for chunk in chunks
        )

    @pytest.mark.asyncio
    async def test_tool_node_forwards_request_options_with_search_agent_schema(self, monkeypatch):
        monkeypatch.setattr("vss_agents.agents.top_agent.get_stream_writer", lambda: lambda _chunk: None)

        class SearchTool:
            args_schema = SearchAgentInput

            def __init__(self):
                self.received_input = None

            async def astream(self, input, config=None):
                self.received_input = input
                yield "search ok"

        search_tool = SearchTool()
        agent = TopAgent.__new__(TopAgent)
        agent.tools_dict = {"search_agent": search_tool}
        agent.subagent_names = set()
        agent.callbacks = []
        state = TopAgentState(
            agent_scratchpad=[
                AIMessage(
                    content="calling search",
                    tool_calls=[{"name": "search_agent", "args": {"query": "boxes"}, "id": "call_1"}],
                )
            ],
            options=AgentRequestOptions(search_source_type="rtsp", use_critic=False),
        )

        await agent.tool_or_subagent_node(state)

        assert search_tool.received_input["request_options"]["search_source_type"] == "rtsp"
        assert search_tool.received_input["request_options"]["use_critic"] is False
        assert "source_type" not in search_tool.received_input

    @pytest.mark.asyncio
    async def test_tool_node_forwards_request_options_to_accepting_subagent_trace(self, monkeypatch):
        chunks = []
        monkeypatch.setattr("vss_agents.agents.top_agent.get_stream_writer", lambda: chunks.append)

        search_tool = MagicMock()
        search_tool.args_schema = MagicMock()
        search_tool.args_schema.model_fields = {
            "request_options": MagicMock(),
            "use_critic": MagicMock(),
        }

        class SearchFunction:
            def __init__(self):
                self.received_input = None

            async def astream(self, input):
                self.received_input = input
                yield AgentMessageChunk(type=AgentMessageChunkType.FINAL, content="search ok")

        search_function = SearchFunction()
        agent = TopAgent.__new__(TopAgent)
        agent.tools_dict = {"search_agent": search_tool}
        agent.subagent_names = {"search_agent"}
        agent.subagent_functions = {"search_agent": search_function}
        agent.callbacks = []
        state = TopAgentState(
            agent_scratchpad=[
                AIMessage(
                    content="calling search",
                    tool_calls=[{"name": "search_agent", "args": {"query": "boxes"}, "id": "call_1"}],
                )
            ],
            options=AgentRequestOptions(search_source_type="rtsp", use_critic=False),
        )

        await agent.tool_or_subagent_node(state)

        assert search_function.received_input["request_options"]["search_source_type"] == "rtsp"
        assert search_function.received_input["request_options"]["use_critic"] is False
        assert search_function.received_input["use_critic"] is False
        assert any(
            chunk.type == AgentMessageChunkType.SUBAGENT_CALL
            and "'request_options':" in chunk.content
            and "'search_source_type': 'rtsp'" in chunk.content
            and "'use_critic': False" in chunk.content
            for chunk in chunks
        )


class TestTopAgentRequestUseCritic:
    """Tests for the use_critic field on TopAgentRequest."""

    def test_use_critic_defaults_to_none(self):
        req = TopAgentRequest(messages=[])
        assert req.use_critic is None

    def test_use_critic_set_true(self):
        req = TopAgentRequest(messages=[], use_critic=True)
        assert req.use_critic is True

    def test_use_critic_set_false(self):
        req = TopAgentRequest(messages=[], use_critic=False)
        assert req.use_critic is False


class TestAugmentContextClipOffsets:
    """Tests for _augment_context_clip_offsets (+Chat [Context] offset rewriting)."""

    @pytest.fixture(autouse=True)
    def _patch_vst(self, monkeypatch):
        """Patch VST helpers so the stream starts at 00:00:00Z (offsets == wall-clock seconds)."""

        async def fake_get_name_to_stream_id_map(*args, **kwargs):
            return {"cam1": "stream-cam1", "cam2": "stream-cam2"}

        async def fake_get_timeline(stream_id, *args, **kwargs):
            return "2025-01-01T00:00:00.000Z", "2025-01-01T01:00:00.000Z"

        monkeypatch.setattr("vss_agents.agents.top_agent.get_name_to_stream_id_map", fake_get_name_to_stream_id_map)
        monkeypatch.setattr("vss_agents.agents.top_agent.get_timeline", fake_get_timeline)

    @pytest.mark.asyncio
    async def test_empty_message_returns_unchanged(self):
        assert await _augment_context_clip_offsets("") == ""

    @pytest.mark.asyncio
    async def test_no_context_block_returns_unchanged(self):
        msg = "what is in the third clip?"
        assert await _augment_context_clip_offsets(msg) == msg

    @pytest.mark.asyncio
    async def test_missing_array_after_prefix_returns_unchanged(self):
        msg = "look here [Context: not-an-array"
        assert await _augment_context_clip_offsets(msg) == msg

    @pytest.mark.asyncio
    async def test_malformed_json_left_unchanged(self):
        msg = "[Context: [not valid json ]"
        assert await _augment_context_clip_offsets(msg) == msg

    @pytest.mark.asyncio
    async def test_non_list_payload_returns_unchanged(self):
        msg = '[Context: {"mediaType": "sensor-clip"}]'
        assert await _augment_context_clip_offsets(msg) == msg

    @pytest.mark.asyncio
    async def test_non_dict_clip_entry_skipped(self):
        msg = 'x [Context: ["just-a-string"]]'
        assert await _augment_context_clip_offsets(msg) == msg

    @pytest.mark.asyncio
    async def test_non_sensor_clip_untouched(self):
        clips = [{"mediaType": "image", "sensorName": "cam1"}]
        msg = f"look [Context: {json.dumps(clips)}]"
        assert await _augment_context_clip_offsets(msg) == msg

    @pytest.mark.asyncio
    async def test_missing_time_fields_skipped(self):
        clips = [{"mediaType": "sensor-clip", "sensorName": "cam1"}]
        msg = f"x [Context: {json.dumps(clips)}]"
        assert await _augment_context_clip_offsets(msg) == msg

    @pytest.mark.asyncio
    async def test_sensor_clip_gets_offsets(self):
        clips = [
            {
                "mediaType": "sensor-clip",
                "sensorName": "cam1",
                "startTime": "2025-01-01T00:00:30.000Z",
                "endTime": "2025-01-01T00:01:00.000Z",
            }
        ]
        msg = f"what is here? [Context: {json.dumps(clips)}]"

        out = await _augment_context_clip_offsets(msg)

        assert out != msg
        assert out.startswith("what is here? [Context: ")
        # Parse the rewritten block back out to assert on structured values.
        payload_start = out.index("[", len("what is here? [Context:"))
        augmented, _ = json.JSONDecoder().raw_decode(out, payload_start)
        assert augmented[0]["startOffset"] == 30.0
        assert augmented[0]["endOffset"] == 60.0
        # Original ISO fields are preserved (non-destructive).
        assert augmented[0]["startTime"] == "2025-01-01T00:00:30.000Z"
        assert augmented[0]["endTime"] == "2025-01-01T00:01:00.000Z"

    @pytest.mark.asyncio
    async def test_text_around_block_is_preserved(self):
        clips = [
            {
                "mediaType": "sensor-clip",
                "sensorName": "cam1",
                "startTime": "2025-01-01T00:00:30.000Z",
                "endTime": "2025-01-01T00:01:00.000Z",
            }
        ]
        msg = f"before [Context: {json.dumps(clips)}] after"

        out = await _augment_context_clip_offsets(msg)

        assert out.startswith("before [Context: ")
        assert out.endswith("] after")
        assert "startOffset" in out

    @pytest.mark.asyncio
    async def test_stream_map_failure_keeps_message(self, monkeypatch):
        async def boom(*args, **kwargs):
            raise RuntimeError("VST down")

        monkeypatch.setattr("vss_agents.agents.top_agent.get_name_to_stream_id_map", boom)
        clips = [
            {
                "mediaType": "sensor-clip",
                "sensorName": "cam1",
                "startTime": "2025-01-01T00:00:30.000Z",
                "endTime": "2025-01-01T00:01:00.000Z",
            }
        ]
        msg = f"x [Context: {json.dumps(clips)}]"

        assert await _augment_context_clip_offsets(msg) == msg

    @pytest.mark.asyncio
    async def test_timeline_failure_keeps_message(self, monkeypatch):
        async def boom(*args, **kwargs):
            raise RuntimeError("timeline down")

        monkeypatch.setattr("vss_agents.agents.top_agent.get_timeline", boom)
        clips = [
            {
                "mediaType": "sensor-clip",
                "sensorName": "cam1",
                "startTime": "2025-01-01T00:00:30.000Z",
                "endTime": "2025-01-01T00:01:00.000Z",
            }
        ]
        msg = f"x [Context: {json.dumps(clips)}]"

        assert await _augment_context_clip_offsets(msg) == msg

    @pytest.mark.asyncio
    async def test_unknown_sensor_skipped(self):
        clips = [
            {
                "mediaType": "sensor-clip",
                "sensorName": "not-a-known-sensor",
                "startTime": "2025-01-01T00:00:30.000Z",
                "endTime": "2025-01-01T00:01:00.000Z",
            }
        ]
        msg = f"x [Context: {json.dumps(clips)}]"

        assert await _augment_context_clip_offsets(msg) == msg

    @pytest.mark.asyncio
    async def test_sensor_name_that_is_stream_id_is_accepted(self):
        # sensorName is already a stream ID present in the map values.
        clips = [
            {
                "mediaType": "sensor-clip",
                "sensorName": "stream-cam1",
                "startTime": "2025-01-01T00:00:30.000Z",
                "endTime": "2025-01-01T00:01:00.000Z",
            }
        ]
        msg = f"x [Context: {json.dumps(clips)}]"

        out = await _augment_context_clip_offsets(msg)

        payload_start = out.index("[", len("x [Context:"))
        augmented, _ = json.JSONDecoder().raw_decode(out, payload_start)
        assert augmented[0]["startOffset"] == 30.0
        assert augmented[0]["endOffset"] == 60.0

    @pytest.mark.asyncio
    async def test_repeated_sensor_dedups_timeline_calls(self, monkeypatch):
        """Two clips from the same sensor should trigger only one timeline lookup."""
        timeline_calls: list[str] = []

        async def counting_get_timeline(stream_id, *args, **kwargs):
            timeline_calls.append(stream_id)
            return "2025-01-01T00:00:00.000Z", "2025-01-01T01:00:00.000Z"

        monkeypatch.setattr("vss_agents.agents.top_agent.get_timeline", counting_get_timeline)
        clips = [
            {
                "mediaType": "sensor-clip",
                "sensorName": "cam1",
                "startTime": "2025-01-01T00:00:30.000Z",
                "endTime": "2025-01-01T00:01:00.000Z",
            },
            {
                "mediaType": "sensor-clip",
                "sensorName": "cam1",
                "startTime": "2025-01-01T00:02:00.000Z",
                "endTime": "2025-01-01T00:03:00.000Z",
            },
        ]
        msg = f"x [Context: {json.dumps(clips)}]"

        out = await _augment_context_clip_offsets(msg)

        # Only one timeline round-trip despite two clips from the same sensor.
        assert timeline_calls == ["stream-cam1"]
        payload_start = out.index("[", len("x [Context:"))
        augmented, _ = json.JSONDecoder().raw_decode(out, payload_start)
        assert augmented[0]["startOffset"] == 30.0
        assert augmented[1]["startOffset"] == 120.0
        assert augmented[1]["endOffset"] == 180.0

    @pytest.mark.asyncio
    async def test_distinct_sensors_each_fetched_once(self, monkeypatch):
        timeline_calls: list[str] = []

        async def counting_get_timeline(stream_id, *args, **kwargs):
            timeline_calls.append(stream_id)
            return "2025-01-01T00:00:00.000Z", "2025-01-01T01:00:00.000Z"

        monkeypatch.setattr("vss_agents.agents.top_agent.get_timeline", counting_get_timeline)
        clips = [
            {
                "mediaType": "sensor-clip",
                "sensorName": "cam1",
                "startTime": "2025-01-01T00:00:30.000Z",
                "endTime": "2025-01-01T00:01:00.000Z",
            },
            {
                "mediaType": "sensor-clip",
                "sensorName": "cam2",
                "startTime": "2025-01-01T00:00:30.000Z",
                "endTime": "2025-01-01T00:01:00.000Z",
            },
        ]
        msg = f"x [Context: {json.dumps(clips)}]"

        await _augment_context_clip_offsets(msg)

        assert sorted(timeline_calls) == ["stream-cam1", "stream-cam2"]


class TestLvsConfigMediaCaptionGate:
    """lvs_config_media must not run HITL unless the user asked to start captioning."""

    def _agent_with_config_tool(self, monkeypatch):
        monkeypatch.setattr("vss_agents.agents.top_agent.get_stream_writer", lambda: lambda _chunk: None)

        class ConfigTool:
            args_schema = None

            def __init__(self):
                self.called = False

            async def astream(self, input, config=None):
                self.called = True
                yield "should not run"

        config_tool = ConfigTool()
        agent = TopAgent.__new__(TopAgent)
        agent.tools_dict = {"lvs_config_media": config_tool}
        agent.subagent_names = set()
        agent.callbacks = []
        return agent, config_tool

    @pytest.mark.asyncio
    async def test_blocks_config_media_on_summarize_query(self, monkeypatch):
        agent, config_tool = self._agent_with_config_tool(monkeypatch)
        state = TopAgentState(
            current_message=HumanMessage(
                content="Summarize the stream sample_warehouse from 45 seconds until now",
            ),
            agent_scratchpad=[
                AIMessage(
                    content="calling config",
                    tool_calls=[
                        {
                            "name": "lvs_config_media",
                            "args": {"stream_name": "sample_warehouse"},
                            "id": "call_1",
                        }
                    ],
                )
            ],
            options=AgentRequestOptions(),
        )

        await agent.tool_or_subagent_node(state)

        assert config_tool.called is False
        assert state.final_answer == LVS_CONFIG_MEDIA_BLOCKED_MESSAGE
        assert any(
            isinstance(msg, ToolMessage) and LVS_CONFIG_MEDIA_BLOCKED_MESSAGE in str(msg.content)
            for msg in state.agent_scratchpad
        )

    @pytest.mark.asyncio
    async def test_allows_config_media_on_start_captioning_query(self, monkeypatch):
        agent, config_tool = self._agent_with_config_tool(monkeypatch)
        state = TopAgentState(
            current_message=HumanMessage(content="start captioning the stream sample_warehouse"),
            agent_scratchpad=[
                AIMessage(
                    content="calling config",
                    tool_calls=[
                        {
                            "name": "lvs_config_media",
                            "args": {"stream_name": "sample_warehouse"},
                            "id": "call_1",
                        }
                    ],
                )
            ],
            options=AgentRequestOptions(),
        )

        await agent.tool_or_subagent_node(state)

        assert config_tool.called is True
        assert state.final_answer != LVS_CONFIG_MEDIA_BLOCKED_MESSAGE
