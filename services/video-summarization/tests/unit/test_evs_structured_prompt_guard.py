# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

"""Unit tests for EVS structured-output prompt guard in _create_vlm_prompt."""

import os
from unittest.mock import patch

import pytest


def _make_handler():
    from via_stream_handler import ViaStreamHandler

    return ViaStreamHandler.__new__(ViaStreamHandler)


def _clear_prompt_overrides():
    for key in (
        "LVS_PROMPT_VLM_ROLE",
        "LVS_PROMPT_VLM_INSTRUCTION",
        "LVS_PROMPT_VLM_CONSTRAINTS",
        "LVS_PROMPT_VLM_STRUCTURED_OUTPUT",
    ):
        os.environ.pop(key, None)


@pytest.mark.unit
class TestEvsStructuredPromptGuard:
    def test_evs_session_appends_schema_guard(self):
        handler = _make_handler()
        with patch.dict(
            os.environ,
            {"VIA_EVS_SESSION": "true", "VLM_VIDEO_PRUNING_RATE": ""},
            clear=False,
        ):
            _clear_prompt_overrides()
            result = handler._create_vlm_prompt(
                "ignored", True, [], ["fire", "normal activity"], "warehouse"
            )
            assert "bbox_2d" in result
            assert "EVS/output-format constraints" in result
            assert "normal activity" in result

    def test_evs_pruning_rate_appends_schema_guard(self):
        handler = _make_handler()
        with patch.dict(
            os.environ,
            {"VIA_EVS_SESSION": "false", "VLM_VIDEO_PRUNING_RATE": "0.5"},
            clear=False,
        ):
            _clear_prompt_overrides()
            result = handler._create_vlm_prompt("ignored", True, [], ["theft"], "warehouse")
            assert "EVS/output-format constraints" in result
            assert "Do NOT emit grounding/detection schemas" in result

    def test_evs_guard_not_added_when_evs_disabled(self):
        handler = _make_handler()
        with patch.dict(
            os.environ,
            {"VIA_EVS_SESSION": "false", "VLM_VIDEO_PRUNING_RATE": "0"},
            clear=False,
        ):
            _clear_prompt_overrides()
            result = handler._create_vlm_prompt("ignored", True, [], ["theft"], "warehouse")
            assert "EVS/output-format constraints" not in result

    def test_evs_guard_appended_to_existing_constraints(self):
        handler = _make_handler()
        with patch.dict(
            os.environ,
            {
                "VIA_EVS_SESSION": "true",
                "LVS_PROMPT_VLM_CONSTRAINTS": "Keep answers concise.",
            },
            clear=False,
        ):
            result = handler._create_vlm_prompt("ignored", True, [], ["fire"], "warehouse")
            assert "Keep answers concise." in result
            assert "EVS/output-format constraints" in result

    def test_is_evs_enabled_helpers(self):
        handler = _make_handler()
        with patch.dict(os.environ, {"VIA_EVS_SESSION": "true"}, clear=False):
            assert handler._is_evs_enabled() is True
        with patch.dict(
            os.environ,
            {"VIA_EVS_SESSION": "false", "VLM_VIDEO_PRUNING_RATE": "0.9"},
            clear=False,
        ):
            assert handler._is_evs_enabled() is True
        with patch.dict(
            os.environ,
            {"VIA_EVS_SESSION": "false", "VLM_VIDEO_PRUNING_RATE": "0"},
            clear=False,
        ):
            assert handler._is_evs_enabled() is False
