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

"""Unit tests for ``web.api.alert_routes`` and ``web.api.incident_routes``.

Both routers dispatch on ``Content-Type``: ``application/x-protobuf`` forwards
the raw body to the Protobuf submit method, anything else is parsed as JSON.
Getting that dispatch wrong means a Protobuf body would be JSON-parsed (422)
or a JSON body forwarded as bytes (400), so the branch is covered from both
sides including the header edge cases (casing, whitespace, charset suffix).

The routers are mounted on a bare FastAPI app rather than ``web.main`` so the
tests do not depend on app-wide startup (config load, alert-config store, ES).
The service is injected through FastAPI's dependency override.
"""

from unittest.mock import AsyncMock

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from web.api.alert_routes import get_alert_service, router as alert_router
from web.api.incident_routes import get_incident_service, router as incident_router

ACCEPTED = ({"status": "accepted", "id": "x", "message": "queued"}, 202)


@pytest.fixture
def service():
    svc = AsyncMock()
    svc.submit_nvschema_alert.return_value = ACCEPTED
    svc.submit_nvschema_alert_protobuf.return_value = ACCEPTED
    svc.submit_nvschema_incident.return_value = ACCEPTED
    svc.submit_nvschema_incident_protobuf.return_value = ACCEPTED
    svc.entity_validator = object()
    svc.redis_client = None
    svc.kafka_producer = object()
    return svc


@pytest.fixture
def client(service):
    app = FastAPI()
    app.include_router(alert_router)
    app.include_router(incident_router)
    app.dependency_overrides[get_alert_service] = lambda: service
    app.dependency_overrides[get_incident_service] = lambda: service
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client
    app.dependency_overrides.clear()


class TestSubmitAlert:
    def test_json_body_goes_to_the_json_path(self, client, service):
        response = client.post("/api/v1/alerts", json={"id": "alert-1"})

        assert response.status_code == 202
        service.submit_nvschema_alert.assert_awaited_once_with({"id": "alert-1"})
        service.submit_nvschema_alert_protobuf.assert_not_awaited()

    def test_service_status_code_is_propagated(self, client, service):
        service.submit_nvschema_alert.return_value = (
            {"status": "error", "error": "internal_error"},
            500,
        )
        response = client.post("/api/v1/alerts", json={"id": "alert-1"})

        assert response.status_code == 500
        assert response.json()["error"] == "internal_error"

    def test_protobuf_content_type_goes_to_the_protobuf_path(self, client, service):
        payload = b"\x0a\x07alert-1"
        response = client.post(
            "/api/v1/alerts",
            content=payload,
            headers={"content-type": "application/x-protobuf"},
        )

        assert response.status_code == 202
        service.submit_nvschema_alert_protobuf.assert_awaited_once_with(payload)
        service.submit_nvschema_alert.assert_not_awaited()

    @pytest.mark.parametrize(
        "content_type", ["APPLICATION/X-PROTOBUF", " application/x-protobuf "]
    )
    def test_protobuf_content_type_matching_is_lenient(self, client, service, content_type):
        client.post(
            "/api/v1/alerts", content=b"\x0a\x01x", headers={"content-type": content_type}
        )
        service.submit_nvschema_alert_protobuf.assert_awaited_once()

    def test_protobuf_content_type_with_parameters_is_not_matched(self, client, service):
        """An exact match is required, so a charset suffix falls to the JSON path."""
        response = client.post(
            "/api/v1/alerts",
            content=b"\x0a\x01x",
            headers={"content-type": "application/x-protobuf; charset=utf-8"},
        )

        assert response.status_code == 422
        service.submit_nvschema_alert_protobuf.assert_not_awaited()

    def test_malformed_json_returns_422(self, client, service):
        response = client.post(
            "/api/v1/alerts",
            content=b"{not json",
            headers={"content-type": "application/json"},
        )

        assert response.status_code == 422
        body = response.json()
        assert body["error"] == "validation_failed"
        assert body["message"] == "Request body must be valid JSON"
        assert body["timestamp"].endswith("Z")
        service.submit_nvschema_alert.assert_not_awaited()

    def test_empty_body_returns_422(self, client, service):
        response = client.post(
            "/api/v1/alerts", content=b"", headers={"content-type": "application/json"}
        )
        assert response.status_code == 422

    def test_missing_content_type_defaults_to_the_json_path(self, client, service):
        response = client.post("/api/v1/alerts", content=b'{"id": "alert-1"}')

        assert response.status_code == 202
        service.submit_nvschema_alert.assert_awaited_once_with({"id": "alert-1"})

    def test_json_array_body_is_forwarded_as_is(self, client, service):
        """The route does not enforce a shape; the service decides."""
        client.post("/api/v1/alerts", json=[{"id": "alert-1"}])
        service.submit_nvschema_alert.assert_awaited_once_with([{"id": "alert-1"}])


