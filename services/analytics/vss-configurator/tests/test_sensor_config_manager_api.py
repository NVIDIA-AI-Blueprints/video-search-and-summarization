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

"""Unit tests for sensor_config_manager Flask API endpoints."""
import os
import json
import pytest
from unittest.mock import patch, MagicMock


@pytest.fixture
def safe_calibration_dir_for_api(monkeypatch, tmp_path):
    """Use tmp_path for calibration so no global dirs are created."""
    monkeypatch.setenv("CALIBRATION_DIR_MOUNT_PATH", str(tmp_path))
    yield tmp_path


@pytest.fixture
def client(safe_calibration_dir_for_api):
    """Flask test client with mocked sensor_mapping and CONFIG."""
    import sensor_config_manager as mod
    mod._config_cache.clear()
    mod.app.config["TESTING"] = True
    with mod.app.test_client() as c:
        yield c


def test_healthz_returns_200_and_healthy(client):
    """GET /healthz returns 200 and status healthy."""
    r = client.get("/healthz")
    assert r.status_code == 200
    data = r.get_json()
    assert data["status"] == "healthy"


def test_readyz_profile_disabled_returns_200_ready(client):
    """GET /readyz returns 200 ready when profile configurator is disabled."""
    import sensor_config_manager as mod
    with patch.dict(os.environ, {"ENABLE_PROFILE_CONFIGURATOR": "false"}):
        # readiness_check reads os.environ.get at request time
        r = client.get("/readyz")
    assert r.status_code == 200
    data = r.get_json()
    assert data["status"] == "ready"


def test_readyz_profile_enabled_marker_present_returns_200(client, tmp_path):
    """GET /readyz returns 200 when profile configurator enabled and marker file exists."""
    import sensor_config_manager as mod
    marker = tmp_path / "ready"
    marker.write_text("")
    with patch.dict(os.environ, {"ENABLE_PROFILE_CONFIGURATOR": "true"}):
        with patch.object(mod, "PROFILE_CONFIG_READY_FILE", str(marker)):
            with patch.object(mod, "is_profile_config_ready", return_value=True):
                r = client.get("/readyz")
    assert r.status_code == 200
    assert r.get_json()["status"] == "ready"


def test_readyz_profile_enabled_marker_missing_returns_503(client, tmp_path):
    """GET /readyz returns 503 when profile configurator enabled and marker file missing."""
    import sensor_config_manager as mod
    with patch.dict(os.environ, {"ENABLE_PROFILE_CONFIGURATOR": "true"}):
        with patch.object(mod, "is_profile_config_ready", return_value=False):
            r = client.get("/readyz")
    assert r.status_code == 503
    assert r.get_json()["status"] == "not_ready"


def test_post_calibration_disabled_returns_503(client, monkeypatch):
    """POST /calibration returns 503 when calibration process is disabled."""
    import sensor_config_manager as mod
    mod._config_cache.clear()
    monkeypatch.setenv("ENABLE_CALIBRATION_PROCESS", "false")
    mod.refresh_config()
    r = client.post("/calibration", json={"sensors": []}, content_type="application/json")
    assert r.status_code == 503


def test_post_calibration_fetch_mode_returns_200_warning(client, monkeypatch):
    """POST /calibration in fetch mode returns 200 with warning message."""
    import sensor_config_manager as mod
    mod._config_cache.clear()
    monkeypatch.setenv("ENABLE_CALIBRATION_PROCESS", "true")
    monkeypatch.setenv("CALIBRATION_MODE", "fetch")
    monkeypatch.setenv("CALIBRATION_API_ENDPOINT", "http://example.com/cal")
    mod.refresh_config()
    r = client.post("/calibration", json={"sensors": []}, content_type="application/json")
    assert r.status_code == 200
    data = r.get_json()
    assert data.get("status") == "warning"
    assert "fetch" in data.get("message", "").lower()


def test_post_calibration_upload_mode_success(client, monkeypatch, tmp_path):
    """POST /calibration in upload mode with valid JSON returns 200 success."""
    import sensor_config_manager as mod
    mod._config_cache.clear()
    monkeypatch.setenv("ENABLE_CALIBRATION_PROCESS", "true")
    monkeypatch.setenv("CALIBRATION_MODE", "upload")
    monkeypatch.setenv("CALIBRATION_DIR_MOUNT_PATH", str(tmp_path))
    mod.refresh_config()
    payload = {"sensors": [{"id": "c1"}], "calibrationType": "test"}
    r = client.post("/calibration", json=payload, content_type="application/json")
    assert r.status_code == 200
    data = r.get_json()
    assert data.get("status") == "success"


