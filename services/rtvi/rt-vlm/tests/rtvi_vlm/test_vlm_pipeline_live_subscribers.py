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
from common.chunk_info import ChunkInfo
from common.service_exception import ServiceException
from models.base_vlm_model import VlmModelOutput
from utils.asset_manager import Asset
from vlm_pipeline.vlm_pipeline import DecoderProcess, VlmPipeline, VlmProcess


def _make_pipeline():
    pipeline = object.__new__(VlmPipeline)
    pipeline._args = SimpleNamespace(num_gpus=1)
    pipeline._decoder_procs = [MagicMock()]
    pipeline._live_stream_id_map = {}
    pipeline._live_stream_lock = Lock()
    return pipeline


def _make_asset(asset_id: str):
    return Asset(
        asset_id=asset_id,
        path="rtsp://example.com/live",
        purpose="",
        media_type="",
        asset_dir="",
    )


def _make_query(asset_id: str, prompt: str, chunk_duration: int = 10):
    return VlmQuery(
        id=uuid.UUID(asset_id),
        model="test-model",
        prompt=prompt,
        stream=True,
        chunk_duration=chunk_duration,
        num_frames_per_second_or_fixed_frames_chunk=4,
        use_fps_for_chunking=False,
        vlm_input_width=224,
        vlm_input_height=224,
    )


def _make_live_chunk_result(stream_id: str, request_id: str, chunk_idx: int):
    return {
        "chunk": ChunkInfo(
            streamId=stream_id,
            chunkIdx=chunk_idx,
            start_pts=chunk_idx * 10,
            end_pts=(chunk_idx + 1) * 10,
        ),
        "request_id": request_id,
        "vlm_output": VlmModelOutput(output="ok", input_tokens=1, output_tokens=1),
        "is_live_stream": True,
        "decode_start_time": 0,
        "decode_end_time": 0,
        "vlm_start_time": 0,
        "vlm_end_time": 0,
    }


def _wait_for(condition, timeout=2.0):
    deadline = monotonic() + timeout
    while monotonic() < deadline:
        if condition():
            return
        sleep(0.01)
    assert condition()


@pytest.mark.no_gpu
def test_add_live_stream_adds_subscriber_without_second_decoder():
    pipeline = _make_pipeline()
    asset_id = str(uuid.uuid4())
    asset = _make_asset(asset_id)

    first_callback = MagicMock()
    second_callback = MagicMock()
    pipeline.add_live_stream(
        asset,
        _make_query(asset_id, "Describe vehicles."),
        first_callback,
        request_id="request-1",
    )
    pipeline.add_live_stream(
        asset,
        _make_query(asset_id, "Describe people."),
        second_callback,
        request_id="request-2",
    )

    decoder = pipeline._decoder_procs[0]
    assert [call.args[0] for call in decoder.send_command.call_args_list] == [
        "start-live-stream",
        "add-live-stream-subscriber",
    ]
    assert decoder.send_command.call_args_list[0].kwargs["asset"] is asset
    assert decoder.send_command.call_args_list[0].kwargs["request_id"] == "request-1"
    assert decoder.send_command.call_args_list[1].kwargs["live_stream_id"] == asset_id
    assert decoder.send_command.call_args_list[1].kwargs["request_id"] == "request-2"

    live_info = pipeline._live_stream_id_map[asset_id]
    assert live_info.gpu_id == 0
    assert set(live_info.subscribers) == {"request-1", "request-2"}
    assert live_info.subscribers["request-1"].on_chunk_result is first_callback
    assert live_info.subscribers["request-2"].on_chunk_result is second_callback


@pytest.mark.no_gpu
def test_add_live_stream_records_late_subscriber_chunk_offset():
    pipeline = _make_pipeline()
    asset_id = str(uuid.uuid4())
    asset = _make_asset(asset_id)
    pipeline._decoder_procs[0].send_command.side_effect = [
        "started",
        {"added": True, "num_chunks": 3},
    ]

    pipeline.add_live_stream(
        asset,
        _make_query(asset_id, "Describe vehicles."),
        MagicMock(),
        request_id="request-1",
    )
    pipeline.add_live_stream(
        asset,
        _make_query(asset_id, "Describe people."),
        MagicMock(),
        request_id="request-2",
    )

    live_info = pipeline._live_stream_id_map[asset_id]
    assert live_info.subscribers["request-1"].start_chunk_count == 0
    assert live_info.subscribers["request-2"].start_chunk_count == 3


