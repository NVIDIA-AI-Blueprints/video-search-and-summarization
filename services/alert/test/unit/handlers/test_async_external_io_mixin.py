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

"""Unit tests for ``handlers.async_external_io_mixin``.

This mixin decides, per external call, whether work runs inline on the worker
thread or is offloaded to the async VLM runtime. Every one of its branches
changes either latency or correctness, so they are covered exhaustively:

* **Dedup/state calls degrade, never fail.** If the async submit times out or
  raises, the operation is re-run synchronously. Dropping it instead would
  lose the dedup decision for that event.
* **Sink publishes are fire-and-forget only in async mode.** The message is
  deep-copied before submission, because the caller mutates the dict after
  the call returns and the queued task would otherwise observe a later state.
* **Enrichment is chained, not raced.** When a publish future exists, the
  enrichment update is scheduled on its completion — and skipped entirely if
  the publish was cancelled or failed, since there would be no document to
  update.
* **VST timeouts are translated.** ``_get_video_stream_url_with_mode`` raises
  ``VSTTimeoutError`` rather than a bare ``FutureTimeoutError`` so the caller's
  existing VST error handling applies in async mode too.

``Future`` objects are real ``concurrent.futures`` instances driven manually,
so callbacks fire deterministically without any background threads.
"""

import threading
from concurrent.futures import Future, TimeoutError as FutureTimeoutError
from unittest.mock import MagicMock, patch

import pytest

from handlers.async_external_io_mixin import AsyncExternalIOMixin
from schemas.vlm_responses import EnrichmentResponse
from vst.exceptions import VSTTimeoutError

MESSAGE = {"Id": "fingerprint-1", "id": "inc-1", "sensorId": "cam-1"}


class Handler(AsyncExternalIOMixin):
    """Minimal host object providing the attributes the mixin reads."""

    def __init__(self, **overrides):
        self.async_io_enabled = False
        self.async_vlm_runtime = None
        self.redis_handler = MagicMock()
        self._vlm_sink_type = "elastic"
        self.async_external_timeout_seconds = 5
        self.async_sink_warn_in_flight = 10
        self._sink_async_lock = threading.Lock()
        self._sink_async_futures = set()
        self.vlm_enhanced_event_sink = MagicMock()
        self._vst_handler = MagicMock()
        self.__dict__.update(overrides)


def completed_future(result=None, exception=None, cancelled=False):
    future = Future()
    if cancelled:
        future.cancel()
        return future
    future.set_running_or_notify_cancel()
    if exception is not None:
        future.set_exception(exception)
    else:
        future.set_result(result)
    return future


def make_runtime(future=None):
    runtime = MagicMock()
    runtime.submit_to_thread.return_value = future if future is not None else completed_future("ok")
    return runtime


class TestModeGates:
    def test_redis_mode_requires_all_three_conditions(self):
        assert Handler()._is_async_redis_mode_enabled() is False
        assert Handler(async_io_enabled=True)._is_async_redis_mode_enabled() is False
        assert Handler(
            async_io_enabled=True, async_vlm_runtime=MagicMock()
        )._is_async_redis_mode_enabled() is True

    def test_redis_mode_is_off_without_a_handler(self):
        handler = Handler(
            async_io_enabled=True, async_vlm_runtime=MagicMock(), redis_handler=None
        )
        assert handler._is_async_redis_mode_enabled() is False

    def test_elastic_sink_mode_requires_the_elastic_sink(self):
        handler = Handler(
            async_io_enabled=True, async_vlm_runtime=MagicMock(), _vlm_sink_type="kafka"
        )
        assert handler._is_async_elastic_sink_mode_enabled() is False

    def test_elastic_sink_mode_enabled(self):
        handler = Handler(async_io_enabled=True, async_vlm_runtime=MagicMock())
        assert handler._is_async_elastic_sink_mode_enabled() is True


