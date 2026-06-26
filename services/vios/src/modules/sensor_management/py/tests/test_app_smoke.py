# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Smoke tests: app imports, routes register, and the three contract behaviors fire."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from sensor_ms.main import app


@pytest.fixture
def client():
    # Context manager runs the lifespan (sets app.state.mgmt, calls start/stop).
    # raise_server_exceptions=False so the global 500 handler's response is returned (as in prod),
    # rather than TestClient re-raising the underlying exception.
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


def test_health_live_ok(client):
    r = client.get("/v1/live")
    assert r.status_code == 200


def test_responses_are_text_plain(client):
    # Contract: JSON body served with Content-Type: text/plain.
    r = client.get("/v1/live")
    assert r.headers["content-type"].startswith("text/plain")


def test_help_lists_endpoints(client):
    r = client.get("/api/v1/sensor/help")
    assert r.status_code == 200
    body = r.json()
    assert "/api/v1/sensor/list" in body


def test_qos_deprecated_null_stats(client):
    r = client.get("/api/v1/sensor/qos")
    assert r.json()["stats"] is None


def test_version_reports_type_and_nonempty_version(client):
    # /version returns the service type (vst/mms) and the release version. The version is injected
    # from the Makefile at image build (SENSOR_MS_VERSION); locally it falls back to the package
    # version, so here we only assert it is present and not the bare "0.1.0" placeholder is allowed.
    r = client.get("/api/v1/sensor/version")
    assert r.status_code == 200
    body = r.json()
    assert body["type"] in ("vst", "mms")
    assert isinstance(body["version"], str) and body["version"]


def test_error_envelope_is_snakecase(client):
    # A VmsError must render as the snake_case envelope (never a 422/stack trace). POST /add with
    # neither sensorUrl nor sensorIp raises InvalidParameterError before any DB access.
    r = client.post("/api/v1/sensor/add", json={"name": "x"})
    assert r.status_code == 400
    body = r.json()
    assert set(body) == {"error_code", "error_message"}
    assert body["error_code"] == "InvalidParameterError"


def test_mutating_endpoint_passes_through_without_token(client):
    # Verified C++ parity: scaled sensor-ms does not enforce bearer auth (multi-user off).
    # POST /sensor/scan (a no-op that doesn't touch the DB) succeeds with no Authorization header.
    r = client.post("/api/v1/sensor/scan")
    assert r.status_code == 200


def test_full_route_surface_registered():
    # Use the OpenAPI schema as the route source (version-stable across FastAPI/Starlette; newer
    # Starlette wraps include_router results in opaque router objects without a flat `.path`).
    paths = set(app.openapi()["paths"])
    for expected in [
        "/api/v1/sensor/list", "/api/v1/sensor/add", "/api/v1/sensor/{sensor_id}",
        "/api/v1/sensor/{sensor_id}/credentials", "/api/v1/sensor/{sensor_id}/network",
        "/api/v1/sensor/scan", "/api/v1/sensor/configuration", "/v1/ready",
        # control-plane APIs completed on top of the read/add/delete core
        "/api/v1/sensor/{sensor_id}/info", "/api/v1/sensor/{sensor_id}/replace",
        "/api/v1/sensor/{sensor_id}/settings", "/api/v1/sensor/{sensor_id}/reboot",
        "/api/v1/sensor/debug/plug", "/api/v1/sensor/debug/unplug", "/api/v1/sensor/debug/status",
    ]:
        assert expected in paths, f"missing route {expected}"
