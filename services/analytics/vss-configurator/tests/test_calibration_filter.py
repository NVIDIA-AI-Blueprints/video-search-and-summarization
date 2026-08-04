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

"""
Tests for calibration file filtering triggered by the keep_count file management operation.

Covers _filter_calibration_file directly and its integration with
_execute_keep_count_operation.
"""

import json
import pytest
from unittest.mock import patch

from profile_configurator.profile_config_manager import ProfileConfigManager


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SENSOR_IDS = ["Camera", "Camera_01", "Camera_02", "Camera_03"]

ROI_ALL_SENSORS = {
    "id": "buffer_zone_1",
    "type": "buffer_zone",
    "roiCoordinates": [],
    "sensors": list(SENSOR_IDS),
    "groups": ["bev-sensor-1"],
}

ROI_SUBSET = {
    "id": "buffer_zone_2",
    "type": "buffer_zone",
    "roiCoordinates": [],
    "sensors": ["Camera", "Camera_01"],
    "groups": ["bev-sensor-1"],
}

ROI_EMPTY_SENSORS = {
    "id": "buffer_zone_empty",
    "type": "buffer_zone",
    "roiCoordinates": [],
    "sensors": [],
    "groups": [],
}


def _make_calib_data(sensors=None, rois=None):
    return {
        "version": "1.0",
        "calibrationType": "cartesian",
        "sensors": sensors if sensors is not None else [{"id": s, "type": "camera"} for s in SENSOR_IDS],
        "rois": rois if rois is not None else [],
    }


def make_manager(env_vars=None) -> ProfileConfigManager:
    with patch.object(ProfileConfigManager, '__init__', return_value=None):
        mgr = ProfileConfigManager.__new__(ProfileConfigManager)
    mgr.env_vars = env_vars or {}
    mgr.profile_configs = {}
    mgr.hardware_profile = 'TEST'
    mgr.deployment_profile = '3d'
    mgr.deployment_modes_enabled = True
    mgr.config = {}
    return mgr


# ---------------------------------------------------------------------------
# _filter_calibration_file — calibration file absent
# ---------------------------------------------------------------------------

def test_filter_skipped_when_calib_file_missing(tmp_path):
    """Returns True and skips silently when the calibration file does not exist."""
    mgr = make_manager({
        "CALIBRATION_DIR_MOUNT_PATH": str(tmp_path),
        "CALIBRATION_FILE_NAME": "calibration.json",
    })
    result = mgr._filter_calibration_file(["Camera_02"])
    assert result is True


# ---------------------------------------------------------------------------
# _filter_calibration_file — sensor removal
# ---------------------------------------------------------------------------

def test_removed_sensors_absent_from_output(tmp_path):
    """Sensor entries for removed IDs are not present in the updated file."""
    calib_path = tmp_path / "calibration.json"
    calib_path.write_text(json.dumps(_make_calib_data()))

    mgr = make_manager({
        "CALIBRATION_DIR_MOUNT_PATH": str(tmp_path),
        "CALIBRATION_FILE_NAME": "calibration.json",
    })
    mgr._filter_calibration_file(["Camera_02", "Camera_03"])

    result = json.loads(calib_path.read_text())
    remaining_ids = [s["id"] for s in result["sensors"]]
    assert "Camera_02" not in remaining_ids
    assert "Camera_03" not in remaining_ids


def test_kept_sensors_present_in_output(tmp_path):
    """Sensor entries for kept cameras are still present in the updated file."""
    calib_path = tmp_path / "calibration.json"
    calib_path.write_text(json.dumps(_make_calib_data()))

    mgr = make_manager({
        "CALIBRATION_DIR_MOUNT_PATH": str(tmp_path),
        "CALIBRATION_FILE_NAME": "calibration.json",
    })
    mgr._filter_calibration_file(["Camera_02", "Camera_03"])

    result = json.loads(calib_path.read_text())
    remaining_ids = [s["id"] for s in result["sensors"]]
    assert "Camera" in remaining_ids
    assert "Camera_01" in remaining_ids


# ---------------------------------------------------------------------------
# _filter_calibration_file — ROI handling
# ---------------------------------------------------------------------------

def test_roi_kept_when_at_least_one_sensor_survives(tmp_path):
    """A ROI with at least one surviving sensor is preserved."""
    calib_path = tmp_path / "calibration.json"
    calib_path.write_text(json.dumps(_make_calib_data(rois=[dict(ROI_ALL_SENSORS)])))

    mgr = make_manager({
        "CALIBRATION_DIR_MOUNT_PATH": str(tmp_path),
        "CALIBRATION_FILE_NAME": "calibration.json",
    })
    mgr._filter_calibration_file(["Camera_02", "Camera_03"])

    result = json.loads(calib_path.read_text())
    assert len(result["rois"]) == 1
    assert result["rois"][0]["id"] == "buffer_zone_1"


def test_removed_sensor_ids_stripped_from_kept_roi(tmp_path):
    """Removed sensor IDs are stripped from the sensors list of a kept ROI."""
    calib_path = tmp_path / "calibration.json"
    calib_path.write_text(json.dumps(_make_calib_data(rois=[dict(ROI_ALL_SENSORS)])))

    mgr = make_manager({
        "CALIBRATION_DIR_MOUNT_PATH": str(tmp_path),
        "CALIBRATION_FILE_NAME": "calibration.json",
    })
    mgr._filter_calibration_file(["Camera_02", "Camera_03"])

    result = json.loads(calib_path.read_text())
    roi_sensors = result["rois"][0]["sensors"]
    assert "Camera_02" not in roi_sensors
    assert "Camera_03" not in roi_sensors
    assert set(roi_sensors) <= {"Camera", "Camera_01"}


