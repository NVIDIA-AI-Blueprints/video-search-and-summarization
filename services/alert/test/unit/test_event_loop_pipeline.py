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

import asyncio
import copy
import threading
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest
from openai import APITimeoutError

from enhance_alert_with_vlm import AnomalyEnhancer
from handlers.async_dispatch_mixin import AsyncDispatchMixin
from handlers.event_loop_pipeline_mixin import EventLoopPipelineMixin
from vlm.vlm_client import AsyncVLMRuntime


class _PipelineStub(AsyncDispatchMixin, EventLoopPipelineMixin):
    pass


class _GatedVLM:
    """Async VLM stand-in that holds every response until the gate opens and
    tracks peak concurrent calls."""

    def __init__(self):
        self._lock = threading.Lock()
        self.in_flight = 0
        self.peak = 0
        self.started = threading.Semaphore(0)
        self._gate = {"event": None}

    async def analyze(self, *args, **kwargs):
        if self._gate["event"] is None:
            self._gate["event"] = asyncio.Event()
        with self._lock:
            self.in_flight += 1
            self.peak = max(self.peak, self.in_flight)
        self.started.release()
        await self._gate["event"].wait()
        with self._lock:
            self.in_flight -= 1
        return SimpleNamespace(content="YES")

    async def _open(self):
        if self._gate["event"] is None:
            self._gate["event"] = asyncio.Event()
        self._gate["event"].set()

    def open_gate(self, runtime):
        runtime.submit_coroutine(self._open()).result(timeout=10)

    def wait_started(self, count, timeout=10):
        for _ in range(count):
            assert self.started.acquire(timeout=timeout), "VLM call did not start in time"


def _build_pipeline_stub(runtime, max_in_flight, vlm_cap, vst_cap):
    stub = _PipelineStub()
    stub.pipeline_mode = "event_loop"
    stub._message_dispatch_lock = threading.Lock()
    stub._message_dispatch_futures = set()
    stub.async_dispatch_max_in_flight = max_in_flight
    stub._dispatch_backpressure_semaphore = threading.BoundedSemaphore(max_in_flight)
    stub._message_dispatch_executor = None
    stub.async_vlm_runtime = runtime
    stub._vlm_capacity = asyncio.Semaphore(vlm_cap)
    stub._vst_capacity = asyncio.Semaphore(vst_cap)
    stub.vlm_media_source_using_base64 = False
    stub.url_transform_enabled = False
    stub._process_single_message = Mock()

    stub._transform_video_urls = lambda url: (url, url)

    async def _prepare_message_context_async(message, sensor_id, latency, worker_start_time):
        return ("up", "sp")

    async def _resolve_video_url_async(message, sensor_id, latency):
        return ("http://video/clip.mp4", "2026-01-01T00:00:00Z", "2026-01-01T00:00:10Z", None)

    async def _validate_video_url_async(url):
        return True

    stub._prepare_message_context_async = _prepare_message_context_async
    stub._resolve_video_url_async = _resolve_video_url_async
    stub._validate_video_url_async = _validate_video_url_async
    stub._get_merged_vlm_config = lambda category: {"max_retries": 0}
    stub._apply_vlm_response = (
        lambda message, response_content, merged_vlm, storage_video_url, latency: (True, response_content)
    )

    published = []
    publish_lock = threading.Lock()

    async def _record_publish(message, user_prompt, system_prompt, response_content,
                              vlm_failure_reason, worker_start_time, latency):
        with publish_lock:
            published.append({"message": message, "latency": latency,
                              "failure_reason": vlm_failure_reason})

    stub._publish_outcome_and_complete_async = _record_publish
    stub.published = published

    async def _no_enrichment(message, video_url, system_prompt, sensor_id, merged_vlm):
        return None

    stub._process_enrichment_event_loop = _no_enrichment
    return stub


def _make_message(idx):
    return {
        "id": f"evt-{idx}",
        "sensorId": f"sensor-{idx}",
        "category": "intrusion",
        "timestamp": "2026-01-01T00:00:00Z",
        "end": "2026-01-01T00:00:10Z",
        "objectIds": [],
    }


