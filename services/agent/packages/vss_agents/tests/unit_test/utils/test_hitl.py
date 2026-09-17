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

"""Unit tests for request-scoped HITL helpers."""

from unittest.mock import AsyncMock

from nat.builder.context import ContextState

from vss_agents.utils.hitl import has_human_prompt_callback


def test_default_http_context_has_no_human_prompt_callback() -> None:
    assert has_human_prompt_callback() is False


def test_registered_websocket_callback_is_detected() -> None:
    token = ContextState.get().user_input_callback.set(AsyncMock())
    try:
        assert has_human_prompt_callback() is True
    finally:
        ContextState.get().user_input_callback.reset(token)
