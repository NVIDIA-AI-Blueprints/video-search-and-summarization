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
"""Additional unit tests for llm_judge module to improve coverage."""

from unittest.mock import AsyncMock
from unittest.mock import MagicMock
from unittest.mock import patch

import pytest
from vss_agents.evaluators.report_evaluator.field_evaluators.llm_judge import FieldEvaluation
from vss_agents.evaluators.report_evaluator.field_evaluators.llm_judge import LLMJudgeMetric


class TestFieldEvaluation:
    """Test FieldEvaluation model."""

    def test_basic(self):
        fe = FieldEvaluation(score=0.95, reference_field="location")
        assert fe.score == 0.95
        assert fe.reference_field == "location"

    def test_no_match(self):
        fe = FieldEvaluation(score=0.0, reference_field=None)
        assert fe.score == 0.0
        assert fe.reference_field is None

    def test_score_bounds(self):
        fe = FieldEvaluation(score=0.0)
        assert fe.score == 0.0
        fe = FieldEvaluation(score=1.0)
        assert fe.score == 1.0


class TestLLMJudgeMetricInit:
    """Test LLMJudgeMetric initialization."""

    def test_missing_llm_raises(self):
        with pytest.raises(ValueError, match="requires 'llm_name'"):
            LLMJudgeMetric(single_field_comparison_prompt="test")

    def test_missing_prompt_raises(self):
        mock_llm = MagicMock()
        mock_llm.model_name = "test"
        with patch(
            "vss_agents.evaluators.report_evaluator.field_evaluators.llm_judge.get_thinking_tag", return_value=None
        ):
            with patch(
                "vss_agents.evaluators.report_evaluator.field_evaluators.llm_judge.get_llm_reasoning_bind_kwargs",
                return_value={},
            ):
                with pytest.raises(ValueError, match="single_field_comparison_prompt"):
                    LLMJudgeMetric(llm=mock_llm)

    def test_valid_init(self):
        mock_llm = MagicMock()
        mock_llm.model_name = "test-model"
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
                assert metric.llm is mock_llm
                assert metric.max_retries == 2
                assert metric.llm_judge_reasoning is True

    def test_with_thinking_tag(self):
        mock_llm = MagicMock()
        mock_llm.model_name = "test-model"
        with patch(
            "vss_agents.evaluators.report_evaluator.field_evaluators.llm_judge.get_thinking_tag",
            return_value="<thinking>",
        ):
            with patch(
                "vss_agents.evaluators.report_evaluator.field_evaluators.llm_judge.get_llm_reasoning_bind_kwargs",
                return_value={"thinking": True},
            ):
                metric = LLMJudgeMetric(
                    llm=mock_llm,
                    single_field_comparison_prompt="Compare {field_context} ref: {reference} act: {actual}",
                )
                assert metric.thinking_tag == "<thinking>"

    def test_with_multi_field_prompt(self):
        mock_llm = MagicMock()
        mock_llm.model_name = "test-model"
        with patch(
            "vss_agents.evaluators.report_evaluator.field_evaluators.llm_judge.get_thinking_tag", return_value=None
        ):
            with patch(
                "vss_agents.evaluators.report_evaluator.field_evaluators.llm_judge.get_llm_reasoning_bind_kwargs",
                return_value={},
            ):
                metric = LLMJudgeMetric(
                    llm=mock_llm,
                    single_field_comparison_prompt="prompt {field_context} {reference} {actual}",
                    multi_field_discovery_prompt="multi prompt {reference_section} {actual_fields}",
                )
                assert metric.multi_field_discovery_prompt is not None


class TestLLMJudgeMetricEvaluate:
    """Test LLMJudgeMetric.evaluate method."""

    @pytest.fixture
    def mock_metric(self):
        mock_llm = MagicMock()
        mock_llm.model_name = "test-model"
        with patch(
            "vss_agents.evaluators.report_evaluator.field_evaluators.llm_judge.get_thinking_tag", return_value=None
        ):
            with patch(
                "vss_agents.evaluators.report_evaluator.field_evaluators.llm_judge.get_llm_reasoning_bind_kwargs",
                return_value={},
            ):
                return LLMJudgeMetric(
                    llm=mock_llm,
                    single_field_comparison_prompt="Compare {field_context} reference: {reference} actual: {actual}",
                )

    @pytest.mark.asyncio
    async def test_evaluate_success(self, mock_metric):
        mock_response = MagicMock()
        mock_response.content = "0.85"
        mock_response.additional_kwargs = {}
        mock_metric.llm.ainvoke = AsyncMock(return_value=mock_response)
        with patch(
            "vss_agents.evaluators.report_evaluator.field_evaluators.llm_judge.parse_reasoning_content",
            return_value=(None, "0.85"),
        ):
            result = await mock_metric.evaluate("actual value", "reference value", "test_field")
            assert result == 0.85

    @pytest.mark.asyncio
    async def test_evaluate_with_dict_values(self, mock_metric):
        mock_response = MagicMock()
        mock_response.content = "0.9"
        mock_response.additional_kwargs = {}
        mock_metric.llm.ainvoke = AsyncMock(return_value=mock_response)
        with patch(
            "vss_agents.evaluators.report_evaluator.field_evaluators.llm_judge.parse_reasoning_content",
            return_value=(None, "0.9"),
        ):
            result = await mock_metric.evaluate({"key": "actual"}, {"key": "reference"}, "test_field")
            assert result == 0.9

    @pytest.mark.asyncio
    async def test_evaluate_failure_returns_none(self, mock_metric):
        mock_metric.llm.ainvoke = AsyncMock(side_effect=RuntimeError("LLM error"))
        result = await mock_metric.evaluate("actual", "reference", "test_field")
        assert result is None