class TestEventLoopConcurrency:
    def test_in_flight_concurrency_exceeds_thread_count(self):
        """12 messages must sit in flight on the VLM simultaneously even though
        the pipeline owns a single event-loop thread and no dispatch executor."""
        runtime = AsyncVLMRuntime({}, io_workers=8)
        vlm = _GatedVLM()
        runtime.analyze_video_url_async = vlm.analyze
        stub = _build_pipeline_stub(runtime, max_in_flight=16, vlm_cap=12, vst_cap=12)
        try:
            for idx in range(12):
                stub._process_single_message_with_mode(worker_id=0, message=_make_message(idx))
            futures = list(stub._message_dispatch_futures)
            assert len(futures) == 12

            vlm.wait_started(12)
            assert vlm.peak == 12
            assert not stub.published

            vlm.open_gate(runtime)
            for future in futures:
                future.result(timeout=30)

            assert len(stub.published) == 12
            assert not stub._message_dispatch_futures
            for _ in range(16):
                assert stub._dispatch_backpressure_semaphore.acquire(blocking=False)
        finally:
            runtime.stop()

    def test_vlm_capacity_cap_is_never_exceeded(self):
        runtime = AsyncVLMRuntime({}, io_workers=8)
        vlm = _GatedVLM()
        runtime.analyze_video_url_async = vlm.analyze
        stub = _build_pipeline_stub(runtime, max_in_flight=16, vlm_cap=3, vst_cap=8)
        try:
            for idx in range(10):
                stub._process_single_message_with_mode(worker_id=0, message=_make_message(idx))
            futures = list(stub._message_dispatch_futures)

            vlm.wait_started(3)
            assert vlm.in_flight == 3

            vlm.open_gate(runtime)
            for future in futures:
                future.result(timeout=30)

            assert len(stub.published) == 10
            assert vlm.peak == 3
        finally:
            runtime.stop()

    def test_capacity_wait_recorded_in_latency(self):
        runtime = AsyncVLMRuntime({}, io_workers=8)
        vlm = _GatedVLM()
        runtime.analyze_video_url_async = vlm.analyze
        stub = _build_pipeline_stub(runtime, max_in_flight=8, vlm_cap=1, vst_cap=8)
        try:
            for idx in range(2):
                stub._process_single_message_with_mode(worker_id=0, message=_make_message(idx))
            futures = list(stub._message_dispatch_futures)
            vlm.wait_started(1)
            vlm.open_gate(runtime)
            for future in futures:
                future.result(timeout=30)

            waits = [entry["latency"].get("capacityWait", {}) for entry in stub.published]
            assert all("vlm" in wait for wait in waits)
            timestamps = [entry["latency"]["timestamps"] for entry in stub.published]
            assert all(ts.get("taskDispatchedAt") for ts in timestamps)
            assert all(ts.get("taskStartedAt") for ts in timestamps)
        finally:
            runtime.stop()


class TestCapacitySlotGaugeWiring:
    def test_capacity_slot_incs_and_decs_service_in_flight(self, monkeypatch):
        import handlers.event_loop_pipeline_mixin as mixin_mod

        calls = []
        monkeypatch.setattr(mixin_mod, "inc_capacity_in_flight", lambda service: calls.append(("inc", service)))
        monkeypatch.setattr(mixin_mod, "dec_capacity_in_flight", lambda service: calls.append(("dec", service)))

        stub = _PipelineStub()

        async def _exercise():
            sem = asyncio.Semaphore(1)
            async with stub._capacity_slot(sem, "vlm"):
                assert calls == [("inc", "vlm")]
            async with stub._capacity_slot(sem, "vst"):
                raise RuntimeError("boom")

        with pytest.raises(RuntimeError):
            asyncio.run(_exercise())

        # dec must fire on both the clean and the exception exit paths
        assert calls == [("inc", "vlm"), ("dec", "vlm"), ("inc", "vst"), ("dec", "vst")]


class TestRuntimeShutdown:
    def test_stop_cancels_pending_tasks_and_joins_thread(self):
        runtime = AsyncVLMRuntime({}, io_workers=4)

        async def _stuck():
            await asyncio.Event().wait()

        future = runtime.submit_coroutine(_stuck())
        try:
            runtime.stop(timeout=10)
            assert future.done()
            assert future.cancelled()
            assert runtime._thread is None
        finally:
            if runtime._thread is not None:
                runtime.stop()


def _scrub(payload):
    """Drop timing-dependent keys so payloads from both modes compare equal."""
    if isinstance(payload, dict):
        return {
            key: _scrub(value)
            for key, value in payload.items()
            if key not in ("latency", "capacityWait", "timestamps")
        }
    if isinstance(payload, list):
        return [_scrub(item) for item in payload]
    return payload


