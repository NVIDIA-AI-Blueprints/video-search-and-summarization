# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import asyncio
import builtins
import pickle
import sys
import time
import types
from dataclasses import dataclass
from threading import Event, Lock, Thread
from unittest.mock import MagicMock, patch
from uuid import uuid4

import numpy as np
import pytest
import torch
from PIL import Image

from common.chunk_info import ChunkInfo
from api_models.captions import StreamingFramePolicy, VlmInferenceMode, VlmQuery
from models.base_vlm_model import BaseVlmModel, VlmGenerationConfig, VlmModelOutput
from models.vllm_compatible.dlalgo_streaming import (
    DlalgoSessionConfig,
    DlalgoStreamingSessionManager,
    StreamingSessionCapacityError,
    StreamingSessionNotFoundError,
    VllmDlalgoSessionFactory,
    apply_native_streaming_engine_args,
    temporal_video_segment_count,
    to_pil_rgb_frame,
)
from models.vllm_compatible.vllm_compatible_model import (
    VllmCompatible,
    _apply_compile_mm_encoder_override,
    _forward_supported_engine_arg,
    _get_processor_use_fast_override,
)
from vlm_pipeline.vlm_pipeline import VlmProcess, VlmRequestParams


def test_streaming_lock_is_created_in_worker_not_spawn_state():
    args = types.SimpleNamespace(
        vlm_batch_size=1, vlm_model_type=types.SimpleNamespace(value="openai-compat"),
        model_path="", model_implementation_path="", num_gpus=1,
    )
    with patch("vlm_pipeline.vlm_pipeline.ProcessBase.__init__"):
        process = VlmProcess(args, "/tmp", model_unhealthy_event=False)
    pickle.dumps(process.__dict__)
    assert not hasattr(process, "_streaming_vlm_lock")
    process._batch_size = 1
    with patch("vlm_pipeline.vlm_pipeline.get_model_class_path", return_value="test"), patch(
        "vlm_pipeline.vlm_pipeline.load_model", return_value=object()
    ):
        assert process._initialize()
    with process._streaming_vlm_lock:
        pass


@dataclass
class _DecodedFrame:
    value: int


@pytest.mark.parametrize(
    ("value", "expected"),
    [(None, None), ("", None), ("false", False), ("true", True)],
)
def test_processor_use_fast_override(monkeypatch, value, expected):
    monkeypatch.delenv("VLLM_PROCESSOR_USE_FAST", raising=False)
    monkeypatch.delenv("RTVI_VLLM_PROCESSOR_USE_FAST", raising=False)
    if value is not None:
        monkeypatch.setenv("VLLM_PROCESSOR_USE_FAST", value)

    assert _get_processor_use_fast_override() is expected


def test_processor_use_fast_override_rejects_invalid_value(monkeypatch):
    monkeypatch.setenv("VLLM_PROCESSOR_USE_FAST", "sometimes")

    with pytest.raises(ValueError, match="must be a boolean"):
        _get_processor_use_fast_override()


def test_compile_mm_encoder_override_preserves_compilation_config(monkeypatch):
    monkeypatch.setenv("VLLM_COMPILE_MM_ENCODER", "true")
    engine_args = {"compilation_config": {"cudagraph_mode": "piecewise"}}

    assert _apply_compile_mm_encoder_override(engine_args, {"compilation_config"})
    assert engine_args["compilation_config"] == {
        "cudagraph_mode": "piecewise",
        "compile_mm_encoder": True,
    }


def test_forward_supported_profiler_config():
    profiler_config = object()
    engine_args = {}

    forwarded = _forward_supported_engine_arg(
        engine_args,
        {"profiler_config"},
        {"profiler_config": profiler_config},
        "profiler_config",
    )

    assert forwarded is True
    assert engine_args == {"profiler_config": profiler_config}


def test_does_not_forward_unsupported_engine_arg():
    engine_args = {}

    forwarded = _forward_supported_engine_arg(
        engine_args,
        set(),
        {"profiler_config": object()},
        "profiler_config",
    )

    assert forwarded is False
    assert engine_args == {}


class _FakeSession:
    def __init__(self, stream_id: str):
        self.stream_id = stream_id
        self.started = False
        self.closed = False
        self.frames = []

    def start(self):
        self.started = True

    async def push_frame(self, frame, *, generate=True):
        self.frames.append((frame, generate))
        if not generate:
            return None
        return {"stream_id": self.stream_id, "frame": frame.value}

    async def push_frames(self, frames, *, generate=True):
        self.frames.append((list(frames), generate))
        if not generate:
            return None
        return {"stream_id": self.stream_id, "frame": frames[-1].value}

    async def close(self):
        self.closed = True


class _PushUntilClosedSession(_FakeSession):
    def __init__(self, stream_id: str):
        super().__init__(stream_id)
        self.push_started = asyncio.Event()
        self.closed_event = asyncio.Event()

    async def push_frame(self, frame, *, generate=True):
        assert generate is True
        self.push_started.set()
        await self.closed_event.wait()
        return {"stream_id": self.stream_id, "frame": frame.value}

    async def close(self):
        self.closed = True
        self.closed_event.set()


def test_native_streaming_enables_safe_async_scheduling_and_disables_prefix_cache():
    engine_args = {"model": "/models/cr3"}

    apply_native_streaming_engine_args(
        engine_args,
        {"model", "async_scheduling", "enable_prefix_caching"},
        async_scheduling=True,
    )

    assert engine_args["async_scheduling"] is True
    assert engine_args["enable_prefix_caching"] is False


def test_native_streaming_keeps_async_scheduling_opt_in():
    engine_args = {}

    apply_native_streaming_engine_args(engine_args, {"async_scheduling"})

    assert engine_args["async_scheduling"] is False


