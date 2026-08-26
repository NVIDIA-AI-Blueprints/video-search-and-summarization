# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

"""
Tests for the live-stream delete fast-path.

Task 1: latency histograms + log.
Task 2: batch delete parallelism via asyncio.gather.
Task 3: release handler lock before VLM-pipeline drain.
Task 4: bounded drain timeout with log-and-proceed fallback.
Task 5: fire-and-forget rmtree via dedicated cleanup executor.
"""

import asyncio
import queue
import threading
import time
import types
import uuid
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from api_models.live_stream import DeleteLiveStreamsRequest
from common.service_exception import ServiceException
from server.rtvi_stream_handler import RequestInfo
from server.rtvi_vlm_server import _await_file_release, _delete_live_streams_batch_impl
from vlm_pipeline.vlm_pipeline import VlmPipeline, VlmProcess


def _make_fake_live_asset(stream_handler, key: str):
    """Register a fake live-stream Asset + RequestInfo so remove_rtsp_stream finds it.

    Returns the fake Asset (MagicMock with asset_id set to ``key``).

    A minimal RequestInfo (is_live=True, status=PROCESSING, assets=[asset]) is
    inserted into ``stream_handler._request_info_map`` so that
    ``_get_live_stream_request(asset.asset_id)`` returns truthy.
    """
    asset = MagicMock()
    asset.asset_id = key
    asset.is_live = True
    asset.use_count = 0

    # Build a minimal RequestInfo that _get_live_stream_request will match.
    req_info = RequestInfo()
    req_info.is_live = True
    req_info.status = RequestInfo.Status.PROCESSING
    req_info.assets = [asset]
    stream_handler._request_info_map[req_info.request_id] = req_info

    return asset


@pytest.mark.no_gpu
def test_delete_latency_histogram_is_registered(stream_handler):
    """Metrics class must expose a _delete_latency histogram."""
    assert hasattr(stream_handler._metrics, "_delete_latency")
    assert callable(stream_handler._metrics._delete_latency.record)


def _build_mocks(num_streams=14, sleep_per_call=0.5):
    """Construct mock asset_manager + stream_handler + executor + request.

    Returns (asset_manager, stream_handler, executor, request, stream_id_uuids).
    """
    # Build 14 fake assets. Each get_asset(id) returns an Asset mock with
    # is_live=True and use_count=0 so the poll loop exits immediately.
    assets = {}
    stream_id_uuids = []
    for i in range(num_streams):
        sid = uuid.uuid4()
        stream_id_uuids.append(sid)
        asset = MagicMock()
        asset.is_live = True
        asset.use_count = 0
        asset.asset_id = str(sid)
        assets[str(sid)] = asset

    asset_manager = MagicMock()
    asset_manager.get_asset.side_effect = lambda sid: assets[sid]
    # Block for sleep_per_call seconds so serial would take num_streams * sleep_per_call.
    asset_manager.cleanup_asset.side_effect = lambda *a, **kw: time.sleep(sleep_per_call)
    asset_manager._asset_map = assets

    stream_handler = MagicMock()
    stream_handler.remove_rtsp_stream.side_effect = lambda *a, **kw: time.sleep(sleep_per_call)

    # Big enough executor to service all deletes in parallel. The real server uses
    # self._async_executor; here we provide a local pool of the same size.
    executor = ThreadPoolExecutor(max_workers=num_streams * 2)

    request = DeleteLiveStreamsRequest(stream_ids=stream_id_uuids)

    return asset_manager, stream_handler, executor, request, stream_id_uuids


