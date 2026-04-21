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
import re
from typing import Any

from langchain_core.messages import HumanMessage
from langchain_core.messages import SystemMessage
from nat.builder.builder import Builder
from nat.builder.framework_enum import LLMFrameworkEnum
from nat.builder.function_info import FunctionInfo
from nat.cli.register_workflow import register_function
from nat.data_models.component_ref import LLMRef
from nat.data_models.function import FunctionBaseConfig
from pydantic import BaseModel
from pydantic import Field

logger = logging.getLogger(__name__)

EVALUATION_COMPRESSOR_PROMPT = """
You are an expert at summarizing and compressing text for evaluation purposes. Your compressed text will be sent to and evaluator which will judge the quality of the agent's response.
Your task is to compress the provided text while preserving all key information and important details for quality evaluation.
Do not omit any critical facts or context. Return the compressed text in markdown paragraph formatting.

RULES:
- Compress each video caption block that is returned from a video caption tool call into one short paragraph, concisely and accurately describing the captions.
- Summarize agent's and supervisor agent's thoughts, actions, decisions and tool calls concisely.
- For the agent's final conclusion and final answer to the user's query, do not compress and keep the original text.
- Maintain the original order of the text.
"""


class EvaluationCompressorConfig(FunctionBaseConfig, name="evaluation_compressor"):
    """Configuration for the Evaluation Compressor tool."""

    llm_name: LLMRef = Field(..., description="The LLM to use to compress the agent output.")
    token_limit: int = Field(..., description="The token limit for the agent output.")
    remove_caption_details: bool = Field(
        default=True, description="Whether to remove caption details from the agent output."
    )


class EvaluationCompressorInput(BaseModel):
    input_text: str = Field(..., description="The input text to compress.")


def remove_caption_details(text: str) -> str:
    """
    Removes paragraphs from the text that start with a timestamp in the format [float_number] followed by text.

    Args:
        text (str): The input text.

    Returns:
        str: The text with caption details removed.
    """
    # Pattern: [float] at the start of a line, possibly with leading spaces, followed by any text
    pattern = re.compile(r"^\s*\[\d+\.\d+\].*$", re.MULTILINE)
    cleaned_text = re.sub(pattern, "", text)
    # remove any resulting multiple blank lines
    cleaned_text = re.sub(r"\n{2,}", "\n\n", cleaned_text).strip()
    return cleaned_text


def count_sections_by_token_limit(input_text: str, token_limit: int, llm_model: str) -> int:
    """
    Returns the number of sections needed to split input_text such that each section
    contains at most token_limit tokens. Uses tiktoken to count tokens.
    """
    import tiktoken

    try:
        enc = tiktoken.encoding_for_model(llm_model)
    except KeyError:
        logger.warning(f"Model {llm_model} not found in tiktoken. Using gpt-4o as fallback.")
        enc = tiktoken.encoding_for_model("gpt-4o")

    token_count = len(enc.encode(input_text))

    num_sections = (token_count + token_limit - 1) // token_limit
    return num_sections


def split_text_by_sections(input_text: str, num_sections: int) -> list:
    """
    Splits input_text into num_sections, trying to make each section as equal in size as possible,
    but only splitting at paragraph boundaries (i.e., after a double newline or single newline if no double found).
    Returns a list of section strings.
    """

    # Split into paragraphs (preserve newlines)
    # We'll treat paragraphs as blocks separated by at least one blank line
    import re

    paragraphs = re.split(r"\n\s*\n", input_text.strip())
    total_paragraphs = len(paragraphs)
    print(f"!!! TOTAL PARAGRAPHS: {total_paragraphs}")
    if num_sections <= 0:
        raise ValueError("num_sections must be a positive integer")
    if num_sections > total_paragraphs:
        # If more sections than paragraphs, just return each paragraph as a section, pad with empty strings
        return [p.strip() for p in paragraphs] + [""] * (num_sections - total_paragraphs)

    # Calculate how many paragraphs per section (as evenly as possible)
    base = total_paragraphs // num_sections
    remainder = total_paragraphs % num_sections

    sections = []
    idx = 0
    for i in range(num_sections):
        # Distribute the remainder: first 'remainder' sections get one extra paragraph
        count = base + (1 if i < remainder else 0)
        section_paragraphs = paragraphs[idx : idx + count]
        section_text = "\n\n".join(section_paragraphs).strip()
        if len(section_text) > 0:
            sections.append(section_text)
        idx += count

    return sections


@register_function(config_type=EvaluationCompressorConfig)
async def evaluation_compressor(config: EvaluationCompressorConfig, builder: Builder) -> AsyncGenerator[FunctionInfo]:
    """
    This tool is used to compress the agent output if it exceeds the token limit.
    """

    llm = await builder.get_llm(config.llm_name, wrapper_type=LLMFrameworkEnum.LANGCHAIN)

    async def _evaluation_compressor(evaluation_compressor_input: EvaluationCompressorInput) -> str:
        # Get rid of caption details if bool set to True
        intial_text = (
            remove_caption_details(evaluation_compressor_input.input_text)
            if config.remove_caption_details
            else evaluation_compressor_input.input_text
        )
        num_sections = count_sections_by_token_limit(intial_text, config.token_limit, llm.model_name)

        # Check if the initial text is within the token limit
        if num_sections <= 1:
            return intial_text

        # If token count is still too high then run compression in parallel
        section_list = split_text_by_sections(intial_text, num_sections)

        # Call LLM in parallel on each section
        import asyncio

        async def compress_section(section: str) -> Any:
            messages = [
                SystemMessage(content=EVALUATION_COMPRESSOR_PROMPT),
                HumanMessage(content=f"The text to compress is:\n\n{section}"),
            ]
            compressed_section = await llm.ainvoke(messages)

            return compressed_section.content

        compressed_sections = await asyncio.gather(*[compress_section(section) for section in section_list])

        # Combine all LLM results
        compressed_text = "\n\n".join(compressed_sections)

        # Check if the compressed text is within the token limit
        final_num_sections = count_sections_by_token_limit(compressed_text, config.token_limit, llm.model_name)
        if final_num_sections > 1:
            # If still too long, compress again by calling compress_section on the full compressed_text
            compressed_text = await compress_section(compressed_text)

        # Return shortened text
        return compressed_text

    yield FunctionInfo.create(
        single_fn=_evaluation_compressor,
        description=_evaluation_compressor.__doc__,
        input_schema=EvaluationCompressorInput,
        single_output_schema=str,
    )
