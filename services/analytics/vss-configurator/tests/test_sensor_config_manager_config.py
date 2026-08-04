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

"""Unit tests for sensor_config_manager configuration and helper functions."""
import os
import pytest
from unittest.mock import patch


@pytest.fixture(autouse=True)
def safe_calibration_dir(monkeypatch, tmp_path):
    """Use tmp_path for calibration dir so no global dirs are created."""
    monkeypatch.setenv("CALIBRATION_DIR_MOUNT_PATH", str(tmp_path))
    # Clear cache so get_config() re-reads env when tests call refresh_config
    yield
    # After test, optionally clear cache so other tests start fresh
    try:
        import sensor_config_manager as _mod
        _mod._config_cache.clear()
    except Exception:
        pass


def test_get_config_required_keys_present(monkeypatch):
    """get_config() returns dict with required keys."""
    monkeypatch.setenv("ENABLE_CALIBRATION_PROCESS", "true")
    monkeypatch.setenv("CALIBRATION_MODE", "upload")
    import sensor_config_manager as mod
    mod._config_cache.clear()
    config = mod.get_config()
    assert "CALIBRATION_MODE" in config
    assert config["CALIBRATION_MODE"] == "upload"
    assert "PORT" in config
    assert config["PORT"] == "5000"
    assert "ENABLE_CALIBRATION_PROCESS" in config
    assert "MESSAGE_BROKER_TYPE" in config
    assert "WDM_KFK_BOOTSTRAP_URL" in config


def test_get_config_bool_env_true_false(monkeypatch):
    """get_config() parses boolean env vars correctly."""
    import sensor_config_manager as mod
    mod._config_cache.clear()
    monkeypatch.setenv("ENABLE_CALIBRATION_PROCESS", "false")
    config = mod.get_config()
    assert config["ENABLE_CALIBRATION_PROCESS"] is False
    mod._config_cache.clear()
    monkeypatch.setenv("ENABLE_CALIBRATION_PROCESS", "true")
    config = mod.get_config()
    assert config["ENABLE_CALIBRATION_PROCESS"] is True


def test_refresh_config_rereads_env(monkeypatch):
    """refresh_config() clears cache so next get_config() reflects new env."""
    import sensor_config_manager as mod
    monkeypatch.setenv("PORT", "5000")
    mod.refresh_config()
    first_port = mod.CONFIG["PORT"]
    monkeypatch.setenv("PORT", "9999")
    mod.refresh_config()
    assert mod.CONFIG["PORT"] == "9999"


def test_calibration_enabled_required_returns_503_when_disabled(monkeypatch):
    """calibration_enabled_required() returns 503 response when ENABLE_CALIBRATION_PROCESS is false."""
    import sensor_config_manager as mod
    mod._config_cache.clear()
    monkeypatch.setenv("ENABLE_CALIBRATION_PROCESS", "false")
    mod.refresh_config()
    with mod.app.app_context():
        response = mod.calibration_enabled_required()
    assert response is not None
    status_code = response[1]
    assert status_code == 503
    assert "disabled" in response[0].get_data(as_text=True).lower()


def test_calibration_enabled_required_returns_none_when_enabled(monkeypatch):
    """calibration_enabled_required() returns None when calibration process is enabled."""
    import sensor_config_manager as mod
    mod._config_cache.clear()
    monkeypatch.setenv("ENABLE_CALIBRATION_PROCESS", "true")
    mod.refresh_config()
    with mod.app.app_context():
        response = mod.calibration_enabled_required()
    assert response is None


def test_is_profile_config_ready_true_when_file_exists(tmp_path, monkeypatch):
    """is_profile_config_ready() returns True when marker file exists."""
    import sensor_config_manager as mod
    marker = tmp_path / "ready"
    marker.write_text("")
    with patch.object(mod, "PROFILE_CONFIG_READY_FILE", str(marker)):
        assert mod.is_profile_config_ready() is True


def test_is_profile_config_ready_false_when_file_missing(tmp_path, monkeypatch):
    """is_profile_config_ready() returns False when marker file does not exist."""
    import sensor_config_manager as mod
    marker = tmp_path / "nonexistent_ready"
    with patch.object(mod, "PROFILE_CONFIG_READY_FILE", str(marker)):
        assert mod.is_profile_config_ready() is False
