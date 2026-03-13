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


from collections.abc import AsyncGenerator

from nat.builder.builder import EvalBuilder
from nat.builder.evaluator import EvaluatorInfo
from nat.cli.register_workflow import register_evaluator
from nat.data_models.evaluator import EvaluatorBaseConfig
from pydantic import Field


class CustomizedQAEvaluatorConfig(EvaluatorBaseConfig, name="customized_qa_evaluator"):
    """Customized QA Evaluator for QA evaluation.

    This evaluator uses an LLM judge to compare agent answers against ground truth
    """

    llm_name: str = Field(description="LLM to use as a judge for QA evaluation.")
    evaluation_method_id: str = Field(
        default="qa",
        description="The evaluation method ID that this evaluator corresponds to. "
        "Items in the dataset must have this ID in their 'evaluation_method' field to be evaluated.",
    )
    custom_prompt_template: str | None = Field(
        default=None,
        description="Optional custom prompt template for the LLM judge. "
        "Must include variables: question, answer, reference. "
        "If not provided, uses the default QA evaluation prompt.",
    )
    max_retries: int = Field(
        default=2,
        description="Maximum number of retry attempts for LLM evaluation after the initial attempt.",
    )
    llm_judge_reasoning: bool = Field(
        default=True,
        description="Enable LLM judge reasoning mode for evaluation.",
    )


@register_evaluator(config_type=CustomizedQAEvaluatorConfig)
async def register_customized_qa_evaluator(
    config: CustomizedQAEvaluatorConfig, builder: EvalBuilder
) -> AsyncGenerator[EvaluatorInfo]:
    """Register the customized QA evaluator."""
    from .evaluate import CustomizedQAEvaluator

    from langchain_core.prompts import PromptTemplate
    from nat.builder.framework_enum import LLMFrameworkEnum

    llm = await builder.get_llm(config.llm_name, wrapper_type=LLMFrameworkEnum.LANGCHAIN)

    custom_prompt = None
    if config.custom_prompt_template:
        custom_prompt = PromptTemplate(
            input_variables=["question", "answer", "reference"],
            template=config.custom_prompt_template,
        )

    _evaluator = CustomizedQAEvaluator(
        llm=llm,
        max_concurrency=builder.get_max_concurrency(),
        custom_prompt=custom_prompt,
        max_retries=config.max_retries,
        evaluation_method_id=config.evaluation_method_id,
        llm_judge_reasoning=config.llm_judge_reasoning,
    )

    yield EvaluatorInfo(config=config, evaluate_fn=_evaluator.evaluate, description="Customized QA Evaluator")