@pytest.mark.no_gpu
def test_batch_delete_runs_in_parallel():
    """14 deletes must finish in ~one stream's time, not 14x."""
    num_streams = 14
    sleep_per_call = 0.5
    asset_manager, stream_handler, executor, request, stream_id_uuids = _build_mocks(
        num_streams=num_streams, sleep_per_call=sleep_per_call
    )

    try:
        t0 = time.monotonic()
        resp = asyncio.run(
            _delete_live_streams_batch_impl(asset_manager, stream_handler, executor, request)
        )
        elapsed = time.monotonic() - t0
    finally:
        executor.shutdown(wait=True)

    assert len(resp.deleted) == num_streams
    assert resp.errors == []
    # Result order must match input order.
    assert resp.deleted == stream_id_uuids

    # Each delete performs two sequential executor calls (remove_rtsp_stream then
    # cleanup_asset) of sleep_per_call each. Serial: 14 * 2 * 0.5s = 14s.
    # Parallel: ~2 * 0.5s + overhead. Allow 2s ceiling.
    assert elapsed < 2.0, f"Batch delete took {elapsed:.2f}s; expected < 2.0s"


@pytest.mark.no_gpu
def test_blocking_batch_delete_releases_idle_vlm_resources():
    asset_manager, stream_handler, executor, request, _ = _build_mocks(
        num_streams=2, sleep_per_call=0.01
    )
    request.blocking = True
    stream_handler.release_idle_vlm_resources.return_value = True

    try:
        response = asyncio.run(
            _delete_live_streams_batch_impl(asset_manager, stream_handler, executor, request)
        )
    finally:
        executor.shutdown(wait=True)

    assert response.errors == []
    stream_handler.release_idle_vlm_resources.assert_called_once_with()


@pytest.mark.no_gpu
def test_blocking_batch_delete_ignores_idle_release_failure():
    asset_manager, stream_handler, executor, request, _ = _build_mocks(
        num_streams=2, sleep_per_call=0.01
    )
    request.blocking = True
    stream_handler.release_idle_vlm_resources.side_effect = RuntimeError("release failed")

    try:
        response = asyncio.run(
            _delete_live_streams_batch_impl(asset_manager, stream_handler, executor, request)
        )
    finally:
        executor.shutdown(wait=True)

    assert response.errors == []
    assert len(response.deleted) == 2
    stream_handler.release_idle_vlm_resources.assert_called_once_with()


@pytest.mark.no_gpu
def test_batch_delete_preserves_error_shape():
    """Mixed successes and failures must yield the same error shape as the old loop."""
    # Build 4 assets: 2 happy, 1 non-live (ServiceException), 1 raises Exception.
    asset_manager, stream_handler, executor, request, stream_id_uuids = _build_mocks(
        num_streams=4, sleep_per_call=0.01
    )

    # Sabotage two of the four.
    non_live_id = str(stream_id_uuids[1])
    crash_id = str(stream_id_uuids[2])
    asset_manager._asset_map[non_live_id].is_live = False

    original_side_effect = asset_manager.get_asset.side_effect

    def flaky_get(sid):
        if sid == crash_id:
            raise RuntimeError("boom")
        return original_side_effect(sid)

    asset_manager.get_asset.side_effect = flaky_get

    try:
        resp = asyncio.run(
            _delete_live_streams_batch_impl(asset_manager, stream_handler, executor, request)
        )
    finally:
        executor.shutdown(wait=True)

    # 2 deleted, 2 errored.
    assert len(resp.deleted) == 2
    assert len(resp.errors) == 2

    # Deleted list preserves input order: stream_id_uuids[0] and [3].
    assert resp.deleted == [stream_id_uuids[0], stream_id_uuids[3]]

    errors_by_id = {e["stream_id"]: e for e in resp.errors}
    svc_err = errors_by_id[non_live_id]
    assert svc_err["error_code"] == "InvalidParameter"
    assert svc_err["status_code"] == 400

    internal_err = errors_by_id[crash_id]
    assert internal_err["error_code"] == "InternalError"
    assert internal_err["status_code"] == 500
    assert "boom" in internal_err["error"]