def _build_parity_enhancer(mode, runtime=None, analyze_error=None):
    enhancer = AnomalyEnhancer.__new__(AnomalyEnhancer)
    enhancer.pipeline_mode = mode
    enhancer.async_vlm_runtime = runtime
    enhancer._vlm_capacity = asyncio.Semaphore(4) if mode == "event_loop" else None
    enhancer._vst_capacity = asyncio.Semaphore(4) if mode == "event_loop" else None
    enhancer.include_latency_info = True
    enhancer.url_transform_enabled = False
    enhancer.vlm_media_source_using_base64 = False
    enhancer.vlm_client = Mock(model="test-model", base_url="http://vlm")
    enhancer.config = {}

    parser = Mock()
    parser.parse.return_value = {"verdict": "yes", "description": "person detected"}
    enhancer._pluggable_parser = parser

    enhancer.prompt_manager = Mock()
    enhancer.prompt_manager.get_prompts_for_message.return_value = ("user-prompt", "system-prompt")
    enhancer.prompt_manager.alert_config_loader = None

    def _skip_stub(message, sensor_id):
        fingerprint = AnomalyEnhancer._compute_fingerprint(message)
        if fingerprint:
            message["Id"] = fingerprint
        return False

    enhancer._set_message_id_and_should_skip = _skip_stub
    enhancer._get_video_stream_url_with_mode = lambda sensor_id, start, end, **kwargs: (
        "http://video/clip.mp4", "2026-01-01T00:00:00Z", "2026-01-01T00:00:10Z",
    )
    enhancer.validate_video_url = lambda url: True
    enhancer._get_merged_vlm_config = lambda category: {"max_retries": 0}

    def _sync_analyze(*args, **kwargs):
        if analyze_error is not None:
            raise analyze_error
        return SimpleNamespace(content="YES")

    enhancer._analyze_video_url_with_mode = _sync_analyze

    enhancer.enrichment_processor = Mock()
    enhancer.enrichment_processor.process.return_value = None

    async def _no_enrichment_async(**kwargs):
        return None

    enhancer.enrichment_processor.process_async = _no_enrichment_async

    captured = {"published": [], "completions": []}

    def _capture_publish(message, user_prompt, system_prompt, payload):
        captured["published"].append(copy.deepcopy(message))
        return None

    enhancer._publish_success_with_mode = _capture_publish
    enhancer._publish_error_with_mode = _capture_publish

    async def _capture_publish_async(message, user_prompt, system_prompt, payload):
        captured["published"].append(copy.deepcopy(message))

    enhancer.vlm_enhanced_event_sink = SimpleNamespace(
        publish_success_async=_capture_publish_async,
        publish_error_async=_capture_publish_async,
    )

    def _capture_complete(publish_future, worker_start_time, message, latency, failure_reason=None):
        captured["completions"].append(failure_reason)

    enhancer._complete_event_after_publish = _capture_complete

    # event_loop-mode leaf seams (async transports)
    enhancer._get_event_loop_http_client = lambda: None

    async def _fake_vst_async(http_client, sensor_id, start, end, **kwargs):
        return ("http://video/clip.mp4", "2026-01-01T00:00:00Z", "2026-01-01T00:00:10Z")

    enhancer._vst_handler = SimpleNamespace(get_video_stream_url_async=_fake_vst_async)

    async def _validate_async(url, **kwargs):
        return True

    enhancer._validate_video_url_async = _validate_async

    enhancer.captured = captured
    return enhancer


def _timeout_error():
    return APITimeoutError(request=httpx.Request("POST", "http://vlm"))


class TestModeParity:
    def test_success_payload_matches_between_sync_and_event_loop(self):
        sync_enhancer = _build_parity_enhancer("sync")
        sync_message = _make_message(1)
        sync_enhancer._process_single_message(0, sync_message)

        runtime = AsyncVLMRuntime({}, io_workers=4)

        async def _async_analyze(*args, **kwargs):
            return SimpleNamespace(content="YES")

        runtime.analyze_video_url_async = _async_analyze
        async_enhancer = _build_parity_enhancer("event_loop", runtime=runtime)
        async_message = _make_message(1)
        try:
            runtime.submit_coroutine(
                async_enhancer._process_single_message_async(0, async_message)
            ).result(timeout=30)
        finally:
            runtime.stop()

        assert len(sync_enhancer.captured["published"]) == 1
        assert len(async_enhancer.captured["published"]) == 1
        assert _scrub(sync_enhancer.captured["published"][0]) == _scrub(
            async_enhancer.captured["published"][0]
        )
        assert sync_enhancer.captured["completions"] == async_enhancer.captured["completions"]

    def test_vlm_timeout_error_payload_matches_between_modes(self):
        sync_enhancer = _build_parity_enhancer("sync", analyze_error=_timeout_error())
        sync_message = _make_message(2)
        sync_enhancer._process_single_message(0, sync_message)

        runtime = AsyncVLMRuntime({}, io_workers=4)

        async def _async_analyze(*args, **kwargs):
            raise _timeout_error()

        runtime.analyze_video_url_async = _async_analyze
        async_enhancer = _build_parity_enhancer("event_loop", runtime=runtime)
        async_message = _make_message(2)
        try:
            runtime.submit_coroutine(
                async_enhancer._process_single_message_async(0, async_message)
            ).result(timeout=30)
        finally:
            runtime.stop()

        assert sync_enhancer.captured["completions"] == ["vlm_timeout"]
        assert async_enhancer.captured["completions"] == ["vlm_timeout"]
        assert _scrub(sync_enhancer.captured["published"][0]) == _scrub(
            async_enhancer.captured["published"][0]
        )


class TestPromptLookupOffLoop:
    def test_prompt_lookup_runs_off_the_event_loop_thread(self):
        enhancer = _build_parity_enhancer("event_loop")
        seen = {}

        def _recording_lookup(message):
            seen["lookup_thread"] = threading.current_thread()
            return ("user-prompt", "system-prompt")

        enhancer.prompt_manager.get_prompts_for_message = _recording_lookup
        message = _make_message(1)

        async def _run():
            seen["loop_thread"] = threading.current_thread()
            return await enhancer._prepare_message_context_async(
                message, message["sensorId"], {}, 0.0
            )

        prompts = asyncio.run(_run())

        assert prompts == ("user-prompt", "system-prompt")
        assert seen["lookup_thread"] is not seen["loop_thread"]
