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
"""Deep coverage tests for video_analytics/tools inner functions."""

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


async def _setup(config, mock_builder, mock_es_client):
    """Setup and return functions dict."""
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
async def test_get_sensor_ids_with_place_filter(config, mock_builder, mock_es_client):
    fns = await _setup(config, mock_builder, mock_es_client)
    result = await fns["get_sensor_ids"](GetSensorIdsInput(place="Intersection_A"))
    assert isinstance(result, dict | list)


@pytest.mark.asyncio
async def test_get_sensor_ids_all(config, mock_builder, mock_es_client):
    fns = await _setup(config, mock_builder, mock_es_client)
    result = await fns["get_sensor_ids"](GetSensorIdsInput())
    assert isinstance(result, dict | list)


@pytest.mark.asyncio
async def test_get_incident_found(config, mock_builder, mock_es_client):
    mock_es_client.search.return_value = [{"id": "inc1", "category": "traffic"}]
    fns = await _setup(config, mock_builder, mock_es_client)
    result = await fns["get_incident"](GetIncidentInput(id="inc1"))
    assert isinstance(result, dict)


@pytest.mark.asyncio
async def test_get_incident_not_found(config, mock_builder, mock_es_client):
    mock_es_client.search.return_value = []
    fns = await _setup(config, mock_builder, mock_es_client)
    result = await fns["get_incident"](GetIncidentInput(id="nonexistent"))
    assert result == {}


@pytest.mark.asyncio
async def test_get_incident_with_includes(config, mock_builder, mock_es_client):
    mock_es_client.search.return_value = [{"id": "inc1", "place": "SJ", "category": "jaywalking"}]
    fns = await _setup(config, mock_builder, mock_es_client)
    result = await fns["get_incident"](GetIncidentInput(id="inc1", includes=["place", "category"]))
    assert isinstance(result, dict)


@pytest.mark.asyncio
async def test_get_incidents_with_source_and_time(config, mock_builder, mock_es_client):
    mock_es_client.search.return_value = [
        {"id": "inc1", "timestamp": "2025-01-01T10:00:00.000Z", "end": "2025-01-01T10:05:00.000Z"},
        {"id": "inc2", "timestamp": "2025-01-01T11:00:00.000Z", "end": "2025-01-01T11:05:00.000Z"},
    ]
    fns = await _setup(config, mock_builder, mock_es_client)
    result = await fns["get_incidents"](
        GetIncidentsInputBase(
            source="sensor-001",
            source_type="sensor",
            start_time="2025-01-01T00:00:00.000Z",
            end_time="2025-01-01T23:59:59.000Z",
            max_count=10,
            includes=["place"],
        )
    )
    assert isinstance(result, dict)
    assert "incidents" in result
    assert "has_more" in result


@pytest.mark.asyncio
async def test_get_incidents_has_more(config, mock_builder, mock_es_client):
    """Test pagination - when more results exist than max_count."""
    mock_es_client.search.return_value = [{"id": f"inc{i}"} for i in range(3)]
    fns = await _setup(config, mock_builder, mock_es_client)
    result = await fns["get_incidents"](GetIncidentsInputBase(max_count=2))
    assert result["has_more"] is True
    assert len(result["incidents"]) == 2


@pytest.mark.asyncio
async def test_get_fov_histogram_with_data(config, mock_builder, mock_es_client):
    mock_es_client.aggregate.return_value = {
        "eventsOverTime": {
            "buckets": [
                {
                    "key_as_string": "2025-01-01T00:00:00.000Z",
                    "key": 1735689600000,
                    "fov": {
                        "searchAggFilter": {
                            "objectType": {
                                "buckets": [
                                    {"key": "Person", "avgCount": {"value": 5.0}},
                                    {"key": "Vehicle", "avgCount": {"value": 2.0}},
                                ]
                            }
                        }
                    },
                }
            ]
        }
    }
    fns = await _setup(config, mock_builder, mock_es_client)
    result = await fns["get_fov_histogram"](
        FovHistogramInput(
            source="sensor-001",
            start_time="2025-01-01T00:00:00.000Z",
            end_time="2025-01-01T01:00:00.000Z",
        )
    )
    assert "histogram" in result
    assert "bucketSizeInSec" in result


@pytest.mark.asyncio
async def test_get_average_speeds_no_data(config, mock_builder, mock_es_client):
    mock_es_client.aggregate.return_value = {"directions": {"buckets": []}}
    fns = await _setup(config, mock_builder, mock_es_client)
    result = await fns["get_average_speeds"](
        AverageSpeedsInput(
            source="sensor-001",
            start_time="2025-01-01T00:00:00.000Z",
            end_time="2025-01-01T01:00:00.000Z",
            source_type="sensor",
        )
    )
    assert result["metrics"] == []


@pytest.mark.asyncio
async def test_get_average_speeds_null_value(config, mock_builder, mock_es_client):
    mock_es_client.aggregate.return_value = {
        "directions": {"buckets": [{"key": "North", "averageSpeed": {"value": None}}]}
    }
    fns = await _setup(config, mock_builder, mock_es_client)
    result = await fns["get_average_speeds"](
        AverageSpeedsInput(
            source="sensor-001",
            start_time="2025-01-01T00:00:00.000Z",
            end_time="2025-01-01T01:00:00.000Z",
            source_type="sensor",
        )
    )
    assert result["metrics"][0]["averageSpeed"] == "0 mph"


