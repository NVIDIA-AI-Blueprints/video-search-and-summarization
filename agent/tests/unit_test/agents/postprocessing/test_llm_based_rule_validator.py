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

"""Unit tests for LLMBasedRuleValidator."""

from unittest.mock import AsyncMock
from unittest.mock import MagicMock
from unittest.mock import patch

from langchain_core.exceptions import OutputParserException
import pytest
from vss_agents.agents.postprocessing.validators.llm_based_rule_validator import LLMBasedRuleValidator
from vss_agents.agents.postprocessing.validators.llm_based_rule_validator import LLMBasedRuleValidatorOutput


@pytest.fixture
def mock_llm():
    """Create a mock LLM that returns structured output."""
    llm = MagicMock()
    llm.model_name = "test-model"
    # with_structured_output returns another mock that has ainvoke
    structured = AsyncMock()
    llm.with_structured_output = MagicMock(return_value=structured)
    return llm


class TestLLMBasedRuleValidatorInit:
    """Tests for LLMBasedRuleValidator initialization."""

    def test_custom_prompt_template(self, mock_llm):
        v = LLMBasedRuleValidator(llm=mock_llm, prompt_template="Custom: {output} {user_query} {trajectory}")
        assert v.prompt_template == "Custom: {output} {user_query} {trajectory}"

    def test_negative_max_retries_raises(self, mock_llm):
        with pytest.raises(ValueError, match="max_retries must be >= 0"):
            LLMBasedRuleValidator(llm=mock_llm, max_retries=-1)


class TestLLMBasedRuleValidatorValidate:
    """Tests for LLMBasedRuleValidator.validate()."""

    @pytest.mark.asyncio
    async def test_passes_when_llm_says_passed(self, mock_llm):
        llm_output = LLMBasedRuleValidatorOutput(passed=True, feedback="")
        mock_llm.with_structured_output.return_value.ainvoke = AsyncMock(return_value=llm_output)

        with (
            patch(
                "vss_agents.agents.postprocessing.validators.llm_based_rule_validator.get_thinking_tag", return_value=""
            ),
            patch(
                "vss_agents.agents.postprocessing.validators.llm_based_rule_validator.get_llm_reasoning_bind_kwargs",
                return_value={},
            ),
        ):
            v = LLMBasedRuleValidator(llm=mock_llm)
            result = await v.validate("good output", user_query="test query")
        assert result.passed is True
        assert result.issues == []

    @pytest.mark.asyncio
    async def test_fails_when_llm_says_failed(self, mock_llm):
        llm_output = LLMBasedRuleValidatorOutput(passed=False, feedback="needs improvement")
        mock_llm.with_structured_output.return_value.ainvoke = AsyncMock(return_value=llm_output)

        with (
            patch(
                "vss_agents.agents.postprocessing.validators.llm_based_rule_validator.get_thinking_tag", return_value=""
            ),
            patch(
                "vss_agents.agents.postprocessing.validators.llm_based_rule_validator.get_llm_reasoning_bind_kwargs",
                return_value={},
            ),
        ):
            v = LLMBasedRuleValidator(llm=mock_llm)
            result = await v.validate("bad output", user_query="test query")
        assert result.passed is False
        assert "needs improvement" in result.issues

    @pytest.mark.asyncio
    async def test_retries_on_output_parser_exception(self, mock_llm):
        """Should retry on OutputParserException, then succeed."""
        llm_output = LLMBasedRuleValidatorOutput(passed=True, feedback="")
        structured = mock_llm.with_structured_output.return_value
        structured.ainvoke = AsyncMock(side_effect=[OutputParserException("parse error"), llm_output])

        with (
            patch(
                "vss_agents.agents.postprocessing.validators.llm_based_rule_validator.get_thinking_tag", return_value=""
            ),
            patch(
                "vss_agents.agents.postprocessing.validators.llm_based_rule_validator.get_llm_reasoning_bind_kwargs",
                return_value={},
            ),
        ):
            v = LLMBasedRuleValidator(llm=mock_llm, max_retries=1)
            result = await v.validate("output", user_query="test")
        assert result.passed is True
        assert structured.ainvoke.call_count == 2

    @pytest.mark.asyncio
    async def test_raises_after_all_retries_exhausted(self, mock_llm):
        """Should raise the last exception after retries are exhausted."""
        structured = mock_llm.with_structured_output.return_value
        structured.ainvoke = AsyncMock(side_effect=OutputParserException("parse error"))

        with (
            patch(
                "vss_agents.agents.postprocessing.validators.llm_based_rule_validator.get_thinking_tag", return_value=""
            ),
            patch(
                "vss_agents.agents.postprocessing.validators.llm_based_rule_validator.get_llm_reasoning_bind_kwargs",
                return_value={},
            ),
        ):
            v = LLMBasedRuleValidator(llm=mock_llm, max_retries=1)
            with pytest.raises(OutputParserException):
                await v.validate("output", user_query="test")
        assert structured.ainvoke.call_count == 2  # 1 initial + 1 retry

    @pytest.mark.asyncio
    async def test_unexpected_exception_breaks_retry_loop(self, mock_llm):
        """Unexpected exceptions should break the retry loop immediately."""
        structured = mock_llm.with_structured_output.return_value
        structured.ainvoke = AsyncMock(side_effect=RuntimeError("unexpected"))

        with (
            patch(
                "vss_agents.agents.postprocessing.validators.llm_based_rule_validator.get_thinking_tag", return_value=""
            ),
            patch(
                "vss_agents.agents.postprocessing.validators.llm_based_rule_validator.get_llm_reasoning_bind_kwargs",
                return_value={},
            ),
        ):
            v = LLMBasedRuleValidator(llm=mock_llm, max_retries=3)
            with pytest.raises(RuntimeError):
                await v.validate("output", user_query="test")
        # Should have broken out after 1 attempt, not retried 3 times
        assert structured.ainvoke.call_count == 1

    @pytest.mark.asyncio
    async def test_bad_prompt_template_falls_back_to_default(self, mock_llm):
        """Bad prompt template should fall back to DEFAULT_PROMPT_TEMPLATE."""
        llm_output = LLMBasedRuleValidatorOutput(passed=True, feedback="")
        mock_llm.with_structured_output.return_value.ainvoke = AsyncMock(return_value=llm_output)

        with (
            patch(
                "vss_agents.agents.postprocessing.validators.llm_based_rule_validator.get_thinking_tag", return_value=""
            ),
            patch(
                "vss_agents.agents.postprocessing.validators.llm_based_rule_validator.get_llm_reasoning_bind_kwargs",
                return_value={},
            ),
        ):
            v = LLMBasedRuleValidator(llm=mock_llm, prompt_template="Bad template: {missing_key}")
            result = await v.validate("output", user_query="test")
        assert result.passed is True
