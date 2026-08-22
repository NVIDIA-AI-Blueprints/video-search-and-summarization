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

"""Redis Streams sink for VLM-enhanced Alert and Incident results.

Selected with ``vlm_enhanced_sink.type: redisStream``. Elasticsearch remains
the default and Kafka the alternative broker.

Payloads are the same protobuf messages :class:`VLMEnhancedKafkaSink` produces,
wrapped in the MDX stream envelope, so the Logstash ``redis_stream`` input can
decode ``mdx-vlm-incidents`` and ``mdx-vlm-alerts`` in Redis mode exactly as it
does in Kafka mode.
"""

from __future__ import annotations

import json
from typing import Any, Dict, Optional

from mdx.redis_stream_broker import RedisStreamBroker, resolve_redis_config
from utils.schema_util import (
    convert_behavior_to_protobuf_behavior,
    convert_incident_to_protobuf_incident,
    get_nested_field,
)

from .sink_base import VLMEnhancedSink, log_enriched_event

DEFAULT_INCIDENT_STREAM = "mdx-vlm-incidents"
DEFAULT_ALERT_STREAM = "mdx-vlm-alerts"


class VLMEnhancedRedisStreamSink(VLMEnhancedSink):
    """Publishes VLM-verified events to per-kind Redis Streams."""

    def __init__(
        self,
        broker: RedisStreamBroker,
        incident_route: Dict[str, Any],
        alert_route: Dict[str, Any],
        category_mapping: Optional[Dict[str, str]] = None,
        alert_config_store: Any = None,
    ) -> None:
        super().__init__(
            alert_config_store=alert_config_store,
            category_mapping=category_mapping,
        )
        self._broker = broker
        self._incident_route = incident_route
        self._alert_route = alert_route

    @classmethod
    def from_config(
        cls,
        config: Dict[str, Any],
        category_mapping: Optional[Dict[str, str]] = None,
        alert_config_store: Any = None,
    ) -> "VLMEnhancedRedisStreamSink":
        """Construct the sink and its Redis connection from configuration."""
        sink_root = config.get("vlm_enhanced_sink", {}) or {}
        connection = sink_root.get("redisStream") or {}
        incident_cfg = (sink_root.get("incident") or {}).get("redisStream", {}) or {}
        alert_cfg = (sink_root.get("alert") or {}).get("redisStream", {}) or {}

        incident_route = {
            "stream": incident_cfg.get("stream") or DEFAULT_INCIDENT_STREAM,
            "key_field": incident_cfg.get("key_field"),
            "message_type": incident_cfg.get("message_type", "incident"),
            "payload_format": (incident_cfg.get("payload_format")
                               or connection.get("payload_format") or "protobuf").lower(),
        }
        alert_route = {
            "stream": alert_cfg.get("stream") or DEFAULT_ALERT_STREAM,
            "key_field": alert_cfg.get("key_field"),
            "message_type": alert_cfg.get("message_type", "alert"),
            "payload_format": (alert_cfg.get("payload_format")
                               or connection.get("payload_format") or "protobuf").lower(),
        }

        broker = RedisStreamBroker(resolve_redis_config(config, override=connection))
        return cls(
            broker=broker,
            incident_route=incident_route,
            alert_route=alert_route,
            category_mapping=category_mapping,
            alert_config_store=alert_config_store,
        )

    def _store_success(
        self,
        event_kind: str,
        document: Dict[str, Any],
        raw_vlm_response: Any,
        user_prompt: str,
    ) -> None:
        self._publish(event_kind, document)

    def _store_error(
        self,
        event_kind: str,
        document: Dict[str, Any],
        error_payload: Dict[str, Any],
    ) -> None:
        self._publish(event_kind, document)

    def _resolve_key(self, route: Dict[str, Any], document: Dict[str, Any]) -> str:
        key_field = route.get("key_field")
        if key_field:
            key_value = get_nested_field(document, key_field)
            if key_value is not None:
                return str(key_value)
        # Prefer the sensor id so cohorts stay co-located, mirroring the
        # partition-key contract the Kafka transport relies on.
        sensor_id = document.get("sensorId") or (document.get("sensor") or {}).get("id")
        return str(sensor_id or document.get("id") or document.get("incidentId") or "")

    @staticmethod
    def _serialize(route: Dict[str, Any], document: Dict[str, Any]) -> bytes:
        if route.get("payload_format") == "json":
            return json.dumps(document).encode("utf-8")

        message_type = (route.get("message_type") or "incident").lower()
        if message_type == "incident":
            proto_msg = convert_incident_to_protobuf_incident(document)
        elif message_type == "alert":
            proto_msg = convert_behavior_to_protobuf_behavior(document)
        else:
            raise ValueError(f"Unsupported message_type for Redis Stream route: {message_type}")
        return proto_msg.SerializeToString()

    def _publish(self, event_kind: str, document: Dict[str, Any]) -> None:
        route = self._alert_route if event_kind == 'alert' else self._incident_route
        stream = route.get("stream")
        if not stream:
            raise ValueError("Redis Stream route requires a stream name")

        key = self._resolve_key(route, document)

        # Apply the operator-configured output category before serialization.
        # The dedup fingerprint was already computed upstream, so mutating
        # ``category`` here only affects the published payload. Reading through
        # ``_resolve_output_category`` picks up live PUT API edits.
        if 'category' in document:
            original_category = document['category']
            resolved = self._resolve_output_category(original_category)
            if resolved and resolved != original_category:
                document['category'] = resolved
                self._logger.debug(
                    "Category mapped for output: %s -> %s", original_category, resolved
                )

        try:
            self._logger.info(
                "Publishing VLM-enhanced event to Redis Stream event_type=%s stream=%s",
                event_kind,
                stream,
            )
            entry_id = self._broker.add(stream, self._serialize(route, document), key=key)
            if entry_id is None:
                self._logger.error(
                    "Redis Stream publish returned no entry id",
                    extra={"incident_id": document.get("id"), "stream": stream},
                )
                return
            log_enriched_event(self._logger, "RedisStream", document.get("id"), document)
        except Exception:
            self._logger.error(
                "Failed to publish VLM-enhanced event to Redis Stream",
                extra={"incident_id": document.get("id"), "stream": stream},
                exc_info=True,
            )
            return