def test_native_streaming_requires_async_scheduling_control():
    with pytest.raises(RuntimeError, match="cannot configure async scheduling"):
        apply_native_streaming_engine_args({}, {"model"})


@pytest.mark.parametrize(
    ("frame_count", "expected_segments"),
    [(1, 1), (2, 1), (15, 8), (16, 8)],
)
def test_temporal_video_segment_count_uses_two_frame_chunks(
    frame_count,
    expected_segments,
):
    assert temporal_video_segment_count(frame_count) == expected_segments


def test_temporal_video_segment_count_rejects_invalid_frame_count():
    with pytest.raises(ValueError, match="frame_count must be at least 1"):
        temporal_video_segment_count(0)


class _SessionFactory:
    def __init__(self):
        self.sessions = {}

    def __call__(self, stream_id, _config):
        session = _FakeSession(stream_id)
        self.sessions[stream_id] = session
        return session


def _run(awaitable):
    return asyncio.run(awaitable)


class _UnsupportedModel(BaseVlmModel):
    def _initialize_model(self, **kwargs):
        pass

    def _shutdown_model(self):
        pass

    @property
    def model_name(self):
        return "unsupported"

    def generate(self, *args, **kwargs):
        return []

    def can_enqueue_requests(self):
        return True

    @staticmethod
    def get_input_config():
        return None

    @staticmethod
    def get_model_info(model_path, vlm_model_type=""):
        return "", "", ""


def test_base_model_streaming_capability_is_fail_closed():
    model = _UnsupportedModel()

    assert model.supports_streaming_vlm() is False
    with pytest.raises(NotImplementedError, match="Streaming VLM"):
        model.start_streaming_vlm_session("stream-a", "Describe changes")
    with pytest.raises(NotImplementedError, match="Streaming VLM"):
        model.generate_streaming_vlm_step(
            "stream-a",
            "Describe changes",
            [],
        )
    assert model.end_streaming_vlm_session("stream-a", "stream-a") is None


def test_vlm_query_keeps_chunked_default_and_exposes_ordered_streaming():
    default_query = VlmQuery(id=uuid4(), prompt="Describe.", model="model")
    streaming_query = VlmQuery(
        id=uuid4(),
        prompt="Describe.",
        model="model",
        inference_mode="streaming_vlm",
        streaming_frame_policy="ordered",
        streaming_window_frames=16,
    )

    assert default_query.inference_mode is VlmInferenceMode.CHUNKED
    assert streaming_query.inference_mode is VlmInferenceMode.STREAMING_VLM
    assert streaming_query.streaming_frame_policy is StreamingFramePolicy.ORDERED
    assert streaming_query.streaming_window_frames == 16
    assert VlmRequestParams.from_vlm_query(default_query).streaming_question_on_decode is False


def test_streaming_question_on_decode_reaches_request_params():
    query = VlmQuery(
        id=uuid4(),
        prompt="Is anyone missing a safety vest?",
        model="model",
        inference_mode="streaming_vlm",
        streaming_question_on_decode=True,
    )

    assert VlmRequestParams.from_vlm_query(query).streaming_question_on_decode is True


def test_manager_reuses_one_native_session_per_stream():
    factory = _SessionFactory()
    manager = DlalgoStreamingSessionManager(factory, max_sessions=2)
    config = DlalgoSessionConfig(system_prompt="Describe changes.")

    async def exercise():
        first = await manager.ensure_session("stream-a", config)
        second = await manager.ensure_session("stream-a", config)
        return first, second

    first, second = _run(exercise())

    assert first is second
    assert first.started is True
    assert manager.active_stream_ids == ("stream-a",)


def test_manager_passes_decoded_frame_without_encoding():
    manager = DlalgoStreamingSessionManager(_SessionFactory(), max_sessions=1)
    frame = _DecodedFrame(7)

    async def exercise():
        await manager.ensure_session("stream-a", DlalgoSessionConfig())
        return await manager.push_frame("stream-a", frame)

    response = _run(exercise())

    assert response == {"stream_id": "stream-a", "frame": 7}
    assert manager._sessions["stream-a"].frames == [(frame, True)]


def test_manager_forwards_append_only_frame_without_waiting_for_output():
    manager = DlalgoStreamingSessionManager(_SessionFactory(), max_sessions=1)
    frame = _DecodedFrame(7)

    async def exercise():
        await manager.ensure_session("stream-a", DlalgoSessionConfig())
        return await manager.push_frame("stream-a", frame, generate=False)

    response = _run(exercise())

    assert response is None
    assert manager._sessions["stream-a"].frames == [(frame, False)]


def test_manager_appends_decoded_frames_as_one_batch():
    manager = DlalgoStreamingSessionManager(_SessionFactory(), max_sessions=1)
    frames = [_DecodedFrame(1), _DecodedFrame(2), _DecodedFrame(3)]

    async def exercise():
        await manager.ensure_session("stream-a", DlalgoSessionConfig())
        return await manager.push_frames("stream-a", frames)

    response = _run(exercise())

    assert response == {"stream_id": "stream-a", "frame": 3}
    assert manager._sessions["stream-a"].frames == [(frames, True)]


def test_manager_defers_decode_until_configured_interval():
    manager = DlalgoStreamingSessionManager(_SessionFactory(), max_sessions=1)

    async def exercise():
        await manager.ensure_session(
            "stream-a",
            DlalgoSessionConfig(decode_interval_steps=3),
        )
        return [
            await manager.push_frames(
                "stream-a",
                [_DecodedFrame(index)],
                generate=None,
            )
            for index in range(3)
        ]

    outputs = _run(exercise())

    assert outputs[:2] == [None, None]
    assert outputs[2] == {"stream_id": "stream-a", "frame": 2}
    assert manager._sessions["stream-a"].frames == [
        ([_DecodedFrame(0)], False),
        ([_DecodedFrame(1)], False),
        ([_DecodedFrame(2)], True),
    ]


