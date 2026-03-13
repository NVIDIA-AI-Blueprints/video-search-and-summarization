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
"""Unit tests for evaluators/utils module."""

from unittest.mock import AsyncMock
from unittest.mock import MagicMock

from nat.eval.evaluator.evaluator_model import EvalInputItem
import pytest
from vss_agents.evaluators.utils import ScoreOutputParser
from vss_agents.evaluators.utils import compute_item_latency
from vss_agents.evaluators.utils import invoke_llm_with_retry
from vss_agents.evaluators.utils import should_evaluate
from vss_agents.evaluators.utils import strip_agent_think_tags


class TestShouldEvaluate:
    """Test should_evaluate function."""

    def test_missing_full_dataset_entry(self):
        item = EvalInputItem(
            id="test_001",
            input_obj="question",
            output_obj="answer",
            expected_output_obj="expected",
            full_dataset_entry={"evaluation_method": ["qa"]},
        )
        # Remove the attribute to simulate missing
        item.full_dataset_entry = None

        with pytest.raises(ValueError, match="missing full_dataset_entry"):
            should_evaluate(item, "qa")

    def test_missing_evaluation_method(self):
        item = EvalInputItem(
            id="test_002",
            input_obj="question",
            output_obj="answer",
            expected_output_obj="expected",
            full_dataset_entry={"other_field": "value"},  # No evaluation_method
        )

        with pytest.raises(ValueError, match="missing required 'evaluation_method'"):
            should_evaluate(item, "qa")

    def test_evaluation_method_not_list(self):
        item = EvalInputItem(
            id="test_003",
            input_obj="question",
            output_obj="answer",
            expected_output_obj="expected",
            full_dataset_entry={"evaluation_method": "qa"},  # String, not list
        )

        with pytest.raises(ValueError, match="Must be a list"):
            should_evaluate(item, "qa")

    def test_evaluator_type_in_list(self):
        item = EvalInputItem(
            id="test_004",
            input_obj="question",
            output_obj="answer",
            expected_output_obj="expected",
            full_dataset_entry={"evaluation_method": ["qa", "trajectory"]},
        )

        assert should_evaluate(item, "qa") is True
        assert should_evaluate(item, "trajectory") is True

    def test_evaluator_type_not_in_list(self):
        item = EvalInputItem(
            id="test_005",
            input_obj="question",
            output_obj="answer",
            expected_output_obj="expected",
            full_dataset_entry={"evaluation_method": ["trajectory"]},
        )

        assert should_evaluate(item, "qa") is False

    def test_empty_evaluation_method_list(self):
        item = EvalInputItem(
            id="test_006",
            input_obj="question",
            output_obj="answer",
            expected_output_obj="expected",
            full_dataset_entry={"evaluation_method": []},
        )

        assert should_evaluate(item, "qa") is False


class TestComputeItemLatency:
    """Test compute_item_latency function."""

    def _make_item(self, trajectory_timestamps=None):
        item = EvalInputItem(
            id="test",
            input_obj="q",
            output_obj=None,
            expected_output_obj=None,
            full_dataset_entry={},
        )
        if trajectory_timestamps is not None:
            item.trajectory = [MagicMock(event_timestamp=ts) for ts in trajectory_timestamps]
        else:
            item.trajectory = []
        return item

    def test_computes_latency_from_timestamps(self):
        item = self._make_item([10.0, 12.5, 15.0])
        assert compute_item_latency(item) == 5.0

    def test_single_timestamp_returns_zero(self):
        item = self._make_item([10.0])
        assert compute_item_latency(item) == 0.0

    def test_two_timestamps(self):
        item = self._make_item([5.0, 8.123])
        assert compute_item_latency(item) == 3.123

    def test_returns_none_for_empty_trajectory(self):
        item = self._make_item([])
        assert compute_item_latency(item) is None

    def test_returns_none_for_no_trajectory(self):
        item = self._make_item()
        assert compute_item_latency(item) is None

    def test_rounds_to_3_decimals(self):
        item = self._make_item([1.0, 1.12356])
        assert compute_item_latency(item) == 0.124


class TestStripAgentThinkTags:
    """Test strip_agent_think_tags function."""

    def test_no_tags(self):
        text = "This is normal text without tags."
        assert strip_agent_think_tags(text) == text

    def test_single_tag(self):
        text = "<agent-think>Some thinking</agent-think>The answer is 42."
        assert strip_agent_think_tags(text) == "The answer is 42."

    def test_multiple_tags(self):
        text = "<agent-think>Think 1</agent-think>Part 1<agent-think>Think 2</agent-think>Part 2"
        assert strip_agent_think_tags(text) == "Part 1Part 2"

    def test_multiline_tags(self):
        text = """<agent-think>
        This is
        multiline thinking
        </agent-think>The final answer."""
        assert strip_agent_think_tags(text) == "The final answer."

    def test_empty_string(self):
        assert strip_agent_think_tags("") == ""

    def test_none_input(self):
        assert strip_agent_think_tags(None) == ""

    def test_only_tags(self):
        text = "<agent-think>Only thinking here</agent-think>"
        assert strip_agent_think_tags(text) == ""


