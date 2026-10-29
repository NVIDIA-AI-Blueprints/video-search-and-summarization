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

import uuid
from queue import Queue
from threading import Event, Lock, Thread
from time import monotonic, sleep
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from api_models.captions import VlmQuery
from common.service_exception import ServiceException
from utils.asset_manager import Asset
from vlm_pipeline.vlm_pipeline import VlmPipeline


def _make_pipeline():
    pipeline = object.__new__(VlmPipeline)
    pipeline._args = SimpleNamespace(num_gpus=1)
    pipeline._decoder_procs = [MagicMock()]
    pipeline._vlm_procs = [MagicMock(_disabled=False)]
    pipeline._live_stream_id_map = {}
    pipeline._live_stream_lock = Lock()
    pipeline._processed_chunk_queue = Queue()
    pipeline._processed_chunk_queue_watcher_stop_event = Event()
    return pipeline


def _make_asset(asset_id: str):
    return Asset(
        asset_id=asset_id,
        path="rtsp://example.com/live",
        purpose="",
        media_type="",
        asset_dir="",
    )


def _make_query(asset_id: str):
    return VlmQuery(
        id=uuid.UUID(asset_id),
        model="test-model",
        prompt="Describe the scene.",
        stream=True,
        chunk_duration=10,
        num_frames_per_second_or_fixed_frames_chunk=4,
        use_fps_for_chunking=False,
        vlm_input_width=224,
        vlm_input_height=224,
    )


def _wait_for(condition, timeout=2.0):
    deadline = monotonic() + timeout
    while monotonic() < deadline:
        if condition():
            return
        sleep(0.01)
    assert condition()


def _decoder_without_stream_after_eos(command, **_kwargs):
    # After natural EOS the decoder drops its handle, so subscribing is refused.
    return False if command == "add-live-stream-subscriber" else "started"


def _send_eos(pipeline, asset_id, total_chunks=0):
    pipeline._processed_chunk_queue.put(
        {"live_stream_ended": True, "live_stream_id": asset_id, "total_chunks": total_chunks}
    )


@pytest.mark.no_gpu
def test_add_live_stream_starts_fresh_decoder_after_natural_eos():
    pipeline = _make_pipeline()
    pipeline.close_evs_sessions = MagicMock()
    asset_id = str(uuid.uuid4())
    asset = _make_asset(asset_id)
    second_callback = MagicMock()

    pipeline.add_live_stream(asset, _make_query(asset_id), MagicMock(), request_id="request-1")
    stale_info = pipeline._live_stream_id_map[asset_id]

    watcher = Thread(target=pipeline._watch_processed_chunk_queue)
    watcher.start()
    try:
        _send_eos(pipeline, asset_id)
        _wait_for(lambda: stale_info.evs_sessions_closed)
    finally:
        pipeline._processed_chunk_queue_watcher_stop_event.set()
        watcher.join(timeout=2)
    pipeline.close_evs_sessions.assert_called_once_with(asset_id)

    decoder = pipeline._decoder_procs[0]
    decoder.send_command.reset_mock()
    decoder.send_command.side_effect = _decoder_without_stream_after_eos

    pipeline.add_live_stream(asset, _make_query(asset_id), second_callback, request_id="request-2")

    assert [call.args[0] for call in decoder.send_command.call_args_list] == ["start-live-stream"]
    fresh_info = pipeline._live_stream_id_map[asset_id]
    assert fresh_info is not stale_info
    assert fresh_info.end_of_stream is False
    assert set(fresh_info.subscribers) == {"request-2"}
    assert fresh_info.subscribers["request-2"].on_chunk_result is second_callback


@pytest.mark.no_gpu
def test_add_live_stream_keeps_stream_still_draining_after_eos():
    pipeline = _make_pipeline()
    pipeline.close_evs_sessions = MagicMock()
    asset_id = str(uuid.uuid4())
    asset = _make_asset(asset_id)

    pipeline.add_live_stream(asset, _make_query(asset_id), MagicMock(), request_id="request-1")
    draining_info = pipeline._live_stream_id_map[asset_id]

    watcher = Thread(target=pipeline._watch_processed_chunk_queue)
    watcher.start()
    try:
        # EOS arrives while two chunks are still in flight.
        _send_eos(pipeline, asset_id, total_chunks=2)
        _wait_for(lambda: draining_info.end_of_stream)
    finally:
        pipeline._processed_chunk_queue_watcher_stop_event.set()
        watcher.join(timeout=2)
    assert draining_info.all_chunks_processed is False

    decoder = pipeline._decoder_procs[0]
    decoder.send_command.reset_mock()
    decoder.send_command.side_effect = _decoder_without_stream_after_eos
    with pytest.raises(ServiceException) as exc_info:
        pipeline.add_live_stream(asset, _make_query(asset_id), MagicMock(), request_id="request-2")

    # The entry must survive so the tail chunks are still accounted for.
    assert exc_info.value.status_code == 409
    assert exc_info.value.code == "StreamNotActive"
    assert pipeline._live_stream_id_map[asset_id] is draining_info
    assert set(draining_info.subscribers) == {"request-1"}


@pytest.mark.no_gpu
def test_add_live_stream_waits_for_eos_evs_cleanup_before_restart():
    pipeline = _make_pipeline()
    close_started = Event()
    release_close = Event()

    def blocked_close(_stream_id):
        close_started.set()
        release_close.wait(timeout=5)

    pipeline.close_evs_sessions = MagicMock(side_effect=blocked_close)
    asset_id = str(uuid.uuid4())
    asset = _make_asset(asset_id)

    pipeline.add_live_stream(asset, _make_query(asset_id), MagicMock(), request_id="request-1")
    stale_info = pipeline._live_stream_id_map[asset_id]

    watcher = Thread(target=pipeline._watch_processed_chunk_queue)
    watcher.start()
    try:
        _send_eos(pipeline, asset_id)
        _wait_for(close_started.is_set)
        assert stale_info.all_chunks_processed
        assert not stale_info.evs_sessions_closed

        decoder = pipeline._decoder_procs[0]
        decoder.send_command.reset_mock()
        decoder.send_command.side_effect = _decoder_without_stream_after_eos
        with pytest.raises(ServiceException) as exc_info:
            pipeline.add_live_stream(
                asset, _make_query(asset_id), MagicMock(), request_id="request-2"
            )
        assert exc_info.value.status_code == 409
        decoder.send_command.assert_not_called()
        assert pipeline._live_stream_id_map[asset_id] is stale_info

        release_close.set()
        _wait_for(lambda: stale_info.evs_sessions_closed)
    finally:
        release_close.set()
        pipeline._processed_chunk_queue_watcher_stop_event.set()
        watcher.join(timeout=2)

    pipeline.add_live_stream(asset, _make_query(asset_id), MagicMock(), request_id="request-2")
    assert [call.args[0] for call in decoder.send_command.call_args_list] == ["start-live-stream"]
    assert pipeline._live_stream_id_map[asset_id] is not stale_info
    pipeline.close_evs_sessions.assert_called_once_with(asset_id)


@pytest.mark.no_gpu
def test_add_live_stream_start_already_running_is_retryable():
    pipeline = _make_pipeline()
    decoder = pipeline._decoder_procs[0]
    decoder.send_command.return_value = "already-running"
    asset_id = str(uuid.uuid4())

    with pytest.raises(ServiceException) as exc_info:
        pipeline.add_live_stream(
            _make_asset(asset_id), _make_query(asset_id), MagicMock(), request_id="request-1"
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.code == "StreamNotActive"
    assert asset_id not in pipeline._live_stream_id_map