def test_post_calibration_empty_body_returns_400(client, monkeypatch, tmp_path):
    """POST /calibration with no body returns 400."""
    import sensor_config_manager as mod
    mod._config_cache.clear()
    monkeypatch.setenv("ENABLE_CALIBRATION_PROCESS", "true")
    monkeypatch.setenv("CALIBRATION_MODE", "upload")
    monkeypatch.setenv("CALIBRATION_DIR_MOUNT_PATH", str(tmp_path))
    mod.refresh_config()
    r = client.post("/calibration", data=None, content_type="application/json")
    assert r.status_code == 400


def test_download_calibration_disabled_returns_503(client, monkeypatch):
    """GET /download returns 503 when calibration process is disabled."""
    import sensor_config_manager as mod
    mod._config_cache.clear()
    monkeypatch.setenv("ENABLE_CALIBRATION_PROCESS", "false")
    mod.refresh_config()
    r = client.get("/download")
    assert r.status_code == 503


def test_download_calibration_file_missing_returns_404(client, monkeypatch, tmp_path):
    """GET /download returns 404 when calibration file does not exist."""
    import sensor_config_manager as mod
    mod._config_cache.clear()
    monkeypatch.setenv("ENABLE_CALIBRATION_PROCESS", "true")
    monkeypatch.setenv("CALIBRATION_DIR_MOUNT_PATH", str(tmp_path))
    mod.refresh_config()
    r = client.get("/download")
    assert r.status_code == 404


def test_download_calibration_file_exists_returns_200(client, monkeypatch, tmp_path):
    """GET /download returns 200 and file when calibration file exists."""
    import sensor_config_manager as mod
    mod._config_cache.clear()
    monkeypatch.setenv("ENABLE_CALIBRATION_PROCESS", "true")
    monkeypatch.setenv("CALIBRATION_DIR_MOUNT_PATH", str(tmp_path))
    monkeypatch.setenv("CALIBRATION_FILE_NAME", "calibration.json")
    mod.refresh_config()
    cal_path = tmp_path / "calibration.json"
    cal_path.write_text('{"sensors": []}')
    r = client.get("/download")
    assert r.status_code == 200
    assert r.data


def test_cameras_mapping_none_returns_503(client):
    """GET /cameras returns 503 when sensor_mapping is not yet created."""
    import sensor_config_manager as mod
    with patch.object(mod, "sensor_mapping", None):
        r = client.get("/cameras")
    assert r.status_code == 503


def test_cameras_with_mapping_returns_list(client, sample_sensor_mapping):
    """GET /cameras returns 200 and list of sensor name|group_id|url when mapping exists."""
    import sensor_config_manager as mod
    with patch.object(mod, "sensor_mapping", sample_sensor_mapping):
        r = client.get("/cameras")
    assert r.status_code == 200
    data = r.get_json()
    assert isinstance(data, list)
    assert len(data) == 2
    for item in data:
        assert "|" in item
        assert "rtsp://" in item


def test_groups_calibration_disabled_returns_503(client, monkeypatch):
    """GET /groups returns 503 when calibration process is disabled."""
    import sensor_config_manager as mod
    mod._config_cache.clear()
    monkeypatch.setenv("ENABLE_CALIBRATION_PROCESS", "false")
    mod.refresh_config()
    r = client.get("/groups")
    assert r.status_code == 503


def test_groups_mapping_none_returns_503(client, monkeypatch):
    """GET /groups returns 503 when sensor_mapping is None."""
    import sensor_config_manager as mod
    mod._config_cache.clear()
    monkeypatch.setenv("ENABLE_CALIBRATION_PROCESS", "true")
    mod.refresh_config()
    with patch.object(mod, "sensor_mapping", None):
        r = client.get("/groups")
    assert r.status_code == 503


