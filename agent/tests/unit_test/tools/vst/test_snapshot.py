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
"""Unit tests for VST snapshot module."""

from pydantic import ValidationError
import pytest

from vss_agents.tools.vst.snapshot import VSTSnapshotConfig
from vss_agents.tools.vst.snapshot import VSTSnapshotISOInput
from vss_agents.tools.vst.snapshot import VSTSnapshotOffsetInput
from vss_agents.tools.vst.snapshot import VSTSnapshotOutput


class TestVSTSnapshotConfig:
    """Test VSTSnapshotConfig model."""

    def test_valid_config(self):
        """Test creating config with valid URLs."""
        config = VSTSnapshotConfig(vst_internal_url="http://localhost:30888", vst_external_url="http://localhost:30888")
        assert config.vst_internal_url == "http://localhost:30888"
        assert config.vst_external_url == "http://localhost:30888"
        assert config.overlay_config is False
        assert config.time_format == "offset"

    def test_config_with_overlay(self):
        """Test config with overlay enabled."""
        config = VSTSnapshotConfig(
            vst_internal_url="http://localhost:30888",
            vst_external_url="http://localhost:30888",
            overlay_config=True,
        )
        assert config.overlay_config is True

    def test_config_with_time_format_iso(self):
        """Test config with ISO timestamp format."""
        config = VSTSnapshotConfig(
            vst_internal_url="http://localhost:30888",
            vst_external_url="http://localhost:30888",
            time_format="iso",
        )
        assert config.time_format == "iso"

    def test_config_with_time_format_offset(self):
        """Test config with offset timestamp format (default)."""
        config = VSTSnapshotConfig(
            vst_internal_url="http://localhost:30888",
            vst_external_url="http://localhost:30888",
            time_format="offset",
        )
        assert config.time_format == "offset"

    def test_config_with_host_ip_placeholder(self):
        """Test config with HOST_IP placeholder."""
        config = VSTSnapshotConfig(
            vst_internal_url="http://${HOST_IP}:30888", vst_external_url="http://${HOST_IP}:30888"
        )
        assert config.vst_internal_url == "http://${HOST_IP}:30888"
        assert config.vst_external_url == "http://${HOST_IP}:30888"

    def test_config_with_trailing_slash(self):
        """Test config with trailing slash in URL."""
        config = VSTSnapshotConfig(
            vst_internal_url="http://localhost:30888/", vst_external_url="http://localhost:30888/"
        )
        assert config.vst_internal_url == "http://localhost:30888/"
        assert config.vst_external_url == "http://localhost:30888/"

    def test_missing_vst_urls_raises(self):
        """Test that missing URLs raises ValidationError."""
        with pytest.raises(ValidationError):
            VSTSnapshotConfig()

    def test_config_description(self):
        """Test that config has proper field description."""
        field_info = VSTSnapshotConfig.model_fields["vst_internal_url"]
        assert "internal" in field_info.description.lower()


class TestVSTSnapshotOffsetInput:
    """Test VSTSnapshotOffsetInput model."""

    def test_valid_input_with_seconds(self):
        """Test creating input with valid sensor_id and start_time in seconds."""
        input_data = VSTSnapshotOffsetInput(sensor_id="carryingcomputer_1", start_time=5.0)
        assert input_data.sensor_id == "carryingcomputer_1"
        assert input_data.start_time == 5.0

    def test_input_with_zero_start_time(self):
        """Test input with zero start_time."""
        input_data = VSTSnapshotOffsetInput(sensor_id="test_video", start_time=0.0)
        assert input_data.start_time == 0.0

    def test_input_with_large_start_time(self):
        """Test input with large start_time value."""
        input_data = VSTSnapshotOffsetInput(sensor_id="test_video", start_time=3600.5)
        assert input_data.start_time == 3600.5

    def test_missing_sensor_id_raises(self):
        """Test that missing sensor_id raises ValidationError."""
        with pytest.raises(ValidationError):
            VSTSnapshotOffsetInput(start_time=5.0)

    def test_empty_sensor_id_raises(self):
        """Test that empty sensor_id raises ValidationError."""
        with pytest.raises(ValidationError):
            VSTSnapshotOffsetInput(sensor_id="", start_time=5.0)

    def test_missing_start_time_raises(self):
        """Test that missing start_time raises ValidationError."""
        with pytest.raises(ValidationError):
            VSTSnapshotOffsetInput(sensor_id="test_video")

    def test_input_descriptions(self):
        """Test that input fields have proper descriptions."""
        sensor_field = VSTSnapshotOffsetInput.model_fields["sensor_id"]
        start_time_field = VSTSnapshotOffsetInput.model_fields["start_time"]
        assert "video" in sensor_field.description.lower() or "name" in sensor_field.description.lower()
        assert "seconds" in start_time_field.description.lower()


