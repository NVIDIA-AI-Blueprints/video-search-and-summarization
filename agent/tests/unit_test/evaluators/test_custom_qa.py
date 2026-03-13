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
"""Unit tests for customized_qa_evaluator/evaluate module."""

from unittest.mock import AsyncMock
from unittest.mock import MagicMock

from nat.eval.evaluator.evaluator_model import EvalInputItem
import pytest
from vss_agents.evaluators.customized_qa_evaluator.evaluate import DEFAULT_QA_EVAL_PROMPT
from vss_agents.evaluators.customized_qa_evaluator.evaluate import CustomizedQAEvaluator


class TestDefaultQAEvalPrompt:
    """Test DEFAULT_QA_EVAL_PROMPT constant."""

    def test_prompt_exists(self):
        assert DEFAULT_QA_EVAL_PROMPT is not None

    def test_prompt_has_input_variables(self):
        assert "question" in DEFAULT_QA_EVAL_PROMPT.input_variables
        assert "answer" in DEFAULT_QA_EVAL_PROMPT.input_variables
        assert "reference" in DEFAULT_QA_EVAL_PROMPT.input_variables

    def test_prompt_template_content(self):
        assert "evaluator" in DEFAULT_QA_EVAL_PROMPT.template.lower()
        assert "score" in DEFAULT_QA_EVAL_PROMPT.template.lower()


class TestCustomizedQAEvaluator:
    """Test CustomizedQAEvaluator class."""

    def test_init_default_prompt(self):
        mock_llm = MagicMock()
        evaluator = CustomizedQAEvaluator(llm=mock_llm)

        assert evaluator.llm is mock_llm
        assert evaluator.max_retries == 2
        assert evaluator.evaluation_method_id == "qa"
        assert evaluator.llm_judge_reasoning is True
        assert evaluator.eval_prompt is DEFAULT_QA_EVAL_PROMPT

    def test_init_custom_prompt(self):
        from langchain_core.prompts import PromptTemplate

        mock_llm = MagicMock()
        custom_prompt = PromptTemplate(
            input_variables=["question", "answer", "reference"],
            template="Custom: {question} {answer} {reference}",
        )

        evaluator = CustomizedQAEvaluator(llm=mock_llm, custom_prompt=custom_prompt)
        assert evaluator.eval_prompt is custom_prompt

    def test_init_custom_params(self):
        mock_llm = MagicMock()
        evaluator = CustomizedQAEvaluator(
            llm=mock_llm,
            max_concurrency=16,
            max_retries=5,
            evaluation_method_id="custom_qa",
            llm_judge_reasoning=False,
        )

        assert evaluator.max_retries == 5
        assert evaluator.evaluation_method_id == "custom_qa"
        assert evaluator.llm_judge_reasoning is False

    @pytest.mark.asyncio
    async def test_evaluate_item_skips_wrong_method(self):
        mock_llm = MagicMock()
        evaluator = CustomizedQAEvaluator(llm=mock_llm, evaluation_method_id="qa")

        item = EvalInputItem(
            id="test_001",
            input_obj="What color is the truck?",
            output_obj="The truck is red.",
            expected_output_obj="Red",
            full_dataset_entry={"evaluation_method": ["trajectory"]},  # Not "qa"
        )

        result = await evaluator.evaluate_item(item)

        assert result.id == "test_001"
        assert result.score is None
        assert "Skipped" in result.reasoning

    @pytest.mark.asyncio
    async def test_evaluate_item_missing_ground_truth(self):
        mock_llm = MagicMock()
        evaluator = CustomizedQAEvaluator(llm=mock_llm, evaluation_method_id="qa")

        item = EvalInputItem(
            id="test_002",
            input_obj="What color is the truck?",
            output_obj="The truck is red.",
            expected_output_obj="",  # Empty ground truth
            full_dataset_entry={"evaluation_method": ["qa"]},
        )

        result = await evaluator.evaluate_item(item)

        assert result.id == "test_002"
        assert result.score == 0.0
        assert "no ground_truth" in result.reasoning

    @pytest.mark.asyncio
    async def test_evaluate_item_success(self):
        mock_response = MagicMock()
        mock_response.content = "0.85"
        mock_response.reasoning_content = None
        mock_response.additional_kwargs = {}
        mock_response.response_metadata = {}

        mock_llm = AsyncMock()
        mock_llm.model_name = "test-model"
        mock_llm.ainvoke = AsyncMock(return_value=mock_response)
        mock_llm.bind = MagicMock(return_value=mock_llm)

        evaluator = CustomizedQAEvaluator(llm=mock_llm, evaluation_method_id="qa")

        item = EvalInputItem(
            id="test_003",
            input_obj="What color is the truck?",
            output_obj="The truck is red.",
            expected_output_obj="Red",
            full_dataset_entry={"evaluation_method": ["qa"]},
        )

        result = await evaluator.evaluate_item(item)

        assert result.id == "test_003"
        assert result.score == 0.85
        mock_llm.ainvoke.assert_called_once()

    @pytest.mark.asyncio
    async def test_evaluate_item_strips_agent_think_tags(self):
        mock_response = MagicMock()
        mock_response.content = "0.9"
        mock_response.reasoning_content = None
        mock_response.additional_kwargs = {}
        mock_response.response_metadata = {}

        mock_llm = AsyncMock()
        mock_llm.model_name = "test-model"
        mock_llm.ainvoke = AsyncMock(return_value=mock_response)
        mock_llm.bind = MagicMock(return_value=mock_llm)

        evaluator = CustomizedQAEvaluator(llm=mock_llm, evaluation_method_id="qa")

        # Answer with agent-think tags that should be stripped
        item = EvalInputItem(
            id="test_004",
            input_obj="What color is the truck?",
            output_obj="<agent-think>Let me think...</agent-think>The truck is red.",
            expected_output_obj="Red",
            full_dataset_entry={"evaluation_method": ["qa"]},
        )

        result = await evaluator.evaluate_item(item)

        assert result.id == "test_004"
        assert result.score == 0.9
