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

import threading
from concurrent.futures import Future
from unittest.mock import Mock

from enhance_alert_with_vlm import AnomalyEnhancer


def _build_dispatch_stub(async_io_enabled: bool = True):
    stub = type("DispatchStub", (), {})()
    stub.async_io_enabled = async_io_enabled
    stub._message_dispatch_lock = threading.Lock()
    stub._message_dispatch_futures = set()
    stub.async_dispatch_max_in_flight = 2
    stub._dispatch_backpressure_semaphore = (
        threading.BoundedSemaphore(2) if async_io_enabled else None
    )
    stub.async_vlm_runtime = Mock() if async_io_enabled else None
    stub._message_dispatch_executor = Mock() if async_io_enabled else None
    stub._process_single_message = Mock()
    stub._on_dispatched_message_done = Mock()
    return stub


class TestAsyncDispatchMode:
    def test_process_single_message_submits_to_dispatch_executor(self):
        stub = _build_dispatch_stub(async_io_enabled=True)
        done_future = Future()
        stub._message_dispatch_executor.submit.return_value = done_future

        message = {"id": "msg-1", "sensorId": "sensor-1"}
        AnomalyEnhancer._process_single_message_with_mode(
            stub,
            worker_id=3,
            message=message,
        )

        stub._message_dispatch_executor.submit.assert_called_once_with(
            stub._process_single_message,
            3,
            message,
            None,
            None,
            None,
        )
        stub.async_vlm_runtime.submit_to_thread.assert_not_called()
        assert done_future in stub._message_dispatch_futures

        done_future.set_result(None)
        stub._on_dispatched_message_done.assert_called_once_with(
            done_future,
            "msg-1",
            "sensor-1",
            True,
        )

    def test_process_single_message_falls_back_when_dispatch_executor_missing(self):
        stub = _build_dispatch_stub(async_io_enabled=True)
        stub.async_vlm_runtime = None
        stub._message_dispatch_executor = None
        stub._dispatch_backpressure_semaphore = threading.BoundedSemaphore(1)

        message = {"id": "msg-2", "sensorId": "sensor-2"}
        AnomalyEnhancer._process_single_message_with_mode(
            stub,
            worker_id=5,
            message=message,
        )

        stub._process_single_message.assert_called_once_with(
            5,
            message,
            None,
            None,
            worker_assigned_at=None,
        )
        assert stub._dispatch_backpressure_semaphore.acquire(blocking=False) is True


class TestAsyncDispatchDoneCallback:
    def test_done_callback_releases_backpressure_slot_on_success(self):
        stub = type("DoneStub", (), {})()
        stub._message_dispatch_lock = threading.Lock()
        done_future = Future()
        done_future.set_result(None)
        stub._message_dispatch_futures = {done_future}
        stub._dispatch_backpressure_semaphore = threading.BoundedSemaphore(1)
        assert stub._dispatch_backpressure_semaphore.acquire(blocking=False) is True

        AnomalyEnhancer._on_dispatched_message_done(
            stub,
            done_future,
            "msg-success",
            "sensor-success",
            True,
        )

        assert done_future not in stub._message_dispatch_futures
        assert stub._dispatch_backpressure_semaphore.acquire(blocking=False) is True