class TestVSTSnapshotISOInput:
    """Test VSTSnapshotISOInput model."""

    def test_valid_input_with_iso_timestamp(self):
        """Test creating input with ISO 8601 timestamp."""
        input_data = VSTSnapshotISOInput(sensor_id="camera-001", start_time="2025-08-25T03:05:55.752Z")
        assert input_data.sensor_id == "camera-001"
        assert input_data.start_time == "2025-08-25T03:05:55.752Z"

    def test_missing_sensor_id_raises(self):
        """Test that missing sensor_id raises ValidationError."""
        with pytest.raises(ValidationError):
            VSTSnapshotISOInput(start_time="2025-08-25T03:05:55.752Z")

    def test_empty_sensor_id_raises(self):
        """Test that empty sensor_id raises ValidationError."""
        with pytest.raises(ValidationError):
            VSTSnapshotISOInput(sensor_id="", start_time="2025-08-25T03:05:55.752Z")

    def test_missing_start_time_raises(self):
        """Test that missing start_time raises ValidationError."""
        with pytest.raises(ValidationError):
            VSTSnapshotISOInput(sensor_id="test_video")

    def test_empty_start_time_raises(self):
        """Test that empty start_time raises ValidationError."""
        with pytest.raises(ValidationError):
            VSTSnapshotISOInput(sensor_id="test_video", start_time="")

    def test_input_descriptions(self):
        """Test that input fields have proper descriptions."""
        sensor_field = VSTSnapshotISOInput.model_fields["sensor_id"]
        start_time_field = VSTSnapshotISOInput.model_fields["start_time"]
        assert "video" in sensor_field.description.lower() or "name" in sensor_field.description.lower()
        assert "iso" in start_time_field.description.lower() or "8601" in start_time_field.description.lower()


class TestVSTSnapshotOutput:
    """Test VSTSnapshotOutput model."""

    def test_valid_output(self):
        """Test creating output with valid image_url and stream_id."""
        output = VSTSnapshotOutput(
            image_url="http://localhost:30888/snapshot/image.jpg",
            stream_id="24c5a7d6-39ce-442e-abf0-430f036b7a3d",
        )
        assert output.image_url == "http://localhost:30888/snapshot/image.jpg"
        assert output.stream_id == "24c5a7d6-39ce-442e-abf0-430f036b7a3d"

    def test_output_with_real_url_format(self):
        """Test output with URL format from real VST server."""
        output = VSTSnapshotOutput(
            image_url="http://10.0.0.1:30888/vst/api/v1/replay/stream/24c5a7d6-39ce-442e-abf0-430f036b7a3d/picture?startTime=2025-01-01T00:00:05.000Z",
            stream_id="24c5a7d6-39ce-442e-abf0-430f036b7a3d",
        )
        assert "24c5a7d6-39ce-442e-abf0-430f036b7a3d" in output.image_url

    def test_missing_image_url_raises(self):
        """Test that missing image_url raises ValidationError."""
        with pytest.raises(ValidationError):
            VSTSnapshotOutput(stream_id="stream-uuid")

    def test_missing_stream_id_raises(self):
        """Test that missing stream_id raises ValidationError."""
        with pytest.raises(ValidationError):
            VSTSnapshotOutput(image_url="http://example.com/snapshot.jpg")

    def test_output_json_serializable(self):
        """Test that output can be serialized to JSON."""
        output = VSTSnapshotOutput(
            image_url="http://example.com/snapshot.jpg",
            stream_id="test-stream-id",
        )
        json_str = output.model_dump_json()
        assert "http://example.com/snapshot.jpg" in json_str
        assert "test-stream-id" in json_str

    def test_output_description(self):
        """Test that output field has proper description."""
        field_info = VSTSnapshotOutput.model_fields["image_url"]
        assert "URL" in field_info.description or "image" in field_info.description.lower()