@pytest.mark.no_gpu
def test_remove_live_stream_subscriber_removes_only_target():
    pipeline = _make_pipeline()
    stream_id = str(uuid.uuid4())
    first_callback = MagicMock()
    second_callback = MagicMock()
    pipeline._live_stream_id_map = {
        stream_id: VlmPipeline._LiveStreamInfo(
            subscribers={
                "request-1": VlmPipeline._LiveStreamSubscriber(first_callback),
                "request-2": VlmPipeline._LiveStreamSubscriber(second_callback),
            },
            gpu_id=0,
        )
    }

    remaining = pipeline.remove_live_stream_subscriber(stream_id, "request-1")

    assert remaining is True
    assert set(pipeline._live_stream_id_map[stream_id].subscribers) == {"request-2"}
    pipeline._decoder_procs[0].send_command.assert_called_once_with(
        "remove-live-stream-subscriber",
        live_stream_id=stream_id,
        request_id="request-1",
    )


@pytest.mark.no_gpu
def test_remove_live_stream_closes_evs_sessions_before_map_pop():
    """An explicitly deleted stream must release its EVS sessions.

    The EOS-driven close only fires on a natural end of stream, and it bails
    once the stream is out of _live_stream_id_map -- so without this call a
    deleted stream leaks its sessions for the life of the process and
    eventually fails session creation with "max sessions reached".
    """
    pipeline = _make_pipeline()
    stream_id = str(uuid.uuid4())
    pipeline._vlm_procs = [MagicMock()]
    pipeline._asr_procs = []
    pipeline._live_stream_id_map = {
        stream_id: VlmPipeline._LiveStreamInfo(
            subscribers={},
            gpu_id=0,
            all_chunks_processed=True,
        )
    }

    observed_map_at_close = {}
    pipeline.close_evs_sessions = MagicMock(
        side_effect=lambda sid: observed_map_at_close.update(
            {"present": sid in pipeline._live_stream_id_map}
        )
    )

    pipeline.remove_live_stream(stream_id, timeout_sec=1.0)

    pipeline.close_evs_sessions.assert_called_once_with(stream_id)
    # Closed while the stream is still registered, and before drop-chunks is
    # lifted, so no in-flight chunk can recreate the session behind us.
    assert observed_map_at_close == {"present": True}
    assert stream_id not in pipeline._live_stream_id_map


@pytest.mark.no_gpu
def test_remove_live_stream_closes_evs_sessions_after_drain_timeout():
    """A drain that times out still has to release the sessions."""
    pipeline = _make_pipeline()
    stream_id = str(uuid.uuid4())
    pipeline._vlm_procs = [MagicMock()]
    pipeline._asr_procs = []
    pipeline._live_stream_id_map = {
        stream_id: VlmPipeline._LiveStreamInfo(
            subscribers={},
            gpu_id=0,
            all_chunks_processed=False,
        )
    }
    pipeline.close_evs_sessions = MagicMock()

    pipeline.remove_live_stream(stream_id, timeout_sec=0.0)

    pipeline.close_evs_sessions.assert_called_once_with(stream_id)
    assert stream_id not in pipeline._live_stream_id_map


@pytest.mark.no_gpu
def test_blocking_remove_aborts_inflight_vlm_requests_before_drain():
    pipeline = _make_pipeline()
    stream_id = str(uuid.uuid4())
    live_info = VlmPipeline._LiveStreamInfo(
        subscribers={},
        gpu_id=0,
        all_chunks_processed=False,
    )
    pipeline._live_stream_id_map = {stream_id: live_info}
    pipeline._asr_procs = []
    pipeline.close_evs_sessions = MagicMock()

    vlm_proc = MagicMock()

    def handle_command(command, **kwargs):
        if command == "abort-live-stream-requests" and kwargs.get("stream_id") == stream_id:
            live_info.all_chunks_processed = True
            return 2
        return None

    vlm_proc.send_command.side_effect = handle_command
    pipeline._vlm_procs = [vlm_proc]

    drain_latency = pipeline.remove_live_stream(
        stream_id,
        timeout_sec=1.0,
        abort_inflight=True,
    )

    assert drain_latency is not None and drain_latency < 0.5
    assert any(
        call.args[0] == "abort-live-stream-requests" and call.kwargs.get("stream_id") == stream_id
        for call in vlm_proc.send_command.call_args_list
    )
    assert stream_id not in pipeline._live_stream_id_map


@pytest.mark.no_gpu
def test_decoder_remove_live_stream_subscriber_keeps_stream_running_with_remaining_subscribers():
    process = object.__new__(DecoderProcess)
    stream_id = str(uuid.uuid4())
    process._live_stream_handle_info_lock = Lock()
    process._thread_pool = MagicMock()
    process._live_stream_handle_info = {
        stream_id: {
            "frame_getter": MagicMock(),
            "num_chunks": 4,
            "subscribers": {"request-1": object(), "request-2": object()},
            "stop_requested": False,
        }
    }

    result = process._handle_command(
        "remove-live-stream-subscriber",
        live_stream_id=stream_id,
        request_id="request-1",
    )

    assert result == {"removed": True, "remaining": 1}
    assert set(process._live_stream_handle_info[stream_id]["subscribers"]) == {"request-2"}
    process._thread_pool.submit.assert_not_called()


