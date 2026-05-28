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

# Adapted from https://github.com/NVIDIA/NeMo-Agent-Toolkit/blob/develop/src/nat/eval/trajectory_evaluator/register.py;
# https://github.com/langchain-ai/langchain/tree/master/libs/langchain/langchain_classic/evaluation/agents

from collections.abc import AsyncGenerator

from nat.builder.builder import EvalBuilder
from nat.builder.evaluator import EvaluatorInfo
from nat.cli.register_workflow import register_evaluator
from nat.data_models.evaluator import EvaluatorBaseConfig
from pydantic import Field


class CustomizedTrajectoryEvaluatorConfig(EvaluatorBaseConfig, name="customized_trajectory_evaluator"):
    """Customized Agent Trajectory Evaluation."""

    llm_name: str = Field(description="LLM as a judge.")
    evaluation_method_id: str = Field(
        default="trajectory",
        description="The evaluation method ID that this evaluator corresponds to. "
        "Items in the dataset must have this ID in their 'evaluation_method' field to be evaluated.",
    )
    track_agent_selected_tools_only: bool = Field(
        default=True,
        description="If True, only track tools directly selected by the agent, "
        "excluding tools called internally by other tools.",
    )
    custom_prompt_template_with_reference: str | None = Field(
        default=None,
        description="Prompt template used when the dataset item has trajectory_ground_truth. "
        "Must include variables: question, agent_trajectory, answer, reference. "
        "If not provided, items with references will be skipped.",
    )
    custom_prompt_template_without_reference: str | None = Field(
        default=None,
        description="Prompt template used when the dataset item has no trajectory_ground_truth. "
        "Must include variables: question, agent_trajectory, answer, tool_schemas, conversation_history. "
        "If not provided, items without references will be skipped.",
    )
    max_retries: int = Field(
        default=2, description="Maximum number of retry attempts for LLM evaluation after the initial attempt. "
    )
    llm_judge_reasoning: bool = Field(
        default=True,
        description="Enable LLM judge reasoning mode for evaluation.",
    )


@register_evaluator(config_type=CustomizedTrajectoryEvaluatorConfig)
async def register_customized_trajectory_evaluator(
    config: CustomizedTrajectoryEvaluatorConfig, builder: EvalBuilder
) -> AsyncGenerator[EvaluatorInfo]:
    from langchain_core.prompts import PromptTemplate
    from nat.builder.framework_enum import LLMFrameworkEnum

    from .evaluate import CustomizedTrajectoryEvaluator

    llm = await builder.get_llm(config.llm_name, wrapper_type=LLMFrameworkEnum.LANGCHAIN)
    tools = await builder.get_all_tools(wrapper_type=LLMFrameworkEnum.LANGCHAIN)

    prompt_with_reference = None
    if config.custom_prompt_template_with_reference:
        prompt_with_reference = PromptTemplate(
            input_variables=["question", "agent_trajectory", "answer", "reference"],
            template=config.custom_prompt_template_with_reference,
        )

    prompt_without_reference = None
    if config.custom_prompt_template_without_reference:
        prompt_without_reference = PromptTemplate(
            input_variables=["question", "agent_trajectory", "answer", "tool_schemas", "conversation_history"],
            template=config.custom_prompt_template_without_reference,
        )

    _evaluator = CustomizedTrajectoryEvaluator(
        llm=llm,
        tools=tools,
        max_concurrency=builder.get_max_concurrency(),
        track_agent_selected_tools_only=config.track_agent_selected_tools_only,
        prompt_with_reference=prompt_with_reference,
        prompt_without_reference=prompt_without_reference,
        max_retries=config.max_retries,
        evaluation_method_id=config.evaluation_method_id,
        llm_judge_reasoning=config.llm_judge_reasoning,
    )

    yield EvaluatorInfo(config=config, evaluate_fn=_evaluator.evaluate, description="CustomizedTrajectory Evaluator")
