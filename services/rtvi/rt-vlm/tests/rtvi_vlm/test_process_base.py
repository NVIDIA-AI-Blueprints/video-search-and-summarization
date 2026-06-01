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

from vlm_pipeline import process_base as process_base_module
from vlm_pipeline.process_base import ProcessBase


class _RecordingQueue:
    def __init__(self):
        self.items = []

    def put(self, item):
        self.items.append(item)


class _NoBatchProcess(ProcessBase):
    def __init__(self):
        pass

    def _supports_batching(self):
        return False


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
