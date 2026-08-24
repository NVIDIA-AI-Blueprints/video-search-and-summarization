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

"""Unit tests for realtime API routes (FastAPI endpoints)."""

import asyncio
import json
import sys
from unittest.mock import AsyncMock

import pytest
import yaml
from fastapi.testclient import TestClient


@pytest.fixture()
def mocks():
    """Create mock services for dependency injection."""
    mock_realtime_svc = AsyncMock()
    mock_incident_svc = AsyncMock()

    mock_realtime_svc.start_alert.return_value = (
        {"status": "success", "id": "rule-1", "created_at": "2025-01-01T00:00:00Z", "message": "created"},
        201,
    )
    mock_realtime_svc.list_alerts.return_value = (
        {"status": "success", "rules": [], "count": 0},
        200,
    )
    mock_realtime_svc.stop_alert.return_value = (
        {"status": "success", "id": "rule-1", "message": "deleted"},
        200,
    )
    mock_incident_svc.list_incidents.return_value = (
        {"status": "success", "incidents": [], "count": 0, "total": 0, "timestamp": "2025-01-01T00:00:00Z"},
        200,
    )
    return {"realtime": mock_realtime_svc, "incident": mock_incident_svc}


@pytest.fixture()
def always_on_service(mocks):
    """Fresh :class:`AlwaysOnService` wrapping the mocked RealtimeAlertService.

    Yielded as a separate fixture so tests that directly drive service
    methods (startup validators, concurrency tests) can reach the same
    instance FastAPI uses for request handling.
    """
    from realtime import AlwaysOnService

    svc = AlwaysOnService(realtime_service=mocks["realtime"])
    yield svc
    svc.reset()


@pytest.fixture()
def client(mocks, always_on_service):
    """Create a TestClient with dependency overrides for service singletons.

    The alert-agent-web/app package is shadowed by a top-level app.py in the
    repo root, so we temporarily manipulate sys.path + sys.modules.
    """
    import sys
    import os

    web_root = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "..", "alert-agent-web")
    )
    repo_root = os.path.abspath(os.path.join(web_root, ".."))

    saved_modules = {k: sys.modules.pop(k) for k in list(sys.modules) if k == "app" or k.startswith("app.")}
    saved_path = sys.path[:]

    sys.path = [p for p in sys.path if os.path.abspath(p) != repo_root]
    sys.path.insert(0, web_root)
    try:
        from app.main import app
        from app.api.realtime_routes import (
            get_always_on_service,
            get_incident_service,
            get_realtime_service,
        )
    except (ImportError, Exception) as exc:
        pytest.skip(f"FastAPI app not importable: {exc}")
    finally:
        sys.path = saved_path
        sys.modules.update(saved_modules)

    app.dependency_overrides[get_realtime_service] = lambda: mocks["realtime"]
    app.dependency_overrides[get_incident_service] = lambda: mocks["incident"]
    app.dependency_overrides[get_always_on_service] = lambda: always_on_service
    yield TestClient(app, raise_server_exceptions=False)
    app.dependency_overrides.clear()


class TestPostRealtimeAlert:
    """POST /api/v1/realtime — create an alert rule.

    Covers both the happy path (every required and optional field
    reaches AlertRuleConfig intact) and representative sad paths for
    each class of failure: Pydantic validation errors on the request,
    ge/le constraint violations, wrong types, and an upstream RTVI
    failure coming back from the service.
    """

    # ── Happy paths ──────────────────────────────────────────────────

    def test_create_success_minimal(self, client):
        resp = client.post("/api/v1/realtime", json={
            "live_stream_url": "rtsp://host/stream",
            "sensor_id": "test-sensor-001",
            "alert_type": "collision",
            "prompt": "Detect collisions",
        })
        assert resp.status_code == 201
        assert resp.json()["status"] == "success"
        assert "id" in resp.json()

    def test_create_success_all_optional_fields(self, client, mocks):
        """All tuning knobs + `sensor_name` round-trip into AlertRuleConfig."""
        resp = client.post("/api/v1/realtime", json={
            "live_stream_url": "rtsp://host/stream",
            "alert_type": "fire",
            "prompt": "detect fire",
            "sensor_name": "warehouse-north",
            "system_prompt": "be concise",
            "model": "the-model",
            "chunk_duration": 60,
            "chunk_overlap_duration": 10,
            "num_frames_per_second_or_fixed_frames_chunk": 7,
            "use_fps_for_chunking": False,
            "vlm_input_width": 512,
            "vlm_input_height": 512,
            "enable_reasoning": False,
        })
        assert resp.status_code == 201
        cfg = mocks["realtime"].start_alert.await_args.args[0]
        assert cfg.live_stream_url == "rtsp://host/stream"
        assert cfg.alert_type == "fire"
        assert cfg.prompt == "detect fire"
        assert cfg.sensor_name == "warehouse-north"
        assert cfg.system_prompt == "be concise"
        assert cfg.model == "the-model"
        assert cfg.chunk_duration == 60
        assert cfg.chunk_overlap_duration == 10
        assert cfg.num_frames_per_second_or_fixed_frames_chunk == 7
        assert cfg.use_fps_for_chunking is False
        assert cfg.vlm_input_width == 512
        assert cfg.vlm_input_height == 512
        assert cfg.enable_reasoning is False

    def test_create_success_extended_rtvi_fields(self, client, mocks):
        """All 12 extended RTVI VLM fields round-trip from the request body
        into AlertRuleConfig so they can reach generate_captions."""
        resp = client.post("/api/v1/realtime", json={
            "live_stream_url": "rtsp://host/stream",
            "alert_type": "ppe",
            "prompt": "detect PPE violations",
            "system_prompt": "You are a safety assistant",
            "model": "cosmos-reason1",
            # Extended fields
            "api_type": "internal",
            "response_format": {"type": "text"},
            "stream_options": {"include_usage": True},
            "max_tokens": 512,
            "temperature": 0.2,
            "top_p": 1.0,
            "top_k": 100,
            "ignore_eos": True,
            "seed": 42,
            "media_info": {"type": "offset", "start_offset": 0, "end_offset": 4000000000},
            "enable_audio": True,
            "mm_processor_kwargs": {"additionalProp1": {}},
        })
        assert resp.status_code == 201
        cfg = mocks["realtime"].start_alert.await_args.args[0]
        assert cfg.api_type == "internal"
        assert cfg.response_format == {"type": "text"}
        assert cfg.stream_options == {"include_usage": True}
        assert cfg.max_tokens == 512
        assert cfg.temperature == 0.2
        assert cfg.top_p == 1.0
        assert cfg.top_k == 100
        assert cfg.ignore_eos is True
        assert cfg.seed == 42
        assert cfg.media_info == {"type": "offset", "start_offset": 0, "end_offset": 4000000000}
        assert cfg.enable_audio is True
        assert cfg.mm_processor_kwargs == {"additionalProp1": {}}

    def test_extended_fields_absent_when_not_supplied(self, client, mocks):
        """When extended fields are omitted, the config carries None for each."""
        resp = client.post("/api/v1/realtime", json={
            "live_stream_url": "rtsp://host/stream",
            "alert_type": "fire",
            "prompt": "detect fire",
        })
        assert resp.status_code == 201
        cfg = mocks["realtime"].start_alert.await_args.args[0]
        for field in (
            "api_type", "response_format", "stream_options", "max_tokens",
            "temperature", "top_p", "top_k", "ignore_eos", "seed",
            "media_info", "enable_audio", "mm_processor_kwargs",
        ):
            assert getattr(cfg, field) is None, f"expected {field} to be None"

    def test_alias_liveStreamUrl_accepted(self, client):
        resp = client.post("/api/v1/realtime", json={
            "liveStreamUrl": "rtsp://host/stream",
            "alert_type": "collision",
            "prompt": "test",
        })
        assert resp.status_code == 201

    def test_alias_chunk_frames_accepted(self, client, mocks):
        """`chunk_frames` maps to `num_frames_per_second_or_fixed_frames_chunk`."""
        resp = client.post("/api/v1/realtime", json={
            "live_stream_url": "rtsp://host/stream",
            "alert_type": "collision",
            "prompt": "test",
            "chunk_frames": 3,
        })
        assert resp.status_code == 201
        cfg = mocks["realtime"].start_alert.await_args.args[0]
        assert cfg.num_frames_per_second_or_fixed_frames_chunk == 3

    # ── Sad paths: missing required fields ───────────────────────────

    def test_missing_required_fields_returns_422(self, client):
        resp = client.post("/api/v1/realtime", json={"prompt": "test"})
        assert resp.status_code == 422

    def test_missing_live_stream_url_returns_422(self, client):
        resp = client.post("/api/v1/realtime", json={
            "alert_type": "collision",
            "prompt": "test",
        })
        assert resp.status_code == 422

    def test_missing_alert_type_returns_422(self, client):
        resp = client.post("/api/v1/realtime", json={
            "live_stream_url": "rtsp://host/stream",
            "prompt": "test",
        })
        assert resp.status_code == 422

    def test_missing_prompt_returns_422(self, client):
        resp = client.post("/api/v1/realtime", json={
            "live_stream_url": "rtsp://host/stream",
            "alert_type": "collision",
        })
        assert resp.status_code == 422

    # ── Sad paths: value-level validation ────────────────────────────

    def test_invalid_rtsp_url_returns_422(self, client):
        resp = client.post("/api/v1/realtime", json={
            "live_stream_url": "http://not-rtsp",
            "sensor_id": "test-sensor",
            "alert_type": "test",
            "prompt": "test",
        })
        assert resp.status_code == 422

    def test_empty_url_returns_422(self, client):
        resp = client.post("/api/v1/realtime", json={
            "live_stream_url": "",
            "sensor_id": "test-sensor",
            "alert_type": "test",
            "prompt": "test",
        })
        assert resp.status_code == 422

    def test_chunk_duration_below_min_returns_422(self, client):
        """`chunk_duration: Field(ge=1)` — zero is invalid."""
        resp = client.post("/api/v1/realtime", json={
            "live_stream_url": "rtsp://host/stream",
            "sensor_id": "test-sensor",
            "alert_type": "collision",
            "prompt": "test",
            "chunk_duration": 0,
        })
        assert resp.status_code == 422

    def test_alias_id_for_sensor_id_accepted(self, client):
        resp = client.post("/api/v1/realtime", json={
            "liveStreamUrl": "rtsp://host/stream",
            "id": "sensor-via-alias",
            "alert_type": "collision",
            "prompt": "test",
        })
        assert resp.status_code == 201

    def test_negative_chunk_overlap_duration_returns_422(self, client):
        """`chunk_overlap_duration: Field(ge=0)` — negative is invalid."""
        resp = client.post("/api/v1/realtime", json={
            "live_stream_url": "rtsp://host/stream",
            "alert_type": "collision",
            "prompt": "test",
            "chunk_overlap_duration": -1,
        })
        assert resp.status_code == 422

    def test_wrong_type_for_int_field_returns_422(self, client):
        resp = client.post("/api/v1/realtime", json={
            "live_stream_url": "rtsp://host/stream",
            "alert_type": "collision",
            "prompt": "test",
            "chunk_duration": "thirty",
        })
        assert resp.status_code == 422

    # ── Sad paths: extended field range validation ────────────────────

    @pytest.mark.parametrize("field,value", [
        ("max_tokens", 0),
        ("max_tokens", -1),
        ("temperature", -0.1),
        ("top_p", -0.1),
        ("top_p", 1.1),
        ("top_k", -1),
    ])
    def test_out_of_range_extended_field_returns_422(self, client, field, value):
        """Pydantic range constraints on the 12 extended fields are enforced at the route level."""
        resp = client.post("/api/v1/realtime", json={
            "live_stream_url": "rtsp://host/stream",
            "alert_type": "fire",
            "prompt": "detect fire",
            field: value,
        })
        assert resp.status_code == 422, (
            f"expected 422 for {field}={value!r}, got {resp.status_code}"
        )

    # ── Sad path: upstream service failure ───────────────────────────

    def test_rtvi_unavailable_surfaces_502(self, client, mocks):
        """If the service reports RTVI unavailable, the route surfaces 502."""
        mocks["realtime"].start_alert.return_value = (
            {
                "status": "error",
                "error": "rtvi_vlm_unavailable",
                "message": "RTVI down",
                "timestamp": "T",
            },
            502,
        )
        resp = client.post("/api/v1/realtime", json={
            "live_stream_url": "rtsp://host/stream",
            "alert_type": "collision",
            "prompt": "test",
        })
        assert resp.status_code == 502
        assert resp.json()["error"] == "rtvi_vlm_unavailable"

    def test_stream_readiness_failure_surfaces_502(self, client, mocks):
        """If the stream fails the inline readiness check, the route surfaces 502."""
        mocks["realtime"].start_alert.return_value = (
            {
                "status": "error",
                "error": "rtvi_stream_not_readable",
                "message": "Stream failed readiness check: connection refused",
                "timestamp": "T",
            },
            502,
        )
        resp = client.post("/api/v1/realtime", json={
            "live_stream_url": "rtsp://host/stream",
            "alert_type": "collision",
            "prompt": "test",
        })
        assert resp.status_code == 502
        assert resp.json()["error"] == "rtvi_stream_not_readable"


