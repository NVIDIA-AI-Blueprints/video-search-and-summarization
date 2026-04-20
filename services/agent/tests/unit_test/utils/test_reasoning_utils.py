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
"""Tests for vss_agents/utils/reasoning_utils.py."""

from unittest.mock import MagicMock

from vss_agents.utils.reasoning_utils import get_llm_reasoning_bind_kwargs
from vss_agents.utils.reasoning_utils import get_thinking_tag


class TestGetThinkingTag:
    """Tests for get_thinking_tag function."""

    def test_thinking_none_returns_none(self):
        """Test that None thinking parameter returns None."""
        llm = MagicMock()
        llm.model_name = "nvidia/nvidia-nemotron"
        result = get_thinking_tag(llm, None)
        assert result is None

    def test_nvidia_nemotron_thinking_enabled(self):
        """Test NVIDIA Nemotron with thinking enabled."""
        llm = MagicMock()
        llm.model_name = "nvidia/nvidia-nemotron-4"
        result = get_thinking_tag(llm, True)
        assert result == "/think"

    def test_nvidia_nemotron_thinking_disabled(self):
        """Test NVIDIA Nemotron with thinking disabled."""
        llm = MagicMock()
        llm.model_name = "nvidia/nvidia-nemotron-4"
        result = get_thinking_tag(llm, False)
        assert result == "/no_think"

    def test_nvidia_nemotron_3_nano(self):
        """Test that Nemotron 3 Nano does not need thinking tag."""
        llm = MagicMock()
        llm.model_name = "nvidia/nvidia-nemotron-3-nano"
        result = get_thinking_tag(llm, True)
        assert result is None

    def test_llama_nemotron_v1_0_thinking_enabled(self):
        """Test Llama Nemotron v1.0 with thinking enabled."""
        llm = MagicMock()
        llm.model_name = "nvidia/llama-nemotron-v1-0"
        result = get_thinking_tag(llm, True)
        assert result == "detailed thinking on"

    def test_llama_nemotron_v1_0_thinking_disabled(self):
        """Test Llama Nemotron v1.0 with thinking disabled."""
        llm = MagicMock()
        llm.model_name = "nvidia/llama-nemotron-v1-0"
        result = get_thinking_tag(llm, False)
        assert result == "detailed thinking off"

    def test_llama_nemotron_v1_1_thinking_enabled(self):
        """Test Llama Nemotron v1.1 with thinking enabled."""
        llm = MagicMock()
        llm.model_name = "nvidia/llama-nemotron-v1-1"
        result = get_thinking_tag(llm, True)
        assert result == "detailed thinking on"

    def test_llama_nemotron_v1_5_thinking_enabled(self):
        """Test Llama Nemotron v1.5 with thinking enabled."""
        llm = MagicMock()
        llm.model_name = "nvidia/llama-nemotron-v1-5"
        result = get_thinking_tag(llm, True)
        assert result == "/think"

    def test_llama_nemotron_v1_5_thinking_disabled(self):
        """Test Llama Nemotron v1.5 with thinking disabled."""
        llm = MagicMock()
        llm.model_name = "nvidia/llama-nemotron-v1-5"
        result = get_thinking_tag(llm, False)
        assert result == "/no_think"

    def test_llama_nemotron_newer_version(self):
        """Test newer Llama Nemotron version uses /think format."""
        llm = MagicMock()
        llm.model_name = "nvidia/llama-nemotron-v2-0"
        result = get_thinking_tag(llm, True)
        assert result == "/think"

    def test_unknown_model(self):
        """Test unknown model returns None."""
        llm = MagicMock()
        llm.model_name = "unknown/model"
        result = get_thinking_tag(llm, True)
        assert result is None

    def test_model_name_with_underscores(self):
        """Test model name with underscores (normalized to dashes)."""
        llm = MagicMock()
        llm.model_name = "nvidia/nvidia_nemotron_4"
        result = get_thinking_tag(llm, True)
        assert result == "/think"

    def test_model_name_with_dots(self):
        """Test model name with dots (normalized to dashes)."""
        llm = MagicMock()
        llm.model_name = "nvidia/nvidia.nemotron.4"
        result = get_thinking_tag(llm, True)
        assert result == "/think"

    def test_azure_deployment_key(self):
        """Test using azure_deployment instead of model_name."""
        llm = MagicMock()
        llm.model_name = None
        llm.model = None
        llm.azure_deployment = "nvidia/nvidia-nemotron-4"
        result = get_thinking_tag(llm, True)
        assert result == "/think"

    def test_model_key(self):
        """Test using model key."""
        llm = MagicMock()
        llm.model_name = None
        llm.model = "nvidia/nvidia-nemotron-4"
        llm.azure_deployment = None
        result = get_thinking_tag(llm, True)
        assert result == "/think"

    def test_no_model_keys(self):
        """Test when no model keys are present."""
        llm = MagicMock(spec=[])  # No attributes
        result = get_thinking_tag(llm, True)
        assert result is None

    def test_llama_ends_with_v1(self):
        """Test Llama model ending with just 'v1'."""
        llm = MagicMock()
        llm.model_name = "nvidia/llama-nemotronv1"
        result = get_thinking_tag(llm, True)
        assert result == "detailed thinking on"


