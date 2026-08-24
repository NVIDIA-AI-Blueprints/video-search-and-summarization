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
Unit tests for Nvstreamer stream discovery.

Nvstreamer registers video files serially, so its streams endpoint advertises a
partial list for a short window after startup. Discovery used to accept the first
non-empty reply, which permanently dropped whichever cameras had not registered
yet while still logging success.
"""
import pytest
from unittest.mock import MagicMock, patch


@pytest.fixture(autouse=True)
def populated_config(monkeypatch, tmp_path):
    """
    Point calibration at tmp_path and make sure CONFIG is populated.

    CONFIG is the same dict object as _config_cache, so a sibling test module
    clearing the cache leaves the endpoint lookups here raising KeyError.
    """
    monkeypatch.setenv("CALIBRATION_DIR_MOUNT_PATH", str(tmp_path))
    import sensor_config_manager as mod
    mod.refresh_config()
    yield
    mod._config_cache.clear()


@pytest.fixture
def no_sleep():
    """Drop the settle and retry delays so the tests run instantly."""
    import sensor_config_manager as mod
    with patch.object(mod.time, "sleep", MagicMock()) as sleep:
        yield sleep


def streams(*names):
    """Build an Nvstreamer streams-endpoint payload for the given cameras."""
    return [
        {
            f"{name}_0": [
                {
                    "isMain": True,
                    "name": name,
                    "streamId": f"{name}_0",
                    "url": f"rtsp://nvstreamer:31554/{name}.mp4",
                    "metadata": {},
                }
            ]
        }
        for name in names
    ]


def discover(mod, replies):
    """Run discovery against a scripted sequence of endpoint reads."""
    with patch.object(mod, "read_nvstreamer_streams", side_effect=replies) as read:
        with patch.object(mod, "nvstreamer_stream_is_valid", return_value=True):
            return mod.fetch_all_streams_from_nvstreamer(), read


def names_of(result):
    return sorted(entry["event"]["camera_name"] for entry in result)


def test_partial_list_is_not_registered(no_sleep):
    """
    The reported bug: Nvstreamer advertises 2 of 3 streams and discovery used to
    register those 2 and never reconcile. It has to keep reading until the third
    camera shows up.
    """
    import sensor_config_manager as mod
    result, read = discover(mod, [
        streams("Camera_02", "Camera"),
        streams("Camera_02", "Camera", "Camera_01"),
        streams("Camera_02", "Camera", "Camera_01"),
    ])
    assert names_of(result) == ["Camera", "Camera_01", "Camera_02"]
    assert read.call_count == 3


def test_a_steady_count_is_accepted(no_sleep):
    """Two reads that agree end the wait, so startup is not delayed further."""
    import sensor_config_manager as mod
    result, read = discover(mod, [streams("a", "b"), streams("a", "b")])
    assert names_of(result) == ["a", "b"]
    assert read.call_count == 2


def test_a_growing_count_keeps_waiting(no_sleep):
    """Every change restarts the wait, so a list still filling up cannot settle."""
    import sensor_config_manager as mod
    result, read = discover(mod, [
        streams("a"),
        streams("a", "b"),
        streams("a", "b", "c"),
        streams("a", "b", "c"),
    ])
    assert names_of(result) == ["a", "b", "c"]
    assert read.call_count == 4


def test_endpoint_failure_does_not_count_as_agreement(no_sleep):
    """A flapping endpoint must not be mistaken for a settled stream list."""
    import sensor_config_manager as mod
    result, read = discover(mod, [
        streams("a", "b"),
        None,
        streams("a", "b"),
        streams("a", "b"),
    ])
    assert names_of(result) == ["a", "b"]
    assert read.call_count == 4


def test_unavailable_endpoint_never_registers_zero_cameras(no_sleep):
    """
    Waiting for Nvstreamer to come up stays unbounded, so an endpoint that is not
    ready yet must not be committed as an empty camera list.
    """
    import sensor_config_manager as mod
    result, read = discover(mod, [None, None, streams("a"), streams("a")])
    assert names_of(result) == ["a"]
    assert read.call_count == 4


def test_streams_that_never_come_online_are_named(no_sleep):
    """Cameras dropped by the online check are reported, not silently lost."""
    import sensor_config_manager as mod
    with patch.object(mod, "read_nvstreamer_streams", return_value=streams("a", "b")):
        with patch.object(mod, "nvstreamer_stream_is_valid", side_effect=lambda name: name != "b"):
            with patch.object(mod, "logger") as log:
                result = mod.fetch_all_streams_from_nvstreamer()
    assert names_of(result) == ["a"]
    assert log.warning.called
    assert "b" in log.warning.call_args[0][0]


def test_empty_reply_is_treated_as_not_ready():
    """read_nvstreamer_streams() reports an empty 200 as not ready rather than done."""
    import sensor_config_manager as mod
    resp = MagicMock(status_code=200)
    resp.json.return_value = []
    with patch.object(mod.requests, "get", return_value=resp):
        assert mod.read_nvstreamer_streams() is None


def test_non_200_reply_is_treated_as_not_ready():
    """A non-200 status is reported as not ready."""
    import sensor_config_manager as mod
    resp = MagicMock(status_code=503)
    with patch.object(mod.requests, "get", return_value=resp):
        assert mod.read_nvstreamer_streams() is None


def test_usable_reply_is_returned():
    """A 200 carrying streams is passed straight back."""
    import sensor_config_manager as mod
    resp = MagicMock(status_code=200)
    resp.json.return_value = streams("a", "b")
    with patch.object(mod.requests, "get", return_value=resp):
        assert len(mod.read_nvstreamer_streams()) == 2
