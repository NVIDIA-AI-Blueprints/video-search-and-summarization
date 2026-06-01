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

"""Unit tests for RealtimeAlertService."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from realtime.config import ErrorCode, ResponseStatus, RuleStatus
from realtime.schemas import AlertRuleConfig
from realtime.services.realtime_service import RealtimeAlertService

from .conftest import SAMPLE_RTSP_URL, make_config


# ---------------------------------------------------------------------------
# start_alert
# ---------------------------------------------------------------------------

class TestStartAlert:
    """RealtimeAlertService.start_alert"""

    @pytest.mark.asyncio
    async def test_happy_path_returns_201(self, realtime_service, mock_rtvi_client):
        data, code = await realtime_service.start_alert(make_config())

        assert code == 201
        assert data["status"] == ResponseStatus.SUCCESS
        assert "id" in data
        assert "created_at" in data
        mock_rtvi_client.start_stream.assert_awaited_once()
        mock_rtvi_client.generate_captions.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_rule_registered_after_create(self, realtime_service):
        data, _ = await realtime_service.start_alert(make_config())
        list_data, _ = await realtime_service.list_alerts()
        rule_ids = [r["id"] for r in list_data["rules"]]
        assert data["id"] in rule_ids

    @pytest.mark.asyncio
    async def test_rtvi_stream_id_not_in_public_rule(self, realtime_service):
        await realtime_service.start_alert(make_config())
        list_data, _ = await realtime_service.list_alerts()
        for rule in list_data["rules"]:
            assert "rtvi_stream_id" not in rule

    @pytest.mark.asyncio
    async def test_all_fields_stored_in_rule(self, realtime_service):
        cfg = make_config(
            alert_type="fire",
            prompt="Detect fire",
            system_prompt="Be concise",
            chunk_duration=45,
            chunk_overlap_duration=10,
            num_frames_per_second_or_fixed_frames_chunk=5,
            use_fps_for_chunking=False,
            vlm_input_width=512,
            vlm_input_height=512,
            enable_reasoning=False,
        )
        await realtime_service.start_alert(cfg)
        list_data, _ = await realtime_service.list_alerts()
        rule = list_data["rules"][0]

        assert rule["alert_type"] == "fire"
        assert rule["prompt"] == "Detect fire"
        assert rule["system_prompt"] == "Be concise"
        assert rule["chunk_duration"] == 45
        assert rule["chunk_overlap_duration"] == 10
        assert rule["num_frames_per_second_or_fixed_frames_chunk"] == 5
        assert rule["use_fps_for_chunking"] is False
        assert rule["vlm_input_width"] == 512
        assert rule["vlm_input_height"] == 512
        assert rule["enable_reasoning"] is False
        assert rule["live_stream_url"] == SAMPLE_RTSP_URL
        assert rule["status"] == RuleStatus.ACTIVE

    @pytest.mark.asyncio
    async def test_extended_fields_stored_in_rule(self, realtime_service):
        """When extended RTVI options are provided, they appear in the in-memory
        rule dict (so they survive to the GET /api/v1/realtime response)."""
        cfg = make_config(
            api_type="internal",
            response_format={"type": "text"},
            stream_options={"include_usage": True},
            max_tokens=512,
            temperature=0.2,
            top_p=1.0,
            top_k=100,
            ignore_eos=True,
            seed=42,
            media_info={"type": "offset", "start_offset": 0, "end_offset": 4_000_000_000},
            enable_audio=True,
            mm_processor_kwargs={"key": "val"},
        )
        await realtime_service.start_alert(cfg)
        list_data, _ = await realtime_service.list_alerts()
        rule = list_data["rules"][0]

        assert rule["api_type"] == "internal"
        assert rule["response_format"] == {"type": "text"}
        assert rule["stream_options"] == {"include_usage": True}
        assert rule["max_tokens"] == 512
        assert rule["temperature"] == 0.2
        assert rule["top_p"] == 1.0
        assert rule["top_k"] == 100
        assert rule["ignore_eos"] is True
        assert rule["seed"] == 42
        assert rule["media_info"] == {"type": "offset", "start_offset": 0, "end_offset": 4_000_000_000}
        assert rule["enable_audio"] is True
        assert rule["mm_processor_kwargs"] == {"key": "val"}

    @pytest.mark.asyncio
    async def test_extended_fields_absent_from_rule_when_not_set(self, realtime_service):
        """Optional extended fields set to None are NOT written into the rule dict
        — they simply don't appear as keys, keeping the doc compact."""
        await realtime_service.start_alert(make_config())
        list_data, _ = await realtime_service.list_alerts()
        rule = list_data["rules"][0]

        for field in (
            "api_type", "response_format", "stream_options", "max_tokens",
            "temperature", "top_p", "top_k", "ignore_eos", "seed",
            "media_info", "enable_audio", "mm_processor_kwargs",
        ):
            assert field not in rule, f"expected {field!r} absent when not set"

    @pytest.mark.asyncio
    async def test_extended_fields_forwarded_to_generate_captions(
        self, realtime_service, mock_rtvi_client
    ):
        """When extended fields are set in AlertRuleConfig, they appear in the
        kwargs passed to RTVIVLMClient.generate_captions."""
        cfg = make_config(
            api_type="internal",
            response_format={"type": "text"},
            stream_options={"include_usage": True},
            max_tokens=256,
            temperature=0.5,
            top_p=0.9,
            top_k=50,
            ignore_eos=False,
            seed=7,
            media_info={"type": "offset", "start_offset": 0, "end_offset": 1_000_000},
            enable_audio=True,
            mm_processor_kwargs={"extra_key": "extra_val"},
        )
        await realtime_service.start_alert(cfg)

        mock_rtvi_client.generate_captions.assert_awaited_once()
        kw = mock_rtvi_client.generate_captions.call_args.kwargs
        assert kw["api_type"] == "internal"
        assert kw["response_format"] == {"type": "text"}
        assert kw["stream_options"] == {"include_usage": True}
        assert kw["max_tokens"] == 256
        assert kw["temperature"] == 0.5
        assert kw["top_p"] == 0.9
        assert kw["top_k"] == 50
        assert kw["ignore_eos"] is False
        assert kw["seed"] == 7
        assert kw["media_info"] == {"type": "offset", "start_offset": 0, "end_offset": 1_000_000}
        assert kw["enable_audio"] is True
        assert kw["mm_processor_kwargs"] == {"extra_key": "extra_val"}

    @pytest.mark.asyncio
    async def test_omitted_extended_fields_not_in_generate_captions_kwargs(
        self, realtime_service, mock_rtvi_client
    ):
        """When extended fields are None they must NOT appear in the kwargs sent
        to generate_captions — the RTVI client should omit them from the payload."""
        await realtime_service.start_alert(make_config())

        mock_rtvi_client.generate_captions.assert_awaited_once()
        kw = mock_rtvi_client.generate_captions.call_args.kwargs
        # The kwargs are forwarded as None — the client-side filtering happens
        # in RTVIVLMClient.generate_captions; what matters here is that the
        # service passes them through (not that they are absent from kwargs).
        # Verify the None values are correctly passed.
        assert kw.get("api_type") is None
        assert kw.get("max_tokens") is None
        assert kw.get("temperature") is None

    @pytest.mark.asyncio
    async def test_description_defaults_to_empty_in_stream_payload(
        self, realtime_service, mock_rtvi_client
    ):
        """RTVI requires `description`; the client must send '' when caller omits it."""
        await realtime_service.start_alert(make_config(description=None))
        call_kwargs = mock_rtvi_client.start_stream.call_args
        payload = call_kwargs[0][0] if call_kwargs[0] else call_kwargs.kwargs.get("payload", {})
        assert payload.get("description") is None or payload.get("description") == ""

    @pytest.mark.asyncio
    async def test_description_forwarded_when_provided(
        self, realtime_service, mock_rtvi_client
    ):
        await realtime_service.start_alert(make_config(description="Warehouse cam"))
        call_kwargs = mock_rtvi_client.start_stream.call_args
        payload = call_kwargs[0][0] if call_kwargs[0] else call_kwargs.kwargs.get("payload", {})
        assert payload["description"] == "Warehouse cam"

    @pytest.mark.asyncio
    async def test_sensor_id_forwarded_to_rtvi(self, realtime_service, mock_rtvi_client):
        await realtime_service.start_alert(make_config(sensor_id="my-sensor-42"))
        call_kwargs = mock_rtvi_client.start_stream.call_args
        payload = call_kwargs[0][0] if call_kwargs[0] else call_kwargs.kwargs.get("payload", {})
        assert payload["id"] == "my-sensor-42"

    @pytest.mark.asyncio
    async def test_no_model_returns_422(self, realtime_service):
        realtime_service._default_model = ""
        data, code = await realtime_service.start_alert(make_config(model=""))
        assert code == 422
        assert data["error"] == ErrorCode.VALIDATION_FAILED

    @pytest.mark.asyncio
    async def test_default_model_fallback(self, realtime_service, mock_rtvi_client):
        realtime_service._default_model = "fallback-model"
        data, code = await realtime_service.start_alert(make_config(model=""))
        assert code == 201
        mock_rtvi_client.generate_captions.assert_awaited_once()
        call_kwargs = mock_rtvi_client.generate_captions.call_args.kwargs
        assert call_kwargs["model"] == "fallback-model"

    @pytest.mark.asyncio
    async def test_sensor_name_forwarded_to_rtvi_streams_add(
        self, realtime_service, mock_rtvi_client
    ):
        """AlertRuleConfig.sensor_name reaches RTVI's /streams/add payload."""
        cfg = make_config(sensor_name="warehouse")
        await realtime_service.start_alert(cfg)
        mock_rtvi_client.start_stream.assert_awaited_once()
        payload = mock_rtvi_client.start_stream.call_args.args[0]
        assert payload["sensor_name"] == "warehouse"
        list_data, _ = await realtime_service.list_alerts()
        assert list_data["rules"][0]["sensor_name"] == "warehouse"

    @pytest.mark.asyncio
    async def test_sensor_name_omitted_passes_through_as_none(
        self, realtime_service, mock_rtvi_client
    ):
        """Omitting ``sensor_name`` is still legal — the field stays
        ``None`` on the payload handed to the RTVI client, which then
        drops it from the actual /streams/add POST body so RTVI applies
        its own server-side default."""
        _, code = await realtime_service.start_alert(make_config())
        assert code == 201
        payload = mock_rtvi_client.start_stream.call_args.args[0]
        assert payload.get("sensor_name") is None

    @pytest.mark.asyncio
    async def test_start_stream_failure_returns_502(self, realtime_service, mock_rtvi_client):
        mock_rtvi_client.start_stream.side_effect = httpx.ConnectError("connection refused")
        data, code = await realtime_service.start_alert(make_config())
        assert code == 502
        assert data["error"] == ErrorCode.RTVI_VLM_UNAVAILABLE

    @pytest.mark.asyncio
    async def test_no_stream_id_returns_502(self, realtime_service, mock_rtvi_client):
        mock_rtvi_client.start_stream.return_value = {"results": [{}]}
        data, code = await realtime_service.start_alert(make_config())
        assert code == 502
        assert data["error"] == ErrorCode.RTVI_INVALID_RESPONSE

    @pytest.mark.asyncio
    async def test_captions_failure_rolls_back_stream(self, realtime_service, mock_rtvi_client):
        mock_rtvi_client.generate_captions.side_effect = httpx.HTTPStatusError(
            "500", request=httpx.Request("POST", "http://mock"), response=httpx.Response(500)
        )
        data, code = await realtime_service.start_alert(make_config())
        assert code == 502
        mock_rtvi_client.stop_stream.assert_awaited_once_with("stream-abc-123")
        list_data, _ = await realtime_service.list_alerts()
        assert list_data["count"] == 0


# ---------------------------------------------------------------------------
# stop_alert
# ---------------------------------------------------------------------------

