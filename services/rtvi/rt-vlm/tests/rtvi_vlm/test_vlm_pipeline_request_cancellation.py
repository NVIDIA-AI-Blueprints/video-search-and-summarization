# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""CPU-only regressions for request-scoped pipeline cancellation."""

import concurrent.futures
import queue
from threading import Condition, Event, Lock, Thread
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from vlm_pipeline import process_base as process_base_module
from vlm_pipeline.process_base import ProcessBase
from vlm_pipeline.vlm_pipeline import PipelineChunkResult, VlmPipeline, VlmProcess

pytestmark = pytest.mark.no_gpu


class _Process(ProcessBase):
    def __init__(self):
        self._output_queue = queue.Queue()
        self._final_output_queue = queue.Queue()
        self._cancelled_request_ids = set()


def _pipeline():
    pipeline = object.__new__(VlmPipeline)
    pipeline._enqueue_lock = Lock()
    pipeline._request_drain_condition = Condition(pipeline._enqueue_lock)
    pipeline._chunk_counter = 0
    pipeline._chunk_callback_map = {}
    pipeline._request_chunk_ids = {}
    pipeline._chunk_request_ids = {}
    pipeline._cancelled_request_ids = set()
    pipeline._decoder_procs = [MagicMock()]
    pipeline._vlm_procs = [MagicMock()]
    pipeline._asr_procs = [MagicMock()]
    return pipeline


def _start_command_thread(proc):
    proc._stop = Event()
    proc._cmd_queue = queue.Queue()
    proc._cmd_response_queue = queue.Queue()
    proc._command_lock = process_base_module.mp_ctx.Lock()
    proc._drop_chunks_stream_list = []
    thread = Thread(target=proc._cmd_handler_thread_func)
    thread.start()
    return thread


def _stop_command_thread(proc, thread):
    proc._stop.set()
    thread.join(timeout=2)
    assert not thread.is_alive()


def test_cancelled_cached_item_does_not_cancel_another_request_on_same_video():
    proc = _Process()
    proc._cancelled_request_ids.add("cancelled")
    chunk = SimpleNamespace(streamId="same-video")
    cancelled = {"chunk": chunk, "chunk_id": 1, "request_id": "cancelled", "frames": object()}
    healthy = {"chunk": chunk, "chunk_id": 2, "request_id": "healthy"}

    assert proc._filter_cancelled_items([cancelled, healthy]) == [healthy]
    receipt = proc._final_output_queue.get_nowait()
    assert receipt == {
        "chunk": chunk,
        "chunk_id": 1,
        "request_id": "cancelled",
        "cancelled": True,
    }


def test_late_worker_result_is_cancelled_before_frames_are_transferred(monkeypatch):
    proc = _Process()
    proc._cancelled_request_ids.add("request")
    monkeypatch.setattr(process_base_module, "_safe_cuda_empty_cache", lambda **_: None)
    transfer = MagicMock(side_effect=AssertionError("cancelled frames must not be transported"))
    monkeypatch.setattr(process_base_module, "_move_cuda_frames_to_cpu", transfer)
    chunk = object()

    proc._handle_result(
        {
            "chunk": chunk,
            "chunk_id": 1,
            "request_id": "request",
            "frames": object(),
            "vlm_output": "late",
        },
        chunk=chunk,
        chunk_id=1,
    )

    assert proc._output_queue.empty()
    assert proc._final_output_queue.get_nowait() == {
        "chunk": chunk,
        "chunk_id": 1,
        "request_id": "request",
        "cancelled": True,
    }
    transfer.assert_not_called()


def test_cancelled_future_produces_explicit_cancellation_receipt(monkeypatch):
    proc = _Process()
    monkeypatch.setattr(process_base_module, "_safe_cuda_empty_cache", lambda **_: None)
    future = concurrent.futures.Future()
    future.cancel()

    proc._handle_result(future, chunk=object(), chunk_id=1, request_id="request")

    assert proc._final_output_queue.get_nowait()["cancelled"] is True


def test_worker_rejects_cancelled_item_before_processing(monkeypatch):
    proc = _Process()
    proc._cancelled_request_ids.add("request")
    proc._process = MagicMock(side_effect=AssertionError("cancelled item reached processing"))
    monkeypatch.setattr(process_base_module, "_safe_cuda_empty_cache", lambda **_: None)

    proc._ProcessBase__process_int(chunk=object(), chunk_id=1, request_id="request")

    proc._process.assert_not_called()
    assert proc._final_output_queue.get_nowait()["cancelled"] is True


def test_worker_command_timeout_does_not_poison_next_response():
    proc = _Process()
    release = Event()

    def handle(command, **_):
        if command == "slow":
            assert release.wait(timeout=2)
        return command

    proc._handle_command = handle
    thread = _start_command_thread(proc)
    try:
        with pytest.raises(TimeoutError):
            proc.send_command("slow", response_timeout_sec=0.02)
        release.set()
        assert proc.send_command("healthy", response_timeout_sec=2) == "healthy"
    finally:
        release.set()
        _stop_command_thread(proc, thread)


def test_worker_abort_failure_is_reported_and_control_thread_survives():
    proc = _Process()
    proc._abort_request = MagicMock(side_effect=RuntimeError("abort failed"))
    proc._handle_command = lambda command, **_: command
    thread = _start_command_thread(proc)
    try:
        with pytest.raises(RuntimeError, match="abort failed"):
            proc.send_command(
                "abort-request", request_id="request", timeout_sec=1, response_timeout_sec=2
            )
        assert "request" in proc._cancelled_request_ids
        assert proc.send_command("healthy", response_timeout_sec=2) == "healthy"
    finally:
        _stop_command_thread(proc, thread)


