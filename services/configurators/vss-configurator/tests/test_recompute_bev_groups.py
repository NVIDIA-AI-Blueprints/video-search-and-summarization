# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for profile-driven, filename-based BEV group recomputation."""

import json
from pathlib import Path
from unittest.mock import patch

from profile_configurator.profile_config_manager import ProfileConfigManager


def make_manager(env_vars=None, mode="3d") -> ProfileConfigManager:
    with patch.object(ProfileConfigManager, "__init__", return_value=None):
        manager = ProfileConfigManager.__new__(ProfileConfigManager)
    manager.env_vars = env_vars or {}
    manager._typed_env_vars = {}
    manager.profile_configs = {}
    manager.hardware_profile = "TEST"
    manager.deployment_profile = mode
    manager.deployment_modes_enabled = True
    manager.config = {}
    return manager


def write_calibration(path: Path, sensor_ids):
    data = {
        "version": "1.0",
        "calibrationType": "cartesian",
        "sensors": [{"id": sensor_id, "type": "camera"} for sensor_id in sensor_ids],
        "sensorGroups": [],
    }
    path.write_text(json.dumps(data), encoding="utf-8")
    return data


def make_operation(video_dir: Path, calibration_file: Path, expected_count=0):
    return {
        "required_mode": "3d",
        "required_calibration_mode": "mount",
        "video_directories": [str(video_dir)],
        "video_patterns": ["*.mp4", "*.mkv"],
        "calibration_file": str(calibration_file),
        "expected_camera_count": expected_count,
        "n_sensor_groups": 1,
    }


def test_discover_camera_names_is_sorted_and_case_insensitive(tmp_path):
    video_dir = tmp_path / "videos"
    video_dir.mkdir()
    for filename in ("Camera_02.MKV", "Camera.mp4", "Camera_01.Mp4", "ignore.txt"):
        (video_dir / filename).write_text("", encoding="utf-8")

    manager = make_manager()

    assert manager._discover_camera_names(
        [str(video_dir)], ["*.mp4", "*.mkv"]
    ) == ["Camera", "Camera_01", "Camera_02"]


def test_discover_camera_names_rejects_duplicate_stems(tmp_path):
    video_dir = tmp_path / "videos"
    video_dir.mkdir()
    (video_dir / "Camera.mp4").write_text("", encoding="utf-8")
    (video_dir / "Camera.mkv").write_text("", encoding="utf-8")

    manager = make_manager()

    try:
        manager._discover_camera_names([str(video_dir)], ["*.mp4", "*.mkv"])
        assert False, "duplicate stems must fail"
    except ValueError as exc:
        assert "Duplicate camera ID 'Camera'" in str(exc)


def test_recompute_uses_filename_stems_and_atomically_updates(tmp_path):
    video_dir = tmp_path / "videos"
    video_dir.mkdir()
    for filename in ("Camera_01.mp4", "Camera.mp4"):
        (video_dir / filename).write_text("", encoding="utf-8")
    calibration_file = tmp_path / "calibration.json"
    write_calibration(calibration_file, ["Camera", "Camera_01"])

    manager = make_manager({"CALIBRATION_MODE": "mount"})
    calls = []

    def fake_recompute(path, sensor_names, n_sensor_groups, max_sensors_per_group):
        calls.append((path, sensor_names, n_sensor_groups, max_sensors_per_group))
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        data["sensorGroups"] = [{"name": "bev-sensor-1", "sensors": sensor_names}]
        Path(path).write_text(json.dumps(data), encoding="utf-8")
        return path

    with patch(
        "profile_configurator.profile_config_manager.recompute_bev_centers",
        side_effect=fake_recompute,
    ):
        assert manager._execute_recompute_bev_groups(
            make_operation(video_dir, calibration_file, expected_count=2)
        )

    assert calls[0][1:] == (["Camera", "Camera_01"], 1, 2)
    result = json.loads(calibration_file.read_text(encoding="utf-8"))
    assert result["sensorGroups"][0]["sensors"] == ["Camera", "Camera_01"]
    assert list(tmp_path.glob("calibration.backup_*.json"))
    assert not list(tmp_path.glob(".calibration.bev_*.json"))


def test_missing_video_sensor_preserves_original(tmp_path):
    video_dir = tmp_path / "videos"
    video_dir.mkdir()
    (video_dir / "Unknown.mp4").write_text("", encoding="utf-8")
    calibration_file = tmp_path / "calibration.json"
    original = write_calibration(calibration_file, ["Camera"])

    manager = make_manager({"CALIBRATION_MODE": "mount"})
    with patch(
        "profile_configurator.profile_config_manager.recompute_bev_centers"
    ) as recompute:
        assert not manager._execute_recompute_bev_groups(
            make_operation(video_dir, calibration_file)
        )

    recompute.assert_not_called()
    assert json.loads(calibration_file.read_text(encoding="utf-8")) == original


