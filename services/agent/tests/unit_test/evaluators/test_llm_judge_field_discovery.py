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
"""Tests for LLM judge field discovery to cover remaining lines."""

from unittest.mock import AsyncMock
from unittest.mock import MagicMock
from unittest.mock import patch

import pytest

from vss_agents.evaluators.report_evaluator.field_evaluators.llm_judge import LLMJudgeMetric


class TestLLMJudgeFieldDiscoverySuccess:
    """Test successful field discovery."""

    @pytest.mark.asyncio
    async def test_field_discovery_success(self):
        mock_llm = MagicMock()
        mock_llm.model_name = "test"

        with patch(
            "vss_agents.evaluators.report_evaluator.field_evaluators.llm_judge.get_thinking_tag", return_value=None
        ):
            with patch(
                "vss_agents.evaluators.report_evaluator.field_evaluators.llm_judge.get_llm_reasoning_bind_kwargs",
                return_value={},
            ):
                metric = LLMJudgeMetric(
                    llm=mock_llm,
                    single_field_comparison_prompt="Compare {field_context} {reference} {actual}",
                    multi_field_discovery_prompt="Score: {reference_section} vs {actual_fields}",
                )

        # Create mock structured output
        mock_result = MagicMock()
        mock_field_eval = MagicMock()
        mock_field_eval.score = 0.85
        mock_field_eval.reference_field = "location"
        mock_result.field1 = mock_field_eval

        mock_structured_llm = AsyncMock()
        mock_structured_llm.ainvoke.return_value = mock_result
        metric.llm.with_structured_output = MagicMock(return_value=mock_structured_llm)

        result = await metric.evaluate_with_field_discovery(
            reference_section={"location": "San Jose"},
            actual_section={"field1": "San Jose, CA"},
            unspecified_fields=["field1"],
        )

        assert "field1" in result
        assert result["field1"]["score"] == 0.85
        assert result["field1"]["reference_field"] == "location"

    @pytest.mark.asyncio
    async def test_field_discovery_missing_attribute(self):
        """Test when structured output missing a field attribute."""
        mock_llm = MagicMock()
        mock_llm.model_name = "test"

        with patch(
            "vss_agents.evaluators.report_evaluator.field_evaluators.llm_judge.get_thinking_tag", return_value=None
        ):
            with patch(
                "vss_agents.evaluators.report_evaluator.field_evaluators.llm_judge.get_llm_reasoning_bind_kwargs",
                return_value={},
            ):
                metric = LLMJudgeMetric(
                    llm=mock_llm,
                    single_field_comparison_prompt="Compare {field_context} {reference} {actual}",
                    multi_field_discovery_prompt="Score: {reference_section} vs {actual_fields}",
                )

        mock_result = MagicMock(spec=[])  # Empty spec so getattr raises AttributeError
        del mock_result.missing_field  # Ensure attribute doesn't exist

        mock_structured_llm = AsyncMock()
        mock_structured_llm.ainvoke.return_value = mock_result
        metric.llm.with_structured_output = MagicMock(return_value=mock_structured_llm)

        result = await metric.evaluate_with_field_discovery(
            reference_section={"location": "SJ"},
            actual_section={"missing_field": "value"},
            unspecified_fields=["missing_field"],
        )

        assert result["missing_field"] is None

    @pytest.mark.asyncio
    async def test_evaluate_with_non_str_actual(self):
        """Test evaluate with non-string, non-dict actual value."""
        mock_llm = MagicMock()
        mock_llm.model_name = "test"

        with patch(
            "vss_agents.evaluators.report_evaluator.field_evaluators.llm_judge.get_thinking_tag", return_value=None
        ):
            with patch(
                "vss_agents.evaluators.report_evaluator.field_evaluators.llm_judge.get_llm_reasoning_bind_kwargs",
                return_value={},
            ):
                metric = LLMJudgeMetric(
                    llm=mock_llm,
                    single_field_comparison_prompt="Compare {field_context} reference: {reference} actual: {actual}",
                )

        mock_response = MagicMock()
        mock_response.content = "0.75"
        mock_response.additional_kwargs = {}
        metric.llm.ainvoke = AsyncMock(return_value=mock_response)

        with patch(
            "vss_agents.evaluators.report_evaluator.field_evaluators.llm_judge.parse_reasoning_content",
            return_value=(None, "0.75"),
        ):
            result = await metric.evaluate(42, 42, "number_field")
            assert result == 0.75

    @pytest.mark.asyncio
    async def test_invoke_with_thinking_tag(self):
        """Test _invoke_llm with thinking tag set."""
        mock_llm = MagicMock()
        mock_llm.model_name = "test"

        with patch(
            "vss_agents.evaluators.report_evaluator.field_evaluators.llm_judge.get_thinking_tag",
            return_value="<thinking>",
        ):
            with patch(
                "vss_agents.evaluators.report_evaluator.field_evaluators.llm_judge.get_llm_reasoning_bind_kwargs",
                return_value={},
            ):
                metric = LLMJudgeMetric(
                    llm=mock_llm,
                    single_field_comparison_prompt="Compare {field_context} {reference} {actual}",
                )

        mock_response = MagicMock()
        mock_response.content = "0.9"
        mock_response.additional_kwargs = {}
        metric.llm.ainvoke = AsyncMock(return_value=mock_response)

        with patch(
            "vss_agents.evaluators.report_evaluator.field_evaluators.llm_judge.parse_reasoning_content",
            return_value=(None, "0.9"),
        ):
            result = await metric._invoke_llm("test prompt", lambda x: float(x.strip()))
            assert result == 0.9