class TestAlertSubmissionHealth:
    def test_healthy_when_validator_and_kafka_are_present(self, client):
        response = client.get("/api/v1/alerts/health")

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "healthy"
        assert body["service"] == "alert-submission"
        assert body["components"] == {"entity_validator": "ok", "event_bridge": "ok"}

    def test_unhealthy_when_no_event_bridge_is_configured(self, client, service):
        service.kafka_producer = None
        response = client.get("/api/v1/alerts/health")

        assert response.status_code == 503
        body = response.json()
        assert body["status"] == "unhealthy"
        assert body["components"]["event_bridge"] == "error"
        assert "error" in body

    def test_redis_client_is_probed_when_present(self, client, service):
        service.redis_client = AsyncMock()
        service.redis_client.ping = lambda: True

        response = client.get("/api/v1/alerts/health")

        assert response.status_code == 200
        assert response.json()["components"]["event_bridge"] == "ok"

    def test_failing_redis_ping_reports_unhealthy(self, client, service):
        service.redis_client = AsyncMock()
        service.redis_client.ping = lambda: False

        response = client.get("/api/v1/alerts/health")

        assert response.status_code == 503
        assert response.json()["components"]["event_bridge"] == "error"

    def test_raising_redis_ping_reports_unhealthy(self, client, service):
        service.redis_client = AsyncMock()

        def boom():
            raise RuntimeError("connection reset")

        service.redis_client.ping = boom

        response = client.get("/api/v1/alerts/health")

        assert response.status_code == 503
        assert response.json()["components"]["event_bridge"] == "error"

    def test_unhealthy_when_validator_is_missing(self, client, service):
        service.entity_validator = None
        response = client.get("/api/v1/alerts/health")

        assert response.status_code == 503
        assert response.json()["components"]["entity_validator"] == "error"


class TestSubmitIncident:
    def test_json_body_goes_to_the_json_path(self, client, service):
        response = client.post("/api/v1/incidents", json={"id": "inc-1"})

        assert response.status_code == 202
        service.submit_nvschema_incident.assert_awaited_once_with({"id": "inc-1"})
        service.submit_nvschema_incident_protobuf.assert_not_awaited()

    def test_protobuf_content_type_goes_to_the_protobuf_path(self, client, service):
        payload = b"\x0a\x05cam-1"
        client.post(
            "/api/v1/incidents",
            content=payload,
            headers={"content-type": "application/x-protobuf"},
        )
        service.submit_nvschema_incident_protobuf.assert_awaited_once_with(payload)

    def test_protobuf_content_type_matching_is_lenient(self, client, service):
        client.post(
            "/api/v1/incidents",
            content=b"\x0a\x01x",
            headers={"content-type": "APPLICATION/X-PROTOBUF "},
        )
        service.submit_nvschema_incident_protobuf.assert_awaited_once()

    def test_malformed_json_returns_422(self, client, service):
        response = client.post(
            "/api/v1/incidents",
            content=b"{not json",
            headers={"content-type": "application/json"},
        )

        assert response.status_code == 422
        assert response.json()["error"] == "validation_failed"
        service.submit_nvschema_incident.assert_not_awaited()

    def test_invalid_protobuf_status_is_propagated(self, client, service):
        service.submit_nvschema_incident_protobuf.return_value = (
            {"status": "error", "error": "invalid_payload"},
            400,
        )
        response = client.post(
            "/api/v1/incidents",
            content=b"\xff\xfe",
            headers={"content-type": "application/x-protobuf"},
        )

        assert response.status_code == 400
        assert response.json()["error"] == "invalid_payload"


class TestServiceDependencies:
    """The routers memoise one service instance per process."""

    def test_alert_service_is_created_once(self, monkeypatch):
        import web.api.alert_routes as module

        monkeypatch.setattr(module, "_alert_service", None)
        created = []

        class FakeService:
            def __init__(self):
                created.append(self)

        monkeypatch.setattr(module, "AlertSubmissionService", FakeService)

        first = module.get_alert_service()
        second = module.get_alert_service()

        assert first is second
        assert len(created) == 1

    def test_incident_service_is_created_once(self, monkeypatch):
        import web.api.incident_routes as module

        monkeypatch.setattr(module, "_incident_service", None)
        created = []

        class FakeService:
            def __init__(self):
                created.append(self)

        monkeypatch.setattr(module, "AlertSubmissionService", FakeService)

        first = module.get_incident_service()
        second = module.get_incident_service()

        assert first is second
        assert len(created) == 1


class TestGetRawBody:
    def test_returns_the_decoded_request_body(self, service):
        from web.api.alert_routes import get_raw_body

        app = FastAPI()

        @app.post("/echo")
        async def echo(raw: str = Depends(get_raw_body)):
            return {"raw": raw}

        with TestClient(app) as test_client:
            response = test_client.post("/echo", content=b'{"id": "alert-1"}')

        assert response.json()["raw"] == '{"id": "alert-1"}'
