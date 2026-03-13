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
from unittest.mock import MagicMock

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
        mock_llm = MagicMock()
        mock_response = MagicMock()
        mock_response.content = "Generated prompt for finding cars"
        mock_llm.__or__ = MagicMock(return_value=AsyncMock(ainvoke=AsyncMock(return_value=mock_response)))
        mock_builder.get_llm.return_value = mock_llm

        gen = prompt_gen.__wrapped__(config, mock_builder)
        function_info = await gen.__anext__()
        inner_fn = function_info.single_fn

        inp = PromptGenInput(user_query="find cars", user_intent="vehicle detection")
        result = await inner_fn(inp)
        assert isinstance(result, str)

    @pytest.mark.asyncio
    async def test_prompt_gen_with_detailed_thinking(self, config, mock_builder):
        mock_llm = MagicMock()
        mock_response = MagicMock()
        mock_response.content = "Detailed prompt"
        mock_llm.__or__ = MagicMock(return_value=AsyncMock(ainvoke=AsyncMock(return_value=mock_response)))
        mock_builder.get_llm.return_value = mock_llm

        gen = prompt_gen.__wrapped__(config, mock_builder)
        function_info = await gen.__anext__()
        inner_fn = function_info.single_fn

        inp = PromptGenInput(user_query="find cars", user_intent="detect", detailed_thinking=True)
        result = await inner_fn(inp)
        assert isinstance(result, str)

    @pytest.mark.asyncio
    async def test_prompt_gen_with_previous_prompt(self, config, mock_builder):
        mock_llm = MagicMock()
        mock_response1 = MagicMock()
        mock_response1.content = "New prompt"
        mock_response2 = MagicMock()
        mock_response2.content = "Merged prompt"

        call_count = [0]

        async def mock_ainvoke(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return mock_response1
            return mock_response2

        mock_chain = AsyncMock(ainvoke=mock_ainvoke)
        mock_llm.__or__ = MagicMock(return_value=mock_chain)
        mock_builder.get_llm.return_value = mock_llm

        gen = prompt_gen.__wrapped__(config, mock_builder)
        function_info = await gen.__anext__()
        inner_fn = function_info.single_fn

        inp = PromptGenInput(
            user_query="find cars",
            user_intent="detect",
            previous_prompt="Old prompt",
        )
        result = await inner_fn(inp)
        assert isinstance(result, str)