@pytest.mark.no_gpu
def test_batch_delete_deletes_live_stream_with_active_caption_request():
    """``use_count == 1`` is the steady state for a live stream and must delete.

    ``RTVIStreamHandler.generate_vlm_captions`` takes one asset lock per live
    caption request and holds it for the request's whole lifetime, so every
    live stream a benchmark wants to tear down has ``use_count >= 1``. A
    server-layer ``use_count > 0`` gate therefore refused *every* delete, and
    streams (with their EVS sessions) accumulated for the life of the process.

    ``remove_rtsp_stream`` is the authority here: it raises 409 only when more
    than one caption request is active and otherwise releases the
    request-held lock itself.
    """
    asset_manager, stream_handler, executor, request, stream_id_uuids = _build_mocks(
        num_streams=1, sleep_per_call=0.01
    )
    for asset in asset_manager._asset_map.values():
        asset.use_count = 1

    try:
        resp = asyncio.run(
            _delete_live_streams_batch_impl(asset_manager, stream_handler, executor, request)
        )
    finally:
        executor.shutdown(wait=True)

    assert resp.errors == []
    assert resp.deleted == stream_id_uuids
    stream_handler.remove_rtsp_stream.assert_called_once()
    asset_manager.cleanup_asset.assert_called_once()


@pytest.mark.no_gpu
def test_batch_delete_surfaces_multi_subscriber_conflict_promptly(monkeypatch):
    """Multiple caption requests must 409 immediately, not after a drain timeout.

    ``use_count`` for a live stream equals the number of active caption
    requests, so ``remove_rtsp_stream`` -- which reads the authoritative
    request map under its own lock -- is what decides the conflict. The server
    layer must not stall on a ``use_count``-based poll first; that count will
    never drop while the subscribers are live.
    """
    monkeypatch.setenv("RTVI_STREAM_DELETE_DRAIN_TIMEOUT_SEC", "30")

    asset_manager, stream_handler, executor, request, stream_id_uuids = _build_mocks(
        num_streams=1, sleep_per_call=0.01
    )
    for asset in asset_manager._asset_map.values():
        asset.use_count = 2

    def reject_multi_subscriber(asset, **kwargs):
        raise ServiceException(
            f"Cannot stop live stream {asset.asset_id} because 2 caption requests are active.",
            "ResourceInUse",
            409,
        )

    stream_handler.remove_rtsp_stream.side_effect = reject_multi_subscriber

    try:
        t0 = time.monotonic()
        resp = asyncio.run(
            _delete_live_streams_batch_impl(asset_manager, stream_handler, executor, request)
        )
        elapsed = time.monotonic() - t0
    finally:
        executor.shutdown(wait=True)

    assert resp.deleted == []
    assert len(resp.errors) == 1
    error = resp.errors[0]
    assert error["stream_id"] == str(stream_id_uuids[0])
    assert error["error_code"] == "ResourceInUse"
    assert error["status_code"] == 409
    assert "caption requests are active" in error["error"]
    assert elapsed < 1.0, f"Batch delete took {elapsed:.2f}s; expected a prompt 409"
    asset_manager.cleanup_asset.assert_not_called()


@pytest.mark.no_gpu
def test_remove_rtsp_stream_releases_lock_before_drain(stream_handler):
    """A second remove must not block while the first is draining."""
    first_drain_started = threading.Event()
    release_first_drain = threading.Event()

    asset_a = _make_fake_live_asset(stream_handler, "a")
    asset_b = _make_fake_live_asset(stream_handler, "b")

    def slow_or_fast_remove(stream_id, timeout_sec=None):
        assert timeout_sec is None
        # asset_a drains slowly; asset_b returns immediately so only asset_a
        # exercises the Phase-B drain path.
        if stream_id == asset_a.asset_id:
            first_drain_started.set()
            assert release_first_drain.wait(timeout=5), "drain not released in time"

    stream_handler._vlm_pipeline.remove_live_stream = slow_or_fast_remove

    t1 = threading.Thread(target=stream_handler.remove_rtsp_stream, args=(asset_a,))
    t1.start()
    assert first_drain_started.wait(timeout=2)

    # Second delete must be able to acquire and release the handler lock
    # (pop its map entry) even though t1 is still draining.
    second_done = threading.Event()

    def run_second():
        stream_handler.remove_rtsp_stream(asset_b)
        second_done.set()

    t2 = threading.Thread(target=run_second)
    t2.start()

    assert second_done.wait(timeout=2), "second remove blocked on first's drain"

    release_first_drain.set()
    t1.join(timeout=5)
    t2.join(timeout=5)