class TestRunRedisOperationWithMode:
    def test_sync_mode_calls_the_operation_inline(self):
        handler = Handler()
        operation = MagicMock(return_value="result")

        assert handler._run_redis_operation_with_mode("dedup", operation, 1, k=2) == "result"
        operation.assert_called_once_with(1, k=2)

    def test_sync_mode_propagates_errors(self):
        handler = Handler()
        operation = MagicMock(side_effect=RuntimeError("state error"))

        with pytest.raises(RuntimeError, match="state error"):
            handler._run_redis_operation_with_mode("dedup", operation)

    def test_async_mode_offloads_to_the_runtime(self):
        runtime = make_runtime(completed_future("async-result"))
        handler = Handler(async_io_enabled=True, async_vlm_runtime=runtime)
        operation = MagicMock()

        result = handler._run_redis_operation_with_mode("dedup", operation, 1, k=2)

        assert result == "async-result"
        runtime.submit_to_thread.assert_called_once_with(operation, 1, k=2)
        operation.assert_not_called()

    def test_timeout_falls_back_to_a_sync_call(self):
        future = MagicMock()
        future.result.side_effect = FutureTimeoutError()
        runtime = make_runtime(future)
        handler = Handler(async_io_enabled=True, async_vlm_runtime=runtime)
        operation = MagicMock(return_value="fallback")

        assert handler._run_redis_operation_with_mode("dedup", operation) == "fallback"
        future.cancel.assert_called_once()
        operation.assert_called_once()

    def test_async_error_falls_back_to_a_sync_call(self):
        runtime = make_runtime(completed_future(exception=RuntimeError("worker died")))
        handler = Handler(async_io_enabled=True, async_vlm_runtime=runtime)
        operation = MagicMock(return_value="fallback")

        assert handler._run_redis_operation_with_mode("dedup", operation) == "fallback"

    def test_submit_failure_falls_back_to_a_sync_call(self):
        runtime = MagicMock()
        runtime.submit_to_thread.side_effect = RuntimeError("runtime stopping")
        handler = Handler(async_io_enabled=True, async_vlm_runtime=runtime)
        operation = MagicMock(return_value="fallback")

        assert handler._run_redis_operation_with_mode("dedup", operation) == "fallback"

    def test_a_failing_fallback_propagates(self):
        runtime = make_runtime(completed_future(exception=RuntimeError("worker died")))
        handler = Handler(async_io_enabled=True, async_vlm_runtime=runtime)
        operation = MagicMock(side_effect=RuntimeError("state error"))

        with pytest.raises(RuntimeError, match="state error"):
            handler._run_redis_operation_with_mode("dedup", operation)

    def test_the_configured_timeout_is_used(self):
        future = MagicMock()
        future.result.return_value = "ok"
        runtime = make_runtime(future)
        handler = Handler(
            async_io_enabled=True, async_vlm_runtime=runtime, async_external_timeout_seconds=2
        )

        handler._run_redis_operation_with_mode("dedup", MagicMock())

        assert future.result.call_args.kwargs["timeout"] == 2


class TestSubmitSinkOperationWithMode:
    def test_sync_mode_calls_the_sink_inline_and_returns_none(self):
        handler = Handler()
        operation = MagicMock()

        assert handler._submit_sink_operation_with_mode("publish", operation, MESSAGE, "a") is None
        operation.assert_called_once()

    def test_sync_mode_propagates_errors(self):
        handler = Handler()
        operation = MagicMock(side_effect=RuntimeError("sink down"))

        with pytest.raises(RuntimeError, match="sink down"):
            handler._submit_sink_operation_with_mode("publish", operation, MESSAGE)

    def test_the_message_is_snapshotted_before_submission(self):
        """The caller mutates the dict after this returns."""
        handler = Handler()
        operation = MagicMock()
        message = dict(MESSAGE)

        handler._submit_sink_operation_with_mode("publish", operation, message)
        snapshot = operation.call_args.args[0]
        message["sensorId"] = "changed"

        assert snapshot["sensorId"] == "cam-1"
        assert snapshot is not message

    def test_an_undeepcopyable_message_falls_back_to_a_shallow_copy(self):
        handler = Handler()
        operation = MagicMock()

        with patch("copy.deepcopy", side_effect=TypeError("cannot pickle")):
            handler._submit_sink_operation_with_mode("publish", operation, MESSAGE)

        assert operation.call_args.args[0] == MESSAGE

    def test_async_mode_returns_the_future_and_tracks_it(self):
        future = Future()
        runtime = make_runtime(future)
        handler = Handler(async_io_enabled=True, async_vlm_runtime=runtime)

        result = handler._submit_sink_operation_with_mode("publish", MagicMock(), MESSAGE)

        assert result is future
        assert future in handler._sink_async_futures

    def test_the_future_is_untracked_once_it_completes(self):
        future = Future()
        runtime = make_runtime(future)
        handler = Handler(async_io_enabled=True, async_vlm_runtime=runtime)

        handler._submit_sink_operation_with_mode("publish", MagicMock(), MESSAGE)
        future.set_running_or_notify_cancel()
        future.set_result(None)

        assert handler._sink_async_futures == set()

    def test_extra_arguments_are_forwarded(self):
        runtime = make_runtime(Future())
        handler = Handler(async_io_enabled=True, async_vlm_runtime=runtime)
        operation = MagicMock()

        handler._submit_sink_operation_with_mode("publish", operation, MESSAGE, "prompt", None)

        args = runtime.submit_to_thread.call_args.args
        assert args[0] is operation
        assert args[2:] == ("prompt", None)

    def test_submit_failure_falls_back_to_a_sync_call(self):
        runtime = MagicMock()
        runtime.submit_to_thread.side_effect = RuntimeError("runtime stopping")
        handler = Handler(async_io_enabled=True, async_vlm_runtime=runtime)
        operation = MagicMock()

        assert handler._submit_sink_operation_with_mode("publish", operation, MESSAGE) is None
        operation.assert_called_once()

    def test_a_failing_fallback_propagates(self):
        runtime = MagicMock()
        runtime.submit_to_thread.side_effect = RuntimeError("runtime stopping")
        handler = Handler(async_io_enabled=True, async_vlm_runtime=runtime)
        operation = MagicMock(side_effect=RuntimeError("sink down"))

        with pytest.raises(RuntimeError, match="sink down"):
            handler._submit_sink_operation_with_mode("publish", operation, MESSAGE)

    def test_the_in_flight_threshold_warns(self, caplog):
        runtime = make_runtime(Future())
        handler = Handler(
            async_io_enabled=True, async_vlm_runtime=runtime, async_sink_warn_in_flight=1
        )

        with caplog.at_level("WARNING"):
            handler._submit_sink_operation_with_mode("publish", MagicMock(), MESSAGE)

        assert "in-flight operations reached warning threshold" in caplog.text

    def test_no_warning_below_the_threshold(self, caplog):
        runtime = make_runtime(Future())
        handler = Handler(
            async_io_enabled=True, async_vlm_runtime=runtime, async_sink_warn_in_flight=99
        )

        with caplog.at_level("WARNING"):
            handler._submit_sink_operation_with_mode("publish", MagicMock(), MESSAGE)

        assert "warning threshold" not in caplog.text

    def test_a_message_without_ids_is_tolerated(self):
        handler = Handler()
        handler._submit_sink_operation_with_mode("publish", MagicMock(), {})


