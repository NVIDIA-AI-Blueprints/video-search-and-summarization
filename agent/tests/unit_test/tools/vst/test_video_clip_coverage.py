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
"""Additional unit tests for vst.video_clip module to improve coverage."""

from pydantic import ValidationError
import pytest
from vss_agents.tools.vst.video_clip import VSTVideoClipConfig
from vss_agents.tools.vst.video_clip import VSTVideoClipISOInput
from vss_agents.tools.vst.video_clip import VSTVideoClipOffsetInput
from vss_agents.tools.vst.video_clip import VSTVideoClipOutput


class TestVSTVideoClipConfig:
    """Test VSTVideoClipConfig model."""

    def test_required_fields(self):
        config = VSTVideoClipConfig(
            vst_internal_url="http://10.0.0.1:30888",
            vst_external_url="http://1.2.3.4:30888",
        )
        assert config.vst_internal_url == "http://10.0.0.1:30888"
        assert config.vst_external_url == "http://1.2.3.4:30888"
        assert config.overlay_config is False
        assert config.time_format == "offset"

    def test_missing_fields_raises(self):
        with pytest.raises(ValidationError):
            VSTVideoClipConfig(vst_internal_url="http://10.0.0.1:30888")

    def test_overlay_config_enabled(self):
        config = VSTVideoClipConfig(
            vst_internal_url="http://10.0.0.1:30888",
            vst_external_url="http://1.2.3.4:30888",
            overlay_config=True,
        )
        assert config.overlay_config is True

    def test_time_format_iso(self):
        config = VSTVideoClipConfig(
            vst_internal_url="http://10.0.0.1:30888",
            vst_external_url="http://1.2.3.4:30888",
            time_format="iso",
        )
        assert config.time_format == "iso"


class TestVSTVideoClipOffsetInput:
    """Test VSTVideoClipOffsetInput model."""

    def test_sensor_id_only(self):
        inp = VSTVideoClipOffsetInput(sensor_id="camera1")
        assert inp.sensor_id == "camera1"
        assert inp.start_time is None
        assert inp.end_time is None

    def test_with_times(self):
        inp = VSTVideoClipOffsetInput(
            sensor_id="camera1",
            start_time=10.0,
            end_time=20.0,
        )
        assert inp.start_time == 10.0
        assert inp.end_time == 20.0

    def test_with_object_ids(self):
        inp = VSTVideoClipOffsetInput(
            sensor_id="camera1",
            start_time=10.0,
            end_time=20.0,
            object_ids=["obj-1", "obj-2"],
        )
        assert inp.object_ids == ["obj-1", "obj-2"]

    def test_empty_sensor_id_raises(self):
        with pytest.raises(ValidationError):
            VSTVideoClipOffsetInput(sensor_id="")

    def test_negative_start_time_raises(self):
        with pytest.raises(ValueError, match="non-negative"):
            VSTVideoClipOffsetInput(sensor_id="cam1", start_time=-1.0)

    def test_negative_end_time_raises(self):
        with pytest.raises(ValueError, match="non-negative"):
            VSTVideoClipOffsetInput(sensor_id="cam1", end_time=-1.0)

    def test_start_after_end_raises(self):
        with pytest.raises(ValueError, match="before end time"):
            VSTVideoClipOffsetInput(sensor_id="cam1", start_time=20.0, end_time=10.0)

    def test_start_equals_end_raises(self):
        with pytest.raises(ValueError, match="before end time"):
            VSTVideoClipOffsetInput(sensor_id="cam1", start_time=10.0, end_time=10.0)

    def test_float_conversion(self):
        inp = VSTVideoClipOffsetInput(sensor_id="cam1", start_time=5, end_time=15)
        assert inp.start_time == 5.0
        assert inp.end_time == 15.0


class TestVSTVideoClipISOInput:
    """Test VSTVideoClipISOInput model."""

    def test_with_iso_timestamps(self):
        inp = VSTVideoClipISOInput(
            sensor_id="camera1",
            start_time="2025-08-25T03:05:55.752Z",
            end_time="2025-08-25T03:06:15.752Z",
        )
        assert inp.start_time == "2025-08-25T03:05:55.752Z"
        assert inp.end_time == "2025-08-25T03:06:15.752Z"

    def test_sensor_id_only(self):
        inp = VSTVideoClipISOInput(sensor_id="camera1")
        assert inp.start_time is None
        assert inp.end_time is None

    def test_empty_sensor_id_raises(self):
        with pytest.raises(ValidationError):
            VSTVideoClipISOInput(sensor_id="")


class TestVSTVideoClipOutput:
    """Test VSTVideoClipOutput model."""

    def test_valid(self):
        output = VSTVideoClipOutput(
            video_url="http://example.com/video.mp4",
            stream_id="stream-uuid",
        )
        assert output.video_url == "http://example.com/video.mp4"
        assert output.stream_id == "stream-uuid"

    def test_missing_fields_raises(self):
        with pytest.raises(ValidationError):
            VSTVideoClipOutput(video_url="http://example.com/video.mp4")
