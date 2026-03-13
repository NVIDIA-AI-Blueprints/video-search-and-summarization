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
"""Unit tests for VST timeline module."""

from pydantic import ValidationError
import pytest
from vss_agents.tools.vst.timeline import VSTTimelineConfig
from vss_agents.tools.vst.timeline import VSTTimelineInput
from vss_agents.tools.vst.timeline import VSTTimelineOutput


class TestVSTTimelineConfig:
    """Test VSTTimelineConfig model."""

    def test_valid_config(self):
        """Test creating config with valid vst_internal_url."""
        config = VSTTimelineConfig(vst_internal_url="http://localhost:30888")
        assert config.vst_internal_url == "http://localhost:30888"

    def test_config_with_trailing_slash(self):
        """Test config with trailing slash in URL."""
        config = VSTTimelineConfig(vst_internal_url="http://localhost:30888/")
        assert config.vst_internal_url == "http://localhost:30888/"

    def test_missing_vst_internal_url_raises(self):
        """Test that missing vst_internal_url raises ValidationError."""
        with pytest.raises(ValidationError):
            VSTTimelineConfig()

    def test_config_description(self):
        """Test that config has proper field description."""
        # Access field info from model_fields
        field_info = VSTTimelineConfig.model_fields["vst_internal_url"]
        assert "internal" in field_info.description.lower()


class TestVSTTimelineInput:
    """Test VSTTimelineInput model."""

    def test_valid_sensor_id(self):
        """Test creating input with valid sensor_id."""
        input_data = VSTTimelineInput(sensor_id="carryingcomputer_1")
        assert input_data.sensor_id == "carryingcomputer_1"

    def test_sensor_id_with_uuid(self):
        """Test creating input with UUID sensor_id."""
        input_data = VSTTimelineInput(sensor_id="24c5a7d6-39ce-442e-abf0-430f036b7a3d")
        assert input_data.sensor_id == "24c5a7d6-39ce-442e-abf0-430f036b7a3d"

    def test_missing_sensor_id_raises(self):
        """Test that missing sensor_id raises ValidationError."""
        with pytest.raises(ValidationError):
            VSTTimelineInput()

    def test_input_description(self):
        """Test that input has proper field description."""
        field_info = VSTTimelineInput.model_fields["sensor_id"]
        assert "sensor" in field_info.description.lower() or "stream ID" in field_info.description


class TestVSTTimelineOutput:
    """Test VSTTimelineOutput model."""

    def test_valid_output(self):
        """Test creating output with valid timestamps."""
        output = VSTTimelineOutput(
            start_timestamp="2025-01-01T00:00:00.000Z",
            end_timestamp="2025-01-01T00:00:12.000Z",
        )
        assert output.start_timestamp == "2025-01-01T00:00:00.000Z"
        assert output.end_timestamp == "2025-01-01T00:00:12.000Z"

    def test_output_with_real_data_timestamps(self):
        """Test output with timestamps from real VST server."""
        output = VSTTimelineOutput(
            start_timestamp="2025-12-18T07:19:59.332Z",
            end_timestamp="2025-12-18T07:20:11.332Z",
        )
        assert output.start_timestamp == "2025-12-18T07:19:59.332Z"
        assert output.end_timestamp == "2025-12-18T07:20:11.332Z"

    def test_missing_start_timestamp_raises(self):
        """Test that missing start_timestamp raises ValidationError."""
        with pytest.raises(ValidationError):
            VSTTimelineOutput(end_timestamp="2025-01-01T00:00:12.000Z")

    def test_missing_end_timestamp_raises(self):
        """Test that missing end_timestamp raises ValidationError."""
        with pytest.raises(ValidationError):
            VSTTimelineOutput(start_timestamp="2025-01-01T00:00:00.000Z")

    def test_output_json_serializable(self):
        """Test that output can be serialized to JSON."""
        output = VSTTimelineOutput(
            start_timestamp="2025-01-01T00:00:00.000Z",
            end_timestamp="2025-01-01T00:00:12.000Z",
        )
        json_str = output.model_dump_json()
        assert "2025-01-01T00:00:00.000Z" in json_str
        assert "2025-01-01T00:00:12.000Z" in json_str

    def test_output_descriptions(self):
        """Test that output fields have proper descriptions."""
        start_field = VSTTimelineOutput.model_fields["start_timestamp"]
        end_field = VSTTimelineOutput.model_fields["end_timestamp"]
        assert "start" in start_field.description.lower()
        assert "end" in end_field.description.lower()
