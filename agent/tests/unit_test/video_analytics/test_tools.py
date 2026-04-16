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
"""Unit tests for video_analytics/tools module."""

from pydantic import ValidationError
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


class TestEmptyInput:
    """Test EmptyInput model."""

    def test_empty_input_creation(self):
        input_data = EmptyInput()
        assert input_data is not None


class TestGetSensorIdsInput:
    """Test GetSensorIdsInput model."""

    def test_no_place_filter(self):
        input_data = GetSensorIdsInput()
        assert input_data.place is None

    def test_with_place_filter(self):
        input_data = GetSensorIdsInput(place="Main Street")
        assert input_data.place == "Main Street"


class TestGetIncidentInput:
    """Test GetIncidentInput model."""

    def test_basic_input(self):
        input_data = GetIncidentInput(id="incident-001")
        assert input_data.id == "incident-001"
        assert input_data.includes is None

    def test_with_includes(self):
        input_data = GetIncidentInput(id="incident-002", includes=["place", "category", "type"])
        assert input_data.includes == ["place", "category", "type"]


class TestGetIncidentsInputBase:
    """Test GetIncidentsInputBase model."""

    def test_defaults(self):
        input_data = GetIncidentsInputBase()
        assert input_data.source is None
        assert input_data.source_type is None
        assert input_data.start_time is None
        assert input_data.end_time is None
        assert input_data.max_count == 10
        assert input_data.includes is None

    def test_with_source_and_type_sensor(self):
        input_data = GetIncidentsInputBase(source="sensor-001", source_type="sensor")
        assert input_data.source == "sensor-001"
        assert input_data.source_type == "sensor"

    def test_with_source_and_type_place(self):
        input_data = GetIncidentsInputBase(source="Main Street", source_type="place")
        assert input_data.source_type == "place"

    def test_invalid_source_type(self):
        with pytest.raises(ValidationError):
            GetIncidentsInputBase(source="test", source_type="invalid")

    def test_source_without_type_fails(self):
        with pytest.raises(ValidationError):
            GetIncidentsInputBase(source="sensor-001")

    def test_type_without_source_fails(self):
        with pytest.raises(ValidationError):
            GetIncidentsInputBase(source_type="sensor")

    def test_with_time_range(self):
        input_data = GetIncidentsInputBase(start_time="2025-01-01T00:00:00.000Z", end_time="2025-01-01T23:59:59.000Z")
        assert input_data.start_time == "2025-01-01T00:00:00.000Z"
        assert input_data.end_time == "2025-01-01T23:59:59.000Z"

    def test_start_time_without_end_time_fails(self):
        with pytest.raises(ValidationError):
            GetIncidentsInputBase(start_time="2025-01-01T00:00:00.000Z")

    def test_end_time_without_start_time_fails(self):
        with pytest.raises(ValidationError):
            GetIncidentsInputBase(end_time="2025-01-01T23:59:59.000Z")

    def test_with_includes(self):
        input_data = GetIncidentsInputBase(includes=["place", "category"])
        assert input_data.includes == ["place", "category"]

    def test_custom_max_count(self):
        input_data = GetIncidentsInputBase(max_count=50)
        assert input_data.max_count == 50


class TestGetIncidentsInputWithVLM:
    """Test GetIncidentsInputWithVLM model."""

    def test_vlm_verdict_all(self):
        input_data = GetIncidentsInputWithVLM(vlm_verdict="all")
        assert input_data.vlm_verdict == "all"

    def test_vlm_verdict_confirmed(self):
        input_data = GetIncidentsInputWithVLM(vlm_verdict="confirmed")
        assert input_data.vlm_verdict == "confirmed"

    def test_vlm_verdict_rejected(self):
        input_data = GetIncidentsInputWithVLM(vlm_verdict="rejected")
        assert input_data.vlm_verdict == "rejected"

    def test_vlm_verdict_verification_failed(self):
        input_data = GetIncidentsInputWithVLM(vlm_verdict="verification-failed")
        assert input_data.vlm_verdict == "verification-failed"

    def test_vlm_verdict_not_confirmed(self):
        input_data = GetIncidentsInputWithVLM(vlm_verdict="not-confirmed")
        assert input_data.vlm_verdict == "not-confirmed"

    def test_vlm_verdict_invalid(self):
        with pytest.raises(ValidationError):
            GetIncidentsInputWithVLM(vlm_verdict="invalid")

    def test_vlm_verdict_none(self):
        input_data = GetIncidentsInputWithVLM()
        assert input_data.vlm_verdict is None


class TestFovHistogramInput:
    """Test FovHistogramInput model."""

    def test_basic_input(self):
        input_data = FovHistogramInput(
            source="sensor-001", start_time="2025-01-01T00:00:00.000Z", end_time="2025-01-01T01:00:00.000Z"
        )
        assert input_data.source == "sensor-001"
        assert input_data.object_type is None
        assert input_data.bucket_count == 10

    def test_with_object_type(self):
        input_data = FovHistogramInput(
            source="sensor-001",
            start_time="2025-01-01T00:00:00.000Z",
            end_time="2025-01-01T01:00:00.000Z",
            object_type="Person",
        )
        assert input_data.object_type == "Person"

    def test_custom_bucket_count(self):
        input_data = FovHistogramInput(
            source="sensor-001",
            start_time="2025-01-01T00:00:00.000Z",
            end_time="2025-01-01T01:00:00.000Z",
            bucket_count=20,
        )
        assert input_data.bucket_count == 20


