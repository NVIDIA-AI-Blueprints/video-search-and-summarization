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

"""Unit tests for ``handlers.event_loop_pipeline_mixin``.

In event_loop mode every in-flight message is a coroutine on one persistent
loop, so anything that blocks or leaks here degrades the whole service rather
than one worker thread. The behaviours covered:

* **Capacity slots are always released.** ``_capacity_slot`` decrements the
  in-flight gauge and releases the semaphore in a ``finally``; a leaked permit
  would permanently shrink VLM/VST concurrency. A ``None`` semaphore is a
  no-op pass-through so the unbounded configuration stays free of bookkeeping.
* **VST resolution emits exactly one duration sample per event**, even when
  the no-overlay retry runs — double-counting would corrupt the latency
  histogram. The retry also clears the captured error on success, so a
  recovered event is not published as a failure.
* **Shutdown closes clients in dependency order** and tolerates a failure in
  any one of them, so one stuck client cannot block the rest.
* **Async sink paths fall back off-loop.** Sinks without ``*_async`` methods
  are driven through ``asyncio.to_thread`` rather than being skipped.

All I/O is mocked; no loop-blocking sleep and no socket.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from handlers.event_loop_pipeline_mixin import EventLoopPipelineMixin
from vst.exceptions import VSTError

MESSAGE = {
    "Id": "fingerprint-1",
    "sensorId": "cam-1",
    "category": "collision",
    "timestamp": "2021-01-01T00:00:00.000Z",
    "end": "2021-01-01T00:00:10.000Z",
    "objectIds": ["obj-1"],
}


class Pipeline(EventLoopPipelineMixin):
    """Minimal host exposing the attributes the mixin reads."""

    def __init__(self, **overrides):
        self.config = {}
        self.async_vlm_runtime = AsyncMock()
        self.prompt_manager = MagicMock()
        self.prompt_manager.alert_config_loader = None
        self.redis_handler = None
        self.vlm_enhanced_event_sink = MagicMock()
        self.enrichment_processor = MagicMock()
        self._vst_handler = MagicMock()
        self._vlm_capacity = None
        self._observe_async_external_io = MagicMock()
        self._complete_event_after_publish = MagicMock()
        self._compute_fingerprint = MagicMock(return_value="fingerprint-1")
        self._classify_pre_processing_failure = MagicMock(return_value="pre_processing_error")
        self._apply_vlm_exception = MagicMock(return_value=("vlm_timeout", "VLM timeout"))
        self._log_vlm_exception = MagicMock()
        self._publish_outcome_and_complete = MagicMock()
        self._update_enrichment_with_mode = MagicMock()
        self.__dict__.update(overrides)


@pytest.fixture
def pipeline():
    return Pipeline()


class TestCapacitySlot:
    @pytest.mark.asyncio
    async def test_a_none_semaphore_is_a_pass_through(self, pipeline):
        with patch("handlers.event_loop_pipeline_mixin.inc_capacity_in_flight") as inc:
            async with pipeline._capacity_slot(None, "vlm"):
                pass

        inc.assert_not_called()

    @pytest.mark.asyncio
    async def test_the_permit_is_acquired_and_released(self, pipeline):
        semaphore = asyncio.Semaphore(1)

        async with pipeline._capacity_slot(semaphore, "vlm"):
            assert semaphore.locked()

        assert not semaphore.locked()

    @pytest.mark.asyncio
    async def test_the_permit_is_released_on_error(self, pipeline):
        semaphore = asyncio.Semaphore(1)

        with pytest.raises(RuntimeError, match="boom"):
            async with pipeline._capacity_slot(semaphore, "vlm"):
                raise RuntimeError("boom")

        assert not semaphore.locked()

    @pytest.mark.asyncio
    async def test_the_in_flight_gauge_is_balanced(self, pipeline):
        semaphore = asyncio.Semaphore(1)

        with patch("handlers.event_loop_pipeline_mixin.inc_capacity_in_flight") as inc, patch(
            "handlers.event_loop_pipeline_mixin.dec_capacity_in_flight"
        ) as dec:
            async with pipeline._capacity_slot(semaphore, "vst"):
                pass

        inc.assert_called_once_with("vst")
        dec.assert_called_once_with("vst")

    @pytest.mark.asyncio
    async def test_the_gauge_is_balanced_even_on_error(self, pipeline):
        semaphore = asyncio.Semaphore(1)

        with patch("handlers.event_loop_pipeline_mixin.dec_capacity_in_flight") as dec:
            with pytest.raises(RuntimeError):
                async with pipeline._capacity_slot(semaphore, "vst"):
                    raise RuntimeError("boom")

        dec.assert_called_once_with("vst")

    @pytest.mark.asyncio
    async def test_the_wait_is_recorded_in_latency(self, pipeline):
        semaphore = asyncio.Semaphore(1)
        latency = {}

        async with pipeline._capacity_slot(semaphore, "vlm", latency):
            pass

        assert "vlm" in latency["capacityWait"]

    @pytest.mark.asyncio
    async def test_repeated_waits_accumulate(self, pipeline):
        semaphore = asyncio.Semaphore(1)
        latency = {"capacityWait": {"vlm": 1.0}}

        async with pipeline._capacity_slot(semaphore, "vlm", latency):
            pass

        assert latency["capacityWait"]["vlm"] >= 1.0

    @pytest.mark.asyncio
    async def test_the_wait_is_observed_as_a_metric(self, pipeline):
        semaphore = asyncio.Semaphore(1)

        with patch("handlers.event_loop_pipeline_mixin.observe_capacity_wait") as observe:
            async with pipeline._capacity_slot(semaphore, "vlm"):
                pass

        assert observe.call_args.args[0] == "vlm"


class TestEventLoopHttpClient:
    def test_the_client_is_created_lazily_and_reused(self, pipeline):
        with patch("httpx.AsyncClient") as client_cls:
            first = pipeline._get_event_loop_http_client()
            second = pipeline._get_event_loop_http_client()

        assert first is second
        assert client_cls.call_count == 1

    def test_redirects_are_followed(self, pipeline):
        with patch("httpx.AsyncClient") as client_cls:
            pipeline._get_event_loop_http_client()

        assert client_cls.call_args.kwargs["follow_redirects"] is True


class TestAcloseEventLoopClients:
    @pytest.mark.asyncio
    async def test_nothing_to_close_is_tolerated(self, pipeline):
        pipeline.vlm_enhanced_event_sink = object()
        await pipeline._aclose_event_loop_clients()

    @pytest.mark.asyncio
    async def test_the_http_client_is_closed_and_cleared(self, pipeline):
        client = AsyncMock()
        pipeline._event_loop_http_client = client

        await pipeline._aclose_event_loop_clients()

        client.aclose.assert_awaited_once()
        assert pipeline._event_loop_http_client is None

    @pytest.mark.asyncio
    async def test_a_failing_http_close_does_not_stop_the_rest(self, pipeline):
        client = AsyncMock()
        client.aclose.side_effect = RuntimeError("stuck")
        pipeline._event_loop_http_client = client
        pipeline.vlm_enhanced_event_sink.aclose_async = AsyncMock()

        await pipeline._aclose_event_loop_clients()

        pipeline.vlm_enhanced_event_sink.aclose_async.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_the_async_sink_is_closed(self, pipeline):
        pipeline.vlm_enhanced_event_sink.aclose_async = AsyncMock()

        await pipeline._aclose_event_loop_clients()

        pipeline.vlm_enhanced_event_sink.aclose_async.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_a_failing_sink_close_does_not_stop_the_rest(self, pipeline):
        pipeline.vlm_enhanced_event_sink.aclose_async = AsyncMock(
            side_effect=RuntimeError("stuck")
        )
        handler = MagicMock()
        handler._es_client = MagicMock()
        handler._es_client.aclose_async = AsyncMock()
        pipeline.redis_handler = handler

        await pipeline._aclose_event_loop_clients()

        handler._es_client.aclose_async.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_the_verdict_check_client_is_closed(self, pipeline):
        handler = MagicMock()
        handler._es_client = MagicMock()
        handler._es_client.aclose_async = AsyncMock()
        pipeline.redis_handler = handler
        pipeline.vlm_enhanced_event_sink = object()

        await pipeline._aclose_event_loop_clients()

        handler._es_client.aclose_async.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_a_failing_verdict_client_close_is_swallowed(self, pipeline):
        handler = MagicMock()
        handler._es_client = MagicMock()
        handler._es_client.aclose_async = AsyncMock(side_effect=RuntimeError("stuck"))
        pipeline.redis_handler = handler
        pipeline.vlm_enhanced_event_sink = object()

        await pipeline._aclose_event_loop_clients()


class TestAnalyzeVideoUrlAsync:
    @pytest.mark.asyncio
    async def test_url_mode_by_default(self, pipeline):
        await pipeline._analyze_video_url_async("http://vst/v.mp4", "prompt", "sys")

        pipeline.async_vlm_runtime.analyze_video_url_async.assert_awaited_once()
        pipeline.async_vlm_runtime.analyze_video_with_base64_async.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_base64_mode_when_requested(self, pipeline):
        await pipeline._analyze_video_url_async(
            "http://vst/v.mp4", "prompt", "sys", use_base64=True
        )

        pipeline.async_vlm_runtime.analyze_video_with_base64_async.assert_awaited_once()
        pipeline.async_vlm_runtime.analyze_video_url_async.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_frames_and_overrides_are_forwarded(self, pipeline):
        overrides = {"model": "other"}
        await pipeline._analyze_video_url_async(
            "u", "p", None, num_frames=4, config_overrides=overrides
        )

        kwargs = pipeline.async_vlm_runtime.analyze_video_url_async.call_args.kwargs
        assert kwargs["num_frames"] == 4
        assert kwargs["config_overrides"] is overrides


class TestSetMessageIdAndShouldSkipAsync:
    @pytest.mark.asyncio
    async def test_a_message_without_a_fingerprint_is_processed(self, pipeline):
        pipeline._compute_fingerprint.return_value = None

        assert await pipeline._set_message_id_and_should_skip_async(dict(MESSAGE), "cam-1") is False

    @pytest.mark.asyncio
    async def test_the_fingerprint_is_stamped_on_the_message(self, pipeline):
        message = {}

        await pipeline._set_message_id_and_should_skip_async(message, "cam-1")

        assert message["Id"] == "fingerprint-1"

    @pytest.mark.asyncio
    async def test_no_verdict_handler_means_no_skip(self, pipeline):
        assert await pipeline._set_message_id_and_should_skip_async({}, "cam-1") is False

    @pytest.mark.asyncio
    async def test_a_confirmed_verdict_skips_the_event(self, pipeline):
        handler = MagicMock()
        handler.is_verdict_confirmed_async = AsyncMock(return_value=True)
        pipeline.redis_handler = handler

        with patch("handlers.event_loop_pipeline_mixin.inc_events_skipped_confirmed") as inc:
            assert await pipeline._set_message_id_and_should_skip_async({}, "cam-1") is True

        inc.assert_called_once()

    @pytest.mark.asyncio
    async def test_an_unconfirmed_verdict_continues(self, pipeline):
        handler = MagicMock()
        handler.is_verdict_confirmed_async = AsyncMock(return_value=False)
        pipeline.redis_handler = handler

        assert await pipeline._set_message_id_and_should_skip_async({}, "cam-1") is False

    @pytest.mark.asyncio
    async def test_a_sync_only_handler_is_driven_off_loop(self, pipeline):
        handler = MagicMock()
        del handler.is_verdict_confirmed_async
        handler.is_verdict_confirmed.return_value = True
        pipeline.redis_handler = handler

        with patch("handlers.event_loop_pipeline_mixin.inc_events_skipped_confirmed"):
            assert await pipeline._set_message_id_and_should_skip_async({}, "cam-1") is True

        handler.is_verdict_confirmed.assert_called_once_with("fingerprint-1")

    @pytest.mark.asyncio
    async def test_a_lookup_failure_continues_processing(self, pipeline):
        """A verdict-store outage must not silently drop events."""
        handler = MagicMock()
        handler.is_verdict_confirmed_async = AsyncMock(side_effect=RuntimeError("ES down"))
        pipeline.redis_handler = handler

        assert await pipeline._set_message_id_and_should_skip_async({}, "cam-1") is False


class TestPrepareMessageContextAsync:
    @pytest.mark.asyncio
    async def test_returns_the_resolved_prompts(self, pipeline):
        pipeline.prompt_manager.get_prompts_for_message.return_value = ("user", "system")

        result = await pipeline._prepare_message_context_async(dict(MESSAGE), "cam-1", {}, 0.0)

        assert result == ("user", "system")

    @pytest.mark.asyncio
    async def test_a_skipped_event_returns_none(self, pipeline):
        with patch.object(
            pipeline, "_set_message_id_and_should_skip_async", AsyncMock(return_value=True)
        ):
            assert await pipeline._prepare_message_context_async(
                dict(MESSAGE), "cam-1", {}, 0.0
            ) is None

    @pytest.mark.asyncio
    async def test_a_skip_check_failure_completes_the_event(self, pipeline):
        with patch.object(
            pipeline,
            "_set_message_id_and_should_skip_async",
            AsyncMock(side_effect=RuntimeError("boom")),
        ), patch("handlers.event_loop_pipeline_mixin.record_event_complete") as record:
            assert await pipeline._prepare_message_context_async(
                dict(MESSAGE), "cam-1", {}, 0.0
            ) is None

        assert record.call_args.kwargs["failure_reason"] == "pre_processing_error"

    @pytest.mark.asyncio
    async def test_a_missing_prompt_completes_the_event_as_no_prompt(self, pipeline):
        pipeline.prompt_manager.get_prompts_for_message.return_value = (None, None)

        with patch("handlers.event_loop_pipeline_mixin.record_event_complete") as record:
            assert await pipeline._prepare_message_context_async(
                dict(MESSAGE), "cam-1", {}, 0.0
            ) is None

        assert record.call_args.kwargs["failure_reason"] == "no_prompt"

    @pytest.mark.asyncio
    async def test_a_system_prompt_alone_is_enough_to_continue(self, pipeline):
        pipeline.prompt_manager.get_prompts_for_message.return_value = (None, "system")

        assert await pipeline._prepare_message_context_async(
            dict(MESSAGE), "cam-1", {}, 0.0
        ) == (None, "system")


class TestResolveVideoUrlAsync:
    def _vst_result(self, url="http://vst/v.mp4"):
        return (url, "2021-01-01T00:00:00.000Z", "2021-01-01T00:00:10.000Z")

    @pytest.fixture
    def pipeline(self):
        pipeline = Pipeline()
        pipeline._event_loop_http_client = MagicMock()
        pipeline._vst_handler.get_video_stream_url_async = AsyncMock(
            return_value=("http://vst/v.mp4", "2021-01-01T00:00:00.000Z", "2021-01-01T00:00:10.000Z")
        )
        return pipeline

    @pytest.mark.asyncio
    async def test_happy_path(self, pipeline):
        latency = {}

        url, start, end, error = await pipeline._resolve_video_url_async(
            dict(MESSAGE), "cam-1", latency
        )

        assert url == "http://vst/v.mp4"
        assert error is None
        assert latency["getVideoStreamUrlWithOverlay"]["success"] is True

    @pytest.mark.asyncio
    async def test_the_per_alert_type_anchor_is_applied(self, pipeline):
        loader = MagicMock()
        loader.get_config_for_alert_type.return_value = MagicMock(segment_anchor="start")
        pipeline.prompt_manager.alert_config_loader = loader

        await pipeline._resolve_video_url_async(dict(MESSAGE), "cam-1", {})

        kwargs = pipeline._vst_handler.get_video_stream_url_async.call_args.kwargs
        assert kwargs["alert_type_anchor"] == "start"

    @pytest.mark.asyncio
    async def test_no_alert_config_leaves_the_anchor_unset(self, pipeline):
        loader = MagicMock()
        loader.get_config_for_alert_type.return_value = None
        pipeline.prompt_manager.alert_config_loader = loader

        await pipeline._resolve_video_url_async(dict(MESSAGE), "cam-1", {})

        assert pipeline._vst_handler.get_video_stream_url_async.call_args.kwargs[
            "alert_type_anchor"
        ] is None

    @pytest.mark.asyncio
    async def test_a_vst_error_is_captured_without_a_retry_by_default(self, pipeline):
        pipeline._vst_handler.get_video_stream_url_async = AsyncMock(
            side_effect=VSTError("vst 500", category="server_error")
        )
        latency = {}

        url, _start, _end, error = await pipeline._resolve_video_url_async(
            dict(MESSAGE), "cam-1", latency
        )

        assert url is None
        assert isinstance(error, VSTError)
        assert latency["getVideoStreamUrlWithOverlay"]["success"] is False
        assert "getVideoStreamUrlWithoutOverlay" not in latency

    @pytest.mark.asyncio
    async def test_the_no_overlay_retry_clears_the_captured_error(self, pipeline):
        pipeline.config = {"vst_config": {"retry_without_overlay": True}}
        pipeline._vst_handler.get_video_stream_url_async = AsyncMock(
            side_effect=[VSTError("vst 500", category="server_error"), self._vst_result()]
        )
        latency = {}

        url, _start, _end, error = await pipeline._resolve_video_url_async(
            dict(MESSAGE), "cam-1", latency
        )

        assert url == "http://vst/v.mp4"
        assert error is None
        assert latency["getVideoStreamUrlWithoutOverlay"]["success"] is True

    @pytest.mark.asyncio
    async def test_the_retry_requests_overlay_removal(self, pipeline):
        pipeline.config = {"vst_config": {"retry_without_overlay": True}}
        pipeline._vst_handler.get_video_stream_url_async = AsyncMock(
            side_effect=[VSTError("vst 500", category="server_error"), self._vst_result()]
        )

        await pipeline._resolve_video_url_async(dict(MESSAGE), "cam-1", {})

        assert pipeline._vst_handler.get_video_stream_url_async.call_args.kwargs[
            "remove_overlay"
        ] is True

    @pytest.mark.asyncio
    async def test_a_failing_retry_keeps_the_retry_error(self, pipeline):
        pipeline.config = {"vst_config": {"retry_without_overlay": True}}
        retry_error = VSTError("vst 503", category="unavailable")
        pipeline._vst_handler.get_video_stream_url_async = AsyncMock(
            side_effect=[VSTError("vst 500", category="server_error"), retry_error]
        )

        url, _start, _end, error = await pipeline._resolve_video_url_async(
            dict(MESSAGE), "cam-1", {}
        )

        assert url is None
        assert error is retry_error

    @pytest.mark.asyncio
    async def test_an_unexpected_retry_error_clears_the_url(self, pipeline):
        pipeline.config = {"vst_config": {"retry_without_overlay": True}}
        pipeline._vst_handler.get_video_stream_url_async = AsyncMock(
            side_effect=[VSTError("vst 500", category="server_error"), RuntimeError("boom")]
        )

        url, _start, _end, _error = await pipeline._resolve_video_url_async(
            dict(MESSAGE), "cam-1", {}
        )

        assert url is None

    @pytest.mark.asyncio
    async def test_an_unexpected_error_is_not_captured_as_a_vst_error(self, pipeline):
        pipeline._vst_handler.get_video_stream_url_async = AsyncMock(
            side_effect=RuntimeError("boom")
        )

        url, _start, _end, error = await pipeline._resolve_video_url_async(
            dict(MESSAGE), "cam-1", {}
        )

        assert url is None
        assert error is None

    @pytest.mark.asyncio
    async def test_vst_duration_is_emitted_once_per_event(self, pipeline):
        """Two attempts must still produce a single duration sample."""
        pipeline.config = {"vst_config": {"retry_without_overlay": True}}
        pipeline._vst_handler.get_video_stream_url_async = AsyncMock(
            side_effect=[VSTError("vst 500", category="server_error"), self._vst_result()]
        )

        with patch("handlers.event_loop_pipeline_mixin.observe_vst_duration") as observe:
            await pipeline._resolve_video_url_async(dict(MESSAGE), "cam-1", {})

        observe.assert_called_once()


class TestValidateVideoUrlAsync:
    def _client(self, response):
        stream = MagicMock()
        stream.__aenter__ = AsyncMock(return_value=response)
        stream.__aexit__ = AsyncMock(return_value=False)
        client = MagicMock()
        client.stream.return_value = stream
        return client

    def _response(self, status_code=200, content_length="2000"):
        response = MagicMock()
        response.status_code = status_code
        response.headers = {"content-type": "video/mp4", "content-length": content_length}
        return response

    @pytest.mark.asyncio
    async def test_a_healthy_response_validates(self, pipeline):
        pipeline._event_loop_http_client = self._client(self._response())

        assert await pipeline._validate_video_url_async("http://vst/v.mp4") is True

    @pytest.mark.asyncio
    async def test_a_small_body_still_validates(self, pipeline):
        pipeline._event_loop_http_client = self._client(self._response(content_length="500"))

        assert await pipeline._validate_video_url_async("http://vst/v.mp4") is True

    @pytest.mark.asyncio
    async def test_an_unparseable_content_length_still_validates(self, pipeline):
        pipeline._event_loop_http_client = self._client(self._response(content_length="n/a"))

        assert await pipeline._validate_video_url_async("http://vst/v.mp4") is True

    @pytest.mark.asyncio
    async def test_an_error_status_is_retried_then_fails(self, pipeline):
        pipeline._event_loop_http_client = self._client(self._response(status_code=404))

        assert await pipeline._validate_video_url_async(
            "http://vst/v.mp4", max_retries=3, retry_delay=0
        ) is False
        assert pipeline._event_loop_http_client.stream.call_count == 3

    @pytest.mark.asyncio
    async def test_a_zero_length_body_is_retried_then_fails(self, pipeline):
        """VST may publish the URL before the file is flushed."""
        pipeline._event_loop_http_client = self._client(self._response(content_length="0"))

        assert await pipeline._validate_video_url_async(
            "http://vst/v.mp4", max_retries=2, retry_delay=0
        ) is False

    @pytest.mark.asyncio
    async def test_a_late_success_is_accepted(self, pipeline):
        first = self._response(content_length="0")
        second = self._response()
        streams = []
        for response in (first, second):
            stream = MagicMock()
            stream.__aenter__ = AsyncMock(return_value=response)
            stream.__aexit__ = AsyncMock(return_value=False)
            streams.append(stream)
        client = MagicMock()
        client.stream.side_effect = streams
        pipeline._event_loop_http_client = client

        assert await pipeline._validate_video_url_async(
            "http://vst/v.mp4", max_retries=3, retry_delay=0
        ) is True

    @pytest.mark.asyncio
    async def test_transport_errors_are_retried_then_fail(self, pipeline):
        client = MagicMock()
        client.stream.side_effect = httpx.ConnectError("refused")
        pipeline._event_loop_http_client = client

        assert await pipeline._validate_video_url_async(
            "http://vst/v.mp4", max_retries=2, retry_delay=0
        ) is False

    @pytest.mark.asyncio
    async def test_an_unexpected_error_fails_immediately(self, pipeline):
        client = MagicMock()
        client.stream.side_effect = RuntimeError("boom")
        pipeline._event_loop_http_client = client

        assert await pipeline._validate_video_url_async(
            "http://vst/v.mp4", max_retries=5, retry_delay=0
        ) is False
        assert client.stream.call_count == 1


class TestPublishOutcomeAndCompleteAsync:
    @pytest.mark.asyncio
    async def test_a_sync_only_sink_is_driven_off_loop(self, pipeline):
        del pipeline.vlm_enhanced_event_sink.publish_success_async

        await pipeline._publish_outcome_and_complete_async(
            dict(MESSAGE), "user", "sys", "content", None, 0.0, {}
        )

        pipeline._publish_outcome_and_complete.assert_called_once()

    @pytest.mark.asyncio
    async def test_success_goes_to_publish_success_async(self, pipeline):
        pipeline.vlm_enhanced_event_sink.publish_success_async = AsyncMock()

        await pipeline._publish_outcome_and_complete_async(
            dict(MESSAGE), "user", "sys", "content", None, 0.0, {}
        )

        pipeline.vlm_enhanced_event_sink.publish_success_async.assert_awaited_once()
        pipeline._complete_event_after_publish.assert_called_once()

    @pytest.mark.asyncio
    async def test_absent_content_goes_to_publish_error_async(self, pipeline):
        pipeline.vlm_enhanced_event_sink.publish_success_async = AsyncMock()
        pipeline.vlm_enhanced_event_sink.publish_error_async = AsyncMock()

        await pipeline._publish_outcome_and_complete_async(
            dict(MESSAGE), "user", "sys", None, "vlm_timeout", 0.0, {}
        )

        pipeline.vlm_enhanced_event_sink.publish_error_async.assert_awaited_once()
        pipeline.vlm_enhanced_event_sink.publish_success_async.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_the_operation_is_observed_as_success(self, pipeline):
        pipeline.vlm_enhanced_event_sink.publish_success_async = AsyncMock()

        await pipeline._publish_outcome_and_complete_async(
            dict(MESSAGE), "user", "sys", "content", None, 0.0, {}
        )

        kwargs = pipeline._observe_async_external_io.call_args.kwargs
        assert kwargs["mode"] == "event_loop"
        assert kwargs["result"] == "success"

    @pytest.mark.asyncio
    async def test_a_publish_failure_is_observed_and_reraised(self, pipeline):
        pipeline.vlm_enhanced_event_sink.publish_success_async = AsyncMock(
            side_effect=RuntimeError("sink down")
        )

        with pytest.raises(RuntimeError, match="sink down"):
            await pipeline._publish_outcome_and_complete_async(
                dict(MESSAGE), "user", "sys", "content", None, 0.0, {}
            )

        assert pipeline._observe_async_external_io.call_args.kwargs["result"] == "error"
        pipeline._complete_event_after_publish.assert_not_called()

    @pytest.mark.asyncio
    async def test_the_error_helper_delegates_with_no_content(self, pipeline):
        pipeline.vlm_enhanced_event_sink.publish_success_async = AsyncMock()
        pipeline.vlm_enhanced_event_sink.publish_error_async = AsyncMock()

        await pipeline._publish_error_and_complete_async(
            dict(MESSAGE), "user", "sys", "vlm_timeout", 0.0, {}
        )

        pipeline.vlm_enhanced_event_sink.publish_error_async.assert_awaited_once()


class TestHandleVlmExceptionAsync:
    @pytest.mark.asyncio
    async def test_the_exception_is_classified_and_published(self, pipeline):
        pipeline.vlm_enhanced_event_sink.publish_success_async = AsyncMock()
        pipeline.vlm_enhanced_event_sink.publish_error_async = AsyncMock()
        exc = TimeoutError("vlm timed out")

        await pipeline._handle_vlm_exception_async(
            exc, dict(MESSAGE), "user", "sys", "http://vst/v.mp4", 0.0, {}
        )

        pipeline._apply_vlm_exception.assert_called_once()
        pipeline.vlm_enhanced_event_sink.publish_error_async.assert_awaited_once()
        pipeline._log_vlm_exception.assert_called_once()

    @pytest.mark.asyncio
    async def test_the_classified_reason_reaches_completion(self, pipeline):
        pipeline.vlm_enhanced_event_sink.publish_success_async = AsyncMock()
        pipeline.vlm_enhanced_event_sink.publish_error_async = AsyncMock()

        await pipeline._handle_vlm_exception_async(
            RuntimeError("boom"), dict(MESSAGE), "user", None, None, 0.0, {}
        )

        assert pipeline._complete_event_after_publish.call_args.kwargs["failure_reason"] == (
            "vlm_timeout"
        )


class TestProcessEnrichmentEventLoop:
    @pytest.mark.asyncio
    async def test_no_enrichment_result_is_a_noop(self, pipeline):
        pipeline.enrichment_processor.process_async = AsyncMock(return_value=None)

        await pipeline._process_enrichment_event_loop(
            dict(MESSAGE), "http://vst/v.mp4", "sys", "cam-1", {}
        )

        pipeline.enrichment_processor.merge_into_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_the_result_is_merged_and_pushed_to_the_async_sink(self, pipeline):
        pipeline.enrichment_processor.process_async = AsyncMock(return_value={"reasoning": "x"})
        pipeline.vlm_enhanced_event_sink.update_enrichment_async = AsyncMock()

        await pipeline._process_enrichment_event_loop(
            dict(MESSAGE), "http://vst/v.mp4", "sys", "cam-1", {}
        )

        pipeline.enrichment_processor.merge_into_message.assert_called_once()
        pipeline.vlm_enhanced_event_sink.update_enrichment_async.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_a_sync_only_sink_is_driven_off_loop(self, pipeline):
        pipeline.enrichment_processor.process_async = AsyncMock(return_value={"reasoning": "x"})
        del pipeline.vlm_enhanced_event_sink.update_enrichment_async

        await pipeline._process_enrichment_event_loop(
            dict(MESSAGE), "http://vst/v.mp4", "sys", "cam-1", {}
        )

        pipeline._update_enrichment_with_mode.assert_called_once()

    @pytest.mark.asyncio
    async def test_the_analyze_callback_takes_a_vlm_capacity_slot(self, pipeline):
        pipeline._vlm_capacity = asyncio.Semaphore(1)
        captured = {}

        async def process_async(**kwargs):
            captured["analyze"] = kwargs["analyze_video_async"]
            return None

        pipeline.enrichment_processor.process_async = process_async

        await pipeline._process_enrichment_event_loop(
            dict(MESSAGE), "http://vst/v.mp4", "sys", "cam-1", {"model": "m"}
        )
        await captured["analyze"]("http://vst/v.mp4", "prompt", "sys")

        pipeline.async_vlm_runtime.analyze_video_url_async.assert_awaited_once()
        assert not pipeline._vlm_capacity.locked()

    @pytest.mark.asyncio
    async def test_the_merged_vlm_config_is_forwarded(self, pipeline):
        pipeline.enrichment_processor.process_async = AsyncMock(return_value=None)
        merged = {"model": "other"}

        await pipeline._process_enrichment_event_loop(
            dict(MESSAGE), "http://vst/v.mp4", "sys", "cam-1", merged
        )

        assert pipeline.enrichment_processor.process_async.call_args.kwargs[
            "config_overrides"
        ] is merged
