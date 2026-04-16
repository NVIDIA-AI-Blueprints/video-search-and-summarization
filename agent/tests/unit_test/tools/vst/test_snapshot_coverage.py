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
"""Additional unit tests for vst.snapshot module to improve coverage."""

from pydantic import ValidationError
import pytest

from vss_agents.tools.vst.snapshot import VSTSnapshotConfig
from vss_agents.tools.vst.snapshot import VSTSnapshotISOInput
from vss_agents.tools.vst.snapshot import VSTSnapshotOffsetInput
from vss_agents.tools.vst.snapshot import VSTSnapshotOutput


class TestVSTSnapshotConfig:
    """Test VSTSnapshotConfig model."""

    def test_required_fields(self):
        config = VSTSnapshotConfig(
            vst_internal_url="http://10.0.0.1:30888",
            vst_external_url="http://1.2.3.4:30888",
        )
        assert config.vst_internal_url == "http://10.0.0.1:30888"
        assert config.vst_external_url == "http://1.2.3.4:30888"
        assert config.overlay_config is False
        assert config.time_format == "offset"

    def test_missing_fields_raises(self):
        with pytest.raises(ValidationError):
            VSTSnapshotConfig(vst_internal_url="http://10.0.0.1:30888")

    def test_overlay_config_enabled(self):
        config = VSTSnapshotConfig(
            vst_internal_url="http://10.0.0.1:30888",
            vst_external_url="http://1.2.3.4:30888",
            overlay_config=True,
        )
        assert config.overlay_config is True

    def test_time_format_iso(self):
        config = VSTSnapshotConfig(
            vst_internal_url="http://10.0.0.1:30888",
            vst_external_url="http://1.2.3.4:30888",
            time_format="iso",
        )
        assert config.time_format == "iso"


class TestVSTSnapshotOffsetInput:
    """Test VSTSnapshotOffsetInput model."""

    def test_valid_input_seconds(self):
        inp = VSTSnapshotOffsetInput(
            sensor_id="camera1",
            start_time=30.0,
        )
        assert inp.sensor_id == "camera1"
        assert inp.start_time == 30.0

    def test_empty_sensor_id_raises(self):
        with pytest.raises(ValidationError):
            VSTSnapshotOffsetInput(sensor_id="", start_time=10.0)

    def test_zero_start_time(self):
        inp = VSTSnapshotOffsetInput(sensor_id="cam1", start_time=0.0)
        assert inp.start_time == 0.0

    def test_missing_fields_raises(self):
        with pytest.raises(ValidationError):
            VSTSnapshotOffsetInput(sensor_id="cam1")


class TestVSTSnapshotISOInput:
    """Test VSTSnapshotISOInput model."""

    def test_valid_input_iso_timestamp(self):
        inp = VSTSnapshotISOInput(
            sensor_id="camera1",
            start_time="2025-08-25T03:05:55.752Z",
        )
        assert inp.sensor_id == "camera1"
        assert inp.start_time == "2025-08-25T03:05:55.752Z"

    def test_empty_sensor_id_raises(self):
        with pytest.raises(ValidationError):
            VSTSnapshotISOInput(sensor_id="", start_time="2025-08-25T03:05:55.752Z")

    def test_missing_start_time_raises(self):
        with pytest.raises(ValidationError):
            VSTSnapshotISOInput(sensor_id="cam1")

    def test_empty_start_time_raises(self):
        with pytest.raises(ValidationError):
            VSTSnapshotISOInput(sensor_id="cam1", start_time="")


class TestVSTSnapshotOutput:
    """Test VSTSnapshotOutput model."""

    def test_valid(self):
        output = VSTSnapshotOutput(image_url="http://example.com/img.jpg", stream_id="stream-uuid")
        assert output.image_url == "http://example.com/img.jpg"
        assert output.stream_id == "stream-uuid"

    def test_missing_url_raises(self):
        with pytest.raises(ValidationError):
            VSTSnapshotOutput(stream_id="stream-uuid")

    def test_missing_stream_id_raises(self):
        with pytest.raises(ValidationError):
            VSTSnapshotOutput(image_url="http://example.com/img.jpg")

    def test_serialization(self):
        output = VSTSnapshotOutput(image_url="http://example.com/img.jpg", stream_id="stream-uuid")
        data = output.model_dump()
        assert "image_url" in data
        assert "stream_id" in data
