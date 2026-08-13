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

"""Unit tests for utils.sensor_mapping."""
import os
import json
import pytest

from utils.sensor_mapping import Sensor, SensorMapping


class TestSensor:
    """Tests for Sensor dataclass."""

    def test_construction_required_only(self):
        s = Sensor(name="c1", url="rtsp://host/1")
        assert s.name == "c1"
        assert s.url == "rtsp://host/1"
        assert s.group_id is None
        assert s.region is None

    def test_construction_with_all_fields(self):
        s = Sensor(name="c1", url="rtsp://host/1", group_id="g1", region="r1")
        assert s.name == "c1"
        assert s.url == "rtsp://host/1"
        assert s.group_id == "g1"
        assert s.region == "r1"


class TestGetSensorIdToRtspMapping:
    """Tests for _get_sensor_id_to_rtsp_mapping."""

    def test_empty_input(self):
        assert SensorMapping._get_sensor_id_to_rtsp_mapping([]) == {}

    def test_single_group(self):
        sb_output = [
            {"rtspURLs": [{"name": "cam-1", "url": "rtsp://a/1"}, {"name": "cam-2", "url": "rtsp://a/2"}]}
        ]
        out = SensorMapping._get_sensor_id_to_rtsp_mapping(sb_output)
        assert out == {"cam-1": "rtsp://a/1", "cam-2": "rtsp://a/2"}

    def test_multiple_groups(self, sample_msb_output):
        out = SensorMapping._get_sensor_id_to_rtsp_mapping(sample_msb_output)
        assert out["camera-001"] == "rtsp://host1:554/stream1"
        assert out["camera-002"] == "rtsp://host1:554/stream2"
        assert out["camera-003"] == "rtsp://host2:554/stream1"
        assert len(out) == 3


class TestFormatIpInUrl:
    """Tests for _format_ip_in_url."""

    def test_no_env_unchanged(self, monkeypatch):
        monkeypatch.delenv("SENSOR_BRIDGE_RTSP_SERVICE_NAME", raising=False)
        url = "rtsp://original:554/path"
        assert SensorMapping._format_ip_in_url(url) == url

    def test_env_replaces_host(self, monkeypatch):
        monkeypatch.setenv("SENSOR_BRIDGE_RTSP_SERVICE_NAME", "replacement")
        url = "rtsp://original:554/path"
        result = SensorMapping._format_ip_in_url(url)
        assert "replacement" in result
        assert "original" not in result
        assert result == "rtsp://replacement:554/path"


class TestSensorMappingGenerateMsb:
    """Tests for SensorMapping.generate with info_source='msb'."""

    def test_with_calibration(self, sample_msb_output, sample_calibration, mock_logger):
        mapping = SensorMapping.generate(
            sample_msb_output, sample_calibration, mock_logger, info_source="msb"
        )
        assert len(mapping.sensors) == 3
        c1 = mapping.sensors["camera-001"]
        assert c1.name == "camera-001"
        assert "stream1" in c1.url
        assert c1.group_id == "group-a"
        assert c1.region == "north"
        c2 = mapping.sensors["camera-002"]
        assert c2.region == "south"
        c3 = mapping.sensors["camera-003"]
        assert c3.group_id == "group-b"

    def test_without_calibration(self, sample_msb_output, mock_logger):
        mapping = SensorMapping.generate(
            sample_msb_output, None, mock_logger, info_source="msb"
        )
        assert len(mapping.sensors) == 3
        for sid in ["camera-001", "camera-002", "camera-003"]:
            s = mapping.sensors[sid]
            assert s.name == sid
            assert s.url
            assert s.group_id is None
            assert s.region is None

    def test_calibration_sensor_not_in_sb_skipped(self, mock_logger):
        sb_output = [{"rtspURLs": [{"name": "only-in-sb", "url": "rtsp://x/1"}]}]
        calibration = {
            "calibrationType": "x",
            "sensors": [
                {
                    "id": "only-in-cal",
                    "group": {"name": "g"},
                    "region": {"placeLevel": "z"},
                    "place": [{"name": "z", "value": "v"}],
                }
            ],
        }
        mapping = SensorMapping.generate(sb_output, calibration, mock_logger, info_source="msb")
        assert "only-in-cal" not in mapping.sensors
        assert "only-in-sb" not in mapping.sensors  # not in calibration so skipped per logic


class TestSensorMappingGenerateVst:
    """Tests for SensorMapping.generate with info_source='nvstreamer'."""

    def test_vst_basic(self, sample_nvstreamer_streams, mock_logger):
        mapping = SensorMapping.generate(
            sample_nvstreamer_streams, None, mock_logger, info_source="nvstreamer"
        )
        assert len(mapping.sensors) == 2
        s1 = mapping.sensors["cam-vst-01"]
        assert s1.url == "rtsp://vst:554/live/01"
        assert s1.group_id is None
        assert s1.region is None

    def test_vst_with_calibration(self, sample_nvstreamer_streams, mock_logger):
        calibration = {
            "sensors": [
                {
                    "id": "cam-vst-01",
                    "group": {"name": "vst-group"},
                    "region": {"placeLevel": "zone"},
                    "place": [{"name": "zone", "value": "entrance"}],
                }
            ]
        }
        mapping = SensorMapping.generate(
            sample_nvstreamer_streams, calibration, mock_logger, info_source="nvstreamer"
        )
        s1 = mapping.sensors["cam-vst-01"]
        assert s1.group_id == "vst-group"
        assert s1.region == "entrance"

    def test_vst_skip_missing_event(self, mock_logger):
        streams = [{"source": "preload"}]  # no 'event'
        mapping = SensorMapping.generate(streams, None, mock_logger, info_source="nvstreamer")
        assert len(mapping.sensors) == 0

    def test_vst_skip_missing_camera_id_or_url(self, mock_logger):
        streams = [
            {"event": {"camera_id": "x"}},  # no url
            {"event": {"camera_url": "rtsp://x"}},  # no id
        ]
        mapping = SensorMapping.generate(streams, None, mock_logger, info_source="nvstreamer")
        assert len(mapping.sensors) == 0


