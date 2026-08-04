# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for required-field validation on the protobuf incident branch.

The HTTP JSON endpoint validates required fields via
``IncidentSubmissionRequest``. Before this change the ``application/x-protobuf``
branch parsed the proto and published to Kafka with no equivalent check, so an
incident sent as protobuf without ``end`` (or another required field) was
accepted and flowed downstream unvalidated. These tests lock down that the
protobuf branch now enforces the same contract as the JSON branch.
"""

import asyncio
import json
import logging
from unittest.mock import Mock

import pytest

from web.core.alert_service import AlertSubmissionService
from utils.schema_util import convert_incident_to_protobuf_incident


def _service():
    """Build an ``AlertSubmissionService`` without running ``__init__`` (which
    loads config and wires a real Kafka producer). Validation returns before
    the producer is used, so a mock producer is enough for the happy path."""
    svc = object.__new__(AlertSubmissionService)
    svc.logger = logging.getLogger("test-alert-service")
    svc.kafka_producer = Mock()
    svc.kafka_incident_topic = "mdx-incidents"
    return svc


def _incident_bytes(**overrides):
    payload = {
        "sensorId": "cam-1",
        "timestamp": "2026-05-12T00:00:00Z",
        "end": "2026-05-12T00:00:30Z",
        "category": "collision",
    }
    payload.update(overrides)
    # A ``None`` override means "omit this field" (simulate a missing field).
    for key in [key for key, value in list(payload.items()) if value is None]:
        del payload[key]
    return convert_incident_to_protobuf_incident(payload).SerializeToString()


def test_valid_protobuf_incident_is_accepted():
    svc = _service()

    response, status = asyncio.run(
        svc.submit_nvschema_incident_protobuf(_incident_bytes())
    )

    assert status == 202
    assert response["status"] == "accepted"
    svc.kafka_producer.produce.assert_called_once()
    svc.kafka_producer.flush.assert_called_once()


@pytest.mark.parametrize("field", ["sensorId", "timestamp", "end", "category"])
def test_missing_required_field_is_rejected(field):
    svc = _service()

    response, status = asyncio.run(
        svc.submit_nvschema_incident_protobuf(_incident_bytes(**{field: None}))
    )

    assert status == 422
    assert response["error"] == "validation_failed"
    assert any(detail["field"] == field for detail in response["details"])
    # A rejected incident must NOT be published to Kafka.
    svc.kafka_producer.produce.assert_not_called()
    svc.kafka_producer.flush.assert_not_called()


def test_structured_info_list_survives_proto_roundtrip():
    """Mode-3 direct media relies on a ``media_urls`` list round-tripping
    through the protobuf ``map<string, string>`` info field: the converter
    JSON-encodes structured values, and the Mode-3 pass-through branch
    ``json.loads`` them back. Lock that contract so re-tightening ``info`` to
    string-only (which broke Mode-3) is caught here."""
    payload = {
        "sensorId": "cam-1",
        "timestamp": "2026-05-12T00:00:00Z",
        "end": "2026-05-12T00:00:30Z",
        "category": "collision",
        "info": {
            "media_urls": ["http://media-store/a.mp4", "http://media-store/b.mp4"],
            "media_type": "video",
        },
    }

    proto = convert_incident_to_protobuf_incident(payload)

    # The proto map stores strings; the list is JSON-encoded.
    assert isinstance(proto.info["media_urls"], str)
    assert json.loads(proto.info["media_urls"]) == [
        "http://media-store/a.mp4",
        "http://media-store/b.mp4",
    ]
    assert proto.info["media_type"] == "video"


def test_invalid_protobuf_payload_is_rejected():
    svc = _service()

    response, status = asyncio.run(
        svc.submit_nvschema_incident_protobuf(b"\xff\xff not a proto")
    )

    assert status == 400
    assert response["error"] == "invalid_payload"
    svc.kafka_producer.produce.assert_not_called()
