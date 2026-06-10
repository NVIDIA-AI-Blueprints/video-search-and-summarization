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


def test_internal_error_returns_snakecase_envelope(client):
    # Smoke env has no real DB, so /sensor/list hits a DB error -> the global handler must render
    # the snake_case VMSInternalError envelope (HTTP 500), never a stack trace or 422 shape.
    r = client.get("/api/v1/sensor/list")
    assert r.status_code == 500
    body = r.json()
    assert set(body) == {"error_code", "error_message"}
    assert body["error_code"] == "VMSInternalError"


def test_mutating_endpoint_passes_through_without_token(client):
    # Verified C++ parity: scaled sensor-ms does not enforce bearer auth (multi-user off).
    # POST /sensor/scan (a no-op that doesn't touch the DB) succeeds with no Authorization header.
    r = client.post("/api/v1/sensor/scan")
    assert r.status_code == 200


def test_full_route_surface_registered():
    paths = {route.path for route in app.routes}
    for expected in [
        "/api/v1/sensor/list", "/api/v1/sensor/add", "/api/v1/sensor/{sensor_id}",
        "/api/v1/sensor/{sensor_id}/credentials", "/api/v1/sensor/{sensor_id}/network",
        "/api/v1/sensor/scan", "/api/v1/sensor/configuration", "/v1/ready",
    ]:
        assert expected in paths, f"missing route {expected}"
