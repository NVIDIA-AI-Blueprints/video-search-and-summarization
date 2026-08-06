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

"""Unit tests for ``web.core.alert_service``.

``AlertSubmissionService`` is the HTTP ingest path: it converts a submitted
alert/incident (JSON or Protobuf) into an nvSchema message and produces it to
the configured Kafka topic. Four behaviours are load-bearing:

* Kafka setup failure must not abort construction — the service still starts
  and only fails at submit time, so the HTTP server stays up.
* The Kafka message key is derived with a documented fallback chain; a wrong
  key silently changes partitioning and breaks per-sensor ordering.
* A malformed Protobuf body maps to 400, everything else to 500 — the JSON
  paths have no 400 branch because conversion errors are not distinguishable.
* Errors never leak internals to the caller.
"""

from unittest.mock import MagicMock, patch

import pytest

from mdx.protobuf import Behavior as nvSchemaBehavior, Incident as nvSchemaIncident
from utils.schema_util import convert_incident_to_protobuf_incident
from web.core.alert_service import AlertSubmissionService

KAFKA_CONFIG = {
    "event_bridge": {
        "sourceType": "kafka",
        "kafka_source": {"topics": {"alert": "mdx-alerts", "incident": "mdx-incidents"}},
    }
}


def valid_incident_bytes(**overrides):
    """Serialized incident satisfying the required-field contract the protobuf
    branch enforces (``sensorId``, ``timestamp``, ``end``, ``category``); a
    payload missing any of them is rejected with 422 before the Kafka produce."""
    payload = {
        "sensorId": "cam-1",
        "timestamp": "2026-05-12T00:00:00Z",
        "end": "2026-05-12T00:00:30Z",
        "category": "collision",
    }
    payload.update(overrides)
    return convert_incident_to_protobuf_incident(payload).SerializeToString()


def make_service(config=None):
    """Build a service with ``load_config`` and the Kafka broker patched out."""
    with patch(
        "utils.config.load_config", return_value=KAFKA_CONFIG if config is None else config
    ), patch("web.core.alert_service.KafkaMessageBroker") as broker_cls:
        broker_cls.return_value.get_producer.return_value = MagicMock(name="producer")
        service = AlertSubmissionService()
    return service


@pytest.fixture
def service():
    return make_service()


class TestConstruction:
    def test_kafka_producer_is_created_for_kafka_source(self, service):
        assert service.kafka_producer is not None
        assert service.kafka_alert_topic == "mdx-alerts"
        assert service.kafka_incident_topic == "mdx-incidents"

    def test_topics_fall_back_to_defaults(self):
        svc = make_service({"event_bridge": {"sourceType": "kafka"}})
        assert svc.kafka_alert_topic == "mdx-alerts"
        assert svc.kafka_incident_topic == "mdx-incidents"

    def test_null_topics_block_falls_back_to_defaults(self):
        svc = make_service(
            {"event_bridge": {"sourceType": "kafka", "kafka_source": {"topics": None}}}
        )
        assert svc.kafka_alert_topic == "mdx-alerts"

    def test_non_kafka_source_skips_producer_setup(self):
        svc = make_service({"event_bridge": {"sourceType": "redisStream"}})
        assert not hasattr(svc, "kafka_producer")

    def test_empty_config_skips_producer_setup(self):
        svc = make_service({})
        assert not hasattr(svc, "kafka_producer")

    def test_entity_validator_is_always_available(self, service):
        assert service.entity_validator is not None

    def test_broker_failure_does_not_abort_construction(self):
        """The HTTP server must come up even when Kafka is unreachable."""
        with patch("utils.config.load_config", return_value=KAFKA_CONFIG), patch(
            "web.core.alert_service.KafkaMessageBroker", side_effect=RuntimeError("no brokers")
        ):
            svc = AlertSubmissionService()

        assert not hasattr(svc, "kafka_producer")
        assert svc.entity_validator is not None

    def test_close_is_a_noop(self, service):
        assert service.close() is None


