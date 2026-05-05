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
"""Unit tests for VST video_clip module."""

from pydantic import ValidationError
import pytest

from vss_agents.tools.vst.video_clip import VSTVideoClipConfig
from vss_agents.tools.vst.video_clip import VSTVideoClipISOInput
from vss_agents.tools.vst.video_clip import VSTVideoClipOffsetInput
from vss_agents.tools.vst.video_clip import VSTVideoClipOutput


class TestVSTVideoClipConfig:
    """Test VSTVideoClipConfig model."""

    def test_valid_config(self):
        """Test creating config with valid URLs."""
        config = VSTVideoClipConfig(vst_internal_url="http://10.0.0.1:30888", vst_external_url="http://localhost:30888")
        assert config.vst_internal_url == "http://10.0.0.1:30888"
        assert config.vst_external_url == "http://localhost:30888"
        assert config.overlay_config is False
        assert config.time_format == "offset"
        assert config.disable_audio is True

    def test_config_with_overlay(self):
        """Test config with overlay enabled."""
        config = VSTVideoClipConfig(
            vst_internal_url="http://10.0.0.1:30888",
            vst_external_url="http://localhost:30888",
            overlay_config=True,
        )
        assert config.overlay_config is True

    def test_config_with_time_format_iso(self):
        """Test config with ISO timestamp format."""
        config = VSTVideoClipConfig(
            vst_internal_url="http://10.0.0.1:30888",
            vst_external_url="http://localhost:30888",
            time_format="iso",
        )
        assert config.time_format == "iso"

    def test_config_with_time_format_offset(self):
        """Test config with offset timestamp format (default)."""
        config = VSTVideoClipConfig(
            vst_internal_url="http://10.0.0.1:30888",
            vst_external_url="http://localhost:30888",
            time_format="offset",
        )
        assert config.time_format == "offset"

    def test_config_with_host_ip_placeholder(self):
        """Test config with HOST_IP placeholder."""
        config = VSTVideoClipConfig(
            vst_internal_url="http://${INTERNAL_IP}:30888", vst_external_url="http://${HOST_IP}:30888"
        )
        assert config.vst_internal_url == "http://${INTERNAL_IP}:30888"
        assert config.vst_external_url == "http://${HOST_IP}:30888"

    def test_config_with_trailing_slash(self):
        """Test config with trailing slash in URL."""
        config = VSTVideoClipConfig(
            vst_internal_url="http://10.0.0.1:30888/", vst_external_url="http://localhost:30888/"
        )
        assert config.vst_internal_url == "http://10.0.0.1:30888/"
        assert config.vst_external_url == "http://localhost:30888/"

    def test_missing_vst_urls_raises(self):
        """Test that missing URLs raises ValidationError."""
        with pytest.raises(ValidationError):
            VSTVideoClipConfig()  # type: ignore

    def test_config_description(self):
        """Test that config has proper field description."""
        field_info = VSTVideoClipConfig.model_fields["vst_internal_url"]
        assert "internal" in field_info.description.lower()  # type: ignore