class TestGetRealtimeAlerts:
    """GET /api/v1/realtime — list active alert rules."""

    def test_list_empty_returns_200(self, client):
        resp = client.get("/api/v1/realtime")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "success"
        assert "rules" in body
        assert body["count"] == 0
        assert body["rules"] == []

    def test_list_returns_rules_when_present(self, client, mocks):
        """When the service has active rules, they flow through verbatim."""
        mocks["realtime"].list_alerts.return_value = (
            {
                "status": "success",
                "rules": [
                    {
                        "id": "11111111-1111-1111-1111-111111111111",
                        "live_stream_url": "rtsp://host/stream",
                        "alert_type": "fire",
                        "sensor_name": "warehouse",
                        "prompt": "detect fire",
                        "system_prompt": "",
                        "model": "m",
                        "chunk_duration": 30,
                        "chunk_overlap_duration": 5,
                        "num_frames_per_second_or_fixed_frames_chunk": 10,
                        "use_fps_for_chunking": True,
                        "vlm_input_width": 256,
                        "vlm_input_height": 256,
                        "enable_reasoning": True,
                        "status": "active",
                        "created_at": "2025-01-01T00:00:00Z",
                    }
                ],
                "count": 1,
            },
            200,
        )
        resp = client.get("/api/v1/realtime")
        assert resp.status_code == 200
        body = resp.json()
        assert body["count"] == 1
        assert body["rules"][0]["alert_type"] == "fire"
        assert body["rules"][0]["sensor_name"] == "warehouse"

    def test_list_includes_extended_fields_when_set(self, client, mocks):
        """All 12 extended fields present on a rule flow through the GET listing response."""
        mocks["realtime"].list_alerts.return_value = (
            {
                "status": "success",
                "rules": [
                    {
                        "id": "22222222-2222-2222-2222-222222222222",
                        "live_stream_url": "rtsp://host/stream",
                        "alert_type": "ppe",
                        "prompt": "detect ppe",
                        "system_prompt": "be concise",
                        "model": "cosmos",
                        "chunk_duration": 30,
                        "chunk_overlap_duration": 5,
                        "num_frames_per_second_or_fixed_frames_chunk": 10,
                        "use_fps_for_chunking": True,
                        "vlm_input_width": 256,
                        "vlm_input_height": 256,
                        "enable_reasoning": True,
                        "status": "active",
                        "created_at": "2025-01-01T00:00:00Z",
                        # extended fields
                        "api_type": "internal",
                        "response_format": {"type": "text"},
                        "stream_options": {"include_usage": True},
                        "max_tokens": 512,
                        "temperature": 0.2,
                        "top_p": 0.95,
                        "top_k": 50,
                        "ignore_eos": True,
                        "seed": 42,
                        "media_info": {"type": "offset", "start_offset": 0, "end_offset": 4000000000},
                        "enable_audio": True,
                        "mm_processor_kwargs": {"key": "val"},
                    }
                ],
                "count": 1,
            },
            200,
        )
        resp = client.get("/api/v1/realtime")
        assert resp.status_code == 200
        rule = resp.json()["rules"][0]
        assert rule["api_type"] == "internal"
        assert rule["response_format"] == {"type": "text"}
        assert rule["stream_options"] == {"include_usage": True}
        assert rule["max_tokens"] == 512
        assert rule["temperature"] == 0.2
        assert rule["top_p"] == 0.95
        assert rule["top_k"] == 50
        assert rule["ignore_eos"] is True
        assert rule["seed"] == 42
        assert rule["media_info"] == {"type": "offset", "start_offset": 0, "end_offset": 4000000000}
        assert rule["enable_audio"] is True
        assert rule["mm_processor_kwargs"] == {"key": "val"}

    def test_list_excludes_extended_fields_when_absent(self, client, mocks):
        """Extended fields not set on a rule must not appear in the GET listing response."""
        mocks["realtime"].list_alerts.return_value = (
            {
                "status": "success",
                "rules": [
                    {
                        "id": "33333333-3333-3333-3333-333333333333",
                        "live_stream_url": "rtsp://host/stream",
                        "alert_type": "fire",
                        "prompt": "detect fire",
                        "system_prompt": "",
                        "model": "m",
                        "chunk_duration": 30,
                        "chunk_overlap_duration": 5,
                        "num_frames_per_second_or_fixed_frames_chunk": 10,
                        "use_fps_for_chunking": True,
                        "vlm_input_width": 256,
                        "vlm_input_height": 256,
                        "enable_reasoning": True,
                        "status": "active",
                        "created_at": "2025-01-01T00:00:00Z",
                        # no extended fields
                    }
                ],
                "count": 1,
            },
            200,
        )
        resp = client.get("/api/v1/realtime")
        assert resp.status_code == 200
        rule = resp.json()["rules"][0]
        for field in (
            "api_type", "response_format", "stream_options", "max_tokens",
            "temperature", "top_p", "top_k", "ignore_eos", "seed",
            "media_info", "enable_audio", "mm_processor_kwargs",
        ):
            assert field not in rule, f"expected {field!r} absent when not set"


class TestDeleteRealtimeAlert:
    """DELETE /api/v1/realtime/{alert_rule_id} — delete an alert rule."""

    def test_delete_valid_uuid(self, client, mocks):
        rule_id = "00000000-0000-0000-0000-000000000001"
        resp = client.delete(f"/api/v1/realtime/{rule_id}")
        assert resp.status_code == 200
        assert resp.json()["status"] == "success"
        # The path param is forwarded to the service as a plain str.
        mocks["realtime"].stop_alert.assert_awaited_once_with(rule_id)

    def test_delete_malformed_uuid_returns_422(self, client, mocks):
        resp = client.delete("/api/v1/realtime/not-a-uuid")
        assert resp.status_code == 422
        mocks["realtime"].stop_alert.assert_not_awaited()

    def test_delete_nonexistent_returns_404(self, client, mocks):
        mocks["realtime"].stop_alert.return_value = (
            {"status": "error", "error": "not_found", "message": "Not found", "timestamp": "T"},
            404,
        )
        resp = client.delete("/api/v1/realtime/00000000-0000-0000-0000-000000000099")
        assert resp.status_code == 404
        assert resp.json()["error"] == "not_found"

    def test_delete_rtvi_unavailable_surfaces_502(self, client, mocks):
        """A 502 from the service (e.g., RTVI returned an unexpected status)
        is propagated to the HTTP client verbatim.
        """
        mocks["realtime"].stop_alert.return_value = (
            {
                "status": "error",
                "error": "rtvi_vlm_unavailable",
                "message": "RTVI down",
                "timestamp": "T",
            },
            502,
        )
        resp = client.delete("/api/v1/realtime/00000000-0000-0000-0000-000000000001")
        assert resp.status_code == 502
        assert resp.json()["error"] == "rtvi_vlm_unavailable"


class TestGetRealtimeAlertById:
    """GET /api/v1/realtime/{alert_rule_id} — get a single alert rule."""

    def test_get_existing_rule(self, client, mocks):
        mocks["realtime"].get_alert.return_value = (
            {
                "status": "success",
                "rule": {
                    "id": "00000000-0000-0000-0000-000000000001",
                    "live_stream_url": "rtsp://host/stream",
                    "alert_type": "fire",
                    "sensor_name": "",
                    "prompt": "detect fire",
                    "system_prompt": "",
                    "model": "m",
                    "chunk_duration": 30,
                    "chunk_overlap_duration": 5,
                    "num_frames_per_second_or_fixed_frames_chunk": 10,
                    "use_fps_for_chunking": True,
                    "vlm_input_width": 256,
                    "vlm_input_height": 256,
                    "enable_reasoning": True,
                    "status": "active",
                    "created_at": "2025-01-01T00:00:00Z",
                },
            },
            200,
        )
        rule_id = "00000000-0000-0000-0000-000000000001"
        resp = client.get(f"/api/v1/realtime/{rule_id}")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "success"
        assert body["rule"]["id"] == rule_id
        assert body["rule"]["alert_type"] == "fire"
        mocks["realtime"].get_alert.assert_awaited_once_with(rule_id)

    def test_get_nonexistent_returns_404(self, client, mocks):
        mocks["realtime"].get_alert.return_value = (
            {"status": "error", "error": "not_found", "message": "Not found", "timestamp": "T"},
            404,
        )
        resp = client.get("/api/v1/realtime/00000000-0000-0000-0000-000000000099")
        assert resp.status_code == 404
        assert resp.json()["error"] == "not_found"

    def test_get_malformed_uuid_returns_422(self, client, mocks):
        resp = client.get("/api/v1/realtime/not-a-uuid")
        assert resp.status_code == 422
        mocks["realtime"].get_alert.assert_not_awaited()

    def test_get_does_not_clash_with_incidents_route(self, client, mocks):
        """GET /api/v1/realtime/incidents must still reach the incidents handler,
        not the {alert_rule_id} route."""
        resp = client.get("/api/v1/realtime/incidents")
        assert resp.status_code == 200
        mocks["incident"].list_incidents.assert_awaited_once()
        mocks["realtime"].get_alert.assert_not_awaited()


class TestGetIncidents:
    """GET /api/v1/realtime/incidents — list incidents from Elasticsearch."""

    # ── Happy paths ──────────────────────────────────────────────────

    def test_incidents_empty_returns_200(self, client):
        resp = client.get("/api/v1/realtime/incidents")
        assert resp.status_code == 200
        assert resp.json()["status"] == "success"
        assert resp.json()["count"] == 0

    def test_incidents_returns_results_when_present(self, client, mocks):
        """When ES has incidents, they flow through to the response body."""
        mocks["incident"].list_incidents.return_value = (
            {
                "status": "success",
                "incidents": [
                    {"_id": "doc-1", "_index": "mdx-vlm-incidents-2025",
                     "sensorId": "s1", "category": "fire"}
                ],
                "count": 1,
                "total": 42,
                "timestamp": "2025-01-01T00:00:00Z",
            },
            200,
        )
        resp = client.get("/api/v1/realtime/incidents")
        assert resp.status_code == 200
        body = resp.json()
        assert body["count"] == 1
        assert body["total"] == 42
        assert body["incidents"][0]["_id"] == "doc-1"

    def test_filters_forwarded_to_service(self, client, mocks):
        """All query-string filters must reach `IncidentService.list_incidents`."""
        resp = client.get(
            "/api/v1/realtime/incidents"
            "?sensor_id=cam-1&category=fire"
            "&start_time=2025-01-01T00:00:00Z"
            "&end_time=2025-01-02T00:00:00Z"
            "&limit=50&offset=10"
        )
        assert resp.status_code == 200
        kwargs = mocks["incident"].list_incidents.await_args.kwargs
        assert kwargs["sensor_id"] == "cam-1"
        assert kwargs["category"] == "fire"
        assert kwargs["start_time"] == "2025-01-01T00:00:00+00:00"
        assert kwargs["end_time"] == "2025-01-02T00:00:00+00:00"
        assert kwargs["limit"] == 50
        assert kwargs["offset"] == 10

    def test_valid_iso_timestamp_accepted(self, client):
        resp = client.get("/api/v1/realtime/incidents?start_time=2025-01-01T00:00:00Z")
        assert resp.status_code == 200

    def test_pagination_params(self, client):
        resp = client.get("/api/v1/realtime/incidents?limit=10&offset=5")
        assert resp.status_code == 200

    # ── Sad paths ────────────────────────────────────────────────────

    def test_invalid_start_time_returns_422(self, client):
        resp = client.get("/api/v1/realtime/incidents?start_time=yesterday")
        assert resp.status_code == 422

    def test_invalid_end_time_returns_422(self, client):
        resp = client.get("/api/v1/realtime/incidents?end_time=nonsense")
        assert resp.status_code == 422

    def test_limit_exceeds_max_returns_422(self, client):
        resp = client.get("/api/v1/realtime/incidents?limit=9999")
        assert resp.status_code == 422

    def test_limit_below_min_returns_422(self, client):
        """limit=0 violates `Query(ge=1)`."""
        resp = client.get("/api/v1/realtime/incidents?limit=0")
        assert resp.status_code == 422

    def test_negative_offset_returns_422(self, client):
        resp = client.get("/api/v1/realtime/incidents?offset=-1")
        assert resp.status_code == 422

    def test_elasticsearch_unavailable_surfaces_503(self, client, mocks):
        """When the IncidentService reports ES unavailable, the route returns 503."""
        mocks["incident"].list_incidents.return_value = (
            {
                "status": "error",
                "error": "service_unavailable",
                "message": "Elasticsearch not reachable",
                "timestamp": "T",
            },
            503,
        )
        resp = client.get("/api/v1/realtime/incidents")
        assert resp.status_code == 503
        assert resp.json()["error"] == "service_unavailable"


