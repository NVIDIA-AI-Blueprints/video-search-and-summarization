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
"""Unit tests for customized_trajectory_evaluator/evaluate module."""

from unittest.mock import AsyncMock
from unittest.mock import MagicMock
from unittest.mock import patch

from langchain_core.exceptions import OutputParserException
from nat.data_models.evaluator import EvalInputItem
import pytest
from vss_agents.evaluators.customized_trajectory_evaluator.evaluate import CustomizedTrajectoryEvaluator
from vss_agents.evaluators.utils import ScoreOutputParser


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

    def test_parse_with_thinking(self):
        parser = ScoreOutputParser()

        mock_response = MagicMock()
        mock_response.content = "<think>My reasoning here.</think>0.75"
        mock_response.reasoning_content = None
        mock_response.additional_kwargs = {}
        mock_response.response_metadata = {}

        result = parser.parse(mock_response)
        assert result["score"] == 0.75
        assert result["reasoning"] == "My reasoning here."

    def test_parse_with_reasoning_content_attribute(self):
        parser = ScoreOutputParser()

        mock_response = MagicMock()
        mock_response.content = "0.9"
        mock_response.reasoning_content = "This is detailed reasoning"
        mock_response.additional_kwargs = {}
        mock_response.response_metadata = {}

        result = parser.parse(mock_response)
        assert result["score"] == 0.9
        assert "reasoning" in result["reasoning"]

    def test_parse_score_zero(self):
        parser = ScoreOutputParser()

        mock_response = MagicMock()
        mock_response.content = "0.0"
        mock_response.reasoning_content = None
        mock_response.additional_kwargs = {}
        mock_response.response_metadata = {}

        result = parser.parse(mock_response)
        assert result["score"] == 0.0

    def test_parse_score_one(self):
        parser = ScoreOutputParser()

        mock_response = MagicMock()
        mock_response.content = "1.0"
        mock_response.reasoning_content = None
        mock_response.additional_kwargs = {}
        mock_response.response_metadata = {}

        result = parser.parse(mock_response)
        assert result["score"] == 1.0

    def test_parse_no_score_raises_error(self):
        parser = ScoreOutputParser()

        mock_response = MagicMock()
        mock_response.content = "no numbers here"
        mock_response.reasoning_content = None
        mock_response.additional_kwargs = {}
        mock_response.response_metadata = {}

        with pytest.raises(OutputParserException):
            parser.parse(mock_response)

    def test_parse_score_out_of_range_raises_error(self):
        parser = ScoreOutputParser()

        mock_response = MagicMock()
        mock_response.content = "1.5"
        mock_response.reasoning_content = None
        mock_response.additional_kwargs = {}
        mock_response.response_metadata = {}

        with pytest.raises(OutputParserException):
            parser.parse(mock_response)


class TestCustomizedTrajectoryEvaluatorInit:
    """Test CustomizedTrajectoryEvaluator constructor."""

    def test_init_with_dual_prompts(self):
        mock_llm = MagicMock()
        mock_prompt_ref = MagicMock()
        mock_prompt_noref = MagicMock()

        evaluator = CustomizedTrajectoryEvaluator(
            llm=mock_llm,
            tools=None,
            prompt_with_reference=mock_prompt_ref,
            prompt_without_reference=mock_prompt_noref,
        )
        assert evaluator.prompt_with_reference is mock_prompt_ref
        assert evaluator.prompt_without_reference is mock_prompt_noref

    def test_init_defaults_to_none_prompts(self):
        mock_llm = MagicMock()
        evaluator = CustomizedTrajectoryEvaluator(llm=mock_llm, tools=None)
        assert evaluator.prompt_with_reference is None
        assert evaluator.prompt_without_reference is None


