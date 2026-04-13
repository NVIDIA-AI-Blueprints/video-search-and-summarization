# SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

# Adapted from https://github.com/NVIDIA/NeMo-Agent-Toolkit/blob/e8dbc1574a2ae53e4fdcd92ad75118024ee37047/packages/nvidia_nat_core/src/nat/eval/trajectory_evaluator/evaluate.py;
# https://github.com/langchain-ai/langchain/tree/master/libs/langchain/langchain_classic/evaluation/agents

import ast
import contextlib
import json
import logging
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.prompts import PromptTemplate
from langchain_core.tools import BaseTool
from langchain_core.utils.function_calling import convert_to_openai_function
from nat.eval.evaluator.base_evaluator import BaseEvaluator
from nat.eval.evaluator.evaluator_model import EvalInputItem
from nat.eval.evaluator.evaluator_model import EvalOutputItem

from vss_agents.evaluators.utils import ScoreOutputParser
from vss_agents.evaluators.utils import invoke_llm_with_retry
from vss_agents.evaluators.utils import should_evaluate
from vss_agents.evaluators.utils import strip_agent_think_tags

logger = logging.getLogger(__name__)


class CustomizedTrajectoryEvaluator(BaseEvaluator):
    def __init__(
        self,
        llm: BaseChatModel,
        tools: list[BaseTool] | None = None,
        max_concurrency: int = 8,
        track_agent_selected_tools_only: bool = False,
        prompt_with_reference: PromptTemplate | None = None,
        prompt_without_reference: PromptTemplate | None = None,
        max_retries: int = 2,
        evaluation_method_id: str = "trajectory",
        llm_judge_reasoning: bool = True,
    ):
        super().__init__(max_concurrency=max_concurrency, tqdm_desc="Evaluating Trajectory")
        self.llm = llm
        self.tools = tools
        self.track_agent_selected_tools_only = track_agent_selected_tools_only
        self.max_retries = max_retries
        self.evaluation_method_id = evaluation_method_id
        self.llm_judge_reasoning = llm_judge_reasoning

        self.prompt_with_reference = prompt_with_reference
        self.prompt_without_reference = prompt_without_reference
        self.output_parser = ScoreOutputParser()

        logger.info(f"Prompt with reference: {'provided' if self.prompt_with_reference else 'not provided'}")
        logger.info(f"Prompt without reference: {'provided' if self.prompt_without_reference else 'not provided'}")
        logger.info(f"Evaluation method ID: {self.evaluation_method_id}")
        logger.info(f"LLM judge reasoning: {self.llm_judge_reasoning}")
        logger.debug("Trajectory evaluator initialized.")

    def _format_tool_schemas(self) -> str:
        """Get the description of the agent tools including their parameters.

        Returns:
            str: The description of the agent tools with schemas.
        """

        if not self.tools:
            return "No tools available for the agent."

        formatted_schemas = []
        for i, tool in enumerate(self.tools, 1):
            tool_schema = convert_to_openai_function(tool)
            tool_desc = (
                f"Tool {i}: {tool_schema['name']}\n"
                f"Description: {tool_schema['description']}\n"
                f"Parameters: {json.dumps(tool_schema['parameters'], indent=2)}"
            )
            formatted_schemas.append(tool_desc)

        return "\n\n".join(formatted_schemas)

    def _extract_tool_calls_from_llm_end(self, llm_end_step: Any) -> list[dict[str, Any]]:
        """
        Extract tool_calls from an LLM_END step's data.output string in the workflow output.

        Args:
            llm_end_step: An LLM_END intermediate step

        Returns:
            list: List of tool call dictionaries
        """
        # Parse tool calls from data.output string in the workflow output
        # Format: "\n\nTool calls: [{'name': '...', 'args': {...}, ...}]"
        if hasattr(llm_end_step, "data") and llm_end_step.data:
            output = getattr(llm_end_step.data, "output", "") or ""
            if isinstance(output, str) and "Tool calls:" in output:
                try:
                    tc_str = output.split("Tool calls:", 1)[1].strip()
                    parsed = ast.literal_eval(tc_str)
                    if isinstance(parsed, list):
                        return parsed
                except Exception:
                    logger.debug(f"Failed to parse tool calls from data.output: {output[:200]}")

        return []

    def _get_agent_selected_uuids(self, trajectory: list[Any]) -> set[str]:
        """
        Extract UUIDs of tools and LLMs that were part of agent's tool selection.

        For each LLM_END, sequentially match the tools it called with the next
        TOOL_ENDs at the same hierarchy level.

        Matching is done by:
        - Hierarchy level (parent_id must match)
        - Tool name (from payload.name)
        - Sequential order (first unmatched tool after LLM_END)

        Args:
            trajectory: Full ordered list of trajectory steps

        Returns:
            set: Set of UUIDs for TOOL_END and LLM_END steps that were part of agent's tool selection.
        """
        from nat.data_models.intermediate_step import IntermediateStepType

        agent_selected_uuids = set()

        # Process each LLM_END in order
        for i, step in enumerate(trajectory):
            if step.event_type != IntermediateStepType.LLM_END:
                continue

            tool_calls = self._extract_tool_calls_from_llm_end(step)
            if not tool_calls:
                continue

            llm_parent_id = step.parent_id

            # This LLM made tool selections, so include it
            agent_selected_uuids.add(step.UUID)

            # For each tool call, find the next matching TOOL_END
            for tool_call in tool_calls:
                # NIM format: {"function": {"name": "..."}}
                # OpenAI format: {"name": "..."}
                tool_name = tool_call.get("function", {}).get("name") or tool_call.get("name")
                if not tool_name:
                    continue

                # Find the next TOOL_END after this LLM_END that matches
                for j in range(i + 1, len(trajectory)):
                    tool_step = trajectory[j]

                    if tool_step.event_type != IntermediateStepType.TOOL_END:
                        continue

                    # Skip if already matched
                    if tool_step.UUID in agent_selected_uuids:
                        continue

                    # TOOL_END must be at same level as LLM_END
                    if tool_step.parent_id != llm_parent_id:
                        continue

                    # Check tool name matches
                    if tool_step.payload.name == tool_name:
                        agent_selected_uuids.add(tool_step.UUID)
                        break  # Found match for this tool_call, move to next

        return agent_selected_uuids

    async def evaluate_item(self, item: EvalInputItem) -> EvalOutputItem:
        """
        Evaluate a single EvalInputItem and return an EvalOutputItem.
        """
        if not should_evaluate(item, self.evaluation_method_id):
            logger.info(
                f"Skipping evaluation for item {item.id} - '{self.evaluation_method_id}' not in evaluation_method"
            )
            return EvalOutputItem(
                id=item.id, score=None, reasoning=f"Skipped: not marked for {self.evaluation_method_id} evaluation"
            )

        from typing import Any

        from nat.data_models.intermediate_step import IntermediateStepType
        import nat.eval.intermediate_step_adapter as adapter_module
        from nat.eval.intermediate_step_adapter import IntermediateStepAdapter
        from pydantic import BaseModel

        # Redefine AgentAction to accept list for multimodal inputs
        class AgentAction(BaseModel):
            tool: str
            tool_input: str | dict[str, Any] | list[Any]  # Added list support
            log: str = ""

        # Patch permanently - other eval code can also benefit from list support
        adapter_module.AgentAction = AgentAction

        intermediate_step_adapter = IntermediateStepAdapter()
        event_filter = [IntermediateStepType.LLM_END, IntermediateStepType.TOOL_END]

        question = item.input_obj
        # Strip out <agent-think> tags from generated answer
        generated_answer = strip_agent_think_tags(item.output_obj)

        trajectory = item.trajectory

        if self.track_agent_selected_tools_only:
            logger.info("Filtering trajectory to only include agent-selected tools")

            # Extract UUIDs of agent-selected tools and the LLMs that selected them
            agent_selected_uuids = self._get_agent_selected_uuids(trajectory)
            logger.info(f"Found {len(agent_selected_uuids)} agent-selected steps")

            # Filter trajectory to only include agent-selected tools
            filtered_trajectory = []
            for step in trajectory:
                if step.event_type in (IntermediateStepType.TOOL_END, IntermediateStepType.LLM_END):
                    # Only keep tools that were agent-selected
                    if step.UUID in agent_selected_uuids:
                        filtered_trajectory.append(step)
                else:
                    filtered_trajectory.append(step)

            trajectory = filtered_trajectory
            logger.info(f"Filtered to {len(trajectory)} steps")

        # Convert filtered trajectory to agent actions
        agent_trajectory = intermediate_step_adapter.get_agent_actions(trajectory, event_filter)

        logger.info(f"After filtering LLM reasoning steps: {len(agent_trajectory)} steps remain")

        # Extract tool calls with step numbers.
        # Each LLM reasoning step (contains "Tool calls:") marks the start of a new step.
        # Tools following the same LLM step are parallel (same step number).
        structured_tool_calls = []
        step_number = 0
        for action, output in agent_trajectory:
            if isinstance(output, str) and "Tool calls:" in str(output):
                step_number += 1
                continue
            if step_number == 0:
                step_number = 1  # No LLM step seen yet, default to step 1
            params = action.tool_input
            if isinstance(params, str):
                with contextlib.suppress(Exception):
                    params = ast.literal_eval(params)
            structured_tool_calls.append({"step": step_number, "name": action.tool, "params": params})

        # Get conversation history from previous turns (multi-turn only)
        conv_history = []
        if hasattr(item, "full_dataset_entry") and item.full_dataset_entry:
            conv_history = item.full_dataset_entry.get("_conversation_history", [])
            if not isinstance(conv_history, list):
                conv_history = []

        # Auto-detect: check if this item has trajectory_ground_truth
        reference = None
        if hasattr(item, "full_dataset_entry") and item.full_dataset_entry:
            reference = item.full_dataset_entry.get("trajectory_ground_truth")

        has_reference = reference is not None

        if has_reference and self.prompt_with_reference:
            # Reference mode: compare structured tool calls against ground truth
            if structured_tool_calls:
                actual_tool_calls_str = json.dumps(structured_tool_calls, indent=2)
            else:
                actual_tool_calls_str = "(no tool calls)"

            reference_str = json.dumps(reference, indent=2)

            prompt_text = self.prompt_with_reference.format(
                question=question,
                agent_trajectory=actual_tool_calls_str,
                answer=generated_answer,
                reference=reference_str,
            )
        elif not has_reference and self.prompt_without_reference:
            # No-reference mode: evaluate trajectory without ground truth
            trajectory_str = "\n".join(
                [
                    f"Action: {action.tool}\nInput: {action.tool_input}\nObservation: {output}"
                    for action, output in agent_trajectory
                ]
            )

            if conv_history:
                history_lines = []
                for turn in conv_history:
                    history_lines.append(f"[{turn['turn_id']}] User: {turn['query']}")
                    history_lines.append(f"[{turn['turn_id']}] Assistant: {turn['answer']}")
                conversation_history_str = "\n".join(history_lines)
            else:
                conversation_history_str = "(no previous turns)"

            prompt_text = self.prompt_without_reference.format(
                question=question,
                agent_trajectory=trajectory_str,
                answer=generated_answer,
                tool_schemas=self._format_tool_schemas(),
                conversation_history=conversation_history_str,
            )
        else:
            mode = "with" if has_reference else "without"
            raise ValueError(
                f"Item {item.id} has {'a' if has_reference else 'no'} trajectory_ground_truth "
                f"but custom_prompt_template_{mode}_reference is not configured. "
                f"Please add it to the trajectory evaluator config in config.yml."
            )

        # Build reasoning closure to capture local variables
        def build_reasoning(eval_result: dict) -> dict:
            return {
                "reasoning": eval_result["reasoning"],
                "query": question,
                "actual_tool_calls": structured_tool_calls,
                "expected_tool_calls": reference,
                "final_answer": generated_answer,
                "trajectory": [(action.model_dump(), output) for action, output in agent_trajectory],
                "conversation_history": conv_history,
                "track_agent_selected_tools_only": self.track_agent_selected_tools_only,
            }

        return await invoke_llm_with_retry(
            llm=self.llm,
            prompt_text=prompt_text,
            output_parser=self.output_parser,
            item_id=item.id,
            max_retries=self.max_retries,
            evaluator_name="Trajectory Evaluator",
            question_preview=question[:50] + "...",
            build_reasoning=build_reasoning,
            llm_judge_reasoning=self.llm_judge_reasoning,
        )
