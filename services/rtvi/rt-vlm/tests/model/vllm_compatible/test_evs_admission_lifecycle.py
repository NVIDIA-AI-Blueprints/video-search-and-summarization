# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""EVS admission stays reserved until the clip future releases its frames.

The vLLM EVS handler signals ``on_encode_done`` before an event-triggered
generation finishes.  ``_generate_evs_session`` still owns the clip's CPU frame
array until that full operation returns, so freeing the admission slot at the
earlier signal permits another batch of frame arrays to accumulate in host
memory.  On unified-memory systems such as Thor that can exhaust system RAM and
hard-reset the host.
"""

import asyncio
import concurrent.futures
import threading
from types import SimpleNamespace

import pytest
import torch

import models.vllm_compatible.vllm_compatible_model as vllm_compatible_model
from models.vllm_compatible.vllm_compatible_model import VllmCompatible


class _GenerationBlockingHandler:
    def __init__(self):
        self.encoded = threading.Event()
        self.finish_generation = threading.Event()

    async def add_clip_tensors(self, session_id, images, on_encode_done=None, **kwargs):
        if on_encode_done:
            on_encode_done()
        self.encoded.set()
        await asyncio.to_thread(self.finish_generation.wait)
        return SimpleNamespace(
            tokens_used=1,
            tokens_remaining=0,
            frames_kept=len(images),
            frames_dropped=0,
            generated=True,
            response_text="done",
            response_usage={"prompt_tokens": 1, "completion_tokens": 1},
            round_timestamps=[0.0],
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
    model._max_batch_size = 1
    model._multimodal_preprocess_limiter = None
    model._use_cuda_mm_tensor_ipc = False
    model._event_loop = loop
    model._output_tpool = pool
    model._ensure_evs_session = lambda *a, **k: "sess-1"
    handler = _GenerationBlockingHandler()
    model._evs_handler = handler

    yield model, handler

    handler.finish_generation.set()
    pool.shutdown(wait=True)
    loop.call_soon_threadsafe(loop.stop)
    loop_thread.join(timeout=5)
    loop.close()


def test_encode_done_does_not_admit_another_clip_while_generation_owns_frames(evs_model):
    model, handler = evs_model
    chunk = SimpleNamespace(
        streamId="stream-1",
        start_pts=0,
        end_pts=10_000_000_000,
        is_last=False,
        chunkIdx=0,
        file="clip.mp4",
    )
    frames = torch.zeros((4, 2, 2, 3), dtype=torch.uint8)

    future = model._generate_evs_session(
        "Describe the scene.",
        frames,
        vllm_compatible_model.VlmGenerationConfig(),
        [0.0, 1.0, 2.0, 3.0],
        chunk,
    )
    assert handler.encoded.wait(timeout=10)

    assert len(model._inflight_req_ids) == 1
    assert model.can_enqueue_requests() is False

    handler.finish_generation.set()
    future.result(timeout=10)
    assert model._inflight_req_ids == []
    assert model.can_enqueue_requests() is True


def test_evs_default_caps_outstanding_clips_below_the_generic_batch_size(monkeypatch):
    """Four concurrent EVS clips leave safe unified-memory headroom on Thor."""
    monkeypatch.setenv("VIA_EVS_SESSION", "true")
    monkeypatch.delenv("VIA_EVS_MAX_INFLIGHT_CLIPS", raising=False)
    model = VllmCompatible.__new__(VllmCompatible)
    model._max_batch_size = 32
    model._multimodal_preprocess_limiter = None
    model._use_cuda_mm_tensor_ipc = False
    model._inflight_req_ids = [f"clip-{index}" for index in range(4)]

    assert model.can_enqueue_requests() is False
