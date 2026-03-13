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
"""Tests for vss_agents/tools/multi_incident_formatter.py."""

from datetime import datetime
from datetime import timedelta

from vss_agents.tools.multi_incident_formatter import IncidentData
from vss_agents.tools.multi_incident_formatter import MultiIncidentFormatterInput
from vss_agents.tools.multi_incident_formatter import MultiIncidentFormatterOutput
from vss_agents.tools.multi_incident_formatter import _determine_optimal_bin_size
from vss_agents.tools.multi_incident_formatter import _normalize_timestamp


class TestNormalizeTimestamp:
    """Tests for _normalize_timestamp function."""

    def test_normalize_microseconds(self):
        """Test normalizing timestamp with microseconds."""
        result = _normalize_timestamp("2025-11-17T15:16:38.273512Z")
        assert result == "2025-11-17T15:16:38.273Z"

    def test_normalize_already_correct(self):
        """Test timestamp that's already in correct format."""
        result = _normalize_timestamp("2025-11-17T15:16:38.273Z")
        assert result == "2025-11-17T15:16:38.273Z"

    def test_normalize_short_milliseconds(self):
        """Test normalizing timestamp with less than 3 digits."""
        result = _normalize_timestamp("2025-11-17T15:16:38.27Z")
        assert result == "2025-11-17T15:16:38.270Z"

    def test_normalize_no_fractional(self):
        """Test timestamp without fractional seconds."""
        result = _normalize_timestamp("2025-11-17T15:16:38Z")
        # Should return as-is since there's no dot
        assert result == "2025-11-17T15:16:38Z"


class TestIncidentData:
    """Tests for IncidentData model."""

    def test_create_incident_data(self):
        """Test creating IncidentData."""
        data = IncidentData(
            incident_id="inc-001",
            sensor_id="sensor-001",
            start_timestamp="2025-01-15T10:00:00.000Z",
            end_timestamp="2025-01-15T10:05:00.000Z",
            metadata={"category": "traffic"},
        )
        assert data.incident_id == "inc-001"
        assert data.sensor_id == "sensor-001"
        assert data.metadata["category"] == "traffic"

    def test_incident_data_default_metadata(self):
        """Test IncidentData with default metadata."""
        data = IncidentData(
            incident_id="inc-001",
            sensor_id="sensor-001",
            start_timestamp="2025-01-15T10:00:00.000Z",
            end_timestamp="2025-01-15T10:05:00.000Z",
        )
        assert data.metadata == {}


class TestMultiIncidentFormatterInput:
    """Tests for MultiIncidentFormatterInput model."""

    def test_create_input_basic(self):
        """Test creating basic input."""
        inp = MultiIncidentFormatterInput(
            source="sensor-001",
            source_type="sensor",
        )
        assert inp.source == "sensor-001"
        assert inp.source_type == "sensor"
        assert inp.max_result_size == 10000

    def test_create_input_with_times(self):
        """Test creating input with time range."""
        inp = MultiIncidentFormatterInput(
            source="San Jose",
            source_type="place",
            start_time="2025-01-15T10:00:00.000Z",
            end_time="2025-01-15T11:00:00.000Z",
        )
        assert inp.start_time == "2025-01-15T10:00:00.000Z"
        assert inp.end_time == "2025-01-15T11:00:00.000Z"

    def test_create_input_timestamp_normalization(self):
        """Test that timestamps are normalized."""
        inp = MultiIncidentFormatterInput(
            source="sensor-001",
            source_type="sensor",
            start_time="2025-01-15T10:00:00.123456Z",
            end_time="2025-01-15T11:00:00.789012Z",
        )
        assert inp.start_time == "2025-01-15T10:00:00.123Z"
        assert inp.end_time == "2025-01-15T11:00:00.789Z"


class TestMultiIncidentFormatterOutput:
    """Tests for MultiIncidentFormatterOutput model."""

    def test_create_output(self):
        """Test creating output."""
        output = MultiIncidentFormatterOutput(
            formatted_incidents="<incidents>...</incidents>",
            total_incidents=10,
            chart_html="<img src='chart.png' />",
        )
        assert output.total_incidents == 10
        assert output.chart_html is not None

    def test_create_output_no_chart(self):
        """Test creating output without chart."""
        output = MultiIncidentFormatterOutput(
            formatted_incidents="<incidents>...</incidents>",
            total_incidents=5,
        )
        assert output.chart_html is None


class TestDetermineOptimalBinSize:
    """Tests for _determine_optimal_bin_size function."""

    def test_determine_bin_size_empty(self):
        """Test with empty incidents."""
        result = _determine_optimal_bin_size([])
        assert result is None

    def test_determine_bin_size_single_incident(self):
        """Test with single incident."""
        incidents = [
            IncidentData(
                incident_id="inc-001",
                sensor_id="sensor-001",
                start_timestamp="2025-01-15T10:00:00.000Z",
                end_timestamp="2025-01-15T10:05:00.000Z",
            )
        ]
        result = _determine_optimal_bin_size(incidents)
        # With less than 2 timestamps, should return default
        assert result == "10min"

    def test_determine_bin_size_hour_range(self):
        """Test with incidents spanning an hour."""
        base_time = datetime(2025, 1, 15, 10, 0, 0)
        incidents = []
        for i in range(30):  # 30 incidents over 1 hour
            ts = (base_time + timedelta(minutes=i * 2)).isoformat() + "Z"
            incidents.append(
                IncidentData(
                    incident_id=f"inc-{i:03d}",
                    sensor_id="sensor-001",
                    start_timestamp=ts,
                    end_timestamp=ts,
                )
            )

        result = _determine_optimal_bin_size(incidents)
        # Should return a reasonable bin size
        assert result in ["1min", "10min", "1hr", "1day"]

    def test_determine_bin_size_day_range(self):
        """Test with incidents spanning multiple days."""
        base_time = datetime(2025, 1, 1, 10, 0, 0)
        incidents = []
        for i in range(30):  # 30 incidents over 30 days
            ts = (base_time + timedelta(days=i)).isoformat() + "Z"
            incidents.append(
                IncidentData(
                    incident_id=f"inc-{i:03d}",
                    sensor_id="sensor-001",
                    start_timestamp=ts,
                    end_timestamp=ts,
                )
            )

        result = _determine_optimal_bin_size(incidents)
        # Should prefer larger bin sizes for longer ranges
        assert result in ["1hr", "1day"]

    def test_determine_bin_size_invalid_timestamps(self):
        """Test with invalid timestamps."""
        incidents = [
            IncidentData(
                incident_id="inc-001",
                sensor_id="sensor-001",
                start_timestamp="invalid",
                end_timestamp="invalid",
            ),
            IncidentData(
                incident_id="inc-002",
                sensor_id="sensor-001",
                start_timestamp="also-invalid",
                end_timestamp="also-invalid",
            ),
        ]
        result = _determine_optimal_bin_size(incidents)
        # With all invalid timestamps, should return default
        assert result == "10min"