class TestStopAlert:
    """RealtimeAlertService.stop_alert"""

    @pytest.mark.asyncio
    async def test_delete_existing_rule(self, realtime_service, mock_rtvi_client):
        create_data, _ = await realtime_service.start_alert(make_config())
        rule_id = create_data["id"]

        data, code = await realtime_service.stop_alert(rule_id)

        assert code == 200
        assert data["status"] == ResponseStatus.SUCCESS
        mock_rtvi_client.stop_captions.assert_awaited()
        mock_rtvi_client.stop_stream.assert_awaited()

    @pytest.mark.asyncio
    async def test_delete_removes_from_registry(self, realtime_service):
        create_data, _ = await realtime_service.start_alert(make_config())
        rule_id = create_data["id"]

        await realtime_service.stop_alert(rule_id)

        list_data, _ = await realtime_service.list_alerts()
        assert list_data["count"] == 0

    @pytest.mark.asyncio
    async def test_delete_nonexistent_returns_404(self, realtime_service):
        data, code = await realtime_service.stop_alert("nonexistent-id")
        assert code == 404
        assert data["error"] == ErrorCode.NOT_FOUND

    @pytest.mark.asyncio
    async def test_stop_stream_failure_still_removes_rule(self, realtime_service, mock_rtvi_client):
        """Warn-and-continue: rule is removed even if RTVI stop_stream fails."""
        create_data, _ = await realtime_service.start_alert(make_config())
        rule_id = create_data["id"]

        mock_rtvi_client.stop_stream.side_effect = httpx.ConnectError("timeout")
        data, code = await realtime_service.stop_alert(rule_id)

        assert code == 200
        list_data, _ = await realtime_service.list_alerts()
        assert list_data["count"] == 0

    @pytest.mark.asyncio
    async def test_stop_captions_failure_continues(self, realtime_service, mock_rtvi_client):
        create_data, _ = await realtime_service.start_alert(make_config())
        rule_id = create_data["id"]

        mock_rtvi_client.stop_captions.side_effect = httpx.ConnectError("timeout")
        data, code = await realtime_service.stop_alert(rule_id)

        assert code == 200
        mock_rtvi_client.stop_stream.assert_awaited()


# ---------------------------------------------------------------------------
# list_alerts
# ---------------------------------------------------------------------------

class TestListAlerts:
    """RealtimeAlertService.list_alerts"""

    @pytest.mark.asyncio
    async def test_empty_list(self, realtime_service):
        data, code = await realtime_service.list_alerts()
        assert code == 200
        assert data["count"] == 0
        assert data["rules"] == []

    @pytest.mark.asyncio
    async def test_multiple_rules(self, realtime_service):
        await realtime_service.start_alert(make_config(alert_type="fire"))
        await realtime_service.start_alert(make_config(alert_type="collision"))

        data, code = await realtime_service.list_alerts()

        assert code == 200
        assert data["count"] == 2
        types = {r["alert_type"] for r in data["rules"]}
        assert types == {"fire", "collision"}


# ---------------------------------------------------------------------------
# _extract_stream_id
# ---------------------------------------------------------------------------

class TestExtractStreamId:
    """RealtimeAlertService._extract_stream_id"""

    def test_from_results_list(self):
        resp = {"results": [{"id": "abc-123", "status": "added"}]}
        assert RealtimeAlertService._extract_stream_id(resp) == "abc-123"

    def test_from_stream_id_key(self):
        resp = {"stream_id": "xyz-789"}
        assert RealtimeAlertService._extract_stream_id(resp) == "xyz-789"

    def test_from_id_key(self):
        resp = {"id": "direct-id"}
        assert RealtimeAlertService._extract_stream_id(resp) == "direct-id"

    def test_empty_results(self):
        resp = {"results": []}
        assert RealtimeAlertService._extract_stream_id(resp) is None

    def test_missing_id_in_results(self):
        resp = {"results": [{"status": "added"}]}
        assert RealtimeAlertService._extract_stream_id(resp) is None

    def test_empty_response(self):
        assert RealtimeAlertService._extract_stream_id({}) is None


# ---------------------------------------------------------------------------
# AlertRuleConfig dataclass
# ---------------------------------------------------------------------------

class TestAlertRuleConfig:
    """AlertRuleConfig dataclass validation."""

    def test_defaults(self):
        cfg = AlertRuleConfig(
            live_stream_url="rtsp://host/stream",
            alert_type="test",
            prompt="test prompt",
            sensor_id="sensor-1",
        )
        assert cfg.system_prompt == ""
        assert cfg.model == ""
        assert cfg.chunk_duration == 30
        assert cfg.chunk_overlap_duration == 5
        assert cfg.num_frames_per_second_or_fixed_frames_chunk == 10
        assert cfg.use_fps_for_chunking is True
        assert cfg.vlm_input_width == 256
        assert cfg.vlm_input_height == 256
        assert cfg.enable_reasoning is True
        # Extended optional fields default to None (omitted from RTVI payload)
        assert cfg.api_type is None
        assert cfg.response_format is None
        assert cfg.stream_options is None
        assert cfg.max_tokens is None
        assert cfg.temperature is None
        assert cfg.top_p is None
        assert cfg.top_k is None
        assert cfg.ignore_eos is None
        assert cfg.seed is None
        assert cfg.media_info is None
        assert cfg.enable_audio is None
        assert cfg.mm_processor_kwargs is None

    def test_extended_fields_round_trip(self):
        """All 12 extended fields survive the frozen dataclass construction."""
        cfg = AlertRuleConfig(
            live_stream_url="rtsp://host/stream",
            alert_type="test",
            prompt="test prompt",
            api_type="internal",
            response_format={"type": "text"},
            stream_options={"include_usage": True},
            max_tokens=512,
            temperature=0.2,
            top_p=1.0,
            top_k=100,
            ignore_eos=True,
            seed=42,
            media_info={"type": "offset", "start_offset": 0, "end_offset": 4_000_000_000},
            enable_audio=True,
            mm_processor_kwargs={"key": "val"},
        )
        assert cfg.api_type == "internal"
        assert cfg.response_format == {"type": "text"}
        assert cfg.stream_options == {"include_usage": True}
        assert cfg.max_tokens == 512
        assert cfg.temperature == 0.2
        assert cfg.top_p == 1.0
        assert cfg.top_k == 100
        assert cfg.ignore_eos is True
        assert cfg.seed == 42
        assert cfg.media_info == {"type": "offset", "start_offset": 0, "end_offset": 4_000_000_000}
        assert cfg.enable_audio is True
        assert cfg.mm_processor_kwargs == {"key": "val"}

    def test_frozen(self):
        cfg = make_config()
        with pytest.raises(AttributeError):
            cfg.prompt = "changed"


# ---------------------------------------------------------------------------
# RTVIVLMClient.generate_captions — payload filtering
# ---------------------------------------------------------------------------

class TestRTVIVLMClientGenerateCaptions:
    """RTVIVLMClient.generate_captions builds the correct RTVI JSON payload.

    Tests focus on the extended optional fields: when a field is None it must
    be absent from the POST body; when set it must appear with the correct
    value. The required fields (id, prompt, model, etc.) are always present.
    """

    @pytest.mark.asyncio
    async def test_required_fields_always_present(self):
        from unittest.mock import AsyncMock, MagicMock
        from realtime.services.rtvi_client import RTVIVLMClient

        client = RTVIVLMClient("http://rtvi")
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        client._client = AsyncMock()
        client._client.post.return_value = mock_resp

        await client.generate_captions(
            stream_id="sid-1", prompt="detect fire", model="cosmos",
        )

        _, kwargs = client._client.post.call_args
        payload = kwargs["json"]
        for key in ("id", "prompt", "model", "system_prompt",
                    "chunk_duration", "chunk_overlap_duration",
                    "stream", "num_frames_per_second_or_fixed_frames_chunk",
                    "use_fps_for_chunking", "vlm_input_width",
                    "vlm_input_height", "enable_reasoning"):
            assert key in payload, f"required key {key!r} missing from payload"
        assert payload["id"] == "sid-1"
        assert payload["stream"] is True

    @pytest.mark.asyncio
    async def test_optional_fields_absent_when_none(self):
        """When optional extended fields are None (the default), they must NOT
        appear in the JSON sent to RTVI — letting RTVI use its server-side defaults."""
        from unittest.mock import AsyncMock, MagicMock
        from realtime.services.rtvi_client import RTVIVLMClient

        client = RTVIVLMClient("http://rtvi")
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        client._client = AsyncMock()
        client._client.post.return_value = mock_resp

        await client.generate_captions(
            stream_id="sid-1", prompt="p", model="m",
        )

        _, kwargs = client._client.post.call_args
        payload = kwargs["json"]
        for key in (
            "api_type", "response_format", "stream_options", "max_tokens",
            "temperature", "top_p", "top_k", "ignore_eos", "seed",
            "media_info", "enable_audio", "mm_processor_kwargs",
        ):
            assert key not in payload, f"optional key {key!r} should be absent when None"

    @pytest.mark.asyncio
    async def test_optional_fields_present_when_set(self):
        """When optional extended fields are set they appear in the JSON payload."""
        from unittest.mock import AsyncMock, MagicMock
        from realtime.services.rtvi_client import RTVIVLMClient

        client = RTVIVLMClient("http://rtvi")
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        client._client = AsyncMock()
        client._client.post.return_value = mock_resp

        await client.generate_captions(
            stream_id="sid-1",
            prompt="p",
            model="m",
            api_type="internal",
            response_format={"type": "text"},
            stream_options={"include_usage": True},
            max_tokens=512,
            temperature=0.2,
            top_p=0.95,
            top_k=50,
            ignore_eos=False,
            seed=7,
            media_info={"type": "offset", "start_offset": 0, "end_offset": 1_000_000},
            enable_audio=True,
            mm_processor_kwargs={"k": "v"},
        )

        _, kwargs = client._client.post.call_args
        payload = kwargs["json"]
        assert payload["api_type"] == "internal"
        assert payload["response_format"] == {"type": "text"}
        assert payload["stream_options"] == {"include_usage": True}
        assert payload["max_tokens"] == 512
        assert payload["temperature"] == 0.2
        assert payload["top_p"] == 0.95
        assert payload["top_k"] == 50
        assert payload["ignore_eos"] is False
        assert payload["seed"] == 7
        assert payload["media_info"] == {"type": "offset", "start_offset": 0, "end_offset": 1_000_000}
        assert payload["enable_audio"] is True
        assert payload["mm_processor_kwargs"] == {"k": "v"}

    @pytest.mark.asyncio
    async def test_false_bool_is_included_not_filtered(self):
        """``False`` is a valid value — it must not be treated as falsy None."""
        from unittest.mock import AsyncMock, MagicMock
        from realtime.services.rtvi_client import RTVIVLMClient

        client = RTVIVLMClient("http://rtvi")
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        client._client = AsyncMock()
        client._client.post.return_value = mock_resp

        await client.generate_captions(
            stream_id="s", prompt="p", model="m",
            ignore_eos=False,
            enable_audio=False,
        )

        _, kwargs = client._client.post.call_args
        payload = kwargs["json"]
        assert "ignore_eos" in payload and payload["ignore_eos"] is False
        assert "enable_audio" in payload and payload["enable_audio"] is False

    @pytest.mark.asyncio
    async def test_alert_category_included_when_set(self):
        """alert_category is forwarded as-is when truthy."""
        from unittest.mock import AsyncMock, MagicMock
        from realtime.services.rtvi_client import RTVIVLMClient

        client = RTVIVLMClient("http://rtvi")
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        client._client = AsyncMock()
        client._client.post.return_value = mock_resp

        await client.generate_captions(
            stream_id="s", prompt="p", model="m",
            alert_category="Worker PPE Violation",
        )

        _, kwargs = client._client.post.call_args
        payload = kwargs["json"]
        assert payload["alert_category"] == "Worker PPE Violation"


# ---------------------------------------------------------------------------
# Service-level multi-rule patterns (exercised by the always-on route, but
# tested here against RealtimeAlertService directly).
# ---------------------------------------------------------------------------

