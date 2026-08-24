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
import logging

from langchain_core.prompts import ChatPromptTemplate
from nat.builder.builder import Builder
from nat.builder.framework_enum import LLMFrameworkEnum
from nat.builder.function_info import FunctionInfo
from nat.cli.register_workflow import register_function
from nat.data_models.function import FunctionBaseConfig
from pydantic import BaseModel
from pydantic import Field

from vss_agents.prompt import VSS_SUMMARIZE_PROMPT

logger = logging.getLogger(__name__)


class PromptGenConfig(FunctionBaseConfig, name="prompt_gen"):
    """Configuration for the Prompt Gen tool."""

    llm_name: str = Field(..., description="The name of the LLM to use")
    prompt: str = Field(default=VSS_SUMMARIZE_PROMPT, description="The prompt to generate the summarize prompt")


class PromptGenInput(BaseModel):
    """Input for the Prompt Gen tool."""

    user_query: str = Field(..., description="The user's query")
    user_intent: str = Field(..., description="The user's intent")
    detailed_thinking: bool = Field(default=False, description="Whether to include detailed thinking in the prompt")
    previous_prompt: str = Field(default="", description="The previous prompt to use to generate the new prompt")


@register_function(config_type=PromptGenConfig, framework_wrappers=[LLMFrameworkEnum.LANGCHAIN])
async def prompt_gen(config: PromptGenConfig, builder: Builder) -> AsyncGenerator[FunctionInfo]:
    """Generate a prompt for the user's query."""

    async def _prompt_gen(prompt_gen_input: PromptGenInput) -> str:
        llm = await builder.get_llm(config.llm_name, wrapper_type=LLMFrameworkEnum.LANGCHAIN)
        messages = []
        if prompt_gen_input.detailed_thinking:
            messages.append(("system", "detailed thinking on"))
        messages.append(("system", config.prompt))
        messages.append(("user", "Please generate the prompts now."))
        qa_chain_prompt = ChatPromptTemplate.from_messages(messages=messages)
        qa_chain = qa_chain_prompt | llm
        result = await qa_chain.ainvoke(
            {"user_query": prompt_gen_input.user_query, "user_intent": prompt_gen_input.user_intent}
        )
        result = result.content
        if prompt_gen_input.previous_prompt:
            merge_quesion_prompt = ChatPromptTemplate.from_messages(
                [
                    (
                        "system",
                        "merge the following prompts into one prompt, remove duplicates, make the prompt concise, clear and cover all instructions. ONLY return the merged prompt, do not include any other text.",
                    ),
                    ("user", "previous prompt: {previous_prompt}"),
                    ("user", "new prompt: {new_prompt}"),
                ]
            )
            merge_quesion_chain = merge_quesion_prompt | llm
            result = await merge_quesion_chain.ainvoke(
                {
                    "previous_prompt": prompt_gen_input.previous_prompt,
                    "new_prompt": result,
                }
            )
            result = result.content
        return str(result)

    yield FunctionInfo.create(
        single_fn=_prompt_gen,
        description=_prompt_gen.__doc__,
        input_schema=PromptGenInput,
        single_output_schema=str,
    )