class TestSensorMappingGenerateFile:
    """Tests for SensorMapping.generate with info_source='file'."""

    def test_file_basic(self, mock_logger):
        file_sensors = [
            {"camera_name": "cam-f1", "rtsp_url": "rtsp://host:554/f1"},
            {"camera_name": "cam-f2", "rtsp_url": "rtsp://host:554/f2", "group_id": "g1", "region": "r1"},
        ]
        mapping = SensorMapping.generate(file_sensors, None, mock_logger, info_source="file")
        assert len(mapping.sensors) == 2
        s1 = mapping.sensors["cam-f1"]
        assert s1.url == "rtsp://host:554/f1"
        assert s1.group_id is None
        assert s1.region is None
        s2 = mapping.sensors["cam-f2"]
        assert s2.group_id == "g1"
        assert s2.region == "r1"

    def test_file_with_calibration_merge(self, mock_logger):
        file_sensors = [
            {"camera_name": "cam-cal", "rtsp_url": "rtsp://host/1"},  # no group/region
        ]
        calibration = {
            "sensors": [
                {
                    "id": "cam-cal",
                    "group": {"name": "cal-group"},
                    "region": {"placeLevel": "zone"},
                    "place": [{"name": "zone", "value": "cal-region"}],
                }
            ]
        }
        mapping = SensorMapping.generate(file_sensors, calibration, mock_logger, info_source="file")
        assert len(mapping.sensors) == 1
        s = mapping.sensors["cam-cal"]
        assert s.group_id == "cal-group"
        assert s.region == "cal-region"

    def test_file_skip_missing_camera_name_or_rtsp_url(self, mock_logger):
        file_sensors = [
            {"camera_name": "ok", "rtsp_url": "rtsp://x"},
            {"rtsp_url": "rtsp://y"},   # missing camera_name
            {"camera_name": "z"},        # missing rtsp_url
        ]
        mapping = SensorMapping.generate(file_sensors, None, mock_logger, info_source="file")
        assert len(mapping.sensors) == 1
        assert "ok" in mapping.sensors

    def test_invalid_info_source_raises(self, sample_msb_output, mock_logger):
        with pytest.raises(ValueError, match="Invalid info_source"):
            SensorMapping.generate(
                sample_msb_output, None, mock_logger, info_source="invalid"
            )


class TestGetSensorInfo:
    """Tests for get_sensor_info."""

    def test_existing_returns_sensor(self, sample_sensor_mapping):
        s = sample_sensor_mapping.get_sensor_info("cam-1")
        assert s is not None
        assert s.name == "cam-1"
        assert s.url == "rtsp://host/1"

    def test_missing_returns_none(self, sample_sensor_mapping):
        assert sample_sensor_mapping.get_sensor_info("nonexistent") is None


class TestGetGroupNames:
    """Tests for get_group_names."""

    def test_unique_region_group_id(self, sample_sensor_mapping):
        names = sample_sensor_mapping.get_group_names()
        assert isinstance(names, list)
        assert "r1|g1" in names
        assert "r2|g1" in names
        assert len(names) == 2

    def test_empty_mapping(self):
        mapping = SensorMapping()
        # get_group_names does f"{sensor.region}|{sensor.group_id}" so with no sensors
        # we get set() -> list() = []
        assert mapping.get_group_names() == []


class TestGetSensorNames:
    """Tests for get_sensor_names (format name|group_id|url)."""

    def test_returns_list_of_strings(self, sample_sensor_mapping):
        names = sample_sensor_mapping.get_sensor_names()
        assert isinstance(names, list)
        assert len(names) == 2
        for item in names:
            assert isinstance(item, str)
            parts = item.split("|")
            assert len(parts) == 3
            assert parts[0] in ("cam-1", "cam-2")
            assert "rtsp://" in parts[2]


class TestSaveLoadRoundTrip:
    """Tests for save_to_file and load_from_file."""

    def test_round_trip(self, sample_sensor_mapping, tmp_path, mock_logger):
        path = tmp_path / "sensor_mapping.json"
        sample_sensor_mapping.save_to_file(str(path))
        assert path.exists()
        loaded = SensorMapping.load_from_file(str(path))
        assert loaded is not None
        assert set(loaded.sensors.keys()) == set(sample_sensor_mapping.sensors.keys())
        for name, sensor in loaded.sensors.items():
            orig = sample_sensor_mapping.sensors[name]
            assert sensor.name == orig.name
            assert sensor.url == orig.url
            assert sensor.group_id == orig.group_id
            assert sensor.region == orig.region

    def test_load_missing_file_returns_none(self, tmp_path):
        path = tmp_path / "does_not_exist.json"
        assert not path.exists()
        assert SensorMapping.load_from_file(str(path)) is None