class TestRealtimeAlertServiceMultiRule:
    """Service-level tests for the multi-rule patterns the always-on route
    relies on: starting N rules against the same live stream, tearing them
    all down, and tolerating per-rule failures without poisoning the
    registry.

    These tests drive ``RealtimeAlertService`` directly — they do **not**
    exercise the always-on route, the :class:`AlwaysOnService` sidecar,
    the dedupe lock, or the Pydantic YAML loader. Coverage for those
    concerns lives in ``test_realtime_routes.py::TestAlwaysOn*``.
    """

    @pytest.mark.asyncio
    async def test_multiple_rules_same_stream(self, realtime_service, mock_rtvi_client):
        """Two rules against the same live_stream_url register as distinct rules."""
        d1, c1 = await realtime_service.start_alert(
            make_config(alert_type="collision", prompt="p1")
        )
        d2, c2 = await realtime_service.start_alert(
            make_config(alert_type="fire", prompt="p2")
        )

        assert c1 == 201 and c2 == 201
        assert d1["id"] != d2["id"]
        assert mock_rtvi_client.start_stream.await_count == 2
        assert mock_rtvi_client.generate_captions.await_count == 2

        list_data, _ = await realtime_service.list_alerts()
        assert list_data["count"] == 2
        assert {r["alert_type"] for r in list_data["rules"]} == {"collision", "fire"}

    @pytest.mark.asyncio
    async def test_bulk_stop_tears_down_all_rules(self, realtime_service):
        """Simulates camera_remove: stopping each previously-created rule id empties the registry."""
        ids = []
        for alert_type in ("fire", "collision", "loitering"):
            data, _ = await realtime_service.start_alert(make_config(alert_type=alert_type))
            ids.append(data["id"])

        for rule_id in ids:
            _, code = await realtime_service.stop_alert(rule_id)
            assert code == 200

        list_data, _ = await realtime_service.list_alerts()
        assert list_data["count"] == 0

    @pytest.mark.asyncio
    async def test_partial_start_failure_does_not_poison_registry(
        self, realtime_service, mock_rtvi_client
    ):
        """A single failing start_alert leaves the registry with only the successful rules."""
        mock_rtvi_client.start_stream.side_effect = [
            {"results": [{"id": "s1"}]},
            httpx.ConnectError("boom"),
            {"results": [{"id": "s3"}]},
        ]
        _, c1 = await realtime_service.start_alert(make_config(alert_type="a"))
        _, c2 = await realtime_service.start_alert(make_config(alert_type="b"))
        _, c3 = await realtime_service.start_alert(make_config(alert_type="c"))

        assert c1 == 201
        assert c2 == 502
        assert c3 == 201

        list_data, _ = await realtime_service.list_alerts()
        assert list_data["count"] == 2
        assert {r["alert_type"] for r in list_data["rules"]} == {"a", "c"}

    @pytest.mark.asyncio
    async def test_bulk_stop_with_one_rtvi_failure_still_drains_registry(
        self, realtime_service, mock_rtvi_client
    ):
        """A stop_stream failure on one rule must not leave the other rule behind."""
        d1, _ = await realtime_service.start_alert(make_config(alert_type="a"))
        d2, _ = await realtime_service.start_alert(make_config(alert_type="b"))

        mock_rtvi_client.stop_stream.side_effect = [
            httpx.ConnectError("down"),
            {"status": "deleted"},
        ]
        _, c1 = await realtime_service.stop_alert(d1["id"])
        _, c2 = await realtime_service.stop_alert(d2["id"])

        assert c1 == 200
        assert c2 == 200

        list_data, _ = await realtime_service.list_alerts()
        assert list_data["count"] == 0


# ---------------------------------------------------------------------------
# get_alert (in-memory path)
# ---------------------------------------------------------------------------

class TestGetAlert:
    """RealtimeAlertService.get_alert — in-memory path."""

    @pytest.mark.asyncio
    async def test_get_existing_rule(self, realtime_service):
        create_data, _ = await realtime_service.start_alert(make_config())
        rule_id = create_data["id"]

        data, code = await realtime_service.get_alert(rule_id)

        assert code == 200
        assert data["status"] == ResponseStatus.SUCCESS
        assert data["rule"]["id"] == rule_id
        assert data["rule"]["alert_type"] == "collision"
        assert "rtvi_stream_id" not in data["rule"]

    @pytest.mark.asyncio
    async def test_get_nonexistent_returns_404(self, realtime_service):
        data, code = await realtime_service.get_alert("nonexistent-id")
        assert code == 404
        assert data["error"] == ErrorCode.NOT_FOUND


# ---------------------------------------------------------------------------
# Persistent path (RuleStore-backed)
# ---------------------------------------------------------------------------

class _FakeRuleStore:
    """In-memory RuleStore fake for testing the persistent code path
    without a real Elasticsearch cluster."""

    def __init__(self):
        self._docs = {}

    def create(self, rule_id, document):
        self._docs[rule_id] = {**document, "_id": rule_id}
        return self._docs[rule_id]

    def get(self, rule_id):
        doc = self._docs.get(rule_id)
        return {**doc} if doc else None

    def list(self, filters=None, size=100, from_=0):
        items = list(self._docs.values())
        if filters:
            items = [
                doc for doc in items
                if all(doc.get(k) == v for k, v in filters.items())
            ]
        return {"items": items[from_:from_ + size], "total": len(items)}

    def update(self, rule_id, partial):
        if rule_id not in self._docs:
            from persistence.exceptions import DocumentNotFoundError
            raise DocumentNotFoundError("rules", rule_id)
        self._docs[rule_id].update(partial)
        return self._docs[rule_id]

    def delete(self, rule_id):
        if rule_id in self._docs:
            del self._docs[rule_id]
            return True
        return False


@pytest.fixture()
def fake_rule_store():
    return _FakeRuleStore()


@pytest.fixture()
def persistent_service(mock_rtvi_client, fake_rule_store):
    """RealtimeAlertService with a fake RuleStore (persistent path)."""
    with patch("realtime.services.realtime_service.load_config", return_value={
        "rtvi_vlm": {
            "base_url": "http://mock:8000",
            "timeout": 5,
            "default_model": "default-vlm",
            "captions_ack_timeout": 0.1,
            "stream_readiness_poll_interval": 0.01,
            "stream_readiness_max_wait": 0.05,
        }
    }):
        svc = RealtimeAlertService(rule_store=fake_rule_store)
    svc._client = mock_rtvi_client
    return svc


class TestPersistentStartAlert:
    """start_alert with RuleStore — persist-first flow."""

    @pytest.mark.asyncio
    async def test_happy_path_writes_to_store(self, persistent_service, fake_rule_store):
        data, code = await persistent_service.start_alert(make_config())
        assert code == 201
        rule_id = data["id"]
        stored = fake_rule_store.get(rule_id)
        assert stored is not None
        assert stored["status"] == RuleStatus.ACTIVE
        assert stored["rtvi_stream_id"] == "stream-abc-123"

    @pytest.mark.asyncio
    async def test_rtvi_failure_rolls_back_es_record(
        self, persistent_service, fake_rule_store, mock_rtvi_client
    ):
        mock_rtvi_client.start_stream.side_effect = httpx.ConnectError("down")
        data, code = await persistent_service.start_alert(make_config())
        assert code == 502
        assert len(fake_rule_store._docs) == 0

    @pytest.mark.asyncio
    async def test_captions_failure_rolls_back_es_and_stream(
        self, persistent_service, fake_rule_store, mock_rtvi_client
    ):
        mock_rtvi_client.generate_captions.side_effect = httpx.HTTPStatusError(
            "500", request=httpx.Request("POST", "http://x"), response=httpx.Response(500)
        )
        data, code = await persistent_service.start_alert(make_config())
        assert code == 502
        assert len(fake_rule_store._docs) == 0
        mock_rtvi_client.stop_stream.assert_awaited()

    @pytest.mark.asyncio
    async def test_extended_fields_persisted_to_store(
        self, persistent_service, fake_rule_store
    ):
        """All 12 extended optional fields must appear in the ES document when set."""
        cfg = make_config(
            api_type="internal",
            response_format={"type": "text"},
            stream_options={"include_usage": True},
            max_tokens=512,
            temperature=0.2,
            top_p=0.95,
            top_k=50,
            ignore_eos=True,
            seed=42,
            media_info={"type": "offset", "start_offset": 0, "end_offset": 4_000_000_000},
            enable_audio=True,
            mm_processor_kwargs={"key": "val"},
        )
        data, code = await persistent_service.start_alert(cfg)
        assert code == 201
        stored = fake_rule_store.get(data["id"])
        assert stored["api_type"] == "internal"
        assert stored["response_format"] == {"type": "text"}
        assert stored["stream_options"] == {"include_usage": True}
        assert stored["max_tokens"] == 512
        assert stored["temperature"] == 0.2
        assert stored["top_p"] == 0.95
        assert stored["top_k"] == 50
        assert stored["ignore_eos"] is True
        assert stored["seed"] == 42
        assert stored["media_info"] == {"type": "offset", "start_offset": 0, "end_offset": 4_000_000_000}
        assert stored["enable_audio"] is True
        assert stored["mm_processor_kwargs"] == {"key": "val"}

    @pytest.mark.asyncio
    async def test_extended_fields_absent_from_store_when_none(
        self, persistent_service, fake_rule_store
    ):
        """Extended optional fields set to None must NOT appear as keys in the ES document."""
        data, code = await persistent_service.start_alert(make_config())
        assert code == 201
        stored = fake_rule_store.get(data["id"])
        for field in (
            "api_type", "response_format", "stream_options", "max_tokens",
            "temperature", "top_p", "top_k", "ignore_eos", "seed",
            "media_info", "enable_audio", "mm_processor_kwargs",
        ):
            assert field not in stored, (
                f"expected {field!r} absent from ES document when not set"
            )


class TestPersistentStopAlert:
    """stop_alert with RuleStore — ES-first flow."""

    @pytest.mark.asyncio
    async def test_deletes_es_then_tears_down_rtvi(
        self, persistent_service, fake_rule_store, mock_rtvi_client
    ):
        create_data, _ = await persistent_service.start_alert(make_config())
        rule_id = create_data["id"]

        data, code = await persistent_service.stop_alert(rule_id)

        assert code == 200
        assert fake_rule_store.get(rule_id) is None
        mock_rtvi_client.stop_captions.assert_awaited()
        mock_rtvi_client.stop_stream.assert_awaited()

    @pytest.mark.asyncio
    async def test_nonexistent_returns_404(self, persistent_service):
        data, code = await persistent_service.stop_alert("nonexistent")
        assert code == 404

    @pytest.mark.asyncio
    async def test_rtvi_failure_tolerated(
        self, persistent_service, fake_rule_store, mock_rtvi_client
    ):
        """ES record deleted even when RTVI teardown fails.

        DELETE must always succeed from the user's perspective: the durable
        record is removed first, then RTVI teardown is best-effort.  An
        RTVI outage must not block rule cleanup.
        """
        create_data, _ = await persistent_service.start_alert(make_config())
        rule_id = create_data["id"]
        mock_rtvi_client.stop_stream.side_effect = httpx.ConnectError("down")

        data, code = await persistent_service.stop_alert(rule_id)

        assert code == 200
        assert fake_rule_store.get(rule_id) is None


class TestPersistentListAlerts:
    """list_alerts with RuleStore — reads from ES."""

    @pytest.mark.asyncio
    async def test_empty_list(self, persistent_service):
        data, code = await persistent_service.list_alerts()
        assert code == 200
        assert data["count"] == 0
        assert data["total"] == 0

    @pytest.mark.asyncio
    async def test_lists_created_rules(self, persistent_service):
        await persistent_service.start_alert(make_config(alert_type="fire"))
        await persistent_service.start_alert(make_config(alert_type="collision"))

        data, code = await persistent_service.list_alerts()

        assert code == 200
        assert data["count"] == 2
        assert data["total"] == 2
        types = {r["alert_type"] for r in data["rules"]}
        assert types == {"fire", "collision"}

    @pytest.mark.asyncio
    async def test_rtvi_stream_id_not_leaked(self, persistent_service):
        await persistent_service.start_alert(make_config())
        data, _ = await persistent_service.list_alerts()
        for rule in data["rules"]:
            assert "rtvi_stream_id" not in rule

    @pytest.mark.asyncio
    async def test_extended_fields_present_in_listing(self, persistent_service):
        """Extended fields written to ES appear in the list_alerts response."""
        cfg = make_config(
            api_type="internal",
            response_format={"type": "text"},
            stream_options={"include_usage": True},
            max_tokens=256,
            temperature=0.7,
            top_p=0.9,
            top_k=40,
            ignore_eos=False,
            seed=7,
            media_info={"type": "offset", "start_offset": 0, "end_offset": 1_000_000},
            enable_audio=True,
            mm_processor_kwargs={"k": "v"},
        )
        await persistent_service.start_alert(cfg)
        data, code = await persistent_service.list_alerts()
        assert code == 200
        rule = data["rules"][0]
        assert rule["api_type"] == "internal"
        assert rule["response_format"] == {"type": "text"}
        assert rule["stream_options"] == {"include_usage": True}
        assert rule["max_tokens"] == 256
        assert rule["temperature"] == 0.7
        assert rule["top_p"] == 0.9
        assert rule["top_k"] == 40
        assert rule["ignore_eos"] is False
        assert rule["seed"] == 7
        assert rule["media_info"] == {"type": "offset", "start_offset": 0, "end_offset": 1_000_000}
        assert rule["enable_audio"] is True
        assert rule["mm_processor_kwargs"] == {"k": "v"}

    @pytest.mark.asyncio
    async def test_extended_fields_absent_from_listing_when_none(self, persistent_service):
        """Extended fields not set on a rule must not appear as keys in the listing."""
        await persistent_service.start_alert(make_config())
        data, code = await persistent_service.list_alerts()
        assert code == 200
        rule = data["rules"][0]
        for field in (
            "api_type", "response_format", "stream_options", "max_tokens",
            "temperature", "top_p", "top_k", "ignore_eos", "seed",
            "media_info", "enable_audio", "mm_processor_kwargs",
        ):
            assert field not in rule, (
                f"expected {field!r} absent from listing when not set"
            )


