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
"""Unit tests for prompt_gen module."""

from vss_agents.tools.prompt_gen import PromptGenConfig
from vss_agents.tools.prompt_gen import PromptGenInput


class TestPromptGenConfig:
    """Test PromptGenConfig model."""

    def test_with_required_field(self):
        config = PromptGenConfig(llm_name="test_llm")
        assert config.llm_name == "test_llm"
        assert config.prompt is not None  # Has default value

    def test_custom_prompt(self):
        custom_prompt = "Custom prompt template"
        config = PromptGenConfig(
            llm_name="test_llm",
            prompt=custom_prompt,
        )
        assert config.prompt == custom_prompt


class TestPromptGenInput:
    """Test PromptGenInput model."""

    def test_basic_input(self):
        input_data = PromptGenInput(
            user_query="What happened?",
            user_intent="Understand incident",
        )
        assert input_data.user_query == "What happened?"
        assert input_data.user_intent == "Understand incident"
        assert input_data.detailed_thinking is False
        assert input_data.previous_prompt == ""

    def test_with_detailed_thinking(self):
        input_data = PromptGenInput(
            user_query="What happened?",
            user_intent="Understand incident",
            detailed_thinking=True,
        )
        assert input_data.detailed_thinking is True

    def test_with_previous_prompt(self):
        input_data = PromptGenInput(
            user_query="What happened?",
            user_intent="Understand incident",
            previous_prompt="Previous prompt content",
        )
        assert input_data.previous_prompt == "Previous prompt content"

    def test_all_fields(self):
        input_data = PromptGenInput(
            user_query="What happened?",
            user_intent="Understand incident",
            detailed_thinking=True,
            previous_prompt="Previous prompt",
        )
        assert input_data.user_query == "What happened?"
        assert input_data.user_intent == "Understand incident"
        assert input_data.detailed_thinking is True
        assert input_data.previous_prompt == "Previous prompt"