class TestPostRealtimeReplay:
    """POST /api/v1/realtime/replay — operator-triggered rule replay."""

    def test_replay_success(self, client, mocks):
        mocks["realtime"].replay.return_value = (
            {
                "status": "success",
                "message": "Replay completed",
                "replayed": 2,
                "failed": 0,
                "total": 2,
                "details": [
                    {"id": "r1", "alert_type": "fire", "result": "success", "rtvi_stream_id": "s1"},
                    {"id": "r2", "alert_type": "collision", "result": "success", "rtvi_stream_id": "s2"},
                ],
            },
            200,
        )
        resp = client.post("/api/v1/realtime/replay", json={})
        assert resp.status_code == 200
        body = resp.json()
        assert body["replayed"] == 2
        assert body["failed"] == 0
        mocks["realtime"].replay.assert_awaited_once()

    def test_replay_empty_store_returns_200(self, client, mocks):
        mocks["realtime"].replay.return_value = (
            {
                "status": "success",
                "message": "No active rules to replay",
                "replayed": 0,
                "failed": 0,
                "total": 0,
                "details": [],
            },
            200,
        )
        resp = client.post("/api/v1/realtime/replay", json={})
        assert resp.status_code == 200
        assert resp.json()["total"] == 0

    def test_replay_409_when_in_flight(self, client, mocks):
        mocks["realtime"].replay.return_value = (
            {
                "status": "error",
                "error": "replay_in_flight",
                "message": "A replay is already in progress",
                "timestamp": "T",
            },
            409,
        )
        resp = client.post("/api/v1/realtime/replay", json={})
        assert resp.status_code == 409
        assert resp.json()["error"] == "replay_in_flight"

    def test_replay_503_when_no_persistence(self, client, mocks):
        mocks["realtime"].replay.return_value = (
            {
                "status": "error",
                "error": "elasticsearch_unavailable",
                "message": "Persistence is not configured",
                "timestamp": "T",
            },
            503,
        )
        resp = client.post("/api/v1/realtime/replay", json={})
        assert resp.status_code == 503

    def test_replay_does_not_clash_with_get_by_id(self, client, mocks):
        """POST /replay must not be mistaken for a parameterized path."""
        mocks["realtime"].replay.return_value = (
            {"status": "success", "message": "ok", "replayed": 0, "failed": 0, "total": 0, "details": []},
            200,
        )
        resp = client.post("/api/v1/realtime/replay", json={})
        assert resp.status_code == 200
        mocks["realtime"].get_alert.assert_not_awaited()


# ---------------------------------------------------------------------------
# Always-on endpoint
# ---------------------------------------------------------------------------


@pytest.fixture()
def routes_module(client):
    """The realtime_routes module wired into the TestClient's app.

    Depends on ``client`` so that the sys.path / sys.modules dance has
    already put the alert-agent-web ``app`` package on sys.modules.
    """
    return sys.modules["app.api.realtime_routes"]


@pytest.fixture()
def always_on(always_on_service, monkeypatch, tmp_path):
    """Configure always-on rules via YAML + env var and reset service state.

    Returns a ``set_rules(rules_list)`` helper that writes a fresh YAML and
    points ``ALWAYS_ON_RULES_CONFIG`` at it. Also writes a minimal
    ``config.yaml`` with ``alert_agent.always_on: true`` and points
    ``CONFIG_PATH`` at it so the feature gate allows requests through.
    The :class:`AlwaysOnService` under test is reset (sidecar,
    in-flight set, flight-done events, rule + enabled caches) before
    and after each test so tests are isolated.
    """
    always_on_service.reset()

    # Enable the feature flag for this test via a tmp config.yaml.
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump({"alert_agent": {"always_on": True}}))
    monkeypatch.setenv("CONFIG_PATH", str(config_path))

    def _set_rules(rules):
        # Reset again so the rules cache is dropped and the new YAML
        # is picked up on the next load. Sidecar is already clean from
        # the initial reset; reset() just makes both explicit.
        always_on_service.reset()
        path = tmp_path / "always-on-rules.yaml"
        path.write_text(yaml.safe_dump({"always_on_rules": rules}))
        monkeypatch.setenv("ALWAYS_ON_RULES_CONFIG", str(path))
        return path

    yield _set_rules

    always_on_service.reset()


def _streaming_event(camera_id="cam-1", camera_url="rtsp://cam-1/stream", camera_name="Cam 1"):
    return {
        "source": "vst",
        "event": {
            "camera_id": camera_id,
            "camera_name": camera_name,
            "camera_url": camera_url,
            "change": "camera_streaming",
        },
    }


def _remove_event(camera_id="cam-1"):
    return {
        "source": "vst",
        "event": {"camera_id": camera_id, "change": "camera_remove"},
    }


def _sample_rule(
    rule_id="rule-1",
    alert_type="collision",
    prompt="detect",
    system_prompt="",
    model="test-model",
):
    """Build a minimal valid always-on rule entry.

    ``system_prompt`` and ``model`` are now required by AlwaysOnRuleParams
    (even though AlertRuleConfig carries defaults for them) so every rule
    used in tests must supply explicit values.
    """
    return {
        "rule_id": rule_id,
        "alert_type": alert_type,
        "always_on_params": {
            "prompt": prompt,
            "system_prompt": system_prompt,
            "model": model,
        },
    }


class TestAlwaysOnPayloadParsing:
    """POST /api/v1/realtime/always-on — event payload shapes."""

    def test_accepts_parsed_event_shape(self, client, always_on):
        always_on([_sample_rule()])
        resp = client.post("/api/v1/realtime/always-on", json=_streaming_event())
        assert resp.status_code == 200
        body = resp.json()
        assert body["reason"] == "STREAM_ADD_SUCCESS"
        assert body["status"] == "HTTP/1.1 200 OK"
        # Fan-out responses carry a per-rule details array; verify basic shape.
        assert [d["rule_id"] for d in body["details"]] == ["rule-1"]
        assert body["details"][0]["result"] == "success"

    def test_accepts_full_vst_payload_with_extra_fields(
        self, client, mocks, always_on
    ):
        """The real VST producer emits extra fields (`alert_type`, `created_at`,
        `camera_vod_url`, `metadata`); they must pass through without rejection.
        """
        always_on([_sample_rule()])
        resp = client.post(
            "/api/v1/realtime/always-on",
            json={
                "source": "vst",
                "alert_type": "camera_status_change",
                "created_at": "2026-04-22T17:38:38Z",
                "event": {
                    "camera_id": "c0413489-6ca1-422e-a09c-08224169ff6a",
                    "camera_name": "warehouse",
                    "camera_url": "rtsp://localhost:8554/live/c0413489",
                    "camera_vod_url": "rtsp://localhost:8554/vod/c0413489",
                    "change": "camera_streaming",
                    "metadata": {"codec": "H264"},
                },
            },
        )
        assert resp.status_code == 200
        assert resp.json()["reason"] == "STREAM_ADD_SUCCESS"
        cfg = mocks["realtime"].start_alert.await_args.args[0]
        assert cfg.live_stream_url == "rtsp://localhost:8554/live/c0413489"

    def test_rejects_legacy_sensor_id_string_shape(self, client, mocks, always_on):
        """The legacy ``{"sensor.id": "<json>"}`` shape is no longer accepted.

        Producers must emit the canonical VST envelope with a top-level
        ``event`` object. Whether the inner string is valid JSON or not, the
        request is rejected as 422 INVALID_PAYLOAD and no RTVI call is made.
        """
        always_on([_sample_rule()])

        # Previously-accepted well-formed string-wrapped shape: now rejected.
        resp = client.post(
            "/api/v1/realtime/always-on",
            json={"sensor.id": json.dumps(_streaming_event())},
        )
        assert resp.status_code == 422
        assert resp.json()["reason"] == "INVALID_PAYLOAD"
        mocks["realtime"].start_alert.assert_not_awaited()

        # Malformed variant: same rejection, no special handling.
        resp = client.post(
            "/api/v1/realtime/always-on",
            json={"sensor.id": "not-json"},
        )
        assert resp.status_code == 422
        assert resp.json()["reason"] == "INVALID_PAYLOAD"
        mocks["realtime"].start_alert.assert_not_awaited()

    def test_empty_payload_returns_422(self, client, always_on):
        always_on([_sample_rule()])
        resp = client.post("/api/v1/realtime/always-on", json={})
        assert resp.status_code == 422
        assert resp.json()["reason"] == "INVALID_PAYLOAD"

    def test_missing_event_key_returns_422(self, client, mocks, always_on):
        """Top-level dict without the canonical `event` key is rejected by Pydantic."""
        always_on([_sample_rule()])
        resp = client.post(
            "/api/v1/realtime/always-on",
            json={"source": "vst"},  # event missing
        )
        assert resp.status_code == 422
        assert resp.json()["reason"] == "INVALID_PAYLOAD"
        mocks["realtime"].start_alert.assert_not_awaited()

    def test_event_null_returns_422(self, client, mocks, always_on):
        """`event: null` fails Pydantic type validation."""
        always_on([_sample_rule()])
        resp = client.post(
            "/api/v1/realtime/always-on",
            json={"source": "vst", "event": None},
        )
        assert resp.status_code == 422
        assert resp.json()["reason"] == "INVALID_PAYLOAD"
        mocks["realtime"].start_alert.assert_not_awaited()


class TestAlwaysOnValidation:
    """POST /api/v1/realtime/always-on — field and change validation."""

    def test_missing_camera_id_returns_422(self, client, always_on):
        always_on([_sample_rule()])
        event = _streaming_event()
        event["event"].pop("camera_id")
        resp = client.post("/api/v1/realtime/always-on", json=event)
        assert resp.status_code == 422
        assert resp.json()["reason"] == "INVALID_PAYLOAD"

    def test_unknown_change_value_returns_422(self, client, always_on):
        always_on([_sample_rule()])
        event = _streaming_event()
        event["event"]["change"] = "camera_renamed"
        resp = client.post("/api/v1/realtime/always-on", json=event)
        assert resp.status_code == 422
        assert resp.json()["reason"] == "INVALID_PAYLOAD"

    def test_streaming_without_camera_url_returns_422(self, client, always_on):
        always_on([_sample_rule()])
        event = _streaming_event()
        event["event"].pop("camera_url")
        resp = client.post("/api/v1/realtime/always-on", json=event)
        assert resp.status_code == 422

    def test_streaming_without_camera_name_returns_422(self, client, always_on):
        always_on([_sample_rule()])
        event = _streaming_event()
        event["event"].pop("camera_name")
        resp = client.post("/api/v1/realtime/always-on", json=event)
        assert resp.status_code == 422

    def test_streaming_with_empty_camera_url_returns_422(self, client, mocks, always_on):
        always_on([_sample_rule()])
        event = _streaming_event(camera_url="")
        resp = client.post("/api/v1/realtime/always-on", json=event)
        assert resp.status_code == 422
        assert resp.json()["reason"] == "INVALID_PAYLOAD"
        mocks["realtime"].start_alert.assert_not_awaited()

    def test_streaming_with_empty_camera_name_returns_422(self, client, mocks, always_on):
        always_on([_sample_rule()])
        event = _streaming_event(camera_name="")
        resp = client.post("/api/v1/realtime/always-on", json=event)
        assert resp.status_code == 422
        assert resp.json()["reason"] == "INVALID_PAYLOAD"
        mocks["realtime"].start_alert.assert_not_awaited()

    def test_empty_camera_id_returns_422(self, client, mocks, always_on):
        """`camera_id: Field(min_length=1)` — empty string is rejected by Pydantic."""
        always_on([_sample_rule()])
        event = _streaming_event()
        event["event"]["camera_id"] = ""
        resp = client.post("/api/v1/realtime/always-on", json=event)
        assert resp.status_code == 422
        assert resp.json()["reason"] == "INVALID_PAYLOAD"
        mocks["realtime"].start_alert.assert_not_awaited()

    def test_missing_change_field_returns_422(self, client, mocks, always_on):
        """`change` is a required Literal; missing the key fails validation."""
        always_on([_sample_rule()])
        event = _streaming_event()
        event["event"].pop("change")
        resp = client.post("/api/v1/realtime/always-on", json=event)
        assert resp.status_code == 422
        assert resp.json()["reason"] == "INVALID_PAYLOAD"
        mocks["realtime"].start_alert.assert_not_awaited()