def test_manager_keeps_retention_and_decode_state_isolated_per_stream():
    manager = DlalgoStreamingSessionManager(_SessionFactory(), max_sessions=2)
    config_a = DlalgoSessionConfig(
        text_round=16,
        reprefill_relocation_interval=512,
        text_sink_tokens=512,
        text_sliding_window_tokens=512,
        decode_interval_steps=2,
    )
    config_b = DlalgoSessionConfig(
        text_round=32,
        reprefill_relocation_interval=128,
        text_sink_tokens=64,
        text_sliding_window_tokens=128,
        decode_interval_steps=3,
    )

    async def exercise():
        await manager.ensure_session("stream-a", config_a)
        await manager.ensure_session("stream-b", config_b)
        first_a = await manager.push_frames("stream-a", [_DecodedFrame(1)], generate=None)
        first_b = await manager.push_frames("stream-b", [_DecodedFrame(2)], generate=None)
        closed_a = await manager.close_session("stream-a")
        second_b = await manager.push_frames("stream-b", [_DecodedFrame(3)], generate=None)
        third_b = await manager.push_frames("stream-b", [_DecodedFrame(4)], generate=None)
        return first_a, first_b, closed_a, second_b, third_b

    first_a, first_b, closed_a, second_b, third_b = _run(exercise())

    assert (first_a, first_b, second_b) == (None, None, None)
    assert closed_a is True
    assert third_b == {"stream_id": "stream-b", "frame": 4}
    assert manager.active_stream_ids == ("stream-b",)
    assert manager._configs == {"stream-b": config_b}
    assert manager._completed_steps == {"stream-b": 3}


def test_manager_rejects_invalid_decode_interval():
    manager = DlalgoStreamingSessionManager(_SessionFactory(), max_sessions=1)

    async def exercise():
        with pytest.raises(ValueError, match="decode_interval_steps"):
            await manager.ensure_session(
                "stream-a",
                DlalgoSessionConfig(decode_interval_steps=0),
            )

    _run(exercise())


@pytest.mark.parametrize("encoded", [b"jpeg", bytearray(b"png"), memoryview(b"image")])
def test_manager_rejects_encoded_frame_payloads(encoded):
    manager = DlalgoStreamingSessionManager(_SessionFactory(), max_sessions=1)

    async def exercise():
        await manager.ensure_session("stream-a", DlalgoSessionConfig())
        with pytest.raises(TypeError, match="decoded frame"):
            await manager.push_frame("stream-a", encoded)

    _run(exercise())


def test_manager_enforces_session_capacity():
    manager = DlalgoStreamingSessionManager(_SessionFactory(), max_sessions=1)

    async def exercise():
        await manager.ensure_session("stream-a", DlalgoSessionConfig())
        with pytest.raises(StreamingSessionCapacityError, match="1 active session"):
            await manager.ensure_session("stream-b", DlalgoSessionConfig())

    _run(exercise())


def test_manager_close_aborts_inflight_push_before_waiting_for_push_lock():
    factory = _SessionFactory()
    manager = DlalgoStreamingSessionManager(factory, max_sessions=1)

    async def exercise():
        await manager.ensure_session("stream-a", DlalgoSessionConfig())
        session = _PushUntilClosedSession("stream-a")
        manager._sessions["stream-a"] = session
        push = asyncio.create_task(manager.push_frame("stream-a", _DecodedFrame(1)))
        await session.push_started.wait()

        closed = await asyncio.wait_for(manager.close_session("stream-a"), timeout=0.5)
        response = await asyncio.wait_for(push, timeout=0.5)
        return closed, response, session

    closed, response, session = _run(exercise())

    assert closed is True
    assert response == {"stream_id": "stream-a", "frame": 1}
    assert session.closed is True
    assert manager.session_count == 0


def test_worker_session_close_does_not_block_lifecycle_commands():
    close_started = Event()
    release_close = Event()
    model = MagicMock()

    def blocking_close(_stream_id, _session):
        close_started.set()
        release_close.wait(timeout=2)

    model.end_streaming_vlm_session.side_effect = blocking_close
    process = object.__new__(VlmProcess)
    process._model = model
    process._streaming_vlm_sessions = {
        ("stream-a", "request-1"): "session-a",
        ("stream-a", "request-2"): "session-b",
    }
    process._closed_streaming_vlm_streams = {}
    process._streaming_vlm_lock = Lock()

    process._handle_command(
        "close-streaming-vlm-session", stream_id="stream-a", request_id="request-1"
    )
    assert set(process._streaming_vlm_sessions) == {("stream-a", "request-2")}
    assert "stream-a" not in process._closed_streaming_vlm_streams

    started = time.monotonic()
    process._handle_command("close-streaming-vlm-session", stream_id="stream-a")
    elapsed = time.monotonic() - started

    assert elapsed < 0.2
    assert close_started.wait(timeout=1)
    assert not process._streaming_vlm_sessions
    assert "stream-a" in process._closed_streaming_vlm_streams
    release_close.set()
    process._handle_command("open-streaming-vlm-session", stream_id="stream-a")
    assert "stream-a" not in process._closed_streaming_vlm_streams


