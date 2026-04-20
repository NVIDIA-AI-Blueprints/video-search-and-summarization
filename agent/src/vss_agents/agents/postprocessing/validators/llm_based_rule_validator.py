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

"""LLM-based validator for soft rule checking."""

import logging
from typing import Any

from vss_agents.agents.postprocessing.data_models import ValidatorResult
from vss_agents.utils.reasoning_utils import get_llm_reasoning_bind_kwargs
from vss_agents.utils.reasoning_utils import get_thinking_tag

from .base import BaseValidator

from langchain_core.exceptions import LangChainException
from langchain_core.exceptions import OutputParserException
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import BaseMessage
from langchain_core.messages import HumanMessage
from langchain_core.messages import SystemMessage
from pydantic import BaseModel
from pydantic import Field

logger = logging.getLogger(__name__)


class LLMBasedRuleValidatorOutput(BaseModel):
    """Structured output from LLM-based rule validation."""

    passed: bool = Field(description="True if the response is acceptable, False if it needs improvement")
    feedback: str = Field(default="", description="Actionable feedback for the agent to improve")


DEFAULT_PROMPT_TEMPLATE = """You are a validator. Check if the agent's response is acceptable.

User's Question: {user_query}

Agent's Trajectory:
{trajectory}

Agent's Final Response: {output}

Decide if the response is acceptable (passed=True) or needs improvement (passed=False).
"""


class LLMBasedRuleValidator(BaseValidator):
    """LLM-based rule validator for validating configurable rules."""

    name = "llm_based_rule_validator"

    def __init__(
        self,
        llm: BaseChatModel,
        prompt_template: str = "",
        feedback_template: str = "",
        max_retries: int = 0,
        **kwargs: Any,  # noqa: ARG002
    ) -> None:
        """Initialize the LLM-based rule validator.

        Args:
            llm: The LLM to use for validation.
            prompt_template: Custom prompt template. Use {output}, {user_query}, {trajectory} placeholders.
            feedback_template: Template for feedback message. Use {issues} placeholder.
            max_retries: Number of retries on LLM parsing/invocation errors.
        """
        super().__init__(
            feedback_template=feedback_template,
        )
        self.llm = llm
        self.prompt_template = prompt_template or DEFAULT_PROMPT_TEMPLATE
        if max_retries < 0:
            raise ValueError("max_retries must be >= 0")
        self.max_retries = max_retries

    async def validate(self, output: str, **kwargs: Any) -> ValidatorResult:
        """Validate output using LLM with structured output.

        Args:
            output: The agent's final response.
            **kwargs: Context including user_query, trajectory, llm_reasoning.
        """
        user_query = kwargs.get("user_query", "")
        trajectory = kwargs.get("trajectory", "")
        llm_reasoning = kwargs.get("llm_reasoning", False)

        try:
            prompt = self.prompt_template.format(
                output=output,
                user_query=user_query or "No user query available",
                trajectory=trajectory or "No trajectory available",
            )
        except KeyError as e:
            logger.warning(f"{self.name}: prompt template missing key: {e}; using DEFAULT_PROMPT_TEMPLATE")
            prompt = DEFAULT_PROMPT_TEMPLATE.format(
                output=output,
                user_query=user_query or "No user query available",
                trajectory=trajectory or "No trajectory available",
            )

        # Configure LLM with reasoning mode if enabled
        llm = self.llm
        thinking_tag = get_thinking_tag(llm, llm_reasoning)
        if thinking_tag:
            logger.debug(f"{self.name}: using thinking tag: {thinking_tag}")

        llm_kwargs = get_llm_reasoning_bind_kwargs(llm, llm_reasoning)
        if llm_kwargs:
            logger.debug(f"{self.name}: binding with reasoning kwargs: {llm_kwargs}")
            llm = llm.bind(**llm_kwargs)  # type: ignore[assignment]

        # Build messages
        messages: list[BaseMessage] = []
        if thinking_tag:
            messages.append(SystemMessage(content=thinking_tag))
        messages.append(HumanMessage(content=prompt))

        structured_llm = llm.with_structured_output(LLMBasedRuleValidatorOutput)

        last_exception: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                raw_result = await structured_llm.ainvoke(messages)
                result = LLMBasedRuleValidatorOutput.model_validate(raw_result)
                logger.info(f"{self.name}: passed={result.passed}, feedback={result.feedback}")
                issues = [result.feedback] if not result.passed and result.feedback else []
                return ValidatorResult(name=self.name, passed=result.passed, issues=issues)
            except (OutputParserException, LangChainException) as e:
                last_exception = e
                if attempt < self.max_retries:
                    logger.warning(f"{self.name} attempt {attempt + 1} failed: {e}, retrying...")
                else:
                    logger.warning(f"{self.name} failed after {self.max_retries + 1} attempts: {e}")
            except Exception as e:
                logger.exception(f"{self.name} unexpected error while validating: {e}")
                last_exception = e
                # Exit retry loop and fall through to the existing fail-open return
                break

        # Propagate the last exception so the node can apply the central fail-open policy
        raise last_exception  # type: ignore[misc]
