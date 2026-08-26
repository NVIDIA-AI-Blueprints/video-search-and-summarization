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

"""A failed EVS session creation reaches the API with its real reason.

``ProcessBase.__process_int`` catches everything and ``_handle_result`` can only
forward a message and status code when the error is a ``ServiceException`` --
anything else becomes "An unknown error occurred". Session creation raises bare
``RuntimeError``s for operator-fixable conditions, the most common being::

    RuntimeError: Cannot create session: max sessions (256) reached

which is fixed by raising ``VIA_EVS_MAX_SESSIONS``. That text has to survive to
the caller, the way clip-encode failures already do via
``EVSClipProcessingError``.
"""

import asyncio
import threading

import pytest

import models.vllm_compatible.vllm_compatible_model as vllm_compatible_model
from common.service_exception import ServiceException
from models.vllm_compatible.vllm_compatible_model import VllmCompatible

_MAX_SESSIONS_ERROR = "Cannot create session: max sessions (256) reached"


class _FailingHandler:
    def __init__(self, exc):
        self._exc = exc

    async def create_session(self, request):
        raise self._exc


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

    def _build(exc):
        handler = _FailingHandler(exc)
        model._ensure_evs_handler = lambda: handler
        model._evs_handler = handler
        return model

    yield _build

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


def test_session_creation_failure_becomes_a_service_exception(evs_model):
    """Bare RuntimeError would reach the caller as "an unknown error"."""
    model = evs_model(RuntimeError(_MAX_SESSIONS_ERROR))

    with pytest.raises(ServiceException) as excinfo:
        _create(model)

    assert _MAX_SESSIONS_ERROR in str(excinfo.value)
    assert excinfo.value.code == "EVSSessionCreateError"


def test_capacity_failure_is_reported_as_service_unavailable(evs_model):
    """Hitting the session cap is transient/operator-fixable, not a 500."""
    model = evs_model(RuntimeError(_MAX_SESSIONS_ERROR))

    with pytest.raises(ServiceException) as excinfo:
        _create(model)

    assert excinfo.value.status_code == 503


def test_capacity_failure_names_the_env_var_to_raise(evs_model):
    """The fix is an operator knob, so the message has to name it."""
    model = evs_model(RuntimeError(_MAX_SESSIONS_ERROR))

    with pytest.raises(ServiceException) as excinfo:
        _create(model)

    assert "VIA_EVS_MAX_SESSIONS" in str(excinfo.value)


def test_other_failures_stay_internal_errors(evs_model):
    """Only the capacity case is special-cased; the rest are 500s."""
    model = evs_model(RuntimeError("engine died during session create"))

    with pytest.raises(ServiceException) as excinfo:
        _create(model)

    assert excinfo.value.status_code == 500
    assert "engine died during session create" in str(excinfo.value)
    assert "VIA_EVS_MAX_SESSIONS" not in str(excinfo.value)


def test_an_already_classified_error_is_not_rewrapped(evs_model):
    """Re-wrapping would flatten a 400 into a 500, as on the clip path."""
    original = ServiceException("bad prompt", "InvalidParameters", 400)
    model = evs_model(original)

    with pytest.raises(ServiceException) as excinfo:
        _create(model)

    assert excinfo.value is original
    assert excinfo.value.status_code == 400