class TestAverageSpeedsInput:
    """Test AverageSpeedsInput model."""

    def test_sensor_source(self):
        input_data = AverageSpeedsInput(
            source="sensor-001",
            start_time="2025-01-01T00:00:00.000Z",
            end_time="2025-01-01T01:00:00.000Z",
            source_type="sensor",
        )
        assert input_data.source_type == "sensor"

    def test_place_source(self):
        input_data = AverageSpeedsInput(
            source="Main Street",
            start_time="2025-01-01T00:00:00.000Z",
            end_time="2025-01-01T01:00:00.000Z",
            source_type="place",
        )
        assert input_data.source_type == "place"

    def test_invalid_source_type(self):
        with pytest.raises(ValidationError):
            AverageSpeedsInput(
                source="test",
                start_time="2025-01-01T00:00:00.000Z",
                end_time="2025-01-01T01:00:00.000Z",
                source_type="invalid",
            )


class TestAnalyzeInput:
    """Test AnalyzeInput model."""

    def test_max_min_incidents(self):
        input_data = AnalyzeInput(
            start_time="2025-01-01T00:00:00.000Z",
            end_time="2025-01-01T01:00:00.000Z",
            source="sensor-001",
            source_type="sensor",
            analysis_type="max_min_incidents",
        )
        assert input_data.analysis_type == "max_min_incidents"

    def test_average_speed(self):
        input_data = AnalyzeInput(
            start_time="2025-01-01T00:00:00.000Z",
            end_time="2025-01-01T01:00:00.000Z",
            source="sensor-001",
            source_type="sensor",
            analysis_type="average_speed",
        )
        assert input_data.analysis_type == "average_speed"

    def test_avg_num_people(self):
        input_data = AnalyzeInput(
            start_time="2025-01-01T00:00:00.000Z",
            end_time="2025-01-01T01:00:00.000Z",
            source="sensor-001",
            source_type="sensor",
            analysis_type="avg_num_people",
        )
        assert input_data.analysis_type == "avg_num_people"

    def test_avg_num_vehicles(self):
        input_data = AnalyzeInput(
            start_time="2025-01-01T00:00:00.000Z",
            end_time="2025-01-01T01:00:00.000Z",
            source="sensor-001",
            source_type="sensor",
            analysis_type="avg_num_vehicles",
        )
        assert input_data.analysis_type == "avg_num_vehicles"

    def test_invalid_analysis_type(self):
        with pytest.raises(ValidationError):
            AnalyzeInput(
                start_time="2025-01-01T00:00:00.000Z",
                end_time="2025-01-01T01:00:00.000Z",
                source="sensor-001",
                source_type="sensor",
                analysis_type="invalid",
            )

    def test_invalid_source_type(self):
        with pytest.raises(ValidationError):
            AnalyzeInput(
                start_time="2025-01-01T00:00:00.000Z",
                end_time="2025-01-01T01:00:00.000Z",
                source="test",
                source_type="invalid",
                analysis_type="max_min_incidents",
            )


class TestVideoAnalyticsToolConfig:
    """Test VideoAnalyticsToolConfig model."""

    def test_defaults(self):
        config = VideoAnalyticsToolConfig()
        assert config.es_url == "http://localhost:9200"
        assert config.index_prefix == ""
        assert config.vlm_verified is False
        assert config.vst_sensor_list_tool is None
        assert config.embedding_model_name == "sentence-transformers/all-MiniLM-L6-v2"
        assert "get_incidents" in config.include
        assert "get_incident" in config.include

    def test_custom_es_url(self):
        config = VideoAnalyticsToolConfig(es_url="http://custom:9200")
        assert config.es_url == "http://custom:9200"

    def test_with_index_prefix(self):
        config = VideoAnalyticsToolConfig(index_prefix="test-")
        assert config.index_prefix == "test-"

    def test_vlm_verified_enabled(self):
        config = VideoAnalyticsToolConfig(vlm_verified=True)
        assert config.vlm_verified is True

    def test_custom_include_list(self):
        config = VideoAnalyticsToolConfig(include=["get_incidents"])
        assert config.include == ["get_incidents"]

    def test_no_embedding_model(self):
        config = VideoAnalyticsToolConfig(embedding_model_name=None)
        assert config.embedding_model_name is None

    def test_with_vst_sensor_list_tool(self):
        config = VideoAnalyticsToolConfig(vst_sensor_list_tool="vst_sensor_list")
        assert config.vst_sensor_list_tool == "vst_sensor_list"