def test_live_queries_on_one_stream_keep_separate_native_sessions():
    process = object.__new__(VlmProcess)
    process._model = MagicMock()
    process._model.supports_streaming_vlm.return_value = True
    process._model.start_streaming_vlm_session.side_effect = lambda **_kwargs: object()
    process._model.generate_streaming_vlm_step.return_value = [VlmModelOutput(output="ok")]
    process._refresh_model_health = MagicMock(return_value=True)
    process._num_gpus = 1
    process._streaming_vlm_sessions = {}
    process._closed_streaming_vlm_streams = {}
    process._streaming_vlm_lock = Lock()
    chunk = ChunkInfo()
    chunk.streamId = "stream-a"
    frame = np.zeros((2, 2, 3), dtype=np.uint8)

    with patch("vlm_pipeline.vlm_pipeline.nvtx"):
        for request_id, prompt in (("request-1", "Describe vehicles"), ("request-2", "Describe people")):
            params = VlmRequestParams(
                vlm_prompt=prompt,
                vlm_generation_config=VlmGenerationConfig(),
                inference_mode="streaming_vlm",
            )
            process._process(
                [chunk], [params], frames=[[frame]], frame_times=[[0.0]],
                is_live_stream=[True], request_id=[request_id], decode_only=[False],
            )

    assert set(process._streaming_vlm_sessions) == {
        ("stream-a", "request-1"),
        ("stream-a", "request-2"),
    }
    assert process._streaming_vlm_sessions[("stream-a", "request-1")] is not (
        process._streaming_vlm_sessions[("stream-a", "request-2")]
    )
    assert [call.kwargs["query"] for call in process._model.start_streaming_vlm_session.call_args_list] == [
        "Describe vehicles", "Describe people"
    ]
    process._handle_command(
        "close-streaming-vlm-session", stream_id="stream-a", request_id="request-1"
    )
    assert ("stream-a", "request-1") in process._closed_streaming_vlm_streams
    assert ("stream-a", "request-2") in process._streaming_vlm_sessions
    params = VlmRequestParams(
        vlm_prompt="Describe vehicles",
        vlm_generation_config=VlmGenerationConfig(),
        inference_mode="streaming_vlm",
    )
    with patch("vlm_pipeline.vlm_pipeline.nvtx"):
        assert process._process(
            [chunk], [params], frames=[[frame]], frame_times=[[1.0]],
            is_live_stream=[True], request_id=["request-1"], decode_only=[False],
        ) == {}


def test_unsubscribe_during_native_session_creation_does_not_leak_session():
    creation_started = Event()
    release_creation = Event()
    session_closed = Event()
    process = object.__new__(VlmProcess)
    process._model = MagicMock()
    process._model.supports_streaming_vlm.return_value = True

    def start_session(**_kwargs):
        creation_started.set()
        release_creation.wait(timeout=2)
        return object()

    process._model.start_streaming_vlm_session.side_effect = start_session
    process._model.end_streaming_vlm_session.side_effect = lambda *_args: session_closed.set()
    process._refresh_model_health = MagicMock(return_value=True)
    process._num_gpus = 1
    process._streaming_vlm_sessions = {}
    process._closed_streaming_vlm_streams = {}
    process._streaming_vlm_lock = Lock()
    chunk = ChunkInfo(streamId="stream-a")
    params = VlmRequestParams(
        vlm_prompt="Describe vehicles",
        vlm_generation_config=VlmGenerationConfig(),
        inference_mode="streaming_vlm",
    )
    result = {}

    def run_inference():
        result["output"] = process._process(
            [chunk], [params], frames=[[np.zeros((2, 2, 3), dtype=np.uint8)]],
            frame_times=[[0.0]], is_live_stream=[True], request_id=["request-1"],
            decode_only=[False],
        )

    with patch("vlm_pipeline.vlm_pipeline.nvtx"):
        worker = Thread(target=run_inference)
        worker.start()
        assert creation_started.wait(timeout=1)
        process._handle_command(
            "close-streaming-vlm-session", stream_id="stream-a", request_id="request-1"
        )
        release_creation.set()
        worker.join(timeout=2)

    assert not worker.is_alive()
    assert result["output"] == {}
    assert not process._streaming_vlm_sessions
    assert session_closed.wait(timeout=1)
    process._model.generate_streaming_vlm_step.assert_not_called()


def test_manager_enforces_aggregate_video_segment_budget():
    manager = DlalgoStreamingSessionManager(
        _SessionFactory(),
        max_sessions=2,
        max_total_video_segments=24,
    )

    async def exercise():
        await manager.ensure_session(
            "stream-a",
            DlalgoSessionConfig(max_video_segments=16),
        )
        with pytest.raises(StreamingSessionCapacityError, match="video segment budget"):
            await manager.ensure_session(
                "stream-b",
                DlalgoSessionConfig(max_video_segments=16),
            )
        await manager.close_session("stream-a")
        await manager.ensure_session(
            "stream-b",
            DlalgoSessionConfig(max_video_segments=16),
        )

    _run(exercise())
    assert manager.active_stream_ids == ("stream-b",)


def test_closing_one_stream_does_not_close_another():
    factory = _SessionFactory()
    manager = DlalgoStreamingSessionManager(factory, max_sessions=2)

    async def exercise():
        await manager.ensure_session("stream-a", DlalgoSessionConfig())
        await manager.ensure_session("stream-b", DlalgoSessionConfig())
        closed = await manager.close_session("stream-a")
        response = await manager.push_frame("stream-b", _DecodedFrame(9))
        return closed, response

    closed, response = _run(exercise())

    assert closed is True
    assert factory.sessions["stream-a"].closed is True
    assert factory.sessions["stream-b"].closed is False
    assert manager.active_stream_ids == ("stream-b",)
    assert response == {
        "stream_id": "stream-b",
        "frame": 9,
    }


