# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Kafka publisher for evaluator-seeded synthetic Alert Bridge incidents."""

import json
import logging
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Optional

from .alert_bridge_client import AlertBridgeError

logger = logging.getLogger(__name__)


def _stringify_map_values(target: Dict[str, Any]) -> None:
    for key, value in list(target.items()):
        if isinstance(value, (dict, list)):
            target[key] = json.dumps(value)
        elif value is None:
            target[key] = ""
        else:
            target[key] = str(value)


def convert_incident_to_protobuf_incident(incident: Dict[str, Any]):
    """Convert an NvSchema incident JSON dictionary to protobuf bytes."""
    from google.protobuf import json_format
    from ..mdx.protobuf import Incident as NvSchemaIncident

    incident_copy = json.loads(json.dumps(incident))
    info = incident_copy.get("info")
    if isinstance(info, dict):
        _stringify_map_values(info)

    message = NvSchemaIncident()
    json_format.ParseDict(incident_copy, message, ignore_unknown_fields=True)
    return message


class KafkaSyntheticIncidentProducer:
    """Publish synthetic NvSchema incidents to Alert Agent's incident topic."""

    def __init__(
        self,
        *,
        bootstrap_servers: str,
        incident_topic: str,
        flush_timeout: float = 10.0,
        producer_factory: Optional[Callable[[Dict[str, Any]], Any]] = None,
    ) -> None:
        self._bootstrap_servers = str(bootstrap_servers or "").strip()
        self._incident_topic = str(incident_topic or "").strip()
        self._flush_timeout = float(flush_timeout or 10.0)
        self._producer_factory = producer_factory
        self._producer = None

    def _get_producer(self):
        if self._producer is not None:
            return self._producer
        if not self._bootstrap_servers:
            raise AlertBridgeError(
                "KAFKA_BOOTSTRAP_SERVERS is required for message_bus synthetic "
                "alert injection"
            )
        if not self._incident_topic:
            raise AlertBridgeError(
                "KAFKA_INCIDENT_TOPIC is required for message_bus synthetic "
                "alert injection"
            )
        factory = self._producer_factory
        if factory is None:
            from confluent_kafka import Producer

            factory = Producer
        self._producer = factory(
            {
                "bootstrap.servers": self._bootstrap_servers,
                "client.id": "alert-bridge-evaluator-synthetic",
            }
        )
        return self._producer

    @staticmethod
    def _producer_key(incident: Dict[str, Any]) -> str:
        return str(
            incident.get("sensorId")
            or incident.get("id")
            or incident.get("incidentId")
            or ""
        )

    def submit_incident(self, incident: Dict[str, Any]) -> Dict[str, Any]:
        """Serialize and publish one synthetic incident to Kafka."""
        producer = self._get_producer()
        message = convert_incident_to_protobuf_incident(incident)
        payload = message.SerializeToString()
        key = self._producer_key(incident)
        errors: list[str] = []

        def _delivery_callback(error, _message) -> None:
            if error is not None:
                errors.append(str(error))

        try:
            producer.produce(
                topic=self._incident_topic,
                value=payload,
                key=key,
                callback=_delivery_callback,
            )
            remaining = producer.flush(self._flush_timeout)
        except Exception as exc:
            raise AlertBridgeError(
                f"Failed to publish synthetic incident to Kafka topic "
                f"{self._incident_topic!r}: {exc}"
            ) from exc

        if errors:
            raise AlertBridgeError(
                f"Kafka delivery failed for synthetic incident on topic "
                f"{self._incident_topic!r}: {'; '.join(errors)}"
            )
        if remaining:
            raise AlertBridgeError(
                f"Timed out publishing synthetic incident to Kafka topic "
                f"{self._incident_topic!r}; {remaining} message(s) still pending"
            )

        logger.info(
            "Published synthetic incident to Kafka topic=%s key=%s incident_id=%s",
            self._incident_topic,
            key,
            incident.get("id", ""),
        )
        return {
            "status": "accepted",
            "id": incident.get("id", ""),
            "message": "Incident queued for processing",
            "topic": self._incident_topic,
            "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        }
