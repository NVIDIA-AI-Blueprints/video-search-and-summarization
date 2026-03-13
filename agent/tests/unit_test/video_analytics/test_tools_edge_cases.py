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
"""Edge case tests for video_analytics/tools to cover remaining lines."""

from unittest.mock import AsyncMock
from unittest.mock import patch

import pytest
from vss_agents.video_analytics.tools import AnalyzeInput
from vss_agents.video_analytics.tools import EmptyInput
from vss_agents.video_analytics.tools import GetSensorIdsInput
from vss_agents.video_analytics.tools import VideoAnalyticsToolConfig
from vss_agents.video_analytics.tools import video_analytics


async def _setup(config, mock_builder, mock_es_client):
    with patch("vss_agents.video_analytics.tools.ESClient", return_value=mock_es_client):
        gen = video_analytics.__wrapped__(config, mock_builder)
        group = await gen.__anext__()
    fns_dict = await group.get_included_functions()
    result = {}
    for name, func_obj in fns_dict.items():
        # Keys may be prefixed like "video_analytics__get_sensor_ids" or "video_analytics.get_sensor_ids"
        if "__" in name:
            short_name = name.split("__", 1)[-1]
        elif "." in name:
            short_name = name.split(".")[-1]
        else:
            short_name = name
        if hasattr(func_obj, "_ainvoke_fn") and func_obj._ainvoke_fn is not None:
            result[short_name] = func_obj._ainvoke_fn
    return result


@pytest.mark.asyncio
async def test_get_sensor_ids_empty_cache():
    """Test _get_sensor_ids when calibration returns empty → fallback path."""
    config = VideoAnalyticsToolConfig(es_url="http://localhost:9200", embedding_model_name=None)
    mock_builder = AsyncMock()
    mock_es = AsyncMock()
    # Return calibration with empty sensors initially → cached_sensors is empty
    mock_es.get_by_id.return_value = {"calibration": {"sensors": []}}

    fns = await _setup(config, mock_builder, mock_es)

    # Now set mock for fallback get_by_id call inside _get_sensor_ids
    mock_es.get_by_id.return_value = {"calibration": {"sensors": [{"id": "s1"}]}}
    result = await fns["get_sensor_ids"](GetSensorIdsInput())
    assert isinstance(result, dict | list)


@pytest.mark.asyncio
async def test_get_sensor_ids_empty_cache_no_calibration():
    """Test _get_sensor_ids when calibration cache is empty AND fallback returns None."""
    config = VideoAnalyticsToolConfig(es_url="http://localhost:9200", embedding_model_name=None)
    mock_builder = AsyncMock()
    mock_es = AsyncMock()
    mock_es.get_by_id.return_value = {"calibration": {"sensors": []}}

    fns = await _setup(config, mock_builder, mock_es)

    # Fallback also returns None
    mock_es.get_by_id.return_value = None
    result = await fns["get_sensor_ids"](GetSensorIdsInput())
    assert isinstance(result, dict | list)


@pytest.mark.asyncio
async def test_get_places_empty_cache():
    """Test _get_places when place_map is empty → fallback path."""
    config = VideoAnalyticsToolConfig(es_url="http://localhost:9200", embedding_model_name=None)
    mock_builder = AsyncMock()
    mock_es = AsyncMock()
    # Empty sensors → empty place_map
    mock_es.get_by_id.return_value = {"calibration": {"sensors": []}}

    fns = await _setup(config, mock_builder, mock_es)

    # Fallback returns calibration with sensors
    mock_es.get_by_id.return_value = {
        "calibration": {
            "sensors": [
                {"id": "s1", "place": [{"value": "City", "type": "city"}, {"value": "Street", "type": "intersection"}]}
            ]
        }
    }
    result = await fns["get_places"](EmptyInput())
    assert isinstance(result, dict)


@pytest.mark.asyncio
async def test_get_places_empty_cache_no_data():
    """Test _get_places when empty cache AND fallback returns None."""
    config = VideoAnalyticsToolConfig(es_url="http://localhost:9200", embedding_model_name=None)
    mock_builder = AsyncMock()
    mock_es = AsyncMock()
    mock_es.get_by_id.return_value = {"calibration": {"sensors": []}}

    fns = await _setup(config, mock_builder, mock_es)

    mock_es.get_by_id.return_value = None
    result = await fns["get_places"](EmptyInput())
    assert result == {}


@pytest.mark.asyncio
async def test_analyze_max_min_no_valid_timestamps():
    """Test analyze when incidents have no valid timestamps (line 803)."""
    config = VideoAnalyticsToolConfig(es_url="http://localhost:9200", embedding_model_name=None)
    mock_builder = AsyncMock()
    mock_es = AsyncMock()
    mock_es.get_by_id.return_value = {"calibration": {"sensors": []}}
    # Return incidents without valid timestamps
    mock_es.search.return_value = [
        {"timestamp": None, "end": None},
        {"no_timestamp": True},
    ]

    fns = await _setup(config, mock_builder, mock_es)
    result = await fns["analyze"](
        AnalyzeInput(
            start_time="2025-01-01T00:00:00.000Z",
            end_time="2025-01-01T23:59:59.000Z",
            source="sensor-001",
            source_type="sensor",
            analysis_type="max_min_incidents",
        )
    )
    assert "no valid incidents" in result.lower() or "no incidents" in result.lower()


@pytest.mark.asyncio
async def test_init_with_vst_sensor_list():
    """Test initialization with VST sensor list tool configured."""
    config = VideoAnalyticsToolConfig(
        es_url="http://localhost:9200",
        embedding_model_name=None,
        vst_sensor_list_tool="vst_sensor_list",
    )
    mock_builder = AsyncMock()
    mock_es = AsyncMock()
    mock_es.get_by_id.return_value = {
        "calibration": {
            "sensors": [
                {"id": "s1", "place": [{"value": "SJ", "type": "city"}, {"value": "Int_A", "type": "intersection"}]}
            ]
        }
    }

    fns = await _setup(config, mock_builder, mock_es)

    # Test get_sensor_ids with VST tool - mock the builder.get_tool
    mock_vst_tool = AsyncMock()
    mock_vst_tool.ainvoke.return_value = '{"s1": {"name": "s1"}, "s2": {"name": "s2"}}'
    mock_builder.get_tool.return_value = mock_vst_tool

    result = await fns["get_sensor_ids"](GetSensorIdsInput())
    assert isinstance(result, dict | list)
