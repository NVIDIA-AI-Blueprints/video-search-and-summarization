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

"""Unit tests for NVStreamer stream-list readiness (partial list race)."""
from unittest.mock import MagicMock, patch
import pytest


@pytest.fixture(autouse=True)
def safe_calibration_dir(monkeypatch, tmp_path):
    """Use tmp_path for calibration dir so no global dirs are created."""
    monkeypatch.setenv("CALIBRATION_DIR_MOUNT_PATH", str(tmp_path))
    yield
    try:
        import sensor_config_manager as _mod
        _mod._config_cache.clear()
    except Exception:
        pass


def _stream_entry(name):
    return {
        f"{name}_0": [{
            "isMain": True,
            "name": name,
            "url": f"rtsp://nvstreamer/{name}",
            "metadata": {},
        }]
    }


class FakeClock:
    def __init__(self):
        self.t = 0.0

    def time(self):
        return self.t

    def sleep(self, seconds):
        self.t += seconds


def _ok_response(payload):
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = payload
    return resp


def test_stream_list_complete_requires_expected_count():
    import sensor_config_manager as mod

    assert mod._nvstreamer_stream_list_is_complete(2, None, 3) is False
    assert mod._nvstreamer_stream_list_is_complete(3, 2, 3) is True
    assert mod._nvstreamer_stream_list_is_complete(4, None, 3) is True


def test_stream_list_complete_requires_stable_count_when_expected_unknown():
    import sensor_config_manager as mod

    assert mod._nvstreamer_stream_list_is_complete(2, None, 0) is False
    assert mod._nvstreamer_stream_list_is_complete(2, 2, 0) is True
    assert mod._nvstreamer_stream_list_is_complete(3, 2, 0) is False
    assert mod._nvstreamer_stream_list_is_complete(0, 0, 0) is False


def test_parse_num_streams_env(monkeypatch):
    import sensor_config_manager as mod

    monkeypatch.delenv("NUM_STREAMS", raising=False)
    assert mod._parse_non_negative_int_env("NUM_STREAMS", 0) == 0
    monkeypatch.setenv("NUM_STREAMS", "3")
    assert mod._parse_non_negative_int_env("NUM_STREAMS", 0) == 3
    monkeypatch.setenv("NUM_STREAMS", "not-a-number")
    assert mod._parse_non_negative_int_env("NUM_STREAMS", 0) == 0
    monkeypatch.setenv("NUM_STREAMS", "-1")
    assert mod._parse_non_negative_int_env("NUM_STREAMS", 0) == 0


def test_get_config_reads_num_streams(monkeypatch):
    import sensor_config_manager as mod

    monkeypatch.setenv("NUM_STREAMS", "3")
    mod.refresh_config()
    assert mod.CONFIG["NUM_STREAMS"] == 3


def test_fetch_waits_for_num_streams_instead_of_first_non_empty(monkeypatch):
    """A 2-stream poll must not commit when NUM_STREAMS=3; the next full list should."""
    import sensor_config_manager as mod

    clock = FakeClock()
    payloads = [
        [_stream_entry("Camera_02"), _stream_entry("Camera")],
        [_stream_entry("Camera_02"), _stream_entry("Camera"), _stream_entry("Camera_01")],
    ]

    monkeypatch.setitem(mod.CONFIG, "NUM_STREAMS", 3)
    monkeypatch.setitem(mod.CONFIG, "NVSTREAMER_STREAMS_ENDPOINT_TIMEOUT", 100)
    monkeypatch.setitem(mod.CONFIG, "NVSTREAMER_STREAMS_ENDPOINT", "http://nvstreamer/streams")

    with patch.object(mod.time, "time", clock.time), \
         patch.object(mod.time, "sleep", clock.sleep), \
         patch.object(mod.requests, "get", side_effect=[_ok_response(p) for p in payloads]), \
         patch.object(mod, "nvstreamer_stream_is_valid", return_value=True):
        streams = mod.fetch_all_streams_from_nvstreamer()

    names = {s["event"]["camera_name"] for s in streams}
    assert names == {"Camera_02", "Camera", "Camera_01"}
    assert clock.t == mod.NVSTREAMER_STREAMS_POLL_INTERVAL_SEC