@pytest.mark.no_gpu
def test_remove_rtsp_stream_unlocks_asset_after_forced_teardown(stream_handler):
    """Delete owns teardown, so it must release the request-held live asset lock."""
    asset = _make_fake_live_asset(stream_handler, "stuck")
    asset.use_count = 1
    asset.unlock.side_effect = lambda: setattr(asset, "use_count", asset.use_count - 1)
    stream_handler._vlm_pipeline.remove_live_stream.return_value = 30.0
    stream_handler._safe_rmtree = MagicMock()

    stream_handler.remove_rtsp_stream(asset)

    assert asset.unlock.call_count == 1
    assert asset.use_count == 0


@pytest.mark.no_gpu
@pytest.mark.timeout(5)
def test_remove_live_stream_times_out_and_proceeds(monkeypatch, stream_handler):
    """If drain never completes, remove_live_stream returns within timeout+epsilon."""
    monkeypatch.setenv("RTVI_STREAM_DELETE_DRAIN_TIMEOUT_SEC", "1")
    pipeline = stream_handler._vlm_pipeline
    # Bind the real method to the MagicMock so we exercise the production code path.
    pipeline.remove_live_stream = types.MethodType(VlmPipeline.remove_live_stream, pipeline)
    pipeline._abort_live_stream_vlm_requests = types.MethodType(
        VlmPipeline._abort_live_stream_vlm_requests,
        pipeline,
    )
    # Provide real lists for proc collections so the method can iterate them.
    pipeline._vlm_procs = [MagicMock()]
    pipeline._asr_procs = [MagicMock()]
    pipeline._decoder_procs = [MagicMock()]

    lsinfo = MagicMock()
    lsinfo.all_chunks_processed = False
    lsinfo.gpu_id = 0
    pipeline._live_stream_id_map = {"wedged": lsinfo}

    t0 = time.monotonic()
    drain_latency = pipeline.remove_live_stream("wedged")
    elapsed = time.monotonic() - t0

    assert 1.0 <= elapsed < 1.5, f"expected ~1s timeout, got {elapsed:.2f}s"
    assert any(
        call.args[0] == "abort-live-stream-requests"
        for call in pipeline._vlm_procs[0].send_command.call_args_list
    )
    assert pipeline._vlm_procs[0].send_command.call_args_list[-1].args[0] == "stop-drop-chunks"
    # Return value must match the measured drain, so callers can record
    # per-stream latency without racing on a shared attribute.
    assert drain_latency is not None and 1.0 <= drain_latency < 1.5


@pytest.mark.no_gpu
def test_forced_delete_eos_completes_with_dropped_chunks():
    """Forced teardown must not wait for results that cancellation discarded."""
    pipeline = object.__new__(VlmPipeline)
    pipeline._processed_chunk_queue = queue.Queue()
    pipeline._processed_chunk_queue_watcher_stop_event = threading.Event()
    pipeline._live_stream_lock = threading.Lock()
    pipeline.close_evs_sessions = MagicMock()

    callback = MagicMock()
    subscriber = VlmPipeline._LiveStreamSubscriber(on_chunk_result=callback)
    stream_info = VlmPipeline._LiveStreamInfo(
        subscribers={"request-a": subscriber},
        abort_requested=True,
    )
    pipeline._live_stream_id_map = {"stream-a": stream_info}

    watcher = threading.Thread(target=pipeline._watch_processed_chunk_queue)
    watcher.start()
    try:
        pipeline._processed_chunk_queue.put(
            {
                "live_stream_ended": True,
                "live_stream_id": "stream-a",
                "total_chunks": 10,
            }
        )
        deadline = time.monotonic() + 1
        while not stream_info.all_chunks_processed and time.monotonic() < deadline:
            time.sleep(0.01)
    finally:
        pipeline._processed_chunk_queue_watcher_stop_event.set()
        watcher.join(timeout=2)

    assert stream_info.end_of_stream is True
    assert subscriber.num_chunks_processed == 0
    assert subscriber.all_chunks_processed is True
    assert stream_info.all_chunks_processed is True
    assert callback.call_count == 1
    assert callback.call_args.args[0].is_live_stream_ended is True


