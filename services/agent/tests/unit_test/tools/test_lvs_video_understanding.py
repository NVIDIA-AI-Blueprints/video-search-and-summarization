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
"""Unit tests for lvs_video_understanding module."""

from unittest.mock import AsyncMock
from unittest.mock import MagicMock
from unittest.mock import patch

from nat.builder.context import ContextState
from pydantic import ValidationError
import pytest

from vss_agents.tools.lvs_video_understanding import LVSVideoUnderstandingConfig
from vss_agents.tools.lvs_video_understanding import LVSVideoUnderstandingInput
from vss_agents.tools.lvs_video_understanding import lvs_video_understanding


class TestLVSVideoUnderstandingConfig:
    """Test LVSVideoUnderstandingConfig model."""

    def test_with_required_fields(self):
        config = LVSVideoUnderstandingConfig(
            lvs_backend_url="http://localhost:38111",
            hitl_scenario_template="Scenario: {scenario}",
            hitl_events_template="Events: {events}",
            hitl_objects_template="Objects: {objects}",
        )
        assert config.lvs_backend_url == "http://localhost:38111"
        assert config.hitl_scenario_template == "Scenario: {scenario}"
        assert config.hitl_events_template == "Events: {events}"
        assert config.hitl_objects_template == "Objects: {objects}"
        # Check defaults
        assert config.conn_timeout_ms == 5000
        assert config.read_timeout_ms == 600000
        assert config.model == "gpt-4o"
        assert config.video_url_tool == "vst_video_url"

    def test_url_translation_fields_are_not_exposed(self):
        assert "vlm_mode" not in LVSVideoUnderstandingConfig.model_fields
        assert "internal_ip" not in LVSVideoUnderstandingConfig.model_fields
        assert "external_ip" not in LVSVideoUnderstandingConfig.model_fields

    def test_custom_timeouts(self):
        config = LVSVideoUnderstandingConfig(
            lvs_backend_url="http://localhost:38111",
            hitl_scenario_template="Scenario template",
            hitl_events_template="Events template",
            hitl_objects_template="Objects template",
            conn_timeout_ms=10000,
            read_timeout_ms=1200000,
        )
        assert config.conn_timeout_ms == 10000
        assert config.read_timeout_ms == 1200000

    def test_custom_model(self):
        config = LVSVideoUnderstandingConfig(
            lvs_backend_url="http://localhost:38111",
            hitl_scenario_template="Scenario template",
            hitl_events_template="Events template",
            hitl_objects_template="Objects template",
            model="custom-model",
        )
        assert config.model == "custom-model"

    def test_custom_video_url_tool(self):
        config = LVSVideoUnderstandingConfig(
            lvs_backend_url="http://localhost:38111",
            hitl_scenario_template="Scenario template",
            hitl_events_template="Events template",
            hitl_objects_template="Objects template",
            video_url_tool="custom_video_tool",
        )
        assert config.video_url_tool == "custom_video_tool"

    def test_missing_lvs_backend_url_fails(self):
        with pytest.raises(ValidationError):
            LVSVideoUnderstandingConfig(
                hitl_scenario_template="Scenario template",
                hitl_events_template="Events template",
                hitl_objects_template="Objects template",
            )

    def test_missing_hitl_template_fails(self):
        with pytest.raises(ValidationError):
            LVSVideoUnderstandingConfig(
                lvs_backend_url="http://localhost:38111",
                hitl_events_template="Events template",
                hitl_objects_template="Objects template",
            )

    def test_enable_audio_defaults_false_and_accepts_true(self):
        base_kwargs = {
            "lvs_backend_url": "http://localhost:38111",
            "hitl_scenario_template": "Scenario template",
            "hitl_events_template": "Events template",
            "hitl_objects_template": "Objects template",
        }
        assert LVSVideoUnderstandingConfig(**base_kwargs).enable_audio is False
        assert LVSVideoUnderstandingConfig(**base_kwargs, enable_audio=True).enable_audio is True


class TestLVSVideoUnderstandingInput:
    """Test LVSVideoUnderstandingInput model."""

    def test_basic_input(self):
        input_data = LVSVideoUnderstandingInput(
            sensor_id="sensor-001",
        )
        assert input_data.sensor_id == "sensor-001"

    def test_missing_sensor_id_fails(self):
        with pytest.raises(ValidationError):
            LVSVideoUnderstandingInput()

    def test_empty_sensor_id_fails(self):
        with pytest.raises(ValidationError):
            LVSVideoUnderstandingInput(
                sensor_id="",
            )


class TestLVSVideoUnderstandingInner:
    """Test the inner LVS video understanding function."""

    @pytest.fixture(autouse=True)
    def conversation_id(self):
        token = ContextState.get().conversation_id.set("default")
        yield
        ContextState.get().conversation_id.reset(token)

    @pytest.mark.asyncio
    async def test_video_understanding_payload_includes_enable_audio_when_set(self):
        config = LVSVideoUnderstandingConfig(
            lvs_backend_url="http://localhost:38111",
            hitl_scenario_template="Scenario",
            hitl_events_template="Events",
            hitl_objects_template="Objects",
            default_scenario="warehouse monitoring",
            default_events=["accident"],
            enable_audio=True,
            vst_internal_url="http://localhost:30888",
        )

        mock_video_url_result = MagicMock()
        mock_video_url_result.video_url = "http://localhost:30888/video.mp4"
        mock_video_url_tool = MagicMock()
        mock_video_url_tool.ainvoke = AsyncMock(return_value=mock_video_url_result)

        mock_builder = MagicMock()
        mock_builder.get_tool = AsyncMock(return_value=mock_video_url_tool)

        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.text = AsyncMock(return_value="")
        mock_resp.json = AsyncMock(
            return_value={"choices": [{"message": {"content": '{"video_summary": "ok", "events": []}'}}]}
        )
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        mock_session = MagicMock()
        mock_session.post.return_value = mock_resp
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        # HITL: empty responses accept defaults (scenario, events) / skip (objects) / proceed (confirm).
        mock_hitl_response = MagicMock()
        mock_hitl_response.content.text = ""
        mock_uim = MagicMock()
        mock_uim.prompt_user_input = AsyncMock(return_value=mock_hitl_response)
        mock_ctx = MagicMock()
        mock_ctx.user_interaction_manager = mock_uim

        with patch("vss_agents.tools.lvs_video_understanding.Context") as mock_context_class:
            mock_context_class.get.return_value = mock_ctx
            with patch("vss_agents.tools.lvs_video_understanding.aiohttp.ClientSession", return_value=mock_session):
                with patch("vss_agents.tools.lvs_video_understanding.aiohttp.ClientTimeout"):
                    gen = lvs_video_understanding.__wrapped__(config, mock_builder)
                    function_info = await gen.__anext__()
                    inner_fn = function_info.single_fn
                    await inner_fn(LVSVideoUnderstandingInput(sensor_id="sensor-1"))

        mock_session.post.assert_called_once()
        _, kwargs = mock_session.post.call_args
        assert kwargs["json"].get("enable_audio") is True
