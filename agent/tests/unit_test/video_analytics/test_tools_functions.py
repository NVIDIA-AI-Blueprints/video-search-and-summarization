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
"""Tests for video_analytics/tools.py inner functions with mocked ES client."""

from unittest.mock import AsyncMock
from unittest.mock import MagicMock
from unittest.mock import patch

import pytest

from vss_agents.video_analytics.tools import AnalyzeInput
from vss_agents.video_analytics.tools import AverageSpeedsInput
from vss_agents.video_analytics.tools import EmptyInput
from vss_agents.video_analytics.tools import FovHistogramInput
from vss_agents.video_analytics.tools import GetIncidentInput
from vss_agents.video_analytics.tools import GetIncidentsInputBase
from vss_agents.video_analytics.tools import GetIncidentsInputWithVLM
from vss_agents.video_analytics.tools import GetSensorIdsInput
from vss_agents.video_analytics.tools import VideoAnalyticsToolConfig
from vss_agents.video_analytics.tools import video_analytics

# Access the unwrapped async generator function
_video_analytics_unwrapped = video_analytics.__wrapped__


class MockESClient:
    """Mock ES client that returns controlled test data."""

    def __init__(self, es_url, index_prefix=""):
        self.es_url = es_url
        self.index_prefix = index_prefix
        self._search_results = []
        self._aggregate_results = {}
        self._get_by_id_results = {}

    def set_search_results(self, results):
        self._search_results = results

    def set_aggregate_results(self, results):
        self._aggregate_results = results

    def set_get_by_id_results(self, results):
        self._get_by_id_results = results

    async def get_by_id(self, index_key, doc_id):
        return self._get_by_id_results.get(f"{index_key}:{doc_id}")

    async def search(self, index_key, query_body, size=100, sort=None, source_includes=None, source_excludes=None):
        return self._search_results

    async def aggregate(self, index_key, query_body, aggs):
        return self._aggregate_results

    async def close(self):
        pass


@pytest.fixture
def mock_builder():
    builder = MagicMock()
    builder.get_tool = AsyncMock(return_value=MagicMock())
    return builder


@pytest.fixture
def sample_calibration_data():
    return {
        "calibration": {
            "sensors": [
                {"id": "sensor-001", "place": [{"value": "San Jose"}, {"value": "Main Street"}]},
                {"id": "sensor-002", "place": [{"value": "San Jose"}, {"value": "Oak Avenue"}]},
                {"id": "sensor-003", "place": [{"value": "Mountain View"}, {"value": "Castro Street"}]},
            ]
        }
    }


@pytest.fixture
def sample_incidents():
    return [
        {
            "Id": "incident-001",
            "timestamp": "2025-01-15T10:00:00.000Z",
            "end": "2025-01-15T10:05:00.000Z",
            "sensorId": "sensor-001",
        },
        {
            "Id": "incident-002",
            "timestamp": "2025-01-15T10:10:00.000Z",
            "end": "2025-01-15T10:15:00.000Z",
            "sensorId": "sensor-001",
        },
    ]


async def invoke_function(group, name, input_obj):
    """Helper to get and invoke a function from the group by name."""
    all_funcs = await group.get_all_functions()
    # NAT 1.4.0 uses double underscores as separator in function names
    full_name = f"video_analytics__{name}"
    func_impl = all_funcs.get(full_name)
    if func_impl is None:
        raise ValueError(f"Function {name} not found in {list(all_funcs.keys())}")
    return await func_impl.ainvoke(input_obj)