@pytest.mark.no_gpu
def test_forced_delete_marks_abort_before_stopping_decoder():
    """EOS handling must see forced-cancel state before decoder shutdown."""
    pipeline = object.__new__(VlmPipeline)
    pipeline._live_stream_lock = threading.Lock()
    stream_info = VlmPipeline._LiveStreamInfo(
        all_chunks_processed=True,
        gpu_id=0,
    )
    pipeline._live_stream_id_map = {"stream-a": stream_info}
    pipeline._vlm_procs = [MagicMock()]
    pipeline._asr_procs = [MagicMock()]
    pipeline._decoder_procs = [MagicMock()]
    pipeline._abort_live_stream_vlm_requests = MagicMock(return_value=0)
    pipeline.close_evs_sessions = MagicMock()

    abort_state_at_stop = []

    def record_abort_state(command, **_kwargs):
        """Capture whether forced-cancel state was set at decoder shutdown."""
        if command == "stop-live-stream":
            abort_state_at_stop.append(stream_info.abort_requested)

    pipeline._decoder_procs[0].send_command.side_effect = record_abort_state

    pipeline.remove_live_stream("stream-a", abort_inflight=True)

    assert abort_state_at_stop == [True]


@pytest.mark.no_gpu
def test_vlm_process_aborts_requests_for_live_stream():
    process = object.__new__(VlmProcess)
    process._model = MagicMock()
    process._model.abort_live_stream_requests.return_value = 3

    aborted = process._handle_command("abort-live-stream-requests", stream_id="stream-a")

    assert aborted == 3
    process._model.abort_live_stream_requests.assert_called_once_with("stream-a")


@pytest.mark.no_gpu
def test_vlm_process_releases_idle_model_resources():
    process = object.__new__(VlmProcess)
    process._model = MagicMock()
    process._model.release_idle_resources.return_value = True

    assert process._handle_command("release-idle-resources") is True
    process._model.release_idle_resources.assert_called_once_with(wait_timeout_sec=0.0)


@pytest.mark.no_gpu
def test_vlm_pipeline_requires_every_process_to_release_idle_resources():
    pipeline = object.__new__(VlmPipeline)
    successful = MagicMock()
    successful.send_command.return_value = True
    failed = MagicMock()
    failed.send_command.side_effect = RuntimeError("release failed")
    subsequent = MagicMock()
    subsequent.send_command.return_value = True
    pipeline._vlm_procs = [successful, failed, subsequent]

    assert pipeline.release_idle_vlm_resources(wait_timeout_sec=12.0) is False
    for proc in pipeline._vlm_procs:
        proc.send_command.assert_called_once_with(
            "release-idle-resources",
            wait_timeout_sec=12.0,
        )


@pytest.mark.no_gpu
def test_stream_handler_releases_resources_only_when_idle(stream_handler):
    stream_handler._vlm_pipeline.release_idle_vlm_resources.return_value = True

    assert stream_handler.release_idle_vlm_resources() is True
    stream_handler._vlm_pipeline.release_idle_vlm_resources.assert_called_once_with(
        wait_timeout_sec=30.0
    )

    stream_handler._request_info_map["active"] = MagicMock()
    stream_handler._vlm_pipeline.release_idle_vlm_resources.reset_mock()

    assert stream_handler.release_idle_vlm_resources() is False
    stream_handler._vlm_pipeline.release_idle_vlm_resources.assert_not_called()


