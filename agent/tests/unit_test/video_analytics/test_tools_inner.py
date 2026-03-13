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
"""Tests for video_analytics/tools generator initialization."""

from unittest.mock import AsyncMock
from unittest.mock import patch

import pytest
from vss_agents.video_analytics.tools import VideoAnalyticsToolConfig
from vss_agents.video_analytics.tools import video_analytics


class TestVideoAnalyticsInitialization:
    """Test video_analytics generator initialization with different configs."""

    @pytest.fixture
    def mock_builder(self):
        return AsyncMock()

    @pytest.mark.asyncio
    async def test_init_with_calibration(self, mock_builder):
        config = VideoAnalyticsToolConfig(
            es_url="http://localhost:9200",
            embedding_model_name=None,
        )
        mock_es_client = AsyncMock()
        mock_es_client.get_by_id.return_value = {
            "calibration": {
                "sensors": [
                    {"id": "sensor-001", "place": [{"value": "San Jose"}, {"value": "Intersection_A"}]},
                ]
            }
        }

        with patch("vss_agents.video_analytics.tools.ESClient", return_value=mock_es_client):
            gen = video_analytics.__wrapped__(config, mock_builder)
            group = await gen.__anext__()
        assert group is not None

    @pytest.mark.asyncio
    async def test_init_calibration_failure(self, mock_builder):
        config = VideoAnalyticsToolConfig(
            es_url="http://localhost:9200",
            embedding_model_name=None,
        )
        mock_es_client = AsyncMock()
        mock_es_client.get_by_id.side_effect = RuntimeError("ES unavailable")

        with patch("vss_agents.video_analytics.tools.ESClient", return_value=mock_es_client):
            gen = video_analytics.__wrapped__(config, mock_builder)
            group = await gen.__anext__()
        assert group is not None

    @pytest.mark.asyncio
    async def test_init_calibration_none(self, mock_builder):
        config = VideoAnalyticsToolConfig(
            es_url="http://localhost:9200",
            embedding_model_name=None,
        )
        mock_es_client = AsyncMock()
        mock_es_client.get_by_id.return_value = None

        with patch("vss_agents.video_analytics.tools.ESClient", return_value=mock_es_client):
            gen = video_analytics.__wrapped__(config, mock_builder)
            group = await gen.__anext__()
        assert group is not None

    @pytest.mark.asyncio
    async def test_init_with_embeddings(self, mock_builder):
        config = VideoAnalyticsToolConfig(
            es_url="http://localhost:9200",
            embedding_model_name="sentence-transformers/all-MiniLM-L6-v2",
        )
        mock_es_client = AsyncMock()
        mock_es_client.get_by_id.return_value = {
            "calibration": {
                "sensors": [
                    {"id": "sensor-001", "place": [{"value": "San Jose"}, {"value": "Intersection_A"}]},
                    {"id": "sensor-002", "place": [{"value": ""}, {"value": "Intersection_B"}]},
                ]
            }
        }

        with patch("vss_agents.video_analytics.tools.ESClient", return_value=mock_es_client):
            gen = video_analytics.__wrapped__(config, mock_builder)
            group = await gen.__anext__()
        assert group is not None

    @pytest.mark.asyncio
    async def test_init_with_vlm_verified(self, mock_builder):
        config = VideoAnalyticsToolConfig(
            es_url="http://localhost:9200",
            vlm_verified=True,
            embedding_model_name=None,
        )
        mock_es_client = AsyncMock()
        mock_es_client.get_by_id.return_value = {"calibration": {"sensors": []}}

        with patch("vss_agents.video_analytics.tools.ESClient", return_value=mock_es_client):
            gen = video_analytics.__wrapped__(config, mock_builder)
            group = await gen.__anext__()
        assert group is not None

    @pytest.mark.asyncio
    async def test_init_with_vst_sensor_tool(self, mock_builder):
        config = VideoAnalyticsToolConfig(
            es_url="http://localhost:9200",
            vst_sensor_list_tool="vst_sensor_list",
            embedding_model_name=None,
        )
        mock_es_client = AsyncMock()
        mock_es_client.get_by_id.return_value = {"calibration": {"sensors": []}}

        with patch("vss_agents.video_analytics.tools.ESClient", return_value=mock_es_client):
            gen = video_analytics.__wrapped__(config, mock_builder)
            group = await gen.__anext__()
        assert group is not None

    @pytest.mark.asyncio
    async def test_init_custom_include(self, mock_builder):
        config = VideoAnalyticsToolConfig(
            es_url="http://localhost:9200",
            include=["get_incidents", "get_sensor_ids"],
            embedding_model_name=None,
        )
        mock_es_client = AsyncMock()
        mock_es_client.get_by_id.return_value = {"calibration": {"sensors": []}}

        with patch("vss_agents.video_analytics.tools.ESClient", return_value=mock_es_client):
            gen = video_analytics.__wrapped__(config, mock_builder)
            group = await gen.__anext__()
        assert group is not None