class TestTimestampValidation:
    """Test timestamp validation in input models."""

    def test_valid_time_formats(self):
        """Test various valid timestamp formats."""
        valid_timestamps = [
            "2025-01-01T00:00:00.000Z",
            "2025-12-31T23:59:59.999Z",
            "2022-06-15T12:30:45.123Z",
        ]
        for ts in valid_timestamps:
            input_data = GetIncidentsInputBase(start_time=ts, end_time=ts)
            assert input_data.start_time == ts

    def test_invalid_timestamp_no_z(self):
        """Test invalid timestamp without Z suffix."""
        with pytest.raises(ValidationError):
            GetIncidentsInputBase(start_time="2025-01-01T00:00:00.000", end_time="2025-01-01T00:00:00.000")

    def test_invalid_timestamp_no_milliseconds(self):
        """Test invalid timestamp without milliseconds."""
        with pytest.raises(ValidationError):
            GetIncidentsInputBase(start_time="2025-01-01T00:00:00Z", end_time="2025-01-01T00:00:00Z")

    def test_fov_histogram_timestamp_validation(self):
        """Test FovHistogramInput timestamp validation."""
        with pytest.raises(ValidationError):
            FovHistogramInput(source="sensor-001", start_time="invalid", end_time="2025-01-01T00:00:00.000Z")

    def test_average_speeds_timestamp_validation(self):
        """Test AverageSpeedsInput timestamp validation."""
        with pytest.raises(ValidationError):
            AverageSpeedsInput(
                source="sensor-001", start_time="2025-01-01T00:00:00.000Z", end_time="invalid", source_type="sensor"
            )


class TestAnalyzeInputValidation:
    """Additional tests for AnalyzeInput model."""

    def test_all_analysis_types(self):
        """Test all valid analysis types."""
        valid_types = ["max_min_incidents", "average_speed", "avg_num_people", "avg_num_vehicles"]
        for analysis_type in valid_types:
            input_data = AnalyzeInput(
                start_time="2025-01-01T00:00:00.000Z",
                end_time="2025-01-01T01:00:00.000Z",
                source="sensor-001",
                source_type="sensor",
                analysis_type=analysis_type,
            )
            assert input_data.analysis_type == analysis_type

    def test_place_source_type(self):
        """Test place source type for AnalyzeInput."""
        input_data = AnalyzeInput(
            start_time="2025-01-01T00:00:00.000Z",
            end_time="2025-01-01T01:00:00.000Z",
            source="San Jose",
            source_type="place",
            analysis_type="max_min_incidents",
        )
        assert input_data.source_type == "place"


class TestGetIncidentsInputValidation:
    """Additional tests for GetIncidentsInput models."""

    def test_full_input_with_all_fields(self):
        """Test input with all optional fields populated."""
        input_data = GetIncidentsInputBase(
            source="sensor-001",
            source_type="sensor",
            start_time="2025-01-01T00:00:00.000Z",
            end_time="2025-01-01T23:59:59.000Z",
            max_count=50,
            includes=["place", "category", "type", "sensorId"],
        )
        assert input_data.max_count == 50
        assert len(input_data.includes) == 4

    def test_vlm_input_with_time_and_source(self):
        """Test VLM input with all filters."""
        input_data = GetIncidentsInputWithVLM(
            source="Main Street",
            source_type="place",
            start_time="2025-01-01T00:00:00.000Z",
            end_time="2025-01-01T23:59:59.000Z",
            vlm_verdict="confirmed",
            max_count=100,
        )
        assert input_data.vlm_verdict == "confirmed"
        assert input_data.source_type == "place"


class TestInputModelSerialization:
    """Test serialization/deserialization of input models."""

    def test_empty_input_serialization(self):
        """Test EmptyInput model dump."""
        input_data = EmptyInput()
        data = input_data.model_dump()
        assert data == {}

    def test_get_sensor_ids_input_serialization(self):
        """Test GetSensorIdsInput serialization."""
        input_data = GetSensorIdsInput(place="Test Place")
        data = input_data.model_dump()
        assert data["place"] == "Test Place"

    def test_get_incident_input_serialization(self):
        """Test GetIncidentInput serialization."""
        input_data = GetIncidentInput(id="incident-123", includes=["place"])
        data = input_data.model_dump()
        assert data["id"] == "incident-123"
        assert data["includes"] == ["place"]

    def test_fov_histogram_input_serialization(self):
        """Test FovHistogramInput serialization."""
        input_data = FovHistogramInput(
            source="sensor-001",
            start_time="2025-01-01T00:00:00.000Z",
            end_time="2025-01-01T01:00:00.000Z",
            object_type="Person",
            bucket_count=20,
        )
        data = input_data.model_dump()
        assert data["object_type"] == "Person"
        assert data["bucket_count"] == 20

    def test_analyze_input_serialization(self):
        """Test AnalyzeInput serialization."""
        input_data = AnalyzeInput(
            start_time="2025-01-01T00:00:00.000Z",
            end_time="2025-01-01T01:00:00.000Z",
            source="sensor-001",
            source_type="sensor",
            analysis_type="average_speed",
        )
        data = input_data.model_dump()
        assert "start_time" in data
        assert "analysis_type" in data