class TestAlwaysOnCameraStreaming:
    """POST /api/v1/realtime/always-on — camera_streaming fan-out."""

    def test_single_rule_happy_path(self, client, mocks, always_on):
        """Simplest successful case: one rule, one camera, one RTVI call."""
        always_on([_sample_rule(rule_id="r1", alert_type="fire", prompt="detect fire")])
        resp = client.post("/api/v1/realtime/always-on", json=_streaming_event())
        assert resp.status_code == 200
        assert resp.json()["reason"] == "STREAM_ADD_SUCCESS"
        mocks["realtime"].start_alert.assert_awaited_once()
        cfg = mocks["realtime"].start_alert.await_args.args[0]
        assert cfg.alert_type == "fire"
        assert cfg.prompt == "detect fire"
        assert cfg.live_stream_url == "rtsp://cam-1/stream"
        assert cfg.sensor_name == "Cam 1"

    def test_fans_out_one_start_alert_per_rule(self, client, mocks, always_on):
        always_on([
            _sample_rule(rule_id="r1", alert_type="fire", prompt="detect fire"),
            _sample_rule(rule_id="r2", alert_type="collision", prompt="detect crashes"),
        ])
        mocks["realtime"].start_alert.side_effect = [
            ({"status": "success", "id": f"rule-{i}", "created_at": "T", "message": "ok"}, 201)
            for i in (1, 2)
        ]

        resp = client.post("/api/v1/realtime/always-on", json=_streaming_event())

        assert resp.status_code == 200
        assert resp.json()["reason"] == "STREAM_ADD_SUCCESS"
        assert mocks["realtime"].start_alert.await_count == 2

    def test_camera_url_from_event_populates_live_stream_url(
        self, client, mocks, always_on
    ):
        always_on([_sample_rule()])
        client.post(
            "/api/v1/realtime/always-on",
            json=_streaming_event(camera_url="rtsp://cam-42/stream"),
        )
        cfg = mocks["realtime"].start_alert.await_args.args[0]
        assert cfg.live_stream_url == "rtsp://cam-42/stream"

    def test_camera_name_from_event_populates_sensor_name(
        self, client, mocks, always_on
    ):
        """`camera_name` on the event is the sole source of `sensor_name`."""
        always_on([_sample_rule()])
        client.post(
            "/api/v1/realtime/always-on",
            json=_streaming_event(camera_name="warehouse"),
        )
        cfg = mocks["realtime"].start_alert.await_args.args[0]
        assert cfg.sensor_name == "warehouse"

    def test_yaml_params_forwarded_to_alert_rule_config(
        self, client, mocks, always_on
    ):
        """All core always_on_params fields (including new extended ones) are
        forwarded verbatim into AlertRuleConfig."""
        always_on([{
            "rule_id": "r1",
            "alert_type": "fire",
            "always_on_params": {
                "prompt": "detect fire",
                "system_prompt": "be concise",
                "model": "the-model",
                "chunk_duration": 60,
                "chunk_overlap_duration": 10,
                "num_frames_per_second_or_fixed_frames_chunk": 7,
                "use_fps_for_chunking": False,
                "vlm_input_width": 512,
                "vlm_input_height": 512,
                "enable_reasoning": False,
            },
        }])
        client.post("/api/v1/realtime/always-on", json=_streaming_event())

        cfg = mocks["realtime"].start_alert.await_args.args[0]
        assert cfg.alert_type == "fire"
        assert cfg.prompt == "detect fire"
        assert cfg.system_prompt == "be concise"
        assert cfg.model == "the-model"
        assert cfg.chunk_duration == 60
        assert cfg.chunk_overlap_duration == 10
        assert cfg.num_frames_per_second_or_fixed_frames_chunk == 7
        assert cfg.use_fps_for_chunking is False
        assert cfg.vlm_input_width == 512
        assert cfg.vlm_input_height == 512
        assert cfg.enable_reasoning is False
        # Optional extended fields default to None when not in the YAML
        assert cfg.api_type is None
        assert cfg.max_tokens is None
        assert cfg.temperature is None

    def test_extended_yaml_params_forwarded_to_alert_rule_config(
        self, client, mocks, always_on
    ):
        """All 12 extended RTVI VLM fields set in always_on_params YAML are
        forwarded verbatim into AlertRuleConfig so they reach generate_captions."""
        always_on([{
            "rule_id": "r1",
            "alert_type": "ppe",
            "always_on_params": {
                "prompt": "detect PPE violations",
                "system_prompt": "You are a safety assistant",
                "model": "cosmos-reason1",
                "api_type": "internal",
                "response_format": {"type": "text"},
                "stream_options": {"include_usage": True},
                "max_tokens": 512,
                "temperature": 0.2,
                "top_p": 1.0,
                "top_k": 100,
                "ignore_eos": True,
                "seed": 42,
                "media_info": {"type": "offset", "start_offset": 0, "end_offset": 4000000000},
                "enable_audio": True,
                "mm_processor_kwargs": {"additionalProp1": {}},
            },
        }])
        client.post("/api/v1/realtime/always-on", json=_streaming_event())

        cfg = mocks["realtime"].start_alert.await_args.args[0]
        assert cfg.api_type == "internal"
        assert cfg.response_format == {"type": "text"}
        assert cfg.stream_options == {"include_usage": True}
        assert cfg.max_tokens == 512
        assert cfg.temperature == 0.2
        assert cfg.top_p == 1.0
        assert cfg.top_k == 100
        assert cfg.ignore_eos is True
        assert cfg.seed == 42
        assert cfg.media_info == {"type": "offset", "start_offset": 0, "end_offset": 4000000000}
        assert cfg.enable_audio is True
        assert cfg.mm_processor_kwargs == {"additionalProp1": {}}

    def test_missing_system_prompt_rejected_at_config_load(
        self, client, mocks, always_on
    ):
        """``system_prompt`` is now required in always_on_params YAML.

        Omitting it triggers a CONFIG_ERROR at load time (Pydantic rejects the
        missing required field) rather than silently falling back to "".
        """
        always_on([{
            "rule_id": "r1",
            "alert_type": "fire",
            "always_on_params": {"prompt": "detect fire", "model": "m"},
        }])
        resp = client.post("/api/v1/realtime/always-on", json=_streaming_event())
        assert resp.status_code == 503
        assert resp.json()["reason"] == "CONFIG_ERROR"
        mocks["realtime"].start_alert.assert_not_awaited()

    def test_missing_model_rejected_at_config_load(
        self, client, mocks, always_on
    ):
        """``model`` is now required in always_on_params YAML.

        Omitting it triggers a CONFIG_ERROR at load time rather than
        silently falling back to the global rtvi_vlm.default_model.
        """
        always_on([{
            "rule_id": "r1",
            "alert_type": "fire",
            "always_on_params": {"prompt": "detect fire", "system_prompt": ""},
        }])
        resp = client.post("/api/v1/realtime/always-on", json=_streaming_event())
        assert resp.status_code == 503
        assert resp.json()["reason"] == "CONFIG_ERROR"
        mocks["realtime"].start_alert.assert_not_awaited()

    def test_unknown_always_on_params_rejected_at_config_load(
        self, client, mocks, always_on
    ):
        """Unknown/misspelled keys fail loudly at config load — no silent drop."""
        always_on([{
            "rule_id": "r1",
            "alert_type": "fire",
            "always_on_params": {
                "prompt": "detect fire",
                "modle": "typo",                   # common typo for `model`
                "made_up_knob": True,              # unknown key
            },
        }])
        resp = client.post("/api/v1/realtime/always-on", json=_streaming_event())
        assert resp.status_code == 503
        assert resp.json()["reason"] == "CONFIG_ERROR"
        mocks["realtime"].start_alert.assert_not_awaited()

    def test_sensor_name_in_always_on_params_rejected_at_config_load(
        self, client, mocks, always_on
    ):
        """`sensor_name` is derived from the event's `camera_name`; putting it in
        YAML params is a user mistake and must fail loudly, matching how we
        treat `live_stream_url` / `alert_type`.
        """
        always_on([{
            "rule_id": "r1",
            "alert_type": "fire",
            "always_on_params": {
                "prompt": "detect fire",
                "sensor_name": "forbidden",
            },
        }])
        resp = client.post("/api/v1/realtime/always-on", json=_streaming_event())
        assert resp.status_code == 503
        assert resp.json()["reason"] == "CONFIG_ERROR"
        mocks["realtime"].start_alert.assert_not_awaited()

    def test_live_stream_url_in_always_on_params_rejected_at_config_load(
        self, client, mocks, always_on
    ):
        """`live_stream_url` comes from the event's `camera_url`; also forbidden in params."""
        always_on([{
            "rule_id": "r1",
            "alert_type": "fire",
            "always_on_params": {
                "prompt": "detect fire",
                "live_stream_url": "rtsp://sneaky/override",
            },
        }])
        resp = client.post("/api/v1/realtime/always-on", json=_streaming_event())
        assert resp.status_code == 503
        assert resp.json()["reason"] == "CONFIG_ERROR"
        mocks["realtime"].start_alert.assert_not_awaited()

    def test_alert_type_in_always_on_params_rejected_at_config_load(
        self, client, mocks, always_on
    ):
        """`alert_type` must come from the rule entry, not from `always_on_params`."""
        always_on([{
            "rule_id": "r1",
            "alert_type": "fire",
            "always_on_params": {
                "prompt": "detect fire",
                "alert_type": "override",
            },
        }])
        resp = client.post("/api/v1/realtime/always-on", json=_streaming_event())
        assert resp.status_code == 503
        assert resp.json()["reason"] == "CONFIG_ERROR"
        mocks["realtime"].start_alert.assert_not_awaited()

    def test_wrong_type_in_always_on_params_rejected_at_config_load(
        self, client, mocks, always_on
    ):
        """A string where an int is expected fails Pydantic type validation."""
        always_on([{
            "rule_id": "r1",
            "alert_type": "fire",
            "always_on_params": {
                "prompt": "detect fire",
                "chunk_duration": "thirty-seconds",
            },
        }])
        resp = client.post("/api/v1/realtime/always-on", json=_streaming_event())
        assert resp.status_code == 503
        assert resp.json()["reason"] == "CONFIG_ERROR"
        mocks["realtime"].start_alert.assert_not_awaited()

    def test_extra_rule_entry_key_rejected_at_config_load(
        self, client, mocks, always_on
    ):
        """`AlwaysOnRuleEntry` uses `extra="forbid"`; typos at the rule level fail."""
        always_on([{
            "rule_id": "r1",
            "alert_type": "fire",
            "enabled": True,                           # unknown top-level key
            "always_on_params": {"prompt": "detect fire"},
        }])
        resp = client.post("/api/v1/realtime/always-on", json=_streaming_event())
        assert resp.status_code == 503
        assert resp.json()["reason"] == "CONFIG_ERROR"
        mocks["realtime"].start_alert.assert_not_awaited()

    def test_empty_alert_type_rejected_at_config_load(
        self, client, mocks, always_on
    ):
        """Whitespace-only `alert_type` is caught by the non-blank field validator."""
        always_on([{
            "rule_id": "r1",
            "alert_type": "   ",
            "always_on_params": {"prompt": "detect fire"},
        }])
        resp = client.post("/api/v1/realtime/always-on", json=_streaming_event())
        assert resp.status_code == 503
        assert resp.json()["reason"] == "CONFIG_ERROR"
        mocks["realtime"].start_alert.assert_not_awaited()

    def test_missing_rule_id_rejected_at_config_load(
        self, client, mocks, always_on
    ):
        """Rules without `rule_id` used to collapse to the same `<unnamed>` label."""
        always_on([{
            "alert_type": "fire",
            "always_on_params": {"prompt": "detect fire"},
        }])
        resp = client.post("/api/v1/realtime/always-on", json=_streaming_event())
        assert resp.status_code == 503
        assert resp.json()["reason"] == "CONFIG_ERROR"
        mocks["realtime"].start_alert.assert_not_awaited()

    def test_duplicate_rule_id_rejected_at_config_load(
        self, client, mocks, always_on
    ):
        """Duplicate config-level rule IDs would make per-rule sidecar
        dedupe ambiguous, so the YAML loader must reject them before any
        RTVI call is made.
        """
        always_on([
            _sample_rule(rule_id="duplicate", alert_type="fire"),
            _sample_rule(rule_id="duplicate", alert_type="collision"),
        ])
        resp = client.post("/api/v1/realtime/always-on", json=_streaming_event())
        assert resp.status_code == 503
        assert resp.json()["reason"] == "CONFIG_ERROR"
        mocks["realtime"].start_alert.assert_not_awaited()

    def test_empty_rule_id_rejected_at_config_load(
        self, client, mocks, always_on
    ):
        """An empty-string `rule_id` is as useless as a missing one."""
        always_on([{
            "rule_id": "   ",
            "alert_type": "fire",
            "always_on_params": {"prompt": "detect fire"},
        }])
        resp = client.post("/api/v1/realtime/always-on", json=_streaming_event())
        assert resp.status_code == 503
        assert resp.json()["reason"] == "CONFIG_ERROR"
        mocks["realtime"].start_alert.assert_not_awaited()

    def test_missing_alert_type_rejected_at_config_load(
        self, client, mocks, always_on
    ):
        """Rules without `alert_type` used to silently default to "always_on"."""
        always_on([{
            "rule_id": "r1",
            "always_on_params": {"prompt": "detect fire"},
        }])
        resp = client.post("/api/v1/realtime/always-on", json=_streaming_event())
        assert resp.status_code == 503
        assert resp.json()["reason"] == "CONFIG_ERROR"
        mocks["realtime"].start_alert.assert_not_awaited()

    def test_rule_without_prompt_rejected_at_config_load(
        self, client, mocks, always_on
    ):
        """A rule with no ``prompt`` field is a config error (Pydantic rejects
        it as a missing required key). SDR never reaches `start_alert` and
        gets `503 CONFIG_ERROR` back.
        """
        always_on([{
            "rule_id": "r1",
            "alert_type": "fire",
            "always_on_params": {},
        }])
        resp = client.post("/api/v1/realtime/always-on", json=_streaming_event())
        assert resp.status_code == 503
        assert resp.json()["reason"] == "CONFIG_ERROR"
        mocks["realtime"].start_alert.assert_not_awaited()

    def test_rule_with_empty_prompt_still_rejected_at_runtime(
        self, client, mocks, always_on
    ):
        """An *empty-string* prompt passes Pydantic's `str` type check (no
        min_length), but the fan-out loop still guards against it and
        returns 502 so callers don't send a blank prompt to the VLM.

        The per-rule entry in `details` records the failure with a
        `message` field (as opposed to a raw upstream `error` dict,
        since no RTVI call was made).
        """
        always_on([{
            "rule_id": "r1",
            "alert_type": "fire",
            "always_on_params": {"prompt": "", "system_prompt": "", "model": "m"},
        }])
        resp = client.post("/api/v1/realtime/always-on", json=_streaming_event())
        assert resp.status_code == 502
        body = resp.json()
        assert body["reason"] == "STREAM_ADD_FAILED"
        assert body["details"] == [{
            "rule_id": "r1",
            "alert_type": "fire",
            "status": 422,
            "result": "error",
            "message": "always_on_params.prompt is required",
        }]
        mocks["realtime"].start_alert.assert_not_awaited()

    def test_always_on_params_must_be_mapping(self, client, mocks, always_on):
        """A non-dict always_on_params is rejected at config load, not per-rule."""
        always_on([{
            "rule_id": "r1",
            "alert_type": "fire",
            "always_on_params": "not-a-dict",
        }])
        resp = client.post("/api/v1/realtime/always-on", json=_streaming_event())
        assert resp.status_code == 503
        assert resp.json()["reason"] == "CONFIG_ERROR"
        mocks["realtime"].start_alert.assert_not_awaited()

    def test_full_success_returns_success_with_details(self, client, mocks, always_on):
        """100% success → 200 STREAM_ADD_SUCCESS, with one success entry per rule."""
        always_on([_sample_rule("r1"), _sample_rule("r2")])
        mocks["realtime"].start_alert.side_effect = [
            ({"status": "success", "id": "alert-1", "created_at": "T", "message": "ok"}, 201),
            ({"status": "success", "id": "alert-2", "created_at": "T", "message": "ok"}, 201),
        ]
        resp = client.post("/api/v1/realtime/always-on", json=_streaming_event())
        assert resp.status_code == 200
        body = resp.json()
        assert body["reason"] == "STREAM_ADD_SUCCESS"
        assert [d["rule_id"] for d in body["details"]] == ["r1", "r2"]
        assert all(d["result"] == "success" for d in body["details"])
        assert [d["alert_rule_id"] for d in body["details"]] == ["alert-1", "alert-2"]

    def test_partial_success_returns_partial_success_with_details(
        self, client, mocks, always_on
    ):
        """1+ success AND 1+ failure → 200 STREAM_ADD_PARTIAL_SUCCESS.

        Status code stays 200 so SDR does not retry the whole camera
        (which would double-spawn the already-running rules), but the
        new `reason` + per-rule `details` array makes the partial
        outcome visible to callers.
        """
        always_on([_sample_rule("r1"), _sample_rule("r2")])
        mocks["realtime"].start_alert.side_effect = [
            ({"status": "success", "id": "alert-1", "created_at": "T", "message": "ok"}, 201),
            ({"status": "error", "error": "rtvi_vlm_unavailable", "message": "bad", "timestamp": "T"}, 502),
        ]
        resp = client.post("/api/v1/realtime/always-on", json=_streaming_event())
        assert resp.status_code == 200
        body = resp.json()
        assert body["reason"] == "STREAM_ADD_PARTIAL_SUCCESS"
        assert len(body["details"]) == 2
        # First rule succeeded.
        assert body["details"][0] == {
            "rule_id": "r1",
            "alert_type": "collision",
            "status": 201,
            "result": "success",
            "alert_rule_id": "alert-1",
        }
        # Second rule failed — upstream error is attached under `error`.
        assert body["details"][1]["rule_id"] == "r2"
        assert body["details"][1]["result"] == "error"
        assert body["details"][1]["status"] == 502
        assert body["details"][1]["error"]["error"] == "rtvi_vlm_unavailable"

    def test_all_rules_fail_returns_502_with_details(self, client, mocks, always_on):
        """0% success → 502 STREAM_ADD_FAILED, with one error entry per rule."""
        always_on([_sample_rule("r1"), _sample_rule("r2")])
        mocks["realtime"].start_alert.return_value = (
            {"status": "error", "error": "rtvi_vlm_unavailable", "message": "no", "timestamp": "T"},
            502,
        )
        resp = client.post("/api/v1/realtime/always-on", json=_streaming_event())
        assert resp.status_code == 502
        body = resp.json()
        assert body["reason"] == "STREAM_ADD_FAILED"
        assert len(body["details"]) == 2
        assert all(d["result"] == "error" for d in body["details"])
        assert all(d["error"]["error"] == "rtvi_vlm_unavailable" for d in body["details"])

    def test_mixed_error_modes_in_same_response(self, client, mocks, always_on):
        """Two rules failing for *different* reasons — one rule-level validation
        (empty prompt, no upstream call) and one upstream 502 — surface
        alongside each other in the details array with the right per-entry
        shape: `message` on the validation failure, `error` dict on the
        upstream failure. Both failing → STREAM_ADD_FAILED.
        """
        always_on([
            {
                "rule_id": "r1",
                "alert_type": "fire",
                "always_on_params": {"prompt": "", "system_prompt": "", "model": "m"},    # rule-level error
            },
            _sample_rule("r2", alert_type="collision"),  # reaches service
        ])
        mocks["realtime"].start_alert.return_value = (
            {"status": "error", "error": "rtvi_vlm_unavailable", "message": "no", "timestamp": "T"},
            502,
        )
        resp = client.post("/api/v1/realtime/always-on", json=_streaming_event())
        assert resp.status_code == 502
        body = resp.json()
        assert body["reason"] == "STREAM_ADD_FAILED"
        # r1: rule-level 422 with `message`; never reached the service.
        assert body["details"][0] == {
            "rule_id": "r1",
            "alert_type": "fire",
            "status": 422,
            "result": "error",
            "message": "always_on_params.prompt is required",
        }
        # r2: upstream 502 with `error` dict attached.
        assert body["details"][1]["rule_id"] == "r2"
        assert body["details"][1]["status"] == 502
        assert body["details"][1]["result"] == "error"
        assert body["details"][1]["error"]["error"] == "rtvi_vlm_unavailable"
        assert "message" not in body["details"][1]
        # Only r2 reached start_alert.
        assert mocks["realtime"].start_alert.await_count == 1

    def test_partial_failure_recovers_on_next_streaming_event(
        self, client, mocks, always_on
    ):
        """P1 #B: a rule that failed on the first fan-out must be retried on
        the next camera_streaming event (not deduped at the camera level).

        The sidecar tracks which *config rule_ids* are already active; a
        replay fires start_alert only for the ones that are missing.
        """
        always_on([_sample_rule("r1"), _sample_rule("r2")])
        mocks["realtime"].start_alert.side_effect = [
            # 1st event: r1 succeeds, r2 fails.
            ({"status": "success", "id": "alert-1", "created_at": "T", "message": "ok"}, 201),
            ({"status": "error", "error": "rtvi_vlm_unavailable", "message": "flaky", "timestamp": "T"}, 502),
            # 2nd event (replay): r2 succeeds this time.
            ({"status": "success", "id": "alert-2", "created_at": "T", "message": "ok"}, 201),
        ]
        resp1 = client.post("/api/v1/realtime/always-on", json=_streaming_event())
        assert resp1.json()["reason"] == "STREAM_ADD_PARTIAL_SUCCESS"

        resp2 = client.post("/api/v1/realtime/always-on", json=_streaming_event())
        assert resp2.status_code == 200
        body = resp2.json()
        assert body["reason"] == "STREAM_ADD_SUCCESS"
        # r1 was already running so it's marked already_active (no
        # start_alert call); r2 is a new success.
        assert body["details"][0]["rule_id"] == "r1"
        assert body["details"][0]["result"] == "already_active"
        assert body["details"][0]["alert_rule_id"] == "alert-1"
        assert body["details"][1]["rule_id"] == "r2"
        assert body["details"][1]["result"] == "success"
        assert body["details"][1]["alert_rule_id"] == "alert-2"
        # Exactly three start_alert calls total: r1+r2 on first event,
        # r2 alone on replay. r1 is NOT re-started.
        assert mocks["realtime"].start_alert.await_count == 3

    def test_readiness_failure_surfaces_and_retries(self, client, mocks, always_on):
        """When start_alert returns 502 with rtvi_stream_not_readable (inline
        readiness check caught an unreadable RTSP source), the failure shows
        up in details and the rule is retried on the next streaming event.
        """
        always_on([_sample_rule("r1"), _sample_rule("r2")])
        mocks["realtime"].start_alert.side_effect = [
            # 1st event: r1 succeeds, r2 fails readiness.
            ({"status": "success", "id": "alert-1", "created_at": "T", "message": "ok"}, 201),
            ({"status": "error", "error": "rtvi_stream_not_readable",
              "message": "Stream failed readiness check: connection refused", "timestamp": "T"}, 502),
            # 2nd event: r2 retried and now succeeds.
            ({"status": "success", "id": "alert-2", "created_at": "T", "message": "ok"}, 201),
        ]

        resp1 = client.post("/api/v1/realtime/always-on", json=_streaming_event())
        assert resp1.status_code == 200
        body1 = resp1.json()
        assert body1["reason"] == "STREAM_ADD_PARTIAL_SUCCESS"
        # r1 succeeded.
        assert body1["details"][0]["rule_id"] == "r1"
        assert body1["details"][0]["result"] == "success"
        # r2 failed with the readiness error code.
        assert body1["details"][1]["rule_id"] == "r2"
        assert body1["details"][1]["result"] == "error"
        assert body1["details"][1]["status"] == 502
        assert body1["details"][1]["error"]["error"] == "rtvi_stream_not_readable"

        # Next streaming event retries only r2 (r1 is already active).
        resp2 = client.post("/api/v1/realtime/always-on", json=_streaming_event())
        assert resp2.status_code == 200
        body2 = resp2.json()
        assert body2["reason"] == "STREAM_ADD_SUCCESS"
        assert body2["details"][0]["rule_id"] == "r1"
        assert body2["details"][0]["result"] == "already_active"
        assert body2["details"][1]["rule_id"] == "r2"
        assert body2["details"][1]["result"] == "success"
        assert mocks["realtime"].start_alert.await_count == 3

    def test_all_rules_already_active_returns_already_active(
        self, client, mocks, always_on
    ):
        """Replay where *every* configured rule is already active returns the
        short-circuit STREAM_ADD_ALREADY_ACTIVE without touching details.
        """
        always_on([_sample_rule("r1"), _sample_rule("r2")])
        mocks["realtime"].start_alert.side_effect = [
            ({"status": "success", "id": f"alert-{i}", "created_at": "T", "message": "ok"}, 201)
            for i in (1, 2)
        ]
        client.post("/api/v1/realtime/always-on", json=_streaming_event())

        resp = client.post("/api/v1/realtime/always-on", json=_streaming_event())
        assert resp.status_code == 200
        assert resp.json()["reason"] == "STREAM_ADD_ALREADY_ACTIVE"
        assert "details" not in resp.json()
        # Second call never reached the fan-out — short-circuit under the lock.
        assert mocks["realtime"].start_alert.await_count == 2

    def test_partial_failure_replay_still_partial_if_same_rule_fails_again(
        self, client, mocks, always_on
    ):
        """If the same rule keeps failing on replays, status stays 200
        STREAM_ADD_PARTIAL_SUCCESS (the other rule remains active) and only
        the failing rule gets retried on each replay.
        """
        always_on([_sample_rule("r1"), _sample_rule("r2")])
        mocks["realtime"].start_alert.side_effect = [
            ({"status": "success", "id": "alert-1", "created_at": "T", "message": "ok"}, 201),
            ({"status": "error", "error": "x", "message": "flaky", "timestamp": "T"}, 502),
            ({"status": "error", "error": "x", "message": "still flaky", "timestamp": "T"}, 502),
        ]
        client.post("/api/v1/realtime/always-on", json=_streaming_event())
        resp = client.post("/api/v1/realtime/always-on", json=_streaming_event())

        assert resp.status_code == 200
        assert resp.json()["reason"] == "STREAM_ADD_PARTIAL_SUCCESS"
        # Third start_alert call was for r2 only (retry); r1 was skipped
        # via already_active dedupe.
        assert mocks["realtime"].start_alert.await_count == 3

    def test_partial_success_three_way_mixed_outcomes(
        self, client, mocks, always_on
    ):
        """A single PARTIAL_SUCCESS response can carry all three detail
        `result` kinds (`already_active` + `success` + `error`) when a
        replay fills in a gap for one rule while another rule is still
        failing and a third was already running.
        """
        always_on([_sample_rule("r1"), _sample_rule("r2"), _sample_rule("r3")])
        mocks["realtime"].start_alert.side_effect = [
            # 1st event: r1 succeeds, r2 fails, r3 fails.
            ({"status": "success", "id": "alert-1", "created_at": "T", "message": "ok"}, 201),
            ({"status": "error", "error": "x", "message": "b", "timestamp": "T"}, 502),
            ({"status": "error", "error": "x", "message": "c", "timestamp": "T"}, 502),
            # Replay: r2 now succeeds, r3 still fails.
            ({"status": "success", "id": "alert-2", "created_at": "T", "message": "ok"}, 201),
            ({"status": "error", "error": "x", "message": "c", "timestamp": "T"}, 502),
        ]
        client.post("/api/v1/realtime/always-on", json=_streaming_event())
        resp = client.post("/api/v1/realtime/always-on", json=_streaming_event())

        assert resp.status_code == 200
        body = resp.json()
        assert body["reason"] == "STREAM_ADD_PARTIAL_SUCCESS"
        by_rule = {d["rule_id"]: d for d in body["details"]}
        # r1 was committed to the sidecar on the first event and is
        # still there — skipped as already_active on the replay.
        assert by_rule["r1"]["result"] == "already_active"
        assert by_rule["r1"]["alert_rule_id"] == "alert-1"
        # r2 was retried and succeeded this time.
        assert by_rule["r2"]["result"] == "success"
        assert by_rule["r2"]["alert_rule_id"] == "alert-2"
        # r3 was retried and failed again — persistent upstream error.
        assert by_rule["r3"]["result"] == "error"
        assert by_rule["r3"]["error"]["error"] == "x"

    def test_partial_success_empty_prompt_plus_already_active(
        self, client, mocks, always_on
    ):
        """PARTIAL_SUCCESS also applies when there is NO newly-started
        rule on this request — a rule-level error combined with an
        already-running rule still counts as "something active" so we
        return 200 (not 502), but the presence of the error demotes
        STREAM_ADD_SUCCESS to STREAM_ADD_PARTIAL_SUCCESS.
        """
        always_on([
            {
                "rule_id": "r1",
                "alert_type": "fire",
                "always_on_params": {"prompt": "", "system_prompt": "", "model": "m"},    # rule-level error
            },
            _sample_rule("r2"),                         # will succeed first time
        ])
        mocks["realtime"].start_alert.return_value = (
            {"status": "success", "id": "alert-2", "created_at": "T", "message": "ok"},
            201,
        )

        # First event: r1 errors (empty prompt, never reaches service),
        # r2 succeeds. => PARTIAL_SUCCESS with 1 error + 1 success.
        resp1 = client.post("/api/v1/realtime/always-on", json=_streaming_event())
        assert resp1.json()["reason"] == "STREAM_ADD_PARTIAL_SUCCESS"
        assert mocks["realtime"].start_alert.await_count == 1  # only r2

        # Replay: r1 still errors (empty prompt), r2 is now already_active.
        # No rule was newly started, but r2 is live → active_count=1
        # and errors=1, so reason is still PARTIAL_SUCCESS.
        resp2 = client.post("/api/v1/realtime/always-on", json=_streaming_event())
        assert resp2.status_code == 200
        body = resp2.json()
        assert body["reason"] == "STREAM_ADD_PARTIAL_SUCCESS"
        by_rule = {d["rule_id"]: d for d in body["details"]}
        assert by_rule["r1"]["result"] == "error"
        assert by_rule["r1"]["message"] == "always_on_params.prompt is required"
        assert by_rule["r2"]["result"] == "already_active"
        # start_alert was NOT called a second time for r2 (already_active
        # dedupe), and was never called for r1 (empty-prompt short-
        # circuit reaches the service). Still just 1 start_alert call
        # since first run.
        assert mocks["realtime"].start_alert.await_count == 1

    def test_partial_success_status_line_is_200_ok(
        self, client, mocks, always_on
    ):
        """Every PARTIAL_SUCCESS response must carry `"HTTP/1.1 200 OK"`
        in its `status` field — the status code is the on-the-wire
        contract SDR uses to decide whether to retry.
        """
        always_on([_sample_rule("r1"), _sample_rule("r2")])
        mocks["realtime"].start_alert.side_effect = [
            ({"status": "success", "id": "alert-1", "created_at": "T", "message": "ok"}, 201),
            ({"status": "error", "error": "x", "message": "bad", "timestamp": "T"}, 502),
        ]
        resp = client.post("/api/v1/realtime/always-on", json=_streaming_event())
        body = resp.json()
        assert body["reason"] == "STREAM_ADD_PARTIAL_SUCCESS"
        assert body["status"] == "HTTP/1.1 200 OK"

    def test_partial_success_commits_only_the_successful_rules(
        self, client, mocks, always_on
    ):
        """After a partial success the sidecar carries ONLY the rules
        that succeeded this round — a subsequent camera_remove stops
        exactly those, and a subsequent camera_streaming re-drives
        only the failed ones. This is the invariant P1 #B depends on.
        """
        always_on([_sample_rule("r1"), _sample_rule("r2")])
        mocks["realtime"].start_alert.side_effect = [
            ({"status": "success", "id": "alert-1", "created_at": "T", "message": "ok"}, 201),
            ({"status": "error", "error": "x", "message": "bad", "timestamp": "T"}, 502),
        ]
        resp = client.post("/api/v1/realtime/always-on", json=_streaming_event())
        assert resp.json()["reason"] == "STREAM_ADD_PARTIAL_SUCCESS"

        # A camera_remove now stops exactly r1 (the one that succeeded)
        # — no stop_alert for r2's (nonexistent) alert_rule_id.
        mocks["realtime"].stop_alert.return_value = (
            {"status": "success", "id": "alert-1", "message": "ok"}, 200,
        )
        resp = client.post("/api/v1/realtime/always-on", json=_remove_event())
        assert resp.status_code == 200
        assert resp.json()["reason"] == "STREAM_REMOVE_SUCCESS"
        stopped_ids = [
            call.args[0] for call in mocks["realtime"].stop_alert.await_args_list
        ]
        assert stopped_ids == ["alert-1"]

    def test_empty_prompt_rule_paired_with_success_is_partial_success(
        self, client, mocks, always_on
    ):
        """A rule-level failure (empty prompt) counts as an error for the
        success/partial/failure tri-state. Paired with a successful rule
        it produces 200 STREAM_ADD_PARTIAL_SUCCESS.
        """
        always_on([
            {
                "rule_id": "r1",
                "alert_type": "fire",
                "always_on_params": {"prompt": "", "system_prompt": "", "model": "m"},    # rule-level error
            },
            _sample_rule("r2"),                         # will succeed
        ])
        mocks["realtime"].start_alert.return_value = (
            {"status": "success", "id": "alert-2", "created_at": "T", "message": "ok"},
            201,
        )
        resp = client.post("/api/v1/realtime/always-on", json=_streaming_event())
        assert resp.status_code == 200
        body = resp.json()
        assert body["reason"] == "STREAM_ADD_PARTIAL_SUCCESS"
        assert body["details"][0]["result"] == "error"
        assert body["details"][0]["message"] == "always_on_params.prompt is required"
        assert body["details"][1]["result"] == "success"
        assert body["details"][1]["alert_rule_id"] == "alert-2"