class TestSubmitNvschemaAlert:
    """JSON Behavior path."""

    @pytest.mark.asyncio
    async def test_accepts_and_publishes(self, service):
        body, status = await service.submit_nvschema_alert({"id": "alert-1"})

        assert status == 202
        assert body["status"] == "accepted"
        assert body["id"] == "alert-1"
        assert body["timestamp"].endswith("Z")
        service.kafka_producer.produce.assert_called_once()
        service.kafka_producer.flush.assert_called_once()

    @pytest.mark.asyncio
    async def test_publishes_serialized_protobuf_to_alert_topic(self, service):
        await service.submit_nvschema_alert({"id": "alert-1", "direction": "north"})

        kwargs = service.kafka_producer.produce.call_args.kwargs
        assert kwargs["topic"] == "mdx-alerts"
        decoded = nvSchemaBehavior()
        decoded.ParseFromString(kwargs["value"])
        assert decoded.id == "alert-1"
        assert decoded.direction == "north"

    @pytest.mark.asyncio
    async def test_key_prefers_id(self, service):
        await service.submit_nvschema_alert(
            {"id": "alert-1", "sensorId": "cam-1", "sensor": {"id": "cam-2"}}
        )
        assert service.kafka_producer.produce.call_args.kwargs["key"] == "alert-1"

    @pytest.mark.asyncio
    async def test_key_falls_back_to_flat_sensor_id(self, service):
        await service.submit_nvschema_alert({"sensorId": "cam-1", "sensor": {"id": "cam-2"}})
        assert service.kafka_producer.produce.call_args.kwargs["key"] == "cam-1"

    @pytest.mark.asyncio
    async def test_key_falls_back_to_nested_sensor_id(self, service):
        await service.submit_nvschema_alert({"sensor": {"id": "cam-2"}})
        assert service.kafka_producer.produce.call_args.kwargs["key"] == "cam-2"

    @pytest.mark.asyncio
    async def test_key_is_empty_when_nothing_identifies_the_alert(self, service):
        await service.submit_nvschema_alert({"direction": "north"})
        assert service.kafka_producer.produce.call_args.kwargs["key"] == ""

    @pytest.mark.asyncio
    async def test_null_sensor_block_is_accepted(self, service):
        """A null nested block is treated as absent rather than rejected.

        Clients that serialise an unset optional as ``null`` used to get a 500
        from ``convert_behavior_to_protobuf_behavior`` even when the rest of
        the alert was well-formed. The converter now coerces the block, so the
        alert is published with the flat fields it did carry; one that carries
        nothing identifying is dropped later by the ``sensorId``/``timestamp``/
        ``end`` guard, the same way a sensor-less protobuf alert already is.
        """
        _body, status = await service.submit_nvschema_alert(
            {"sensorId": "cam-9", "sensor": None}
        )
        assert status == 202
        service.kafka_producer.produce.assert_called_once()

    @pytest.mark.asyncio
    async def test_unconfigured_kafka_returns_500(self):
        svc = make_service({})
        body, status = await svc.submit_nvschema_alert({"id": "alert-1"})

        assert status == 500
        assert body["error"] == "internal_error"

    @pytest.mark.asyncio
    async def test_produce_failure_returns_500_without_leaking_details(self, service):
        service.kafka_producer.produce.side_effect = RuntimeError("broker down: 10.0.0.1")
        body, status = await service.submit_nvschema_alert({"id": "alert-1"})

        assert status == 500
        assert body["message"] == "Internal server error occurred"
        assert "10.0.0.1" not in str(body)


class TestSubmitNvschemaAlertProtobuf:
    @pytest.mark.asyncio
    async def test_accepts_and_republishes_the_original_bytes(self, service):
        payload = nvSchemaBehavior(id="alert-1").SerializeToString()
        body, status = await service.submit_nvschema_alert_protobuf(payload)

        assert status == 202
        assert body["id"] == "alert-1"
        assert service.kafka_producer.produce.call_args.kwargs["value"] == payload

    @pytest.mark.asyncio
    async def test_key_prefers_message_id(self, service):
        payload = nvSchemaBehavior(id="alert-1").SerializeToString()
        await service.submit_nvschema_alert_protobuf(payload)
        assert service.kafka_producer.produce.call_args.kwargs["key"] == "alert-1"

    @pytest.mark.asyncio
    async def test_key_falls_back_to_nested_sensor_id(self, service):
        message = nvSchemaBehavior()
        message.sensor.id = "cam-1"
        await service.submit_nvschema_alert_protobuf(message.SerializeToString())
        assert service.kafka_producer.produce.call_args.kwargs["key"] == "cam-1"

    @pytest.mark.asyncio
    async def test_malformed_payload_returns_400(self, service):
        body, status = await service.submit_nvschema_alert_protobuf(b"\xff\xfe\xfd\xfc")

        assert status == 400
        assert body["error"] == "invalid_payload"
        service.kafka_producer.produce.assert_not_called()

    @pytest.mark.asyncio
    async def test_unconfigured_kafka_returns_500(self):
        svc = make_service({})
        _body, status = await svc.submit_nvschema_alert_protobuf(
            nvSchemaBehavior(id="alert-1").SerializeToString()
        )
        assert status == 500

    @pytest.mark.asyncio
    async def test_produce_failure_returns_500(self, service):
        service.kafka_producer.produce.side_effect = RuntimeError("broker down")
        _body, status = await service.submit_nvschema_alert_protobuf(
            nvSchemaBehavior(id="alert-1").SerializeToString()
        )
        assert status == 500