def test_closed_stream_can_be_recreated_without_replacing_peer_session():
    factory = _SessionFactory()
    manager = DlalgoStreamingSessionManager(factory, max_sessions=2)

    async def exercise():
        await manager.ensure_session("stream-a", DlalgoSessionConfig())
        await manager.ensure_session("stream-b", DlalgoSessionConfig())
        original_a = factory.sessions["stream-a"]
        original_b = factory.sessions["stream-b"]

        await manager.close_session("stream-a")
        await manager.ensure_session("stream-a", DlalgoSessionConfig())
        recreated_a = factory.sessions["stream-a"]
        response_a = await manager.push_frame("stream-a", _DecodedFrame(10))
        response_b = await manager.push_frame("stream-b", _DecodedFrame(11))
        return original_a, original_b, recreated_a, response_a, response_b

    original_a, original_b, recreated_a, response_a, response_b = _run(exercise())

    assert original_a.closed is True
    assert recreated_a is not original_a
    assert recreated_a.closed is False
    assert factory.sessions["stream-b"] is original_b
    assert original_b.closed is False
    assert set(manager.active_stream_ids) == {"stream-a", "stream-b"}
    assert response_a == {"stream_id": "stream-a", "frame": 10}
    assert response_b == {"stream_id": "stream-b", "frame": 11}


def test_close_all_releases_every_session():
    factory = _SessionFactory()
    manager = DlalgoStreamingSessionManager(factory, max_sessions=2)

    async def exercise():
        await manager.ensure_session("stream-a", DlalgoSessionConfig())
        await manager.ensure_session("stream-b", DlalgoSessionConfig())
        await manager.close_all()

    _run(exercise())

    assert manager.active_stream_ids == ()
    assert manager._configs == {}
    assert manager._completed_steps == {}
    assert all(session.closed for session in factory.sessions.values())


def test_push_requires_an_active_stream():
    manager = DlalgoStreamingSessionManager(_SessionFactory(), max_sessions=1)

    async def exercise():
        with pytest.raises(StreamingSessionNotFoundError, match="stream-missing"):
            await manager.push_frame("stream-missing", _DecodedFrame(1))

    _run(exercise())


def test_manager_serializes_concurrent_pushes_within_one_stream():
    active = 0
    max_active = 0

    class _OrderedSession(_FakeSession):
        async def push_frame(self, frame, *, generate=True):
            assert generate is True
            nonlocal active, max_active
            active += 1
            max_active = max(max_active, active)
            await asyncio.sleep(0)
            active -= 1
            return frame.value

    manager = DlalgoStreamingSessionManager(
        lambda stream_id, _config: _OrderedSession(stream_id),
        max_sessions=1,
    )

    async def exercise():
        await manager.ensure_session("stream-a", DlalgoSessionConfig())
        return await asyncio.gather(
            manager.push_frame("stream-a", _DecodedFrame(1)),
            manager.push_frame("stream-a", _DecodedFrame(2)),
        )

    assert _run(exercise()) == [1, 2]
    assert max_active == 1


def test_manager_allows_concurrent_pushes_across_streams():
    active = 0
    max_active = 0
    both_active = asyncio.Event()

    class _ConcurrentSession(_FakeSession):
        async def push_frame(self, frame, *, generate=True):
            assert generate is True
            nonlocal active, max_active
            active += 1
            max_active = max(max_active, active)
            if active == 2:
                both_active.set()
            await asyncio.wait_for(both_active.wait(), timeout=0.5)
            active -= 1
            return {"stream_id": self.stream_id, "frame": frame.value}

    manager = DlalgoStreamingSessionManager(
        lambda stream_id, _config: _ConcurrentSession(stream_id),
        max_sessions=2,
    )

    async def exercise():
        await manager.ensure_session("stream-a", DlalgoSessionConfig())
        await manager.ensure_session("stream-b", DlalgoSessionConfig())
        return await asyncio.gather(
            manager.push_frame("stream-a", _DecodedFrame(1)),
            manager.push_frame("stream-b", _DecodedFrame(2)),
        )

    assert _run(exercise()) == [
        {"stream_id": "stream-a", "frame": 1},
        {"stream_id": "stream-b", "frame": 2},
    ]
    assert max_active == 2


def test_vllm_factory_builds_source_session_with_retention(monkeypatch):
    created = {}

    class _SamplingParams:
        def __init__(self, **kwargs):
            created["sampling_instance"] = self
            created["sampling"] = kwargs

    class _Retention:
        def __init__(self, **kwargs):
            created["retention_instance"] = self
            created["retention"] = kwargs

    class _StreamingSession:
        def __init__(self, *args, **kwargs):
            created["session_args"] = args
            created["session_kwargs"] = kwargs

    sampling_module = types.ModuleType("vllm.sampling_params")
    sampling_module.SamplingParams = _SamplingParams
    sampling_module.RequestOutputKind = types.SimpleNamespace(DELTA="delta")
    session_module = types.ModuleType("vllm.entrypoints.openai.streaming.session")
    session_module.StreamingSession = _StreamingSession
    retention_module = types.ModuleType("vllm.v1.streaming.retention")
    retention_module.StreamingRetentionParams = _Retention
    monkeypatch.setitem(sys.modules, "vllm.sampling_params", sampling_module)
    monkeypatch.setitem(
        sys.modules,
        "vllm.entrypoints.openai.streaming.session",
        session_module,
    )
    monkeypatch.setitem(sys.modules, "vllm.v1.streaming.retention", retention_module)

    renderer = object()
    engine = types.SimpleNamespace(renderer=renderer)
    config = DlalgoSessionConfig(
        system_prompt="Watch traffic.",
        question="What changed?",
        fps=2.0,
        max_tokens=20,
        min_tokens=20,
        ignore_eos=True,
        temperature=0.0,
        top_p=0.8,
        top_k=50,
        repetition_penalty=1.0,
        seed=42,
        max_video_segments=16,
        max_text_tokens=256,
        max_session_tokens=7000,
        reprefill_threshold=0.7,
        exact_video_compaction=True,
        text_round=16,
        reprefill_relocation_interval=512,
        text_sink_tokens=512,
        text_sliding_window_tokens=512,
        previous_text="Prior commentary.",
        time_offset_s=1000.0,
        mm_processor_kwargs={"use_fast": False},
        structured_outputs={"choice": ["yes", "no"]},
        question_on_decode=True,
        absolute_segment_timestamps=True,
        retain_generated_text=False,
    )

    session = VllmDlalgoSessionFactory(engine)("stream-a", config)

    assert isinstance(session, _StreamingSession)
    assert created["sampling"] == {
        "max_tokens": 20,
        "min_tokens": 20,
        "ignore_eos": True,
        "temperature": 0.0,
        "top_p": 0.8,
        "top_k": 50,
        "repetition_penalty": 1.0,
        "seed": 42,
        "structured_outputs": {"choice": ["yes", "no"]},
        "output_kind": "delta",
        "extra_args": {"streaming_retention": created["retention_instance"]},
    }
    assert created["retention"] == {
        "max_video_segments": 16,
        "max_text_tokens": 256,
        "max_session_tokens": 7000,
        "reprefill_threshold": 0.7,
        "exact_video_compaction": True,
        "text_round": 16,
        "reprefill_relocation_interval": 512,
        "text_sink_tokens": 512,
        "text_sliding_window_tokens": 512,
        "retain_generated_text": False,
    }
    assert created["session_args"][:4] == (
        "stream-a",
        engine,
        created["sampling_instance"],
        renderer,
    )
    assert created["session_kwargs"] == {
        "system_prompt": "Watch traffic.",
        "question": "What changed?",
        "fps": 2.0,
        "previous_text": "Prior commentary.",
        "time_offset_s": 1000.0,
        "mm_processor_kwargs": {"use_fast": False},
        "question_on_decode": True,
        "absolute_segment_timestamps": True,
    }


