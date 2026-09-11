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

"""
Compatibility tests for the accepted shapes of VLM parameters in a request.

The current contract is a top-level ``vlm_params``. The nested
``vss_params.vlm_params`` form predates the removal of the Alert-side VSS
workflow and is retained only so older clients keep working. These tests exist
to keep that acceptance from being dropped silently, and to pin which shape
wins when more than one is present.
"""
import copy

import pytest

from schemas import EntityValidator


BASE_REQUEST = {
    "id": "compat_001",
    "@timestamp": "2024-01-01T12:00:00Z",
    "sensor_id": "camera_01",
    "video_path": "/recordings/compat_001.mp4",
    "alert": {
        "severity": "HIGH",
        "status": "ACTIVE",
        "type": "security_breach",
        "description": "Unauthorized access detected",
    },
    "event": {
        "type": "person_detected",
        "description": "Person detected in restricted area",
        "confidence": 0.95,
    },
}

CURRENT_PROMPT = "Current shape prompt."
LEGACY_PROMPT = "Legacy shape prompt."


def _validate_one(overrides):
    request = copy.deepcopy(BASE_REQUEST)
    request.update(overrides)
    entities = EntityValidator().validate_and_build([request])
    assert len(entities) == 1
    return entities[0]


@pytest.mark.parametrize(
    "overrides",
    [
        pytest.param({"vlm_params": {"prompt": CURRENT_PROMPT}}, id="snake_case"),
        pytest.param({"vlmParams": {"prompt": CURRENT_PROMPT}}, id="camel_case"),
    ],
)
def test_current_shape_is_accepted(overrides):
    """A top-level vlm_params / vlmParams supplies the VLM prompt."""
    entity = _validate_one(overrides)
    assert entity.vlm_params.prompt == CURRENT_PROMPT


@pytest.mark.parametrize(
    "outer_key",
    ["vss_params", "vssParams"],
)
def test_legacy_nested_shape_is_still_accepted(outer_key):
    """Compatibility: VLM parameters nested under the removed vss_params key."""
    entity = _validate_one({outer_key: {"vlm_params": {"prompt": LEGACY_PROMPT}}})
    assert entity.vlm_params.prompt == LEGACY_PROMPT


def test_current_shape_wins_over_legacy():
    """A client sending both gets the top-level value, not the nested one."""
    entity = _validate_one(
        {
            "vlm_params": {"prompt": CURRENT_PROMPT},
            "vss_params": {"vlm_params": {"prompt": LEGACY_PROMPT}},
        }
    )
    assert entity.vlm_params.prompt == CURRENT_PROMPT


def test_config_defaults_fill_unspecified_fields():
    """Fields the request omits come from alert_request_defaults.yaml."""
    entity = _validate_one({"vlm_params": {"prompt": CURRENT_PROMPT}})
    assert entity.vlm_params.prompt == CURRENT_PROMPT
    # max_tokens is not in the request, so it comes from the defaults file.
    assert entity.vlm_params.max_tokens is not None