class TestAsyncExternalIOMode:
    def test_get_video_stream_url_uses_async_runtime_when_enabled(self):
        stub = type("VSTStub", (), {})()
        stub.async_vst_enabled = True
        stub.async_vlm_runtime = Mock()
        stub.async_external_timeout_seconds = 5.0
        stub._vst_handler = Mock()
        stub._observe_async_external_io = Mock()

        done_future = Future()
        done_future.set_result(("http://vst/video.mp4", "start", "end"))
        stub.async_vlm_runtime.submit_to_thread.return_value = done_future

        result = AnomalyEnhancer._get_video_stream_url_with_mode(
            stub,
            "sensor-1",
            "ts-start",
            "ts-end",
            objects_ids=["o1"],
            latency={},
        )

        stub.async_vlm_runtime.submit_to_thread.assert_called_once_with(
            stub._vst_handler.get_video_stream_url,
            "sensor-1",
            "ts-start",
            "ts-end",
            objects_ids=["o1"],
            latency={},
        )
        assert result == ("http://vst/video.mp4", "start", "end")

    def test_submit_sink_operation_async_mode_tracks_future(self):
        stub = type("SinkStub", (), {})()
        stub._is_async_elastic_sink_mode_enabled = Mock(return_value=True)
        stub.async_vlm_runtime = Mock()
        stub._sink_async_lock = threading.Lock()
        stub._sink_async_futures = set()
        stub.async_sink_warn_in_flight = 100
        stub._observe_async_external_io = Mock()
        stub._count_async_external_fallback = Mock()
        stub._on_async_sink_operation_done = Mock()

        done_future = Future()
        stub.async_vlm_runtime.submit_to_thread.return_value = done_future

        operation_fn = Mock()
        message = {"id": "msg-async", "sensorId": "sensor-async"}

        returned = AnomalyEnhancer._submit_sink_operation_with_mode(
            stub,
            "publish_success",
            operation_fn,
            message,
            "user prompt",
            "system prompt",
            "response",
        )

        assert returned is done_future
        operation_fn.assert_not_called()
        assert done_future in stub._sink_async_futures

        done_future.set_result(None)
        stub._on_async_sink_operation_done.assert_called_once()
        callback_args = stub._on_async_sink_operation_done.call_args[0]
        assert callback_args[0] is done_future
        assert callback_args[1] == "publish_success"
        assert callback_args[2] == "msg-async"
        assert callback_args[3] == "sensor-async"
        assert isinstance(callback_args[4], float)

    def test_submit_sink_operation_sync_fallback(self):
        stub = type("SinkStub", (), {})()
        stub._is_async_elastic_sink_mode_enabled = Mock(return_value=False)
        stub._observe_async_external_io = Mock()
        stub._count_async_external_fallback = Mock()

        operation_fn = Mock()
        message = {"id": "msg-sync", "sensorId": "sensor-sync"}

        returned = AnomalyEnhancer._submit_sink_operation_with_mode(
            stub,
            "publish_error",
            operation_fn,
            message,
            "user prompt",
            "system prompt",
            {},
        )

        assert returned is None
        operation_fn.assert_called_once_with(
            message,
            "user prompt",
            "system prompt",
            {},
        )

    def test_submit_sink_operation_uses_message_snapshot(self):
        stub = type("SinkStub", (), {})()
        stub._is_async_elastic_sink_mode_enabled = Mock(return_value=False)
        stub._observe_async_external_io = Mock()
        stub._count_async_external_fallback = Mock()

        captured_payloads = []

        def operation_fn(payload, *_):
            captured_payloads.append(payload)
            payload.setdefault("info", {})["sink_mutated"] = True

        message = {
            "id": "msg-snapshot",
            "sensorId": "sensor-snapshot",
            "info": {"verdict": "normal"},
        }

        returned = AnomalyEnhancer._submit_sink_operation_with_mode(
            stub,
            "publish_success",
            operation_fn,
            message,
            "user prompt",
            "system prompt",
            "response",
        )

        assert returned is None
        assert len(captured_payloads) == 1
        assert captured_payloads[0] is not message
        assert "sink_mutated" not in message["info"]

    def test_run_redis_operation_uses_async_runtime_when_enabled(self):
        stub = type("RedisStub", (), {})()
        stub._is_async_redis_mode_enabled = Mock(return_value=True)
        stub.async_vlm_runtime = Mock()
        stub.async_external_timeout_seconds = 5.0
        stub._observe_async_external_io = Mock()
        stub._count_async_external_fallback = Mock()

        done_future = Future()
        done_future.set_result(["msg-1"])
        stub.async_vlm_runtime.submit_to_thread.return_value = done_future

        operation_fn = Mock(return_value=["sync-msg"])
        result = AnomalyEnhancer._run_redis_operation_with_mode(
            stub,
            "filter_new_events_dedup",
            operation_fn,
            [{"id": "msg-1"}],
            verify_only_finished_events=True,
        )

        assert result == ["msg-1"]
        stub.async_vlm_runtime.submit_to_thread.assert_called_once_with(
            operation_fn,
            [{"id": "msg-1"}],
            verify_only_finished_events=True,
        )
        operation_fn.assert_not_called()

    def test_run_redis_operation_falls_back_to_sync_when_async_submit_fails(self):
        stub = type("RedisStub", (), {})()
        stub._is_async_redis_mode_enabled = Mock(return_value=True)
        stub.async_vlm_runtime = Mock()
        stub.async_external_timeout_seconds = 5.0
        stub._observe_async_external_io = Mock()
        stub._count_async_external_fallback = Mock()
        stub.async_vlm_runtime.submit_to_thread.side_effect = RuntimeError("submit failed")

        operation_fn = Mock(return_value=["sync-msg"])
        result = AnomalyEnhancer._run_redis_operation_with_mode(
            stub,
            "filter_by_end_time_delta",
            operation_fn,
            [{"id": "msg-sync"}],
        )

        assert result == ["sync-msg"]
        operation_fn.assert_called_once_with([{"id": "msg-sync"}])

    def test_set_message_id_and_should_skip_uses_redis_mode_helper(self):
        stub = type("SkipStub", (), {})()
        stub.redis_handler = Mock()
        stub._compute_fingerprint = Mock(return_value="fp-1")
        stub._run_redis_operation_with_mode = Mock(return_value=True)

        message = {"id": "msg-1", "sensorId": "sensor-1"}
        should_skip = AnomalyEnhancer._set_message_id_and_should_skip(
            stub,
            message,
            "sensor-1",
        )

        assert should_skip is True
        assert message["Id"] == "fp-1"
        stub._run_redis_operation_with_mode.assert_called_once_with(
            "is_verdict_confirmed",
            stub.redis_handler.is_verdict_confirmed,
            "fp-1",
        )

    def test_done_callback_releases_backpressure_slot_on_failure(self):
        stub = type("DoneStub", (), {})()
        stub._message_dispatch_lock = threading.Lock()
        done_future = Future()
        done_future.set_exception(RuntimeError("boom"))
        stub._message_dispatch_futures = {done_future}
        stub._dispatch_backpressure_semaphore = threading.BoundedSemaphore(1)
        assert stub._dispatch_backpressure_semaphore.acquire(blocking=False) is True

        AnomalyEnhancer._on_dispatched_message_done(
            stub,
            done_future,
            "msg-failure",
            "sensor-failure",
            True,
        )

        assert done_future not in stub._message_dispatch_futures
        assert stub._dispatch_backpressure_semaphore.acquire(blocking=False) is True
