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

"""Every EVS clip that reaches the session handler carries usable video metadata.

Qwen3-VL reads ``frames_indices`` out of the video metadata to derive per-frame
timestamps, and ``VideoMetadata`` defaults that field to ``None``. The recovery
branch that recomputes indices from ``total_num_frames`` only runs when
``do_sample_frames`` is true, which the EVS path forces off so frames stay
paired with their own timestamps. So on this path a clip handed over without
``frames_indices`` is fatal:

    File "vllm/model_executor/models/qwen3_vl.py", line 776, in _calculate_timestamps
        indices = indices.tolist()
    AttributeError: 'NoneType' object has no attribute 'tolist'

The regular (non-EVS) path never hits this because a single-frame chunk is sent
as an *image*, which needs no video metadata at all. EVS has no image branch --
every clip goes over as a video -- so degenerate clips must still be described.
"""

import asyncio
import concurrent.futures
import threading
from types import SimpleNamespace

import numpy as np
import pytest
import torch

import models.vllm_compatible.vllm_compatible_model as vllm_compatible_model
from models.vllm_compatible.vllm_compatible_model import VllmCompatible


def _Frames(num_frames):
    """A frame tensor as the EVS session path receives it.

    A real tensor rather than a stub: the path indexes it (to cap or pad the
    clip) as well as calling ``.cpu()``/``.numpy()``, so a double that models
    only the latter hides breakage.
    """
    return torch.zeros((num_frames, 2, 2, 3), dtype=torch.uint8)


class _RecordingHandler:
    def __init__(self):
        self.metadata = None
        self.timestamps = None

    async def add_clip_tensors(self, session_id, images, on_encode_done=None, **kwargs):
        self.metadata = kwargs.get("metadata")
        self.timestamps = kwargs.get("timestamps")
        if on_encode_done:
            on_encode_done()
        return SimpleNamespace(
            tokens_used=0,
            tokens_remaining=0,
            frames_kept=len(images),
            frames_dropped=0,
            generated=False,
            response_text=None,
            response_usage=None,
            round_timestamps=[],
        )


@pytest.fixture
def evs_model():
    loop = asyncio.new_event_loop()
    loop_thread = threading.Thread(
        target=vllm_compatible_model.start_loop, args=(loop,), daemon=True
    )
    loop_thread.start()
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)

    model = VllmCompatible.__new__(VllmCompatible)
    model._vlm_model_type = "cosmos-reason2"
    model._inflight_req_ids = []
    model._event_loop = loop
    model._output_tpool = pool
    handler = _RecordingHandler()
    model._evs_handler = handler
    model._ensure_evs_session = lambda *a, **k: "sess-1"

    yield model, handler

    pool.shutdown(wait=True)
    loop.call_soon_threadsafe(loop.stop)
    loop_thread.join(timeout=5)
    loop.close()


def _chunk(is_last=False):
    return SimpleNamespace(
        streamId="stream-1",
        start_pts=0,
        end_pts=10_000_000_000,
        is_last=is_last,
        chunkIdx=0,
        file="rtsp://host/live",
    )


def _call(model, frames, video_frames_times, is_last=False):
    future = model._generate_evs_session(
        "Describe the scene.",
        frames,
        vllm_compatible_model.VlmGenerationConfig(),
        video_frames_times,
        _chunk(is_last=is_last),
    )
    future.result(timeout=10)


def test_last_single_frame_clip_carries_frame_indices(monkeypatch, evs_model):
    """A one-frame final clip still flushes, so it still needs metadata.

    Clips shorter than three frames are dropped mid-stream, but never when
    ``is_last`` is set -- the tail chunk of a file, or the flush at stream stop,
    goes through with whatever it has.
    """
    # Relative mode, pinned: an EVS session turns absolute timestamps on by
    # itself, so the default depends on VIA_EVS_SESSION in the environment.
    monkeypatch.setattr(vllm_compatible_model, "_VIDEO_METADATA_ABSOLUTE_TIMESTAMPS", False)
    monkeypatch.delenv("VIA_EVS_SESSION", raising=False)
    model, handler = evs_model

    _call(model, _Frames(1), [4.5], is_last=True)

    # Two entries, not one: the lone frame is repeated up to the video
    # processor's temporal factor before the metadata is built, so the two stay
    # consistent (see test_evs_min_frames.py). Relative mode numbers frames by
    # position, so the repeat is [0, 1] here; absolute mode derives indices from
    # the timestamps and repeats those instead.
    assert handler.metadata["frames_indices"] == [0, 1]
    assert handler.metadata["total_num_frames"] == 2
    assert handler.metadata["fps"] > 0


def test_last_single_frame_clip_carries_absolute_frame_indices(monkeypatch, evs_model):
    """Absolute-timestamp mode keeps encoding the real time for a lone frame."""
    monkeypatch.setattr(vllm_compatible_model, "_VIDEO_METADATA_ABSOLUTE_TIMESTAMPS", True)
    model, handler = evs_model

    _call(model, _Frames(1), [4.5], is_last=True)

    # Repeated frame, repeated timestamp (see test_evs_min_frames.py).
    assert handler.metadata["frames_indices"] == [4500, 4500]
    assert handler.metadata["fps"] == vllm_compatible_model._ABSOLUTE_TIMESTAMP_SOURCE_FPS


def test_clip_without_timestamps_falls_back_to_relative_indices(evs_model):
    """Missing frame times cost the real timestamps, not the whole clip."""
    model, handler = evs_model

    _call(model, _Frames(4), None)

    assert handler.metadata["frames_indices"] == [0, 1, 2, 3]
    assert handler.metadata["total_num_frames"] == 4


def test_clip_without_timestamps_sends_frame_aligned_timestamps(evs_model):
    """The clip's timestamps follow the same fallback as its metadata.

    ``clip.timestamps`` feeds the session's kept-frame timestamps and the
    ``{timestamps}`` placeholder of a configured prompt template, so they must
    describe the frames actually sent.
    """
    model, handler = evs_model

    _call(model, _Frames(4), None)

    assert handler.timestamps == [0.0, 1.0, 2.0, 3.0]


def test_mismatched_timestamps_are_not_forwarded(evs_model):
    """A wrong-length list must not reach the session alongside relative metadata."""
    model, handler = evs_model

    _call(model, _Frames(4), [10.0, 11.0, 12.0])

    assert handler.metadata["frames_indices"] == [0, 1, 2, 3]
    assert handler.timestamps == [0.0, 1.0, 2.0, 3.0]