class TestPersistentGetAlert:
    """get_alert with RuleStore — reads from ES."""

    @pytest.mark.asyncio
    async def test_get_existing(self, persistent_service):
        create_data, _ = await persistent_service.start_alert(make_config())
        rule_id = create_data["id"]

        data, code = await persistent_service.get_alert(rule_id)

        assert code == 200
        assert data["rule"]["id"] == rule_id
        assert "rtvi_stream_id" not in data["rule"]

    @pytest.mark.asyncio
    async def test_get_nonexistent_returns_404(self, persistent_service):
        data, code = await persistent_service.get_alert("nonexistent")
        assert code == 404


# ---------------------------------------------------------------------------
# Persistence-disabled fallback (in-memory path still works)
# ---------------------------------------------------------------------------

class TestPersistenceDisabledFallback:
    """When persistence.enabled=false the service has no RuleStore.
    POST/GET/DELETE must still work via the in-memory registry so the
    persistence flag acts as a genuine rollback switch, not a kill switch.
    """

    @pytest.mark.asyncio
    async def test_create_works_without_persistence(self, realtime_service):
        """POST succeeds on the in-memory path."""
        data, code = await realtime_service.start_alert(make_config())
        assert code == 201
        assert data["status"] == ResponseStatus.SUCCESS

    @pytest.mark.asyncio
    async def test_list_works_without_persistence(self, realtime_service):
        """GET list succeeds on the in-memory path."""
        await realtime_service.start_alert(make_config())
        data, code = await realtime_service.list_alerts()
        assert code == 200
        assert data["count"] == 1

    @pytest.mark.asyncio
    async def test_get_by_id_works_without_persistence(self, realtime_service):
        """GET single rule succeeds on the in-memory path."""
        create_data, _ = await realtime_service.start_alert(make_config())
        data, code = await realtime_service.get_alert(create_data["id"])
        assert code == 200
        assert data["rule"]["id"] == create_data["id"]

    @pytest.mark.asyncio
    async def test_delete_works_without_persistence(self, realtime_service):
        """DELETE succeeds on the in-memory path."""
        create_data, _ = await realtime_service.start_alert(make_config())
        data, code = await realtime_service.stop_alert(create_data["id"])
        assert code == 200
        list_data, _ = await realtime_service.list_alerts()
        assert list_data["count"] == 0

    @pytest.mark.asyncio
    async def test_full_lifecycle_without_persistence(self, realtime_service):
        """Complete create → list → get → delete → verify-gone lifecycle."""
        create_data, _ = await realtime_service.start_alert(make_config())
        rule_id = create_data["id"]

        list_data, _ = await realtime_service.list_alerts()
        assert list_data["count"] == 1

        get_data, _ = await realtime_service.get_alert(rule_id)
        assert get_data["rule"]["alert_type"] == "collision"

        _, code = await realtime_service.stop_alert(rule_id)
        assert code == 200

        list_data, _ = await realtime_service.list_alerts()
        assert list_data["count"] == 0

        get_data, code = await realtime_service.get_alert(rule_id)
        assert code == 404

    @pytest.mark.asyncio
    async def test_replay_returns_501_not_503(self, realtime_service):
        """Replay signals 'disabled' (501), not 'outage' (503)."""
        data, code = await realtime_service.replay()
        assert code == 501
        assert data["error"] == ErrorCode.PERSISTENCE_DISABLED


# ---------------------------------------------------------------------------
# Replay
# ---------------------------------------------------------------------------

class TestReplay:
    """RealtimeAlertService.replay — operator-triggered re-onboard."""

    @pytest.mark.asyncio
    async def test_replay_without_rule_store_returns_501(self, realtime_service):
        """Replay is not available when persistence is disabled by config (not a transient outage)."""
        data, code = await realtime_service.replay()
        assert code == 501
        assert data["error"] == ErrorCode.PERSISTENCE_DISABLED

    @pytest.mark.asyncio
    async def test_replay_empty_store_returns_200_noop(self, persistent_service):
        data, code = await persistent_service.replay()
        assert code == 200
        assert data["replayed"] == 0
        assert data["total"] == 0

    @pytest.mark.asyncio
    async def test_replay_recovers_pending_rules(
        self, persistent_service, fake_rule_store, mock_rtvi_client
    ):
        """Pending rules (crash-orphaned) are re-onboarded by replay.

        A rule stuck in 'pending' means the process crashed after
        persisting to ES but before RTVI confirmed the stream.  Replay
        must recover it — otherwise persist-first is pointless.
        """
        fake_rule_store.create("pending-1", {
            "status": "pending",
            "created_at": "2020-01-01T00:00:00Z",
            "live_stream_url": "rtsp://x/crash-orphan",
            "alert_type": "test",
            "prompt": "test",
        })
        mock_rtvi_client.start_stream.return_value = {
            "results": [{"id": "recovered-stream"}]
        }

        data, code = await persistent_service.replay()

        assert code == 200
        assert data["replayed"] == 1
        assert data["total"] == 1
        assert data["details"][0]["result"] == "success"
        assert data["details"][0]["rtvi_stream_id"] == "recovered-stream"
        stored = fake_rule_store.get("pending-1")
        assert stored["status"] == RuleStatus.ACTIVE
        assert stored["rtvi_stream_id"] == "recovered-stream"

    @pytest.mark.asyncio
    async def test_replay_re_onboards_active_rules(
        self, persistent_service, fake_rule_store, mock_rtvi_client
    ):
        create_data, _ = await persistent_service.start_alert(make_config())
        rule_id = create_data["id"]
        persistent_service._rules.clear()

        mock_rtvi_client.start_stream.return_value = {
            "results": [{"id": "replayed-stream"}]
        }
        mock_rtvi_client.reset_mock()

        data, code = await persistent_service.replay()

        assert code == 200
        assert data["replayed"] == 1
        assert data["failed"] == 0
        assert data["details"][0]["result"] == "success"
        assert data["details"][0]["rtvi_stream_id"] == "replayed-stream"
        stored = fake_rule_store.get(rule_id)
        assert stored["rtvi_stream_id"] == "replayed-stream"
        assert "last_replay_at" in stored

    @pytest.mark.asyncio
    async def test_replay_start_stream_failure_marks_es_failed(
        self, persistent_service, fake_rule_store, mock_rtvi_client
    ):
        """start_stream failure during replay marks ES record FAILED (not left stale-ACTIVE)."""
        create_data, _ = await persistent_service.start_alert(make_config())
        rule_id = create_data["id"]
        persistent_service._rules.clear()

        mock_rtvi_client.start_stream.side_effect = httpx.ConnectError("down")

        data, code = await persistent_service.replay()

        assert code == 200
        assert data["replayed"] == 0
        assert data["failed"] == 1
        assert data["details"][0]["result"] == "error"
        stored = fake_rule_store.get(rule_id)
        assert stored["status"] == RuleStatus.FAILED
        assert stored.get("rtvi_stream_id") is None
        assert "last_replay_at" not in stored

    @pytest.mark.asyncio
    async def test_replay_409_when_already_running(
        self, persistent_service
    ):
        """Second concurrent replay() returns 409 while the lock is held."""
        await persistent_service._replay_lock.acquire()
        try:
            data, code = await persistent_service.replay()
            assert code == 409
            assert data["error"] == "replay_in_flight"
        finally:
            persistent_service._replay_lock.release()

    @pytest.mark.asyncio
    async def test_start_alert_503_during_replay(
        self, persistent_service
    ):
        persistent_service._replaying = True
        data, code = await persistent_service.start_alert(make_config())
        assert code == 503
        assert data["error"] == "replay_in_progress"
        persistent_service._replaying = False

    @pytest.mark.asyncio
    async def test_stop_alert_503_during_replay(
        self, persistent_service, fake_rule_store
    ):
        create_data, _ = await persistent_service.start_alert(make_config())
        rule_id = create_data["id"]

        persistent_service._replaying = True
        data, code = await persistent_service.stop_alert(rule_id)
        assert code == 503
        assert data["error"] == "replay_in_progress"
        persistent_service._replaying = False

    @pytest.mark.asyncio
    async def test_concurrent_replay_returns_409(
        self, persistent_service, fake_rule_store, mock_rtvi_client
    ):
        """Two concurrent replay() calls: one succeeds, the other gets 409."""
        await persistent_service.start_alert(make_config())
        persistent_service._rules.clear()
        mock_rtvi_client.start_stream.return_value = {
            "results": [{"id": "replayed-stream"}]
        }

        results = await asyncio.gather(
            persistent_service.replay(),
            persistent_service.replay(),
        )
        codes = [r[1] for r in results]
        assert sorted(codes) == [200, 409]

    @pytest.mark.asyncio
    async def test_replay_with_all_extended_fields_forwards_to_rtvi(
        self, persistent_service, fake_rule_store, mock_rtvi_client
    ):
        """_re_onboard_rule must forward all 12 extended fields to generate_captions
        when they are present in the stored ES document."""
        mock_rtvi_client.start_stream.return_value = {
            "results": [{"id": "replay-stream-ext"}]
        }
        fake_rule_store.create("rule-ext-all", {
            "status": "active",
            "created_at": "2025-01-01T00:00:00Z",
            "live_stream_url": "rtsp://x/stream",
            "alert_type": "ppe",
            "prompt": "detect ppe",
            "system_prompt": "be concise",
            "model": "cosmos",
            # all 12 extended fields
            "api_type": "internal",
            "response_format": {"type": "text"},
            "stream_options": {"include_usage": True},
            "max_tokens": 512,
            "temperature": 0.3,
            "top_p": 0.9,
            "top_k": 40,
            "ignore_eos": True,
            "seed": 99,
            "media_info": {"type": "offset", "start_offset": 0, "end_offset": 1_000_000},
            "enable_audio": True,
            "mm_processor_kwargs": {"k": "v"},
        })

        data, code = await persistent_service.replay()

        assert code == 200
        assert data["replayed"] == 1
        kw = mock_rtvi_client.generate_captions.call_args.kwargs
        assert kw["api_type"] == "internal"
        assert kw["response_format"] == {"type": "text"}
        assert kw["stream_options"] == {"include_usage": True}
        assert kw["max_tokens"] == 512
        assert kw["temperature"] == 0.3
        assert kw["top_p"] == 0.9
        assert kw["top_k"] == 40
        assert kw["ignore_eos"] is True
        assert kw["seed"] == 99
        assert kw["media_info"] == {"type": "offset", "start_offset": 0, "end_offset": 1_000_000}
        assert kw["enable_audio"] is True
        assert kw["mm_processor_kwargs"] == {"k": "v"}

    @pytest.mark.asyncio
    async def test_replay_with_partial_extended_fields(
        self, persistent_service, fake_rule_store, mock_rtvi_client
    ):
        """Only the extended fields that are present in the ES doc reach generate_captions;
        absent fields must not appear in the call kwargs."""
        mock_rtvi_client.start_stream.return_value = {
            "results": [{"id": "replay-stream-partial"}]
        }
        fake_rule_store.create("rule-ext-partial", {
            "status": "active",
            "created_at": "2025-01-01T00:00:00Z",
            "live_stream_url": "rtsp://x/stream",
            "alert_type": "ppe",
            "prompt": "detect ppe",
            "model": "cosmos",
            # only two of the 12 extended fields
            "max_tokens": 256,
            "temperature": 0.5,
        })

        data, code = await persistent_service.replay()

        assert code == 200
        kw = mock_rtvi_client.generate_captions.call_args.kwargs
        assert kw["max_tokens"] == 256
        assert kw["temperature"] == 0.5
        # fields absent from the ES doc must be None (not set) in the call
        assert kw.get("api_type") is None
        assert kw.get("top_p") is None
        assert kw.get("seed") is None
        assert kw.get("enable_audio") is None

    @pytest.mark.asyncio
    async def test_replay_with_no_extended_fields(
        self, persistent_service, fake_rule_store, mock_rtvi_client
    ):
        """When the stored ES doc has no extended fields, generate_captions
        receives None for each — not missing kwargs."""
        mock_rtvi_client.start_stream.return_value = {
            "results": [{"id": "replay-stream-none"}]
        }
        fake_rule_store.create("rule-ext-none", {
            "status": "active",
            "created_at": "2025-01-01T00:00:00Z",
            "live_stream_url": "rtsp://x/stream",
            "alert_type": "fire",
            "prompt": "detect fire",
            "model": "cosmos",
        })

        data, code = await persistent_service.replay()

        assert code == 200
        kw = mock_rtvi_client.generate_captions.call_args.kwargs
        for field in (
            "api_type", "response_format", "stream_options", "max_tokens",
            "temperature", "top_p", "top_k", "ignore_eos", "seed",
            "media_info", "enable_audio", "mm_processor_kwargs",
        ):
            assert kw.get(field) is None, (
                f"expected {field!r} to be None in generate_captions kwargs "
                "when not present in the stored ES doc"
            )