class TestAlwaysOnDedupe:
    """POST /api/v1/realtime/always-on — idempotency for duplicate camera_streaming.

    SDR retries camera_streaming on any transient failure, so the endpoint
    must dedupe per-camera instead of spawning a fresh set of RTVI sessions
    per retry.
    """

    def test_duplicate_streaming_for_same_camera_is_idempotent(
        self, client, mocks, always_on
    ):
        always_on([_sample_rule("r1"), _sample_rule("r2")])
        mocks["realtime"].start_alert.side_effect = [
            ({"status": "success", "id": f"rule-{i}", "created_at": "T", "message": "ok"}, 201)
            for i in (1, 2)
        ]

        first = client.post("/api/v1/realtime/always-on", json=_streaming_event())
        second = client.post("/api/v1/realtime/always-on", json=_streaming_event())

        assert first.status_code == 200
        assert first.json()["reason"] == "STREAM_ADD_SUCCESS"
        assert second.status_code == 200
        assert second.json()["reason"] == "STREAM_ADD_ALREADY_ACTIVE"
        assert mocks["realtime"].start_alert.await_count == 2  # no fan-out on retry

    def test_streaming_for_different_cameras_runs_independently(
        self, client, mocks, always_on
    ):
        always_on([_sample_rule("r1")])
        mocks["realtime"].start_alert.side_effect = [
            ({"status": "success", "id": f"rule-{cam}", "created_at": "T", "message": "ok"}, 201)
            for cam in ("A", "B")
        ]

        resp_a = client.post(
            "/api/v1/realtime/always-on",
            json=_streaming_event(camera_id="cam-A"),
        )
        resp_b = client.post(
            "/api/v1/realtime/always-on",
            json=_streaming_event(camera_id="cam-B"),
        )

        assert resp_a.status_code == 200
        assert resp_a.json()["reason"] == "STREAM_ADD_SUCCESS"
        assert resp_b.status_code == 200
        assert resp_b.json()["reason"] == "STREAM_ADD_SUCCESS"
        assert mocks["realtime"].start_alert.await_count == 2

    def test_streaming_after_remove_succeeds_again(
        self, client, mocks, always_on
    ):
        """Once a camera is removed it can be re-added without hitting dedupe."""
        always_on([_sample_rule("r1")])
        mocks["realtime"].start_alert.return_value = (
            {"status": "success", "id": "rule-1", "created_at": "T", "message": "ok"},
            201,
        )
        mocks["realtime"].stop_alert.return_value = (
            {"status": "success", "id": "rule-1", "message": "deleted"},
            200,
        )

        client.post("/api/v1/realtime/always-on", json=_streaming_event())
        client.post("/api/v1/realtime/always-on", json=_remove_event())
        resp = client.post("/api/v1/realtime/always-on", json=_streaming_event())

        assert resp.status_code == 200
        assert resp.json()["reason"] == "STREAM_ADD_SUCCESS"
        assert mocks["realtime"].start_alert.await_count == 2


