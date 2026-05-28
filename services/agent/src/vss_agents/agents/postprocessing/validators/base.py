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

"""Base class for validators."""

from abc import ABC
from abc import abstractmethod
from typing import Any
from typing import ClassVar

from vss_agents.agents.postprocessing.data_models import ValidatorResult


class BaseValidator(ABC):
    """Base class for validators."""

    name: ClassVar[str] = "base_validator"

    def __init__(
        self,
        feedback_template: str | None = None,
    ) -> None:
        """Initialize the base validator.

        Args:
            feedback_template: Template for formatting validation feedback. Use {issues} placeholder.
        """
        self.feedback_template = feedback_template or ""

    @abstractmethod
    async def validate(self, output: str, **kwargs: Any) -> ValidatorResult:
        """Run the validation.

        Args:
            output: The agent's final response to validate.
            **kwargs: Additional context.
        """
        pass

    def format_feedback(self, issues: list[str]) -> str:
        """Format feedback with template support. Use {issues} placeholder."""
        if not issues:
            return ""
        issues_str = ", ".join(issues)
        if not self.feedback_template:
            return issues_str
        try:
            return self.feedback_template.format(issues=issues_str)
        except KeyError:
            return self.feedback_template