def test_vllm_factory_rejects_relocation_interval_without_source_support(monkeypatch):
    class _SamplingParams:
        def __init__(self, **kwargs):
            pass

    class _Retention:
        def __init__(
            self,
            max_video_segments,
            max_text_tokens,
            max_session_tokens,
            reprefill_threshold,
            text_round,
            text_sink_tokens,
            text_sliding_window_tokens,
        ):
            pass

    class _StreamingSession:
        def __init__(self, *args, **kwargs):
            pass

    sampling_module = types.ModuleType("vllm.sampling_params")
    sampling_module.SamplingParams = _SamplingParams
    sampling_module.RequestOutputKind = types.SimpleNamespace(DELTA="delta")
    session_module = types.ModuleType("vllm.entrypoints.openai.streaming.session")
    session_module.StreamingSession = _StreamingSession
    retention_module = types.ModuleType("vllm.v1.streaming.retention")
    retention_module.StreamingRetentionParams = _Retention
    monkeypatch.setitem(sys.modules, "vllm.sampling_params", sampling_module)
    monkeypatch.setitem(
        sys.modules,
        "vllm.entrypoints.openai.streaming.session",
        session_module,
    )
    monkeypatch.setitem(sys.modules, "vllm.v1.streaming.retention", retention_module)

    factory = VllmDlalgoSessionFactory(types.SimpleNamespace(renderer=object()))

    with pytest.raises(RuntimeError, match="per-session periodic re-prefill"):
        factory(
            "stream-a",
            DlalgoSessionConfig(
                text_round=16,
                reprefill_relocation_interval=512,
            ),
        )


def test_vllm_factory_rejects_exact_compaction_without_source_support(monkeypatch):
    class _SamplingParams:
        def __init__(self, **kwargs):
            pass

    class _Retention:
        def __init__(
            self,
            max_video_segments,
            max_text_tokens,
            max_session_tokens,
            reprefill_threshold,
            text_round,
            reprefill_relocation_interval,
            text_sink_tokens,
            text_sliding_window_tokens,
        ):
            pass

    class _StreamingSession:
        def __init__(self, *args, **kwargs):
            pass

    sampling_module = types.ModuleType("vllm.sampling_params")
    sampling_module.SamplingParams = _SamplingParams
    sampling_module.RequestOutputKind = types.SimpleNamespace(DELTA="delta")
    session_module = types.ModuleType("vllm.entrypoints.openai.streaming.session")
    session_module.StreamingSession = _StreamingSession
    retention_module = types.ModuleType("vllm.v1.streaming.retention")
    retention_module.StreamingRetentionParams = _Retention
    monkeypatch.setitem(sys.modules, "vllm.sampling_params", sampling_module)
    monkeypatch.setitem(
        sys.modules,
        "vllm.entrypoints.openai.streaming.session",
        session_module,
    )
    monkeypatch.setitem(sys.modules, "vllm.v1.streaming.retention", retention_module)

    factory = VllmDlalgoSessionFactory(types.SimpleNamespace(renderer=object()))

    with pytest.raises(RuntimeError, match="exact video compaction"):
        factory(
            "stream-a",
            DlalgoSessionConfig(exact_video_compaction=True),
        )