class TestAlwaysOnFeatureFlag:
    """POST /api/v1/realtime/always-on — `alert_agent.always_on` gate."""

    def test_flag_false_returns_503_always_on_disabled(
        self, client, mocks, monkeypatch, always_on_service, tmp_path
    ):
        """Without `alert_agent.always_on: true` the endpoint short-circuits."""
        # Explicitly write a config.yaml with the flag disabled and point
        # CONFIG_PATH at it. This overrides whatever the `always_on`
        # fixture would have set (which is `true`).
        config_path = tmp_path / "config.yaml"
        config_path.write_text(yaml.safe_dump({"alert_agent": {"always_on": False}}))
        monkeypatch.setenv("CONFIG_PATH", str(config_path))
        always_on_service.reset()

        resp = client.post("/api/v1/realtime/always-on", json=_streaming_event())

        assert resp.status_code == 503
        assert resp.json()["reason"] == "ALWAYS_ON_DISABLED"
        mocks["realtime"].start_alert.assert_not_awaited()

    def test_flag_missing_defaults_to_disabled(
        self, client, mocks, monkeypatch, always_on_service, tmp_path
    ):
        """Omitting the flag entirely is equivalent to `false`."""
        config_path = tmp_path / "config.yaml"
        config_path.write_text(yaml.safe_dump({"alert_agent": {}}))
        monkeypatch.setenv("CONFIG_PATH", str(config_path))
        always_on_service.reset()

        resp = client.post("/api/v1/realtime/always-on", json=_streaming_event())

        assert resp.status_code == 503
        assert resp.json()["reason"] == "ALWAYS_ON_DISABLED"
        mocks["realtime"].start_alert.assert_not_awaited()

    def test_startup_validator_noop_when_disabled(
        self, monkeypatch, always_on_service, tmp_path
    ):
        """With the flag off, startup validation is a no-op even if no rules
        config is present — the feature is simply not running.
        """
        config_path = tmp_path / "config.yaml"
        config_path.write_text(yaml.safe_dump({"alert_agent": {"always_on": False}}))
        monkeypatch.setenv("CONFIG_PATH", str(config_path))
        monkeypatch.delenv("ALWAYS_ON_RULES_CONFIG", raising=False)
        always_on_service.reset()

        # Should NOT raise despite ALWAYS_ON_RULES_CONFIG being unset.
        always_on_service.validate_config_at_startup()

    def test_startup_validator_raises_when_enabled_and_config_broken(
        self, monkeypatch, always_on_service, tmp_path
    ):
        """With the flag on and a broken rules config, startup validation
        raises — this is how app boot is supposed to fail in deployment.
        """
        from realtime import AlwaysOnRulesConfigError

        config_path = tmp_path / "config.yaml"
        config_path.write_text(yaml.safe_dump({"alert_agent": {"always_on": True}}))
        monkeypatch.setenv("CONFIG_PATH", str(config_path))
        monkeypatch.delenv("ALWAYS_ON_RULES_CONFIG", raising=False)
        always_on_service.reset()

        with pytest.raises(AlwaysOnRulesConfigError):
            always_on_service.validate_config_at_startup()

    def test_startup_validator_success_when_enabled_and_config_valid(
        self, monkeypatch, always_on_service, tmp_path
    ):
        """With the flag on and a valid rules YAML, startup validation succeeds."""
        config_path = tmp_path / "config.yaml"
        config_path.write_text(yaml.safe_dump({"alert_agent": {"always_on": True}}))
        monkeypatch.setenv("CONFIG_PATH", str(config_path))
        rules_path = tmp_path / "rules.yaml"
        rules_path.write_text(yaml.safe_dump({"always_on_rules": [_sample_rule()]}))
        monkeypatch.setenv("ALWAYS_ON_RULES_CONFIG", str(rules_path))
        always_on_service.reset()

        always_on_service.validate_config_at_startup()  # must not raise


