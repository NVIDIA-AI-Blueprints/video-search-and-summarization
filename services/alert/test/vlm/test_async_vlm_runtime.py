# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

from unittest.mock import Mock, patch

import asyncio
import pytest

from vlm.vlm_client import AsyncVLMRuntime


def test_submit_coroutine_uses_stable_loop_reference():
    runtime = AsyncVLMRuntime({})
    runtime._ensure_started = Mock()
    runtime._stopping = False

    loop = Mock()
    thread = Mock()
    thread.is_alive.return_value = True
    runtime._loop = loop
    runtime._thread = thread

    coro = asyncio.sleep(0)
    marker = object()
    with patch(
        "vlm.vlm_client.asyncio.run_coroutine_threadsafe",
        return_value=marker,
    ) as submit_mock:
        result = runtime.submit_coroutine(coro)

    assert result is marker
    submit_mock.assert_called_once_with(coro, loop)
    coro.close()


def test_submit_coroutine_closes_coroutine_when_runtime_unavailable():
    runtime = AsyncVLMRuntime({})
    runtime._ensure_started = Mock()
    runtime._stopping = False
    runtime._loop = None
    runtime._thread = None

    closed = {"value": False}

    class DummyCoroutine:
        def close(self):
            closed["value"] = True

    with pytest.raises(RuntimeError):
        runtime.submit_coroutine(DummyCoroutine())

    assert closed["value"] is True


def test_submit_coroutine_closes_coroutine_on_submit_error():
    runtime = AsyncVLMRuntime({})
    runtime._ensure_started = Mock()
    runtime._stopping = False

    loop = Mock()
    thread = Mock()
    thread.is_alive.return_value = True
    runtime._loop = loop
    runtime._thread = thread

    closed = {"value": False}

    class DummyCoroutine:
        def close(self):
            closed["value"] = True

    with patch(
        "vlm.vlm_client.asyncio.run_coroutine_threadsafe",
        side_effect=RuntimeError("loop is closed"),
    ):
        with pytest.raises(RuntimeError):
            runtime.submit_coroutine(DummyCoroutine())

    assert closed["value"] is True
