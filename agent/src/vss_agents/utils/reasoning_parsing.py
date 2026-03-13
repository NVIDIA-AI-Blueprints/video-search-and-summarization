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

from typing import Any


def parse_content_blocks(response: Any) -> tuple[str | None, str | None]:
    """Extract reasoning and text from content_blocks on a response.

    Args:
        response: LLM response object that may have a content_blocks attribute.

    Returns:
        tuple: (reasoning, text) where either can be None if empty/not found.
    """
    blocks = getattr(response, "content_blocks", None)
    if not blocks or not isinstance(blocks, list):
        return None, None

    reasoning_parts = []
    text_parts = []
    for block in blocks:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "reasoning":
            reasoning_parts.append(block.get("reasoning", ""))
        elif block.get("type") == "text":
            text_parts.append(block.get("text", ""))

    reasoning = "\n".join(reasoning_parts).strip() or None
    text = "\n".join(text_parts).strip() or None
    return reasoning, text


def parse_reasoning_content(response: Any) -> tuple[str | None, str | None]:
    """
    Generic parser that extracts reasoning and content from LLM response objects.

    This function handles multiple formats by trying to find the reasoning content in the following order:
    1. Content with single </think> tag delimiter
    2. Content with <think></think> paired tags
    3. Response objects with separate reasoning_content field
    4. content_blocks with "reasoning" typed blocks
    5. Plain content without reasoning

    Args:
        response: LLM response object

    Returns:
        tuple: (reasoning_content, actual_content) where either can be None if empty/not found
    """
    content = getattr(response, "content", "")

    # If content is not a string, skip think-tag parsing
    if not isinstance(content, str):
        content = ""

    # Check for single </think> tag (format where everything before </think> is reasoning)
    if "</think>" in content and "<think>" not in content:
        think_end_idx = content.find("</think>")
        reasoning_part = content[:think_end_idx]
        actual_content = content[think_end_idx + len("</think>") :]

        reasoning = reasoning_part.strip("\n").strip()
        actual = actual_content.strip("\n").strip()

        return reasoning or None, actual or None

    # Check for paired <think></think> tags
    if "<think>" in content and "</think>" in content:
        think_start_idx = content.find("<think>")
        think_end_idx = content.find("</think>")

        # Make sure both tags are in the right order
        if think_start_idx != -1 and think_end_idx != -1 and think_start_idx < think_end_idx:
            reasoning_part = content[think_start_idx + len("<think>") : think_end_idx]
            actual_content = content[think_end_idx + len("</think>") :]

            reasoning = reasoning_part.strip("\n").strip()
            actual = actual_content.strip("\n").strip()

            return reasoning or None, actual or None

    # No think tags in content, fall back to reasoning_content field
    # Check for reasoning_content in multiple locations
    reasoning_field = getattr(response, "reasoning_content", None)

    if not reasoning_field and hasattr(response, "additional_kwargs"):
        additional_kwargs = getattr(response, "additional_kwargs", {})
        if isinstance(additional_kwargs, dict):
            reasoning_field = additional_kwargs.get("reasoning_content")

    if not reasoning_field and hasattr(response, "response_metadata"):
        response_metadata = getattr(response, "response_metadata", {})
        if isinstance(response_metadata, dict):
            reasoning_field = response_metadata.get("reasoning_content")

    if reasoning_field and isinstance(reasoning_field, str):
        return reasoning_field.strip() or None, content.strip() if content else None

    # Check for content_blocks
    block_reasoning, block_text = parse_content_blocks(response)
    if block_reasoning is not None or block_text is not None:
        return block_reasoning, block_text

    # No reasoning found, return plain content
    return None, content or None
