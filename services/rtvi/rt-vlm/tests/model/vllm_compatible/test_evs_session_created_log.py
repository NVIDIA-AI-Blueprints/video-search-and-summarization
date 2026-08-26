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

"""The session-created log states the EVS similarity threshold in effect.

The threshold reaches the engine (``evs_similarity_threshold``) and the serving
handler without ever being logged, and it silently defaults to 0.4 when unset.
It is also read through the ``RTVI_VLLM_*`` alias, because
``_sanitize_rtvi_vllm_env()`` moves the variable out of the ``VLLM_`` namespace
before vLLM is imported -- so a misscoped or typo'd variable degrades to the
default with no trace. Stating it alongside the other session parameters makes
the effective value visible per session.
"""

import asyncio
import logging
import threading

import pytest

import models.vllm_compatible.vllm_compatible_model as vllm_compatible_model
from models.vllm_compatible.vllm_compatible_model import VllmCompatible

_ENV_VARS = ("RTVI_VLLM_EVS_SIMILARITY_THRESHOLD", "VLLM_EVS_SIMILARITY_THRESHOLD")


class _RecordingHandler:
    async def create_session(self, request):
        return type("Resp", (), {"session_id": "sess-log-1"})()


@pytest.fixture
def evs_model():
    loop = asyncio.new_event_loop()
    loop_thread = threading.Thread(
        target=vllm_compatible_model.start_loop, args=(loop,), daemon=True
    )
    loop_thread.start()

    model = VllmCompatible.__new__(VllmCompatible)
    model._vlm_model_type = "cosmos-reason2"
    model._event_loop = loop
    model._evs_sessions = {}
    model._evs_sessions_lock = threading.Lock()
    handler = _RecordingHandler()
    model._ensure_evs_handler = lambda: handler
    model._evs_handler = handler

    yield model

    loop.call_soon_threadsafe(loop.stop)
    loop_thread.join(timeout=5)
    loop.close()


def _create(model):
    return model._ensure_evs_session(
        "stream-1",
        prompt="Describe the scene.",
        max_tokens=64,
        chunk_size_s=10.0,
        timestamp_prompt_template=None,
        generation_config=vllm_compatible_model.VlmGenerationConfig(),
        system_prompt=None,
    )


def _created_line(caplog):
    return next(r.getMessage() for r in caplog.records if "EVS session created" in r.getMessage())


def test_logs_the_default_similarity_threshold(caplog, monkeypatch, evs_model):
    """An unset threshold defaults to 0.4 -- say so rather than staying silent."""
    for name in _ENV_VARS:
        monkeypatch.delenv(name, raising=False)

    with caplog.at_level(logging.INFO):
        _create(evs_model)

    assert "similarity_threshold=0.40" in _created_line(caplog)


def test_logs_a_configured_similarity_threshold(caplog, monkeypatch, evs_model):
    """The value is read through the RTVI alias, so that is what must be shown."""
    monkeypatch.delenv("VLLM_EVS_SIMILARITY_THRESHOLD", raising=False)
    monkeypatch.setenv("RTVI_VLLM_EVS_SIMILARITY_THRESHOLD", "0.25")

    with caplog.at_level(logging.INFO):
        _create(evs_model)

    assert "similarity_threshold=0.25" in _created_line(caplog)
