# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import pickle
import time
import types
from threading import Event, Lock, Thread
from unittest.mock import MagicMock, patch
from uuid import uuid4

import numpy as np
import pytest

from api_models.captions import (
    StreamingFramePolicy,
    StreamingVlmMetrics,
    VlmInferenceMode,
    VlmQuery,
)
from common.chunk_info import ChunkInfo
from models.base_vlm_model import BaseVlmModel, VlmGenerationConfig, VlmModelOutput
from vlm_pipeline.vlm_pipeline import (
    VlmProcess,
    VlmRequestParams,
    _validate_streaming_nim_worker_count,
)


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


def test_streaming_nim_requires_one_vlm_worker(monkeypatch):
    monkeypatch.setenv("VIA_VLM_STREAMING_NIM_ENABLED", "true")

    _validate_streaming_nim_worker_count(1)
    with pytest.raises(ValueError, match="exactly one VLM process"):
        _validate_streaming_nim_worker_count(2)


def test_streaming_lock_is_created_in_worker_not_spawn_state():
    args = types.SimpleNamespace(
        vlm_batch_size=1,
        vlm_model_type=types.SimpleNamespace(value="openai-compat"),
        model_path="",
        model_implementation_path="",
        num_gpus=1,
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


def test_base_model_streaming_capability_is_fail_closed():
    model = _UnsupportedModel()

    assert model.supports_streaming_vlm() is False
    with pytest.raises(NotImplementedError, match="Streaming VLM"):
        model.start_streaming_vlm_session("stream-a", "Describe changes")
    with pytest.raises(NotImplementedError, match="Streaming VLM"):
        model.generate_streaming_vlm_step("session", "Describe changes", [])
    assert model.end_streaming_vlm_session("stream-a", "session") is None


def test_query_defaults_to_chunked_and_maps_streaming_nim_options():
    default_query = VlmQuery(id=uuid4(), prompt="Describe.", model="model")
    streaming_query = VlmQuery(
        id=uuid4(),
        prompt="Describe.",
        model="model",
        inference_mode="streaming_vlm",
        streaming_frame_policy="ordered",
        streaming_window_frames=8,
        num_frames_per_second_or_fixed_frames_chunk=1,
        use_fps_for_chunking=False,
    )

    params = VlmRequestParams.from_vlm_query(streaming_query)
    assert default_query.inference_mode is VlmInferenceMode.CHUNKED
    assert streaming_query.inference_mode is VlmInferenceMode.STREAMING_VLM
    assert streaming_query.streaming_frame_policy is StreamingFramePolicy.ORDERED
    assert params.streaming_window_frames == 8
    assert params.streaming_fps == 1.0
    assert params.streaming_question_on_decode is False
    assert StreamingVlmMetrics.model_validate(
        {"frame_index": 0, "ttft_s": 0.1, "latency_s": 0.2}
    ).frame_index == 0


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({}, "exactly one frame"),
        ({"num_frames_per_second_or_fixed_frames_chunk": 2}, "exactly one frame"),
        (
            {
                "num_frames_per_second_or_fixed_frames_chunk": 1,
                "use_fps_for_chunking": True,
            },
            "exactly one frame",
        ),
        (
            {"num_frames_per_second_or_fixed_frames_chunk": 1, "top_k": 10},
            "does not support: top_k",
        ),
        (
            {
                "num_frames_per_second_or_fixed_frames_chunk": 1,
                "streaming_question_on_decode": True,
            },
            "first frame",
        ),
    ],
)
def test_streaming_query_rejects_unsupported_contract(overrides, message):
    with pytest.raises(ValueError, match=message):
        VlmQuery(
            id=uuid4(),
            prompt="Describe.",
            model="model",
            inference_mode="streaming_vlm",
            **overrides,
        )


def test_live_queries_on_one_stream_keep_separate_sessions():
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
    chunk = ChunkInfo(streamId="stream-a")
    frame = np.zeros((2, 2, 3), dtype=np.uint8)

    with patch("vlm_pipeline.vlm_pipeline.nvtx"):
        for request_id, prompt in (
            ("request-1", "Describe vehicles"),
            ("request-2", "Describe people"),
        ):
            process._process(
                [chunk],
                [
                    VlmRequestParams(
                        vlm_prompt=prompt,
                        vlm_generation_config=VlmGenerationConfig(),
                        inference_mode="streaming_vlm",
                    )
                ],
                frames=[[frame]],
                frame_times=[[0.0]],
                is_live_stream=[True],
                request_id=[request_id],
                decode_only=[False],
            )

    assert set(process._streaming_vlm_sessions) == {
        ("stream-a", "request-1"),
        ("stream-a", "request-2"),
    }
    assert [
        call.kwargs["query"]
        for call in process._model.start_streaming_vlm_session.call_args_list
    ] == ["Describe vehicles", "Describe people"]


def test_unsubscribe_during_session_creation_does_not_leak_session():
    creation_started = Event()
    release_creation = Event()
    session_closed = Event()
    process = object.__new__(VlmProcess)
    process._model = MagicMock()
    process._model.supports_streaming_vlm.return_value = True

    def start_session(**_kwargs):
        creation_started.set()
        assert release_creation.wait(timeout=2)
        return object()

    process._model.start_streaming_vlm_session.side_effect = start_session
    process._model.end_streaming_vlm_session.side_effect = lambda *_args: session_closed.set()
    process._refresh_model_health = MagicMock(return_value=True)
    process._num_gpus = 1
    process._streaming_vlm_sessions = {}
    process._streaming_vlm_failed_closes = {}
    process._closed_streaming_vlm_streams = {}
    process._streaming_vlm_close_threads = {}
    process._streaming_vlm_lock = Lock()
    process._streaming_vlm_close_stop = Event()
    chunk = ChunkInfo(streamId="stream-a")
    params = VlmRequestParams(
        vlm_prompt="Describe vehicles",
        vlm_generation_config=VlmGenerationConfig(),
        inference_mode="streaming_vlm",
    )
    result = {}

    def run_inference():
        result["output"] = process._process(
            [chunk],
            [params],
            frames=[[np.zeros((2, 2, 3), dtype=np.uint8)]],
            frame_times=[[0.0]],
            is_live_stream=[True],
            request_id=["request-1"],
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


def test_failed_session_close_can_be_retried():
    close_attempted = Event()
    attempts = 0
    process = object.__new__(VlmProcess)
    process._model = MagicMock()
    process._streaming_vlm_sessions = {("stream-a", "request-1"): "session-a"}
    process._streaming_vlm_failed_closes = {}
    process._closed_streaming_vlm_streams = {}
    process._streaming_vlm_close_threads = {}
    process._streaming_vlm_lock = Lock()
    process._streaming_vlm_close_stop = Event()

    def close_session(_stream_id, _session):
        nonlocal attempts
        attempts += 1
        close_attempted.set()
        if attempts <= 3:
            raise RuntimeError("remote delete failed")

    process._model.end_streaming_vlm_session.side_effect = close_session
    process._handle_command(
        "close-streaming-vlm-session", stream_id="stream-a", request_id="request-1"
    )
    assert close_attempted.wait(timeout=1)
    deadline = time.monotonic() + 2
    while attempts < 4 and time.monotonic() < deadline:
        time.sleep(0.01)
    assert attempts == 4
    assert not process._streaming_vlm_sessions
    assert not process._streaming_vlm_failed_closes
    assert not process._streaming_vlm_close_threads
