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

from collections.abc import Callable
import json
import logging
from typing import TYPE_CHECKING
from typing import Any
from typing import TypeVar
from typing import cast

from vss_agents.utils.reasoning_parsing import parse_reasoning_content
from vss_agents.utils.reasoning_utils import get_llm_reasoning_bind_kwargs
from vss_agents.utils.reasoning_utils import get_thinking_tag

from .base import EvaluationMetric
from .base import register_metric

from langchain_core.messages import BaseMessage
from langchain_core.messages import HumanMessage
from langchain_core.messages import SystemMessage
from pydantic import BaseModel
from pydantic import Field
from pydantic import create_model

if TYPE_CHECKING:
    from langchain_core.language_models import BaseChatModel

logger = logging.getLogger(__name__)

T = TypeVar("T")


class FieldEvaluation(BaseModel):
    """Evaluation result for a field."""

    score: float = Field(ge=0.0, le=1.0, description="Match score between 0.0 and 1.0")
    reference_field: str | None = Field(None, description="Matched reference field name. If no match, set to None.")


@register_metric("llm_judge")
class LLMJudgeMetric(EvaluationMetric):
    """
    LLM judge for evaluating any values (strings, dicts, etc.).

    Can operate in two modes:
    1. Single comparison: Compare two values, return single score
    2. Field discovery: Score multiple fields with unspecified metrics, return structured output
    """

    def __init__(self, **kwargs: Any) -> None:
        """
        Initialize LLM judge metric.

        Expected kwargs:
            llm: BaseChatModel instance for evaluation (required)
            single_field_comparison_prompt: Prompt template for comparing one field value (required)
            multi_field_discovery_prompt: Prompt template for discovering and scoring multiple fields (optional, required only if using dynamic field discovery)
            max_retries: Maximum retry attempts after initial attempt (default: 2)
            llm_judge_reasoning: Whether to enable LLM reasoning mode (default: True)
        """
        llm = kwargs.get("llm")
        if llm is None:
            raise ValueError("LLM judge metric requires 'llm_name' in config")
        self.llm: BaseChatModel = llm

        self.single_field_comparison_prompt = kwargs.get("single_field_comparison_prompt")
        if self.single_field_comparison_prompt is None:
            raise ValueError("LLM judge metric requires 'single_field_comparison_prompt' in config")

        self.multi_field_discovery_prompt = kwargs.get("multi_field_discovery_prompt")

        self.max_retries = kwargs.get("max_retries", 2)
        self.llm_judge_reasoning = kwargs.get("llm_judge_reasoning", True)

        self.thinking_tag = get_thinking_tag(self.llm, self.llm_judge_reasoning)
        if self.thinking_tag:
            logger.info(f"LLM Judge: Applying thinking tag: '{self.thinking_tag}' for LLM Judge")

        llm_kwargs = get_llm_reasoning_bind_kwargs(self.llm, self.llm_judge_reasoning)
        if llm_kwargs:
            logger.info(f"LLM Judge: Binding LLM with reasoning kwargs: {llm_kwargs}")
            self.llm = cast("BaseChatModel", self.llm.bind(**llm_kwargs))

    async def _invoke_llm(
        self,
        prompt: str,
        parser: Callable[[str], T],
        context: str = "",
    ) -> T:
        """
        Invoke the LLM and parse the response.

        Args:
            prompt: The prompt to send to the LLM
            parser: Function to parse the LLM response text into desired type
            context: Context string for logging (e.g., field name, operation description)

        Returns:
            Parsed result of type T

        Raises:
            ValueError: If all retry attempts fail
        """
        last_error = None
        last_response = None

        # Build messages with optional thinking tag as system message
        messages: list[BaseMessage] = []
        if self.thinking_tag:
            messages.append(SystemMessage(content=self.thinking_tag))
        messages.append(HumanMessage(content=prompt))

        for attempt in range(self.max_retries + 1):
            try:
                if attempt > 0:
                    logger.info(
                        f"LLM Judge{f' ({context})' if context else ''}: Invoking LLM (retry {attempt}/{self.max_retries})"
                    )

                response = await self.llm.ainvoke(messages)
                last_response = str(response)
                logger.debug(f"LLM Judge{f' ({context})' if context else ''}: Response: {response}")

                _reasoning, actual_content = parse_reasoning_content(response)
                result = parser(actual_content or "")
                return result

            except Exception as e:
                last_error = e
                if attempt < self.max_retries:
                    logger.warning(
                        f"LLM Judge{f' ({context})' if context else ''} {'initial attempt' if attempt == 0 else f'retry {attempt}/{self.max_retries}'} failed: {e}. Retrying..."
                    )

        raise ValueError(
            f"LLM failed after {self.max_retries + 1} attempts (1 initial + {self.max_retries} retries){f' ({context})' if context else ''}. "
            f"Last error: {last_error}. Last response: {last_response}"
        )

    async def evaluate(self, actual: Any, reference: Any, field_name: str = "") -> float | None:
        """
        Evaluate by comparing two values using LLM.
        Args:
            actual: Actual generated value
            reference: Reference value
            field_name: Field name for context

        Returns:
            Score between 0.0 and 1.0, or None if LLM evaluation fails
        """
        # Convert to strings for comparison
        # If dict, pretty-print as JSON
        if isinstance(actual, dict):
            actual_str = json.dumps(actual, indent=2)
        else:
            actual_str = str(actual) if not isinstance(actual, str) else actual

        if isinstance(reference, dict):
            ref_str = json.dumps(reference, indent=2)
        else:
            ref_str = str(reference) if not isinstance(reference, str) else reference

        field_context = f"\n\nField being evaluated: {field_name}" if field_name else ""

        # Format the configured prompt
        if self.single_field_comparison_prompt is None:
            raise ValueError("single_field_comparison_prompt is not configured")
        judge_prompt = self.single_field_comparison_prompt.format(
            field_context=field_context, reference=ref_str, actual=actual_str
        )

        def parse_score(text: str) -> float:
            """Parse LLM response as a float score."""
            score = float(text.strip())
            logger.debug(f"LLM Judge score for '{field_name}': {score:.2f}")
            return score

        try:
            return await self._invoke_llm(
                prompt=judge_prompt,
                parser=parse_score,
                context=f"field '{field_name}'" if field_name else "evaluate",
            )
        except Exception:
            logger.exception(f"LLM evaluation failed for field '{field_name}'. Returning None")
            return None

    async def evaluate_with_field_discovery(
        self,
        reference_section: dict[str, Any],
        actual_section: dict[str, Any],
        unspecified_fields: list[str],
    ) -> dict[str, dict[str, Any] | None]:
        """
        Score multiple unspecified fields at once using structured outputs.

        Args:
            reference_section: Complete reference section
            actual_section: Complete actual section
            unspecified_fields: List of field names to score

        Returns:
            Dictionary mapping actual field names to evaluation results:
            {"actual_field_name": {"score": 0.93, "reference_field": "matched_ref_field"},
             "another_field": {"score": 0.0, "reference_field": null}}

        Raises:
            ValueError: If multi_field_discovery_prompt is not configured
        """
        if not unspecified_fields:
            return {}

        if self.multi_field_discovery_prompt is None:
            raise ValueError(
                "Cannot use evaluate_with_field_discovery: 'multi_field_discovery_prompt' is required "
                "when using dynamic field discovery (allow_dynamic_field_discovery=True). "
                "Please add 'multi_field_discovery_prompt' to your metric_configs for llm_judge."
            )

        reference_fields_json = json.dumps(reference_section, indent=2)

        # Extract unspecified fields from actual section
        actual_fields = {k: actual_section[k] for k in unspecified_fields if k in actual_section}
        actual_fields_json = json.dumps(actual_fields, indent=2)

        # Dynamically create Pydantic model for these unspecified fields
        fields_dict: dict[str, Any] = {
            field_name: (FieldEvaluation, Field(..., description=f"Evaluation for {field_name}"))
            for field_name in unspecified_fields
        }
        DynamicFieldScores = create_model("DynamicFieldScores", **fields_dict)  # noqa: N806

        structured_llm = self.llm.with_structured_output(DynamicFieldScores)

        # Format the configured prompt
        prompt = self.multi_field_discovery_prompt.format(
            reference_section=reference_fields_json, actual_fields=actual_fields_json
        )

        # Build messages with optional thinking tag as system message
        messages: list[BaseMessage] = []
        if self.thinking_tag:
            messages.append(SystemMessage(content=self.thinking_tag))
        messages.append(HumanMessage(content=prompt))

        try:
            logger.info(f"LLM Judge: Evaluating {len(unspecified_fields)} fields with structured output")
            result = await structured_llm.ainvoke(messages)
            logger.info(f"LLM Judge: Result: {result}")

            # Convert Pydantic model to dict
            result_dict: dict[str, Any] = {}
            for field_name in unspecified_fields:
                try:
                    field_eval = getattr(result, field_name)
                    result_dict[field_name] = {
                        "score": field_eval.score,
                        "reference_field": field_eval.reference_field,
                    }
                except AttributeError:
                    logger.warning(f"Missing field '{field_name}' in structured output")
                    result_dict[field_name] = None

            scored_count = sum(1 for v in result_dict.values() if v is not None)
            logger.info(f"LLM Judge: Successfully scored {scored_count}/{len(unspecified_fields)} fields")
            return result_dict

        except Exception as e:
            logger.exception(
                f"LLM field discovery failed: {e}. Returning None for all {len(unspecified_fields)} fields"
            )
            return dict.fromkeys(unspecified_fields)