def _make_mock(class_name, model_name="", model=""):
    """Create a MagicMock whose type().__name__ returns *class_name*."""
    mock_cls = type(class_name, (MagicMock,), {})
    mock_llm = mock_cls()
    mock_llm.model_name = model_name
    mock_llm.model = model
    return mock_llm


class TestGetLlmReasoningBindKwargs:
    """Test get_llm_reasoning_bind_kwargs function."""

    # --- ChatNVIDIA / gpt-oss ---

    def test_chatnvidia_gpt_oss_reasoning_false(self):
        mock_llm = _make_mock("ChatNVIDIA", model_name="openai/gpt-oss-20b")
        result = get_llm_reasoning_bind_kwargs(mock_llm, llm_reasoning=False)
        assert result == {"reasoning_effort": "low"}

    def test_chatnvidia_gpt_oss_reasoning_true(self):
        mock_llm = _make_mock("ChatNVIDIA", model_name="openai/gpt-oss-20b")
        result = get_llm_reasoning_bind_kwargs(mock_llm, llm_reasoning=True)
        assert result == {"reasoning_effort": "medium"}

    def test_chatnvidia_gpt_oss_reasoning_none(self):
        mock_llm = _make_mock("ChatNVIDIA", model_name="openai/gpt-oss-20b")
        result = get_llm_reasoning_bind_kwargs(mock_llm, llm_reasoning=None)
        assert result == {}

    # --- ChatNVIDIA / nemotron-3 ---

    def test_chatnvidia_nemotron_reasoning_true(self):
        mock_llm = _make_mock("ChatNVIDIA", model_name="nvidia/nemotron-3-nano-30b-a3b")
        result = get_llm_reasoning_bind_kwargs(mock_llm, llm_reasoning=True)
        assert result == {"chat_template_kwargs": {"enable_thinking": True}}

    def test_chatnvidia_nemotron_reasoning_false(self):
        mock_llm = _make_mock("ChatNVIDIA", model_name="nvidia/nemotron-3-nano-30b-a3b")
        result = get_llm_reasoning_bind_kwargs(mock_llm, llm_reasoning=False)
        assert result == {"chat_template_kwargs": {"enable_thinking": False}}

    def test_chatnvidia_nemotron_reasoning_none(self):
        mock_llm = _make_mock("ChatNVIDIA", model_name="nvidia/nemotron-3-nano-30b-a3b")
        result = get_llm_reasoning_bind_kwargs(mock_llm, llm_reasoning=None)
        assert result == {}

    # --- ChatNVIDIA / other models ---

    def test_chatnvidia_unknown_model_returns_empty(self):
        mock_llm = _make_mock("ChatNVIDIA", model_name="nvidia/nvidia-nemotron-nano-9b-v2")
        result = get_llm_reasoning_bind_kwargs(mock_llm, llm_reasoning=True)
        assert result == {}

    # --- ChatNVIDIA / fallback to model attribute ---

    def test_chatnvidia_fallback_to_model_attribute(self):
        mock_llm = _make_mock("ChatNVIDIA", model="openai/gpt-oss-20b")
        result = get_llm_reasoning_bind_kwargs(mock_llm, llm_reasoning=True)
        assert result == {"reasoning_effort": "medium"}

    # --- ChatOpenAI ---

    def test_chatopenai_reasoning_true(self):
        mock_llm = _make_mock("ChatOpenAI", model_name="o3-mini")
        result = get_llm_reasoning_bind_kwargs(mock_llm, llm_reasoning=True)
        assert result == {"reasoning": {"effort": "medium", "summary": "auto"}}

    def test_chatopenai_reasoning_false(self):
        mock_llm = _make_mock("ChatOpenAI", model_name="o3-mini")
        result = get_llm_reasoning_bind_kwargs(mock_llm, llm_reasoning=False)
        assert result == {}

    def test_chatopenai_reasoning_none(self):
        mock_llm = _make_mock("ChatOpenAI", model_name="o3-mini")
        result = get_llm_reasoning_bind_kwargs(mock_llm, llm_reasoning=None)
        assert result == {}

    # --- Other / unsupported LLM type ---

    def test_other_llm_type_returns_empty(self):
        mock_llm = _make_mock("ChatAnthropic", model_name="claude-3")
        result = get_llm_reasoning_bind_kwargs(mock_llm, llm_reasoning=True)
        assert result == {}

    def test_other_llm_type_reasoning_false_returns_empty(self):
        mock_llm = _make_mock("ChatAnthropic", model_name="claude-3")
        result = get_llm_reasoning_bind_kwargs(mock_llm, llm_reasoning=False)
        assert result == {}
