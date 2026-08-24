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

from __future__ import annotations

import json
from typing import Any, Dict, Optional

from mdx.anomaly.kafka_message_broker import KafkaMessageBroker

from .sink_base import VLMEnhancedSink, log_enriched_event
from utils.schema_util import (
    convert_behavior_to_protobuf_behavior,
    convert_incident_to_protobuf_incident,
    get_nested_field,
)


class VLMEnhancedKafkaSink(VLMEnhancedSink):
    def __init__(
        self,
        producer: Any,
        incident_route: Dict[str, Any],
        alert_route: Dict[str, Any],
        category_mapping: Optional[Dict[str, str]] = None,
        alert_config_store: Any = None,
    ) -> None:
        super().__init__(
            alert_config_store=alert_config_store,
            category_mapping=category_mapping,
        )
        self._producer = producer
        self._incident_route = incident_route
        self._alert_route = alert_route

    @classmethod
    def from_config(
        cls,
        config: Dict[str, Any],
        category_mapping: Optional[Dict[str, str]] = None,
        alert_config_store: Any = None,
    ) -> "VLMEnhancedKafkaSink":
        """Construct sink by parsing config and creating the Kafka producer internally."""
        sink_root = config.get("vlm_enhanced_sink", {}) or {}
        incident_cfg = (sink_root.get("incident") or {}).get("kafka", {}) or {}
        alert_cfg = (sink_root.get("alert") or {}).get("kafka", {}) or {}

        # Per-kind routes with defaults
        incident_route = {
            "topic": incident_cfg.get("topic") or "mdx-vlm-incidents",
            "key_field": incident_cfg.get("key_field"),
            "message_type": incident_cfg.get("message_type", "incident"),
        }
        alert_route = {
            "topic": alert_cfg.get("topic") or "mdx-vlm-alerts",
            "key_field": alert_cfg.get("key_field"),
            "message_type": alert_cfg.get("message_type", "behavior"),
        }

        producer = KafkaMessageBroker(config).get_producer()
        return cls(
            producer=producer,
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
        self._produce(event_kind, document)

    def _store_error(
        self,
        event_kind: str,
        document: Dict[str, Any],
        error_payload: Dict[str, Any],
    ) -> None:
        self._produce(event_kind, document)

    def _produce(self, event_kind: str, document: Dict[str, Any]) -> None:
        route = self._alert_route if event_kind == 'alert' else self._incident_route
        topic = route.get("topic")
        if not topic:
            raise ValueError("Kafka route requires a topic")
        key_field = route.get("key_field")
        # Resolve key using nested field path when provided; fallback to id/incidentId
        if key_field:
            key_value = get_nested_field(document, key_field)
            key = str(key_value) if key_value is not None else str(document.get("id", document.get("incidentId", "")))
        else:
            key = str(document.get("id", document.get("incidentId", "")))

        # ─── Apply custom output category before serialization ───
        # Fingerprint was already computed earlier in _set_message_id_and_should_skip(),
        # so mutating ``category`` here only affects the published payload.
        # ``_resolve_output_category`` reads the live AlertConfigStore when
        # available so PUT API edits take effect without a restart, falling
        # back to the file-loaded mapping otherwise.
        if 'category' in document:
            original_category = document['category']
            resolved = self._resolve_output_category(original_category)
            if resolved and resolved != original_category:
                document['category'] = resolved
                self._logger.debug(
                    "Category mapped for output: %s -> %s",
                    original_category,
                    resolved,
                )
        # ─────────────────────────────────────────────────────────

        try:
            self._logger.info(
                "Publishing VLM-enhanced event to Kafka event_type=%s topic=%s",
                event_kind,
                topic,
            )
            # Convert to protobuf based on message_type (default: incident)
            message_type = (route.get("message_type") or "incident").lower()
            if message_type == "incident":
                proto_msg = convert_incident_to_protobuf_incident(document)
            elif message_type == "alert":
                proto_msg = convert_behavior_to_protobuf_behavior(document)
            else:
                raise ValueError(f"Unsupported message_type for Kafka route: {message_type}")
            payload = proto_msg.SerializeToString()
            self._producer.produce(topic=topic, value=payload, key=key)
            self._producer.flush()
            # Mirror Elastic sink post-publish logging
            log_enriched_event(self._logger, "Kafka", document.get("id"), document)
        except Exception:
            self._logger.error(
                "Failed to publish VLM-enhanced event to Kafka",
                extra={"incident_id": document.get("id"), "topic": topic},
                exc_info=True,
            )
            return
