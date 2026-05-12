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
"""Unit tests for VLM video understanding settings loader."""

from nat.llm.nim_llm import NIMModelConfig

from vss_agents.utils.vlm_video_understanding_settings import VlmVideoUnderstandingSettings
from vss_agents.utils.vlm_video_understanding_settings import vlm_video_settings_from_llm_config


class TestVlmVideoSettingsFromLlmConfig:
    """Tests for vlm_video_settings_from_llm_config."""

    def test_defaults_when_no_video_fields(self):
        cfg = NIMModelConfig(model_name="nvidia/test", base_url="http://localhost:8000/v1")
        s = vlm_video_settings_from_llm_config(cfg)
        assert s.max_frames == 24
        assert s.max_fps == 2
        assert s.min_pixels == 1568
        assert s.max_pixels == 345600
        assert s.reasoning is False
        assert s.use_base64 is False

    def test_extra_fields_on_nim_config(self):
        cfg = NIMModelConfig(
            model_name="nvidia/test",
            base_url="http://localhost:8000/v1",
            max_frames=30,
            max_fps=3,
            min_pixels=3136,
            max_pixels=8388608,
            reasoning=True,
            use_base64=True,
        )
        s = vlm_video_settings_from_llm_config(cfg)
        assert s.max_frames == 30
        assert s.max_fps == 3
        assert s.min_pixels == 3136
        assert s.max_pixels == 8388608
        assert s.reasoning is True
        assert s.use_base64 is True

    def test_partial_override_uses_defaults_for_rest(self):
        cfg = NIMModelConfig(model_name="nvidia/test", base_url="http://localhost:8000/v1", max_frames=10)
        s = vlm_video_settings_from_llm_config(cfg)
        assert s.max_frames == 10
        assert s.max_fps == 2
        assert s.reasoning is False

    def test_model_validate_preserves_field_descriptions_model(self):
        s = VlmVideoUnderstandingSettings()
        assert s.max_frames == 24