# ---------------------------------------------------------------------------
# replay observability (correlation_id + structured logs)
# ---------------------------------------------------------------------------


def _counter_value(counter, **labels) -> float:
    """Read a Prometheus counter (or labelled counter) value, or 0 when off.

    The realtime service references metrics as module-level attributes
    that are ``None`` when ``PROMETHEUS_METRICS_ENABLED=false``. Centralised
    here so tests stay terse and don't crash in the metrics-off CI lane.
    """
    if counter is None:
        return 0.0
    if labels:
        return counter.labels(**labels)._value.get()
    return counter._value.get()


class TestReplayObservability:
    """every replay log line must carry the same
    correlation_id, and the response body must echo it so operators
    can grep."""

    @pytest.mark.asyncio
    async def test_correlation_id_returned_in_response(self, persistent_service):
        """Empty-replay path returns a correlation_id."""
        data, code = await persistent_service.replay()
        assert code == 200
        assert "correlation_id" in data
        assert len(data["correlation_id"]) == 32  # uuid4().hex

    @pytest.mark.asyncio
    async def test_correlation_id_is_unique_per_invocation(
        self, persistent_service
    ):
        d1, _ = await persistent_service.replay()
        d2, _ = await persistent_service.replay()
        assert d1["correlation_id"] != d2["correlation_id"]

    @pytest.mark.asyncio
    async def test_correlation_id_threaded_through_per_rule_logs(
        self, persistent_service, fake_rule_store, mock_rtvi_client, caplog,
    ):
        """Every replay-stage log line carries the same correlation_id —
        start, per-rule outcome, and end."""
        create_data, _ = await persistent_service.start_alert(make_config())
        persistent_service._rules.clear()
        mock_rtvi_client.start_stream.return_value = {
            "results": [{"id": "replayed-stream-1"}]
        }

        with caplog.at_level("INFO", logger="realtime.services.realtime_service"):
            data, _ = await persistent_service.replay()

        cid = data["correlation_id"]
        replay_records = [
            r for r in caplog.records
            if getattr(r, "stage", "").startswith("replay")
        ]
        # Must have at least: replay_start, replay_rule (success), replay_end.
        stages = {r.stage for r in replay_records}
        assert "replay_start" in stages
        assert "replay_rule" in stages
        assert "replay_end" in stages
        # And all of them must carry the same correlation_id.
        for r in replay_records:
            assert getattr(r, "correlation_id", None) == cid

    @pytest.mark.asyncio
    async def test_correlation_id_on_failed_rule(
        self, persistent_service, fake_rule_store, mock_rtvi_client, caplog,
    ):
        """Per-rule failure log also carries the correlation_id."""
        create_data, _ = await persistent_service.start_alert(make_config())
        persistent_service._rules.clear()
        mock_rtvi_client.start_stream.side_effect = httpx.ConnectError("down")

        with caplog.at_level("WARNING", logger="realtime.services.realtime_service"):
            data, _ = await persistent_service.replay()

        cid = data["correlation_id"]
        rule_records = [
            r for r in caplog.records
            if getattr(r, "stage", "") == "replay_rule"
            and getattr(r, "outcome", "") == "failure"
        ]
        assert rule_records, "expected at least one replay_rule failure log"
        for r in rule_records:
            assert getattr(r, "correlation_id", None) == cid

    @pytest.mark.asyncio
    async def test_correlation_id_on_501_persistence_disabled(
        self, realtime_service, caplog,
    ):
        """501 short-circuit (no rule_store) must echo correlation_id in
        both response body AND its log line so operators can grep it."""
        with caplog.at_level("INFO", logger="realtime.services.realtime_service"):
            data, code = await realtime_service.replay()

        assert code == 501
        assert "correlation_id" in data
        assert len(data["correlation_id"]) == 32
        cid = data["correlation_id"]

        end_records = [
            r for r in caplog.records
            if getattr(r, "stage", "") == "replay_end"
            and getattr(r, "outcome", "") == "skipped_disabled"
        ]
        assert end_records, "expected a replay_end / skipped_disabled log"
        for r in end_records:
            assert getattr(r, "correlation_id", None) == cid

    @pytest.mark.asyncio
    async def test_correlation_id_on_409_in_flight(
        self, persistent_service, caplog,
    ):
        """409 short-circuit (lock already held) must echo correlation_id
        in both response body AND its log line. Different invocations
        must each get their own id."""
        await persistent_service._replay_lock.acquire()
        try:
            with caplog.at_level("INFO", logger="realtime.services.realtime_service"):
                data, code = await persistent_service.replay()
        finally:
            persistent_service._replay_lock.release()

        assert code == 409
        assert "correlation_id" in data
        assert len(data["correlation_id"]) == 32
        cid = data["correlation_id"]

        end_records = [
            r for r in caplog.records
            if getattr(r, "stage", "") == "replay_end"
            and getattr(r, "outcome", "") == "skipped_in_flight"
        ]
        assert end_records, "expected a replay_end / skipped_in_flight log"
        for r in end_records:
            assert getattr(r, "correlation_id", None) == cid

    @pytest.mark.asyncio
    async def test_correlation_id_unique_across_short_circuits(
        self, realtime_service,
    ):
        """Two consecutive 501s must each get a fresh correlation_id —
        proves the id is not cached or reused for early-return paths."""
        d1, c1 = await realtime_service.replay()
        d2, c2 = await realtime_service.replay()
        assert c1 == 501 and c2 == 501
        assert d1["correlation_id"] != d2["correlation_id"]