@pytest.mark.no_gpu
def test_decoder_dropped_live_handoff_emits_frame_free_completion():
    process = object.__new__(DecoderProcess)
    stream_id = str(uuid.uuid4())
    process._stop = Event()
    process._live_output_handoff_queue = Queue()
    process._live_output_handoff_pending = 1
    process._live_output_handoff_lock = Lock()
    process._dropped_live_handoff_stream_ids = {stream_id}
    process._output_queue = Queue()
    process._final_output_queue = Queue()

    process._live_output_handoff_queue.put(
        {
            "chunk": ChunkInfo(streamId=stream_id, chunkIdx=1),
            "frames": object(),
            "request_id": "request-1",
            "is_live_stream": True,
        }
    )
    worker = Thread(target=process._live_output_handoff_loop)
    worker.start()
    try:
        completion = process._final_output_queue.get(timeout=1)
    finally:
        process._stop.set()
        worker.join(timeout=2)

    assert completion["chunk"].streamId == stream_id
    assert completion["request_id"] == "request-1"
    assert completion["is_live_stream"] is True
    assert "frames" not in completion
    assert process._live_output_handoff_pending == 0


@pytest.mark.no_gpu
def test_late_live_stream_subscriber_reaches_eos_after_tail_chunks():
    pipeline = object.__new__(VlmPipeline)
    stream_id = str(uuid.uuid4())
    early_callback = MagicMock()
    late_callback = MagicMock()
    pipeline._processed_chunk_queue = Queue()
    pipeline._processed_chunk_queue_watcher_stop_event = Event()
    pipeline._live_stream_lock = Lock()
    pipeline._live_stream_id_map = {
        stream_id: VlmPipeline._LiveStreamInfo(
            subscribers={
                "request-1": VlmPipeline._LiveStreamSubscriber(
                    early_callback,
                    num_chunks_processed=5,
                ),
                "request-2": VlmPipeline._LiveStreamSubscriber(
                    late_callback,
                    start_chunk_count=3,
                ),
            },
            gpu_id=0,
        )
    }
    pipeline.close_evs_sessions = MagicMock()

    watcher = Thread(target=pipeline._watch_processed_chunk_queue)
    watcher.start()
    try:
        pipeline._processed_chunk_queue.put(_make_live_chunk_result(stream_id, "request-2", 3))
        pipeline._processed_chunk_queue.put(_make_live_chunk_result(stream_id, "request-2", 4))
        pipeline._processed_chunk_queue.put(
            {
                "live_stream_ended": True,
                "live_stream_id": stream_id,
                "total_chunks": 5,
            }
        )

        _wait_for(
            lambda: any(call.args[0].is_live_stream_ended for call in late_callback.call_args_list)
        )
        live_info = pipeline._live_stream_id_map[stream_id]
        assert live_info.subscribers["request-2"].num_chunks_processed == 2
        assert live_info.subscribers["request-2"].all_chunks_processed is True
        assert live_info.all_chunks_processed is True
    finally:
        pipeline._processed_chunk_queue_watcher_stop_event.set()
        watcher.join(timeout=2)


@pytest.mark.no_gpu
def test_add_live_stream_rejects_mismatched_decode_settings_for_same_asset():
    pipeline = _make_pipeline()
    asset_id = str(uuid.uuid4())
    asset = _make_asset(asset_id)

    pipeline.add_live_stream(
        asset,
        _make_query(asset_id, "Describe vehicles.", chunk_duration=10),
        MagicMock(),
        request_id="request-1",
    )

    with pytest.raises(ServiceException) as exc_info:
        pipeline.add_live_stream(
            asset,
            _make_query(asset_id, "Describe people.", chunk_duration=20),
            MagicMock(),
            request_id="request-2",
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.code == "BadParameters"
    assert set(pipeline._live_stream_id_map[asset_id].subscribers) == {"request-1"}
    assert [call.args[0] for call in pipeline._decoder_procs[0].send_command.call_args_list] == [
        "start-live-stream"
    ]


@pytest.mark.no_gpu
def test_vlm_process_does_not_batch_live_stream_items():
    process = object.__new__(VlmProcess)
    process._model = MagicMock()
    process._model.can_batch.return_value = True

    assert (
        process._can_batch(
            {"is_live_stream": True, "request_id": "request-1"},
            {"is_live_stream": True, "request_id": "request-2"},
        )
        is False
    )
    process._model.can_batch.assert_not_called()