@pytest.mark.asyncio
async def test_analyze_max_min_with_data(config, mock_builder, mock_es_client):
    mock_es_client.search.return_value = [
        {"timestamp": "2025-01-01T10:00:00.000Z", "end": "2025-01-01T10:05:00.000Z"},
        {"timestamp": "2025-01-01T10:02:00.000Z", "end": "2025-01-01T10:07:00.000Z"},
        {"timestamp": "2025-01-01T10:04:00.000Z", "end": "2025-01-01T10:09:00.000Z"},
    ]
    fns = await _setup(config, mock_builder, mock_es_client)
    result = await fns["analyze"](
        AnalyzeInput(
            start_time="2025-01-01T00:00:00.000Z",
            end_time="2025-01-01T23:59:59.000Z",
            source="sensor-001",
            source_type="sensor",
            analysis_type="max_min_incidents",
        )
    )
    assert isinstance(result, str)
    assert "Maximum overlap" in result
    assert "Minimum overlap" in result


@pytest.mark.asyncio
async def test_analyze_average_speed_with_data(config, mock_builder, mock_es_client):
    mock_es_client.aggregate.return_value = {
        "directions": {
            "buckets": [
                {"key": "North", "averageSpeed": {"value": 25.0}},
                {"key": "South", "averageSpeed": {"value": 30.0}},
            ]
        }
    }
    fns = await _setup(config, mock_builder, mock_es_client)
    result = await fns["analyze"](
        AnalyzeInput(
            start_time="2025-01-01T00:00:00.000Z",
            end_time="2025-01-01T01:00:00.000Z",
            source="sensor-001",
            source_type="sensor",
            analysis_type="average_speed",
        )
    )
    assert "North" in result
    assert "South" in result


@pytest.mark.asyncio
async def test_analyze_avg_num_people_with_data(config, mock_builder, mock_es_client):
    mock_es_client.aggregate.return_value = {
        "eventsOverTime": {
            "buckets": [
                {
                    "key_as_string": "2025-01-01T00:00:00.000Z",
                    "key": 1735689600000,
                    "fov": {
                        "searchAggFilter": {"objectType": {"buckets": [{"key": "Person", "avgCount": {"value": 3.0}}]}}
                    },
                }
            ]
        }
    }
    fns = await _setup(config, mock_builder, mock_es_client)
    result = await fns["analyze"](
        AnalyzeInput(
            start_time="2025-01-01T00:00:00.000Z",
            end_time="2025-01-01T01:00:00.000Z",
            source="sensor-001",
            source_type="sensor",
            analysis_type="avg_num_people",
        )
    )
    assert "average" in result.lower()
    assert "3" in result


@pytest.mark.asyncio
async def test_analyze_avg_num_vehicles_with_data(config, mock_builder, mock_es_client):
    mock_es_client.aggregate.return_value = {
        "eventsOverTime": {
            "buckets": [
                {
                    "key_as_string": "2025-01-01T00:00:00.000Z",
                    "key": 1735689600000,
                    "fov": {
                        "searchAggFilter": {"objectType": {"buckets": [{"key": "Vehicle", "avgCount": {"value": 7.0}}]}}
                    },
                }
            ]
        }
    }
    fns = await _setup(config, mock_builder, mock_es_client)
    result = await fns["analyze"](
        AnalyzeInput(
            start_time="2025-01-01T00:00:00.000Z",
            end_time="2025-01-01T01:00:00.000Z",
            source="sensor-001",
            source_type="sensor",
            analysis_type="avg_num_vehicles",
        )
    )
    assert "average" in result.lower()
    assert "7" in result


@pytest.mark.asyncio
async def test_analyze_average_speed_no_data(config, mock_builder, mock_es_client):
    mock_es_client.aggregate.return_value = {"directions": {"buckets": []}}
    fns = await _setup(config, mock_builder, mock_es_client)
    result = await fns["analyze"](
        AnalyzeInput(
            start_time="2025-01-01T00:00:00.000Z",
            end_time="2025-01-01T01:00:00.000Z",
            source="sensor-001",
            source_type="sensor",
            analysis_type="average_speed",
        )
    )
    assert "no speed data" in result.lower()


@pytest.mark.asyncio
async def test_get_places_from_cache(config, mock_builder, mock_es_client):
    fns = await _setup(config, mock_builder, mock_es_client)
    result = await fns["get_places"](EmptyInput())
    assert isinstance(result, dict)


@pytest.mark.asyncio
async def test_get_places_empty_cache(mock_builder):
    config = VideoAnalyticsToolConfig(
        es_url="http://localhost:9200",
        embedding_model_name=None,
    )
    mock_es = AsyncMock()
    # Return None for calibration → empty cache
    mock_es.get_by_id.return_value = None

    fns = await _setup(config, mock_builder, mock_es)
    result = await fns["get_places"](EmptyInput())
    assert isinstance(result, dict)
    assert result == {}
