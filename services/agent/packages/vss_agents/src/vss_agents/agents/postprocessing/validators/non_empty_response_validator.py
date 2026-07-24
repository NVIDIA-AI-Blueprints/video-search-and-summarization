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

"""Validator that ensures the response is not empty."""

import logging
from typing import Any

from vss_agents.agents.postprocessing.data_models import ValidatorResult
from vss_agents.agents.postprocessing.validators.base import BaseValidator

logger = logging.getLogger(__name__)


class NonEmptyResponseValidator(BaseValidator):
    """Validates that the response is not empty."""

    name = "non_empty_response_validator"

    def __init__(
        self,
        feedback_template: str = "",
        **kwargs: Any,  # noqa: ARG002
    ) -> None:
        """Initialize the non-empty response validator.

        Args:
            feedback_template: Template for feedback message. Use {issues} placeholder.
        """
        super().__init__(
            feedback_template=feedback_template,
        )

    async def validate(self, output: str, **kwargs: Any) -> ValidatorResult:  # noqa: ARG002
        """Validate that the output is not empty.

        Args:
            output: The response to validate.
            **kwargs: Additional context.

        Returns:
            ValidatorResult with pass/fail status.
        """
        stripped = output.strip() if output else ""

        if not stripped:
            logger.info(f"{self.name}: Response is empty")
            return ValidatorResult(
                name=self.name,
                passed=False,
                issues=["Response is empty"],
            )

        logger.info(f"{self.name}: PASSED")
        return ValidatorResult(name=self.name, passed=True)
