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

"""A failed EVS clip encode must surface as a chunk error, not an empty caption.

``add_clip_tensors`` raises when the vLLM-side EVS encode fails -- most commonly
``EVS encode failed: streaming cache miss`` when ``handle_evs_encode`` finds no
``ClipEVSState`` for the request. Returning the empty placeholder there made the
chunk look like a successful caption with no content, so the client saw silence
and the pipeline's error path (``PipelineChunkResult.error`` -> ERROR_BUS /
``ServiceException``) never fired.

``_run_evs_clip`` now re-raises as a ``ServiceException`` carrying the underlying
message. ``ProcessBase._handle_result`` preserves a ``ServiceException``'s
``message``/``status_code`` verbatim, so the operator sees the real cause instead
of "An unknown error occurred".
"""

import asyncio
import concurrent.futures
import threading
from types import SimpleNamespace

import pytest
import torch

import models.vllm_compatible.vllm_compatible_model as vllm_compatible_model
from common.service_exception import ServiceException
from models.vllm_compatible.vllm_compatible_model import VllmCompatible


class _FailingHandler:
    """Stands in for the EVS handler, failing the way vLLM's encode does."""

    def __init__(self, exc):
        self._exc = exc
        self.encode_done_calls = 0

    async def add_clip_tensors(self, session_id, images, on_encode_done=None, **kwargs):
        raise self._exc


@pytest.fixture
def evs_model():
    """A VllmCompatible wired with just enough state for _generate_evs_session."""
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
    model._ensure_evs_session = lambda *a, **k: "sess-1"

    yield model, pool

    pool.shutdown(wait=True)
    loop.call_soon_threadsafe(loop.stop)
    loop_thread.join(timeout=5)
    loop.close()


def _chunk():
    return SimpleNamespace(
        streamId="stream-1",
        start_pts=0,
        end_pts=10_000_000_000,
        is_last=False,
        chunkIdx=7,
        file="rtsp://host/live",
    )


def _frames(num_frames=4):
    # A CPU tensor stands in for the pipeline's CUDA frames: _generate_evs_session
    # calls .cpu().numpy() on it before the handoff.
    return torch.zeros((num_frames, 2, 2, 3), dtype=torch.uint8)


def _call(model, frames):
    return model._generate_evs_session(
        "Describe the scene.",
        frames,
        vllm_compatible_model.VlmGenerationConfig(),
        [0.0, 1.0, 2.0, 3.0],
        _chunk(),
    )


_CACHE_MISS = RuntimeError(
    "EVS encode failed: streaming cache miss. Ensure --video-pruning-rate is set."
)


def test_cache_miss_raises_instead_of_returning_an_empty_caption(evs_model):
    """The future must fail so the pipeline routes the chunk down its error path."""
    model, _pool = evs_model
    model._evs_handler = _FailingHandler(_CACHE_MISS)

    future = _call(model, _frames())

    with pytest.raises(ServiceException):
        future.result(timeout=10)


def test_error_message_names_the_cause_and_the_chunk(evs_model):
    """`_handle_result` forwards ServiceException.message verbatim to the client.

    Without the underlying text the operator would only get "An unknown error
    occurred", which does not say to check --video-pruning-rate.
    """
    model, _pool = evs_model
    model._evs_handler = _FailingHandler(_CACHE_MISS)

    future = _call(model, _frames())

    with pytest.raises(ServiceException) as excinfo:
        future.result(timeout=10)

    message = excinfo.value.message
    assert "streaming cache miss" in message
    assert "--video-pruning-rate" in message
    assert "stream-1" in message
    assert "7" in message


def test_inflight_slot_is_released_when_the_encode_fails(evs_model):
    """A leaked in-flight id would wedge _is_busy() at max_batch_size forever."""
    model, _pool = evs_model
    model._evs_handler = _FailingHandler(_CACHE_MISS)

    future = _call(model, _frames())

    with pytest.raises(ServiceException):
        future.result(timeout=10)
    assert model._inflight_req_ids == []


def test_cuda_oom_text_survives_the_wrap(evs_model):
    """`_handle_result` checks is_cuda_oom_error() on the raised exception first.

    It matches on the message, so the OOM prefix has to stay in the wrapped text
    for the chunk to keep its 503 rather than collapsing to a generic 500.
    """
    model, _pool = evs_model
    model._evs_handler = _FailingHandler(
        RuntimeError("CUDA out of memory. Tried to allocate 2.00 GiB")
    )

    future = _call(model, _frames())

    with pytest.raises(ServiceException) as excinfo:
        future.result(timeout=10)
    assert "CUDA out of memory" in str(excinfo.value)


def test_service_exception_from_the_handler_is_not_double_wrapped(evs_model):
    """A handler that already classified the failure keeps its code/status."""
    model, _pool = evs_model
    original = ServiceException("Input exceeds model limits", "InvalidParameter", 400)
    model._evs_handler = _FailingHandler(original)

    future = _call(model, _frames())

    with pytest.raises(ServiceException) as excinfo:
        future.result(timeout=10)
    assert excinfo.value is original
    assert excinfo.value.status_code == 400