def test_groups_with_mapping_returns_list(client, sample_sensor_mapping, monkeypatch):
    """GET /groups returns 200 and list of group names when mapping exists."""
    import sensor_config_manager as mod
    mod._config_cache.clear()
    monkeypatch.setenv("ENABLE_CALIBRATION_PROCESS", "true")
    mod.refresh_config()
    with patch.object(mod, "sensor_mapping", sample_sensor_mapping):
        r = client.get("/groups")
    assert r.status_code == 200
    data = r.get_json()
    assert isinstance(data, list)
    assert "r1|g1" in data
    assert "r2|g1" in data


# ---------------------------------------------------------------------------
# add_sensor() unit tests — covers the hot-loop / duplicate-block bug fixes
# ---------------------------------------------------------------------------

def _make_sensor(name="cam-1", url="rtsp://host/1"):
    from utils.sensor_mapping import Sensor
    return Sensor(name=name, url=url, group_id="g1", region="r1")


@pytest.fixture
def no_sleep(monkeypatch):
    """Patch time.sleep so tests don't actually wait."""
    monkeypatch.setattr("sensor_config_manager.time.sleep", lambda _: None)


def test_add_sensor_200_returns_immediately(safe_calibration_dir_for_api, no_sleep):
    """add_sensor returns after a single 200 response without sleeping."""
    import sensor_config_manager as mod
    ok = MagicMock(status_code=200, text="ok")
    with patch("sensor_config_manager.requests.post", return_value=ok) as mock_post:
        mod.add_sensor(_make_sensor(), delay=5, max_retries=3)
    assert mock_post.call_count == 1


def test_add_sensor_409_treated_as_success(safe_calibration_dir_for_api, no_sleep):
    """HTTP 409 (already exists) is treated as success and does NOT retry."""
    import sensor_config_manager as mod
    conflict = MagicMock(status_code=409, text="sensor already exists")
    with patch("sensor_config_manager.requests.post", return_value=conflict) as mock_post:
        mod.add_sensor(_make_sensor(), delay=5, max_retries=10)
    # Must exit on the first 409 — must NOT spin 10 times
    assert mock_post.call_count == 1


def test_add_sensor_already_in_body_treated_as_success(safe_calibration_dir_for_api, no_sleep):
    """A 400 response whose body contains 'already' is treated as success."""
    import sensor_config_manager as mod
    conflict = MagicMock(status_code=400, text='{"message":"Sensor already registered"}')
    with patch("sensor_config_manager.requests.post", return_value=conflict) as mock_post:
        mod.add_sensor(_make_sensor(), delay=5, max_retries=10)
    assert mock_post.call_count == 1


def test_add_sensor_non200_retries_then_gives_up(safe_calibration_dir_for_api, no_sleep):
    """A persistent non-200 (not duplicate) causes bounded retries then returns."""
    import sensor_config_manager as mod
    bad = MagicMock(status_code=500, text="internal error")
    max_retries = 4
    with patch("sensor_config_manager.requests.post", return_value=bad) as mock_post:
        mod.add_sensor(_make_sensor(), delay=1, max_retries=max_retries)
    # Must have tried exactly max_retries times and then given up (not looped forever)
    assert mock_post.call_count == max_retries


def test_add_sensor_exception_retries_then_gives_up(safe_calibration_dir_for_api, no_sleep):
    """Repeated connection errors cause bounded retries then return (no infinite loop)."""
    import sensor_config_manager as mod
    max_retries = 3
    with patch("sensor_config_manager.requests.post", side_effect=ConnectionError("refused")) as mock_post:
        mod.add_sensor(_make_sensor(), delay=1, max_retries=max_retries)
    assert mock_post.call_count == max_retries


def test_add_sensor_sleep_called_on_non200_not_exception(safe_calibration_dir_for_api):
    """Sleep is called between retries even when VMS responds (not just on exceptions).

    This directly tests defect #1: before the fix, sleep was only in the except
    branch so non-200 responses from a reachable VMS caused ~3 ms hot-looping.
    """
    import sensor_config_manager as mod
    bad = MagicMock(status_code=500, text="error")
    sleep_calls = []
    with patch("sensor_config_manager.requests.post", return_value=bad):
        with patch("sensor_config_manager.time.sleep", side_effect=lambda t: sleep_calls.append(t)):
            mod.add_sensor(_make_sensor(), delay=1, max_retries=3)
    # Sleep must have been called between the first two attempts (at minimum)
    assert len(sleep_calls) >= 1, "time.sleep must be called on the non-200 retry path"
