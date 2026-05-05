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
"""Unit tests for the LVS stream understanding tool."""

import json
from unittest.mock import AsyncMock
from unittest.mock import MagicMock
from unittest.mock import patch

from nat.builder.context import ContextState
from pydantic import ValidationError
import pytest

from vss_agents.tools.lvs_config_media import LVSMediaStatus
from vss_agents.tools.lvs_media_state import LVSConfiguredMedia
from vss_agents.tools.lvs_media_state import clear_configured_media_state
from vss_agents.tools.lvs_media_state import remember_configured_media
from vss_agents.tools.lvs_stream_understanding import LVSStreamUnderstandingConfig
from vss_agents.tools.lvs_stream_understanding import LVSStreamUnderstandingInput
from vss_agents.tools.lvs_stream_understanding import LVSStreamUnderstandingOutput
from vss_agents.tools.lvs_stream_understanding import lvs_stream_understanding


class TestLVSStreamUnderstandingModels:
    """Test LVS stream understanding tool models."""

    def test_config_required_fields(self):
        config = LVSStreamUnderstandingConfig(lvs_backend_url="http://localhost:38111")
        assert config.lvs_backend_url == "http://localhost:38111"
        assert config.model == "gpt-4o"
        assert config.conn_timeout_ms == 5000
        assert config.read_timeout_ms == 600000

    def test_input_defaults_to_summary(self):
        input_data = LVSStreamUnderstandingInput(
            stream_name="CAM_1",
            start_time=0,
            end_time=45,
        )
        assert input_data.start_time == 0
        assert input_data.end_time == 45
        assert input_data.response_type == "summary"

    def test_input_extracts_seconds_from_text(self):
        input_data = LVSStreamUnderstandingInput(
            stream_name="CAM_1",
            start_time="start",
            end_time="45 seconds",
        )
        assert input_data.start_time == 0
        assert input_data.end_time == 45

    def test_input_validation(self):
        with pytest.raises(ValidationError):
            LVSStreamUnderstandingInput(
                stream_name="CAM_1",
                start_time="",
                end_time=45,
            )

        with pytest.raises(ValidationError):
            LVSStreamUnderstandingInput(
                stream_name="CAM_1",
                start_time=0,
                end_time=45,
                response_type="invalid",
            )

    def test_not_configured_output_does_not_finalize_agent(self):
        output = LVSStreamUnderstandingOutput(
            status=LVSMediaStatus.NOT_CONFIGURED,
            stream_name="CAM_1",
            configured=False,
            message="Call lvs_config_media before summarizing this stream.",
        )

        assert output.summary is None

    def test_failed_configured_output_finalizes_agent(self):
        output = LVSStreamUnderstandingOutput(
            status=LVSMediaStatus.FAILED,
            stream_name="CAM_1",
            stream_id="stream-uuid",
            configured=True,
            message='LVS /v1/stream_summarize failed with status 500: {"message":"backend error"}',
        )

        assert "stream_summarize failed" in output.summary