class TestVSTVideoClipOffsetInput:
    """Test VSTVideoClipOffsetInput model including model_validator."""

    def test_valid_input_with_times(self):
        """Test creating input with valid sensor_id and time range."""
        input_data = VSTVideoClipOffsetInput(sensor_id="carryingcomputer_1", start_time=0.0, end_time=10.0)
        assert input_data.sensor_id == "carryingcomputer_1"
        assert input_data.start_time == 0.0
        assert input_data.end_time == 10.0

    def test_valid_input_without_times(self):
        """Test creating input with only sensor_id (optional times)."""
        input_data = VSTVideoClipOffsetInput(sensor_id="carryingcomputer_1")
        assert input_data.sensor_id == "carryingcomputer_1"
        assert input_data.start_time is None
        assert input_data.end_time is None

    def test_valid_input_with_object_ids(self):
        """Test creating input with object_ids."""
        input_data = VSTVideoClipOffsetInput(
            sensor_id="camera-001",
            start_time=0.0,
            end_time=20.0,
            object_ids=["obj-1", "obj-2"],
        )
        assert input_data.object_ids == ["obj-1", "obj-2"]

    def test_input_object_ids_default_none(self):
        """Test that object_ids defaults to None."""
        input_data = VSTVideoClipOffsetInput(sensor_id="camera-001")
        assert input_data.object_ids is None

    def test_input_with_uuid_sensor_id(self):
        """Test input with UUID-style sensor_id."""
        input_data = VSTVideoClipOffsetInput(sensor_id="24c5a7d6-39ce-442e-abf0-430f036b7a3d")
        assert input_data.sensor_id == "24c5a7d6-39ce-442e-abf0-430f036b7a3d"

    def test_input_with_only_start_time(self):
        """Test input with only start_time (end_time is None)."""
        input_data = VSTVideoClipOffsetInput(sensor_id="test_video", start_time=5.0)
        assert input_data.start_time == 5.0
        assert input_data.end_time is None

    def test_input_with_only_end_time(self):
        """Test input with only end_time (start_time is None)."""
        input_data = VSTVideoClipOffsetInput(sensor_id="test_video", end_time=10.0)
        assert input_data.start_time is None
        assert input_data.end_time == 10.0

    def test_missing_sensor_id_raises(self):
        """Test that missing sensor_id raises ValidationError."""
        with pytest.raises(ValidationError):
            VSTVideoClipOffsetInput(start_time=0.0, end_time=10.0)  # type: ignore

    def test_empty_sensor_id_raises(self):
        """Test that empty sensor_id raises ValidationError."""
        with pytest.raises(ValidationError):
            VSTVideoClipOffsetInput(sensor_id="", start_time=0.0, end_time=10.0)

    def test_negative_start_time_raises(self):
        """Test that negative start_time raises ValidationError."""
        with pytest.raises(ValidationError):
            VSTVideoClipOffsetInput(sensor_id="test_video", start_time=-1.0, end_time=10.0)

    def test_negative_end_time_raises(self):
        """Test that negative end_time raises ValidationError."""
        with pytest.raises(ValidationError):
            VSTVideoClipOffsetInput(sensor_id="test_video", start_time=0.0, end_time=-5.0)

    def test_start_time_equals_end_time_raises(self):
        """Test that start_time equal to end_time raises ValidationError."""
        with pytest.raises(ValidationError):
            VSTVideoClipOffsetInput(sensor_id="test_video", start_time=5.0, end_time=5.0)

    def test_start_time_greater_than_end_time_raises(self):
        """Test that start_time greater than end_time raises ValidationError."""
        with pytest.raises(ValidationError):
            VSTVideoClipOffsetInput(sensor_id="test_video", start_time=10.0, end_time=5.0)

    def test_validator_with_integer_times(self):
        """Test that model_validator handles integer times."""
        input_data = VSTVideoClipOffsetInput(sensor_id="test_video", start_time=0, end_time=10)
        assert isinstance(input_data.start_time, float)
        assert isinstance(input_data.end_time, float)

    def test_input_descriptions(self):
        """Test that input fields have proper descriptions."""
        sensor_field = VSTVideoClipOffsetInput.model_fields["sensor_id"]
        start_field = VSTVideoClipOffsetInput.model_fields["start_time"]
        end_field = VSTVideoClipOffsetInput.model_fields["end_time"]
        assert sensor_field.description is not None
        assert start_field.description is not None
        assert end_field.description is not None
        assert "name" in sensor_field.description.lower() or "stream" in sensor_field.description.lower()
        assert "time" in start_field.description.lower()
        assert "time" in end_field.description.lower()


class TestVSTVideoClipISOInput:
    """Test VSTVideoClipISOInput model."""

    def test_valid_input_with_iso_timestamps(self):
        """Test creating input with ISO 8601 timestamps."""
        input_data = VSTVideoClipISOInput(
            sensor_id="camera-001",
            start_time="2025-08-25T03:05:55.752Z",
            end_time="2025-08-25T03:06:15.752Z",
        )
        assert input_data.sensor_id == "camera-001"
        assert input_data.start_time == "2025-08-25T03:05:55.752Z"
        assert input_data.end_time == "2025-08-25T03:06:15.752Z"

    def test_valid_input_without_times(self):
        """Test creating input with only sensor_id."""
        input_data = VSTVideoClipISOInput(sensor_id="camera-001")
        assert input_data.start_time is None
        assert input_data.end_time is None

    def test_valid_input_with_object_ids(self):
        """Test creating input with object_ids."""
        input_data = VSTVideoClipISOInput(
            sensor_id="camera-001",
            start_time="2025-08-25T03:05:55.752Z",
            end_time="2025-08-25T03:06:15.752Z",
            object_ids=["obj-1", "obj-2"],
        )
        assert input_data.object_ids == ["obj-1", "obj-2"]

    def test_missing_sensor_id_raises(self):
        """Test that missing sensor_id raises ValidationError."""
        with pytest.raises(ValidationError):
            VSTVideoClipISOInput(start_time="2025-08-25T03:05:55.752Z", end_time="2025-08-25T03:06:15.752Z")  # type: ignore

    def test_empty_sensor_id_raises(self):
        """Test that empty sensor_id raises ValidationError."""
        with pytest.raises(ValidationError):
            VSTVideoClipISOInput(sensor_id="", start_time="2025-08-25T03:05:55.752Z")

    def test_input_descriptions(self):
        """Test that input fields have proper descriptions."""
        sensor_field = VSTVideoClipISOInput.model_fields["sensor_id"]
        start_field = VSTVideoClipISOInput.model_fields["start_time"]
        end_field = VSTVideoClipISOInput.model_fields["end_time"]
        assert sensor_field.description is not None
        assert start_field.description is not None
        assert end_field.description is not None
        assert "name" in sensor_field.description.lower() or "stream" in sensor_field.description.lower()  # type: ignore
        assert "iso" in start_field.description.lower() or "8601" in start_field.description
        assert "iso" in end_field.description.lower() or "8601" in end_field.description


