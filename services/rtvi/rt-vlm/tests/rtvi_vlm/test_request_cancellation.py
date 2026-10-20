# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import asyncio
import concurrent.futures
from threading import Event, RLock, Thread
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from server.rtvi_stream_handler import RequestInfo
from server.rtvi_vlm_server import RTVIServer
from vlm_pipeline.vlm_pipeline import PipelineChunkResult, VlmProcess


def make_process():
    proc = VlmProcess.__new__(VlmProcess)
    proc._request_cancel_lock = RLock()
    proc._cancelled_request_ids = set()
    proc._request_futures = {}
    return proc


def test_abort_cancels_only_original_generation_future():
    proc = make_process()
    orphan = concurrent.futures.Future()
    healthy = concurrent.futures.Future()
    proc._track_request_future("orphan", orphan)
    proc._track_request_future("healthy", healthy)

    proc._handle_command("abort-request", request_id="orphan")

    assert orphan.cancelled()
    assert not healthy.cancelled()
    assert "orphan" not in proc._request_futures
    assert "orphan" in proc._cancelled_request_ids
    proc._handle_command("finish-request-abort", request_id="orphan")
    assert "orphan" not in proc._cancelled_request_ids
    healthy.set_result(None)
    assert not proc._request_futures


def test_abort_before_future_registration_cancels_late_submission():
    proc = make_process()
    proc._handle_command("abort-request", request_id="orphan")
    future = concurrent.futures.Future()
    proc._track_request_future("orphan", future)
    assert future.cancelled()
    assert not proc._request_futures


def test_requests_sharing_an_asset_cannot_share_a_cancellable_batch():
    proc = make_process()
    proc._model = SimpleNamespace(can_batch=lambda a, b: True)
    first = {"request_id": "first"}
    assert not proc._can_batch(first, {"request_id": "second"})
    assert proc._can_batch(first, dict(first))


@pytest.mark.parametrize("cancel_task", [False, True])
def test_async_wait_aborts_on_disconnect_or_task_cancellation(cancel_task):
    async def run():
        server = RTVIServer.__new__(RTVIServer)
        server._stream_handler = MagicMock()
        server._stream_handler.is_request_done.return_value = False
        client = SimpleNamespace(is_disconnected=AsyncMock(return_value=not cancel_task))
        task = asyncio.create_task(server._wait_for_request_done("orphan", client))
        if cancel_task:
            await asyncio.sleep(0.01)
            task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        server._stream_handler.abort_request.assert_called_once_with("orphan")
        server._stream_handler.wait_for_request_done.assert_not_called()

    asyncio.run(run())


def test_completed_wait_does_not_abort():
    server = RTVIServer.__new__(RTVIServer)
    server._stream_handler = MagicMock()
    server._stream_handler.is_request_done.return_value = True
    client = SimpleNamespace(is_disconnected=AsyncMock(return_value=False))
    asyncio.run(server._wait_for_request_done("healthy", client))
    server._stream_handler.abort_request.assert_not_called()


def test_submission_cancellation_recovers_request_id_before_aborting():
    async def run():
        from concurrent.futures import ThreadPoolExecutor

        server = RTVIServer.__new__(RTVIServer)
        server._stream_handler = MagicMock()
        submitted = Event()
        release = Event()

        def submit():
            submitted.set()
            assert release.wait(2)
            return "orphan"

        with ThreadPoolExecutor(max_workers=1) as server._async_executor:
            task = asyncio.create_task(server._submit_vlm_request(submit))
            while not submitted.is_set():
                await asyncio.sleep(0.001)
            task.cancel()
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await task
        server._stream_handler.abort_request.assert_called_once_with("orphan")

    asyncio.run(run())


def test_abort_file_drains_before_releasing_shared_asset(stream_handler):
    handler = stream_handler
    asset = MagicMock()
    req = RequestInfo(status=RequestInfo.Status.PROCESSING, assets=[asset], chunk_count=1)
    other = RequestInfo(status=RequestInfo.Status.PROCESSING, assets=[asset])
    handler._request_info_map = {req.request_id: req, other.request_id: other}
    handler._process_output = MagicMock()

    assert handler.abort_request(req.request_id)
    assert req.cancelled and req.status == RequestInfo.Status.FAILED
    assert req.status_event.is_set()
    assert other.status == RequestInfo.Status.PROCESSING
    handler._vlm_pipeline.abort_request.assert_called_once_with(req.request_id)
    handler._process_output.assert_not_called()
    asset.unlock.assert_not_called()

    handler._on_vlm_chunk_response(PipelineChunkResult(), req)
    handler._process_output.assert_called_once_with(req, False, [])
    handler._vlm_pipeline.finish_request_abort.assert_called_once_with(req.request_id)
    handler._vlm_pipeline.close_evs_sessions.assert_not_called()
    assert not handler.abort_request(req.request_id)


def test_abort_completed_request_is_a_noop(stream_handler):
    req = RequestInfo(status=RequestInfo.Status.SUCCESSFUL)
    stream_handler._request_info_map[req.request_id] = req
    assert not stream_handler.abort_request(req.request_id)
    stream_handler._vlm_pipeline.abort_request.assert_not_called()


def test_normal_completion_and_abort_have_one_terminal_owner(stream_handler):
    handler = stream_handler
    req = RequestInfo(status=RequestInfo.Status.PROCESSING, chunk_count=1)
    handler._request_info_map[req.request_id] = req
    entered = Event()
    release = Event()
    abort_results = []

    def normal_completion(chunk, request):
        entered.set()
        assert release.wait(2)
        request.status = RequestInfo.Status.SUCCESSFUL

    handler._process_vlm_chunk_response = normal_completion
    callback = Thread(target=handler._on_vlm_chunk_response, args=(PipelineChunkResult(), req))
    callback.start()
    assert entered.wait(2)
    abort = Thread(target=lambda: abort_results.append(handler.abort_request(req.request_id)))
    abort.start()
    try:
        assert abort.is_alive()
    finally:
        release.set()
        callback.join(2)
        abort.join(2)
    assert not callback.is_alive() and not abort.is_alive()
    assert abort_results == [False]
    handler._vlm_pipeline.abort_request.assert_not_called()


def test_live_drain_does_not_hold_file_lifecycle_lock(stream_handler):
    handler = stream_handler
    asset = MagicMock()
    req = RequestInfo(status=RequestInfo.Status.PROCESSING, is_live=True, assets=[asset])
    handler._request_info_map[req.request_id] = req
    handler.remove_rtsp_stream_request = MagicMock()
    completed = Event()
    with req._lifecycle_lock:
        worker = Thread(target=lambda: (handler.abort_request(req.request_id), completed.set()))
        worker.start()
        assert completed.wait(1)
    worker.join(1)
    handler.remove_rtsp_stream_request.assert_called_once_with(asset, req.request_id)
