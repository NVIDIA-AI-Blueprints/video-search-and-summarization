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

"""Unit tests for EVS session lifecycle ownership.

These tests keep the scope small and avoid requiring a running vLLM engine:
the vendored session manager is loaded directly, while the RTVI-side
``_ensure_evs_session`` test uses a tiny protocol/handler stub.
"""

import asyncio
import importlib.util
import os
import sys
import threading
import time
from types import ModuleType, SimpleNamespace

import pytest

import models.vllm_compatible.vllm_compatible_model as vllm_compatible_model
from models.vllm_compatible.vllm_compatible_model import VllmCompatible

_VENDORED_VIDEO_SESSION = os.path.abspath(
    os.path.join(
        os.path.dirname(__file__),
        "..",
        "..",
        "..",
        "docker",
        "rtvi_vlm",
        "patches",
        "evs_vllm_files",
        "multimodal",
        "video_session.py",
    )
)


@pytest.fixture(scope="module")
def evs_video_session():
    """Load the vendored EVS video_session module under a private name."""
    spec = importlib.util.spec_from_file_location(
        "evs_video_session_vendored_under_test", _VENDORED_VIDEO_SESSION
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
        yield module
    finally:
        sys.modules.pop(spec.name, None)


def test_stale_session_expiry_returns_merged_mm_hashes(evs_video_session):
    manager = evs_video_session.VideoSessionManager(max_sessions=8, session_timeout=1.0)
    session_id = manager.create_session(model="test-model", token_budget=1024)
    session = manager.get_session(session_id)
    session.clips[0] = evs_video_session.ClipState(
        clip_id="clip-0",
        num_tokens=1,
        timestamps=[0.0],
        grid_thw=(1, 1, 1),
        kept_frames=1,
        mm_hash="clip_hash",
    )
    session.pending_clips.append(
        evs_video_session.ClipState(
            clip_id="clip-1",
            num_tokens=1,
            timestamps=[1.0],
            grid_thw=(1, 1, 1),
            kept_frames=1,
            mm_hash="pending_hash",
        )
    )
    session.record_merged_mm_hash("merged_hash")
    manager._last_activity[session_id] = 0.0

    expired_hashes = manager.expire_stale_sessions()

    assert set(expired_hashes) == {"clip_hash", "pending_hash", "merged_hash"}
    assert session_id not in manager._sessions


def test_create_session_refuses_to_exceed_max_sessions(evs_video_session):
    """The cap behind ``VIA_EVS_MAX_SESSIONS`` is what makes the API answer 503.

    ``_ensure_evs_session`` classifies this failure by looking for "max sessions"
    in the message; if the wording here drifted, the caller would silently get a
    500 with no hint instead.
    """
    manager = evs_video_session.VideoSessionManager(max_sessions=2, session_timeout=1000.0)
    for _ in range(2):
        manager.create_session(model="test-model", token_budget=1024)

    with pytest.raises(RuntimeError) as excinfo:
        manager.create_session(model="test-model", token_budget=1024)

    assert "max sessions (2) reached" in str(excinfo.value)
    assert len(manager._sessions) == 2


def test_expired_sessions_free_capacity_for_a_new_one(evs_video_session):
    """create_session expires stale sessions first, so the cap counts live ones."""
    manager = evs_video_session.VideoSessionManager(max_sessions=1, session_timeout=1000.0)
    stale_id = manager.create_session(model="test-model", token_budget=1024)
    # Derived from the timeout, not a fixed 0.0: expire_stale_sessions
    # compares against time.monotonic(), whose epoch is boot time on Linux,
    # so 0.0 only reads as stale once the host has been up longer than
    # session_timeout — passing on a long-lived box and failing on a fresh
    # CI runner.
    manager._last_activity[stale_id] = time.monotonic() - (manager.session_timeout + 1.0)

    fresh_id = manager.create_session(model="test-model", token_budget=1024)

    assert stale_id not in manager._sessions
    assert fresh_id in manager._sessions


class _CompletedFuture:
    def __init__(self, value):
        self._value = value

    def result(self):
        return self._value


class _FakeSamplingParams:
    def __init__(self, **kwargs):
        self.kwargs = dict(kwargs)
        self.temperature = kwargs.get("temperature", 1.0)
        self.top_p = kwargs.get("top_p", 1.0)
        self.top_k = kwargs.get("top_k", -1)
        self.repetition_penalty = kwargs.get("repetition_penalty", 1.0)
        self.seed = kwargs.get("seed")


class _FakeRequest:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


class _FakeEvsHandler:
    def __init__(self):
        self.created_requests = []
        self.deleted_sessions = []

    async def create_session(self, request):
        self.created_requests.append(request)
        return SimpleNamespace(session_id=f"sess-{len(self.created_requests)}")

    async def delete_session(self, session_id):
        self.deleted_sessions.append(session_id)


def _install_fake_evs_protocol(monkeypatch):
    protocol_module = ModuleType("vllm.entrypoints.openai.engine.protocol")
    protocol_module.EvsAdvancedConfig = _FakeRequest
    protocol_module.VideoSessionCreateRequest = _FakeRequest
    protocol_module.VideoSessionSamplingParams = _FakeSamplingParams

    for name in [
        "vllm",
        "vllm.entrypoints",
        "vllm.entrypoints.openai",
        "vllm.entrypoints.openai.engine",
    ]:
        monkeypatch.setitem(sys.modules, name, ModuleType(name))
    monkeypatch.setitem(
        sys.modules,
        "vllm.entrypoints.openai.engine.protocol",
        protocol_module,
    )


def _make_evs_model(handler):
    model = VllmCompatible.__new__(VllmCompatible)
    model._evs_sessions = {}
    model._evs_sessions_lock = threading.Lock()
    model._evs_handler = handler
    model._event_loop = object()
    model._vlm_model_type = "cosmos-reason2"
    model.model_dir_name = "cosmos-reason2-8b"
    model._ensure_evs_handler = lambda: handler
    return model


def test_same_stream_different_prompt_uses_separate_evs_sessions(monkeypatch):
    _install_fake_evs_protocol(monkeypatch)
    handler = _FakeEvsHandler()
    model = _make_evs_model(handler)

    monkeypatch.setattr(
        vllm_compatible_model.asyncio,
        "run_coroutine_threadsafe",
        lambda coro, _loop: _CompletedFuture(asyncio.run(coro)),
    )

    first_session = model._ensure_evs_session("stream-1", prompt="first prompt", max_tokens=1)
    reused_session = model._ensure_evs_session("stream-1", prompt="first prompt", max_tokens=1)
    second_session = model._ensure_evs_session("stream-1", prompt="second prompt", max_tokens=1)

    assert first_session == reused_session
    assert second_session != first_session
    assert [req.prompt for req in handler.created_requests] == [
        "first prompt",
        "second prompt",
    ]

    model._close_evs_session("stream-1")

    assert set(handler.deleted_sessions) == {first_session, second_session}


def test_jittered_chunk_duration_reuses_one_evs_session(monkeypatch):
    """A live stream's measured chunk span must not split the session cache.

    Live chunks are finalized with ``end_pts`` rewritten to the real last-frame
    PTS (video_file_frame_getter.py), so ``(end_pts - start_pts) / 1e9`` lands on
    a different float for every chunk. Chunk duration cannot legitimately vary
    for a given stream id -- ``add_live_stream`` rejects a subscriber whose
    decode signature differs, and file requests each get their own stream id --
    so it must not participate in the session cache key. When it did, every
    chunk minted a new session and the stream ran the engine out of sessions.
    """
    _install_fake_evs_protocol(monkeypatch)
    handler = _FakeEvsHandler()
    model = _make_evs_model(handler)

    monkeypatch.setattr(
        vllm_compatible_model.asyncio,
        "run_coroutine_threadsafe",
        lambda coro, _loop: _CompletedFuture(asyncio.run(coro)),
    )

    # Spans observed in a real 10s-chunk RTSP run: 9.53 .. 9.60, all distinct.
    session_ids = [
        model._ensure_evs_session(
            "stream-1",
            prompt="same prompt",
            max_tokens=1,
            chunk_size_s=chunk_size_s,
        )
        for chunk_size_s in (9.534221, 9.581903, 9.556742, 9.598115, 9.531007)
    ]

    assert len(set(session_ids)) == 1
    assert len(handler.created_requests) == 1
    # The first chunk's measured cadence still seeds the detector.
    assert handler.created_requests[0].event_chunk_duration_s == pytest.approx(9.534221)