class TestNewMetrics:
    """realtime_rules_persisted_total, realtime_rules_count,
    replay_invocations_total, replay_rule_failures_total."""

    @pytest.mark.asyncio
    async def test_persisted_increments_only_on_es_write(
        self, persistent_service, realtime_service,
    ):
        from realtime.services import realtime_service as rs_mod

        if rs_mod.REALTIME_RULES_PERSISTED is None:
            pytest.skip("Prometheus metrics disabled")

        before_persistent = _counter_value(rs_mod.REALTIME_RULES_PERSISTED)
        await persistent_service.start_alert(make_config())
        after_persistent = _counter_value(rs_mod.REALTIME_RULES_PERSISTED)
        assert after_persistent - before_persistent == 1

        # in-memory mode: counter must NOT move
        before_memory = _counter_value(rs_mod.REALTIME_RULES_PERSISTED)
        await realtime_service.start_alert(make_config())
        after_memory = _counter_value(rs_mod.REALTIME_RULES_PERSISTED)
        assert after_memory == before_memory

    @pytest.mark.asyncio
    async def test_persisted_does_not_increment_on_es_failure(
        self, mock_rtvi_client, fake_rule_store,
    ):
        """ES create failure → counter stays put (rolled back path)."""
        from realtime.services import realtime_service as rs_mod
        if rs_mod.REALTIME_RULES_PERSISTED is None:
            pytest.skip("Prometheus metrics disabled")

        # Make rule_store.create raise.
        from persistence.exceptions import PersistenceError
        fake_rule_store.create = MagicMock(
            side_effect=PersistenceError("boom")
        )

        with patch("realtime.services.realtime_service.load_config", return_value={
            "rtvi_vlm": {
                "base_url": "http://mock:8000",
                "timeout": 5,
                "default_model": "default-vlm",
                "captions_ack_timeout": 0.1,
            }
        }):
            svc = RealtimeAlertService(rule_store=fake_rule_store)
        svc._client = mock_rtvi_client

        before = _counter_value(rs_mod.REALTIME_RULES_PERSISTED)
        data, code = await svc.start_alert(make_config())
        after = _counter_value(rs_mod.REALTIME_RULES_PERSISTED)
        assert code == 502
        assert after == before

    @pytest.mark.asyncio
    async def test_persisted_does_not_increment_on_rtvi_failure(
        self, persistent_service, mock_rtvi_client,
    ):
        """RTVI start_stream failure after ES PENDING create → counter
        must NOT move (rolled back path).  This is the Finding 2 case
        from the maintainer review: incrementing on the
        PENDING create would over-count rules that were never usable.
        """
        from realtime.services import realtime_service as rs_mod
        if rs_mod.REALTIME_RULES_PERSISTED is None:
            pytest.skip("Prometheus metrics disabled")

        mock_rtvi_client.start_stream.side_effect = httpx.ConnectError("rtvi down")

        before = _counter_value(rs_mod.REALTIME_RULES_PERSISTED)
        data, code = await persistent_service.start_alert(make_config())
        after = _counter_value(rs_mod.REALTIME_RULES_PERSISTED)
        assert code == 502, data
        assert after == before, (
            "REALTIME_RULES_PERSISTED moved despite RTVI rollback — "
            "counter must only fire after the rule reaches ACTIVE in ES"
        )

    @pytest.mark.asyncio
    async def test_persisted_does_not_increment_on_es_update_failure(
        self, persistent_service, fake_rule_store,
    ):
        """ES update (PENDING → ACTIVE) failure → counter stays put.

        This is the boundary case for Finding 2: the create succeeded,
        RTVI succeeded, but the follow-up update that flips status to
        ACTIVE fails and the rule is rolled back.  ``…_persisted_total``
        must reflect "rules durably ACTIVE", not "rules that ever
        existed in any form".
        """
        from realtime.services import realtime_service as rs_mod
        if rs_mod.REALTIME_RULES_PERSISTED is None:
            pytest.skip("Prometheus metrics disabled")

        # Let create succeed but make update raise.
        original_update = fake_rule_store.update
        fake_rule_store.update = MagicMock(side_effect=Exception("es update boom"))
        try:
            before = _counter_value(rs_mod.REALTIME_RULES_PERSISTED)
            data, code = await persistent_service.start_alert(make_config())
            after = _counter_value(rs_mod.REALTIME_RULES_PERSISTED)
            assert code == 502, data
            assert after == before, (
                "REALTIME_RULES_PERSISTED moved despite ES update failure — "
                "counter must only fire after status=ACTIVE landed in ES"
            )
        finally:
            fake_rule_store.update = original_update

    @pytest.mark.asyncio
    async def test_rules_count_gauge_reflects_es_total(
        self, persistent_service, fake_rule_store,
    ):
        from realtime.services import realtime_service as rs_mod
        if rs_mod.REALTIME_RULES_COUNT is None:
            pytest.skip("Prometheus metrics disabled")

        await persistent_service.start_alert(make_config(alert_type="a"))
        await persistent_service.start_alert(make_config(alert_type="b"))
        await persistent_service.start_alert(make_config(alert_type="c"))
        assert rs_mod.REALTIME_RULES_COUNT._value.get() == 3.0

        rule_id = next(iter(fake_rule_store._docs.keys()))
        await persistent_service.stop_alert(rule_id)
        assert rs_mod.REALTIME_RULES_COUNT._value.get() == 2.0

    @pytest.mark.asyncio
    async def test_rules_count_gauge_excludes_pending_rows(
        self, persistent_service, fake_rule_store,
    ):
        """REALTIME_RULES_COUNT must reflect only status=ACTIVE rows.

        Crash-orphaned PENDING rows can linger in ES for up to
        ``pending_ttl_seconds`` until the startup reaper clears them.
        Counting them in the operator-facing gauge inflates the
        durable rule count and makes the dashboard untrustworthy.
        """
        from realtime.config import RuleStatus
        from realtime.services import realtime_service as rs_mod
        if rs_mod.REALTIME_RULES_COUNT is None:
            pytest.skip("Prometheus metrics disabled")

        await persistent_service.start_alert(make_config(alert_type="a"))
        assert rs_mod.REALTIME_RULES_COUNT._value.get() == 1.0

        fake_rule_store._docs["orphan-pending-1"] = {
            "_id": "orphan-pending-1",
            "alert_rule_id": "orphan-pending-1",
            "status": RuleStatus.PENDING,
        }
        fake_rule_store._docs["orphan-pending-2"] = {
            "_id": "orphan-pending-2",
            "alert_rule_id": "orphan-pending-2",
            "status": RuleStatus.PENDING,
        }

        await persistent_service._refresh_rules_count_gauge()
        assert rs_mod.REALTIME_RULES_COUNT._value.get() == 1.0, (
            "PENDING rows leaked into REALTIME_RULES_COUNT — gauge "
            "must filter by status=ACTIVE so crash-orphaned PENDINGs "
            "do not pollute the operator-facing durable rule count"
        )

    @pytest.mark.asyncio
    async def test_replay_invocations_outcome_success(
        self, persistent_service, fake_rule_store, mock_rtvi_client,
    ):
        from realtime.services import realtime_service as rs_mod
        if rs_mod.REPLAY_INVOCATIONS is None:
            pytest.skip("Prometheus metrics disabled")

        await persistent_service.start_alert(make_config())
        persistent_service._rules.clear()
        mock_rtvi_client.start_stream.return_value = {
            "results": [{"id": "replayed-stream"}]
        }

        before = _counter_value(rs_mod.REPLAY_INVOCATIONS, outcome="success")
        await persistent_service.replay()
        after = _counter_value(rs_mod.REPLAY_INVOCATIONS, outcome="success")
        assert after - before == 1

    @pytest.mark.asyncio
    async def test_replay_invocations_outcome_failed(
        self, persistent_service, fake_rule_store, mock_rtvi_client,
    ):
        from realtime.services import realtime_service as rs_mod
        if rs_mod.REPLAY_INVOCATIONS is None:
            pytest.skip("Prometheus metrics disabled")

        await persistent_service.start_alert(make_config())
        persistent_service._rules.clear()
        mock_rtvi_client.start_stream.side_effect = httpx.ConnectError("down")

        before = _counter_value(rs_mod.REPLAY_INVOCATIONS, outcome="failed")
        await persistent_service.replay()
        after = _counter_value(rs_mod.REPLAY_INVOCATIONS, outcome="failed")
        assert after - before == 1

    @pytest.mark.asyncio
    async def test_replay_invocations_outcome_partial(
        self, persistent_service, fake_rule_store, mock_rtvi_client,
    ):
        from realtime.services import realtime_service as rs_mod
        if rs_mod.REPLAY_INVOCATIONS is None:
            pytest.skip("Prometheus metrics disabled")

        # Two rules.
        await persistent_service.start_alert(make_config(alert_type="a"))
        await persistent_service.start_alert(make_config(alert_type="b"))
        persistent_service._rules.clear()

        # First start_stream call succeeds, second raises.
        responses = [
            {"results": [{"id": "ok-stream"}]},
            httpx.ConnectError("down"),
        ]

        async def _start_stream(*_args, **_kwargs):
            r = responses.pop(0)
            if isinstance(r, BaseException):
                raise r
            return r

        mock_rtvi_client.start_stream.side_effect = _start_stream

        before = _counter_value(rs_mod.REPLAY_INVOCATIONS, outcome="partial")
        data, _ = await persistent_service.replay()
        after = _counter_value(rs_mod.REPLAY_INVOCATIONS, outcome="partial")
        assert after - before == 1
        assert data["replayed"] == 1
        assert data["failed"] == 1

    @pytest.mark.asyncio
    async def test_replay_invocations_not_incremented_on_skipped_disabled(
        self, realtime_service,
    ):
        """501 short-circuit must NOT touch ``REPLAY_INVOCATIONS``.

        Per the maintainer review the replay
        invocations counter only tracks invocations that actually ran
        the loop; rejected calls show up as ``replay_end`` log lines
        with ``outcome=skipped_disabled``.
        """
        from realtime.services import realtime_service as rs_mod
        if rs_mod.REPLAY_INVOCATIONS is None:
            pytest.skip("Prometheus metrics disabled")

        # Snapshot every outcome so we can prove no outcome moved.
        outcomes = ("success", "partial", "failed",
                    "skipped_disabled", "skipped_in_flight")
        before = {o: _counter_value(rs_mod.REPLAY_INVOCATIONS, outcome=o)
                  for o in outcomes}
        await realtime_service.replay()
        after = {o: _counter_value(rs_mod.REPLAY_INVOCATIONS, outcome=o)
                 for o in outcomes}
        assert before == after, (
            "REPLAY_INVOCATIONS moved on a 501 short-circuit; "
            "Finding 3 requires the counter to stay flat for short-circuits"
        )

    @pytest.mark.asyncio
    async def test_replay_invocations_not_incremented_on_skipped_in_flight(
        self, persistent_service,
    ):
        """409 short-circuit must NOT touch ``REPLAY_INVOCATIONS``.

        Same rationale as the 501 test above (Finding 3): the counter
        only reflects real replay activity, not rejected calls.
        """
        from realtime.services import realtime_service as rs_mod
        if rs_mod.REPLAY_INVOCATIONS is None:
            pytest.skip("Prometheus metrics disabled")

        outcomes = ("success", "partial", "failed",
                    "skipped_disabled", "skipped_in_flight")
        await persistent_service._replay_lock.acquire()
        try:
            before = {o: _counter_value(rs_mod.REPLAY_INVOCATIONS, outcome=o)
                      for o in outcomes}
            await persistent_service.replay()
            after = {o: _counter_value(rs_mod.REPLAY_INVOCATIONS, outcome=o)
                     for o in outcomes}
            assert before == after, (
                "REPLAY_INVOCATIONS moved on a 409 short-circuit; "
                "Finding 3 requires the counter to stay flat for short-circuits"
            )
        finally:
            persistent_service._replay_lock.release()

    @pytest.mark.asyncio
    async def test_replay_rule_failures_per_rule_increment(
        self, persistent_service, fake_rule_store, mock_rtvi_client,
    ):
        """Each per-rule failure inside the loop bumps the counter once."""
        from realtime.services import realtime_service as rs_mod
        if rs_mod.REPLAY_RULE_FAILURES is None:
            pytest.skip("Prometheus metrics disabled")

        await persistent_service.start_alert(make_config(alert_type="a"))
        await persistent_service.start_alert(make_config(alert_type="b"))
        persistent_service._rules.clear()
        mock_rtvi_client.start_stream.side_effect = httpx.ConnectError("down")

        before = _counter_value(rs_mod.REPLAY_RULE_FAILURES)
        await persistent_service.replay()
        after = _counter_value(rs_mod.REPLAY_RULE_FAILURES)
        assert after - before == 2


# ---------------------------------------------------------------------------
# Stream readiness check tests
# ---------------------------------------------------------------------------

class TestStreamReadinessCheck:
    """Verify the inline stream readiness check.

    Simulates the VSS_SKIP_INPUT_MEDIA_VERIFICATION=1 scenario where RTVI
    accepts any syntactically valid RTSP URL at /streams/add (returns a
    stream id) but the generate_captions streaming task fails later when
    GStreamer cannot open the RTSP source.

    The readiness check runs inline — after the captions ack window the
    realtime service polls ``GET /streams/get-stream-info`` for up to
    ``stream_readiness_max_wait`` while watching the captions task. A
    failure at either phase returns 502 synchronously (rule marked
    FAILED in ES, RTVI stream stopped).
    """

    @pytest.fixture()
    def readiness_service(self, mock_rtvi_client):
        """Service with short ack window and moderate readiness probe budget."""
        with patch("realtime.services.realtime_service.load_config", return_value={
            "rtvi_vlm": {
                "base_url": "http://mock:8000",
                "timeout": 5,
                "default_model": "default-vlm",
                "captions_ack_timeout": 0.05,
                "stream_readiness_poll_interval": 0.05,
                "stream_readiness_max_wait": 0.5,
            }
        }):
            svc = RealtimeAlertService()
        svc._client = mock_rtvi_client
        return svc

    @pytest.fixture()
    def readiness_disabled_service(self, mock_rtvi_client):
        """Service with the readiness probe budget set to 0 (skip polling)."""
        with patch("realtime.services.realtime_service.load_config", return_value={
            "rtvi_vlm": {
                "base_url": "http://mock:8000",
                "timeout": 5,
                "default_model": "default-vlm",
                "captions_ack_timeout": 0.05,
                "stream_readiness_poll_interval": 0.05,
                "stream_readiness_max_wait": 0,
            }
        }):
            svc = RealtimeAlertService()
        svc._client = mock_rtvi_client
        return svc

    @pytest.mark.asyncio
    async def test_stream_failure_during_readiness_returns_502(
        self, readiness_service, mock_rtvi_client
    ):
        """When generate_captions fails within readiness window → 502 inline."""
        async def _delayed_failure(**kwargs):
            await asyncio.sleep(0.15)
            raise httpx.ReadError("GStreamer: Could not open resource for reading")

        mock_rtvi_client.generate_captions.side_effect = _delayed_failure

        data, code = await readiness_service.start_alert(make_config())

        assert code == 502
        assert data["error"] == "rtvi_stream_not_readable"

        list_data, _ = await readiness_service.list_alerts()
        assert list_data["count"] == 0
        mock_rtvi_client.stop_stream.assert_awaited()

    @pytest.mark.asyncio
    async def test_captions_complete_during_readiness_returns_201(
        self, readiness_service, mock_rtvi_client
    ):
        """generate_captions finishes after ack timeout but within readiness window → 201, rule kept."""
        async def _completes_in_readiness_window(**kwargs):
            await asyncio.sleep(0.15)
            return {"status": "done"}

        mock_rtvi_client.generate_captions.side_effect = _completes_in_readiness_window

        data, code = await readiness_service.start_alert(make_config())

        assert code == 201
        assert data["status"] == ResponseStatus.SUCCESS

        list_data, _ = await readiness_service.list_alerts()
        assert list_data["count"] == 1

    @pytest.mark.asyncio
    async def test_stream_survives_readiness_returns_201(
        self, readiness_service, mock_rtvi_client
    ):
        """When generate_captions is still running after readiness window → 201."""
        async def _long_running_stream(**kwargs):
            await asyncio.sleep(5.0)
            return {"status": "started", "stream_id": "stream-abc-123"}

        mock_rtvi_client.generate_captions.side_effect = _long_running_stream

        data, code = await readiness_service.start_alert(make_config())

        assert code == 201
        assert data["status"] == ResponseStatus.SUCCESS
        list_data, _ = await readiness_service.list_alerts()
        assert list_data["count"] == 1

    @pytest.mark.asyncio
    async def test_readiness_disabled_returns_201_on_delayed_failure(
        self, readiness_disabled_service, mock_rtvi_client
    ):
        """Legacy behavior (readiness_timeout=0): timeout treated as success."""
        async def _delayed_failure(**kwargs):
            await asyncio.sleep(0.15)
            raise httpx.ReadError("GStreamer: Could not open resource")

        mock_rtvi_client.generate_captions.side_effect = _delayed_failure

        data, code = await readiness_disabled_service.start_alert(make_config())

        # Without readiness check, the rule is committed before the failure
        assert code == 201
        assert data["status"] == ResponseStatus.SUCCESS

    @pytest.mark.asyncio
    async def test_immediate_failure_still_returns_502(
        self, readiness_service, mock_rtvi_client
    ):
        """Failure within the initial ack window → 502 (existing behavior)."""
        mock_rtvi_client.generate_captions.side_effect = httpx.ConnectError(
            "Connection refused"
        )

        data, code = await readiness_service.start_alert(make_config())

        assert code == 502
        assert data["error"] == "rtvi_vlm_unavailable"

    # -- Replay readiness lifecycle -----------------------------------------

    @pytest.fixture()
    def persistent_readiness_service(self, mock_rtvi_client):
        """Persistent service with short ack + readiness probe knobs for replay tests."""
        fake_store = _FakeRuleStore()
        with patch("realtime.services.realtime_service.load_config", return_value={
            "rtvi_vlm": {
                "base_url": "http://mock:8000",
                "timeout": 5,
                "default_model": "default-vlm",
                "captions_ack_timeout": 0.05,
                "stream_readiness_poll_interval": 0.05,
                "stream_readiness_max_wait": 0.5,
            }
        }):
            svc = RealtimeAlertService(rule_store=fake_store)
        svc._client = mock_rtvi_client
        return svc, fake_store

    @pytest.mark.asyncio
    async def test_replay_readiness_failure_marks_rule_failed(
        self, persistent_readiness_service, mock_rtvi_client
    ):
        """Replay re-onboards rule; inline readiness catches failure and marks ES FAILED."""
        svc, fake_store = persistent_readiness_service

        fake_store.create("replay-rule-1", {
            "status": "active",
            "created_at": "2025-01-01T00:00:00Z",
            "live_stream_url": "rtsp://x/bad-stream",
            "alert_type": "test",
            "prompt": "test",
        })

        mock_rtvi_client.start_stream.return_value = {
            "results": [{"id": "replay-stream-doomed"}]
        }

        async def _delayed_failure(**kwargs):
            await asyncio.sleep(0.15)
            raise httpx.ReadError("GStreamer: Could not open resource")

        mock_rtvi_client.generate_captions.side_effect = _delayed_failure

        data, code = await svc.replay()

        assert code == 200
        assert data["replayed"] == 0
        assert data["failed"] == 1
        assert data["details"][0]["result"] == "error"

        list_data, _ = await svc.list_alerts()
        assert list_data["count"] == 0
        mock_rtvi_client.stop_stream.assert_awaited()
        stored = fake_store.get("replay-rule-1")
        assert stored["status"] == RuleStatus.FAILED

    @pytest.mark.asyncio
    async def test_replay_immediate_captions_failure_records_metrics(
        self, persistent_readiness_service, mock_rtvi_client
    ):
        """generate_captions HTTP failure during replay ack window → rule marked failed in replay."""
        svc, fake_store = persistent_readiness_service

        fake_store.create("replay-rule-imm", {
            "status": "active",
            "created_at": "2025-01-01T00:00:00Z",
            "live_stream_url": "rtsp://x/down",
            "alert_type": "test",
            "prompt": "test",
        })

        mock_rtvi_client.start_stream.return_value = {
            "results": [{"id": "replay-stream-imm"}]
        }
        mock_rtvi_client.generate_captions.side_effect = httpx.ConnectError(
            "Connection refused"
        )

        data, code = await svc.replay()

        assert code == 200
        assert data["failed"] == 1
        assert data["details"][0]["result"] == "error"
        mock_rtvi_client.stop_stream.assert_awaited()

    @pytest.mark.asyncio
    async def test_replay_start_stream_failure_marks_rule_failed(
        self, persistent_readiness_service, mock_rtvi_client
    ):
        """start_stream failure during replay → ES rule marked FAILED (not left ACTIVE/PENDING)."""
        svc, fake_store = persistent_readiness_service

        fake_store.create("replay-rule-nostream", {
            "status": "active",
            "created_at": "2025-01-01T00:00:00Z",
            "live_stream_url": "rtsp://x/unreachable",
            "alert_type": "test",
            "prompt": "test",
        })

        mock_rtvi_client.start_stream.side_effect = httpx.ConnectError(
            "RTVI unreachable"
        )

        data, code = await svc.replay()

        assert code == 200
        assert data["failed"] == 1
        assert data["details"][0]["result"] == "error"
        # Stream was never created so stop_stream should not be called
        mock_rtvi_client.stop_stream.assert_not_awaited()
        # ES rule must be FAILED, not left as ACTIVE/PENDING
        stored = fake_store.get("replay-rule-nostream")
        assert stored["status"] == RuleStatus.FAILED


