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
"""Unit tests for llm_judge module."""

from unittest.mock import AsyncMock
from unittest.mock import MagicMock

from pydantic import ValidationError
import pytest
from vss_agents.evaluators.report_evaluator.field_evaluators.llm_judge import FieldEvaluation
from vss_agents.evaluators.report_evaluator.field_evaluators.llm_judge import LLMJudgeMetric


class TestFieldEvaluation:
    """Test FieldEvaluation model."""

    def test_field_evaluation_basic(self):
        eval_result = FieldEvaluation(score=0.85, reference_field="title")
        assert eval_result.score == 0.85
        assert eval_result.reference_field == "title"

    def test_field_evaluation_no_match(self):
        eval_result = FieldEvaluation(score=0.0, reference_field=None)
        assert eval_result.score == 0.0
        assert eval_result.reference_field is None

    def test_field_evaluation_perfect_score(self):
        eval_result = FieldEvaluation(score=1.0, reference_field="name")
        assert eval_result.score == 1.0

    def test_field_evaluation_score_bounds(self):
        # Score must be between 0 and 1
        eval_result = FieldEvaluation(score=0.0)
        assert eval_result.score == 0.0

        eval_result = FieldEvaluation(score=1.0)
        assert eval_result.score == 1.0

    def test_field_evaluation_invalid_score_above(self):
        with pytest.raises(ValidationError):
            FieldEvaluation(score=1.5)

    def test_field_evaluation_invalid_score_below(self):
        with pytest.raises(ValidationError):
            FieldEvaluation(score=-0.1)


class TestLLMJudgeMetric:
    """Test LLMJudgeMetric class."""

    def test_init_missing_llm(self):
        with pytest.raises(ValueError, match="requires 'llm_name'"):
            LLMJudgeMetric(single_field_comparison_prompt="test")

    def test_init_missing_prompt(self):
        mock_llm = MagicMock()
        with pytest.raises(ValueError, match="requires 'single_field_comparison_prompt'"):
            LLMJudgeMetric(llm=mock_llm)

    def test_init_success(self):
        mock_llm = MagicMock()
        metric = LLMJudgeMetric(
            llm=mock_llm,
            single_field_comparison_prompt="Compare: {reference} vs {actual}",
        )
        assert metric.llm is mock_llm
        assert metric.max_retries == 2

    def test_init_custom_max_retries(self):
        mock_llm = MagicMock()
        metric = LLMJudgeMetric(
            llm=mock_llm,
            single_field_comparison_prompt="test",
            max_retries=5,
        )
        assert metric.max_retries == 5

    def test_init_with_multi_field_prompt(self):
        mock_llm = MagicMock()
        metric = LLMJudgeMetric(
            llm=mock_llm,
            single_field_comparison_prompt="single",
            multi_field_discovery_prompt="multi",
        )
        assert metric.multi_field_discovery_prompt == "multi"

    @pytest.mark.asyncio
    async def test_evaluate_with_strings(self):
        # Create a mock response object with .content attribute
        mock_response = MagicMock()
        mock_response.content = "0.85"
        mock_response.reasoning_content = None
        mock_response.additional_kwargs = {}
        mock_response.response_metadata = {}

        mock_llm = AsyncMock()
        mock_llm.model_name = "test-model"
        mock_llm.ainvoke = AsyncMock(return_value=mock_response)

        metric = LLMJudgeMetric(
            llm=mock_llm,
            single_field_comparison_prompt="{field_context}\nReference: {reference}\nActual: {actual}",
        )

        result = await metric.evaluate("actual value", "reference value", "test_field")
        assert result == 0.85
        mock_llm.ainvoke.assert_called_once()

    @pytest.mark.asyncio
    async def test_evaluate_with_dicts(self):
        # Create a mock response object with .content attribute
        mock_response = MagicMock()
        mock_response.content = "0.9"
        mock_response.reasoning_content = None
        mock_response.additional_kwargs = {}
        mock_response.response_metadata = {}

        mock_llm = AsyncMock()
        mock_llm.model_name = "test-model"
        mock_llm.ainvoke = AsyncMock(return_value=mock_response)

        metric = LLMJudgeMetric(
            llm=mock_llm,
            single_field_comparison_prompt="{field_context}\nReference: {reference}\nActual: {actual}",
        )

        result = await metric.evaluate({"key": "value1"}, {"key": "value2"}, "dict_field")
        assert result == 0.9

    @pytest.mark.asyncio
    async def test_evaluate_llm_error_returns_none(self):
        mock_llm = AsyncMock()
        mock_llm.model_name = "test-model"
        mock_llm.ainvoke = AsyncMock(side_effect=Exception("LLM error"))

        metric = LLMJudgeMetric(
            llm=mock_llm,
            single_field_comparison_prompt="{field_context}\nReference: {reference}\nActual: {actual}",
            max_retries=0,
        )

        result = await metric.evaluate("actual", "reference")
        assert result is None

    @pytest.mark.asyncio
    async def test_evaluate_with_field_discovery_no_prompt(self):
        mock_llm = MagicMock()
        metric = LLMJudgeMetric(
            llm=mock_llm,
            single_field_comparison_prompt="test",
        )

        with pytest.raises(ValueError, match="multi_field_discovery_prompt"):
            await metric.evaluate_with_field_discovery(
                {"ref": "value"},
                {"actual": "value"},
                ["field1"],
            )

    @pytest.mark.asyncio
    async def test_evaluate_with_field_discovery_empty_fields(self):
        mock_llm = MagicMock()
        metric = LLMJudgeMetric(
            llm=mock_llm,
            single_field_comparison_prompt="test",
            multi_field_discovery_prompt="multi",
        )

        result = await metric.evaluate_with_field_discovery({}, {}, [])
        assert result == {}