class TestSubmitNvschemaIncident:
    """JSON Incident path."""

    @pytest.mark.asyncio
    async def test_accepts_and_publishes_to_incident_topic(self, service):
        body, status = await service.submit_nvschema_incident(
            {"id": "inc-1", "sensorId": "cam-1"}
        )

        assert status == 202
        assert body["id"] == "inc-1"
        kwargs = service.kafka_producer.produce.call_args.kwargs
        assert kwargs["topic"] == "mdx-incidents"
        decoded = nvSchemaIncident()
        decoded.ParseFromString(kwargs["value"])
        assert decoded.sensorId == "cam-1"

    @pytest.mark.asyncio
    async def test_key_prefers_id_then_incident_id_then_sensor_id(self, service):
        await service.submit_nvschema_incident(
            {"id": "inc-1", "incidentId": "inc-2", "sensorId": "cam-1"}
        )
        assert service.kafka_producer.produce.call_args.kwargs["key"] == "inc-1"

    @pytest.mark.asyncio
    async def test_key_falls_back_to_incident_id(self, service):
        await service.submit_nvschema_incident({"incidentId": "inc-2", "sensorId": "cam-1"})
        assert service.kafka_producer.produce.call_args.kwargs["key"] == "inc-2"

    @pytest.mark.asyncio
    async def test_key_falls_back_to_sensor_id(self, service):
        await service.submit_nvschema_incident({"sensorId": "cam-1"})
        assert service.kafka_producer.produce.call_args.kwargs["key"] == "cam-1"

    @pytest.mark.asyncio
    async def test_unknown_enrichment_fields_are_tolerated(self, service):
        """VLM enrichment adds keys nvSchema does not declare."""
        _body, status = await service.submit_nvschema_incident(
            {"id": "inc-1", "sensorId": "cam-1", "vlmVerdict": "confirmed"}
        )
        assert status == 202

    @pytest.mark.asyncio
    async def test_unconvertible_payload_returns_500(self, service):
        _body, status = await service.submit_nvschema_incident({"info": "not-a-map"})
        assert status == 500

    @pytest.mark.asyncio
    async def test_unconfigured_kafka_returns_500(self):
        svc = make_service({})
        _body, status = await svc.submit_nvschema_incident({"id": "inc-1"})
        assert status == 500


class TestSubmitNvschemaIncidentProtobuf:
    @pytest.mark.asyncio
    async def test_accepts_and_republishes_the_original_bytes(self, service):
        payload = valid_incident_bytes()
        _body, status = await service.submit_nvschema_incident_protobuf(payload)

        assert status == 202
        assert service.kafka_producer.produce.call_args.kwargs["value"] == payload

    @pytest.mark.asyncio
    async def test_key_falls_back_to_sensor_id(self, service):
        await service.submit_nvschema_incident_protobuf(valid_incident_bytes())
        assert service.kafka_producer.produce.call_args.kwargs["key"] == "cam-1"

    @pytest.mark.asyncio
    async def test_malformed_payload_returns_400(self, service):
        body, status = await service.submit_nvschema_incident_protobuf(b"\xff\xfe\xfd\xfc")

        assert status == 400
        assert body["error"] == "invalid_payload"

    @pytest.mark.asyncio
    async def test_unconfigured_kafka_returns_500(self):
        svc = make_service({})
        _body, status = await svc.submit_nvschema_incident_protobuf(valid_incident_bytes())
        assert status == 500

    @pytest.mark.asyncio
    async def test_produce_failure_returns_500(self, service):
        service.kafka_producer.produce.side_effect = RuntimeError("broker down")
        _body, status = await service.submit_nvschema_incident_protobuf(
            valid_incident_bytes()
        )
        assert status == 500


class TestBuildErrorResponse:
    def test_shape_is_stable(self, service):
        result = service._build_error_response("bad_thing", "It broke")

        assert result["status"] == "error"
        assert result["error"] == "bad_thing"
        assert result["message"] == "It broke"
        assert result["details"] is None
        assert result["timestamp"].endswith("Z")

    def test_details_are_included_when_supplied(self, service):
        result = service._build_error_response("bad_thing", "It broke", {"field": "id"})
        assert result["details"] == {"field": "id"}
