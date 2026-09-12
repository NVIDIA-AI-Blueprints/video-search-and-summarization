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
from vss_agents.utils.reasoning_parsing import parse_reasoning_content
from vss_agents.utils.reasoning_utils import get_llm_reasoning_bind_kwargs

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

    def _content_of(result: object, step: str, fallback: str) -> str:
        """Return the answer, dropping any reasoning the model emitted alongside it.

        Falls back rather than raising. The top agent converts a tool exception into
        an error ToolMessage, `plan_update` then preserves the plan with the step
        still pending, and the exec node calls this tool again -- so raising only
        buys a clearer log line on the way to exhausting max_iterations. A weaker
        prompt that still carries the user's intent starts the alert; a raise does
        not.
        """
        # No raw-content fallback: parse_reasoning_content already returns plain
        # content as the second element, so None means the model produced reasoning
        # and no answer. Substituting result.content there would hand back the
        # unparsed "<think>...</think>" blob as the detection prompt.
        _reasoning, content = parse_reasoning_content(result)
        content = (content or "").strip()
        if not content:
            # Empty here means the model spent its whole max_tokens budget on
            # reasoning and was cut off (finish_reason=length).
            logger.warning(
                "prompt_gen produced no content during %s; using the fallback prompt. The LLM "
                "likely exhausted max_tokens on reasoning -- raise max_tokens for '%s' or "
                "disable its thinking mode.",
                step,
                config.llm_name,
            )
            return fallback
        return content

    async def _prompt_gen(prompt_gen_input: PromptGenInput) -> str:
        llm = await builder.get_llm(config.llm_name, wrapper_type=LLMFrameworkEnum.LANGCHAIN)
        # This helper writes one short Yes/No detection question, so reasoning buys
        # nothing and on a small max_tokens budget consumes the entire answer. Bind
        # the caller's intent explicitly rather than inheriting the server default,
        # which `--default-chat-template-kwargs {"enable_thinking":true}` now sets on
        # every hardware profile.
        reasoning_kwargs = get_llm_reasoning_bind_kwargs(llm, prompt_gen_input.detailed_thinking)
        if reasoning_kwargs:
            llm = llm.bind(**reasoning_kwargs)
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
        # Mirrors rtvi_vlm_alert's own default: keep the user's words so the alert
        # still detects what they asked for, just without the LLM's refinement.
        fallback = f"Detect for {prompt_gen_input.user_query}. Answer in Yes or No."
        result = _content_of(result, "prompt generation", fallback)
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
            merged = await merge_quesion_chain.ainvoke(
                {
                    "previous_prompt": prompt_gen_input.previous_prompt,
                    "new_prompt": result,
                }
            )
            # An empty merge falls back to the unmerged new prompt, which is
            # strictly better than the generic fallback.
            result = _content_of(merged, "prompt merge", result)
        return str(result)

    yield FunctionInfo.create(
        single_fn=_prompt_gen,
        description=_prompt_gen.__doc__,
        input_schema=PromptGenInput,
        single_output_schema=str,
    )
