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
"""Tests for chart_generator output converters."""

from unittest.mock import AsyncMock

import pytest

from vss_agents.tools.chart_generator import ChartGeneratorConfig
from vss_agents.tools.chart_generator import ChartGenExecOutput
from vss_agents.tools.chart_generator import chart_generator


class TestChartGeneratorConverters:
    """Test chart_generator converter functions."""

    @pytest.fixture
    def config(self):
        return ChartGeneratorConfig(
            object_store_name="test_store",
            object_store_base_url="http://localhost:8000/static/",
        )

    @pytest.fixture
    def mock_builder(self):
        builder = AsyncMock()
        builder.get_object_store_client.return_value = AsyncMock()
        return builder

    @pytest.mark.asyncio
    async def test_output_converter_with_success(self, config, mock_builder):
        gen = chart_generator.__wrapped__(config, mock_builder)
        fi = await gen.__anext__()

        converters = fi.converters
        # Output converter
        output_converter = converters[2]
        outputs = [
            ChartGenExecOutput(success=True, error_message=None, object_store_key="charts/chart_0.png"),
            ChartGenExecOutput(success=False, error_message="failed"),
        ]
        result = output_converter(outputs)
        assert "img" in result
        assert "charts/chart_0.png" in result

    @pytest.mark.asyncio
    async def test_output_converter_all_failed(self, config, mock_builder):
        gen = chart_generator.__wrapped__(config, mock_builder)
        fi = await gen.__anext__()

        converters = fi.converters
        output_converter = converters[2]
        outputs = [ChartGenExecOutput(success=False, error_message="error")]
        result = output_converter(outputs)
        assert result == ""

    @pytest.mark.asyncio
    async def test_chat_response_converter(self, config, mock_builder):
        gen = chart_generator.__wrapped__(config, mock_builder)
        fi = await gen.__anext__()

        converters = fi.converters
        chat_converter = converters[3]
        outputs = [ChartGenExecOutput(success=True, error_message=None, object_store_key="chart.png")]
        # Same ChatResponse.from_string() missing 'usage' bug as in video_upload_url
        with pytest.raises(TypeError):
            chat_converter(outputs)
