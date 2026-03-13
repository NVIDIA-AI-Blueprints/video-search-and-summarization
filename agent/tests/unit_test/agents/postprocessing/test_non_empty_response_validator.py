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

"""Unit tests for NonEmptyResponseValidator."""

import pytest
from vss_agents.agents.postprocessing.validators.non_empty_response_validator import NonEmptyResponseValidator


@pytest.fixture
def validator():
    return NonEmptyResponseValidator()


@pytest.fixture
def validator_with_template():
    return NonEmptyResponseValidator(feedback_template="Issue: {issues}")


class TestNonEmptyResponseValidator:
    """Tests for NonEmptyResponseValidator."""

    @pytest.mark.asyncio
    async def test_passes_on_non_empty_output(self, validator):
        result = await validator.validate("Hello world")
        assert result.passed is True
        assert result.issues == []

    @pytest.mark.asyncio
    async def test_fails_on_empty_string(self, validator):
        result = await validator.validate("")
        assert result.passed is False
        assert "Response is empty" in result.issues

    @pytest.mark.asyncio
    async def test_fails_on_whitespace_only(self, validator):
        result = await validator.validate("   \n\t  ")
        assert result.passed is False
        assert "Response is empty" in result.issues

    def test_feedback_template(self, validator_with_template):
        feedback = validator_with_template.format_feedback(["Response is empty"])
        assert "Issue:" in feedback

    def test_feedback_template_none_normalized(self):
        v = NonEmptyResponseValidator(feedback_template=None)
        assert v.feedback_template == ""
