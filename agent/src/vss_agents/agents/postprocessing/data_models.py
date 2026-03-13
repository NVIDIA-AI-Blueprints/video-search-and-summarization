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

"""Data models for postprocessing module."""

from __future__ import annotations

from pydantic import BaseModel
from pydantic import Field

# Marker used to identify postprocessing feedback messages in scratchpad
POSTPROCESSING_FEEDBACK_MARKER = "[YOUR PREVIOUS RESPONSE FAILED POSTPROCESSING VALIDATION. HERE IS THE FEEDBACK]"


class ValidatorResult(BaseModel):
    """Result from a validator."""

    name: str
    passed: bool
    issues: list[str] = Field(default_factory=list)


class PostprocessingResult(BaseModel):
    """Result from postprocessing node."""

    passed: bool
    feedback: str = ""


# --- Config models ---


class BaseValidatorConfig(BaseModel):
    """Base configuration for validators."""

    feedback_template: str = ""


class URLValidatorConfig(BaseValidatorConfig):
    """Configuration for URL validator."""

    timeout: float = 10.0
    max_retries: int = 2
    internal_ip: str


class NonEmptyResponseValidatorConfig(BaseValidatorConfig):
    """Configuration for non-empty response validator."""

    pass


class LLMBasedRuleValidatorConfig(BaseValidatorConfig):
    """Configuration for LLM-based rule validator."""

    prompt_template: str = ""
    max_retries: int = 2
    llm_name: str | None = (
        None  # Optional: LLM used will defaults to workflow LLM. Specify if you want to use a different LLM.
    )


class ValidatorsConfig(BaseModel):
    """Configuration for all validators."""

    url_validator: URLValidatorConfig | None = None
    non_empty_response_validator: NonEmptyResponseValidatorConfig | None = None
    llm_based_rule_validator: LLMBasedRuleValidatorConfig | None = None


class PostprocessingConfig(BaseModel):
    """Configuration for postprocessing node."""

    enabled: bool = True
    validators: ValidatorsConfig = Field(default_factory=ValidatorsConfig)
    # Validation order: list of groups. Validators in same group run concurrently with aggregated feedback.
    # Groups run sequentially, next group only runs if previous group all passed.
    validation_order: list[list[str]] | None = None
    # Maximum wall-clock seconds for each validation group to complete.
    # None means no timeout (wait indefinitely).
    group_timeout_seconds: float | None = None
    # When True (default), validator exceptions and group timeouts are treated as
    # a pass (fail-open), preserving current behavior. When False, exceptions and
    # timeouts are treated as explicit failures with diagnostic feedback.
    fail_open_on_validator_error: bool = True