class TestExtractToolCallsFromLlmEnd:
    """Test _extract_tool_calls_from_llm_end with data.output parsing."""

    @pytest.fixture
    def evaluator(self):
        mock_llm = MagicMock()
        return CustomizedTrajectoryEvaluator(llm=mock_llm, tools=None)

    def test_parses_tool_calls_from_data_output(self, evaluator):
        step = MagicMock()
        step.data = MagicMock()
        step.data.output = (
            "\n\nTool calls: [{'id': 'call-1', 'type': 'function', "
            "'function': {'name': 'tool_a', 'arguments': '{\"param_1\": \"value_1\"}'}}]"
        )

        result = evaluator._extract_tool_calls_from_llm_end(step)
        assert len(result) == 1
        assert result[0]["function"]["name"] == "tool_a"

    def test_parses_openai_format_tool_calls(self, evaluator):
        step = MagicMock()
        step.data = MagicMock()
        step.data.output = "\n\nTool calls: [{'name': 'tool_a', 'args': {'param_1': 'value_1'}}]"

        result = evaluator._extract_tool_calls_from_llm_end(step)
        assert len(result) == 1
        assert result[0]["name"] == "tool_a"

    def test_parses_multiple_tool_calls(self, evaluator):
        step = MagicMock()
        step.data = MagicMock()
        step.data.output = (
            "\n\nTool calls: ["
            "{'name': 'tool_a', 'args': {'param_1': 'value_1'}}, "
            "{'name': 'tool_b', 'args': {'param_2': 'value_2'}}]"
        )

        result = evaluator._extract_tool_calls_from_llm_end(step)
        assert len(result) == 2

    def test_returns_empty_for_no_data(self, evaluator):
        step = MagicMock(spec=[])
        result = evaluator._extract_tool_calls_from_llm_end(step)
        assert result == []

    def test_returns_empty_for_no_tool_calls_string(self, evaluator):
        step = MagicMock()
        step.data = MagicMock()
        step.data.output = "Some other output without tool calls"

        result = evaluator._extract_tool_calls_from_llm_end(step)
        assert result == []

    def test_returns_empty_for_malformed_tool_calls(self, evaluator):
        step = MagicMock()
        step.data = MagicMock()
        step.data.output = "\n\nTool calls: not-valid-python"

        result = evaluator._extract_tool_calls_from_llm_end(step)
        assert result == []


