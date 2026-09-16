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
from vss_agents.tools.rtvi_vlm_alert import _sensor_to_alert_rule_id
from vss_agents.tools.rtvi_vlm_alert import rtvi_vlm_alert


class TestRTVIVLMAlertInner:
    """Test the inner _rtvi_vlm_alert function."""

    @pytest.fixture
    def config(self):
        return RTVIVLMAlertConfig(
            alert_bridge_url="http://localhost:9080",
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
            alert_bridge_url="http://localhost:9080",
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

        # VA projects only Id/timestamp/end/sensorId unless `includes` asks for more;
        # the alerts path needs category and info to render an incident table.
        va_input = mock_va_tool.ainvoke.call_args.kwargs["input"]
        assert va_input["includes"] == ["category", "info"]

    @pytest.mark.asyncio
    async def test_get_incidents_by_id_uses_exact_lookup(self, mock_builder):
        config = RTVIVLMAlertConfig(
            alert_bridge_url="http://localhost:9080",
            vst_internal_url="http://10.0.0.1:30888",
            va_get_incidents_tool="va_get_incidents",
            va_get_incident_tool="va_get_incident",
        )
        mock_va_tool = AsyncMock()
        mock_va_tool.ainvoke.return_value = {
            "Id": "incident-123",
            "sensorId": "HWY_20",
            "timestamp": "2026-01-06T00:00:00.000Z",
        }
        mock_builder.get_tool.return_value = mock_va_tool

        inner_fn = await self._get_inner_fn(config, mock_builder)
        result = await inner_fn(
            RTVIVLMAlertInput(
                action="get_incidents",
                sensor_name="HWY_20",
                incident_id="incident-123",
                vlm_verified=True,
            )
        )

        assert result.success is True
        assert result.total_count == 1
        assert result.incidents == [mock_va_tool.ainvoke.return_value]
        mock_builder.get_tool.assert_awaited_once()
        assert mock_builder.get_tool.call_args.args[0] == "va_get_incident"
        assert mock_va_tool.ainvoke.call_args.kwargs["input"] == {
            "id": "incident-123",
            "includes": ["category", "info"],
            "vlm_verified": True,
        }

    @pytest.mark.asyncio
    async def test_get_incidents_by_id_rejects_other_sensor(self, mock_builder):
        config = RTVIVLMAlertConfig(
            alert_bridge_url="http://localhost:9080",
            vst_internal_url="http://10.0.0.1:30888",
            va_get_incident_tool="va_get_incident",
        )
        mock_va_tool = AsyncMock()
        mock_va_tool.ainvoke.return_value = {"Id": "incident-123", "sensorId": "OTHER_SENSOR"}
        mock_builder.get_tool.return_value = mock_va_tool

        inner_fn = await self._get_inner_fn(config, mock_builder)
        result = await inner_fn(
            RTVIVLMAlertInput(
                action="get_incidents",
                sensor_name="HWY_20",
                incident_id="incident-123",
            )
        )

        assert result.success is True
        assert result.total_count == 0
        assert result.incidents == []
        assert "not found" in result.message.lower()

    @pytest.mark.asyncio
    async def test_get_incidents_by_id_requires_exact_lookup_tool(self, mock_builder):
        config = RTVIVLMAlertConfig(
            alert_bridge_url="http://localhost:9080",
            vst_internal_url="http://10.0.0.1:30888",
            va_get_incidents_tool="va_get_incidents",
        )

        inner_fn = await self._get_inner_fn(config, mock_builder)
        result = await inner_fn(
            RTVIVLMAlertInput(
                action="get_incidents",
                sensor_name="HWY_20",
                incident_id="incident-123",
            )
        )

        assert result.success is False
        assert "va_get_incident_tool" in result.message
        mock_builder.get_tool.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_specific_incident_reports_succeed_three_of_three(self, mock_builder):
        """Reproduce the flaky Generate Report path: three older IDs on one sensor.

        Listing only the newest incident (max_count=1) matches 1/3. Exact ID
        lookup must return the requested document for every ID.
        """
        sensor = "sample-warehouse-ladder"
        requested_ids = (
            "72ac8fd288f681cde89cffa602cf466f2d2080d1",
            "06796dc8a1b5bdbee6516b6dfb1e1a2adef03ae2",
            "b6634aa9a92a5b647d8dd2985c8931b43ebe4420",
        )
        newest = {
            "Id": "b4b576b24bb73d1b628f030ce2a10b42416341ea",
            "sensorId": sensor,
            "timestamp": "2026-08-27T12:45:36.652Z",
        }
        catalog = {
            incident_id: {
                "Id": incident_id,
                "sensorId": sensor,
                "timestamp": f"2026-08-27T03:4{idx}:27.140Z",
            }
            for idx, incident_id in enumerate(requested_ids)
        }

        list_tool = AsyncMock()
        list_tool.ainvoke.return_value = {"incidents": [newest], "has_more": True}
        get_tool = AsyncMock()
        get_tool.ainvoke.side_effect = lambda input: catalog[input["id"]]

        async def _get_tool(name, wrapper_type=None):
            return get_tool if name == "va_get_incident" else list_tool

        mock_builder.get_tool.side_effect = _get_tool
        config = RTVIVLMAlertConfig(
            alert_bridge_url="http://localhost:9080",
            vst_internal_url="http://10.0.0.1:30888",
            va_get_incidents_tool="va_get_incidents",
            va_get_incident_tool="va_get_incident",
        )
        inner_fn = await self._get_inner_fn(config, mock_builder)

        list_hits = 0
        exact_hits = 0
        for incident_id in requested_ids:
            listed = await inner_fn(
                RTVIVLMAlertInput(action="get_incidents", sensor_name=sensor, max_count=1)
            )
            listed_ids = [incident.get("Id") for incident in listed.incidents or []]
            if incident_id in listed_ids:
                list_hits += 1

            found = await inner_fn(
                RTVIVLMAlertInput(
                    action="get_incidents",
                    sensor_name=sensor,
                    incident_id=incident_id,
                    vlm_verified=True,
                )
            )
            if found.success and found.total_count == 1 and found.incidents[0]["Id"] == incident_id:
                exact_hits += 1

        assert list_hits == 0
        assert exact_hits == 3
        assert get_tool.ainvoke.await_count == 3

    @pytest.mark.asyncio
    async def test_get_incidents_string_result(self, mock_builder):
        config = RTVIVLMAlertConfig(
            alert_bridge_url="http://localhost:9080",
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
            alert_bridge_url="http://localhost:9080",
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
        """Stop returns success when Alert Bridge reports rule already gone."""
        _sensor_to_alert_rule_id["SENSOR_404"] = "rule-uuid-404"

        mock_delete_resp = MagicMock()
        mock_delete_resp.status = 404
        mock_delete_resp.text = AsyncMock(return_value='{"status":"error","error":"not_found"}')
        mock_delete_cm = AsyncMock()
        mock_delete_cm.__aenter__ = AsyncMock(return_value=mock_delete_resp)
        mock_delete_cm.__aexit__ = AsyncMock(return_value=False)

        mock_session = MagicMock()
        mock_session.delete.return_value = mock_delete_cm

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
        assert "SENSOR_404" not in _sensor_to_alert_rule_id

    @pytest.mark.asyncio
    async def test_stop_error_response(self, config, mock_builder):
        """Stop reports failure when Alert Bridge returns 5xx."""
        _sensor_to_alert_rule_id["SENSOR_ERR"] = "rule-uuid-err"

        mock_delete_resp = MagicMock()
        mock_delete_resp.status = 500
        mock_delete_resp.text = AsyncMock(return_value="Internal error")
        mock_delete_cm = AsyncMock()
        mock_delete_cm.__aenter__ = AsyncMock(return_value=mock_delete_resp)
        mock_delete_cm.__aexit__ = AsyncMock(return_value=False)

        mock_session = MagicMock()
        mock_session.delete.return_value = mock_delete_cm

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
    async def test_stop_no_active_alert(self, config, mock_builder):
        _sensor_to_alert_rule_id.pop("MISSING_SENSOR", None)

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
        _sensor_to_alert_rule_id["TEST_STOP"] = "rule-uuid-999"

        mock_delete_resp = MagicMock()
        mock_delete_resp.status = 200
        mock_delete_resp.text = AsyncMock(return_value='{"status":"success"}')
        mock_delete_cm = AsyncMock()
        mock_delete_cm.__aenter__ = AsyncMock(return_value=mock_delete_resp)
        mock_delete_cm.__aexit__ = AsyncMock(return_value=False)

        mock_session = MagicMock()
        mock_session.delete.return_value = mock_delete_cm

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
        assert "TEST_STOP" not in _sensor_to_alert_rule_id

    @pytest.mark.asyncio
    async def test_connection_error(self, config, mock_builder):
        _sensor_to_alert_rule_id["ERR_SENSOR"] = "rule-uuid-err"

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
        _sensor_to_alert_rule_id["GEN_ERR"] = "rule-uuid-gen"

        mock_session_cm = AsyncMock()
        mock_session_cm.__aenter__ = AsyncMock(side_effect=RuntimeError("something broke"))

        with patch("vss_agents.tools.rtvi_vlm_alert.aiohttp.ClientSession", return_value=mock_session_cm):
            with patch("vss_agents.tools.rtvi_vlm_alert.aiohttp.ClientTimeout"):
                inner_fn = await self._get_inner_fn(config, mock_builder)
                inp = RTVIVLMAlertInput(action="stop", sensor_name="GEN_ERR")
                result = await inner_fn(inp)

        assert result.success is False