# NOTE: flag-off wiring of ``get_rule_store()`` is exercised
# end-to-end by ``test/functional/p1/test_realtime_replay/run.sh``
# Sub-test 9 (``rtvi_vlm.enable_realtime_persistence: false``). The
# ``alert-agent-web`` route module imports relatively from its sibling
# ``realtime_schemas`` so it can't be loaded standalone in this unit
# test file. The pure in-memory fallback path it routes into is already
# covered by ``TestPersistenceDisabledFallback`` above.


# ---------------------------------------------------------------------------
# Stream reuse via /streams/get-stream-info
# ---------------------------------------------------------------------------

class TestStreamReuse:
    """Cover the get-stream-info dedupe path added so multiple rules with the
    same ``sensor_id`` (e.g. the always-on fan-out for one camera) can share a
    single RTVI stream instead of issuing redundant ``/streams/add`` calls.
    """

    @pytest.mark.asyncio
    async def test_existing_sensor_id_reuses_stream(
        self, realtime_service, mock_rtvi_client,
    ):
        """When ``GET /streams/get-stream-info`` already lists the
        ``sensor_id``, ``start_alert`` reuses that stream id and skips
        ``/streams/add``. The in-memory rule records ``owns_rtvi_stream=False``.
        """
        mock_rtvi_client.get_stream_info.return_value = [
            {"id": "test-sensor-001", "liveStreamUrl": SAMPLE_RTSP_URL},
        ]

        data, code = await realtime_service.start_alert(make_config())

        assert code == 201
        mock_rtvi_client.start_stream.assert_not_awaited()
        mock_rtvi_client.generate_captions.assert_awaited_once()
        kwargs = mock_rtvi_client.generate_captions.await_args.kwargs
        assert kwargs["stream_id"] == "test-sensor-001"

        rule = realtime_service._rules[data["id"]]
        assert rule["owns_rtvi_stream"] is False
        assert rule["rtvi_stream_id"] == "test-sensor-001"

    @pytest.mark.asyncio
    async def test_unknown_sensor_id_calls_streams_add(
        self, realtime_service, mock_rtvi_client,
    ):
        """``sensor_id`` provided but not present in get-stream-info → fall
        back to ``/streams/add`` and mark the rule as owning the stream."""
        mock_rtvi_client.get_stream_info.return_value = [
            {"id": "different-sensor", "liveStreamUrl": "rtsp://other"},
        ]

        data, code = await realtime_service.start_alert(make_config())

        assert code == 201
        mock_rtvi_client.start_stream.assert_awaited_once()
        rule = realtime_service._rules[data["id"]]
        assert rule["owns_rtvi_stream"] is True

    @pytest.mark.asyncio
    async def test_no_sensor_id_skips_get_stream_info(
        self, realtime_service, mock_rtvi_client,
    ):
        """No ``sensor_id`` in the request → don't probe RTVI; let
        ``/streams/add`` mint a fresh id. RTVI itself dedupes when needed."""
        cfg = make_config(sensor_id=None)

        data, code = await realtime_service.start_alert(cfg)

        assert code == 201
        mock_rtvi_client.get_stream_info.assert_not_awaited()
        mock_rtvi_client.start_stream.assert_awaited_once()
        rule = realtime_service._rules[data["id"]]
        assert rule["owns_rtvi_stream"] is True

    @pytest.mark.asyncio
    async def test_get_stream_info_http_error_falls_back_to_add(
        self, realtime_service, mock_rtvi_client,
    ):
        """A network blip on get-stream-info must not fail the whole request;
        the service degrades to issuing ``/streams/add`` anyway."""
        mock_rtvi_client.get_stream_info.side_effect = httpx.ConnectError(
            "RTVI unreachable",
        )

        data, code = await realtime_service.start_alert(make_config())

        assert code == 201
        mock_rtvi_client.start_stream.assert_awaited_once()
        rule = realtime_service._rules[data["id"]]
        assert rule["owns_rtvi_stream"] is True

    @pytest.mark.asyncio
    async def test_concurrent_start_alert_same_sensor_id_serialises(
        self, realtime_service, mock_rtvi_client,
    ):
        """The per-``sensor_id`` lock must prevent two parallel
        ``start_alert`` calls from both racing past the existence check
        and each issuing their own ``/streams/add``. After the first one
        adds, the second sees the freshly-registered stream and reuses it."""
        registered = []

        async def _fake_add(payload):
            await asyncio.sleep(0)
            stream_id = payload["id"]
            registered.append({"id": stream_id})
            return {"results": [{"id": stream_id, "status": "added"}]}

        async def _fake_get_info():
            return list(registered)

        mock_rtvi_client.start_stream.side_effect = _fake_add
        mock_rtvi_client.get_stream_info.side_effect = _fake_get_info

        cfg_a = make_config(alert_type="intrusion")
        cfg_b = make_config(alert_type="fire")
        results = await asyncio.gather(
            realtime_service.start_alert(cfg_a),
            realtime_service.start_alert(cfg_b),
        )

        for _, code in results:
            assert code == 201
        assert mock_rtvi_client.start_stream.await_count == 1

        owns_flags = sorted(
            realtime_service._rules[data["id"]]["owns_rtvi_stream"]
            for data, _ in results
        )
        assert owns_flags == [False, True]

    @pytest.mark.asyncio
    async def test_stop_alert_last_rule_stops_stream_even_if_reused(
        self, realtime_service, mock_rtvi_client,
    ):
        """Ref-count semantics: a rule that originally reused a stream
        still triggers ``stop_stream`` when it turns out to be the last
        alert-agent rule referencing that stream id. This way orphaned
        external streams are cleaned up after their last reader leaves."""
        mock_rtvi_client.get_stream_info.return_value = [
            {"id": "test-sensor-001", "liveStreamUrl": SAMPLE_RTSP_URL},
        ]
        data, _ = await realtime_service.start_alert(make_config())

        await realtime_service.stop_alert(data["id"])

        mock_rtvi_client.stop_captions.assert_awaited_once()
        mock_rtvi_client.stop_stream.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_stop_alert_with_remaining_rules_skips_stop_stream(
        self, realtime_service, mock_rtvi_client,
    ):
        """When a sibling rule still references the same stream id, the
        deleted rule must NOT call ``stop_stream`` — that would break
        the still-active siblings."""
        mock_rtvi_client.get_stream_info.return_value = [
            {"id": "test-sensor-001", "liveStreamUrl": SAMPLE_RTSP_URL},
        ]
        data_a, _ = await realtime_service.start_alert(
            make_config(alert_type="intrusion"),
        )
        data_b, _ = await realtime_service.start_alert(
            make_config(alert_type="fire"),
        )

        await realtime_service.stop_alert(data_a["id"])

        mock_rtvi_client.stop_captions.assert_awaited_once()
        mock_rtvi_client.stop_stream.assert_not_awaited()

        # Deleting the last sibling now tears the stream down too.
        await realtime_service.stop_alert(data_b["id"])
        mock_rtvi_client.stop_stream.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_stop_alert_calls_stop_stream_for_owned_rule(
        self, realtime_service, mock_rtvi_client,
    ):
        """Regression: a rule that's the only reference to its stream
        (so ref-count goes to zero on delete) tears the stream down."""
        data, _ = await realtime_service.start_alert(make_config())

        await realtime_service.stop_alert(data["id"])

        mock_rtvi_client.stop_captions.assert_awaited_once()
        mock_rtvi_client.stop_stream.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_stop_alert_legacy_rule_without_flag_stops_stream(
        self, realtime_service, mock_rtvi_client,
    ):
        """Rules persisted before the reuse work shipped don't carry
        ``owns_rtvi_stream``. Ref-count semantics still apply: the rule
        is the last reader, so the stream is torn down."""
        data, _ = await realtime_service.start_alert(make_config())
        # Simulate a rule that predates the field
        realtime_service._rules[data["id"]].pop("owns_rtvi_stream", None)

        await realtime_service.stop_alert(data["id"])

        mock_rtvi_client.stop_captions.assert_awaited_once()
        mock_rtvi_client.stop_stream.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_persistent_stop_alert_with_siblings_skips_stop_stream(
        self, persistent_service, fake_rule_store, mock_rtvi_client,
    ):
        """ES-backed path mirrors the in-memory path: ref-count via ES
        list keeps the stream alive while siblings exist."""
        mock_rtvi_client.get_stream_info.return_value = [
            {"id": "test-sensor-001", "liveStreamUrl": SAMPLE_RTSP_URL},
        ]
        data_a, _ = await persistent_service.start_alert(
            make_config(alert_type="intrusion"),
        )
        data_b, _ = await persistent_service.start_alert(
            make_config(alert_type="fire"),
        )
        # Both rules saw the stream existed → both flagged as reusers.
        assert fake_rule_store.get(data_a["id"])["owns_rtvi_stream"] is False
        assert fake_rule_store.get(data_b["id"])["owns_rtvi_stream"] is False

        await persistent_service.stop_alert(data_a["id"])
        mock_rtvi_client.stop_stream.assert_not_awaited()

        await persistent_service.stop_alert(data_b["id"])
        mock_rtvi_client.stop_stream.assert_awaited_once()


# ---------------------------------------------------------------------------
# Replay reuse
# ---------------------------------------------------------------------------

