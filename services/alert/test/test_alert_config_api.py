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

"""Unit tests for the alert-config REST API: alert config REST API schemas."""

import sys
import os
import importlib.util

_schema_path = os.path.join(
    os.path.dirname(__file__), '..', 'alert-agent-web', 'app', 'api', 'alert_config_schemas.py'
)
_spec = importlib.util.spec_from_file_location("alert_config_schemas", _schema_path)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

AlertConfigRequest = _mod.AlertConfigRequest
AlertConfigUpdateRequest = _mod.AlertConfigUpdateRequest
AlertConfigResponse = _mod.AlertConfigResponse
VlmParams = _mod.VlmParams

import pytest
from pydantic import ValidationError


class TestAlertConfigRequest:

    def test_valid_request(self):
        req = AlertConfigRequest(
            alert_type="collision",
            prompt="Analyze the scene for collisions.",
            system_prompt="Answer yes or no",
            output_category="Vehicle Collision",
        )
        assert req.alert_type == "collision"
        assert req.prompt == "Analyze the scene for collisions."
        assert req.vlm_params is None

    def test_with_vlm_params(self):
        req = AlertConfigRequest(
            alert_type="collision",
            prompt="Analyze",
            vlm_params=VlmParams(
                model="nvidia/cosmos-reason2-8b",
                chunk_duration=20,
                temperature=0.6,
                max_tokens=512,
            ),
        )
        assert req.vlm_params.chunk_duration == 20
        assert req.vlm_params.temperature == 0.6

    def test_unknown_vlm_param_rejected(self):
        with pytest.raises(ValidationError):
            VlmParams(max_token=512)  # typo, missing 's'

    def test_with_enrichment_prompt(self):
        req = AlertConfigRequest(
            alert_type="collision",
            prompt="Analyze",
            enrichment_prompt="Describe in detail",
        )
        assert req.enrichment_prompt == "Describe in detail"

    def test_alert_type_normalized(self):
        req = AlertConfigRequest(alert_type="  Collision  ", prompt="test")
        assert req.alert_type == "collision"

    def test_alert_type_allows_spaces(self):
        req = AlertConfigRequest(alert_type="Stop Anomaly Module", prompt="test")
        assert req.alert_type == "stop anomaly module"

    def test_alert_type_rejects_special_chars(self):
        with pytest.raises(ValidationError):
            AlertConfigRequest(alert_type="test@invalid!", prompt="test")

    def test_empty_prompt_rejected(self):
        with pytest.raises(ValidationError):
            AlertConfigRequest(alert_type="test", prompt="")

    def test_prompt_required(self):
        with pytest.raises(ValidationError):
            AlertConfigRequest(alert_type="test")

    def test_unknown_top_level_field_rejected(self):
        with pytest.raises(ValidationError):
            AlertConfigRequest(alert_type="test", prompt="p", unknown_field="x")


class TestAlertConfigUpdateRequest:

    def test_partial_update_prompt_only(self):
        req = AlertConfigUpdateRequest(prompt="Updated prompt")
        assert req.prompt == "Updated prompt"
        assert req.vlm_params is None

    def test_partial_update_vlm_params_only(self):
        req = AlertConfigUpdateRequest(
            vlm_params=VlmParams(chunk_duration=15, max_tokens=1024)
        )
        assert req.vlm_params.chunk_duration == 15
        assert req.prompt is None

    def test_all_fields(self):
        req = AlertConfigUpdateRequest(
            prompt="new prompt",
            system_prompt="new system",
            vlm_params=VlmParams(temperature=0.3),
            output_category="New Category",
        )
        assert req.prompt == "new prompt"
        assert req.output_category == "New Category"

    def test_empty_prompt_rejected(self):
        with pytest.raises(ValidationError):
            AlertConfigUpdateRequest(prompt="   ")

    def test_unknown_top_level_field_rejected(self):
        with pytest.raises(ValidationError):
            AlertConfigUpdateRequest(prompt="p", typo_field="x")


class TestVlmParams:

    def test_all_optional(self):
        vp = VlmParams()
        assert vp.model_dump(exclude_none=True) == {}

    def test_supported_fields(self):
        vp = VlmParams(
            chunk_duration=20,
            num_frames=10,
            enable_sampling=True,
        )
        assert vp.chunk_duration == 20
        assert vp.num_frames == 10
        assert vp.enable_sampling is True

    def test_mixed_fields(self):
        vp = VlmParams(
            model="test-model",
            temperature=0.6,
            chunk_duration=15,
            max_tokens=2048,
        )
        dumped = vp.model_dump(exclude_none=True)
        assert len(dumped) == 4

    def test_extra_field_rejected(self):
        with pytest.raises(ValidationError):
            VlmParams(unknown_field="value")


class TestAlertConfigResponse:

    def test_from_dict(self):
        resp = AlertConfigResponse(
            alert_type="collision",
            prompt="Analyze collisions",
            system_prompt="Answer yes or no",
            vlm_params={"model": "cosmos", "chunk_duration": 20},
            output_category="Vehicle Collision",
            created_at="2026-04-03T14:00:00Z",
            updated_at="2026-04-03T14:00:00Z",
        )
        assert resp.alert_type == "collision"
        assert resp.vlm_params["chunk_duration"] == 20

    def test_minimal(self):
        resp = AlertConfigResponse(alert_type="test", prompt="test prompt")
        assert resp.system_prompt is None
        assert resp.vlm_params is None
        assert resp.output_category is None

    def test_unknown_top_level_field_rejected(self):
        with pytest.raises(ValidationError):
            AlertConfigResponse(alert_type="t", prompt="p", typo_field="x")