def test_fetch_accepts_stable_count_when_num_streams_unset(monkeypatch):
    import sensor_config_manager as mod

    clock = FakeClock()
    two = [_stream_entry("Camera_02"), _stream_entry("Camera")]

    monkeypatch.setitem(mod.CONFIG, "NUM_STREAMS", 0)
    monkeypatch.setitem(mod.CONFIG, "NVSTREAMER_STREAMS_ENDPOINT_TIMEOUT", 100)
    monkeypatch.setitem(mod.CONFIG, "NVSTREAMER_STREAMS_ENDPOINT", "http://nvstreamer/streams")

    with patch.object(mod.time, "time", clock.time), \
         patch.object(mod.time, "sleep", clock.sleep), \
         patch.object(mod.requests, "get", side_effect=[_ok_response(two), _ok_response(two)]), \
         patch.object(mod, "nvstreamer_stream_is_valid", return_value=True):
        streams = mod.fetch_all_streams_from_nvstreamer()

    assert len(streams) == 2


def test_fetch_times_out_with_partial_list_and_warns(monkeypatch, caplog):
    import logging
    import sensor_config_manager as mod

    clock = FakeClock()
    partial = [_stream_entry("Camera_02"), _stream_entry("Camera")]

    monkeypatch.setitem(mod.CONFIG, "NUM_STREAMS", 3)
    monkeypatch.setitem(mod.CONFIG, "NVSTREAMER_STREAMS_ENDPOINT_TIMEOUT", 2)
    monkeypatch.setitem(mod.CONFIG, "NVSTREAMER_STREAMS_ENDPOINT", "http://nvstreamer/streams")

    def always_partial(*_args, **_kwargs):
        return _ok_response(partial)

    with caplog.at_level(logging.WARNING), \
         patch.object(mod.time, "time", clock.time), \
         patch.object(mod.time, "sleep", clock.sleep), \
         patch.object(mod.requests, "get", side_effect=always_partial), \
         patch.object(mod, "nvstreamer_stream_is_valid", return_value=True):
        streams = mod.fetch_all_streams_from_nvstreamer()

    assert len(streams) == 2
    assert clock.t == 2
    assert any("NUM_STREAMS=3" in rec.message for rec in caplog.records)
    assert any("NVSTREAMER_STREAMS_ENDPOINT_TIMEOUT" in rec.message for rec in caplog.records)


def test_fetch_errors_and_empty_retry_until_complete_list(monkeypatch):
    """Unreachable / empty responses retry indefinitely; timeout applies only to a partial list."""
    import sensor_config_manager as mod

    clock = FakeClock()
    empty = MagicMock()
    empty.status_code = 200
    empty.json.return_value = []
    error = MagicMock()
    error.status_code = 503
    complete = [
        _stream_entry("Camera_02"),
        _stream_entry("Camera"),
        _stream_entry("Camera_01"),
    ]

    monkeypatch.setitem(mod.CONFIG, "NUM_STREAMS", 3)
    monkeypatch.setitem(mod.CONFIG, "NVSTREAMER_STREAMS_ENDPOINT_TIMEOUT", 2)
    monkeypatch.setitem(mod.CONFIG, "NVSTREAMER_STREAMS_ENDPOINT", "http://nvstreamer/streams")

    with patch.object(mod.time, "time", clock.time), \
         patch.object(mod.time, "sleep", clock.sleep), \
         patch.object(
             mod.requests,
             "get",
             side_effect=[error, empty, error, _ok_response(complete)],
         ), \
         patch.object(mod, "nvstreamer_stream_is_valid", return_value=True):
        streams = mod.fetch_all_streams_from_nvstreamer()

    names = {s["event"]["camera_name"] for s in streams}
    assert names == {"Camera_02", "Camera", "Camera_01"}
    assert clock.t > 2


def test_get_config_reads_endpoint_timeout(monkeypatch):
    import sensor_config_manager as mod

    monkeypatch.setenv("NVSTREAMER_STREAMS_ENDPOINT_TIMEOUT", "120")
    mod.refresh_config()
    assert mod.CONFIG["NVSTREAMER_STREAMS_ENDPOINT_TIMEOUT"] == 120
