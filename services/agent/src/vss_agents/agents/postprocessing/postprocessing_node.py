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

"""Postprocessing node to run validators and provide feedback to agent."""

import asyncio
import logging
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import BaseMessage
from langchain_core.messages import HumanMessage

from vss_agents.agents.postprocessing.data_models import POSTPROCESSING_FEEDBACK_MARKER
from vss_agents.agents.postprocessing.data_models import PostprocessingConfig
from vss_agents.agents.postprocessing.data_models import PostprocessingResult
from vss_agents.agents.postprocessing.validators.base import BaseValidator
from vss_agents.agents.postprocessing.validators.llm_based_rule_validator import LLMBasedRuleValidator
from vss_agents.agents.postprocessing.validators.non_empty_response_validator import NonEmptyResponseValidator
from vss_agents.agents.postprocessing.validators.url_validator import URLValidator

logger = logging.getLogger(__name__)

# Registry: field_name -> validator_class
_VALIDATOR_REGISTRY = {
    "url_validator": URLValidator,
    "non_empty_response_validator": NonEmptyResponseValidator,
    "llm_based_rule_validator": LLMBasedRuleValidator,
}


def _format_message(msg: BaseMessage) -> str:
    """Format a single message for trajectory display."""
    import json

    from langchain_core.messages import AIMessage

    msg_type = type(msg).__name__

    # For AIMessage, show tool calls if present
    if isinstance(msg, AIMessage) and msg.tool_calls:
        return f"[{msg_type}]: {json.dumps(msg.tool_calls, default=str)}"

    content = str(msg.content) if msg.content else ""

    # Skip placeholder content
    if content == "Agent wants to call tools.":
        return ""

    return f"[{msg_type}]: {content}"


def extract_current_trajectory(scratchpad: list[BaseMessage]) -> str:
    """Extract and format the current trajectory from scratchpad.

    Only includes messages after the last feedback message (current attempt).

    Args:
        scratchpad: The agent's scratchpad.

    Returns:
        Formatted trajectory string.
    """
    if not scratchpad:
        return ""

    # Find the last feedback message
    last_feedback_idx = -1
    for i, msg in enumerate(scratchpad):
        if isinstance(msg, HumanMessage) and POSTPROCESSING_FEEDBACK_MARKER in str(msg.content):
            last_feedback_idx = i

    # Only include messages after the last feedback
    start_idx = last_feedback_idx + 1 if last_feedback_idx >= 0 else 0
    current_messages = scratchpad[start_idx:]

    if not current_messages:
        return ""

    # Format trajectory
    lines = [_format_message(msg) for msg in current_messages]
    lines = [line for line in lines if line]  # Remove empty lines

    return "\n".join(lines)


class PostprocessingNode:
    """Run validators in groups and provide feedback on failure."""

    def __init__(
        self,
        config: PostprocessingConfig | None = None,
        llm: BaseChatModel | None = None,
    ):
        self.config = config or PostprocessingConfig()
        self.llm = llm
        self.validators_by_name: dict[str, BaseValidator] = {}
        self.validation_order: list[list[str]] = []
        self._create_validators()
        logger.info(f"PostprocessingNode initialized with validation_order: {self.validation_order}")

    def _create_validators(self) -> None:
        """Create validators from config."""
        validators_cfg = self.config.validators

        for field_name in validators_cfg.model_fields:
            validator_config = getattr(validators_cfg, field_name, None)
            if validator_config is not None:
                if field_name not in _VALIDATOR_REGISTRY:
                    logger.warning(f"Unknown validator: {field_name}")
                    continue
                try:
                    validator_cls = _VALIDATOR_REGISTRY[field_name]
                    validator = validator_cls(llm=self.llm, **validator_config.model_dump())
                    self.validators_by_name[field_name] = validator
                except Exception as e:
                    logger.warning(f"Failed to create validator {field_name}: {e}")

        # Set up validation order
        if self.config.validation_order:
            # Use configured order, filter out validators that weren't created
            self.validation_order = [
                [name for name in group if name in self.validators_by_name] for group in self.config.validation_order
            ]
            self.validation_order = [g for g in self.validation_order if g]
        else:
            # Default: each validator in its own group (sequential execution)
            self.validation_order = [[name] for name in self.validators_by_name]

    async def _run_validator(self, validator: BaseValidator, **kwargs: Any) -> PostprocessingResult:
        """Run a single validator."""
        try:
            result = await validator.validate(**kwargs)
            if result.passed:
                return PostprocessingResult(passed=True)
            else:
                logger.info(f"{validator.name}: FAILED with issues: {result.issues}")
                feedback = f"[VALIDATION FAILED]\n{validator.name}:\n{validator.format_feedback(result.issues)}"
                return PostprocessingResult(passed=False, feedback=feedback)
        except Exception as e:
            if self.config.fail_open_on_validator_error:
                logger.warning(f"{validator.name} error (fail-open): {e}")
                return PostprocessingResult(passed=True)
            else:
                logger.error(f"{validator.name} error (fail-closed): {e}")
                return PostprocessingResult(
                    passed=False,
                    feedback=f"[VALIDATION ERROR]\n{validator.name}: {e}",
                )

    async def process(
        self,
        output: str,
        user_query: str = "",
        scratchpad: list[BaseMessage] | None = None,
        llm_reasoning: bool = False,
    ) -> PostprocessingResult:
        """Run validators in groups. Validators in same group run concurrently with aggregated feedback."""
        if (not output or not output.strip()) and "non_empty_response_validator" not in self.validators_by_name:
            return PostprocessingResult(passed=True)

        trajectory = extract_current_trajectory(scratchpad or [])
        context = {
            "output": output,
            "user_query": user_query,
            "trajectory": trajectory,
            "llm_reasoning": llm_reasoning,
        }
        logger.info(f"Running validation_order={self.validation_order}")

        for group in self.validation_order:
            if not group:
                continue

            # Run all validators in this group concurrently
            async def run_validator_by_name(name: str) -> PostprocessingResult:
                validator = self.validators_by_name[name]
                return await self._run_validator(validator, **context)

            group_coro = asyncio.gather(*[run_validator_by_name(name) for name in group])

            try:
                if self.config.group_timeout_seconds is not None:
                    results = await asyncio.wait_for(group_coro, timeout=self.config.group_timeout_seconds)
                else:
                    results = await group_coro
            except TimeoutError:
                if self.config.fail_open_on_validator_error:
                    logger.warning(
                        f"Validation group {group} timed out after {self.config.group_timeout_seconds}s (fail-open)"
                    )
                    continue  # treat as passed
                else:
                    logger.error(
                        f"Validation group {group} timed out after {self.config.group_timeout_seconds}s (fail-closed)"
                    )
                    return PostprocessingResult(
                        passed=False,
                        feedback=(
                            f"[VALIDATION TIMEOUT]\nValidation group {group} "
                            f"exceeded {self.config.group_timeout_seconds}s timeout."
                        ),
                    )

            # Collect all failures in this group
            failures = [r for r in results if not r.passed]
            if failures:
                combined_feedback = "\n\n".join(f.feedback for f in failures)
                logger.info(f"Validation group {group} failed: {len(failures)} validator(s)")
                return PostprocessingResult(
                    passed=False,
                    feedback=combined_feedback,
                )

            logger.debug(f"Validation group {group}: PASSED")

        logger.info("All postprocessing validators PASSED")
        return PostprocessingResult(passed=True)
