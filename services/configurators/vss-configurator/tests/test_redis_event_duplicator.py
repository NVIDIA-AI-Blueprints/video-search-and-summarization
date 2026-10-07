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

"""Behavior of the Redis event duplicator after the complexity split."""
import json
from unittest.mock import MagicMock

import pytest


class RedisError(Exception):
    """Stand-in for redis.RedisError. The test suite mocks the redis package."""


class StopDuplicator(BaseException):
    """Leaves the reconnect loop. Exception would be swallowed."""


def _module(monkeypatch, tmp_path):
    monkeypatch.setenv("CALIBRATION_DIR_MOUNT_PATH", str(tmp_path))
    monkeypatch.setenv("CV_SUFFIX", "-cv")
    monkeypatch.setenv("PN_SUFFIX", "-pn")
    import sensor_config_manager as mod

    mod._config_cache.clear()
    mod.refresh_config()
    mod.redis.RedisError = RedisError
    return mod


def _payload(camera_name):
    body = json.dumps({"event": {"camera_name": camera_name}}).encode("utf-8")
    return {b"sensor.id": body}


def _camera_name(record):
    raw = record[b"sensor.id"]
    return json.loads(raw.decode("utf-8"))["event"]["camera_name"]


def test_each_target_topic_gets_its_own_suffix(monkeypatch, tmp_path):
    mod = _module(monkeypatch, tmp_path)
    original = _payload("cam")
    cv = mod._message_for_target_topic(original, "vst.event.cv")
    pn = mod._message_for_target_topic(original, "vst.event.pn26")

    assert _camera_name(cv) == "cam-cv"
    assert _camera_name(pn) == "cam-pn"
    assert _camera_name(original) == "cam"


def test_invalid_json_is_forwarded_unchanged(monkeypatch, tmp_path):
    mod = _module(monkeypatch, tmp_path)
    client = MagicMock()
    original = {b"sensor.id": b"not-json"}

    mod._forward_message_to_topic(client, original, "vst.event.cv")

    client.xadd.assert_called_once_with("vst.event.cv", original)


def test_modify_failure_forwards_the_original_message(monkeypatch, tmp_path):
    mod = _module(monkeypatch, tmp_path)
    client = MagicMock()
    original = MagicMock()
    original.copy.side_effect = RuntimeError("copy failed")

    mod._forward_message_to_topic(client, original, "vst.event.cv")

    client.xadd.assert_called_once_with("vst.event.cv", original)


def test_unexpected_message_format_is_still_published(monkeypatch, tmp_path):
    mod = _module(monkeypatch, tmp_path)
    client = MagicMock()
    original = {"other": "field"}

    mod._forward_message_to_topic(client, original, "vst.event.cv")

    published = client.xadd.call_args.args[1]
    assert published == {"other": "field"}
    assert published is not original


def test_failed_message_still_advances_the_stream_id(monkeypatch, tmp_path):
    mod = _module(monkeypatch, tmp_path)
    client = MagicMock()
    client.xadd.side_effect = RuntimeError("publish failed")
    response = [(b"vst.event", [(b"1-0", _payload("cam")), (b"2-0", _payload("next"))])]

    last_id = mod._advance_duplicated_messages(client, response, "$")

    assert last_id == b"2-0"


def test_empty_read_keeps_the_previous_stream_id(monkeypatch, tmp_path):
    mod = _module(monkeypatch, tmp_path)
    client = MagicMock()
    client.xread.return_value = []

    assert mod._read_and_duplicate_once(client, "$") == "$"
    client.xread.assert_called_once_with(
        streams={mod.CONFIG["REDIS_SOURCE_TOPIC"]: "$"},
        count=10,
        block=1000,
    )


def test_lost_redis_connection_closes_the_client_and_waits(monkeypatch, tmp_path):
    mod = _module(monkeypatch, tmp_path)
    client = MagicMock()
    client.xread.side_effect = RedisError("gone")

    with pytest.MonkeyPatch.context() as patch:
        slept = {}
        patch.setattr(mod.time, "sleep", lambda seconds: slept.setdefault("seconds", seconds))
        last_id, returned, connected = mod._duplicate_until_disconnect(client, "9-0")

    assert (last_id, returned, connected) == ("9-0", None, False)
    client.close.assert_called_once()
    assert slept["seconds"] == 5


def test_non_redis_read_error_keeps_the_connection(monkeypatch, tmp_path):
    mod = _module(monkeypatch, tmp_path)
    client = MagicMock()
    client.xread.side_effect = RuntimeError("bug")

    last_id, returned, connected = mod._duplicate_until_disconnect(client, "9-0")

    assert (last_id, returned, connected) == ("9-0", client, True)
    client.close.assert_not_called()


def test_connect_redis_error_closes_the_new_client_and_retries(monkeypatch, tmp_path):
    mod = _module(monkeypatch, tmp_path)
    client = MagicMock()
    client.ping.side_effect = RedisError("down")
    mod.redis.StrictRedis.return_value = client

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(mod.time, "sleep", lambda seconds: None)
        returned, connected, retry = mod._connect_redis_duplicator(None, False)

    assert (returned, connected, retry) == (None, False, True)
    client.close.assert_called_once()


def test_non_redis_connect_error_does_not_close_the_client(monkeypatch, tmp_path):
    mod = _module(monkeypatch, tmp_path)
    client = MagicMock()
    client.ping.side_effect = RuntimeError("bug")
    mod.redis.StrictRedis.return_value = client

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(mod.time, "sleep", lambda seconds: None)
        returned, connected, retry = mod._connect_redis_duplicator(None, False)

    assert returned is client
    assert connected is False
    assert retry is True
    client.close.assert_not_called()


def test_established_connection_is_reused(monkeypatch, tmp_path):
    mod = _module(monkeypatch, tmp_path)
    client = MagicMock()
    mod.redis.StrictRedis.reset_mock()

    returned, connected, retry = mod._connect_redis_duplicator(client, True)

    assert (returned, connected, retry) == (client, True, False)
    mod.redis.StrictRedis.assert_not_called()


def test_duplicator_publishes_one_batch_then_stops(monkeypatch, tmp_path):
    mod = _module(monkeypatch, tmp_path)
    client = MagicMock()
    client.xread.side_effect = [
        [(b"vst.event", [(b"1-0", _payload("cam"))])],
        StopDuplicator(),
    ]
    mod.redis.StrictRedis.return_value = client

    with pytest.raises(StopDuplicator):
        mod.start_redis_duplicator_thread()

    names = [_camera_name(call.args[1]) for call in client.xadd.call_args_list]
    assert names == ["cam-cv", "cam-pn"]
    client.ping.assert_called_once()