class TestGetAgentSelectedUuids:
    """Test _get_agent_selected_uuids method."""

    @pytest.fixture
    def evaluator(self):
        """Create a CustomizedTrajectoryEvaluator instance for testing."""
        mock_llm = MagicMock()
        return CustomizedTrajectoryEvaluator(llm=mock_llm, tools=None)

    def _create_mock_step(self, event_type, uuid, parent_id, payload_name=None, tool_calls_output=None):
        """Helper to create mock trajectory steps using the new data.output format."""
        step = MagicMock()
        step.event_type = event_type
        step.UUID = uuid
        step.parent_id = parent_id
        step.payload = MagicMock()
        step.payload.name = payload_name

        if tool_calls_output:
            step.data = MagicMock()
            step.data.output = f"\n\nTool calls: {tool_calls_output}"
        else:
            step.data = MagicMock()
            step.data.output = ""

        return step

    def test_returns_llm_end_that_made_tool_selection(self, evaluator):
        """Test that LLM_END events that made tool selections are included."""
        from nat.data_models.intermediate_step import IntermediateStepType

        llm_uuid = "llm-uuid-1"
        tool_uuid = "tool-uuid-1"
        parent_id = "parent-1"

        tool_calls_str = "[{'function': {'name': 'search_tool'}}]"

        trajectory = [
            self._create_mock_step(IntermediateStepType.LLM_END, llm_uuid, parent_id, tool_calls_output=tool_calls_str),
            self._create_mock_step(IntermediateStepType.TOOL_END, tool_uuid, parent_id, payload_name="search_tool"),
        ]

        result = evaluator._get_agent_selected_uuids(trajectory)

        assert llm_uuid in result, "LLM_END that made tool selection should be included"
        assert tool_uuid in result, "TOOL_END that was selected should be included"

    def test_excludes_llm_end_without_tool_calls(self, evaluator):
        """Test that LLM_END events without tool calls are not included."""
        from nat.data_models.intermediate_step import IntermediateStepType

        llm_uuid = "llm-uuid-internal"
        parent_id = "parent-1"

        # LLM_END without tool_calls
        trajectory = [
            self._create_mock_step(IntermediateStepType.LLM_END, llm_uuid, parent_id),
        ]

        result = evaluator._get_agent_selected_uuids(trajectory)

        assert llm_uuid not in result, "LLM_END without tool calls should not be included"

    def test_multiple_tool_selections(self, evaluator):
        """Test that multiple tool selections are all included."""
        from nat.data_models.intermediate_step import IntermediateStepType

        llm_uuid = "llm-uuid"
        tool1_uuid = "tool1-uuid"
        tool2_uuid = "tool2-uuid"
        parent_id = "parent-1"

        tool_calls_str = "[{'function': {'name': 'tool_a'}}, {'function': {'name': 'tool_b'}}]"

        trajectory = [
            self._create_mock_step(IntermediateStepType.LLM_END, llm_uuid, parent_id, tool_calls_output=tool_calls_str),
            self._create_mock_step(IntermediateStepType.TOOL_END, tool1_uuid, parent_id, payload_name="tool_a"),
            self._create_mock_step(IntermediateStepType.TOOL_END, tool2_uuid, parent_id, payload_name="tool_b"),
        ]

        result = evaluator._get_agent_selected_uuids(trajectory)

        assert llm_uuid in result
        assert tool1_uuid in result
        assert tool2_uuid in result

    def test_nested_tool_calls_filtered(self, evaluator):
        """Test that nested tool calls (tools called by tools) are filtered out."""
        from nat.data_models.intermediate_step import IntermediateStepType

        agent_llm_uuid = "agent-llm-uuid"
        outer_tool_uuid = "outer-tool-uuid"
        nested_tool_uuid = "nested-tool-uuid"
        agent_parent_id = "agent-parent"
        outer_tool_parent_id = "outer-tool-parent"  # Different parent for nested calls

        tool_calls_str = "[{'function': {'name': 'outer_tool'}}]"

        nested_llm_uuid = "nested-llm-uuid"
        final_llm_uuid = "final-llm-uuid"

        trajectory = [
            # Agent's LLM selecting outer_tool
            self._create_mock_step(
                IntermediateStepType.LLM_END, agent_llm_uuid, agent_parent_id, tool_calls_output=tool_calls_str
            ),
            # Nested LLM call
            self._create_mock_step(IntermediateStepType.LLM_END, nested_llm_uuid, outer_tool_parent_id),
            # Nested tool call
            self._create_mock_step(
                IntermediateStepType.TOOL_END, nested_tool_uuid, outer_tool_parent_id, payload_name="nested_tool"
            ),
            # The outer tool result
            self._create_mock_step(
                IntermediateStepType.TOOL_END, outer_tool_uuid, agent_parent_id, payload_name="outer_tool"
            ),
            # Agent's final LLM response (no tool_calls)
            self._create_mock_step(IntermediateStepType.LLM_END, final_llm_uuid, agent_parent_id),
        ]

        result = evaluator._get_agent_selected_uuids(trajectory)

        assert agent_llm_uuid in result, "Agent's LLM with tool_calls should be included"
        assert outer_tool_uuid in result, "Agent-selected outer tool should be included"
        assert nested_llm_uuid not in result, "Nested LLM call should not be included"
        assert nested_tool_uuid not in result, "Nested tool call should not be included"
        assert final_llm_uuid not in result, "Agent's LLM without tool_calls should not be included"

    def test_tool_name_must_match(self, evaluator):
        """Test that TOOL_END is only matched when tool name matches the tool_call."""
        from nat.data_models.intermediate_step import IntermediateStepType

        llm_uuid = "llm-uuid"
        matching_tool_uuid = "matching-tool-uuid"
        non_matching_tool_uuid = "non-matching-tool-uuid"
        parent_id = "parent-1"

        tool_calls_str = "[{'function': {'name': 'expected_tool'}}]"

        trajectory = [
            self._create_mock_step(IntermediateStepType.LLM_END, llm_uuid, parent_id, tool_calls_output=tool_calls_str),
            # Tool with wrong name: should not be matched
            self._create_mock_step(
                IntermediateStepType.TOOL_END, non_matching_tool_uuid, parent_id, payload_name="wrong_tool"
            ),
            # Tool with correct name: should be matched
            self._create_mock_step(
                IntermediateStepType.TOOL_END, matching_tool_uuid, parent_id, payload_name="expected_tool"
            ),
        ]

        result = evaluator._get_agent_selected_uuids(trajectory)

        assert llm_uuid in result
        assert matching_tool_uuid in result, "Tool with matching name should be included"
        assert non_matching_tool_uuid not in result, "Tool with non-matching name should not be included"

    def test_tool_matching_respects_order(self, evaluator):
        """Test that tools are matched in order after LLM_END."""
        from nat.data_models.intermediate_step import IntermediateStepType

        llm_uuid = "llm-uuid"
        first_tool_uuid = "first-tool-uuid"
        second_tool_uuid = "second-tool-uuid"
        parent_id = "parent-1"

        # LLM calls same tool twice
        tool_calls_str = "[{'function': {'name': 'repeated_tool'}}, {'function': {'name': 'repeated_tool'}}]"

        trajectory = [
            self._create_mock_step(IntermediateStepType.LLM_END, llm_uuid, parent_id, tool_calls_output=tool_calls_str),
            self._create_mock_step(
                IntermediateStepType.TOOL_END, first_tool_uuid, parent_id, payload_name="repeated_tool"
            ),
            self._create_mock_step(
                IntermediateStepType.TOOL_END, second_tool_uuid, parent_id, payload_name="repeated_tool"
            ),
        ]

        result = evaluator._get_agent_selected_uuids(trajectory)

        assert llm_uuid in result
        assert first_tool_uuid in result, "First matching tool should be included"
        assert second_tool_uuid in result, "Second matching tool should also be included"

    def test_openai_format_tool_name(self, evaluator):
        """Test that OpenAI format tool names ({"name": "..."}) are matched."""
        from nat.data_models.intermediate_step import IntermediateStepType

        llm_uuid = "llm-uuid"
        tool_uuid = "tool-uuid"
        parent_id = "parent-1"

        tool_calls_str = "[{'name': 'my_tool', 'args': {'key': 'value'}}]"

        trajectory = [
            self._create_mock_step(IntermediateStepType.LLM_END, llm_uuid, parent_id, tool_calls_output=tool_calls_str),
            self._create_mock_step(IntermediateStepType.TOOL_END, tool_uuid, parent_id, payload_name="my_tool"),
        ]

        result = evaluator._get_agent_selected_uuids(trajectory)

        assert llm_uuid in result
        assert tool_uuid in result, "Tool matched via OpenAI format name should be included"


