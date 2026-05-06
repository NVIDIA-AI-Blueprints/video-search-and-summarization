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
"""Unit tests for the LVS media configuration tool."""

from unittest.mock import AsyncMock
from unittest.mock import MagicMock
from unittest.mock import patch

from nat.builder.context import ContextState
from pydantic import ValidationError
import pytest

from vss_agents.tools.lvs_config_media import LVSConfigMediaConfig
from vss_agents.tools.lvs_config_media import LVSConfigMediaInput
from vss_agents.tools.lvs_config_media import LVSConfigMediaOutput
from vss_agents.tools.lvs_config_media import LVSMediaStatus
from vss_agents.tools.lvs_config_media import lvs_config_media
from vss_agents.tools.lvs_media_state import clear_configured_media_state
from vss_agents.tools.lvs_media_state import configured_media


class TestLVSConfigMediaModels:
    """Test LVS media configuration tool models."""

    def test_config_required_fields(self):
        config = LVSConfigMediaConfig(
            lvs_backend_url="http://localhost:38111",
            vst_internal_url="http://localhost:30888",
            hitl_scenario_template="Scenario",
            hitl_events_template="Events",
            hitl_objects_template="Objects",
        )
        assert config.lvs_backend_url == "http://localhost:38111"
        assert config.vst_internal_url == "http://localhost:30888"
        assert config.chunk_duration == 10

    def test_missing_required_fields_raises(self):
        with pytest.raises(ValidationError):
            LVSConfigMediaConfig(
                lvs_backend_url="http://localhost:38111",
                hitl_scenario_template="Scenario",
                hitl_events_template="Events",
                hitl_objects_template="Objects",
            )

    def test_input_validates_stream_name(self):
        input_data = LVSConfigMediaInput(stream_name=" CAM_1 ")
        assert input_data.media_type == "stream"
        assert input_data.stream_name == "CAM_1"

        with pytest.raises(ValidationError):
            LVSConfigMediaInput(stream_name=" ")

    def test_output_summary_marks_configuration_attempt_as_terminal(self):
        output = LVSConfigMediaOutput(
            status=LVSMediaStatus.ACCEPTED,
            media_type="stream",
            media_name="CAM_1",
            media_id="stream-uuid",
            configured=True,
            message="Caption generation started. Please try again later.",
        )

        assert output.summary == "Caption generation started. Please try again later."


class TestLVSConfigMediaInner:
    """Test the inner LVS media configuration function."""

    @pytest.fixture(autouse=True)
    def clear_memory(self):
        token = ContextState.get().conversation_id.set("default")
        clear_configured_media_state()
        yield
        clear_configured_media_state()
        ContextState.get().conversation_id.reset(token)

    async def _get_inner_fn(self, config):
        gen = lvs_config_media.__wrapped__(config, AsyncMock())
        function_info = await gen.__anext__()
        return function_info.single_fn

    @pytest.mark.asyncio
    async def test_config_media_calls_generate_captions_and_updates_memory(self):
        config = LVSConfigMediaConfig(
            lvs_backend_url="http://localhost:38111",
            vst_internal_url="http://localhost:30888",
            model="nvidia/cosmos-reason2-8b",
            hitl_scenario_template="Scenario",
            hitl_events_template="Events",
            hitl_objects_template="Objects",
            default_scenario="warehouse monitoring",
            default_events=["accident"],
        )

        mock_resp = MagicMock()
        mock_resp.status = 202
        mock_resp.text = AsyncMock(return_value="")
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        mock_session = MagicMock()
        mock_session.post.return_value = mock_resp
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch("vss_agents.tools.lvs_config_media.get_stream_info_by_name", new_callable=AsyncMock) as mock_stream:
            mock_stream.return_value = ("stream-uuid", "rtsp://example/stream")
            with patch(
                "vss_agents.tools.lvs_config_media._prompt_user_input",
                new_callable=AsyncMock,
            ) as mock_prompt:
                mock_prompt.side_effect = ["", "", "forklifts, workers", ""]
                with patch("vss_agents.tools.lvs_config_media.aiohttp.ClientSession", return_value=mock_session):
                    with patch("vss_agents.tools.lvs_config_media.aiohttp.ClientTimeout"):
                        inner_fn = await self._get_inner_fn(config)
                        result = await inner_fn(LVSConfigMediaInput(stream_name="CAM_1"))

        assert result.status == LVSMediaStatus.ACCEPTED
        assert result.configured is True
        assert result.media_id == "stream-uuid"
        assert result.message == "Caption generation started. Please try again later."
        remembered_media = configured_media("stream", "cam_1")
        assert remembered_media is not None
        assert remembered_media.media_id == "stream-uuid"
        assert remembered_media.scenario == "warehouse monitoring"
        mock_session.post.assert_called_once_with(
            "http://localhost:38111/v1/generate_captions",
            json={
                "id": "stream-uuid",
                "model": "nvidia/cosmos-reason2-8b",
                "scenario": "warehouse monitoring",
                "events": ["accident"],
                "chunk_duration": 10,
            },
        )

    @pytest.mark.asyncio
    async def test_config_media_stream_not_found(self):
        config = LVSConfigMediaConfig(
            lvs_backend_url="http://localhost:38111",
            vst_internal_url="http://localhost:30888",
            hitl_scenario_template="Scenario",
            hitl_events_template="Events",
            hitl_objects_template="Objects",
        )

        with patch("vss_agents.tools.lvs_config_media.get_stream_info_by_name", new_callable=AsyncMock) as mock_stream:
            mock_stream.return_value = (None, None)
            inner_fn = await self._get_inner_fn(config)
            result = await inner_fn(LVSConfigMediaInput(stream_name="CAM_1"))

        assert result.status == LVSMediaStatus.FAILED
        assert "not found" in result.message.lower()
