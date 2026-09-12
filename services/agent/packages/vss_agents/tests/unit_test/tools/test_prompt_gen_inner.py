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
"""Tests for prompt_gen inner function via generator invocation."""

from unittest.mock import AsyncMock

from langchain_core.messages import AIMessage
from langchain_core.runnables import Runnable
import pytest

from vss_agents.tools.prompt_gen import PromptGenConfig
from vss_agents.tools.prompt_gen import PromptGenInput
from vss_agents.tools.prompt_gen import prompt_gen


class TestPromptGenInner:
    """Test the inner _prompt_gen function."""

    @pytest.fixture
    def config(self):
        return PromptGenConfig(
            llm_name="test-llm", prompt="Generate a prompt for: {user_query} with intent: {user_intent}"
        )

    @pytest.fixture
    def mock_builder(self):
        return AsyncMock()

    @pytest.mark.asyncio
    async def test_basic_prompt_gen(self, config, mock_builder):
        llm = ChatNVIDIA(AIMessage(content="Generated prompt for finding cars"))
        mock_builder.get_llm.return_value = llm

        gen = prompt_gen.__wrapped__(config, mock_builder)
        function_info = await gen.__anext__()
        inner_fn = function_info.single_fn

        inp = PromptGenInput(user_query="find cars", user_intent="vehicle detection")
        result = await inner_fn(inp)
        assert result == "Generated prompt for finding cars"

    @pytest.mark.asyncio
    async def test_prompt_gen_with_detailed_thinking(self, config, mock_builder):
        llm = ChatNVIDIA(AIMessage(content="Detailed prompt"))
        mock_builder.get_llm.return_value = llm

        gen = prompt_gen.__wrapped__(config, mock_builder)
        function_info = await gen.__anext__()
        inner_fn = function_info.single_fn

        inp = PromptGenInput(user_query="find cars", user_intent="detect", detailed_thinking=True)
        result = await inner_fn(inp)
        assert result == "Detailed prompt"

    @pytest.mark.asyncio
    async def test_prompt_gen_with_previous_prompt(self, config, mock_builder):
        llm = ChatNVIDIA(AIMessage(content="New prompt"))
        responses = [AIMessage(content="New prompt"), AIMessage(content="Merged prompt")]

        async def _ainvoke(input, config=None, **kwargs):
            return responses.pop(0)

        llm.ainvoke = _ainvoke
        mock_builder.get_llm.return_value = llm

        gen = prompt_gen.__wrapped__(config, mock_builder)
        function_info = await gen.__anext__()
        inner_fn = function_info.single_fn

        inp = PromptGenInput(
            user_query="find cars",
            user_intent="detect",
            previous_prompt="Old prompt",
        )
        result = await inner_fn(inp)
        assert result == "Merged prompt"
        assert responses == []


class ChatNVIDIA(Runnable):
    """Minimal stand-in for the real client.

    Named ChatNVIDIA because get_llm_reasoning_bind_kwargs dispatches on
    type(llm).__name__, and a real Runnable because prompt_gen builds
    `ChatPromptTemplate | llm` — a MagicMock gets coerced to a RunnableLambda and
    its ainvoke is never called.
    """

    def __init__(self, response, model_name="nvidia/nemotron-3.5-lightning-30b-a3b"):
        self.response = response
        self.model_name = model_name
        self.bind_calls: list[dict] = []

    def bind(self, **kwargs):
        self.bind_calls.append(kwargs)
        return self

    def invoke(self, input, config=None, **kwargs):
        return self.response

    async def ainvoke(self, input, config=None, **kwargs):
        return self.response