_EVAL_MODULE = "vss_agents.evaluators.customized_trajectory_evaluator.evaluate"
_ADAPTER_CLASS = "nat.plugins.eval.utils.intermediate_step_adapter.IntermediateStepAdapter"


class TestEvaluateItem:
    """Test evaluate_item method: prompt selection, structured tool calls, conversation history."""

    def _make_evaluator(self, prompt_with_ref=None, prompt_without_ref=None):
        return CustomizedTrajectoryEvaluator(
            llm=MagicMock(),
            tools=None,
            prompt_with_reference=prompt_with_ref,
            prompt_without_reference=prompt_without_ref,
        )

    def _make_item(self, item_id="test_001", query="What?", output="Answer", full_dataset_entry=None):
        item = EvalInputItem(
            id=item_id,
            input_obj=query,
            output_obj=output,
            expected_output_obj=None,
            full_dataset_entry=full_dataset_entry or {"evaluation_method": ["trajectory"]},
        )
        item.trajectory = []
        return item

    def _make_agent_action(self, tool_name, tool_input):
        """Create a mock AgentAction as returned by IntermediateStepAdapter.get_agent_actions."""
        action = MagicMock()
        action.tool = tool_name
        action.tool_input = tool_input
        action.model_dump.return_value = {"tool": tool_name, "tool_input": tool_input, "log": ""}
        return action

    # --- Prompt selection ---

    @pytest.mark.asyncio
    @patch(f"{_EVAL_MODULE}.invoke_llm_with_retry", new_callable=AsyncMock)
    @patch(_ADAPTER_CLASS)
    async def test_uses_prompt_with_reference(self, mock_adapter, mock_invoke):
        """When item has trajectory_ground_truth, uses prompt_with_reference."""
        mock_prompt = MagicMock()
        mock_prompt.format.return_value = "formatted prompt"
        evaluator = self._make_evaluator(prompt_with_ref=mock_prompt)

        mock_adapter.return_value.get_agent_actions.return_value = []
        mock_invoke.return_value = MagicMock(id="test_001", score=0.8)

        item = self._make_item(
            full_dataset_entry={
                "evaluation_method": ["trajectory"],
                "trajectory_ground_truth": [{"step": 1, "name": "tool_a"}],
            }
        )

        await evaluator.evaluate_item(item)

        mock_prompt.format.assert_called_once()
        fmt_kwargs = mock_prompt.format.call_args.kwargs
        assert "reference" in fmt_kwargs
        assert "question" in fmt_kwargs
        assert "agent_trajectory" in fmt_kwargs
        assert "answer" in fmt_kwargs

    @pytest.mark.asyncio
    @patch(f"{_EVAL_MODULE}.invoke_llm_with_retry", new_callable=AsyncMock)
    @patch(_ADAPTER_CLASS)
    async def test_uses_prompt_without_reference(self, mock_adapter, mock_invoke):
        """When item has no trajectory_ground_truth, uses prompt_without_reference."""
        mock_prompt = MagicMock()
        mock_prompt.format.return_value = "formatted prompt"
        evaluator = self._make_evaluator(prompt_without_ref=mock_prompt)

        mock_adapter.return_value.get_agent_actions.return_value = []
        mock_invoke.return_value = MagicMock(id="test_001", score=0.8)

        item = self._make_item(full_dataset_entry={"evaluation_method": ["trajectory"]})

        await evaluator.evaluate_item(item)

        mock_prompt.format.assert_called_once()
        fmt_kwargs = mock_prompt.format.call_args.kwargs
        assert "conversation_history" in fmt_kwargs
        assert "tool_schemas" in fmt_kwargs
        assert "question" in fmt_kwargs

    @pytest.mark.asyncio
    @patch(_ADAPTER_CLASS)
    async def test_raises_when_reference_but_no_prompt(self, mock_adapter):
        """ValueError when item has trajectory_ground_truth but prompt_with_reference is not configured."""
        evaluator = self._make_evaluator()
        mock_adapter.return_value.get_agent_actions.return_value = []

        item = self._make_item(
            full_dataset_entry={
                "evaluation_method": ["trajectory"],
                "trajectory_ground_truth": [{"step": 1, "name": "tool_a"}],
            }
        )

        with pytest.raises(ValueError, match="custom_prompt_template_with_reference"):
            await evaluator.evaluate_item(item)

    @pytest.mark.asyncio
    @patch(_ADAPTER_CLASS)
    async def test_raises_when_no_reference_and_no_prompt(self, mock_adapter):
        """ValueError when item has no trajectory_ground_truth and prompt_without_reference is not configured."""
        evaluator = self._make_evaluator()
        mock_adapter.return_value.get_agent_actions.return_value = []

        item = self._make_item(full_dataset_entry={"evaluation_method": ["trajectory"]})

        with pytest.raises(ValueError, match="custom_prompt_template_without_reference"):
            await evaluator.evaluate_item(item)

    # --- Structured tool call extraction ---

    @pytest.mark.asyncio
    @patch(f"{_EVAL_MODULE}.invoke_llm_with_retry", new_callable=AsyncMock)
    @patch(_ADAPTER_CLASS)
    async def test_structured_tool_calls_step_numbering(self, mock_adapter, mock_invoke):
        """Parallel tool calls share a step number; new LLM step increments it."""
        mock_prompt = MagicMock()
        mock_prompt.format.return_value = "prompt"
        evaluator = self._make_evaluator(prompt_with_ref=mock_prompt)

        action_tool_a = self._make_agent_action("tool_a", {"p1": "v1"})
        action_tool_b = self._make_agent_action("tool_b", {"p2": "v2"})
        action_tool_c = self._make_agent_action("tool_c", {"p3": "v3"})

        mock_adapter.return_value.get_agent_actions.return_value = [
            # LLM step 1: selects tool_a and tool_b in parallel
            (self._make_agent_action("", ""), "reasoning\n\nTool calls: [{'name': 'tool_a'}, {'name': 'tool_b'}]"),
            (action_tool_a, "result_a"),
            (action_tool_b, "result_b"),
            # LLM step 2: selects tool_c
            (self._make_agent_action("", ""), "more reasoning\n\nTool calls: [{'name': 'tool_c'}]"),
            (action_tool_c, "result_c"),
        ]
        mock_invoke.return_value = MagicMock(id="test_001", score=0.9)

        item = self._make_item(
            full_dataset_entry={
                "evaluation_method": ["trajectory"],
                "trajectory_ground_truth": [{"step": 1, "name": "tool_a"}],
            }
        )

        await evaluator.evaluate_item(item)

        build_reasoning = mock_invoke.call_args.kwargs["build_reasoning"]
        actual = build_reasoning({"reasoning": "r"})["actual_tool_calls"]
        assert len(actual) == 3
        assert actual[0] == {"step": 1, "name": "tool_a", "params": {"p1": "v1"}}
        assert actual[1] == {"step": 1, "name": "tool_b", "params": {"p2": "v2"}}
        assert actual[2] == {"step": 2, "name": "tool_c", "params": {"p3": "v3"}}

    @pytest.mark.asyncio
    @patch(f"{_EVAL_MODULE}.invoke_llm_with_retry", new_callable=AsyncMock)
    @patch(_ADAPTER_CLASS)
    async def test_tool_with_no_preceding_llm_defaults_to_step_1(self, mock_adapter, mock_invoke):
        """Tool with no preceding LLM reasoning step gets default step number 1."""
        mock_prompt = MagicMock()
        mock_prompt.format.return_value = "prompt"
        evaluator = self._make_evaluator(prompt_with_ref=mock_prompt)

        action_tool = self._make_agent_action("tool_a", {"p": "v"})
        mock_adapter.return_value.get_agent_actions.return_value = [
            (action_tool, "result"),
        ]
        mock_invoke.return_value = MagicMock(id="test_001", score=0.9)

        item = self._make_item(
            full_dataset_entry={
                "evaluation_method": ["trajectory"],
                "trajectory_ground_truth": [{"step": 1, "name": "tool_a"}],
            }
        )

        await evaluator.evaluate_item(item)

        build_reasoning = mock_invoke.call_args.kwargs["build_reasoning"]
        actual = build_reasoning({"reasoning": "r"})["actual_tool_calls"]
        assert actual[0]["step"] == 1

    @pytest.mark.asyncio
    @patch(f"{_EVAL_MODULE}.invoke_llm_with_retry", new_callable=AsyncMock)
    @patch(_ADAPTER_CLASS)
    async def test_string_tool_input_is_parsed(self, mock_adapter, mock_invoke):
        """String tool_input is parsed via ast.literal_eval into a dict."""
        mock_prompt = MagicMock()
        mock_prompt.format.return_value = "prompt"
        evaluator = self._make_evaluator(prompt_with_ref=mock_prompt)

        action_tool = self._make_agent_action("tool_a", "{'key': 'value'}")
        mock_adapter.return_value.get_agent_actions.return_value = [
            (action_tool, "result"),
        ]
        mock_invoke.return_value = MagicMock(id="test_001", score=0.9)

        item = self._make_item(
            full_dataset_entry={
                "evaluation_method": ["trajectory"],
                "trajectory_ground_truth": [{"step": 1, "name": "tool_a"}],
            }
        )

        await evaluator.evaluate_item(item)

        build_reasoning = mock_invoke.call_args.kwargs["build_reasoning"]
        actual = build_reasoning({"reasoning": "r"})["actual_tool_calls"]
        assert actual[0]["params"] == {"key": "value"}

    # --- Conversation history ---

    @pytest.mark.asyncio
    @patch(f"{_EVAL_MODULE}.invoke_llm_with_retry", new_callable=AsyncMock)
    @patch(_ADAPTER_CLASS)
    async def test_conversation_history_formatted_in_prompt(self, mock_adapter, mock_invoke):
        """Conversation history from _conversation_history is formatted and passed to prompt."""
        mock_prompt = MagicMock()
        mock_prompt.format.return_value = "prompt"
        evaluator = self._make_evaluator(prompt_without_ref=mock_prompt)

        mock_adapter.return_value.get_agent_actions.return_value = []
        mock_invoke.return_value = MagicMock(id="test_001", score=0.8)

        item = self._make_item(
            full_dataset_entry={
                "evaluation_method": ["trajectory"],
                "_conversation_history": [
                    {"turn_id": "turn_1", "query": "Hello", "answer": "Hi"},
                    {"turn_id": "turn_2", "query": "More?", "answer": "Sure"},
                ],
            }
        )

        await evaluator.evaluate_item(item)

        history_str = mock_prompt.format.call_args.kwargs["conversation_history"]
        assert "[turn_1] User: Hello" in history_str
        assert "[turn_1] Assistant: Hi" in history_str
        assert "[turn_2] User: More?" in history_str
        assert "[turn_2] Assistant: Sure" in history_str

    @pytest.mark.asyncio
    @patch(f"{_EVAL_MODULE}.invoke_llm_with_retry", new_callable=AsyncMock)
    @patch(_ADAPTER_CLASS)
    async def test_no_conversation_history_shows_placeholder(self, mock_adapter, mock_invoke):
        """Without _conversation_history, prompt receives a placeholder string."""
        mock_prompt = MagicMock()
        mock_prompt.format.return_value = "prompt"
        evaluator = self._make_evaluator(prompt_without_ref=mock_prompt)

        mock_adapter.return_value.get_agent_actions.return_value = []
        mock_invoke.return_value = MagicMock(id="test_001", score=0.8)

        item = self._make_item(full_dataset_entry={"evaluation_method": ["trajectory"]})

        await evaluator.evaluate_item(item)

        assert mock_prompt.format.call_args.kwargs["conversation_history"] == "(no previous turns)"

    # --- build_reasoning output ---

    @pytest.mark.asyncio
    @patch(f"{_EVAL_MODULE}.invoke_llm_with_retry", new_callable=AsyncMock)
    @patch(_ADAPTER_CLASS)
    async def test_build_reasoning_includes_all_fields(self, mock_adapter, mock_invoke):
        """build_reasoning callback produces dict with all expected fields."""
        mock_prompt = MagicMock()
        mock_prompt.format.return_value = "prompt"
        evaluator = self._make_evaluator(prompt_with_ref=mock_prompt)

        mock_adapter.return_value.get_agent_actions.return_value = []
        mock_invoke.return_value = MagicMock(id="test_001", score=0.8)

        ground_truth = [{"step": 1, "name": "tool_a"}]
        conv_history = [{"turn_id": "t1", "query": "q", "answer": "a"}]
        item = self._make_item(
            query="What is X?",
            output="X is Y",
            full_dataset_entry={
                "evaluation_method": ["trajectory"],
                "trajectory_ground_truth": ground_truth,
                "_conversation_history": conv_history,
            },
        )

        await evaluator.evaluate_item(item)

        build_reasoning = mock_invoke.call_args.kwargs["build_reasoning"]
        result = build_reasoning({"reasoning": "my reasoning"})
        assert result["reasoning"] == "my reasoning"
        assert result["query"] == "What is X?"
        assert result["expected_tool_calls"] == ground_truth
        assert result["final_answer"] == "X is Y"
        assert isinstance(result["actual_tool_calls"], list)
        assert result["conversation_history"] == conv_history
        assert result["track_agent_selected_tools_only"] is False