def test_roi_dropped_when_all_sensors_removed(tmp_path):
    """A ROI whose entire sensors list is removed is dropped from the output."""
    roi = dict(ROI_SUBSET)  # sensors: ["Camera", "Camera_01"]
    calib_path = tmp_path / "calibration.json"
    calib_path.write_text(json.dumps(_make_calib_data(rois=[roi])))

    mgr = make_manager({
        "CALIBRATION_DIR_MOUNT_PATH": str(tmp_path),
        "CALIBRATION_FILE_NAME": "calibration.json",
    })
    mgr._filter_calibration_file(["Camera", "Camera_01"])

    result = json.loads(calib_path.read_text())
    assert result["rois"] == []


def test_roi_with_initially_empty_sensors_is_preserved(tmp_path):
    """A ROI that already had an empty sensors list is not dropped."""
    calib_path = tmp_path / "calibration.json"
    calib_path.write_text(json.dumps(_make_calib_data(rois=[dict(ROI_EMPTY_SENSORS)])))

    mgr = make_manager({
        "CALIBRATION_DIR_MOUNT_PATH": str(tmp_path),
        "CALIBRATION_FILE_NAME": "calibration.json",
    })
    mgr._filter_calibration_file(["Camera_02"])

    result = json.loads(calib_path.read_text())
    assert len(result["rois"]) == 1
    assert result["rois"][0]["id"] == "buffer_zone_empty"


def test_multiple_rois_handled_independently(tmp_path):
    """ROIs are evaluated independently — one dropped, one kept."""
    roi_drop = dict(ROI_SUBSET)       # sensors: ["Camera", "Camera_01"] — both removed
    roi_keep = dict(ROI_ALL_SENSORS)  # sensors include Camera_02 which survives
    calib_path = tmp_path / "calibration.json"
    calib_path.write_text(json.dumps(_make_calib_data(rois=[roi_drop, roi_keep])))

    mgr = make_manager({
        "CALIBRATION_DIR_MOUNT_PATH": str(tmp_path),
        "CALIBRATION_FILE_NAME": "calibration.json",
    })
    mgr._filter_calibration_file(["Camera", "Camera_01"])

    result = json.loads(calib_path.read_text())
    roi_ids = [r["id"] for r in result["rois"]]
    assert "buffer_zone_2" not in roi_ids
    assert "buffer_zone_1" in roi_ids


# ---------------------------------------------------------------------------
# _filter_calibration_file — backup
# ---------------------------------------------------------------------------

def test_backup_is_created(tmp_path):
    """_create_backup is called with the calibration file path."""
    calib_path = tmp_path / "calibration.json"
    calib_path.write_text(json.dumps(_make_calib_data()))

    mgr = make_manager({
        "CALIBRATION_DIR_MOUNT_PATH": str(tmp_path),
        "CALIBRATION_FILE_NAME": "calibration.json",
    })
    with patch.object(mgr, '_create_backup', return_value=str(tmp_path / "calib.bak")) as mock_backup:
        mgr._filter_calibration_file(["Camera_02"])

    mock_backup.assert_called_once_with(str(calib_path))


def test_filtering_proceeds_when_backup_fails(tmp_path):
    """Filtering still completes and returns True even if backup creation fails."""
    calib_path = tmp_path / "calibration.json"
    calib_path.write_text(json.dumps(_make_calib_data()))

    mgr = make_manager({
        "CALIBRATION_DIR_MOUNT_PATH": str(tmp_path),
        "CALIBRATION_FILE_NAME": "calibration.json",
    })
    with patch.object(mgr, '_create_backup', return_value=None):
        result = mgr._filter_calibration_file(["Camera_02"])

    assert result is True
    data = json.loads(calib_path.read_text())
    assert not any(s["id"] == "Camera_02" for s in data["sensors"])


# ---------------------------------------------------------------------------
# _execute_keep_count_operation — integration with calibration filtering
# ---------------------------------------------------------------------------

def test_keep_count_triggers_calibration_filter_for_removed_files(tmp_path):
    """_filter_calibration_file is called with stems of removed video files."""
    video_dir = tmp_path / "videos"
    video_dir.mkdir()
    for name in ["Camera.mp4", "Camera_01.mp4", "Camera_02.mp4", "Camera_03.mp4"]:
        (video_dir / name).write_text("")

    mgr = make_manager({
        "CALIBRATION_DIR_MOUNT_PATH": str(tmp_path),
        "CALIBRATION_FILE_NAME": "calibration.json",
    })
    mgr.config = {}

    with patch.object(mgr, '_filter_calibration_file', return_value=True) as mock_filter:
        mgr._execute_keep_count_operation([str(video_dir)], {"count": "2", "pattern": "*.mp4"})

    mock_filter.assert_called_once()
    assert set(mock_filter.call_args[0][0]) == {"Camera_02", "Camera_03"}


def test_keep_count_no_filter_when_no_files_removed(tmp_path):
    """_filter_calibration_file is not called when no files exceed the keep count."""
    video_dir = tmp_path / "videos"
    video_dir.mkdir()
    for name in ["Camera.mp4", "Camera_01.mp4"]:
        (video_dir / name).write_text("")

    mgr = make_manager({})
    mgr.config = {}

    with patch.object(mgr, '_filter_calibration_file', return_value=True) as mock_filter:
        mgr._execute_keep_count_operation([str(video_dir)], {"count": "4", "pattern": "*.mp4"})

    mock_filter.assert_not_called()