class TestVideoAnalyticsFunctions:
    """Test the inner functions of video_analytics."""

    @pytest.mark.asyncio
    async def test_get_incident_found(self, mock_builder, sample_calibration_data):
        mock_es = MockESClient("http://localhost:9200")
        mock_es.set_get_by_id_results({"calibration:calibration": sample_calibration_data})
        mock_es.set_search_results(
            [
                {
                    "Id": "incident-123",
                    "timestamp": "2025-01-15T10:00:00.000Z",
                    "end": "2025-01-15T10:05:00.000Z",
                    "sensorId": "sensor-001",
                }
            ]
        )

        config = VideoAnalyticsToolConfig(embedding_model_name=None)

        with patch("vss_agents.video_analytics.tools.ESClient", return_value=mock_es):
            async for group in _video_analytics_unwrapped(config, mock_builder):
                result = await invoke_function(group, "get_incident", GetIncidentInput(id="incident-123"))
                assert result["Id"] == "incident-123"
                break

    @pytest.mark.asyncio
    async def test_get_incident_not_found(self, mock_builder, sample_calibration_data):
        mock_es = MockESClient("http://localhost:9200")
        mock_es.set_get_by_id_results({"calibration:calibration": sample_calibration_data})
        mock_es.set_search_results([])

        config = VideoAnalyticsToolConfig(embedding_model_name=None)

        with patch("vss_agents.video_analytics.tools.ESClient", return_value=mock_es):
            async for group in _video_analytics_unwrapped(config, mock_builder):
                result = await invoke_function(group, "get_incident", GetIncidentInput(id="nonexistent"))
                assert result == {}
                break

    @pytest.mark.asyncio
    async def test_get_incidents_basic(self, mock_builder, sample_calibration_data, sample_incidents):
        mock_es = MockESClient("http://localhost:9200")
        mock_es.set_get_by_id_results({"calibration:calibration": sample_calibration_data})
        mock_es.set_search_results(sample_incidents)

        config = VideoAnalyticsToolConfig(embedding_model_name=None)

        with patch("vss_agents.video_analytics.tools.ESClient", return_value=mock_es):
            async for group in _video_analytics_unwrapped(config, mock_builder):
                result = await invoke_function(group, "get_incidents", GetIncidentsInputBase())
                assert "incidents" in result
                assert len(result["incidents"]) == 2
                break

    @pytest.mark.asyncio
    async def test_get_incidents_with_source(self, mock_builder, sample_calibration_data, sample_incidents):
        mock_es = MockESClient("http://localhost:9200")
        mock_es.set_get_by_id_results({"calibration:calibration": sample_calibration_data})
        mock_es.set_search_results(sample_incidents)

        config = VideoAnalyticsToolConfig(embedding_model_name=None)

        with patch("vss_agents.video_analytics.tools.ESClient", return_value=mock_es):
            async for group in _video_analytics_unwrapped(config, mock_builder):
                result = await invoke_function(
                    group,
                    "get_incidents",
                    GetIncidentsInputBase(
                        source="sensor-001",
                        source_type="sensor",
                        start_time="2025-01-15T00:00:00.000Z",
                        end_time="2025-01-15T23:59:59.000Z",
                    ),
                )
                assert "incidents" in result
                break

    @pytest.mark.asyncio
    async def test_get_incidents_has_more(self, mock_builder, sample_calibration_data):
        many_incidents = [
            {"Id": f"i-{i}", "timestamp": "2025-01-15T10:00:00.000Z", "end": "2025-01-15T10:05:00.000Z"}
            for i in range(11)
        ]
        mock_es = MockESClient("http://localhost:9200")
        mock_es.set_get_by_id_results({"calibration:calibration": sample_calibration_data})
        mock_es.set_search_results(many_incidents)

        config = VideoAnalyticsToolConfig(embedding_model_name=None)

        with patch("vss_agents.video_analytics.tools.ESClient", return_value=mock_es):
            async for group in _video_analytics_unwrapped(config, mock_builder):
                result = await invoke_function(group, "get_incidents", GetIncidentsInputBase(max_count=10))
                assert result["has_more"] is True
                assert len(result["incidents"]) == 10
                break

    @pytest.mark.asyncio
    async def test_get_sensor_ids_all(self, mock_builder, sample_calibration_data):
        mock_es = MockESClient("http://localhost:9200")
        mock_es.set_get_by_id_results({"calibration:calibration": sample_calibration_data})

        config = VideoAnalyticsToolConfig(embedding_model_name=None)

        with patch("vss_agents.video_analytics.tools.ESClient", return_value=mock_es):
            async for group in _video_analytics_unwrapped(config, mock_builder):
                result = await invoke_function(group, "get_sensor_ids", GetSensorIdsInput())
                assert "sensor-001" in result
                assert "sensor-002" in result
                break

    @pytest.mark.asyncio
    async def test_get_sensor_ids_with_place(self, mock_builder, sample_calibration_data):
        mock_es = MockESClient("http://localhost:9200")
        mock_es.set_get_by_id_results({"calibration:calibration": sample_calibration_data})

        config = VideoAnalyticsToolConfig(embedding_model_name=None)

        with patch("vss_agents.video_analytics.tools.ESClient", return_value=mock_es):
            async for group in _video_analytics_unwrapped(config, mock_builder):
                result = await invoke_function(group, "get_sensor_ids", GetSensorIdsInput(place="Main Street"))
                assert result == ["sensor-001"]
                break

    @pytest.mark.asyncio
    async def test_get_places(self, mock_builder, sample_calibration_data):
        mock_es = MockESClient("http://localhost:9200")
        mock_es.set_get_by_id_results({"calibration:calibration": sample_calibration_data})

        config = VideoAnalyticsToolConfig(embedding_model_name=None)

        with patch("vss_agents.video_analytics.tools.ESClient", return_value=mock_es):
            async for group in _video_analytics_unwrapped(config, mock_builder):
                result = await invoke_function(group, "get_places", EmptyInput())
                assert "San Jose" in result
                assert "Mountain View" in result
                break

    @pytest.mark.asyncio
    async def test_get_fov_histogram(self, mock_builder, sample_calibration_data):
        mock_es = MockESClient("http://localhost:9200")
        mock_es.set_get_by_id_results({"calibration:calibration": sample_calibration_data})
        mock_es.set_aggregate_results(
            {
                "eventsOverTime": {
                    "buckets": [
                        {
                            "key": 1736935200000,
                            "key_as_string": "2025-01-15T10:00:00.000Z",
                            "fov": {
                                "searchAggFilter": {
                                    "objectType": {"buckets": [{"key": "Person", "avgCount": {"value": 5.0}}]}
                                }
                            },
                        }
                    ]
                }
            }
        )

        config = VideoAnalyticsToolConfig(embedding_model_name=None)

        with patch("vss_agents.video_analytics.tools.ESClient", return_value=mock_es):
            async for group in _video_analytics_unwrapped(config, mock_builder):
                result = await invoke_function(
                    group,
                    "get_fov_histogram",
                    FovHistogramInput(
                        source="sensor-001",
                        start_time="2025-01-15T10:00:00.000Z",
                        end_time="2025-01-15T11:00:00.000Z",
                        bucket_count=10,
                    ),
                )
                assert "bucketSizeInSec" in result
                assert "histogram" in result
                break

    @pytest.mark.asyncio
    async def test_get_average_speeds(self, mock_builder, sample_calibration_data):
        mock_es = MockESClient("http://localhost:9200")
        mock_es.set_get_by_id_results({"calibration:calibration": sample_calibration_data})
        mock_es.set_aggregate_results({"directions": {"buckets": [{"key": "North", "averageSpeed": {"value": 25.5}}]}})

        config = VideoAnalyticsToolConfig(embedding_model_name=None)

        with patch("vss_agents.video_analytics.tools.ESClient", return_value=mock_es):
            async for group in _video_analytics_unwrapped(config, mock_builder):
                result = await invoke_function(
                    group,
                    "get_average_speeds",
                    AverageSpeedsInput(
                        source="sensor-001",
                        start_time="2025-01-15T10:00:00.000Z",
                        end_time="2025-01-15T11:00:00.000Z",
                        source_type="sensor",
                    ),
                )
                assert "metrics" in result
                assert "25 mph" in result["metrics"][0]["averageSpeed"]
                break

    @pytest.mark.asyncio
    async def test_analyze_max_min_incidents(self, mock_builder, sample_calibration_data):
        mock_es = MockESClient("http://localhost:9200")
        mock_es.set_get_by_id_results({"calibration:calibration": sample_calibration_data})
        mock_es.set_search_results(
            [
                {"timestamp": "2025-01-15T10:00:00.000Z", "end": "2025-01-15T10:10:00.000Z"},
                {"timestamp": "2025-01-15T10:05:00.000Z", "end": "2025-01-15T10:15:00.000Z"},
            ]
        )

        config = VideoAnalyticsToolConfig(embedding_model_name=None)

        with patch("vss_agents.video_analytics.tools.ESClient", return_value=mock_es):
            async for group in _video_analytics_unwrapped(config, mock_builder):
                result = await invoke_function(
                    group,
                    "analyze",
                    AnalyzeInput(
                        start_time="2025-01-15T00:00:00.000Z",
                        end_time="2025-01-15T23:59:59.000Z",
                        source="sensor-001",
                        source_type="sensor",
                        analysis_type="max_min_incidents",
                    ),
                )
                assert "Maximum overlap" in result
                break

    @pytest.mark.asyncio
    async def test_analyze_no_incidents(self, mock_builder, sample_calibration_data):
        mock_es = MockESClient("http://localhost:9200")
        mock_es.set_get_by_id_results({"calibration:calibration": sample_calibration_data})
        mock_es.set_search_results([])

        config = VideoAnalyticsToolConfig(embedding_model_name=None)

        with patch("vss_agents.video_analytics.tools.ESClient", return_value=mock_es):
            async for group in _video_analytics_unwrapped(config, mock_builder):
                result = await invoke_function(
                    group,
                    "analyze",
                    AnalyzeInput(
                        start_time="2025-01-15T00:00:00.000Z",
                        end_time="2025-01-15T23:59:59.000Z",
                        source="sensor-001",
                        source_type="sensor",
                        analysis_type="max_min_incidents",
                    ),
                )
                assert "no incidents" in result.lower()
                break

    @pytest.mark.asyncio
    async def test_analyze_average_speed(self, mock_builder, sample_calibration_data):
        mock_es = MockESClient("http://localhost:9200")
        mock_es.set_get_by_id_results({"calibration:calibration": sample_calibration_data})
        mock_es.set_aggregate_results({"directions": {"buckets": [{"key": "North", "averageSpeed": {"value": 25.0}}]}})

        config = VideoAnalyticsToolConfig(embedding_model_name=None)

        with patch("vss_agents.video_analytics.tools.ESClient", return_value=mock_es):
            async for group in _video_analytics_unwrapped(config, mock_builder):
                result = await invoke_function(
                    group,
                    "analyze",
                    AnalyzeInput(
                        start_time="2025-01-15T00:00:00.000Z",
                        end_time="2025-01-15T23:59:59.000Z",
                        source="sensor-001",
                        source_type="sensor",
                        analysis_type="average_speed",
                    ),
                )
                assert "speed" in result.lower()
                break

    @pytest.mark.asyncio
    async def test_analyze_avg_num_people(self, mock_builder, sample_calibration_data):
        mock_es = MockESClient("http://localhost:9200")
        mock_es.set_get_by_id_results({"calibration:calibration": sample_calibration_data})
        mock_es.set_aggregate_results(
            {
                "eventsOverTime": {
                    "buckets": [
                        {
                            "key": 1736935200000,
                            "key_as_string": "2025-01-15T10:00:00.000Z",
                            "fov": {
                                "searchAggFilter": {
                                    "objectType": {"buckets": [{"key": "Person", "avgCount": {"value": 5.0}}]}
                                }
                            },
                        }
                    ]
                }
            }
        )

        config = VideoAnalyticsToolConfig(embedding_model_name=None)

        with patch("vss_agents.video_analytics.tools.ESClient", return_value=mock_es):
            async for group in _video_analytics_unwrapped(config, mock_builder):
                result = await invoke_function(
                    group,
                    "analyze",
                    AnalyzeInput(
                        start_time="2025-01-15T10:00:00.000Z",
                        end_time="2025-01-15T11:00:00.000Z",
                        source="sensor-001",
                        source_type="sensor",
                        analysis_type="avg_num_people",
                    ),
                )
                assert "people" in result.lower()
                break

    @pytest.mark.asyncio
    async def test_analyze_avg_num_vehicles(self, mock_builder, sample_calibration_data):
        mock_es = MockESClient("http://localhost:9200")
        mock_es.set_get_by_id_results({"calibration:calibration": sample_calibration_data})
        mock_es.set_aggregate_results(
            {
                "eventsOverTime": {
                    "buckets": [
                        {
                            "key": 1736935200000,
                            "key_as_string": "2025-01-15T10:00:00.000Z",
                            "fov": {
                                "searchAggFilter": {
                                    "objectType": {"buckets": [{"key": "Vehicle", "avgCount": {"value": 3.0}}]}
                                }
                            },
                        }
                    ]
                }
            }
        )

        config = VideoAnalyticsToolConfig(embedding_model_name=None)

        with patch("vss_agents.video_analytics.tools.ESClient", return_value=mock_es):
            async for group in _video_analytics_unwrapped(config, mock_builder):
                result = await invoke_function(
                    group,
                    "analyze",
                    AnalyzeInput(
                        start_time="2025-01-15T10:00:00.000Z",
                        end_time="2025-01-15T11:00:00.000Z",
                        source="sensor-001",
                        source_type="sensor",
                        analysis_type="avg_num_vehicles",
                    ),
                )
                assert "vehicle" in result.lower()
                break


