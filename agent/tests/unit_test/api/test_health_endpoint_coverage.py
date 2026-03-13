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
"""Tests for health_endpoint inner function."""

from unittest.mock import MagicMock

import pytest
from vss_agents.api.health_endpoint import HealthEndpointConfig
from vss_agents.api.health_endpoint import health_endpoint


class TestHealthEndpointConfig:
    """Test HealthEndpointConfig model."""

    def test_defaults(self):
        config = HealthEndpointConfig()
        assert config.description == "Check if the service is healthy"

    def test_custom(self):
        config = HealthEndpointConfig(description="Custom health check")
        assert config.description == "Custom health check"


class TestHealthEndpointInner:
    """Test the inner _health_endpoint function."""

    @pytest.mark.asyncio
    async def test_health_check(self):
        config = HealthEndpointConfig()
        mock_builder = MagicMock()

        gen = health_endpoint.__wrapped__(config, mock_builder)
        function_info = await gen.__anext__()
        inner_fn = function_info.single_fn

        result = await inner_fn(None)
        assert result == {"isAlive": True}
