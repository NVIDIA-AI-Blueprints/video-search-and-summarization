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

from unittest.mock import AsyncMock
from unittest.mock import MagicMock

from langchain_core.messages import AIMessage
from langchain_core.messages import HumanMessage
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.prompts import MessagesPlaceholder
from langchain_core.runnables import RunnableLambda
import pytest

from vss_agents.agents.top_agent import EMPTY_MESSAGES_ERROR
from vss_agents.agents.top_agent import EMPTY_SCRATCHPAD_ERROR
from vss_agents.agents.top_agent import NO_INPUT_ERROR_MESSAGE
from vss_agents.agents.top_agent import TOOL_NOT_FOUND_ERROR_MESSAGE
from vss_agents.agents.top_agent import AgentRequestOptions
from vss_agents.agents.top_agent import TopAgent
from vss_agents.agents.top_agent import TopAgentRequest
from vss_agents.agents.top_agent import TopAgentState
from vss_agents.agents.top_agent import strip_frontend_tags


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


class TestSearchRuntimeParamContext:
    """Tests for stale search runtime-param context."""

    def _agent_with_search_tool(self):
        agent = TopAgent.__new__(TopAgent)
        agent.tools_dict = {"search_agent": MagicMock()}
        search_tool = agent.tools_dict["search_agent"]
        search_tool.args_schema = MagicMock()
        search_tool.args_schema.model_fields = {
            "source_type": MagicMock(),
            "use_critic": MagicMock(),
        }
        return agent

    def test_runtime_params_for_search_agent(self):
        agent = self._agent_with_search_tool()

        params = agent._runtime_params_for_tool(
            AgentRequestOptions(search_source_type="rtsp", use_critic=False),
            "search_agent",
        )

        assert params == {"source_type": "rtsp", "use_critic": False}

    def test_search_runtime_param_context_omits_unchanged_params(self):
        agent = self._agent_with_search_tool()
        state = TopAgentState(
            options=AgentRequestOptions(search_source_type="video_file", use_critic=True),
            last_search_runtime_params={"source_type": "video_file", "use_critic": True},
        )

        assert agent._search_runtime_param_context(state) == ""

    def test_search_runtime_param_context_omits_without_search_agent(self):
        agent = TopAgent.__new__(TopAgent)
        agent.tools_dict = {}
        state = TopAgentState(
            options=AgentRequestOptions(search_source_type="rtsp", use_critic=True),
            last_search_runtime_params={"source_type": "video_file", "use_critic": True},
        )

        assert agent._search_runtime_param_context(state) == ""

    def test_search_runtime_param_context_describes_changed_params(self):
        agent = self._agent_with_search_tool()
        state = TopAgentState(
            options=AgentRequestOptions(search_source_type="rtsp", use_critic=True),
            last_search_runtime_params={"source_type": "video_file", "use_critic": True},
        )

        context = agent._search_runtime_param_context(state)

        assert "Search runtime parameters changed" in context
        assert '"current_search_params": {"source_type": "rtsp", "use_critic": true}' in context
        assert '"previous_search_result_params": {"source_type": "video_file", "use_critic": true}' in context

    @pytest.mark.asyncio
    async def test_agent_node_passes_changed_search_context_without_forcing_tool_call(self, monkeypatch):
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
                ("system", "current time: {current_time}{search_runtime_param_context}{thinking_tag}"),
                MessagesPlaceholder(variable_name="conversation_history", optional=True),
                ("user", "{question}"),
                MessagesPlaceholder(variable_name="agent_scratchpad", optional=True),
            ]
        )
        agent.plan_exec_prompt = None
        agent.callbacks = []
        state = TopAgentState(
            current_message=HumanMessage(content="person carrying boxes"),
            options=AgentRequestOptions(search_source_type="rtsp", use_critic=True),
            last_search_runtime_params={"source_type": "video_file", "use_critic": True},
        )

        result = await agent.agent_node(state)

        assert result.final_answer == "Here are the previous results."
        assert len(result.agent_scratchpad) == 1
        ai_message = result.agent_scratchpad[0]
        assert isinstance(ai_message, AIMessage)
        assert not ai_message.tool_calls
        assert "Search runtime parameters changed" in captured["messages"][0].content

    @pytest.mark.asyncio
    async def test_plan_node_includes_changed_search_context(self, monkeypatch):
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
            options=AgentRequestOptions(search_source_type="rtsp", use_critic=True),
            last_search_runtime_params={"source_type": "video_file", "use_critic": True},
        )

        result = await agent._plan_node(state)

        assert result.plan == "1. Call `search_agent` with the user's query."
        assert "Search runtime parameters changed" in captured["system"]

    @pytest.mark.parametrize(
        "fails,expected_params",
        [
            (False, {"source_type": "rtsp", "use_critic": False}),
            (True, {"source_type": "video_file", "use_critic": True}),
        ],
    )
    @pytest.mark.asyncio
    async def test_tool_node_records_search_params_only_after_success(self, monkeypatch, fails, expected_params):
        monkeypatch.setattr("vss_agents.agents.top_agent.get_stream_writer", lambda: lambda _chunk: None)

        class SearchTool:
            def __init__(self):
                self.args_schema = MagicMock()
                self.args_schema.model_fields = {
                    "source_type": MagicMock(),
                    "use_critic": MagicMock(),
                }
                self.received_input = None

            async def astream(self, input, config=None):
                self.received_input = input
                if fails:
                    raise RuntimeError("search failed")
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
            last_search_runtime_params={"source_type": "video_file", "use_critic": True},
        )

        result = await agent.tool_or_subagent_node(state)

        assert search_tool.received_input["source_type"] == "rtsp"
        assert search_tool.received_input["use_critic"] is False
        assert result.last_search_runtime_params == expected_params


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
