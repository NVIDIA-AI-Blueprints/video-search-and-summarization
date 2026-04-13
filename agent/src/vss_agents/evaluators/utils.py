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

"""Shared utilities for evaluators."""

from collections.abc import Callable
import logging
import re
from typing import Any
from typing import cast

from langchain_core.exceptions import OutputParserException
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import BaseMessage
from langchain_core.messages import HumanMessage
from langchain_core.messages import SystemMessage
from nat.eval.evaluator.evaluator_model import EvalInputItem
from nat.eval.evaluator.evaluator_model import EvalOutputItem

from vss_agents.utils.reasoning_parsing import parse_reasoning_content
from vss_agents.utils.reasoning_utils import get_llm_reasoning_bind_kwargs
from vss_agents.utils.reasoning_utils import get_thinking_tag

logger = logging.getLogger(__name__)


def compute_item_latency(item: EvalInputItem) -> float | None:
    """
    Compute the wall-clock latency for an evaluation item from its trajectory timestamps.

    Returns the time in seconds between the first and last event, or None if no trajectory.
    """
    if not item.trajectory:
        return None
    try:
        min_ts = min(step.event_timestamp for step in item.trajectory)
        max_ts = max(step.event_timestamp for step in item.trajectory)
        return float(round(max_ts - min_ts, 3))
    except Exception:
        return None


def should_evaluate(item: EvalInputItem, evaluator_type: str) -> bool:
    """
    Check if an item should be evaluated by the specified evaluator type.

    Args:
        item: The evaluation input item
        evaluator_type: The evaluation method ID

    Returns:
        bool: True if the item should be evaluated, False otherwise

    Raises:
        ValueError: If evaluation_method field is missing from the dataset entry
    """
    if not hasattr(item, "full_dataset_entry") or item.full_dataset_entry is None:
        raise ValueError(f"Item {item.id} missing full_dataset_entry - cannot determine evaluation_method")

    eval_methods = item.full_dataset_entry.get("evaluation_method", None)
    if eval_methods is None:
        raise ValueError(
            f"Item {item.id} missing required 'evaluation_method' field. "
            f'Must be a list like ["qa"], ["trajectory"], ["report"], or ["qa", "trajectory", "report"]'
        )

    if not isinstance(eval_methods, list):
        raise ValueError(
            f"Item {item.id} has invalid 'evaluation_method' field: {eval_methods}. "
            f'Must be a list like ["qa"], ["trajectory"], ["report"], or ["qa", "trajectory", "report"]'
        )

    return evaluator_type in eval_methods


class ScoreOutputParser:
    """
    Output parser that extracts a score (0.0-1.0) and reasoning from LLM responses.

    Handles reasoning content in various formats including thinking tags.
    """

    def parse(self, response: Any) -> dict:
        """
        Parse the LLM output to extract score and reasoning.

        Args:
            response: The LLM response (can be string or AIMessage)

        Returns:
            dict: Contains 'score' (float) and 'reasoning' (str)

        Raises:
            OutputParserException: If score cannot be extracted or is invalid
        """
        thinking_content, actual_content = parse_reasoning_content(response)
        reasoning = thinking_content if thinking_content else ""

        # Extract score from actual_content
        if not actual_content:
            raise OutputParserException(f"No actual content found. Expected score. Full text: {str(response)[:300]}")

        # Extract the number from actual_content
        score_match = re.search(r"([0-9]+\.?[0-9]*)", actual_content.strip())

        if not score_match:
            raise OutputParserException(
                f"Could not extract score from output. Expected a number between 0.0 and 1.0. "
                f"Got: {actual_content[:200]}"
            )

        try:
            score = float(score_match.group(1))
            # Ensure score is between 0.0 and 1.0
            if not (0.0 <= score <= 1.0):
                raise OutputParserException(f"Score must be between 0.0 and 1.0, got: {score}")
        except ValueError as e:
            raise OutputParserException(f"Could not convert score to float: {score_match.group(1)}") from e

        return {"score": score, "reasoning": reasoning}


def strip_agent_think_tags(text: str) -> str:
    """
    Remove <agent-think>...</agent-think> blocks from text.

    Args:
        text: The text to clean

    Returns:
        str: Text with agent-think blocks removed
    """
    if not text:
        return ""
    # Remove all <agent-think>...</agent-think> blocks
    cleaned_text = re.sub(r"<agent-think>.*?</agent-think>", "", text, flags=re.DOTALL)
    # Remove any extra whitespace left behind
    return cleaned_text.strip()


async def invoke_llm_with_retry(
    llm: BaseChatModel,
    prompt_text: str,
    output_parser: ScoreOutputParser,
    item_id: str,
    max_retries: int,
    evaluator_name: str,
    question_preview: str,
    build_reasoning: Callable[[dict], dict],
    llm_judge_reasoning: bool = True,
) -> EvalOutputItem:
    """
    Invoke LLM with retry logic and parse the response.

    Args:
        llm: The LLM to invoke
        prompt_text: The formatted prompt to send
        output_parser: Parser to extract score from response
        item_id: ID for the evaluation item
        max_retries: Maximum number of retry attempts after initial attempt
        evaluator_name: Name of the evaluator (for logging)
        question_preview: Preview of the question (for logging)
        build_reasoning: Callback to build reasoning dict from eval_result
        llm_judge_reasoning: Whether to enable LLM judge reasoning mode

    Returns:
        EvalOutputItem with score and reasoning
    """
    last_error = None
    last_response = None

    # Get the thinking tag based on the LLM model
    thinking_tag = get_thinking_tag(llm, llm_judge_reasoning)
    if thinking_tag:
        logger.info(f"Applying thinking tag: '{thinking_tag}' for LLM Judge")

    # Bind LLM with reasoning kwargs if applicable
    llm_kwargs = get_llm_reasoning_bind_kwargs(llm, llm_judge_reasoning)
    if llm_kwargs:
        logger.info(f"Binding LLM with reasoning kwargs: {llm_kwargs}")
        llm = cast("BaseChatModel", llm.bind(**llm_kwargs))

    # Build messages with optional thinking tag as system message
    messages: list[BaseMessage] = []
    if thinking_tag:
        messages.append(SystemMessage(content=thinking_tag))
    messages.append(HumanMessage(content=prompt_text))

    for attempt in range(max_retries + 1):
        try:
            if attempt > 0:
                logger.info(
                    f"{evaluator_name}: Retrying evaluation for question '{question_preview}' "
                    f"(retry {attempt}/{max_retries})"
                )

            # Invoke the LLM with messages
            response = await llm.ainvoke(messages)
            last_response = str(response)
            logger.debug(f"{evaluator_name}: Response: {response}")

            # Parse the response
            eval_result = output_parser.parse(response)

            reasoning = build_reasoning(eval_result)
            return EvalOutputItem(id=item_id, score=eval_result["score"], reasoning=reasoning)

        except Exception as e:
            last_error = e
            last_response = str(e)

            if attempt < max_retries:
                logger.warning(
                    f"{evaluator_name}: "
                    f"{'Initial attempt' if attempt == 0 else f'Retry {attempt}/{max_retries}'} "
                    f"failed for question '{question_preview}': {e}. Retrying..."
                )
            else:
                logger.exception(
                    f"{evaluator_name}: All retry attempts exhausted for question '{question_preview}'. Error: {e}"
                )

    return EvalOutputItem(
        id=item_id,
        score=0.0,
        reasoning=f"Error evaluating after {max_retries + 1} attempts. "
        f"Last error: {last_error}. Last response: {last_response}",
    )