class TestVideoAnalyticsWithVLM:
    """Test with VLM verification enabled."""

    @pytest.mark.asyncio
    async def test_get_incidents_vlm_verified(self, mock_builder, sample_calibration_data, sample_incidents):
        mock_es = MockESClient("http://localhost:9200")
        mock_es.set_get_by_id_results({"calibration:calibration": sample_calibration_data})
        mock_es.set_search_results(sample_incidents)

        config = VideoAnalyticsToolConfig(embedding_model_name=None, vlm_verified=True)

        with patch("vss_agents.video_analytics.tools.ESClient", return_value=mock_es):
            async for group in _video_analytics_unwrapped(config, mock_builder):
                result = await invoke_function(
                    group, "get_incidents", GetIncidentsInputWithVLM(vlm_verdict="confirmed")
                )
                assert "incidents" in result
                break


class TestVideoAnalyticsNoCalibration:
    """Test when calibration data is missing."""

    @pytest.mark.asyncio
    async def test_no_calibration_data(self, mock_builder):
        mock_es = MockESClient("http://localhost:9200")
        mock_es.set_get_by_id_results({})

        config = VideoAnalyticsToolConfig(embedding_model_name=None)

        with patch("vss_agents.video_analytics.tools.ESClient", return_value=mock_es):
            async for group in _video_analytics_unwrapped(config, mock_builder):
                result = await invoke_function(group, "get_places", EmptyInput())
                assert result == {}
                break

    @pytest.mark.asyncio
    async def test_calibration_error(self, mock_builder):
        mock_es = MockESClient("http://localhost:9200")

        async def raise_error(*a, **kw):
            raise Exception("ES down")

        mock_es.get_by_id = raise_error

        config = VideoAnalyticsToolConfig(embedding_model_name=None)

        with patch("vss_agents.video_analytics.tools.ESClient", return_value=mock_es):
            async for group in _video_analytics_unwrapped(config, mock_builder):
                assert group is not None
                break


