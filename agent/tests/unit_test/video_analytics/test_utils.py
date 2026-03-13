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
"""Tests for vss_agents/video_analytics/utils.py."""

from datetime import UTC
from datetime import datetime

import pytest
from vss_agents.video_analytics.utils import build_place_map
from vss_agents.video_analytics.utils import build_sensor_map
from vss_agents.video_analytics.utils import compute_bucket_size_seconds
from vss_agents.video_analytics.utils import create_empty_histogram_buckets
from vss_agents.video_analytics.utils import create_events_from_incidents
from vss_agents.video_analytics.utils import parse_vst_sensor_list_response
from vss_agents.video_analytics.utils import sweep_overlapping_incidents
from vss_agents.video_analytics.utils import validate_iso_timestamp


class TestValidateIsoTimestamp:
    """Tests for validate_iso_timestamp function."""

    def test_valid_timestamp(self):
        """Test valid ISO timestamp."""
        timestamp = "2022-08-25T00:00:10.000Z"
        result = validate_iso_timestamp(timestamp)
        assert result == timestamp

    def test_invalid_format_no_milliseconds(self):
        """Test invalid format without milliseconds."""
        with pytest.raises(ValueError, match="Invalid timestamp format"):
            validate_iso_timestamp("2022-08-25T00:00:10Z")

    def test_invalid_format_no_z(self):
        """Test invalid format without Z."""
        with pytest.raises(ValueError, match="Invalid timestamp format"):
            validate_iso_timestamp("2022-08-25T00:00:10.000")

    def test_invalid_date_values(self):
        """Test invalid date values."""
        with pytest.raises(ValueError):
            validate_iso_timestamp("2022-13-25T00:00:10.000Z")  # Invalid month


class TestBuildSensorMap:
    """Tests for build_sensor_map function."""

    def test_build_sensor_map_basic(self, sample_sensors):
        """Test building sensor map with basic data."""
        result = build_sensor_map(sample_sensors)
        assert "San Jose" in result
        assert "Intersection_A" in result["San Jose"]
        assert "sensor-001" in result["San Jose"]["Intersection_A"]

    def test_build_sensor_map_multiple_cities(self, sample_sensors):
        """Test building sensor map with multiple cities."""
        result = build_sensor_map(sample_sensors)
        assert "San Jose" in result
        assert "Mountain View" in result

    def test_build_sensor_map_missing_place(self):
        """Test building sensor map with missing place field."""
        sensors = [{"id": "sensor-001"}]  # No place field
        result = build_sensor_map(sensors)
        assert result == {}

    def test_build_sensor_map_malformed_place(self):
        """Test building sensor map with malformed place field."""
        sensors = [{"id": "sensor-001", "place": []}]  # Empty place
        result = build_sensor_map(sensors)
        assert result == {}

    def test_build_sensor_map_missing_id(self):
        """Test building sensor map with missing id."""
        sensors = [{"place": [{"value": "City"}, {"value": "Intersection"}]}]
        result = build_sensor_map(sensors)
        # The function creates the structure but with empty sensor list when id is missing
        assert "City" in result
        assert "Intersection" in result["City"]
        assert result["City"]["Intersection"] == []  # Empty because no id

    def test_build_sensor_map_missing_city_value(self):
        """Test building sensor map with missing city value (covers line 84)."""
        sensors = [{"id": "sensor-001", "place": [{"value": None}, {"value": "Intersection"}]}]
        result = build_sensor_map(sensors)
        assert result == {}

    def test_build_sensor_map_missing_intersection_value(self):
        """Test building sensor map with missing intersection value (covers line 85)."""
        sensors = [{"id": "sensor-001", "place": [{}, {"value": "Intersection"}]}]
        result = build_sensor_map(sensors)
        assert result == {}


class TestBuildPlaceMap:
    """Tests for build_place_map function."""

    def test_build_place_map_basic(self, sample_sensors):
        """Test building place map."""
        result = build_place_map(sample_sensors)
        assert "San Jose" in result
        assert "Intersection_A" in result["San Jose"]
        assert "Intersection_B" in result["San Jose"]

    def test_build_place_map_sorted(self, sample_sensors):
        """Test that place map intersections are sorted."""
        result = build_place_map(sample_sensors)
        # Intersections should be sorted alphabetically
        san_jose_intersections = result["San Jose"]
        assert san_jose_intersections == sorted(san_jose_intersections)

    def test_build_place_map_no_duplicates(self):
        """Test that place map has no duplicate intersections."""
        sensors = [
            {"id": "s1", "place": [{"value": "City"}, {"value": "Int1"}]},
            {"id": "s2", "place": [{"value": "City"}, {"value": "Int1"}]},  # Same intersection
        ]
        result = build_place_map(sensors)
        assert len(result["City"]) == 1

    def test_build_place_map_missing_place(self):
        """Test building place map with missing place field (covers line 127)."""
        sensors = [{"id": "sensor-001"}]  # No place field
        result = build_place_map(sensors)
        assert result == {}

    def test_build_place_map_malformed_place(self):
        """Test building place map with malformed place (covers line 128)."""
        sensors = [{"id": "sensor-001", "place": []}]  # Empty place
        result = build_place_map(sensors)
        assert result == {}

    def test_build_place_map_missing_city_value(self):
        """Test building place map with missing city value (covers line 134)."""
        sensors = [{"id": "sensor-001", "place": [{"value": None}, {"value": "Intersection"}]}]
        result = build_place_map(sensors)
        assert result == {}

    def test_build_place_map_missing_intersection_value(self):
        """Test building place map with missing intersection value (covers line 135)."""
        sensors = [{"id": "sensor-001", "place": [{}, {"value": "Intersection"}]}]
        result = build_place_map(sensors)
        assert result == {}


