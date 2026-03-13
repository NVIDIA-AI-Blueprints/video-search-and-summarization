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
"""Tests for video_analytics/tools inner functions."""

from unittest.mock import AsyncMock
from unittest.mock import patch

import pytest
from vss_agents.video_analytics.tools import AnalyzeInput
from vss_agents.video_analytics.tools import AverageSpeedsInput
from vss_agents.video_analytics.tools import EmptyInput
from vss_agents.video_analytics.tools import FovHistogramInput
from vss_agents.video_analytics.tools import GetIncidentInput
from vss_agents.video_analytics.tools import GetIncidentsInputBase
from vss_agents.video_analytics.tools import GetSensorIdsInput
from vss_agents.video_analytics.tools import VideoAnalyticsToolConfig
from vss_agents.video_analytics.tools import video_analytics


async def _get_fns(config, mock_builder, mock_es_client):
    """Get raw callable functions from the group, keyed by name."""
    with patch("vss_agents.video_analytics.tools.ESClient", return_value=mock_es_client):
        gen = video_analytics.__wrapped__(config, mock_builder)
        group = await gen.__anext__()

    fns_dict = await group.get_included_functions()
    # Extract the raw callable from LambdaFunction._ainvoke_fn
    # Keys are prefixed like "video_analytics__get_sensor_ids" or "video_analytics.get_sensor_ids"
    result = {}
    for name, func_obj in fns_dict.items():
        if "__" in name:
            short_name = name.split("__", 1)[-1]
        elif "." in name:
            short_name = name.split(".")[-1]
        else:
            short_name = name
        if hasattr(func_obj, "_ainvoke_fn") and func_obj._ainvoke_fn is not None:
            result[short_name] = func_obj._ainvoke_fn
    return result


@pytest.fixture
def config():
    return VideoAnalyticsToolConfig(
        es_url="http://localhost:9200",
        embedding_model_name=None,
        vst_sensor_list_tool=None,
    )


@pytest.fixture
def mock_builder():
    return AsyncMock()


@pytest.fixture
def mock_es_client():
    client = AsyncMock()
    client.get_by_id.return_value = {
        "calibration": {
            "sensors": [
                {
                    "id": "sensor-001",
                    "place": [
                        {"value": "San Jose", "type": "city"},
                        {"value": "Intersection_A", "type": "intersection"},
                    ],
                },
                {
                    "id": "sensor-002",
                    "place": [
                        {"value": "Mountain View", "type": "city"},
                        {"value": "Intersection_B", "type": "intersection"},
                    ],
                },
            ]
        }
    }
    return client


@pytest.mark.asyncio
async def test_get_sensor_ids_fn(config, mock_builder, mock_es_client):
    fns = await _get_fns(config, mock_builder, mock_es_client)
    assert "get_sensor_ids" in fns, f"Expected get_sensor_ids in {list(fns.keys())}"
    result = await fns["get_sensor_ids"](GetSensorIdsInput())
    assert isinstance(result, dict | list)


@pytest.mark.asyncio
async def test_get_places_fn(config, mock_builder, mock_es_client):
    fns = await _get_fns(config, mock_builder, mock_es_client)
    if "get_places" in fns:
        fn = fns["get_places"]
        result = await fn(EmptyInput())
        assert isinstance(result, dict)


@pytest.mark.asyncio
async def test_get_incident_fn(config, mock_builder, mock_es_client):
    mock_es_client.search.return_value = [{"id": "inc1", "category": "test"}]
    fns = await _get_fns(config, mock_builder, mock_es_client)
    if "get_incident" in fns:
        fn = fns["get_incident"]
        result = await fn(GetIncidentInput(id="inc1"))
        assert isinstance(result, dict | list)


@pytest.mark.asyncio
async def test_get_incidents_fn(config, mock_builder, mock_es_client):
    mock_es_client.search.return_value = [{"id": "inc1"}]
    fns = await _get_fns(config, mock_builder, mock_es_client)
    if "get_incidents" in fns:
        fn = fns["get_incidents"]
        result = await fn(GetIncidentsInputBase())
        assert isinstance(result, dict)


@pytest.mark.asyncio
async def test_get_incidents_with_sensor(config, mock_builder, mock_es_client):
    mock_es_client.search.return_value = []
    fns = await _get_fns(config, mock_builder, mock_es_client)
    if "get_incidents" in fns:
        fn = fns["get_incidents"]
        result = await fn(
            GetIncidentsInputBase(
                source="sensor-001",
                source_type="sensor",
                start_time="2025-01-01T00:00:00.000Z",
                end_time="2025-01-01T23:59:59.000Z",
            )
        )
        assert isinstance(result, dict)


