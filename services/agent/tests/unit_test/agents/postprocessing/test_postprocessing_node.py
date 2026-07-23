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

"""Unit tests for PostprocessingNode."""

from unittest.mock import AsyncMock

import pytest

from vss_agents.agents.postprocessing.data_models import NonEmptyResponseValidatorConfig
from vss_agents.agents.postprocessing.data_models import PostprocessingConfig
from vss_agents.agents.postprocessing.data_models import URLValidatorConfig
from vss_agents.agents.postprocessing.data_models import ValidatorResult
from vss_agents.agents.postprocessing.data_models import ValidatorsConfig
from vss_agents.agents.postprocessing.postprocessing_node import PostprocessingNode


class TestPostprocessingNodeInit:
    """Tests for PostprocessingNode initialization."""

    def test_default_config(self):
        node = PostprocessingNode()
        assert node.config.enabled is True
        assert node.validators_by_name == {}
        assert node.validation_order == []

    def test_creates_non_empty_validator(self):
        config = PostprocessingConfig(
            validators=ValidatorsConfig(non_empty_response_validator=NonEmptyResponseValidatorConfig())
        )
        node = PostprocessingNode(config=config)
        assert "non_empty_response_validator" in node.validators_by_name

    def test_creates_url_validator(self):
        config = PostprocessingConfig(
            validators=ValidatorsConfig(url_validator=URLValidatorConfig(internal_ip="127.0.0.1"))
        )
        node = PostprocessingNode(config=config)
        assert "url_validator" in node.validators_by_name

    def test_custom_validation_order(self):
        config = PostprocessingConfig(
            validators=ValidatorsConfig(
                url_validator=URLValidatorConfig(internal_ip="127.0.0.1"),
                non_empty_response_validator=NonEmptyResponseValidatorConfig(),
            ),
            validation_order=[["non_empty_response_validator", "url_validator"]],
        )
        node = PostprocessingNode(config=config)
        assert node.validation_order == [["non_empty_response_validator", "url_validator"]]

    def test_default_validation_order_is_sequential(self):
        config = PostprocessingConfig(
            validators=ValidatorsConfig(
                url_validator=URLValidatorConfig(internal_ip="127.0.0.1"),
                non_empty_response_validator=NonEmptyResponseValidatorConfig(),
            ),
        )
        node = PostprocessingNode(config=config)
        # Each validator in its own group
        for group in node.validation_order:
            assert len(group) == 1


class TestPostprocessingNodeProcess:
    """Tests for PostprocessingNode.process()."""

    @pytest.mark.asyncio
    async def test_empty_output_passes_without_non_empty_validator(self):
        config = PostprocessingConfig(
            validators=ValidatorsConfig(url_validator=URLValidatorConfig(internal_ip="127.0.0.1"))
        )
        node = PostprocessingNode(config=config)
        result = await node.process("")
        assert result.passed is True

    @pytest.mark.asyncio
    async def test_empty_output_fails_with_non_empty_validator(self):
        config = PostprocessingConfig(
            validators=ValidatorsConfig(non_empty_response_validator=NonEmptyResponseValidatorConfig())
        )
        node = PostprocessingNode(config=config)
        result = await node.process("")
        assert result.passed is False

    @pytest.mark.asyncio
    async def test_non_empty_output_passes_non_empty_validator(self):
        config = PostprocessingConfig(
            validators=ValidatorsConfig(non_empty_response_validator=NonEmptyResponseValidatorConfig())
        )
        node = PostprocessingNode(config=config)
        result = await node.process("Hello world")
        assert result.passed is True

    @pytest.mark.asyncio
    async def test_no_validators_passes(self):
        node = PostprocessingNode()
        result = await node.process("anything")
        assert result.passed is True


class TestPostprocessingNodeFailOpen:
    """Tests for fail-open / fail-closed behavior."""

    @pytest.mark.asyncio
    async def test_fail_open_on_validator_exception(self):
        config = PostprocessingConfig(
            validators=ValidatorsConfig(non_empty_response_validator=NonEmptyResponseValidatorConfig()),
            fail_open_on_validator_error=True,
        )
        node = PostprocessingNode(config=config)

        # Make the validator raise
        validator = node.validators_by_name["non_empty_response_validator"]
        validator.validate = AsyncMock(side_effect=RuntimeError("boom"))

        result = await node.process("test output")
        assert result.passed is True  # fail-open

    @pytest.mark.asyncio
    async def test_fail_closed_on_validator_exception(self):
        config = PostprocessingConfig(
            validators=ValidatorsConfig(non_empty_response_validator=NonEmptyResponseValidatorConfig()),
            fail_open_on_validator_error=False,
        )
        node = PostprocessingNode(config=config)

        validator = node.validators_by_name["non_empty_response_validator"]
        validator.validate = AsyncMock(side_effect=RuntimeError("boom"))

        result = await node.process("test output")
        assert result.passed is False
        assert "VALIDATION ERROR" in result.feedback


class TestPostprocessingNodeGroupTimeout:
    """Tests for group timeout behavior."""

    @pytest.mark.asyncio
    async def test_group_timeout_fail_open(self):
        config = PostprocessingConfig(
            validators=ValidatorsConfig(non_empty_response_validator=NonEmptyResponseValidatorConfig()),
            group_timeout_seconds=0.001,  # very short timeout
            fail_open_on_validator_error=True,
        )
        node = PostprocessingNode(config=config)

        # Make validator hang
        async def slow_validate(**kwargs):
            import asyncio

            await asyncio.sleep(10)
            return ValidatorResult(name="test", passed=True)

        validator = node.validators_by_name["non_empty_response_validator"]
        validator.validate = slow_validate

        result = await node.process("test output")
        assert result.passed is True  # fail-open on timeout

    @pytest.mark.asyncio
    async def test_group_timeout_fail_closed(self):
        config = PostprocessingConfig(
            validators=ValidatorsConfig(non_empty_response_validator=NonEmptyResponseValidatorConfig()),
            group_timeout_seconds=0.001,
            fail_open_on_validator_error=False,
        )
        node = PostprocessingNode(config=config)

        async def slow_validate(**kwargs):
            import asyncio

            await asyncio.sleep(10)
            return ValidatorResult(name="test", passed=True)

        validator = node.validators_by_name["non_empty_response_validator"]
        validator.validate = slow_validate

        result = await node.process("test output")
        assert result.passed is False
        assert "TIMEOUT" in result.feedback