class TestVideoAnalyticsIncludeConfig:
    """Test include configuration."""

    @pytest.mark.asyncio
    async def test_include_only_get_incidents(self, mock_builder, sample_calibration_data):
        mock_es = MockESClient("http://localhost:9200")
        mock_es.set_get_by_id_results({"calibration:calibration": sample_calibration_data})

        config = VideoAnalyticsToolConfig(embedding_model_name=None, include=["get_incidents"])

        with patch("vss_agents.video_analytics.tools.ESClient", return_value=mock_es):
            async for group in _video_analytics_unwrapped(config, mock_builder):
                all_funcs = await group.get_all_functions()
                assert "video_analytics__get_incidents" in all_funcs
                assert "video_analytics__get_incident" not in all_funcs
                break

    @pytest.mark.asyncio
    async def test_include_empty(self, mock_builder, sample_calibration_data):
        mock_es = MockESClient("http://localhost:9200")
        mock_es.set_get_by_id_results({"calibration:calibration": sample_calibration_data})

        config = VideoAnalyticsToolConfig(embedding_model_name=None, include=[])

        with patch("vss_agents.video_analytics.tools.ESClient", return_value=mock_es):
            async for group in _video_analytics_unwrapped(config, mock_builder):
                all_funcs = await group.get_all_functions()
                assert len(all_funcs) == 0
                break