@pytest.mark.asyncio
async def test_get_incidents_with_place(config, mock_builder, mock_es_client):
    mock_es_client.search.return_value = []
    fns = await _get_fns(config, mock_builder, mock_es_client)
    if "get_incidents" in fns:
        fn = fns["get_incidents"]
        result = await fn(GetIncidentsInputBase(source="San Jose", source_type="place"))
        assert isinstance(result, dict)


@pytest.mark.asyncio
async def test_get_fov_histogram_fn(config, mock_builder, mock_es_client):
    mock_es_client.aggregate.return_value = {}
    fns = await _get_fns(config, mock_builder, mock_es_client)
    if "get_fov_histogram" in fns:
        fn = fns["get_fov_histogram"]
        result = await fn(
            FovHistogramInput(
                source="sensor-001",
                start_time="2025-01-01T00:00:00.000Z",
                end_time="2025-01-01T01:00:00.000Z",
            )
        )
        assert isinstance(result, dict)


@pytest.mark.asyncio
async def test_get_average_speeds_fn(config, mock_builder, mock_es_client):
    mock_es_client.aggregate.return_value = {
        "directions": {"buckets": [{"key": "North", "averageSpeed": {"value": 25.0}}]}
    }
    fns = await _get_fns(config, mock_builder, mock_es_client)
    if "get_average_speeds" in fns:
        fn = fns["get_average_speeds"]
        result = await fn(
            AverageSpeedsInput(
                source="sensor-001",
                start_time="2025-01-01T00:00:00.000Z",
                end_time="2025-01-01T01:00:00.000Z",
                source_type="sensor",
            )
        )
        assert isinstance(result, dict)


@pytest.mark.asyncio
async def test_analyze_max_min_incidents(config, mock_builder, mock_es_client):
    mock_es_client.search.return_value = [
        {"timestamp": "2025-01-01T10:00:00.000Z", "end": "2025-01-01T10:05:00.000Z"},
        {"timestamp": "2025-01-01T10:03:00.000Z", "end": "2025-01-01T10:08:00.000Z"},
    ]
    fns = await _get_fns(config, mock_builder, mock_es_client)
    if "analyze" in fns:
        fn = fns["analyze"]
        result = await fn(
            AnalyzeInput(
                start_time="2025-01-01T00:00:00.000Z",
                end_time="2025-01-01T23:59:59.000Z",
                source="sensor-001",
                source_type="sensor",
                analysis_type="max_min_incidents",
            )
        )
        assert isinstance(result, str)
        assert "incident" in result.lower()


@pytest.mark.asyncio
async def test_analyze_no_incidents(config, mock_builder, mock_es_client):
    mock_es_client.search.return_value = []
    fns = await _get_fns(config, mock_builder, mock_es_client)
    if "analyze" in fns:
        fn = fns["analyze"]
        result = await fn(
            AnalyzeInput(
                start_time="2025-01-01T00:00:00.000Z",
                end_time="2025-01-01T23:59:59.000Z",
                source="sensor-001",
                source_type="sensor",
                analysis_type="max_min_incidents",
            )
        )
        assert isinstance(result, str)
        assert "no incidents" in result.lower()


@pytest.mark.asyncio
async def test_analyze_average_speed(config, mock_builder, mock_es_client):
    mock_es_client.aggregate.return_value = {
        "directions": {"buckets": [{"key": "East", "averageSpeed": {"value": 30.0}}]}
    }
    fns = await _get_fns(config, mock_builder, mock_es_client)
    if "analyze" in fns:
        fn = fns["analyze"]
        result = await fn(
            AnalyzeInput(
                start_time="2025-01-01T00:00:00.000Z",
                end_time="2025-01-01T01:00:00.000Z",
                source="sensor-001",
                source_type="sensor",
                analysis_type="average_speed",
            )
        )
        assert isinstance(result, str)


@pytest.mark.asyncio
async def test_analyze_avg_num_people(config, mock_builder, mock_es_client):
    mock_es_client.aggregate.return_value = {}
    fns = await _get_fns(config, mock_builder, mock_es_client)
    if "analyze" in fns:
        fn = fns["analyze"]
        result = await fn(
            AnalyzeInput(
                start_time="2025-01-01T00:00:00.000Z",
                end_time="2025-01-01T01:00:00.000Z",
                source="sensor-001",
                source_type="sensor",
                analysis_type="avg_num_people",
            )
        )
        assert isinstance(result, str)


@pytest.mark.asyncio
async def test_analyze_avg_num_vehicles(config, mock_builder, mock_es_client):
    mock_es_client.aggregate.return_value = {}
    fns = await _get_fns(config, mock_builder, mock_es_client)
    if "analyze" in fns:
        fn = fns["analyze"]
        result = await fn(
            AnalyzeInput(
                start_time="2025-01-01T00:00:00.000Z",
                end_time="2025-01-01T01:00:00.000Z",
                source="sensor-001",
                source_type="sensor",
                analysis_type="avg_num_vehicles",
            )
        )
        assert isinstance(result, str)
