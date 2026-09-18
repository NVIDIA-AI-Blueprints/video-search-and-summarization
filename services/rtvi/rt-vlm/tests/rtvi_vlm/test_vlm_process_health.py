# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import concurrent.futures
import queue
from threading import Event, Lock
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from common.chunk_info import ChunkInfo
from common.service_exception import ServiceException
from models.vllm_compatible.vllm_compatible_model import VllmCompatible
from vlm_pipeline.vlm_pipeline import VlmPipeline, VlmProcess


def _make_process(healthy):
    process = object.__new__(VlmProcess)
    process._model = MagicMock()
    process._model.is_healthy.return_value = healthy
    process._model.can_enqueue_requests.return_value = True
    process._model_unhealthy_event = Event()
    process._next_model_health_check_at = 0.0
    return process


def _make_pipeline(healthy):
    pipeline = object.__new__(VlmPipeline)
    pipeline._enqueue_lock = Lock()
    pipeline._chunk_counter = 0
    pipeline._chunk_callback_map = {}
    process = MagicMock()
    process._disabled = False
    process.is_alive.return_value = True
    process.is_model_healthy.return_value = healthy
    pipeline._vlm_procs = [process]
    pipeline._decoder_procs = []
    pipeline._asr_procs = []
    return pipeline


@pytest.mark.no_gpu
def test_worker_rejects_after_backend_death():
    process = _make_process(False)

    assert process._is_busy() is False
    with pytest.raises(ServiceException) as exc_info:
        process._process(
            chunk=[ChunkInfo(chunk_type="text", text_input="hello")],
            request_params=[SimpleNamespace(vlm_prompt="hello")],
        )

    assert exc_info.value.status_code == 503
    process._model.generate.assert_not_called()


@pytest.mark.no_gpu
def test_failure_signal_is_sticky_across_workers():
    shared_failure = Event()
    failed = _make_process(False)
    peer = _make_process(True)
    failed._model_unhealthy_event = shared_failure
    peer._model_unhealthy_event = shared_failure

    assert failed._refresh_model_health(force=True) is False
    assert peer.is_model_healthy() is False
    assert peer._is_busy() is False


@pytest.mark.no_gpu
def test_readiness_checks_backend_not_only_wrapper_process():
    pipeline = _make_pipeline(False)

    check = pipeline.get_health_status()[0]

    assert check.healthy is False
    assert check.message == "VLM process 0 model backend is unhealthy"


@pytest.mark.no_gpu
def test_unhealthy_pipeline_rejects_before_registering_callback():
    pipeline = _make_pipeline(False)
    results = []
    chunk = ChunkInfo(chunkIdx=2)

    assert pipeline._reserve_chunk_callback(chunk, results.append) is None
    assert results[0].error_status_code == 503
    assert pipeline._chunk_callback_map == {}

    with pytest.raises(ServiceException) as exc_info:
        pipeline._reserve_chunk_callback(chunk, results.append, raise_on_unhealthy=True)
    assert exc_info.value.status_code == 503


@pytest.mark.no_gpu
def test_engine_dead_detection_is_qualified_and_cancelled_future_is_safe():
    vllm_error = type("EngineDeadError", (RuntimeError,), {})
    vllm_error.__module__ = "vllm.v1.engine.exceptions"
    custom_error = type("EngineDeadError", (RuntimeError,), {})
    assert VlmProcess._engine_dead_error_signals(vllm_error("dead"))[0] is True
    assert VlmProcess._engine_dead_error_signals(custom_error("dead"))[0] is False

    process = _make_process(True)
    process._final_output_queue = queue.Queue()
    future = concurrent.futures.Future()
    future.cancel()
    process._handle_result(
        future,
        chunk=[ChunkInfo(chunkIdx=1)],
        chunk_id=[1],
        is_live_stream=[False],
        request_id=["cancelled-request"],
    )
    routed = process._final_output_queue.get_nowait()
    assert routed["is_live_stream"] is False
    assert routed["request_id"] == "cancelled-request"

    model = object.__new__(VllmCompatible)
    model._llm = SimpleNamespace(errored=False)
    assert model.is_healthy() is True


@pytest.mark.no_gpu
@pytest.mark.parametrize("healthy_after_signal", [True, False])
def test_engine_dead_message_requires_authoritative_health_confirmation(healthy_after_signal):
    process = _make_process(healthy_after_signal)
    process._final_output_queue = queue.Queue()

    process._handle_result(
        RuntimeError("EngineCore encountered an issue while serving a request"),
        chunk=[ChunkInfo(chunkIdx=9)],
        chunk_id=[9],
        is_live_stream=[False],
        request_id=["message-only-request"],
    )

    process._model.is_healthy.assert_called_with()
    assert process.is_model_healthy() is healthy_after_signal
    assert process._final_output_queue.get_nowait()["request_id"] == "message-only-request"
