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

import pytest

from handlers.async_dispatch_mixin import (
    AsyncDispatchMixin,
    PIPELINE_MODE_EVENT_LOOP,
    PIPELINE_MODE_SYNC,
    PIPELINE_MODE_THREAD_BRIDGE,
    resolve_pipeline_mode,
)


class _DispatchStub(AsyncDispatchMixin):
    def __init__(self, pipeline_mode=PIPELINE_MODE_EVENT_LOOP, max_in_flight=2):
        self.pipeline_mode = pipeline_mode
        self._message_dispatch_lock = threading.Lock()
        self._message_dispatch_futures = set()
        self.async_dispatch_max_in_flight = max_in_flight
        self._dispatch_backpressure_semaphore = (
            threading.BoundedSemaphore(max_in_flight)
            if pipeline_mode != PIPELINE_MODE_SYNC
            else None
        )
        self.async_vlm_runtime = Mock()
        self._message_dispatch_executor = None
        self._process_single_message = Mock()
        self._async_coroutines = []

    def _process_single_message_async(self, *args, **kwargs):
        marker = Mock(name="coroutine")
        self._async_coroutines.append((args, kwargs))
        return marker


class TestResolvePipelineMode:
    def test_explicit_mode_wins_over_legacy_flag(self):
        assert resolve_pipeline_mode("event_loop", False) == PIPELINE_MODE_EVENT_LOOP
        assert resolve_pipeline_mode("sync", True) == PIPELINE_MODE_SYNC
        assert resolve_pipeline_mode("THREAD_BRIDGE", False) == PIPELINE_MODE_THREAD_BRIDGE

    def test_unset_mode_derives_from_legacy_flag(self):
        assert resolve_pipeline_mode(None, False) == PIPELINE_MODE_SYNC
        assert resolve_pipeline_mode(None, True) == PIPELINE_MODE_THREAD_BRIDGE

    def test_invalid_mode_raises_naming_valid_options(self):
        with pytest.raises(ValueError) as exc_info:
            resolve_pipeline_mode("turbo", True)
        message = str(exc_info.value)
        assert "turbo" in message
        for valid in ("sync", "thread_bridge", "event_loop"):
            assert valid in message


class TestEffectivePipelineMode:
    def test_stub_without_pipeline_mode_attr_uses_legacy_flag(self):
        stub = type("LegacyStub", (), {})()
        stub.async_io_enabled = True
        assert AsyncDispatchMixin._effective_pipeline_mode(stub) == PIPELINE_MODE_THREAD_BRIDGE
        stub.async_io_enabled = False
        assert AsyncDispatchMixin._effective_pipeline_mode(stub) == PIPELINE_MODE_SYNC


class TestEventLoopDispatch:
    def test_submits_coroutine_to_runtime_and_tracks_future(self):
        stub = _DispatchStub()
        done_future = Future()
        stub.async_vlm_runtime.submit_coroutine.return_value = done_future

        message = {"id": "msg-1", "sensorId": "sensor-1"}
        stub._process_single_message_with_mode(worker_id=3, message=message)

        stub.async_vlm_runtime.submit_coroutine.assert_called_once()
        assert done_future in stub._message_dispatch_futures
        assert len(stub._async_coroutines) == 1
        args, _ = stub._async_coroutines[0]
        assert args[0] == 3
        assert args[1] is message
        # task_dispatched_at stamped as the last positional argument
        assert isinstance(args[5], str)
        stub._process_single_message.assert_not_called()

        # Done callback releases the backpressure slot and untracks the future
        done_future.set_result(None)
        assert done_future not in stub._message_dispatch_futures
        assert stub._dispatch_backpressure_semaphore.acquire(blocking=False)
        assert stub._dispatch_backpressure_semaphore.acquire(blocking=False)
        assert not stub._dispatch_backpressure_semaphore.acquire(blocking=False)

    def test_falls_back_inline_when_runtime_missing(self):
        stub = _DispatchStub()
        stub.async_vlm_runtime = None

        message = {"id": "msg-2", "sensorId": "sensor-2"}
        stub._process_single_message_with_mode(worker_id=1, message=message)

        stub._process_single_message.assert_called_once()
        assert not stub._message_dispatch_futures
        # Slot released on fallback
        assert stub._dispatch_backpressure_semaphore.acquire(blocking=False)
        assert stub._dispatch_backpressure_semaphore.acquire(blocking=False)

    def test_falls_back_inline_when_submit_raises(self):
        stub = _DispatchStub()
        stub.async_vlm_runtime.submit_coroutine.side_effect = RuntimeError("loop down")

        message = {"id": "msg-3", "sensorId": "sensor-3"}
        stub._process_single_message_with_mode(worker_id=1, message=message)

        stub._process_single_message.assert_called_once()
        assert not stub._message_dispatch_futures
        assert stub._dispatch_backpressure_semaphore.acquire(blocking=False)
        assert stub._dispatch_backpressure_semaphore.acquire(blocking=False)

    def test_sync_mode_processes_inline(self):
        stub = _DispatchStub(pipeline_mode=PIPELINE_MODE_SYNC)

        message = {"id": "msg-4", "sensorId": "sensor-4"}
        stub._process_single_message_with_mode(worker_id=1, message=message)

        stub._process_single_message.assert_called_once()
        stub.async_vlm_runtime.submit_coroutine.assert_not_called()

    def test_backpressure_blocks_third_dispatch_until_slot_frees(self):
        stub = _DispatchStub(max_in_flight=2)
        first, second, third = Future(), Future(), Future()
        stub.async_vlm_runtime.submit_coroutine.side_effect = [first, second, third]

        stub._process_single_message_with_mode(worker_id=1, message={"id": "m1", "sensorId": "s"})
        stub._process_single_message_with_mode(worker_id=1, message={"id": "m2", "sensorId": "s"})
        assert not stub._dispatch_backpressure_semaphore.acquire(blocking=False)

        blocked_done = threading.Event()

        def _third_dispatch():
            stub._process_single_message_with_mode(worker_id=1, message={"id": "m3", "sensorId": "s"})
            blocked_done.set()

        blocker = threading.Thread(target=_third_dispatch, daemon=True)
        blocker.start()
        # Completing one in-flight message frees the slot the blocked
        # dispatcher is waiting on.
        first.set_result(None)
        assert blocked_done.wait(timeout=10)
        blocker.join(timeout=10)
        assert stub.async_vlm_runtime.submit_coroutine.call_count == 3
