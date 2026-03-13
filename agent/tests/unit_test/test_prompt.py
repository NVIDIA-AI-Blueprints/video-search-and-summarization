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
"""Tests for vss_agents/prompt.py."""

from vss_agents.prompt import INIT_SUMMARIZE_PROMPT
from vss_agents.prompt import VIDEO_FRAME_TIMESTAMP_PROMPT
from vss_agents.prompt import VLM_FORMAT_INSTRUCTION
from vss_agents.prompt import VLM_PROMPT_EXAMPLES
from vss_agents.prompt import VSS_SUMMARIZE_PROMPT


class TestVlmPromptExamples:
    """Tests for VLM_PROMPT_EXAMPLES constant."""

    def test_examples_is_list(self):
        """Test that VLM_PROMPT_EXAMPLES is a list."""
        assert isinstance(VLM_PROMPT_EXAMPLES, list)

    def test_examples_not_empty(self):
        """Test that VLM_PROMPT_EXAMPLES has examples."""
        assert len(VLM_PROMPT_EXAMPLES) > 0


class TestVlmFormatInstruction:
    """Tests for VLM_FORMAT_INSTRUCTION constant."""

    def test_instruction_is_string(self):
        """Test that VLM_FORMAT_INSTRUCTION is a string."""
        assert isinstance(VLM_FORMAT_INSTRUCTION, str)

    def test_instruction_mentions_timestamp(self):
        """Test that instruction mentions timestamp."""
        assert "timestamp" in VLM_FORMAT_INSTRUCTION.lower()


class TestInitSummarizePrompt:
    """Tests for INIT_SUMMARIZE_PROMPT constant."""

    def test_prompt_is_dict(self):
        """Test that INIT_SUMMARIZE_PROMPT is a dict."""
        assert isinstance(INIT_SUMMARIZE_PROMPT, dict)

    def test_prompt_has_required_keys(self):
        """Test that INIT_SUMMARIZE_PROMPT has required keys."""
        assert "prompt" in INIT_SUMMARIZE_PROMPT
        assert "caption_summarization_prompt" in INIT_SUMMARIZE_PROMPT
        assert "summary_aggregation_prompt" in INIT_SUMMARIZE_PROMPT


class TestVideoFrameTimestampPrompt:
    """Tests for VIDEO_FRAME_TIMESTAMP_PROMPT constant."""

    def test_prompt_is_string(self):
        """Test that VIDEO_FRAME_TIMESTAMP_PROMPT is a string."""
        assert isinstance(VIDEO_FRAME_TIMESTAMP_PROMPT, str)


class TestVssSummarizePrompt:
    """Tests for VSS_SUMMARIZE_PROMPT constant."""

    def test_prompt_is_string(self):
        """Test that VSS_SUMMARIZE_PROMPT is a string."""
        assert isinstance(VSS_SUMMARIZE_PROMPT, str)

    def test_prompt_contains_placeholders(self):
        """Test that prompt contains expected placeholders."""
        assert "{user_query}" in VSS_SUMMARIZE_PROMPT
        assert "{user_intent}" in VSS_SUMMARIZE_PROMPT