def test_model_forwards_sampling_controls_to_native_session():
    captured = {}

    class _Manager:
        async def ensure_session(self, stream_id, config):
            captured["stream_id"] = stream_id
            captured["config"] = config

    loop = asyncio.new_event_loop()
    loop_thread = Thread(target=loop.run_forever)
    loop_thread.start()
    model = object.__new__(VllmCompatible)
    model._dlalgo_streaming_manager = _Manager()
    model._event_loop = loop
    model._system_prompt = ""

    try:
        model.start_streaming_vlm_session(
            "stream-a",
            "Describe changes.",
            generation_config=VlmGenerationConfig(
                max_new_tokens=20,
                min_tokens=20,
                ignore_eos=True,
                top_p=1.0,
                top_k=50,
                mm_processor_kwargs={"use_fast": False},
            ),
            streaming_config={
                "window_frames": 16,
                "exact_video_compaction": True,
                "text_round": 16,
                "reprefill_relocation_interval": 512,
                "text_sink_tokens": 256,
                "text_sliding_window_tokens": 384,
                "retain_generated_text": False,
            },
        )
    finally:
        loop.call_soon_threadsafe(loop.stop)
        loop_thread.join()
        loop.close()

    assert captured["stream_id"].startswith("stream-a:stream-a:")
    assert captured["config"].max_tokens == 20
    assert captured["config"].min_tokens == 20
    assert captured["config"].ignore_eos is True
    assert captured["config"].top_p == 1.0
    assert captured["config"].top_k == 50
    assert captured["config"].max_video_segments == 8
    assert captured["config"].exact_video_compaction is True
    assert captured["config"].text_round == 16
    assert captured["config"].reprefill_relocation_interval == 512
    assert captured["config"].text_sink_tokens == 256
    assert captured["config"].text_sliding_window_tokens == 384
    assert captured["config"].retain_generated_text is False
    assert captured["config"].mm_processor_kwargs == {"use_fast": False}


def test_model_uses_distinct_native_ids_for_live_subscribers():
    sessions = set()

    class _Manager:
        async def ensure_session(self, session_id, _config):
            sessions.add(session_id)

        async def close_session(self, session_id):
            sessions.remove(session_id)
            return True

    loop = asyncio.new_event_loop()
    loop_thread = Thread(target=loop.run_forever)
    loop_thread.start()
    model = object.__new__(VllmCompatible)
    model._dlalgo_streaming_manager = _Manager()
    model._dlalgo_last_frame_time_by_stream = {}
    model._event_loop = loop
    model._system_prompt = ""

    try:
        first = model.start_streaming_vlm_session(
            "stream-a", "Describe vehicles", request_id="request-1"
        )
        second = model.start_streaming_vlm_session(
            "stream-a", "Describe people", request_id="request-2"
        )
        assert first != second
        assert len(sessions) == 2
        assert model.end_streaming_vlm_session("stream-a", first) is True
        assert sessions == {second[1]}
        reopened = model.start_streaming_vlm_session(
            "stream-a", "Describe vehicles", request_id="request-1"
        )
        assert reopened != first
        assert sessions == {second[1], reopened[1]}
    finally:
        loop.call_soon_threadsafe(loop.stop)
        loop_thread.join()
        loop.close()


def test_model_rejects_unbounded_raw_key_shadow_without_text_relocation(monkeypatch):
    monkeypatch.setenv("VLLM_STREAMING_VLM_RAW_KEY_SHADOW", "true")
    model = object.__new__(VllmCompatible)
    model._dlalgo_streaming_manager = object()
    model._system_prompt = ""

    with pytest.raises(ValueError, match="requires text_round > 0"):
        model.start_streaming_vlm_session(
            "stream-a",
            "Describe changes.",
            streaming_config={"text_round": 0},
        )


def test_model_allows_raw_key_shadow_with_exact_compaction(monkeypatch):
    captured = {}

    class _Manager:
        async def ensure_session(self, _stream_id, config):
            captured["config"] = config

    loop = asyncio.new_event_loop()
    loop_thread = Thread(target=loop.run_forever)
    loop_thread.start()
    model = object.__new__(VllmCompatible)
    model._dlalgo_streaming_manager = _Manager()
    model._event_loop = loop
    model._system_prompt = ""
    monkeypatch.setenv("VLLM_STREAMING_VLM_RAW_KEY_SHADOW", "true")
    monkeypatch.setenv("RTVI_STREAMING_VLM_EXACT_VIDEO_COMPACTION", "true")

    try:
        model.start_streaming_vlm_session(
            "stream-a",
            "Describe changes.",
            streaming_config={"text_round": 0},
        )
    finally:
        loop.call_soon_threadsafe(loop.stop)
        loop_thread.join()
        loop.close()

    assert captured["config"].exact_video_compaction is True


def test_model_decodes_once_after_appending_a_multi_frame_update():
    calls = []

    class _Response:
        text = "A worker moves a pallet."
        token_count = 20
        prompt_token_count = 317
        vision_token_count = 299
        frame_count = 2
        frame_index = 15
        finish_reason = "length"
        ttft_s = 0.1
        latency_s = 0.2

    class _Manager:
        async def push_frames(self, stream_id, frames, *, generate=True):
            calls.append((stream_id, list(frames), generate))
            return _Response()

    loop = asyncio.new_event_loop()
    loop_thread = Thread(target=loop.run_forever)
    loop_thread.start()
    model = object.__new__(VllmCompatible)
    model._dlalgo_streaming_manager = _Manager()
    model._dlalgo_last_frame_time_by_stream = {}
    model._event_loop = loop
    frames = [np.zeros((6, 8, 3), dtype=np.uint8) for _ in range(16)]

    try:
        future = model.generate_streaming_vlm_step(
            session="stream-a",
            query="Describe changes.",
            chunks=[types.SimpleNamespace(streamId="stream-a")],
            video_frames=[frames],
        )
        outputs = future.result(timeout=1)
    finally:
        loop.call_soon_threadsafe(loop.stop)
        loop_thread.join()
        loop.close()

    assert len(calls) == 1
    assert calls[0][0] == "stream-a"
    assert len(calls[0][1]) == 16
    assert calls[0][2] is None
    assert len(outputs) == 1
    assert outputs[0].input_tokens == 317
    assert outputs[0].output_tokens == 20
    assert outputs[0].streaming_metrics["frames_processed"] == 16
    assert outputs[0].streaming_metrics["processed_frames"] == 2
    assert outputs[0].streaming_metrics["prompt_tokens"] == 317
    assert outputs[0].streaming_metrics["vision_tokens"] == 299
    assert outputs[0].streaming_metrics["native_new_vision_tokens"] == 299