class TestVSTVideoClipOutput:
    """Test VSTVideoClipOutput model."""

    def test_valid_output(self):
        """Test creating output with valid video_url and stream_id."""
        output = VSTVideoClipOutput(
            video_url="http://localhost:30888/video/clip.mp4",
            stream_id="24c5a7d6-39ce-442e-abf0-430f036b7a3d",
        )
        assert output.video_url == "http://localhost:30888/video/clip.mp4"
        assert output.stream_id == "24c5a7d6-39ce-442e-abf0-430f036b7a3d"

    def test_output_with_real_url_format(self):
        """Test output with URL format from real VST server."""
        output = VSTVideoClipOutput(
            video_url="http://10.0.0.1:30888/vst/api/v1/storage/file/24c5a7d6-39ce-442e-abf0-430f036b7a3d/url?startTime=2025-12-18T07:19:59.332Z&endTime=2025-12-18T07:20:11.332Z",
            stream_id="24c5a7d6-39ce-442e-abf0-430f036b7a3d",
        )
        assert "24c5a7d6-39ce-442e-abf0-430f036b7a3d" in output.video_url
        assert output.stream_id == "24c5a7d6-39ce-442e-abf0-430f036b7a3d"

    def test_missing_video_url_raises(self):
        """Test that missing video_url raises ValidationError."""
        with pytest.raises(ValidationError):
            VSTVideoClipOutput(stream_id="24c5a7d6-39ce-442e-abf0-430f036b7a3d")  # type: ignore

    def test_missing_stream_id_raises(self):
        """Test that missing stream_id raises ValidationError."""
        with pytest.raises(ValidationError):
            VSTVideoClipOutput(video_url="http://example.com/video.mp4")  # type: ignore

    def test_output_json_serializable(self):
        """Test that output can be serialized to JSON."""
        output = VSTVideoClipOutput(
            video_url="http://example.com/video.mp4",
            stream_id="test-stream-id",
        )
        json_str = output.model_dump_json()
        assert "http://example.com/video.mp4" in json_str
        assert "test-stream-id" in json_str

    def test_output_descriptions(self):
        """Test that output fields have proper descriptions."""
        video_field = VSTVideoClipOutput.model_fields["video_url"]
        stream_field = VSTVideoClipOutput.model_fields["stream_id"]
        assert video_field.description is not None
        assert stream_field.description is not None
        assert "URL" in video_field.description or "video" in video_field.description.lower()  # type: ignore
        assert "stream" in stream_field.description.lower()


class TestVSTVideoClipOffsetInputEdgeCases:
    """Test edge cases for VSTVideoClipOffsetInput model_validator."""

    def test_very_small_time_difference(self):
        """Test input with very small time difference."""
        input_data = VSTVideoClipOffsetInput(sensor_id="test_video", start_time=0.0, end_time=0.001)
        assert input_data.start_time is not None
        assert input_data.end_time is not None
        assert input_data.start_time < input_data.end_time

    def test_large_time_values(self):
        """Test input with large time values."""
        input_data = VSTVideoClipOffsetInput(sensor_id="test_video", start_time=0.0, end_time=86400.0)  # 24 hours
        assert input_data.end_time == 86400.0

    def test_float_precision(self):
        """Test input maintains float precision."""
        input_data = VSTVideoClipOffsetInput(sensor_id="test_video", start_time=1.123456789, end_time=2.987654321)
        assert input_data.start_time is not None
        assert input_data.end_time is not None
        assert abs(input_data.start_time - 1.123456789) < 1e-9
        assert abs(input_data.end_time - 2.987654321) < 1e-9