class TestAlwaysOnConfigErrors:
    """POST /api/v1/realtime/always-on — YAML config resolution failures.

    These tests take the ``always_on`` fixture (which enables
    ``alert_agent.always_on: true``) but override ALWAYS_ON_RULES_CONFIG
    directly so the rules-load path fails — verifying that the feature
    gate and the rules-config gate are independent.
    """

    def test_env_unset_returns_503(
        self, client, always_on, monkeypatch, always_on_service
    ):
        always_on_service.reset()
        monkeypatch.delenv("ALWAYS_ON_RULES_CONFIG", raising=False)
        resp = client.post("/api/v1/realtime/always-on", json=_streaming_event())
        assert resp.status_code == 503
        assert resp.json()["reason"] == "CONFIG_ERROR"

    def test_missing_yaml_file_returns_503(
        self, client, always_on, monkeypatch, tmp_path, always_on_service
    ):
        always_on_service.reset()
        monkeypatch.setenv("ALWAYS_ON_RULES_CONFIG", str(tmp_path / "nope.yaml"))
        resp = client.post("/api/v1/realtime/always-on", json=_streaming_event())
        assert resp.status_code == 503
        assert resp.json()["reason"] == "CONFIG_ERROR"

    def test_malformed_yaml_returns_503(
        self, client, always_on, monkeypatch, tmp_path, always_on_service
    ):
        always_on_service.reset()
        bad = tmp_path / "bad.yaml"
        bad.write_text("always_on_rules: [unclosed")
        monkeypatch.setenv("ALWAYS_ON_RULES_CONFIG", str(bad))
        resp = client.post("/api/v1/realtime/always-on", json=_streaming_event())
        assert resp.status_code == 503
        assert resp.json()["reason"] == "CONFIG_ERROR"

    def test_empty_rules_list_returns_503(
        self, client, always_on, monkeypatch, tmp_path, always_on_service
    ):
        always_on_service.reset()
        empty = tmp_path / "empty.yaml"
        empty.write_text(yaml.safe_dump({"always_on_rules": []}))
        monkeypatch.setenv("ALWAYS_ON_RULES_CONFIG", str(empty))
        resp = client.post("/api/v1/realtime/always-on", json=_streaming_event())
        assert resp.status_code == 503
        assert resp.json()["reason"] == "CONFIG_ERROR"

    def test_missing_rules_key_returns_503(
        self, client, always_on, monkeypatch, tmp_path, always_on_service
    ):
        always_on_service.reset()
        no_key = tmp_path / "no-key.yaml"
        no_key.write_text(yaml.safe_dump({"other_key": []}))
        monkeypatch.setenv("ALWAYS_ON_RULES_CONFIG", str(no_key))
        resp = client.post("/api/v1/realtime/always-on", json=_streaming_event())
        assert resp.status_code == 503

    def test_shipped_sample_yaml_loads_cleanly(
        self, client, mocks, always_on, monkeypatch, always_on_service
    ):
        """`realtime-config-sample.yaml` is the ground truth for the schema.

        If someone renames a key in the loader without updating the sample
        (or vice versa), this test catches it before it hits users.
        """
        import os
        sample_path = os.path.abspath(
            os.path.join(
                os.path.dirname(__file__), "..", "..",
                "realtime-config-sample.yaml",
            )
        )
        assert os.path.exists(sample_path), (
            f"sample config missing at {sample_path}"
        )
        always_on_service.reset()
        monkeypatch.setenv("ALWAYS_ON_RULES_CONFIG", sample_path)
        resp = client.post("/api/v1/realtime/always-on", json=_streaming_event())
        assert resp.status_code == 200, resp.json()
        assert resp.json()["reason"] == "STREAM_ADD_SUCCESS"
        assert mocks["realtime"].start_alert.await_count >= 1


class TestAlwaysOnDetailsOmittedOnShortCircuit:
    """Short-circuit responses never reach the fan-out loop, so they must not
    include a `details` key — callers rely on the key's presence/absence to
    tell fan-out outcomes from pre-fan-out rejections apart.
    """

    def test_always_on_disabled_omits_details(
        self, client, monkeypatch, always_on_service, tmp_path
    ):
        config_path = tmp_path / "config.yaml"
        config_path.write_text(yaml.safe_dump({"alert_agent": {"always_on": False}}))
        monkeypatch.setenv("CONFIG_PATH", str(config_path))
        always_on_service.reset()
        resp = client.post("/api/v1/realtime/always-on", json=_streaming_event())
        assert resp.status_code == 503
        assert resp.json()["reason"] == "ALWAYS_ON_DISABLED"
        assert "details" not in resp.json()

    def test_invalid_payload_omits_details(self, client, always_on):
        always_on([_sample_rule()])
        resp = client.post("/api/v1/realtime/always-on", json={})
        assert resp.status_code == 422
        assert resp.json()["reason"] == "INVALID_PAYLOAD"
        assert "details" not in resp.json()

    def test_config_error_omits_details(
        self, client, always_on, monkeypatch, always_on_service
    ):
        """Rules-config error bounces before any rule is touched."""
        always_on_service.reset()
        monkeypatch.delenv("ALWAYS_ON_RULES_CONFIG", raising=False)
        resp = client.post("/api/v1/realtime/always-on", json=_streaming_event())
        assert resp.status_code == 503
        assert resp.json()["reason"] == "CONFIG_ERROR"
        assert "details" not in resp.json()

    def test_already_active_dedupe_omits_details(
        self, client, mocks, always_on
    ):
        """The dedupe short-circuit runs before the fan-out loop, so it has
        no per-rule results to report.
        """
        always_on([_sample_rule("r1")])
        mocks["realtime"].start_alert.return_value = (
            {"status": "success", "id": "alert-1", "created_at": "T", "message": "ok"},
            201,
        )
        client.post("/api/v1/realtime/always-on", json=_streaming_event())
        resp = client.post("/api/v1/realtime/always-on", json=_streaming_event())
        assert resp.status_code == 200
        body = resp.json()
        assert body["reason"] == "STREAM_ADD_ALREADY_ACTIVE"
        assert "details" not in body