@pytest.mark.no_gpu
def test_remove_live_stream_returns_none_for_unknown_id():
    """Unknown stream_id short-circuits with None (nothing to drain)."""
    from unittest.mock import MagicMock as _MM

    pipeline = _MM(spec=VlmPipeline)
    pipeline.remove_live_stream = types.MethodType(VlmPipeline.remove_live_stream, pipeline)
    pipeline._live_stream_id_map = {}

    assert pipeline.remove_live_stream("no-such-stream") is None


@pytest.mark.no_gpu
def test_safe_rmtree_submits_to_cleanup_executor(stream_handler, tmp_path):
    """When a cleanup executor is wired in, _safe_rmtree must hand rmtree
    off to it (fire-and-forget) rather than blocking inline."""
    submitted = []

    class _Recorder:
        def submit(self, fn, *args, **kwargs):
            submitted.append((fn, args, kwargs))
            # Do NOT invoke fn — proving the caller doesn't block on rmtree.

    stream_handler.set_cleanup_executor(_Recorder())

    doomed = tmp_path / "victim"
    doomed.mkdir()

    stream_handler._safe_rmtree(str(doomed))

    assert doomed.exists(), "rmtree must not run inline when executor is set"
    assert len(submitted) == 1


@pytest.mark.no_gpu
def test_cleanup_asset_offloads_rmtree_to_executor(monkeypatch, tmp_path):
    """AssetManager.cleanup_asset(executor=...) must submit rmtree and
    return immediately, leaving actual deletion to the executor."""
    from utils.asset_manager import AssetManager

    asset_root = tmp_path / "assets"
    asset_root.mkdir()

    mgr = AssetManager(str(asset_root))

    asset = SimpleNamespace(
        use_count=0,
        asset_dir=str(tmp_path / "doomed"),
        camera_id=None,
    )
    (tmp_path / "doomed").mkdir()
    aid = "aid-1"
    mgr._asset_map[aid] = asset

    submitted = []

    class _Recorder:
        def submit(self, fn, *args, **kwargs):
            submitted.append((fn, args, kwargs))

    mgr.cleanup_asset(aid, executor=_Recorder())

    assert (tmp_path / "doomed").exists(), "rmtree must be deferred to executor"
    assert len(submitted) == 1
    # The submitted callable, when invoked, must actually remove the dir.
    fn, args, kwargs = submitted[0]
    fn(*args, **kwargs)
    assert not (tmp_path / "doomed").exists()