class TestOnAsyncSinkOperationDone:
    def _handler(self):
        return Handler(async_io_enabled=True, async_vlm_runtime=make_runtime())

    def test_a_completed_future_is_discarded(self):
        handler = self._handler()
        future = completed_future("ok")
        handler._sink_async_futures.add(future)

        handler._on_async_sink_operation_done(future, "publish", "m-1", "cam-1", 0.0)

        assert future not in handler._sink_async_futures

    def test_a_cancelled_future_is_reported(self, caplog):
        handler = self._handler()
        future = completed_future(cancelled=True)

        with caplog.at_level("WARNING"):
            handler._on_async_sink_operation_done(future, "publish", "m-1", "cam-1")

        assert "Async sink operation cancelled" in caplog.text

    def test_a_failed_future_is_reported(self, caplog):
        handler = self._handler()
        future = completed_future(exception=RuntimeError("sink down"))

        with caplog.at_level("ERROR"):
            handler._on_async_sink_operation_done(future, "publish", "m-1", "cam-1")

        assert "Async sink operation failed" in caplog.text

    def test_a_successful_future_is_not_logged_as_an_error(self, caplog):
        handler = self._handler()

        with caplog.at_level("ERROR"):
            handler._on_async_sink_operation_done(completed_future("ok"), "publish", "m-1", "cam-1")

        assert caplog.text == ""

    def test_an_untracked_future_is_tolerated(self):
        handler = self._handler()
        handler._on_async_sink_operation_done(completed_future("ok"), "publish", "m-1", "cam-1")


class TestPublishHelpers:
    def test_publish_success_delegates_to_the_sink(self):
        handler = Handler()

        handler._publish_success_with_mode(MESSAGE, "user", "system", "content")

        handler.vlm_enhanced_event_sink.publish_success.assert_called_once()
        args = handler.vlm_enhanced_event_sink.publish_success.call_args.args
        assert args[1:] == ("user", "system", "content")

    def test_publish_error_delegates_to_the_sink(self):
        handler = Handler()
        payload = {"error": "VLM timeout"}

        handler._publish_error_with_mode(MESSAGE, "user", None, payload)

        args = handler.vlm_enhanced_event_sink.publish_error.call_args.args
        assert args[1:] == ("user", None, payload)


