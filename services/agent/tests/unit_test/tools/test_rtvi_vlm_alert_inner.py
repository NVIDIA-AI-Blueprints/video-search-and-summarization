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
"""Tests for rtvi_vlm_alert inner function via generator invocation."""

import json
from unittest.mock import AsyncMock
from unittest.mock import MagicMock
from unittest.mock import patch

import aiohttp
import pytest

from vss_agents.tools.rtvi_vlm_alert import RTVIVLMAlertConfig
from vss_agents.tools.rtvi_vlm_alert import RTVIVLMAlertInput
from vss_agents.tools.rtvi_vlm_alert import _sensor_to_rtvi_stream_id
from vss_agents.tools.rtvi_vlm_alert import rtvi_vlm_alert


class TestRTVIVLMAlertInner:
    """Test the inner _rtvi_vlm_alert function."""

    @pytest.fixture
    def config(self):
        return RTVIVLMAlertConfig(
            rtvi_vlm_base_url="http://localhost:8000",
            vst_internal_url="http://10.0.0.1:30888",
        )

    @pytest.fixture
    def mock_builder(self):
        return AsyncMock()

    async def _get_inner_fn(self, config, mock_builder):
        gen = rtvi_vlm_alert.__wrapped__(config, mock_builder)
        function_info = await gen.__anext__()
        return function_info.single_fn

    @pytest.mark.asyncio
    async def test_get_incidents_no_sensor_name(self, config, mock_builder):
        inner_fn = await self._get_inner_fn(config, mock_builder)
        inp = RTVIVLMAlertInput(action="get_incidents")
        result = await inner_fn(inp)
        assert result.success is False
        assert "required" in result.message.lower()

    @pytest.mark.asyncio
    async def test_get_incidents_no_va_tool(self, config, mock_builder):
        inner_fn = await self._get_inner_fn(config, mock_builder)
        inp = RTVIVLMAlertInput(action="get_incidents", sensor_name="HWY_20")
        result = await inner_fn(inp)
        assert result.success is False
        assert "not configured" in result.message.lower()

    @pytest.mark.asyncio
    async def test_get_incidents_with_va_tool(self, mock_builder):
        config = RTVIVLMAlertConfig(
            rtvi_vlm_base_url="http://localhost:8000",
            vst_internal_url="http://10.0.0.1:30888",
            va_get_incidents_tool="va_get_incidents",
        )
        mock_va_tool = AsyncMock()
        mock_va_tool.ainvoke.return_value = {"incidents": [{"id": "1"}], "has_more": False}
        mock_builder.get_tool.return_value = mock_va_tool

        inner_fn = await self._get_inner_fn(config, mock_builder)
        inp = RTVIVLMAlertInput(
            action="get_incidents",
            sensor_name="HWY_20",
            start_time="2026-01-06T00:00:00.000Z",
            end_time="2026-01-07T00:00:00.000Z",
        )
        result = await inner_fn(inp)
        assert result.success is True
        assert result.total_count == 1

    @pytest.mark.asyncio
    async def test_get_incidents_string_result(self, mock_builder):
        config = RTVIVLMAlertConfig(
            rtvi_vlm_base_url="http://localhost:8000",
            vst_internal_url="http://10.0.0.1:30888",
            va_get_incidents_tool="va_get_incidents",
        )
        mock_va_tool = AsyncMock()
        mock_va_tool.ainvoke.return_value = json.dumps({"incidents": [{"id": "1"}, {"id": "2"}]})
        mock_builder.get_tool.return_value = mock_va_tool

        inner_fn = await self._get_inner_fn(config, mock_builder)
        inp = RTVIVLMAlertInput(action="get_incidents", sensor_name="HWY_20")
        result = await inner_fn(inp)
        assert result.success is True
        assert result.total_count == 2

    @pytest.mark.asyncio
    async def test_get_incidents_va_tool_error(self, mock_builder):
        config = RTVIVLMAlertConfig(
            rtvi_vlm_base_url="http://localhost:8000",
            vst_internal_url="http://10.0.0.1:30888",
            va_get_incidents_tool="va_get_incidents",
        )
        mock_va_tool = AsyncMock()
        mock_va_tool.ainvoke.side_effect = RuntimeError("VA error")
        mock_builder.get_tool.return_value = mock_va_tool

        inner_fn = await self._get_inner_fn(config, mock_builder)
        inp = RTVIVLMAlertInput(action="get_incidents", sensor_name="HWY_20")
        result = await inner_fn(inp)
        assert result.success is False
        assert "Failed" in result.message

    @pytest.mark.asyncio
    async def test_start_no_sensor_name(self, config, mock_builder):
        inner_fn = await self._get_inner_fn(config, mock_builder)
        inp = RTVIVLMAlertInput(action="start")
        result = await inner_fn(inp)
        assert result.success is False
        assert "required" in result.message.lower()

    @pytest.mark.asyncio
    async def test_stop_no_sensor_name(self, config, mock_builder):
        inner_fn = await self._get_inner_fn(config, mock_builder)
        inp = RTVIVLMAlertInput(action="stop")
        result = await inner_fn(inp)
        assert result.success is False
        assert "required" in result.message.lower()

    @pytest.mark.asyncio
    async def test_start_sensor_not_found(self, config, mock_builder):
        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.raise_for_status = MagicMock()
        mock_resp.text = AsyncMock(
            return_value=json.dumps([{"stream1": [{"name": "OTHER_SENSOR", "url": "rtsp://ip/stream"}]}])
        )
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        mock_session = MagicMock()
        mock_session.get.return_value = mock_resp
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch("vss_agents.tools.rtvi_vlm_alert.aiohttp.ClientSession", return_value=mock_session):
            with patch("vss_agents.tools.rtvi_vlm_alert.aiohttp.ClientTimeout"):
                inner_fn = await self._get_inner_fn(config, mock_builder)
                inp = RTVIVLMAlertInput(action="start", sensor_name="HWY_20")
                result = await inner_fn(inp)

        assert result.success is False
        assert "not found" in result.message.lower()

    @pytest.mark.asyncio
    async def test_stop_404_response(self, config, mock_builder):
        """Test stop when stream delete returns 404."""
        _sensor_to_rtvi_stream_id["SENSOR_404"] = "rtvi-uuid-404"

        mock_delete_caption_resp = MagicMock()
        mock_delete_caption_resp.status = 200
        mock_delete_caption_cm = AsyncMock()
        mock_delete_caption_cm.__aenter__ = AsyncMock(return_value=mock_delete_caption_resp)
        mock_delete_caption_cm.__aexit__ = AsyncMock(return_value=False)

        mock_delete_stream_resp = MagicMock()
        mock_delete_stream_resp.status = 404
        mock_delete_stream_cm = AsyncMock()
        mock_delete_stream_cm.__aenter__ = AsyncMock(return_value=mock_delete_stream_resp)
        mock_delete_stream_cm.__aexit__ = AsyncMock(return_value=False)

        mock_session = MagicMock()
        mock_session.delete.side_effect = [mock_delete_caption_cm, mock_delete_stream_cm]

        mock_session_cm = AsyncMock()
        mock_session_cm.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session_cm.__aexit__ = AsyncMock(return_value=False)

        with patch("vss_agents.tools.rtvi_vlm_alert.aiohttp.ClientSession", return_value=mock_session_cm):
            with patch("vss_agents.tools.rtvi_vlm_alert.aiohttp.ClientTimeout"):
                inner_fn = await self._get_inner_fn(config, mock_builder)
                inp = RTVIVLMAlertInput(action="stop", sensor_name="SENSOR_404")
                result = await inner_fn(inp)

        assert result.success is True
        assert "already stopped" in result.message.lower()

    @pytest.mark.asyncio
    async def test_stop_error_response(self, config, mock_builder):
        """Test stop when stream delete returns error."""
        _sensor_to_rtvi_stream_id["SENSOR_ERR"] = "rtvi-uuid-err2"

        mock_delete_caption_resp = MagicMock()
        mock_delete_caption_resp.status = 200
        mock_delete_caption_cm = AsyncMock()
        mock_delete_caption_cm.__aenter__ = AsyncMock(return_value=mock_delete_caption_resp)
        mock_delete_caption_cm.__aexit__ = AsyncMock(return_value=False)

        mock_delete_stream_resp = MagicMock()
        mock_delete_stream_resp.status = 500
        mock_delete_stream_resp.text = AsyncMock(return_value="Internal error")
        mock_delete_stream_cm = AsyncMock()
        mock_delete_stream_cm.__aenter__ = AsyncMock(return_value=mock_delete_stream_resp)
        mock_delete_stream_cm.__aexit__ = AsyncMock(return_value=False)

        mock_session = MagicMock()
        mock_session.delete.side_effect = [mock_delete_caption_cm, mock_delete_stream_cm]

        mock_session_cm = AsyncMock()
        mock_session_cm.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session_cm.__aexit__ = AsyncMock(return_value=False)

        with patch("vss_agents.tools.rtvi_vlm_alert.aiohttp.ClientSession", return_value=mock_session_cm):
            with patch("vss_agents.tools.rtvi_vlm_alert.aiohttp.ClientTimeout"):
                inner_fn = await self._get_inner_fn(config, mock_builder)
                inp = RTVIVLMAlertInput(action="stop", sensor_name="SENSOR_ERR")
                result = await inner_fn(inp)

        assert result.success is False
        assert "Failed" in result.message

    @pytest.mark.asyncio
    async def test_stop_caption_error_continues(self, config, mock_builder):
        """Test stop when caption deletion raises error but continues."""
        _sensor_to_rtvi_stream_id["SENSOR_CAP_ERR"] = "rtvi-uuid-cap"

        mock_delete_caption_cm = AsyncMock()
        mock_delete_caption_cm.__aenter__ = AsyncMock(side_effect=RuntimeError("caption error"))
        mock_delete_caption_cm.__aexit__ = AsyncMock(return_value=False)

        mock_delete_stream_resp = MagicMock()
        mock_delete_stream_resp.status = 200
        mock_delete_stream_cm = AsyncMock()
        mock_delete_stream_cm.__aenter__ = AsyncMock(return_value=mock_delete_stream_resp)
        mock_delete_stream_cm.__aexit__ = AsyncMock(return_value=False)

        mock_session = MagicMock()
        mock_session.delete.side_effect = [mock_delete_caption_cm, mock_delete_stream_cm]

        mock_session_cm = AsyncMock()
        mock_session_cm.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session_cm.__aexit__ = AsyncMock(return_value=False)

        with patch("vss_agents.tools.rtvi_vlm_alert.aiohttp.ClientSession", return_value=mock_session_cm):
            with patch("vss_agents.tools.rtvi_vlm_alert.aiohttp.ClientTimeout"):
                inner_fn = await self._get_inner_fn(config, mock_builder)
                inp = RTVIVLMAlertInput(action="stop", sensor_name="SENSOR_CAP_ERR")
                result = await inner_fn(inp)

        assert result.success is True

    @pytest.mark.asyncio
    async def test_stop_no_active_alert(self, config, mock_builder):
        # Clear mapping
        _sensor_to_rtvi_stream_id.pop("MISSING_SENSOR", None)

        mock_session = MagicMock()
        mock_session_cm = AsyncMock()
        mock_session_cm.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session_cm.__aexit__ = AsyncMock(return_value=False)

        with patch("vss_agents.tools.rtvi_vlm_alert.aiohttp.ClientSession", return_value=mock_session_cm):
            with patch("vss_agents.tools.rtvi_vlm_alert.aiohttp.ClientTimeout"):
                inner_fn = await self._get_inner_fn(config, mock_builder)
                inp = RTVIVLMAlertInput(action="stop", sensor_name="MISSING_SENSOR")
                result = await inner_fn(inp)

        assert result.success is False
        assert "No active alert" in result.message

    @pytest.mark.asyncio
    async def test_stop_success(self, config, mock_builder):
        # Set up mapping
        _sensor_to_rtvi_stream_id["TEST_STOP"] = "rtvi-uuid-999"

        mock_delete_caption_resp = AsyncMock()
        mock_delete_caption_resp.status = 200
        mock_delete_caption_resp.__aenter__ = AsyncMock(return_value=mock_delete_caption_resp)
        mock_delete_caption_resp.__aexit__ = AsyncMock(return_value=False)

        mock_delete_stream_resp = AsyncMock()
        mock_delete_stream_resp.status = 200
        mock_delete_stream_resp.__aenter__ = AsyncMock(return_value=mock_delete_stream_resp)
        mock_delete_stream_resp.__aexit__ = AsyncMock(return_value=False)

        mock_session = MagicMock()
        mock_session.delete.side_effect = [mock_delete_caption_resp, mock_delete_stream_resp]

        mock_session_cm = AsyncMock()
        mock_session_cm.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session_cm.__aexit__ = AsyncMock(return_value=False)

        with patch("vss_agents.tools.rtvi_vlm_alert.aiohttp.ClientSession", return_value=mock_session_cm):
            with patch("vss_agents.tools.rtvi_vlm_alert.aiohttp.ClientTimeout"):
                inner_fn = await self._get_inner_fn(config, mock_builder)
                inp = RTVIVLMAlertInput(action="stop", sensor_name="TEST_STOP")
                result = await inner_fn(inp)

        assert result.success is True
        assert "stopped" in result.message.lower()

    @pytest.mark.asyncio
    async def test_connection_error(self, config, mock_builder):
        _sensor_to_rtvi_stream_id["ERR_SENSOR"] = "rtvi-uuid-err"

        mock_session_cm = AsyncMock()
        mock_session_cm.__aenter__ = AsyncMock(side_effect=aiohttp.ClientError("connection refused"))

        with patch("vss_agents.tools.rtvi_vlm_alert.aiohttp.ClientSession", return_value=mock_session_cm):
            with patch("vss_agents.tools.rtvi_vlm_alert.aiohttp.ClientTimeout"):
                inner_fn = await self._get_inner_fn(config, mock_builder)
                inp = RTVIVLMAlertInput(action="stop", sensor_name="ERR_SENSOR")
                result = await inner_fn(inp)

        assert result.success is False
        assert "Connection error" in result.message

    @pytest.mark.asyncio
    async def test_generic_error(self, config, mock_builder):
        _sensor_to_rtvi_stream_id["GEN_ERR"] = "rtvi-uuid-gen"

        mock_session_cm = AsyncMock()
        mock_session_cm.__aenter__ = AsyncMock(side_effect=RuntimeError("something broke"))

        with patch("vss_agents.tools.rtvi_vlm_alert.aiohttp.ClientSession", return_value=mock_session_cm):
            with patch("vss_agents.tools.rtvi_vlm_alert.aiohttp.ClientTimeout"):
                inner_fn = await self._get_inner_fn(config, mock_builder)
                inp = RTVIVLMAlertInput(action="stop", sensor_name="GEN_ERR")
                result = await inner_fn(inp)

        assert result.success is False
