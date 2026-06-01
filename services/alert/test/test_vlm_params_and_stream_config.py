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

"""Unit tests for per-alert-type VLM config overrides: per-alert-type VLM parameter overrides."""

import pytest

from handlers.prompt_handler.alert_type_config_loader import (
    VlmParams,
    AlertTypeConfig,
    AlertTypeConfigFile,
    Prompts,
)
from pydantic import ValidationError


class TestVlmParams:
    """Tests for the VlmParams model (per-alert-type VLM config overrides)."""

    def test_partial_fields(self):
        vp = VlmParams(max_tokens=2048, temperature=0.4, num_frames=10)
        assert vp.max_tokens == 2048
        assert vp.temperature == 0.4
        assert vp.num_frames == 10
        assert vp.base_url is None
        assert vp.model is None
        assert vp.enable_sampling is None

    def test_all_none(self):
        vp = VlmParams()
        assert vp.model_dump(exclude_none=True) == {}

    def test_all_fields(self):
        vp = VlmParams(
            base_url="http://test:8000/v1",
            model="test-model",
            max_tokens=4096,
            temperature=0.7,
            request_timeout=30,
            use_vlm_media_defaults=True,
            min_pixels=1000,
            max_pixels=500000,
            num_frames=20,
            enable_sampling=True,
            sampling_fps=8,
            cr1_optimization=False,
            max_retries=3,
        )
        dumped = vp.model_dump(exclude_none=True)
        assert len(dumped) == 13


class TestAlertTypeConfigVlmParams:
    """Tests for vlm_params field on AlertTypeConfig (per-alert-type VLM config overrides)."""

    def test_with_vlm_params(self):
        cfg = AlertTypeConfig(
            alert_type="collision",
            prompts=Prompts(user="test"),
            vlm_params=VlmParams(temperature=0.3),
        )
        assert cfg.vlm_params is not None
        assert cfg.vlm_params.temperature == 0.3

    def test_without_vlm_params(self):
        cfg = AlertTypeConfig(
            alert_type="collision",
            prompts=Prompts(user="test"),
        )
        assert cfg.vlm_params is None

    def test_config_file_mixed(self):
        data = {
            "version": "1.1",
            "alerts": [
                {
                    "alert_type": "collision",
                    "prompts": {"user": "analyze", "system": "helper"},
                    "vlm_params": {"max_tokens": 2048, "temperature": 0.4},
                },
                {
                    "alert_type": "wildlife",
                    "prompts": {"user": "detect animals"},
                },
            ],
        }
        cf = AlertTypeConfigFile.model_validate(data)
        assert len(cf.alerts) == 2
        assert cf.alerts[0].vlm_params.max_tokens == 2048
        assert cf.alerts[1].vlm_params is None

    def test_backward_compatible_no_vlm_params_in_json(self):
        data = {
            "version": "1.0",
            "alerts": [
                {"alert_type": "collision", "prompts": {"user": "test"}},
            ],
        }
        cf = AlertTypeConfigFile.model_validate(data)
        assert cf.alerts[0].vlm_params is None


class TestVlmParamsMergeLogic:
    """Tests for VLM parameter merge logic (per-alert-type VLM config overrides)."""

    def test_override_specific_fields(self):
        global_cfg = {"max_tokens": 4096, "temperature": 0.6, "num_frames": 5, "model": "cosmos"}
        overrides = VlmParams(max_tokens=2048, temperature=0.4).model_dump(exclude_none=True)
        merged = dict(global_cfg)
        merged.update(overrides)
        assert merged["max_tokens"] == 2048
        assert merged["temperature"] == 0.4
        assert merged["num_frames"] == 5
        assert merged["model"] == "cosmos"

    def test_no_override_returns_global(self):
        global_cfg = {"max_tokens": 4096, "temperature": 0.6}
        merged = dict(global_cfg)
        assert merged == global_cfg

    def test_empty_vlm_params_no_change(self):
        global_cfg = {"max_tokens": 4096, "temperature": 0.6}
        overrides = VlmParams().model_dump(exclude_none=True)
        merged = dict(global_cfg)
        merged.update(overrides)
        assert merged == global_cfg