class TestPromptGenReasoning:
    """Thinking mode is bound explicitly, and an empty answer is never returned."""

    @pytest.fixture
    def config(self):
        return PromptGenConfig(
            llm_name="test-llm", prompt="Generate a prompt for: {user_query} with intent: {user_intent}"
        )

    @pytest.fixture
    def mock_builder(self):
        return AsyncMock()

    async def _inner(self, config, mock_builder, llm):
        mock_builder.get_llm.return_value = llm
        gen = prompt_gen.__wrapped__(config, mock_builder)
        function_info = await gen.__anext__()
        return function_info.single_fn

    @pytest.mark.asyncio
    async def test_thinking_is_disabled_by_default(self, config, mock_builder):
        """The NIM default is enable_thinking=true; this helper must opt out."""
        llm = ChatNVIDIA(AIMessage(content="Is there a box on the floor? Answer YES or NO."))
        inner_fn = await self._inner(config, mock_builder, llm)

        await inner_fn(PromptGenInput(user_query="boxes dropped", user_intent="real-time monitoring"))

        assert llm.bind_calls == [{"chat_template_kwargs": {"enable_thinking": False}}]

    @pytest.mark.asyncio
    async def test_detailed_thinking_is_honoured_when_asked_for(self, config, mock_builder):
        llm = ChatNVIDIA(AIMessage(content="Is there a box on the floor? Answer YES or NO."))
        inner_fn = await self._inner(config, mock_builder, llm)

        await inner_fn(PromptGenInput(user_query="boxes dropped", user_intent="monitoring", detailed_thinking=True))

        assert llm.bind_calls == [{"chat_template_kwargs": {"enable_thinking": True}}]

    @pytest.mark.asyncio
    async def test_empty_content_falls_back_to_a_prompt_carrying_the_user_intent(self, config, mock_builder):
        """finish_reason=length leaves content empty.

        Raising would not help: the top agent turns a tool exception into an error
        ToolMessage, plan_update preserves the pending step, and the tool is called
        again until max_iterations. Degrade to a usable prompt instead.
        """
        llm = ChatNVIDIA(AIMessage(content=""))
        inner_fn = await self._inner(config, mock_builder, llm)

        result = await inner_fn(PromptGenInput(user_query="boxes dropped", user_intent="monitoring"))

        assert result == "Detect for boxes dropped. Answer in Yes or No."

    @pytest.mark.asyncio
    async def test_reasoning_is_stripped_from_the_generated_prompt(self, config, mock_builder):
        """A think-blob must not become part of the detection prompt."""
        llm = ChatNVIDIA(
            AIMessage(content="<think>The user wants boxes.</think>Is there a box on the floor? Answer YES or NO.")
        )
        inner_fn = await self._inner(config, mock_builder, llm)

        result = await inner_fn(PromptGenInput(user_query="boxes dropped", user_intent="monitoring"))

        assert result == "Is there a box on the floor? Answer YES or NO."
        assert "<think>" not in result
        assert "The user wants boxes." not in result

    @pytest.mark.asyncio
    async def test_reasoning_only_reply_falls_back(self, config, mock_builder):
        """Thinking consumed the budget: reasoning present, no answer after it."""
        llm = ChatNVIDIA(AIMessage(content="<think>Let me consider what to monitor</think>"))
        inner_fn = await self._inner(config, mock_builder, llm)

        result = await inner_fn(PromptGenInput(user_query="boxes dropped", user_intent="monitoring"))

        assert result == "Detect for boxes dropped. Answer in Yes or No."
        assert "<think>" not in result

    @pytest.mark.asyncio
    async def test_empty_merge_falls_back_to_the_unmerged_prompt(self, config, mock_builder):
        """A failed merge must not discard the prompt the first call produced."""
        responses = [
            AIMessage(content="Is there a box on the floor? Answer YES or NO."),
            AIMessage(content="<think>merging</think>"),
        ]
        llm = ChatNVIDIA(responses[0])

        async def _ainvoke(input, config=None, **kwargs):
            return responses.pop(0)

        llm.ainvoke = _ainvoke
        inner_fn = await self._inner(config, mock_builder, llm)

        result = await inner_fn(
            PromptGenInput(user_query="boxes dropped", user_intent="monitoring", previous_prompt="Old prompt")
        )

        assert result == "Is there a box on the floor? Answer YES or NO."