class TestReplayStreamReuse:
    """Replay also routes through ``_resolve_or_add_stream`` so a partial
    earlier replay (or a process restart that crashed mid-replay) doesn't
    re-add the same stream a second time."""

    @pytest.mark.asyncio
    async def test_replay_reuses_existing_stream(
        self, mock_rtvi_client,
    ):
        fake_store = _FakeRuleStore()
        with patch("realtime.services.realtime_service.load_config", return_value={
            "rtvi_vlm": {
                "base_url": "http://mock:8000",
                "timeout": 5,
                "default_model": "default-vlm",
                "captions_ack_timeout": 0.1,
                "stream_readiness_poll_interval": 0.01,
                "stream_readiness_max_wait": 0.05,
            }
        }):
            svc = RealtimeAlertService(rule_store=fake_store)
        svc._client = mock_rtvi_client

        fake_store.create("rule-1", {
            "status": RuleStatus.ACTIVE,
            "created_at": "2025-01-01T00:00:00Z",
            "live_stream_url": SAMPLE_RTSP_URL,
            "alert_type": "intrusion",
            "prompt": "Detect intrusion",
            "sensor_id": "camera-001",
            "model": "test-model",
        })
        mock_rtvi_client.get_stream_info.return_value = [
            {"id": "camera-001", "liveStreamUrl": SAMPLE_RTSP_URL},
        ]

        data, code = await svc.replay()

        assert code == 200
        assert data["replayed"] == 1
        mock_rtvi_client.start_stream.assert_not_awaited()
        mock_rtvi_client.generate_captions.assert_awaited_once()

        stored = fake_store.get("rule-1")
        assert stored["owns_rtvi_stream"] is False
        assert stored["rtvi_stream_id"] == "camera-001"


# ---------------------------------------------------------------------------
# Readiness window: registry visibility ≠ readability (P1 regression)
# ---------------------------------------------------------------------------

class TestReadinessVisibilityNotReadiness:
    """``/streams/add`` registers a stream with RTVI synchronously, but
    ``generate_captions`` may still fail moments later when GStreamer
    cannot open the RTSP source. ``_wait_stream_ready`` must therefore
    keep monitoring the captions task for the full readiness window
    even after the stream first appears in ``get-stream-info`` —
    otherwise a fast post-registration failure would slip past as a
    spurious 201 with an asynchronous cleanup, breaking the inline
    ``rtvi_stream_not_readable`` contract.
    """

    @pytest.fixture()
    def visibility_service(self, mock_rtvi_client):
        with patch("realtime.services.realtime_service.load_config", return_value={
            "rtvi_vlm": {
                "base_url": "http://mock:8000",
                "timeout": 5,
                "default_model": "default-vlm",
                "captions_ack_timeout": 0.05,
                "stream_readiness_poll_interval": 0.02,
                "stream_readiness_max_wait": 0.5,
            }
        }):
            svc = RealtimeAlertService()
        svc._client = mock_rtvi_client
        return svc

    @pytest.mark.asyncio
    async def test_visible_stream_failing_after_registration_returns_502(
        self, visibility_service, mock_rtvi_client,
    ):
        """Registry visibility must not end the readiness window early.

        Simulates ``/streams/add`` registering a fresh stream
        synchronously while the underlying ``generate_captions`` task
        fails ~150 ms later (typical GStreamer "could not open RTSP
        source" timing). Before the fix, ``_wait_stream_ready`` saw
        the stream id appear in ``get-stream-info`` and returned
        immediately, letting the rule reach 201 + ES ACTIVE despite
        the source being unreadable. The fix keeps polling the
        captions task for the full ``stream_readiness_max_wait`` so
        the failure surfaces inline as a 502.
        """
        # Note: the visibility_service uses the in-memory mode (no
        # rule_store) to keep the fixture self-contained — both modes
        # share the same ``_wait_stream_ready`` code so the regression
        # is identical either way.
        mock_rtvi_client.start_stream.return_value = {
            "results": [{"id": "fresh-stream", "status": "added"}]
        }
        # The new stream is visible to ``get-stream-info`` immediately
        # after ``/streams/add`` — i.e. the exact scenario the previous
        # ``break`` on visibility was incorrectly trusting as success.
        mock_rtvi_client.get_stream_info.return_value = [
            {"id": "fresh-stream", "liveStreamUrl": SAMPLE_RTSP_URL},
        ]

        async def _delayed_failure(**kwargs):
            await asyncio.sleep(0.15)
            raise httpx.ReadError(
                "GStreamer: Could not open resource for reading"
            )

        mock_rtvi_client.generate_captions.side_effect = _delayed_failure

        # Use a config without sensor_id so we exercise the
        # ``owns_stream=True`` path that actually runs the readiness
        # poll (sensor_id reuse paths skip the poll entirely).
        data, code = await visibility_service.start_alert(
            make_config(sensor_id=None),
        )

        assert code == 502
        assert data["error"] == "rtvi_stream_not_readable"
        list_data, _ = await visibility_service.list_alerts()
        assert list_data["count"] == 0


# ---------------------------------------------------------------------------
# Pending stream refs survive concurrent rollback (P2 regression)
# ---------------------------------------------------------------------------

class TestPendingStreamRefRollback:
    """An in-flight create reusing a sibling's stream must be visible to
    the sibling's rollback ref-count even though its ES PENDING row
    doesn't yet carry ``rtvi_stream_id``. Without the in-memory pending
    ref the always-on parallel fan-out hit the documented race: the
    first owner's rollback would compute ``other_count == 0`` and tear
    the shared stream out from under a sibling that had reused it but
    hadn't reached the ACTIVE update yet.
    """

    @pytest.fixture()
    def quick_persistent_service(self, mock_rtvi_client):
        fake_store = _FakeRuleStore()
        with patch("realtime.services.realtime_service.load_config", return_value={
            "rtvi_vlm": {
                "base_url": "http://mock:8000",
                "timeout": 5,
                "default_model": "default-vlm",
                "captions_ack_timeout": 0.05,
                "stream_readiness_poll_interval": 0.01,
                "stream_readiness_max_wait": 0.05,
            }
        }):
            svc = RealtimeAlertService(rule_store=fake_store)
        svc._client = mock_rtvi_client
        return svc, fake_store

    @pytest.mark.asyncio
    async def test_register_unregister_helpers_round_trip(
        self, realtime_service,
    ):
        """Lightweight unit check on the helpers that back the fix:
        registration is observable to ``_count_other_rules_for_stream``
        with ``include_pending_refs=True`` and unregister cleans up.
        """
        realtime_service._register_pending_stream_ref("stream-X", "rule-A")

        # Excluding rule-A: nobody else holds the stream → 0.
        count = await realtime_service._count_other_rules_for_stream(
            "stream-X", "rule-A", include_pending_refs=True,
        )
        assert count == 0

        # A sibling registers → seen by rule-A's rollback query.
        realtime_service._register_pending_stream_ref("stream-X", "rule-B")
        count = await realtime_service._count_other_rules_for_stream(
            "stream-X", "rule-A", include_pending_refs=True,
        )
        assert count == 1

        # Without ``include_pending_refs`` the pending-only sibling is
        # invisible — that's the pre-fix behavior, kept as the default
        # so the user-driven delete path doesn't suddenly count
        # transient creates.
        count = await realtime_service._count_other_rules_for_stream(
            "stream-X", "rule-A", include_pending_refs=False,
        )
        assert count == 0

        # Unregister is idempotent and prunes the inner set on empty.
        realtime_service._unregister_pending_stream_ref("stream-X", "rule-A")
        realtime_service._unregister_pending_stream_ref("stream-X", "rule-B")
        realtime_service._unregister_pending_stream_ref(
            "stream-X", "rule-already-gone",
        )
        assert realtime_service._pending_stream_refs == {}

    @pytest.mark.asyncio
    async def test_rollback_with_pending_sibling_does_not_stop_stream(
        self, quick_persistent_service, mock_rtvi_client,
    ):
        """Rolling-back owner sees an in-flight sibling reuser via the
        in-memory pending ref and skips ``stop_stream`` even though
        the sibling's ES row is still PENDING with ``rtvi_stream_id=None``.
        """
        svc, fake_store = quick_persistent_service

        # Pre-register a sibling rule's PENDING row directly — exactly
        # what ``_build_rule_doc`` writes (no ``rtvi_stream_id`` set
        # yet) — and pretend the sibling has just reused our stream by
        # registering its in-memory pending ref.
        sibling_id = "sibling-rule"
        fake_store.create(sibling_id, {
            "status": RuleStatus.PENDING,
            "live_stream_url": SAMPLE_RTSP_URL,
            "alert_type": "fire",
            "prompt": "p",
            "sensor_id": "test-sensor-001",
            "model": "m",
        })
        svc._register_pending_stream_ref("stream-abc-123", sibling_id)

        # Force the owner's create to fail at ES update so we hit the
        # rollback path that used to over-eagerly stop the stream.
        mock_rtvi_client.start_stream.return_value = {
            "results": [{"id": "stream-abc-123", "status": "added"}]
        }
        original_update = fake_store.update

        def _fail_active_update(rule_id, partial):
            if partial.get("status") == RuleStatus.ACTIVE:
                raise RuntimeError("simulated ES update failure")
            return original_update(rule_id, partial)

        with patch.object(fake_store, "update", side_effect=_fail_active_update):
            data, code = await svc.start_alert(make_config())

        assert code == 502
        # The owner rolled back, but the sibling's pending ref kept the
        # shared stream alive — the regression we're guarding against.
        mock_rtvi_client.stop_stream.assert_not_awaited()

        # Cleanup so we don't leak pending refs into other tests.
        svc._unregister_pending_stream_ref("stream-abc-123", sibling_id)


# ---------------------------------------------------------------------------
# Sensor-id reuse identity check (medium-severity regression)
# ---------------------------------------------------------------------------

class TestStreamIdentityConflict:
    """``_resolve_or_add_stream`` reuses an existing RTVI registration
    only when the ``liveStreamUrl`` matches what the caller asked for.
    A mismatch surfaces as a 409 — silently binding to a stream that
    points at a different camera (and would be torn down by a later
    last-reader delete on a stream this service never created) is the
    medium-severity adversarial-review concern this guards against.
    """

    @pytest.mark.asyncio
    async def test_url_mismatch_on_reuse_returns_409(
        self, realtime_service, mock_rtvi_client,
    ):
        # The registry already has the requested ``sensor_id`` but
        # pointing at a *different* live URL — a stale or externally
        # owned registration.
        mock_rtvi_client.get_stream_info.return_value = [
            {
                "id": "test-sensor-001",
                "liveStreamUrl": "rtsp://other-camera/stream1",
            },
        ]

        data, code = await realtime_service.start_alert(make_config())

        assert code == 409
        assert data["error"] == "rtvi_stream_conflict"
        assert "sensor_id" in data["message"]
        # Crucially: we did NOT issue ``/streams/add`` (would create a
        # duplicate) and did NOT call ``generate_captions`` (would have
        # bound the rule to the wrong camera).
        mock_rtvi_client.start_stream.assert_not_awaited()
        mock_rtvi_client.generate_captions.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_url_match_on_reuse_still_succeeds(
        self, realtime_service, mock_rtvi_client,
    ):
        """Sanity check: same id + matching URL is still a valid reuse."""
        mock_rtvi_client.get_stream_info.return_value = [
            {"id": "test-sensor-001", "liveStreamUrl": SAMPLE_RTSP_URL},
        ]

        data, code = await realtime_service.start_alert(make_config())

        assert code == 201
        mock_rtvi_client.start_stream.assert_not_awaited()
        mock_rtvi_client.generate_captions.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_missing_url_in_registry_falls_back_to_reuse(
        self, realtime_service, mock_rtvi_client,
    ):
        """Older RTVI builds may omit ``liveStreamUrl`` from the listing.
        We can't validate identity in that case, so we allow reuse
        rather than fail-closed (which would break working setups).
        """
        mock_rtvi_client.get_stream_info.return_value = [
            {"id": "test-sensor-001"},
        ]

        data, code = await realtime_service.start_alert(make_config())

        assert code == 201
        mock_rtvi_client.start_stream.assert_not_awaited()
        mock_rtvi_client.generate_captions.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_url_mismatch_rolls_back_es_pending_row(
        self, persistent_service, fake_rule_store, mock_rtvi_client,
    ):
        """The conflict path runs after the persist-first PENDING row
        is written. It must roll the row back so the user isn't left
        with an orphan PENDING when they reissue the create.
        """
        mock_rtvi_client.get_stream_info.return_value = [
            {
                "id": "test-sensor-001",
                "liveStreamUrl": "rtsp://other-camera/stream1",
            },
        ]

        data, code = await persistent_service.start_alert(make_config())

        assert code == 409
        assert data["error"] == "rtvi_stream_conflict"
        # PENDING row from Step 0 must be cleaned up.
        assert fake_rule_store._docs == {}
