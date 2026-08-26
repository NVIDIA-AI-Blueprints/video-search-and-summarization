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

"""Unit tests for add_sensor()'s VST 400 "already exists" idempotency handling."""
from unittest.mock import MagicMock
import pytest
import requests


@pytest.fixture(autouse=True)
def safe_calibration_dir(monkeypatch, tmp_path):
    """Use tmp_path for calibration dir so no global dirs are created."""
    monkeypatch.setenv("CALIBRATION_DIR_MOUNT_PATH", str(tmp_path))
    yield
    try:
        import sensor_config_manager as _mod
        _mod.refresh_config()
    except Exception:
        pass


def _response(status_code, error_message=None):
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = "" if error_message is None else f'{{"error_message": "{error_message}"}}'
    if status_code == 200:
        resp.json.return_value = {"sensorId": "some-id"}
    else:
        resp.json.return_value = {"error_code": "InvalidParameterError", "error_message": error_message or ""}
    return resp


def _sensor_info(name="Camera_02"):
    from utils.sensor_mapping import Sensor
    return Sensor(name=name, url=f"rtsp://host/{name}", group_id=None, region=None)


def test_add_sensor_returns_immediately_on_200(monkeypatch):
    """A clean 200 returns right away without sleeping."""
    import sensor_config_manager as mod

    post = MagicMock(return_value=_response(200))
    sleep = MagicMock()
    monkeypatch.setattr(mod.requests, "post", post)
    monkeypatch.setattr(mod.time, "sleep", sleep)

    mod.add_sensor(_sensor_info())

    assert post.call_count == 1
    sleep.assert_not_called()


def test_add_sensor_treats_already_exists_400_as_success(monkeypatch):
    """A 400 "already exists" is the desired end state, not a failure -- return, don't retry."""
    import sensor_config_manager as mod

    post = MagicMock(
        return_value=_response(
            400,
            "Sensor exists already, sensorId: df996508-d848-40cb-ac40-dd0a39925732, sensorName: Camera_02",
        )
    )
    sleep = MagicMock()
    monkeypatch.setattr(mod.requests, "post", post)
    monkeypatch.setattr(mod.time, "sleep", sleep)

    mod.add_sensor(_sensor_info())

    assert post.call_count == 1
    sleep.assert_not_called()


def test_add_sensor_treats_already_exists_after_timeout_as_success(monkeypatch):
    """If the client times out but VST completes the add, the duplicate retry is success."""
    import sensor_config_manager as mod

    post = MagicMock(
        side_effect=[
            requests.exceptions.Timeout("read timed out"),
            _response(
                400,
                "Sensor exists already, sensorId: df996508-d848-40cb-ac40-dd0a39925732, sensorName: Camera_02",
            ),
        ]
    )
    sleep = MagicMock()
    monkeypatch.setattr(mod.requests, "post", post)
    monkeypatch.setattr(mod.time, "sleep", sleep)

    mod.add_sensor(_sensor_info(), delay=30, timeout=5)

    assert post.call_count == 2
    sleep.assert_called_once_with(30)


def test_add_sensor_treats_already_exists_after_499_as_success(monkeypatch):
    """Ingress 499 means the client gave up; the follow-up duplicate confirms VST added it."""
    import sensor_config_manager as mod

    post = MagicMock(
        side_effect=[
            _response(499, "client closed request"),
            _response(
                400,
                "Sensor exists already, sensorId: df996508-d848-40cb-ac40-dd0a39925732, sensorName: Camera_02",
            ),
        ]
    )
    sleep = MagicMock()
    monkeypatch.setattr(mod.requests, "post", post)
    monkeypatch.setattr(mod.time, "sleep", sleep)

    mod.add_sensor(_sensor_info(), delay=30, timeout=5)

    assert post.call_count == 2
    sleep.assert_called_once_with(30)


def test_add_sensor_retries_on_name_conflict_400(monkeypatch):
    """VST's name-only collision ("different URL, same name") is a real
    conflict, not our own abandoned request -- it must retry, not succeed."""
    import sensor_config_manager as mod

    responses = [
        _response(400, "User given name is invalid or already exists, sensorId: abc, sensorName: Camera_02"),
        _response(200),
    ]
    post = MagicMock(side_effect=responses)
    sleep = MagicMock()
    monkeypatch.setattr(mod.requests, "post", post)
    monkeypatch.setattr(mod.time, "sleep", sleep)

    mod.add_sensor(_sensor_info(), delay=30)

    assert post.call_count == 2
    sleep.assert_called_once_with(30)


def test_add_sensor_ignores_already_exists_text_on_non_400(monkeypatch):
    """Only a 400 counts as the duplicate-add case -- a 5xx whose body happens
    to mention "already exists" must still retry, not be treated as success."""
    import sensor_config_manager as mod

    responses = [
        _response(500, "Sensor exists already, sensorId: abc, sensorName: Camera_02"),
        _response(200),
    ]
    post = MagicMock(side_effect=responses)
    sleep = MagicMock()
    monkeypatch.setattr(mod.requests, "post", post)
    monkeypatch.setattr(mod.time, "sleep", sleep)

    mod.add_sensor(_sensor_info(), delay=30)

    assert post.call_count == 2
    sleep.assert_called_once_with(30)


def test_add_sensor_retries_on_genuine_error(monkeypatch):
    """A real (non-duplicate) error still retries with a delay until it succeeds."""
    import sensor_config_manager as mod

    responses = [
        _response(400, "Invalid Parameters"),
        _response(400, "Invalid Parameters"),
        _response(200),
    ]
    post = MagicMock(side_effect=responses)
    sleep = MagicMock()
    monkeypatch.setattr(mod.requests, "post", post)
    monkeypatch.setattr(mod.time, "sleep", sleep)

    mod.add_sensor(_sensor_info(), delay=30)

    assert post.call_count == 3
    assert sleep.call_count == 2
    sleep.assert_called_with(30)


def test_add_sensor_uses_configured_timeout(monkeypatch):
    """The default VST add timeout can be tuned for slower ingress/VST paths."""
    import sensor_config_manager as mod

    monkeypatch.setitem(mod.CONFIG, "VST_CAMERA_ADD_TIMEOUT", 22)
    post = MagicMock(return_value=_response(200))
    monkeypatch.setattr(mod.requests, "post", post)
    monkeypatch.setattr(mod.time, "sleep", MagicMock())

    mod.add_sensor(_sensor_info())

    _, kwargs = post.call_args
    assert kwargs["timeout"] == 22


def test_add_sensor_uses_generous_default_timeout(monkeypatch):
    """Default timeout covers VST's ~5-6s cold-start add latency (was 5s)."""
    import sensor_config_manager as mod

    post = MagicMock(return_value=_response(200))
    monkeypatch.setattr(mod.requests, "post", post)
    monkeypatch.setattr(mod.time, "sleep", MagicMock())

    mod.add_sensor(_sensor_info())

    _, kwargs = post.call_args
    assert kwargs["timeout"] >= 15