@pytest.mark.no_gpu
def test_add_live_stream_rejects_duplicate_camera_id(tmp_path):
    """CV camera IDs are unique identifiers and must not be reused."""
    from common.service_exception import ServiceException
    from utils.asset_manager import AssetManager

    mgr = AssetManager(str(tmp_path / "assets"))
    first_asset_id = mgr.add_live_stream(
        "rtsp://example.com/first",
        camera_id="cam-001",
    )

    with pytest.raises(ServiceException) as exc_info:
        mgr.add_live_stream(
            "rtsp://example.com/second",
            camera_id="cam-001",
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.code == "DuplicateCameraId"
    assert mgr.get_asset_id_by_camera_id("cam-001") == first_asset_id


@pytest.mark.no_gpu
def test_add_live_stream_allows_camera_id_reuse_after_cleanup(tmp_path):
    """Removing a stream frees its CV camera ID for a later add."""
    from utils.asset_manager import AssetManager

    mgr = AssetManager(str(tmp_path / "assets"))
    first_asset_id = mgr.add_live_stream(
        "rtsp://example.com/first",
        camera_id="cam-001",
    )
    mgr.cleanup_asset(first_asset_id)

    second_asset_id = mgr.add_live_stream(
        "rtsp://example.com/second",
        camera_id="cam-001",
    )

    assert second_asset_id != first_asset_id
    assert mgr.get_asset_id_by_camera_id("cam-001") == second_asset_id


@pytest.mark.no_gpu
def test_add_live_stream_rejects_duplicate_stream_id(tmp_path):
    """Caller-provided stream IDs must not overwrite active assets."""
    from common.service_exception import ServiceException
    from utils.asset_manager import AssetManager

    mgr = AssetManager(str(tmp_path / "assets"))
    stream_id = "00000000-0000-0000-0000-000000000001"
    first_asset_id = mgr.add_live_stream(
        "rtsp://example.com/first",
        stream_id=stream_id,
    )

    with pytest.raises(ServiceException) as exc_info:
        mgr.add_live_stream(
            "rtsp://example.com/second",
            stream_id=stream_id,
        )

    assert first_asset_id == stream_id
    assert exc_info.value.status_code == 409
    assert exc_info.value.code == "DuplicateStreamId"
    assert mgr.get_asset(stream_id).path == "rtsp://example.com/first"


@pytest.mark.no_gpu
def test_add_live_stream_allows_stream_id_reuse_after_cleanup(tmp_path):
    """Removing a stream frees its caller-provided stream ID for a later add."""
    from utils.asset_manager import AssetManager

    mgr = AssetManager(str(tmp_path / "assets"))
    stream_id = "00000000-0000-0000-0000-000000000001"
    first_asset_id = mgr.add_live_stream(
        "rtsp://example.com/first",
        stream_id=stream_id,
    )
    mgr.cleanup_asset(first_asset_id)

    second_asset_id = mgr.add_live_stream(
        "rtsp://example.com/second",
        stream_id=stream_id,
    )

    assert second_asset_id == stream_id
    assert mgr.get_asset(stream_id).path == "rtsp://example.com/second"


@pytest.mark.no_gpu
def test_batch_delete_passes_cleanup_executor_through():
    """_delete_live_streams_batch_impl must forward cleanup_executor into
    asset_manager.cleanup_asset so rmtree is offloaded."""
    asset_manager, stream_handler, executor, request, _ = _build_mocks(
        num_streams=2, sleep_per_call=0.0
    )

    sentinel = object()
    try:
        asyncio.run(
            _delete_live_streams_batch_impl(
                asset_manager, stream_handler, executor, request, cleanup_executor=sentinel
            )
        )
    finally:
        executor.shutdown(wait=True)

    # Every cleanup_asset call must have received executor=sentinel.
    assert asset_manager.cleanup_asset.call_count == 2
    for call in asset_manager.cleanup_asset.call_args_list:
        assert call.kwargs.get("executor") is sentinel


@pytest.mark.no_gpu
def test_batch_delete_non_blocking_uses_drain_timeout(monkeypatch):
    """blocking=false must use the normal drain timeout when no request override is set."""
    monkeypatch.setenv("RTVI_STREAM_DELETE_DRAIN_TIMEOUT_SEC", "12.5")
    asset_manager, stream_handler, executor, _, stream_ids = _build_mocks(
        num_streams=1, sleep_per_call=0.0
    )
    request = DeleteLiveStreamsRequest(stream_ids=stream_ids, blocking=False)

    try:
        resp = asyncio.run(
            _delete_live_streams_batch_impl(asset_manager, stream_handler, executor, request)
        )
    finally:
        executor.shutdown(wait=True)

    assert resp.deleted == stream_ids
    assert resp.errors == []
    assert stream_handler.remove_rtsp_stream.call_args.kwargs["drain_timeout_sec"] == 12.5
    assert stream_handler.remove_rtsp_stream.call_args.kwargs["abort_inflight"] is False


@pytest.mark.no_gpu
def test_batch_delete_blocking_uses_blocking_timeout(monkeypatch):
    """blocking=true must pass the longer delete-batch drain timeout."""
    monkeypatch.setenv("RTVI_STREAM_DELETE_BLOCKING_TIMEOUT_SEC", "123")
    asset_manager, stream_handler, executor, _, stream_ids = _build_mocks(
        num_streams=1, sleep_per_call=0.0
    )
    request = DeleteLiveStreamsRequest(stream_ids=stream_ids, blocking=True)

    try:
        resp = asyncio.run(
            _delete_live_streams_batch_impl(asset_manager, stream_handler, executor, request)
        )
    finally:
        executor.shutdown(wait=True)

    assert resp.deleted == stream_ids
    assert resp.errors == []
    assert stream_handler.remove_rtsp_stream.call_args.kwargs["drain_timeout_sec"] == 123.0
    assert stream_handler.remove_rtsp_stream.call_args.kwargs["abort_inflight"] is True


@pytest.mark.no_gpu
def test_batch_delete_drain_timeout_overrides_blocking_env(monkeypatch):
    """Per-request drain_timeout_seconds must override the blocking env default."""
    monkeypatch.setenv("RTVI_STREAM_DELETE_BLOCKING_TIMEOUT_SEC", "123")
    asset_manager, stream_handler, executor, _, stream_ids = _build_mocks(
        num_streams=1, sleep_per_call=0.0
    )
    request = DeleteLiveStreamsRequest(
        stream_ids=stream_ids,
        blocking=True,
        drain_timeout_seconds=7.5,
    )

    try:
        resp = asyncio.run(
            _delete_live_streams_batch_impl(asset_manager, stream_handler, executor, request)
        )
    finally:
        executor.shutdown(wait=True)

    assert resp.deleted == stream_ids
    assert resp.errors == []
    assert stream_handler.remove_rtsp_stream.call_args.kwargs["drain_timeout_sec"] == 7.5
    assert stream_handler.remove_rtsp_stream.call_args.kwargs["abort_inflight"] is True


@pytest.mark.no_gpu
def test_await_file_release_returns_immediately_when_idle():
    """If use_count is already 0, the helper must not poll at all."""
    asset = SimpleNamespace(use_count=0)
    t0 = time.monotonic()
    asyncio.run(_await_file_release(asset, "file-id"))
    elapsed = time.monotonic() - t0
    assert elapsed < 0.2, f"Idle path took {elapsed:.3f}s; expected near-zero"


@pytest.mark.no_gpu
def test_await_file_release_waits_then_proceeds_when_use_count_drops(monkeypatch):
    """A transient in-flight request must not produce a 409: the helper waits
    for ``use_count`` to drop within the bounded window and proceeds without
    surfacing an error."""
    monkeypatch.setenv("RTVI_STREAM_DELETE_DRAIN_TIMEOUT_SEC", "1")
    asset = SimpleNamespace(use_count=1)

    async def _release_after_delay():
        await asyncio.sleep(0.2)
        asset.use_count = 0

    async def _scenario():
        await asyncio.gather(
            _await_file_release(asset, "file-id"),
            _release_after_delay(),
        )

    t0 = time.monotonic()
    asyncio.run(_scenario())
    elapsed = time.monotonic() - t0
    assert asset.use_count == 0
    # Helper must have waited for the release, not bailed immediately.
    # Wide tolerance so CI under load doesn't flake; assertion intent is
    # "we waited at least the release delay, and not the full timeout".
    assert 0.1 < elapsed < 0.95, f"Waited {elapsed:.3f}s; expected ~0.2s"


@pytest.mark.no_gpu
def test_await_file_release_bounds_wait_when_stuck(monkeypatch):
    """A wedged request (use_count never decreases) must not hang the delete:
    the helper logs and proceeds after the bounded deadline."""
    monkeypatch.setenv("RTVI_STREAM_DELETE_DRAIN_TIMEOUT_SEC", "0.2")
    asset = SimpleNamespace(use_count=1)

    t0 = time.monotonic()
    asyncio.run(_await_file_release(asset, "file-id"))
    elapsed = time.monotonic() - t0

    # Helper returns after the deadline; cleanup_asset will surface 409 if
    # the file is genuinely stuck. Wide tolerance for CI under load.
    assert asset.use_count == 1, "Helper must not mutate use_count"
    assert 0.1 < elapsed < 2.0, f"Bounded wait took {elapsed:.3f}s; expected ~0.2s"
