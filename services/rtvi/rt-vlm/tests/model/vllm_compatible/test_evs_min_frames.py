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

"""A one-frame EVS clip is padded to the video processor's temporal factor.

Qwen3-VL's ``smart_resize`` rejects a single-frame video outright::

    ValueError: t:1 must be larger than temporal_factor:2

The regular path never reaches that check -- a one-frame chunk is routed to the
image branch (``is_single_image``), which needs no temporal dimension. EVS has
no image branch, so every clip travels as a video and a lone frame is
structurally invalid.

This is reachable from ordinary input: fps-based chunking computes
``int(fps * chunk_seconds)``, so 4 fps over a 0.167 s clip asks for 0 frames and
``video_file_frame_getter`` clamps that to 1. Duplicating the frame keeps the
request answerable; the redundant copy carries the same timestamp and is what
EVS's own similarity pruning is built to collapse.
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


class _RecordingHandler:
    def __init__(self):
        self.images = None
        self.metadata = None
        self.timestamps = None

    async def add_clip_tensors(self, session_id, images, on_encode_done=None, **kwargs):
        self.images = images
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


def _frames(count):
    return torch.zeros((count, 2, 2, 3), dtype=torch.uint8)


def _chunk(is_last=False):
    return SimpleNamespace(
        streamId="stream-1",
        start_pts=0,
        end_pts=167_000_000,
        is_last=is_last,
        chunkIdx=0,
        file="clip.mp4",
    )


def _call(model, frames, times, is_last=False):
    model._generate_evs_session(
        "Describe the scene.",
        frames,
        vllm_compatible_model.VlmGenerationConfig(),
        times,
        _chunk(is_last=is_last),
    ).result(timeout=10)


def test_single_frame_clip_is_padded_to_two_frames(evs_model):
    """One frame would raise in smart_resize; send two instead."""
    model, handler = evs_model

    _call(model, _frames(1), [4.5], is_last=True)

    assert isinstance(handler.images, np.ndarray)
    assert len(handler.images) == 2
    assert handler.metadata["total_num_frames"] == 2
    assert len(handler.metadata["frames_indices"]) == 2


def test_padding_repeats_the_frame_and_its_timestamp(evs_model):
    """The copy must be the same frame at the same time, not an invention."""
    model, handler = evs_model
    frames = _frames(1)
    frames[0, 0, 0, 0] = 42

    _call(model, frames, [4.5], is_last=True)

    assert handler.images[0].tolist() == handler.images[1].tolist()
    assert handler.images[0][0][0][0] == 42
    assert handler.timestamps == [4.5, 4.5]


def test_clip_that_already_meets_the_temporal_factor_is_untouched(evs_model):
    """Padding applies only to the degenerate case."""
    model, handler = evs_model

    _call(model, _frames(4), [0.0, 1.0, 2.0, 3.0])

    assert len(handler.images) == 4
    assert handler.timestamps == [0.0, 1.0, 2.0, 3.0]