def test_expected_camera_count_mismatch_fails_before_backup(tmp_path):
    video_dir = tmp_path / "videos"
    video_dir.mkdir()
    (video_dir / "Camera.mp4").write_text("", encoding="utf-8")
    calibration_file = tmp_path / "calibration.json"
    write_calibration(calibration_file, ["Camera"])

    manager = make_manager({"CALIBRATION_MODE": "mount"})
    assert not manager._execute_recompute_bev_groups(
        make_operation(video_dir, calibration_file, expected_count=2)
    )

    assert not list(tmp_path.glob("calibration.backup_*.json"))


def test_wrong_calibration_mode_fails_without_modifying_file(tmp_path):
    video_dir = tmp_path / "videos"
    video_dir.mkdir()
    (video_dir / "Camera.mp4").write_text("", encoding="utf-8")
    calibration_file = tmp_path / "calibration.json"
    original = write_calibration(calibration_file, ["Camera"])

    manager = make_manager({"CALIBRATION_MODE": "fetch"})
    assert not manager._execute_recompute_bev_groups(
        make_operation(video_dir, calibration_file)
    )

    assert json.loads(calibration_file.read_text(encoding="utf-8")) == original


def test_other_deployment_mode_skips_operation(tmp_path):
    manager = make_manager({"CALIBRATION_MODE": "mount"}, mode="2d")

    assert manager._execute_recompute_bev_groups(
        make_operation(tmp_path / "missing", tmp_path / "missing.json")
    )


def test_recompute_exception_preserves_original_and_cleans_temp(tmp_path):
    video_dir = tmp_path / "videos"
    video_dir.mkdir()
    (video_dir / "Camera.mp4").write_text("", encoding="utf-8")
    calibration_file = tmp_path / "calibration.json"
    original = write_calibration(calibration_file, ["Camera"])

    manager = make_manager({"CALIBRATION_MODE": "mount"})
    with patch(
        "profile_configurator.profile_config_manager.recompute_bev_centers",
        side_effect=RuntimeError("calculation failed"),
    ):
        assert not manager._execute_recompute_bev_groups(
            make_operation(video_dir, calibration_file)
        )

    assert json.loads(calibration_file.read_text(encoding="utf-8")) == original
    assert not list(tmp_path.glob(".calibration.bev_*.json"))


def test_invalid_recompute_output_preserves_original(tmp_path):
    video_dir = tmp_path / "videos"
    video_dir.mkdir()
    (video_dir / "Camera.mp4").write_text("", encoding="utf-8")
    calibration_file = tmp_path / "calibration.json"
    original = write_calibration(calibration_file, ["Camera"])

    def write_invalid_json(path, *_args):
        Path(path).write_text("{invalid", encoding="utf-8")
        return path

    manager = make_manager({"CALIBRATION_MODE": "mount"})
    with patch(
        "profile_configurator.profile_config_manager.recompute_bev_centers",
        side_effect=write_invalid_json,
    ):
        assert not manager._execute_recompute_bev_groups(
            make_operation(video_dir, calibration_file)
        )

    assert json.loads(calibration_file.read_text(encoding="utf-8")) == original


def test_duplicate_calibration_ids_fail_before_recompute(tmp_path):
    video_dir = tmp_path / "videos"
    video_dir.mkdir()
    (video_dir / "Camera.mp4").write_text("", encoding="utf-8")
    calibration_file = tmp_path / "calibration.json"
    write_calibration(calibration_file, ["Camera", "Camera"])

    manager = make_manager({"CALIBRATION_MODE": "mount"})
    with patch(
        "profile_configurator.profile_config_manager.recompute_bev_centers"
    ) as recompute:
        assert not manager._execute_recompute_bev_groups(
            make_operation(video_dir, calibration_file)
        )

    recompute.assert_not_called()


def test_backup_failure_prevents_recompute(tmp_path):
    video_dir = tmp_path / "videos"
    video_dir.mkdir()
    (video_dir / "Camera.mp4").write_text("", encoding="utf-8")
    calibration_file = tmp_path / "calibration.json"
    original = write_calibration(calibration_file, ["Camera"])

    manager = make_manager({"CALIBRATION_MODE": "mount"})
    with patch.object(manager, "_create_backup", return_value=None), patch(
        "profile_configurator.profile_config_manager.recompute_bev_centers"
    ) as recompute:
        assert not manager._execute_recompute_bev_groups(
            make_operation(video_dir, calibration_file)
        )

    recompute.assert_not_called()
    assert json.loads(calibration_file.read_text(encoding="utf-8")) == original


def test_unexpected_output_path_preserves_original(tmp_path):
    video_dir = tmp_path / "videos"
    video_dir.mkdir()
    (video_dir / "Camera.mp4").write_text("", encoding="utf-8")
    calibration_file = tmp_path / "calibration.json"
    original = write_calibration(calibration_file, ["Camera"])
    unexpected_output = tmp_path / "unexpected.json"
    write_calibration(unexpected_output, ["Camera"])

    manager = make_manager({"CALIBRATION_MODE": "mount"})
    with patch(
        "profile_configurator.profile_config_manager.recompute_bev_centers",
        return_value=str(unexpected_output),
    ):
        assert not manager._execute_recompute_bev_groups(
            make_operation(video_dir, calibration_file)
        )

    assert json.loads(calibration_file.read_text(encoding="utf-8")) == original