class TestUpdateEnrichmentWithMode:
    ENRICHMENT = EnrichmentResponse(reasoning="two vehicles", response_code=200, response_status="OK")

    def test_a_sink_without_enrichment_support_is_skipped(self):
        handler = Handler()
        del handler.vlm_enhanced_event_sink.update_enrichment

        assert handler._update_enrichment_with_mode(MESSAGE, self.ENRICHMENT) is None

    def test_without_a_publish_future_the_update_runs_immediately(self):
        handler = Handler()

        handler._update_enrichment_with_mode(MESSAGE, self.ENRICHMENT)

        handler.vlm_enhanced_event_sink.update_enrichment.assert_called_once()

    def test_with_a_publish_future_the_update_is_chained(self):
        handler = Handler()
        publish_future = Future()

        result = handler._update_enrichment_with_mode(MESSAGE, self.ENRICHMENT, publish_future)

        assert result is publish_future
        handler.vlm_enhanced_event_sink.update_enrichment.assert_not_called()

        publish_future.set_running_or_notify_cancel()
        publish_future.set_result(None)
        handler.vlm_enhanced_event_sink.update_enrichment.assert_called_once()

    def test_a_cancelled_publish_skips_the_update(self, caplog):
        handler = Handler()
        publish_future = Future()
        handler._update_enrichment_with_mode(MESSAGE, self.ENRICHMENT, publish_future)

        with caplog.at_level("WARNING"):
            publish_future.cancel()

        handler.vlm_enhanced_event_sink.update_enrichment.assert_not_called()
        assert "sink publish was cancelled" in caplog.text

    def test_a_failed_publish_skips_the_update(self, caplog):
        handler = Handler()
        publish_future = Future()
        handler._update_enrichment_with_mode(MESSAGE, self.ENRICHMENT, publish_future)

        with caplog.at_level("WARNING"):
            publish_future.set_running_or_notify_cancel()
            publish_future.set_exception(RuntimeError("sink down"))

        handler.vlm_enhanced_event_sink.update_enrichment.assert_not_called()
        assert "sink publish failed" in caplog.text


class TestGetVideoStreamUrlWithMode:
    def test_sync_mode_calls_vst_inline(self):
        handler = Handler()
        handler._vst_handler.get_video_stream_url.return_value = "http://vst/clip.mp4"

        result = handler._get_video_stream_url_with_mode("cam-1", "t0", "t1", quality="high")

        assert result == "http://vst/clip.mp4"
        handler._vst_handler.get_video_stream_url.assert_called_once_with(
            "cam-1", "t0", "t1", quality="high"
        )

    def test_sync_errors_propagate(self):
        handler = Handler()
        handler._vst_handler.get_video_stream_url.side_effect = RuntimeError("vst down")

        with pytest.raises(RuntimeError, match="vst down"):
            handler._get_video_stream_url_with_mode("cam-1", "t0", "t1")

    def test_async_mode_offloads_to_the_runtime(self):
        runtime = make_runtime(completed_future("http://vst/clip.mp4"))
        handler = Handler(async_io_enabled=True, async_vlm_runtime=runtime)

        result = handler._get_video_stream_url_with_mode("cam-1", "t0", "t1")

        assert result == "http://vst/clip.mp4"
        handler._vst_handler.get_video_stream_url.assert_not_called()

    def test_a_timeout_is_translated_to_vst_timeout_error(self):
        future = MagicMock()
        future.result.side_effect = FutureTimeoutError()
        runtime = make_runtime(future)
        handler = Handler(async_io_enabled=True, async_vlm_runtime=runtime)

        with pytest.raises(VSTTimeoutError):
            handler._get_video_stream_url_with_mode("cam-1", "t0", "t1")

        future.cancel.assert_called_once()

    def test_async_mode_does_not_fall_back_on_timeout(self):
        """A VST timeout is surfaced, not silently retried inline."""
        future = MagicMock()
        future.result.side_effect = FutureTimeoutError()
        runtime = make_runtime(future)
        handler = Handler(async_io_enabled=True, async_vlm_runtime=runtime)

        with pytest.raises(VSTTimeoutError):
            handler._get_video_stream_url_with_mode("cam-1", "t0", "t1")

        handler._vst_handler.get_video_stream_url.assert_not_called()

    def test_other_async_errors_propagate_unchanged(self):
        runtime = make_runtime(completed_future(exception=RuntimeError("vst 500")))
        handler = Handler(async_io_enabled=True, async_vlm_runtime=runtime)

        with pytest.raises(RuntimeError, match="vst 500"):
            handler._get_video_stream_url_with_mode("cam-1", "t0", "t1")

    def test_async_mode_needs_a_runtime(self):
        handler = Handler(async_io_enabled=True, async_vlm_runtime=None)
        handler._vst_handler.get_video_stream_url.return_value = "http://vst/clip.mp4"

        assert handler._get_video_stream_url_with_mode("cam-1", "t0", "t1") == (
            "http://vst/clip.mp4"
        )

    def test_kwargs_reach_the_runtime_submission(self):
        runtime = make_runtime(completed_future("url"))
        handler = Handler(async_io_enabled=True, async_vlm_runtime=runtime)

        handler._get_video_stream_url_with_mode("cam-1", "t0", "t1", quality="low")

        assert runtime.submit_to_thread.call_args.kwargs == {"quality": "low"}
