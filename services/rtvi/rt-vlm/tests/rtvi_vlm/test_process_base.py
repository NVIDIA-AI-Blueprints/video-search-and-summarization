# SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

import concurrent.futures
import queue
from threading import Lock

from vlm_pipeline import process_base as process_base_module
from vlm_pipeline import vlm_pipeline as vlm_pipeline_module
from vlm_pipeline.process_base import ProcessBase
from vlm_pipeline.vlm_pipeline import DecoderProcess


class _RecordingQueue:
    def __init__(self):
        self.items = []

    def put(self, item):
        self.items.append(item)


class _FullQueue:
    def qsize(self):
        return 1

    def put_nowait(self, item):
        raise queue.Full


class _NoBatchProcess(ProcessBase):
    def __init__(self):
        pass

    def _supports_batching(self):
        return False


class _BatchProcess(_NoBatchProcess):
    def _supports_batching(self):
        return True


def test_live_output_handoff_drops_chunk_when_host_backlog_is_full(monkeypatch):
    proc = DecoderProcess.__new__(DecoderProcess)
    proc._output_queue = _FullQueue()
    proc._final_output_queue = _RecordingQueue()
    proc._live_output_handoff_queue = queue.Queue(maxsize=1)
    proc._live_output_handoff_pending = 1
    proc._live_output_handoff_lock = Lock()
    monkeypatch.setattr(vlm_pipeline_module, "_contains_cuda_tensor", lambda _: True)

    def fail_if_spilled(_):
        raise AssertionError("full backlog must be rejected before copying frames to host")

    monkeypatch.setattr(vlm_pipeline_module, "_spill_cuda_frames_to_cpu", fail_if_spilled)

    proc._enqueue_live_output({"frames": [object()], "chunk_id": "chunk-1"}, 1)

    assert proc._live_output_handoff_pending == 1
    assert proc._live_output_handoff_queue.empty()
    assert proc._final_output_queue.items == [
        {
            "chunk_id": "chunk-1",
            "error": "Live decoder backlog exceeded the bounded decoder-to-VLM transport",
            "error_status_code": 503,
        }
    ]


def test_handle_result_reports_frame_transfer_failure(monkeypatch):
    proc = _NoBatchProcess()
    proc._output_queue = _RecordingQueue()
    proc._final_output_queue = _RecordingQueue()
    monkeypatch.setattr(process_base_module, "_safe_cuda_empty_cache", lambda **kwargs: None)

    def fail_frame_transfer(value):
        raise RuntimeError("CUDA illegal memory access")

    monkeypatch.setattr(process_base_module, "_move_cuda_frames_to_cpu", fail_frame_transfer)

    chunk = object()
    proc._handle_result(
        {
            "chunk": chunk,
            "chunk_id": 7,
            "frames": object(),
            "error": None,
        },
        chunk=chunk,
        chunk_id=7,
    )

    assert proc._output_queue.items == []
    assert len(proc._final_output_queue.items) == 1
    error_item = proc._final_output_queue.items[0]
    assert error_item["chunk"] is chunk
    assert error_item["chunk_id"] == 7
    assert error_item["error_status_code"] == 500
    assert "CUDA illegal memory access" in error_item["error"]
    assert "frames" not in error_item


def test_handle_result_moves_error_frames_before_final_queue(monkeypatch):
    proc = _NoBatchProcess()
    proc._output_queue = _RecordingQueue()
    proc._final_output_queue = _RecordingQueue()
    monkeypatch.setattr(process_base_module, "_safe_cuda_empty_cache", lambda **kwargs: None)

    calls = []

    def record_frame_transfer(value):
        calls.append(value)
        return "cpu-frames"

    monkeypatch.setattr(process_base_module, "_move_cuda_frames_to_cpu", record_frame_transfer)

    chunk = object()
    frames = object()
    proc._handle_result(
        {
            "chunk": chunk,
            "chunk_id": 8,
            "frames": frames,
            "error": "Decode error",
        },
        chunk=chunk,
        chunk_id=8,
    )

    assert calls == [frames]
    assert proc._output_queue.items == []
    assert len(proc._final_output_queue.items) == 1
    error_item = proc._final_output_queue.items[0]
    assert error_item["chunk"] is chunk
    assert error_item["chunk_id"] == 8
    assert error_item["error"] == "Decode error"
    assert error_item["frames"] == "cpu-frames"


def test_cancelled_live_future_preserves_stream_routing(monkeypatch):
    proc = _BatchProcess()
    proc._output_queue = _RecordingQueue()
    proc._final_output_queue = _RecordingQueue()
    monkeypatch.setattr(process_base_module, "_safe_cuda_empty_cache", lambda **kwargs: None)

    cancelled_future = concurrent.futures.Future()
    cancelled_future.set_exception(concurrent.futures.CancelledError())
    chunk = object()

    proc._handle_result(
        cancelled_future,
        chunk=[chunk],
        chunk_id=["chunk-1:request-1"],
        is_live_stream=[True],
        request_id=["request-1"],
    )

    assert proc._output_queue.items == []
    assert len(proc._final_output_queue.items) == 1
    error_item = proc._final_output_queue.items[0]
    assert error_item["chunk"] is chunk
    assert error_item["is_live_stream"] is True
    assert error_item["request_id"] == "request-1"


def test_async_callback_preserves_live_stream_routing(monkeypatch):
    proc = _BatchProcess()
    proc._output_queue = _RecordingQueue()
    proc._final_output_queue = _RecordingQueue()
    monkeypatch.setattr(process_base_module, "_safe_cuda_empty_cache", lambda **kwargs: None)

    future = concurrent.futures.Future()
    proc._process = lambda **kwargs: future
    chunk = object()
    proc._ProcessBase__process_int(
        chunk=[chunk],
        chunk_id=["chunk-1:request-1"],
        is_live_stream=[True],
        request_id=["request-1"],
        frames=[object()],
    )
    future.set_exception(concurrent.futures.CancelledError())

    assert proc._output_queue.items == []
    assert len(proc._final_output_queue.items) == 1
    error_item = proc._final_output_queue.items[0]
    assert error_item["chunk"] is chunk
    assert error_item["is_live_stream"] is True
    assert error_item["request_id"] == "request-1"