class TestLVSStreamUnderstandingInner:
    """Test the inner stream understanding function."""

    @pytest.fixture(autouse=True)
    def clear_memory(self):
        token = ContextState.get().conversation_id.set("default")
        clear_configured_media_state()
        yield
        clear_configured_media_state()
        ContextState.get().conversation_id.reset(token)

    async def _get_inner_fn(self, config):
        gen = lvs_stream_understanding.__wrapped__(config, AsyncMock())
        function_info = await gen.__anext__()
        return function_info.single_fn

    @pytest.mark.asyncio
    async def test_not_configured_returns_actionable_status(self):
        config = LVSStreamUnderstandingConfig(lvs_backend_url="http://localhost:38111")
        inner_fn = await self._get_inner_fn(config)

        result = await inner_fn(
            LVSStreamUnderstandingInput(
                stream_name="CAM_1",
                start_time=0,
                end_time=45,
            )
        )

        assert result.status == LVSMediaStatus.NOT_CONFIGURED
        assert result.configured is False
        # Message must (a) tell the user there are no captions yet and
        # (b) include the explicit trigger phrase the user must reply with to
        # start caption generation. The agent prompt requires this phrasing
        # so it surfaces verbatim and does NOT auto-call lvs_config_media.
        assert "no captions stored" in result.message.lower()
        assert "start summarizing the stream CAM_1" in result.message

    @pytest.mark.asyncio
    async def test_configured_stream_calls_stream_summarize(self):
        remember_configured_media(
            LVSConfiguredMedia(
                media_type="stream",
                media_name="CAM_1",
                media_id="stream-uuid",
                media_url="rtsp://example/stream",
                scenario="warehouse monitoring",
                events=("accident",),
                objects_of_interest=("forklift",),
            )
        )

        response_payload = {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "summary": "No incidents were detected.",
                            }
                        )
                    }
                }
            ]
        }
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.text = AsyncMock(return_value=json.dumps(response_payload))
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        mock_session = MagicMock()
        mock_session.post.return_value = mock_resp
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        config = LVSStreamUnderstandingConfig(
            lvs_backend_url="http://localhost:38111",
            model="nvidia/cosmos-reason2-8b",
        )

        with patch("vss_agents.tools.lvs_stream_understanding.aiohttp.ClientSession", return_value=mock_session):
            with patch("vss_agents.tools.lvs_stream_understanding.aiohttp.ClientTimeout"):
                inner_fn = await self._get_inner_fn(config)
                result = await inner_fn(
                    LVSStreamUnderstandingInput(
                        stream_name="CAM_1",
                        start_time=0,
                        end_time=45,
                        response_type="report",
                    )
                )

        assert result.status == LVSMediaStatus.SUCCESS
        assert result.configured is True
        assert result.stream_id == "stream-uuid"
        assert result.content == {"summary": "No incidents were detected."}
        mock_session.post.assert_called_once_with(
            "http://localhost:38111/v1/stream_summarize",
            json={
                "id": "stream-uuid",
                "model": "nvidia/cosmos-reason2-8b",
                "start_time": 0,
                "end_time": 45,
            },
        )

    @pytest.mark.asyncio
    async def test_configured_stream_accepted_message_asks_user_to_try_later(self):
        remember_configured_media(
            LVSConfiguredMedia(
                media_type="stream",
                media_name="CAM_1",
                media_id="stream-uuid",
                media_url="rtsp://example/stream",
                scenario="warehouse monitoring",
                events=("accident",),
                objects_of_interest=("forklift",),
            )
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

        config = LVSStreamUnderstandingConfig(
            lvs_backend_url="http://localhost:38111",
        )

        with patch("vss_agents.tools.lvs_stream_understanding.aiohttp.ClientSession", return_value=mock_session):
            with patch("vss_agents.tools.lvs_stream_understanding.aiohttp.ClientTimeout"):
                inner_fn = await self._get_inner_fn(config)
                result = await inner_fn(
                    LVSStreamUnderstandingInput(
                        stream_name="CAM_1",
                        start_time=0,
                        end_time=45,
                    )
                )

        assert result.status == LVSMediaStatus.ACCEPTED
        assert result.message == "Caption generation started. Please try again later."
        assert result.summary == "Caption generation started. Please try again later."

    @pytest.mark.asyncio
    async def test_configured_stream_is_scoped_to_conversation(self):
        remember_configured_media(
            LVSConfiguredMedia(
                media_type="stream",
                media_name="CAM_1",
                media_id="stream-uuid",
                media_url="rtsp://example/stream",
                scenario="warehouse monitoring",
                events=("accident",),
                objects_of_interest=("forklift",),
            )
        )

        other_conversation_token = ContextState.get().conversation_id.set("other-conversation")
        try:
            config = LVSStreamUnderstandingConfig(lvs_backend_url="http://localhost:38111")
            inner_fn = await self._get_inner_fn(config)

            result = await inner_fn(
                LVSStreamUnderstandingInput(
                    stream_name="CAM_1",
                    start_time=0,
                    end_time=45,
                )
            )
        finally:
            ContextState.get().conversation_id.reset(other_conversation_token)

        assert result.status == LVSMediaStatus.NOT_CONFIGURED
        assert result.configured is False
