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
"""Additional unit tests for prompt_gen module to improve coverage."""

from pydantic import ValidationError
import pytest
from vss_agents.tools.prompt_gen import PromptGenConfig
from vss_agents.tools.prompt_gen import PromptGenInput


class TestPromptGenConfig:
    """Test PromptGenConfig model."""

    def test_required_fields(self):
        config = PromptGenConfig(llm_name="test-llm")
        assert config.llm_name == "test-llm"
        assert config.prompt is not None  # default prompt

    def test_custom_prompt(self):
        config = PromptGenConfig(llm_name="llm", prompt="Custom prompt")
        assert config.prompt == "Custom prompt"

    def test_missing_llm_raises(self):
        with pytest.raises(ValidationError):
            PromptGenConfig()


class TestPromptGenInput:
    """Test PromptGenInput model."""

    def test_required_fields(self):
        inp = PromptGenInput(user_query="What cars are in the video?", user_intent="find vehicles")
        assert inp.user_query == "What cars are in the video?"
        assert inp.user_intent == "find vehicles"
        assert inp.detailed_thinking is False
        assert inp.previous_prompt == ""

    def test_all_fields(self):
        inp = PromptGenInput(
            user_query="test query",
            user_intent="test intent",
            detailed_thinking=True,
            previous_prompt="Previous prompt text",
        )
        assert inp.detailed_thinking is True
        assert inp.previous_prompt == "Previous prompt text"

    def test_missing_user_query_raises(self):
        with pytest.raises(ValidationError):
            PromptGenInput(user_intent="intent")

    def test_missing_user_intent_raises(self):
        with pytest.raises(ValidationError):
            PromptGenInput(user_query="query")