@pytest.mark.parametrize("multiprocess_queue", [False, True])
def test_queued_request_cancels_while_unrelated_model_work_is_busy(multiprocess_queue, monkeypatch):
    proc = _Process()
    if multiprocess_queue:
        proc._queue = process_base_module.mp_ctx.Queue(maxsize=3)
        proc._qlock = process_base_module.mp_ctx.Lock()
        try:
            proc._queue.qsize()
        except NotImplementedError:
            # macOS lacks sem_getvalue; Linux containers report this known full size.
            monkeypatch.setattr(proc._queue, "qsize", lambda: 3)
    else:
        proc._queue = queue.Queue(maxsize=3)
        proc._qlock = Lock()
    proc._is_busy = lambda: True  # No main-loop capacity becomes available.
    healthy_before = {"chunk_id": 1, "request_id": "healthy"}
    cancelled = {"chunk_id": 2, "request_id": "cancelled", "frames": object()}
    healthy_after = {"chunk_id": 3, "request_id": "healthy"}
    for item in (healthy_before, cancelled, healthy_after):
        proc._queue.put(item)
    thread = _start_command_thread(proc)
    produced = Event()

    def produce():
        proc._put_queue_item(proc._queue, proc._qlock, {"chunk_id": 4, "request_id": "new"})
        produced.set()

    producer = Thread(target=produce)
    producer.start()
    try:
        # The producer waiting on a full queue must not hold its lock and block abort.
        proc.send_command(
            "abort-request", request_id="cancelled", timeout_sec=1, response_timeout_sec=2
        )
        assert produced.wait(timeout=2)
        assert proc._is_busy()
        assert proc._final_output_queue.get_nowait() == {
            "chunk_id": 2, "request_id": "cancelled", "cancelled": True
        }
        assert [proc._queue.get(timeout=2)["chunk_id"] for _ in range(3)] == [1, 3, 4]
    finally:
        _stop_command_thread(proc, thread)
        if producer.is_alive():
            proc._queue.get(timeout=2)
        producer.join(timeout=2)
        if multiprocess_queue:
            proc._queue.close()
            proc._queue.join_thread()
    assert not producer.is_alive()


def test_model_worker_calls_request_owned_abort_and_release():
    proc = object.__new__(VlmProcess)
    proc._disabled = False
    proc._model = MagicMock()
    proc._model.abort_request_generations.return_value = 2

    assert proc._abort_request("request", 4) == 2
    proc._release_request_tracking("request")

    proc._model.abort_request_generations.assert_called_once_with("request", timeout_sec=4)
    proc._model.release_request_tracking.assert_called_once_with("request")


def test_pipeline_cancellation_waits_until_callback_accounting_finishes():
    pipeline = _pipeline()
    entered = Event()
    release = Event()
    finished = Event()
    errors = []

    def callback(result):
        assert result.cancelled
        entered.set()
        assert release.wait(timeout=2)

    chunk_id = pipeline._reserve_chunk_callback(object(), callback, request_id="request")

    def abort():
        try:
            pipeline.abort_request("request", timeout_sec=2)
        except Exception as ex:
            errors.append(ex)
        finally:
            finished.set()

    abort_thread = Thread(target=abort)
    abort_thread.start()
    completion = Thread(
        target=pipeline._complete_chunk, args=(chunk_id, PipelineChunkResult(cancelled=True))
    )
    completion.start()
    assert entered.wait(timeout=2)
    assert not finished.wait(timeout=0.02)
    release.set()
    completion.join(timeout=2)
    abort_thread.join(timeout=2)

    assert errors == []
    assert finished.is_set()
    assert pipeline._request_chunk_ids == {}
    assert pipeline._chunk_request_ids == {}
    assert "request" in pipeline._cancelled_request_ids


def test_late_submission_is_rejected_until_tracking_is_released():
    pipeline = _pipeline()
    pipeline.abort_request("cancelled")
    results = []

    assert (
        pipeline._reserve_chunk_callback(object(), results.append, request_id="cancelled")
        is None
    )
    assert results[0].cancelled
    assert (
        pipeline._reserve_chunk_callback(object(), results.append, request_id="healthy")
        is not None
    )

    pipeline.release_request_cancellation("cancelled")
    assert "cancelled" not in pipeline._cancelled_request_ids
    assert (
        pipeline._reserve_chunk_callback(object(), results.append, request_id="cancelled")
        is not None
    )


def test_pipeline_timeout_keeps_request_ownership_visible():
    pipeline = _pipeline()
    pipeline._reserve_chunk_callback(object(), MagicMock(), request_id="request")

    with pytest.raises(TimeoutError, match="did not drain"):
        pipeline.abort_request("request", timeout_sec=0.02)
    assert pipeline._request_chunk_ids["request"]
    assert "request" in pipeline._cancelled_request_ids
    with pytest.raises(RuntimeError, match="active request"):
        pipeline.release_request_cancellation("request")


def test_failed_worker_abort_does_not_claim_pipeline_drain():
    pipeline = _pipeline()
    pipeline._vlm_procs[0].send_command.side_effect = RuntimeError("engine abort failed")

    with pytest.raises(RuntimeError, match="engine abort failed"):
        pipeline.abort_request("request")
    assert "request" in pipeline._cancelled_request_ids