def test_model_returns_no_output_for_deferred_decode_step():
    class _Manager:
        async def push_frames(self, stream_id, frames, *, generate=True):
            assert stream_id == "stream-a"
            assert len(frames) == 2
            assert generate is None
            return None

    loop = asyncio.new_event_loop()
    loop_thread = Thread(target=loop.run_forever)
    loop_thread.start()
    model = object.__new__(VllmCompatible)
    model._dlalgo_streaming_manager = _Manager()
    model._dlalgo_last_frame_time_by_stream = {}
    model._event_loop = loop
    frame = np.zeros((6, 8, 3), dtype=np.uint8)

    try:
        outputs = model.generate_streaming_vlm_step(
            session="stream-a",
            query="Describe changes.",
            chunks=[types.SimpleNamespace(streamId="stream-a")],
            video_frames=[[frame, frame]],
        ).result(timeout=1)
    finally:
        loop.call_soon_threadsafe(loop.stop)
        loop_thread.join()
        loop.close()

    assert outputs == []


def test_model_appends_only_new_frames_from_overlapping_updates():
    calls = []

    class _Response:
        text = "Workers continue moving boxes."
        token_count = 20
        frame_index = 23
        finish_reason = "length"
        ttft_s = 0.1
        latency_s = 0.2

    class _Manager:
        async def push_frames(self, stream_id, frames, *, generate=True):
            calls.append((stream_id, list(frames), generate))
            return _Response()

    loop = asyncio.new_event_loop()
    loop_thread = Thread(target=loop.run_forever)
    loop_thread.start()
    model = object.__new__(VllmCompatible)
    model._dlalgo_streaming_manager = _Manager()
    model._dlalgo_last_frame_time_by_stream = {}
    model._event_loop = loop
    frame = np.zeros((6, 8, 3), dtype=np.uint8)

    try:
        first = model.generate_streaming_vlm_step(
            session="stream-a",
            query="Describe changes.",
            chunks=[types.SimpleNamespace(streamId="stream-a")],
            video_frames=[[frame] * 16],
            video_frames_times=[list(range(16))],
        ).result(timeout=1)
        first_call_count = len(calls)
        second = model.generate_streaming_vlm_step(
            session="stream-a",
            query="Describe changes.",
            chunks=[types.SimpleNamespace(streamId="stream-a")],
            video_frames=[[frame] * 16],
            video_frames_times=[list(range(8, 24))],
        ).result(timeout=1)
    finally:
        loop.call_soon_threadsafe(loop.stop)
        loop_thread.join()
        loop.close()

    assert first_call_count == 1
    assert len(calls) == 2
    assert len(calls[0][1]) == 16
    assert len(calls[1][1]) == 8
    assert first[0].streaming_metrics["frames_received"] == 16
    assert first[0].streaming_metrics["frames_processed"] == 16
    assert second[0].streaming_metrics["frames_received"] == 16
    assert second[0].streaming_metrics["frames_processed"] == 8


def test_vllm_factory_requires_source_patch(monkeypatch):
    real_import = builtins.__import__

    def import_without_source_patch(name, *args, **kwargs):
        if name == "vllm.entrypoints.openai.streaming.session":
            raise ImportError("source patch unavailable")
        return real_import(name, *args, **kwargs)

    monkeypatch.delitem(
        sys.modules,
        "vllm.entrypoints.openai.streaming.session",
        raising=False,
    )
    monkeypatch.setattr(builtins, "__import__", import_without_source_patch)

    with pytest.raises(RuntimeError, match="DL Algo Streaming VLM patch"):
        VllmDlalgoSessionFactory(types.SimpleNamespace(renderer=object()))


def test_vllm_factory_requires_engine_renderer(monkeypatch):
    session_module = types.ModuleType("vllm.entrypoints.openai.streaming.session")
    session_module.StreamingSession = object
    retention_module = types.ModuleType("vllm.v1.streaming.retention")
    retention_module.StreamingRetentionParams = object
    sampling_module = types.ModuleType("vllm.sampling_params")
    sampling_module.SamplingParams = object
    sampling_module.RequestOutputKind = types.SimpleNamespace(DELTA="delta")
    monkeypatch.setitem(sys.modules, "vllm.sampling_params", sampling_module)
    monkeypatch.setitem(
        sys.modules,
        "vllm.entrypoints.openai.streaming.session",
        session_module,
    )
    monkeypatch.setitem(sys.modules, "vllm.v1.streaming.retention", retention_module)

    with pytest.raises(RuntimeError, match="renderer"):
        VllmDlalgoSessionFactory(types.SimpleNamespace())


@pytest.mark.parametrize(
    "frame",
    [
        np.zeros((6, 8, 3), dtype=np.uint8),
        np.zeros((1, 6, 8, 3), dtype=np.uint8),
        torch.zeros((3, 6, 8), dtype=torch.uint8),
        torch.zeros((1, 3, 6, 8), dtype=torch.uint8),
    ],
)
def test_native_frame_adapter_accepts_decoded_rtvi_layouts(frame):
    image = to_pil_rgb_frame(frame)

    assert isinstance(image, Image.Image)
    assert image.mode == "RGB"
    assert image.size == (8, 6)


def test_native_frame_adapter_preserves_pil_without_codec_roundtrip():
    source = Image.new("RGB", (8, 6), color=(1, 2, 3))

    assert to_pil_rgb_frame(source) is source


@pytest.mark.parametrize(
    "frame",
    [
        b"jpeg",
        np.zeros((2, 6, 8, 3), dtype=np.uint8),
        np.zeros((6, 8), dtype=np.uint8),
    ],
)
def test_native_frame_adapter_rejects_encoded_or_ambiguous_frames(frame):
    with pytest.raises((TypeError, ValueError), match="decoded|single|shape"):
        to_pil_rgb_frame(frame)