class TestScoreOutputParser:
    """Test ScoreOutputParser class."""

    def test_parse_simple_score(self):
        parser = ScoreOutputParser()

        mock_response = MagicMock()
        mock_response.content = "0.85"
        mock_response.reasoning_content = None
        mock_response.additional_kwargs = {}
        mock_response.response_metadata = {}

        result = parser.parse(mock_response)
        assert result["score"] == 0.85
        assert result["reasoning"] == ""

    def test_parse_score_with_text(self):
        parser = ScoreOutputParser()

        mock_response = MagicMock()
        mock_response.content = "The score is 0.75 based on analysis"
        mock_response.reasoning_content = None
        mock_response.additional_kwargs = {}
        mock_response.response_metadata = {}

        result = parser.parse(mock_response)
        assert result["score"] == 0.75

    def test_parse_with_think_tags(self):
        parser = ScoreOutputParser()

        mock_response = MagicMock()
        mock_response.content = "<think>My reasoning</think>0.8"
        mock_response.reasoning_content = None
        mock_response.additional_kwargs = {}
        mock_response.response_metadata = {}

        result = parser.parse(mock_response)
        assert result["score"] == 0.8
        assert "My reasoning" in result["reasoning"]


class TestInvokeLLMWithRetry:
    """Test invoke_llm_with_retry function."""

    @pytest.mark.asyncio
    async def test_successful_invocation(self):
        mock_response = MagicMock()
        mock_response.content = "0.9"
        mock_response.reasoning_content = None
        mock_response.additional_kwargs = {}
        mock_response.response_metadata = {}

        mock_llm = AsyncMock()
        mock_llm.model_name = "test-model"
        mock_llm.ainvoke = AsyncMock(return_value=mock_response)
        mock_llm.bind = MagicMock(return_value=mock_llm)

        parser = ScoreOutputParser()

        def build_reasoning(eval_result):
            return {"reasoning": eval_result["reasoning"]}

        result = await invoke_llm_with_retry(
            llm=mock_llm,
            prompt_text="Test prompt",
            output_parser=parser,
            item_id="test_001",
            max_retries=2,
            evaluator_name="Test Evaluator",
            question_preview="Test question...",
            build_reasoning=build_reasoning,
        )

        assert result.id == "test_001"
        assert result.score == 0.9
        mock_llm.ainvoke.assert_called_once()

    @pytest.mark.asyncio
    async def test_retry_on_failure(self):
        mock_response = MagicMock()
        mock_response.content = "0.8"
        mock_response.reasoning_content = None
        mock_response.additional_kwargs = {}
        mock_response.response_metadata = {}

        mock_llm = AsyncMock()
        mock_llm.model_name = "test-model"
        # First call fails, second succeeds
        mock_llm.ainvoke = AsyncMock(side_effect=[Exception("Temporary error"), mock_response])
        mock_llm.bind = MagicMock(return_value=mock_llm)

        parser = ScoreOutputParser()

        def build_reasoning(eval_result):
            return {"reasoning": eval_result["reasoning"]}

        result = await invoke_llm_with_retry(
            llm=mock_llm,
            prompt_text="Test prompt",
            output_parser=parser,
            item_id="test_002",
            max_retries=2,
            evaluator_name="Test Evaluator",
            question_preview="Test question...",
            build_reasoning=build_reasoning,
        )

        assert result.id == "test_002"
        assert result.score == 0.8
        assert mock_llm.ainvoke.call_count == 2

    @pytest.mark.asyncio
    async def test_exhausted_retries(self):
        mock_llm = AsyncMock()
        mock_llm.model_name = "test-model"
        mock_llm.ainvoke = AsyncMock(side_effect=Exception("Persistent error"))
        mock_llm.bind = MagicMock(return_value=mock_llm)

        parser = ScoreOutputParser()

        def build_reasoning(eval_result):
            return {"reasoning": eval_result["reasoning"]}

        result = await invoke_llm_with_retry(
            llm=mock_llm,
            prompt_text="Test prompt",
            output_parser=parser,
            item_id="test_003",
            max_retries=1,
            evaluator_name="Test Evaluator",
            question_preview="Test question...",
            build_reasoning=build_reasoning,
        )

        assert result.id == "test_003"
        assert result.score == 0.0
        assert "Error evaluating" in result.reasoning
        assert mock_llm.ainvoke.call_count == 2  # Initial + 1 retry

    @pytest.mark.asyncio
    async def test_llm_judge_reasoning_disabled(self):
        mock_response = MagicMock()
        mock_response.content = "0.7"
        mock_response.reasoning_content = None
        mock_response.additional_kwargs = {}
        mock_response.response_metadata = {}

        mock_llm = AsyncMock()
        mock_llm.model_name = "test-model"
        mock_llm.ainvoke = AsyncMock(return_value=mock_response)
        mock_llm.bind = MagicMock(return_value=mock_llm)

        parser = ScoreOutputParser()

        def build_reasoning(eval_result):
            return {"reasoning": eval_result["reasoning"]}

        result = await invoke_llm_with_retry(
            llm=mock_llm,
            prompt_text="Test prompt",
            output_parser=parser,
            item_id="test_004",
            max_retries=0,
            evaluator_name="Test Evaluator",
            question_preview="Test question...",
            build_reasoning=build_reasoning,
            llm_judge_reasoning=False,
        )

        assert result.id == "test_004"
        assert result.score == 0.7