class TestAlwaysOnCameraRemove:
    """POST /api/v1/realtime/always-on — camera_remove teardown."""

    def test_remove_after_streaming_stops_tracked_rules(
        self, client, mocks, always_on
    ):
        always_on([_sample_rule("r1"), _sample_rule("r2")])
        mocks["realtime"].start_alert.side_effect = [
            ({"status": "success", "id": f"rule-{i}", "created_at": "T", "message": "ok"}, 201)
            for i in (1, 2)
        ]
        client.post("/api/v1/realtime/always-on", json=_streaming_event())

        mocks["realtime"].stop_alert.return_value = (
            {"status": "success", "id": "x", "message": "deleted"},
            200,
        )
        resp = client.post("/api/v1/realtime/always-on", json=_remove_event())

        assert resp.status_code == 200
        body = resp.json()
        assert body["reason"] == "STREAM_REMOVE_SUCCESS"
        # Every tracked rule has a success entry in the details array,
        # carrying both the config `rule_id` and the service `alert_rule_id`
        # so callers can correlate to either identifier.
        assert sorted(body["details"], key=lambda d: d["alert_rule_id"]) == [
            {"rule_id": "r1", "alert_rule_id": "rule-1", "status": 200, "result": "success"},
            {"rule_id": "r2", "alert_rule_id": "rule-2", "status": 200, "result": "success"},
        ]
        stopped_ids = {
            call.args[0] for call in mocks["realtime"].stop_alert.await_args_list
        }
        assert stopped_ids == {"rule-1", "rule-2"}

    def test_remove_unknown_camera_is_noop_success(self, client, mocks, always_on):
        """camera_remove for a camera that was never streamed is a clean no-op."""
        always_on([_sample_rule("r1")])
        resp = client.post(
            "/api/v1/realtime/always-on",
            json=_remove_event(camera_id="never-added"),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["reason"] == "STREAM_REMOVE_SUCCESS"
        # No tracked rules → no per-rule details but the array is still present
        # (empty) so clients don't need to special-case its absence.
        assert body["details"] == []
        mocks["realtime"].stop_alert.assert_not_awaited()

    def test_remove_details_include_per_rule_outcomes(
        self, client, mocks, always_on
    ):
        """Remove responses carry a details array matching each stop_alert call."""
        always_on([_sample_rule("r1"), _sample_rule("r2"), _sample_rule("r3")])
        mocks["realtime"].start_alert.side_effect = [
            ({"status": "success", "id": f"rule-{i}", "created_at": "T", "message": "ok"}, 201)
            for i in (1, 2, 3)
        ]
        client.post("/api/v1/realtime/always-on", json=_streaming_event())

        mocks["realtime"].stop_alert.side_effect = [
            ({"status": "success", "id": "rule-1", "message": "ok"}, 200),
            ({"status": "error", "error": "not_found", "message": "gone", "timestamp": "T"}, 404),
            (
                {"status": "error", "error": "rtvi_vlm_unavailable", "message": "no", "timestamp": "T"},
                502,
            ),
        ]
        resp = client.post("/api/v1/realtime/always-on", json=_remove_event())

        # Mixed outcome: one clean, one already-gone, one hard failure → 502.
        assert resp.status_code == 502
        body = resp.json()
        assert body["reason"] == "STREAM_REMOVE_FAILED"
        assert [d["alert_rule_id"] for d in body["details"]] == [
            "rule-1", "rule-2", "rule-3",
        ]
        assert body["details"][0]["status"] == 200
        assert body["details"][0]["result"] == "success"
        assert body["details"][1]["status"] == 404
        assert body["details"][1]["result"] == "success"     # idempotent
        assert "already removed" in body["details"][1]["message"]
        assert body["details"][2]["status"] == 502
        assert body["details"][2]["result"] == "error"
        assert body["details"][2]["error"]["error"] == "rtvi_vlm_unavailable"

    def test_remove_missing_camera_id_returns_422(self, client, mocks, always_on):
        """`camera_remove` with no camera_id is still rejected at validation time."""
        always_on([_sample_rule("r1")])
        event = _remove_event()
        event["event"].pop("camera_id")
        resp = client.post("/api/v1/realtime/always-on", json=event)
        assert resp.status_code == 422
        assert resp.json()["reason"] == "INVALID_PAYLOAD"
        mocks["realtime"].stop_alert.assert_not_awaited()

    def test_remove_with_unexpected_stop_failure_returns_502(
        self, client, mocks, always_on
    ):
        """A non-{200,404} response from stop_alert is surfaced as 502."""
        always_on([_sample_rule("r1"), _sample_rule("r2")])
        mocks["realtime"].start_alert.side_effect = [
            ({"status": "success", "id": f"rule-{i}", "created_at": "T", "message": "ok"}, 201)
            for i in (1, 2)
        ]
        client.post("/api/v1/realtime/always-on", json=_streaming_event())

        mocks["realtime"].stop_alert.side_effect = [
            ({"status": "success", "id": "rule-1", "message": "ok"}, 200),
            ({"status": "error", "error": "rtvi_vlm_unavailable", "message": "no", "timestamp": "T"}, 502),
        ]
        resp = client.post("/api/v1/realtime/always-on", json=_remove_event())

        assert resp.status_code == 502
        assert resp.json()["reason"] == "STREAM_REMOVE_FAILED"

    def test_remove_partial_failure_retains_only_failing_rule_ids(
        self, client, mocks, always_on
    ):
        """On partial failure, the sidecar must keep only the still-failing
        rule ids so SDR's retry re-drives *just* those — not all rules and
        not nothing. We prove this end-to-end via the stop_alert call
        history across two consecutive remove requests (black-box; the
        sidecar identity isn't stable across the test-client re-import
        dance so direct dict inspection is unreliable here).
        """
        always_on([_sample_rule("r1"), _sample_rule("r2"), _sample_rule("r3")])
        mocks["realtime"].start_alert.side_effect = [
            ({"status": "success", "id": f"rule-{i}", "created_at": "T", "message": "ok"}, 201)
            for i in (1, 2, 3)
        ]
        client.post("/api/v1/realtime/always-on", json=_streaming_event())

        # 1st & 3rd stop cleanly; 2nd fails with a real 502.
        mocks["realtime"].stop_alert.side_effect = [
            ({"status": "success", "id": "rule-1", "message": "ok"}, 200),
            ({"status": "error", "error": "rtvi_vlm_unavailable", "message": "no", "timestamp": "T"}, 502),
            ({"status": "success", "id": "rule-3", "message": "ok"}, 200),
        ]
        resp = client.post("/api/v1/realtime/always-on", json=_remove_event())

        assert resp.status_code == 502
        assert resp.json()["reason"] == "STREAM_REMOVE_FAILED"
        # First remove: all three rules were attempted.
        first_round_ids = [
            call.args[0] for call in mocks["realtime"].stop_alert.await_args_list
        ]
        assert first_round_ids == ["rule-1", "rule-2", "rule-3"]

        # Now simulate SDR's retry: the failing rule's stop_alert succeeds.
        mocks["realtime"].stop_alert.reset_mock(side_effect=True)
        mocks["realtime"].stop_alert.return_value = (
            {"status": "success", "id": "rule-2", "message": "ok"},
            200,
        )
        resp = client.post("/api/v1/realtime/always-on", json=_remove_event())

        assert resp.status_code == 200
        assert resp.json()["reason"] == "STREAM_REMOVE_SUCCESS"
        # Retry re-drove ONLY rule-2 — not the two that already succeeded.
        # This is the proof that the sidecar retained `[rule-2]` after the
        # partial failure (rather than being cleared, which would have
        # produced zero stop_alert calls).
        retried_ids = [
            call.args[0] for call in mocks["realtime"].stop_alert.await_args_list
        ]
        assert retried_ids == ["rule-2"]

        # A third remove is now a clean no-op: the sidecar has been popped
        # because the retry drained it successfully.
        mocks["realtime"].stop_alert.reset_mock(side_effect=True)
        mocks["realtime"].stop_alert.return_value = (
            {"status": "success", "id": "x", "message": "ok"},
            200,
        )
        resp = client.post("/api/v1/realtime/always-on", json=_remove_event())
        assert resp.status_code == 200
        assert resp.json()["reason"] == "STREAM_REMOVE_SUCCESS"
        mocks["realtime"].stop_alert.assert_not_awaited()

    def test_remove_treats_already_gone_404_as_idempotent_success(
        self, client, mocks, always_on
    ):
        """stop_alert returning 404 means the rule is already gone — that is
        exactly the end-state camera_remove wants, so it must not 502.
        """
        always_on([_sample_rule("r1"), _sample_rule("r2")])
        mocks["realtime"].start_alert.side_effect = [
            ({"status": "success", "id": f"rule-{i}", "created_at": "T", "message": "ok"}, 201)
            for i in (1, 2)
        ]
        client.post("/api/v1/realtime/always-on", json=_streaming_event())

        mocks["realtime"].stop_alert.side_effect = [
            ({"status": "success", "id": "rule-1", "message": "ok"}, 200),
            (
                {"status": "error", "error": "not_found", "message": "no rule 'rule-2'", "timestamp": "T"},
                404,
            ),
        ]
        resp = client.post("/api/v1/realtime/always-on", json=_remove_event())

        assert resp.status_code == 200
        assert resp.json()["reason"] == "STREAM_REMOVE_SUCCESS"

    def test_remove_all_404_returns_success_with_per_rule_notes(
        self, client, mocks, always_on
    ):
        """Every rule returning 404 is still a clean success — every entry in
        the details array is marked `result: "success"` with an
        `"already removed"` message, not an error."""
        always_on([_sample_rule("r1"), _sample_rule("r2")])
        mocks["realtime"].start_alert.side_effect = [
            ({"status": "success", "id": f"rule-{i}", "created_at": "T", "message": "ok"}, 201)
            for i in (1, 2)
        ]
        client.post("/api/v1/realtime/always-on", json=_streaming_event())

        mocks["realtime"].stop_alert.side_effect = [
            ({"status": "error", "error": "not_found", "message": f"no rule-{i}", "timestamp": "T"}, 404)
            for i in (1, 2)
        ]
        resp = client.post("/api/v1/realtime/always-on", json=_remove_event())

        assert resp.status_code == 200
        body = resp.json()
        assert body["reason"] == "STREAM_REMOVE_SUCCESS"
        assert len(body["details"]) == 2
        for entry in body["details"]:
            assert entry["status"] == 404
            assert entry["result"] == "success"
            assert "already removed" in entry["message"]
            assert "error" not in entry

    def test_remove_consumes_tracked_rules_on_success(
        self, client, mocks, always_on
    ):
        """After a successful remove, a second remove for the same camera is a no-op."""
        always_on([_sample_rule("r1")])
        mocks["realtime"].start_alert.return_value = (
            {"status": "success", "id": "rule-1", "created_at": "T", "message": "ok"},
            201,
        )
        mocks["realtime"].stop_alert.return_value = (
            {"status": "success", "id": "rule-1", "message": "deleted"},
            200,
        )

        client.post("/api/v1/realtime/always-on", json=_streaming_event())
        client.post("/api/v1/realtime/always-on", json=_remove_event())

        mocks["realtime"].stop_alert.reset_mock()
        resp = client.post("/api/v1/realtime/always-on", json=_remove_event())

        assert resp.status_code == 200
        assert resp.json()["reason"] == "STREAM_REMOVE_SUCCESS"
        mocks["realtime"].stop_alert.assert_not_awaited()


# ---------------------------------------------------------------------------
# Concurrency correctness
# ---------------------------------------------------------------------------

class TestAlwaysOnConcurrency:
    """Tests that can't be expressed as back-to-back TestClient requests
    because they need two coroutines in flight simultaneously. These
    invoke the route handler directly under ``@pytest.mark.asyncio`` and
    drive coordination through the :class:`AlwaysOnService` instance's
    sidecar + flight-done Event map.
    """

    @pytest.mark.asyncio
    async def test_remove_during_in_flight_streaming_awaits_and_tears_down(
        self, always_on, routes_module, monkeypatch
    ):
        """P1 #A: a camera_remove that arrives while the matching
        camera_streaming fan-out is mid-flight must NOT snapshot an empty
        rule set and return success — it must wait for the add-path to
        commit, then stop every rule that was actually created.

        Proof: schedule both handlers as tasks, hold start_alert until
        both are running, assert that stop_alert is only invoked AFTER
        the add-path has committed, and assert it's invoked with the
        UUID(s) the add-path created.
        """
        from realtime import AlwaysOnService

        always_on([_sample_rule("r1")])

        # Dedicated mock RealtimeAlertService whose start_alert signals
        # when it has entered the await (so the test can move on
        # deterministically) and then blocks on a second event so the
        # test controls when the add-path finishes. stop_alert is
        # instant-success.
        realtime_mock = AsyncMock()
        entered = asyncio.Event()
        release = asyncio.Event()

        async def _blocking_start_alert(cfg):
            entered.set()
            await release.wait()
            return (
                {"status": "success", "id": "alert-1",
                 "created_at": "T", "message": "ok"},
                201,
            )
        realtime_mock.start_alert.side_effect = _blocking_start_alert
        realtime_mock.stop_alert.return_value = (
            {"status": "success", "id": "alert-1", "message": "ok"}, 200,
        )

        # Fresh AlwaysOnService wrapping the blocking mock. It'll load
        # rules from the YAML the ``always_on`` fixture pointed the env
        # var at.
        svc = AlwaysOnService(realtime_service=realtime_mock)

        streaming_payload = _streaming_event()
        remove_payload = _remove_event()

        add_task = asyncio.create_task(
            routes_module.always_on_realtime(streaming_payload, svc)
        )
        # Wait until the add-path has passed the in-flight reservation
        # and is awaiting inside the mocked start_alert.
        await asyncio.wait_for(entered.wait(), timeout=5)
        assert "cam-1" in svc._in_flight
        assert "cam-1" in svc._flight_done

        remove_task = asyncio.create_task(
            routes_module.always_on_realtime(remove_payload, svc)
        )
        # Yield enough times for remove to reach the flight-done wait.
        for _ in range(20):
            await asyncio.sleep(0)

        # CRITICAL: stop_alert must not have been called yet — remove is
        # blocked waiting for the add-path to commit.
        realtime_mock.stop_alert.assert_not_awaited()
        assert not add_task.done()
        assert not remove_task.done()

        # Release the add-path. Both tasks drain.
        release.set()
        add_resp = await add_task
        remove_resp = await remove_task

        # Streaming succeeded.
        add_body = json.loads(add_resp.body.decode())
        assert add_resp.status_code == 200
        assert add_body["reason"] == "STREAM_ADD_SUCCESS"

        # Remove waited for the commit and then stopped the rule that
        # was actually created — *not* the empty set it would have seen
        # if it snapshotted mid-flight.
        remove_body = json.loads(remove_resp.body.decode())
        assert remove_resp.status_code == 200
        assert remove_body["reason"] == "STREAM_REMOVE_SUCCESS"
        realtime_mock.stop_alert.assert_awaited_once_with("alert-1")

        # Sidecar fully drained — no stranded rules.
        assert "cam-1" not in svc._camera_rules
        assert "cam-1" not in svc._in_flight
        assert "cam-1" not in svc._flight_done

    @pytest.mark.asyncio
    async def test_duplicate_streaming_during_in_flight_waits_then_dedupes(
        self, always_on, routes_module
    ):
        """A duplicate camera_streaming request that arrives while the first
        add fan-out is still running must wait for the first commit before
        deciding whether another RTVI call is needed.
        """
        from realtime import AlwaysOnService

        always_on([_sample_rule("r1")])

        realtime_mock = AsyncMock()
        entered = asyncio.Event()
        release = asyncio.Event()

        async def _blocking_start_alert(cfg):
            entered.set()
            await release.wait()
            return (
                {"status": "success", "id": "alert-1",
                 "created_at": "T", "message": "ok"},
                201,
            )

        realtime_mock.start_alert.side_effect = _blocking_start_alert
        svc = AlwaysOnService(realtime_service=realtime_mock)

        first_task = asyncio.create_task(
            routes_module.always_on_realtime(_streaming_event(), svc)
        )
        await asyncio.wait_for(entered.wait(), timeout=5)
        assert "cam-1" in svc._in_flight

        second_task = asyncio.create_task(
            routes_module.always_on_realtime(_streaming_event(), svc)
        )
        for _ in range(20):
            await asyncio.sleep(0)

        # The duplicate request is waiting on the first flight-done
        # event; it has not launched a second start_alert call.
        assert realtime_mock.start_alert.await_count == 1
        assert not first_task.done()
        assert not second_task.done()

        release.set()
        first_resp = await first_task
        second_resp = await second_task

        first_body = json.loads(first_resp.body.decode())
        second_body = json.loads(second_resp.body.decode())
        assert first_resp.status_code == 200
        assert first_body["reason"] == "STREAM_ADD_SUCCESS"
        assert second_resp.status_code == 200
        assert second_body["reason"] == "STREAM_ADD_ALREADY_ACTIVE"
        assert "details" not in second_body
        assert realtime_mock.start_alert.await_count == 1

    @pytest.mark.asyncio
    async def test_duplicate_streaming_during_in_flight_retries_after_failure(
        self, always_on, routes_module
    ):
        """If the first in-flight add fails completely, a duplicate
        camera_streaming request waiting behind it should re-evaluate and
        make its own start attempt instead of returning ALREADY_ACTIVE.
        """
        from realtime import AlwaysOnService

        always_on([_sample_rule("r1")])

        realtime_mock = AsyncMock()
        entered = asyncio.Event()
        release = asyncio.Event()
        call_count = 0

        async def _start_alert(cfg):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                entered.set()
                await release.wait()
                return (
                    {"status": "error", "error": "rtvi_vlm_unavailable",
                     "message": "down", "timestamp": "T"},
                    502,
                )
            return (
                {"status": "success", "id": "alert-2",
                 "created_at": "T", "message": "ok"},
                201,
            )

        realtime_mock.start_alert.side_effect = _start_alert
        svc = AlwaysOnService(realtime_service=realtime_mock)

        first_task = asyncio.create_task(
            routes_module.always_on_realtime(_streaming_event(), svc)
        )
        await asyncio.wait_for(entered.wait(), timeout=5)
        second_task = asyncio.create_task(
            routes_module.always_on_realtime(_streaming_event(), svc)
        )
        for _ in range(20):
            await asyncio.sleep(0)
        assert realtime_mock.start_alert.await_count == 1

        release.set()
        first_resp = await first_task
        second_resp = await second_task

        first_body = json.loads(first_resp.body.decode())
        second_body = json.loads(second_resp.body.decode())
        assert first_resp.status_code == 502
        assert first_body["reason"] == "STREAM_ADD_FAILED"
        assert second_resp.status_code == 200
        assert second_body["reason"] == "STREAM_ADD_SUCCESS"
        assert second_body["details"][0]["alert_rule_id"] == "alert-2"
        assert realtime_mock.start_alert.await_count == 2
