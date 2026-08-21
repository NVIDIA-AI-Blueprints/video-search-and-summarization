# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for incident submission request validation."""

from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from web.api.incident_routes import get_incident_service, router


@pytest.fixture()
def mock_service():
    service = AsyncMock()
    service.submit_nvschema_incident.return_value = (
        {
            "status": "accepted",
            "id": "",
            "message": "Incident queued for processing",
            "timestamp": "2026-07-27T00:00:00Z",
        },
        202,
    )
    return service


@pytest.fixture()
def client(mock_service):
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_incident_service] = lambda: mock_service
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture()
def valid_incident():
    return {
        "sensorId": "camera-01",
        "timestamp": "2026-05-12T00:00:00Z",
        "end": "2026-05-12T00:00:30Z",
        "category": "FOV Count Violation",
        "info": {"primaryObjectId": "12345", "location": "warehouse"},
    }


def test_valid_incident_is_accepted(client, mock_service, valid_incident):
    response = client.post("/api/v1/incidents", json=valid_incident)

    assert response.status_code == 202
    mock_service.submit_nvschema_incident.assert_awaited_once_with(valid_incident)


def test_non_string_info_values_are_rejected(client, mock_service, valid_incident):
    valid_incident["info"] = {
        "primaryObjectId": 12345,
        "location": True,
        "count": 3.14,
    }

    response = client.post("/api/v1/incidents", json=valid_incident)

    assert response.status_code == 422
    body = response.json()
    assert body["error"] == "validation_failed"
    assert {error["field"] for error in body["details"]} == {
        "info.primaryObjectId",
        "info.location",
        "info.count",
    }
    mock_service.submit_nvschema_incident.assert_not_awaited()


def test_structured_info_values_are_accepted(client, mock_service, valid_incident):
    """Mode-3 direct-media producers send structured ``info`` values (a
    ``media_urls`` list, an object). These must remain accepted — they are
    JSON-encoded into the protobuf string map downstream and decoded back by
    the Mode-3 pass-through branch. Only bare non-string scalars are rejected."""
    valid_incident["info"] = {
        "media_urls": ["http://media-store/a.mp4", "http://media-store/b.mp4"],
        "media_type": "video",
        "location": "warehouse",
        "meta": {"nested": "object"},
    }

    response = client.post("/api/v1/incidents", json=valid_incident)

    assert response.status_code == 202
    mock_service.submit_nvschema_incident.assert_awaited_once_with(valid_incident)


@pytest.mark.parametrize("field", ["sensorId", "timestamp", "end", "category"])
def test_missing_required_field_is_rejected(
    client,
    mock_service,
    valid_incident,
    field,
):
    del valid_incident[field]

    response = client.post("/api/v1/incidents", json=valid_incident)

    assert response.status_code == 422
    body = response.json()
    assert any(error["field"] == field for error in body["details"])
    mock_service.submit_nvschema_incident.assert_not_awaited()


def test_malformed_json_is_rejected(client, mock_service):
    response = client.post(
        "/api/v1/incidents",
        content="not json",
        headers={"content-type": "application/json"},
    )

    assert response.status_code == 422
    assert response.json()["error"] == "validation_failed"
    mock_service.submit_nvschema_incident.assert_not_awaited()