class TestLLMJudgeMetricInvokeLLM:
    """Test LLMJudgeMetric._invoke_llm method."""

    @pytest.fixture
    def mock_metric(self):
        mock_llm = MagicMock()
        mock_llm.model_name = "test-model"
        with patch(
            "vss_agents.evaluators.report_evaluator.field_evaluators.llm_judge.get_thinking_tag", return_value=None
        ):
            with patch(
                "vss_agents.evaluators.report_evaluator.field_evaluators.llm_judge.get_llm_reasoning_bind_kwargs",
                return_value={},
            ):
                return LLMJudgeMetric(
                    llm=mock_llm,
                    single_field_comparison_prompt="Compare {field_context} {reference} {actual}",
                    max_retries=2,
                )

    @pytest.mark.asyncio
    async def test_invoke_with_retries(self, mock_metric):
        mock_response = MagicMock()
        mock_response.content = "0.5"
        mock_response.additional_kwargs = {}

        mock_metric.llm.ainvoke = AsyncMock(side_effect=[ValueError("parse error"), mock_response])
        with patch(
            "vss_agents.evaluators.report_evaluator.field_evaluators.llm_judge.parse_reasoning_content",
            return_value=(None, "0.5"),
        ):
            result = await mock_metric._invoke_llm(
                prompt="test prompt",
                parser=lambda x: float(x.strip()),
                context="test",
            )
            assert result == 0.5

    @pytest.mark.asyncio
    async def test_invoke_all_retries_fail(self, mock_metric):
        mock_metric.llm.ainvoke = AsyncMock(side_effect=ValueError("always fails"))
        with pytest.raises(ValueError, match="LLM failed after"):
            await mock_metric._invoke_llm(
                prompt="test",
                parser=lambda x: float(x),
                context="test",
            )


class TestLLMJudgeMetricFieldDiscovery:
    """Test LLMJudgeMetric.evaluate_with_field_discovery method."""

    @pytest.fixture
    def mock_metric(self):
        mock_llm = MagicMock()
        mock_llm.model_name = "test-model"
        with patch(
            "vss_agents.evaluators.report_evaluator.field_evaluators.llm_judge.get_thinking_tag", return_value=None
        ):
            with patch(
                "vss_agents.evaluators.report_evaluator.field_evaluators.llm_judge.get_llm_reasoning_bind_kwargs",
                return_value={},
            ):
                return LLMJudgeMetric(
                    llm=mock_llm,
                    single_field_comparison_prompt="Compare {field_context} {reference} {actual}",
                    multi_field_discovery_prompt="Score: {reference_section} vs {actual_fields}",
                )

    @pytest.mark.asyncio
    async def test_empty_unspecified_fields(self, mock_metric):
        result = await mock_metric.evaluate_with_field_discovery(
            reference_section={}, actual_section={}, unspecified_fields=[]
        )
        assert result == {}

    @pytest.mark.asyncio
    async def test_missing_multi_field_prompt_raises(self):
        mock_llm = MagicMock()
        mock_llm.model_name = "test-model"
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
                )
                with pytest.raises(ValueError, match="multi_field_discovery_prompt"):
                    await metric.evaluate_with_field_discovery(
                        reference_section={"a": "b"},
                        actual_section={"a": "c"},
                        unspecified_fields=["a"],
                    )

    @pytest.mark.asyncio
    async def test_field_discovery_exception_returns_none(self, mock_metric):
        """Test that exceptions in field discovery return None for all fields."""
        mock_structured_llm = AsyncMock()
        mock_structured_llm.ainvoke.side_effect = RuntimeError("LLM error")
        mock_metric.llm.with_structured_output = MagicMock(return_value=mock_structured_llm)

        result = await mock_metric.evaluate_with_field_discovery(
            reference_section={"field1": "ref"},
            actual_section={"field1": "act"},
            unspecified_fields=["field1"],
        )
        assert result == {"field1": None}