class TestParseVstSensorListResponse:
    """Tests for parse_vst_sensor_list_response function."""

    def test_parse_empty_response(self):
        """Test parsing empty response."""
        result = parse_vst_sensor_list_response("")
        assert result == set()

    def test_parse_dict_response(self):
        """Test parsing dictionary response."""
        response = '{"sensor1": {"name": "Camera1"}, "sensor2": {"name": "Camera2"}}'
        result = parse_vst_sensor_list_response(response)
        assert "Camera1" in result
        assert "Camera2" in result

    def test_parse_quoted_response(self):
        """Test parsing quoted response."""
        response = '"{\\"sensor1\\": {\\"name\\": \\"Camera1\\"}}"'
        # This tests handling of wrapper quotes
        parse_vst_sensor_list_response(response)
        # May return empty if parsing fails

    def test_parse_invalid_json(self):
        """Test parsing invalid JSON."""
        result = parse_vst_sensor_list_response("not valid json")
        assert result == set()


class TestComputeBucketSizeSeconds:
    """Tests for compute_bucket_size_seconds function."""

    def test_compute_bucket_size_hour(self):
        """Test computing bucket size for 1 hour range."""
        start = "2022-08-25T00:00:00.000Z"
        end = "2022-08-25T01:00:00.000Z"
        result = compute_bucket_size_seconds(start, end, bucket_count=10)
        assert result == 360  # 3600 / 10

    def test_compute_bucket_size_minimum(self):
        """Test that bucket size is at least 1 second."""
        start = "2022-08-25T00:00:00.000Z"
        end = "2022-08-25T00:00:01.000Z"
        result = compute_bucket_size_seconds(start, end, bucket_count=100)
        assert result >= 1

    def test_compute_bucket_size_invalid_count(self):
        """Test with invalid bucket count."""
        start = "2022-08-25T00:00:00.000Z"
        end = "2022-08-25T01:00:00.000Z"
        with pytest.raises(ValueError):
            compute_bucket_size_seconds(start, end, bucket_count=0)


class TestCreateEmptyHistogramBuckets:
    """Tests for create_empty_histogram_buckets function."""

    def test_create_buckets(self):
        """Test creating histogram buckets."""
        start = "2022-08-25T00:00:00.000Z"
        end = "2022-08-25T00:01:00.000Z"
        result = create_empty_histogram_buckets(start, end, bucket_size_sec=30)
        assert len(result) >= 1
        assert "start" in result[0]
        assert "end" in result[0]
        assert "objects" in result[0]

    def test_create_buckets_invalid_size(self):
        """Test with invalid bucket size."""
        start = "2022-08-25T00:00:00.000Z"
        end = "2022-08-25T01:00:00.000Z"
        with pytest.raises(ValueError):
            create_empty_histogram_buckets(start, end, bucket_size_sec=0)

    def test_create_buckets_truncated_last_bucket(self):
        """Test that last bucket is truncated to end time (covers line 243)."""
        start = "2022-08-25T00:00:00.000Z"
        # End time doesn't align with bucket size
        end = "2022-08-25T00:00:45.000Z"  # 45 seconds, bucket size 30
        result = create_empty_histogram_buckets(start, end, bucket_size_sec=30)
        # Should have 2 buckets: 0-30s and 30-45s (truncated)
        assert len(result) == 2
        # Last bucket should end at exactly the end time
        assert result[-1]["end"] == end


class TestCreateEventsFromIncidents:
    """Tests for create_events_from_incidents function."""

    def test_create_events_basic(self, sample_incidents):
        """Test creating events from incidents."""
        events, count = create_events_from_incidents(sample_incidents)
        assert count == 2
        assert len(events) == 4  # 2 start + 2 end events

    def test_create_events_empty(self):
        """Test creating events from empty list."""
        events, count = create_events_from_incidents([])
        assert count == 0
        assert len(events) == 0

    def test_create_events_missing_timestamps(self):
        """Test creating events with missing timestamps."""
        incidents = [{"Id": "1"}]  # No timestamps
        _events, count = create_events_from_incidents(incidents)
        assert count == 0


class TestSweepOverlappingIncidents:
    """Tests for sweep_overlapping_incidents function."""

    def test_sweep_no_overlap(self):
        """Test sweep with non-overlapping events."""
        events = [
            (datetime(2022, 1, 1, 10, 0, tzinfo=UTC), 1),
            (datetime(2022, 1, 1, 10, 5, tzinfo=UTC), -1),
            (datetime(2022, 1, 1, 11, 0, tzinfo=UTC), 1),
            (datetime(2022, 1, 1, 11, 5, tzinfo=UTC), -1),
        ]
        max_count, _max_time, _min_count, _min_time = sweep_overlapping_incidents(events)
        assert max_count == 1

    def test_sweep_with_overlap(self):
        """Test sweep with overlapping events."""
        events = [
            (datetime(2022, 1, 1, 10, 0, tzinfo=UTC), 1),
            (datetime(2022, 1, 1, 10, 2, tzinfo=UTC), 1),
            (datetime(2022, 1, 1, 10, 5, tzinfo=UTC), -1),
            (datetime(2022, 1, 1, 10, 7, tzinfo=UTC), -1),
        ]
        max_count, _max_time, _min_count, _min_time = sweep_overlapping_incidents(events)
        assert max_count == 2

    def test_sweep_empty_events(self):
        """Test sweep with empty events."""
        max_count, max_time, _min_count, _min_time = sweep_overlapping_incidents([])
        assert max_count == 0
        assert max_time is None
