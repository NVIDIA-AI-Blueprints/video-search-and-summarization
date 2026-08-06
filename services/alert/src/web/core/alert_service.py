#!/usr/bin/env python3
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

"""
Alert Agent HTTP Service

Service layer for handling HTTP alert submissions.
"""

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple, Union

from schemas import EntityValidator
from mdx.kafka_message_broker import KafkaMessageBroker
from utils.schema_util import convert_behavior_to_protobuf_behavior, convert_incident_to_protobuf_incident
from mdx.protobuf import Behavior as nvSchemaBehavior
from mdx.protobuf import Incident as nvSchemaIncident
from google.protobuf.message import DecodeError


class AlertSubmissionService:
    """
    Service for processing HTTP alert submissions.
    """
    
    def __init__(self, config_file: str = "config.yaml"):
        """
        Initialize the alert submission service.
        
        Args:
            config_file: Path to configuration file
        """
        self.logger = logging.getLogger(self.__class__.__name__)
        
        # Load configuration
        self.config = self._load_config(config_file)
        
        # Initialize entity validator
        self.entity_validator = EntityValidator()

        # HTTP alert/incident submissions publish to Kafka. (Redis has been
        # removed; there is no Redis input-stream path anymore.)
        try:
            if self.config.get('event_bridge', {}).get('sourceType') == 'kafka':
                self._setup_kafka_producer()
        except Exception as e:
            # Do not prevent initialization; Kafka path will error at use time if misconfigured
            self.logger.error(f"Kafka producer setup failed: {e}")

        self.logger.info("Alert submission service initialized")

    def _setup_kafka_producer(self) -> None:
        """Setup Kafka producer for writing Behavior messages to alert topic."""
        broker = KafkaMessageBroker(self.config)
        self.kafka_producer = broker.get_producer()
        topics = self.config.get('event_bridge', {}).get('kafka_source', {}).get('topics', {}) or {}
        self.kafka_alert_topic = topics.get('alert') or 'mdx-alerts'
        self.kafka_incident_topic = topics.get('incident') or 'mdx-incidents'
        self.logger.info("Kafka producer initialized for alert topic", extra={"topic": self.kafka_alert_topic})
    
    async def submit_nvschema_alert_protobuf(self, behavior_bytes: bytes) -> Tuple[Dict[str, Any], int]:
        """
        Accept NvSchema Behavior protobuf bytes and publish to Kafka alert topic.
        Returns a 202 accepted-style response on success.
        """
        try:
            if not hasattr(self, 'kafka_producer') or not hasattr(self, 'kafka_alert_topic'):
                raise RuntimeError("Kafka is not configured for alert submissions")

            # Parse protobuf payload
            message = nvSchemaBehavior()
            message.ParseFromString(behavior_bytes)

            # Derive key: prefer id, fallback to nested sensor.id
            key = str(message.id or getattr(getattr(message, "sensor", None), "id", "") or "")

            # Produce to Kafka
            self.kafka_producer.produce(topic=self.kafka_alert_topic, value=behavior_bytes, key=key)
            self.kafka_producer.flush()

            response = {
                "status": "accepted",
                "id": message.id,
                "message": "Alert queued for processing",
                "timestamp": datetime.utcnow().isoformat() + "Z",
            }
            return response, 202
        except DecodeError:
            return self._build_error_response(
                "invalid_payload",
                "Invalid Protobuf payload"
            ), 400
        except Exception as e:
            self.logger.error("Failed to publish Protobuf NvSchema alert to Kafka", extra={"error": str(e)}, exc_info=True)
            return self._build_error_response(
                "internal_error",
                "Internal server error occurred"
            ), 500

    async def submit_nvschema_incident(self, incident_json: Dict[str, Any]) -> Tuple[Dict[str, Any], int]:
        """
        Accept NvSchema Incident JSON, convert to protobuf, and publish to Kafka incident topic.
        """
        try:
            if not hasattr(self, 'kafka_producer') or not hasattr(self, 'kafka_incident_topic'):
                raise RuntimeError("Kafka is not configured for incident submissions")

            proto_msg = convert_incident_to_protobuf_incident(incident_json)
            payload = proto_msg.SerializeToString()

            # Derive key: prefer id or incidentId; fallback to sensorId
            key = str(
                incident_json.get('id')
                or incident_json.get('incidentId')
                or incident_json.get('sensorId', '')
            )

            self.kafka_producer.produce(topic=self.kafka_incident_topic, value=payload, key=key)
            self.kafka_producer.flush()

            response = {
                "status": "accepted",
                "id": incident_json.get('id', ''),
                "message": "Incident queued for processing",
                "timestamp": datetime.utcnow().isoformat() + "Z",
            }
            return response, 202
        except Exception as e:
            self.logger.error("Failed to publish NvSchema incident to Kafka", extra={"error": str(e)}, exc_info=True)
            return self._build_error_response(
                "internal_error",
                "Internal server error occurred"
            ), 500

    async def submit_nvschema_incident_protobuf(self, incident_bytes: bytes) -> Tuple[Dict[str, Any], int]:
        """
        Accept NvSchema Incident protobuf bytes and publish to Kafka incident topic.
        """
        try:
            if not hasattr(self, 'kafka_producer') or not hasattr(self, 'kafka_incident_topic'):
                raise RuntimeError("Kafka is not configured for incident submissions")

            message = nvSchemaIncident()
            message.ParseFromString(incident_bytes)

            # Enforce the same required-field contract as the JSON branch so a
            # protobuf payload cannot bypass validation and flow downstream
            # missing fields the pipeline dereferences (e.g. ``end``). ``timestamp``
            # and ``end`` are protobuf message fields (Timestamp) so presence is
            # checked with ``HasField``; ``sensorId`` and ``category`` are scalars
            # that default to the empty string when unset.
            missing_fields = []
            if not message.sensorId:
                missing_fields.append("sensorId")
            if not message.HasField("timestamp"):
                missing_fields.append("timestamp")
            if not message.HasField("end"):
                missing_fields.append("end")
            if not message.category:
                missing_fields.append("category")
            if missing_fields:
                self.logger.error(
                    "Protobuf incident submission missing required field(s)",
                    extra={"missing_fields": missing_fields},
                )
                return self._build_error_response(
                    "validation_failed",
                    f"Request validation failed with {len(missing_fields)} error(s)",
                    details=[
                        {"field": field, "type": "missing", "message": "Field required"}
                        for field in missing_fields
                    ],
                ), 422

            # Derive key: id or sensorId
            key = str(getattr(message, "id", "") or getattr(message, "sensorId", ""))

            self.kafka_producer.produce(topic=self.kafka_incident_topic, value=incident_bytes, key=key)
            self.kafka_producer.flush()

            response = {
                "status": "accepted",
                "id": getattr(message, "id", ""),
                "message": "Incident queued for processing",
                "timestamp": datetime.utcnow().isoformat() + "Z",
            }
            return response, 202
        except DecodeError:
            return self._build_error_response(
                "invalid_payload",
                "Invalid Protobuf payload"
            ), 400
        except Exception as e:
            self.logger.error("Failed to publish Protobuf NvSchema incident to Kafka", extra={"error": str(e)}, exc_info=True)
            return self._build_error_response(
                "internal_error",
                "Internal server error occurred"
            ), 500
    async def submit_nvschema_alert(self, behavior_json: Dict[str, Any]) -> Tuple[Dict[str, Any], int]:
        """
        Accept NvSchema Behavior JSON, convert to protobuf, and publish to Kafka alert topic.
        Returns a 202 accepted-style response on success.
        """
        try:
            # Ensure Kafka is configured
            if not hasattr(self, 'kafka_producer') or not hasattr(self, 'kafka_alert_topic'):
                raise RuntimeError("Kafka is not configured for alert submissions")

            # Convert JSON to protobuf Behavior
            proto_msg = convert_behavior_to_protobuf_behavior(behavior_json)
            payload = proto_msg.SerializeToString()

            # Derive key: prefer id, fallback to nested sensor.id or sensorId
            key = str(behavior_json.get('id') or behavior_json.get('sensorId') or
                      (behavior_json.get('sensor') or {}).get('id') or "")

            # Produce to Kafka
            self.kafka_producer.produce(topic=self.kafka_alert_topic, value=payload, key=key)
            self.kafka_producer.flush()

            response = {
                "status": "accepted",
                "id": behavior_json.get('id', ''),
                "message": "Alert queued for processing",
                "timestamp": datetime.utcnow().isoformat() + "Z",
            }
            return response, 202
        except Exception as e:
            self.logger.error("Failed to publish NvSchema alert to Kafka", extra={"error": str(e)}, exc_info=True)
            return self._build_error_response(
                "internal_error",
                "Internal server error occurred"
            ), 500
    
    
    def _build_error_response(
        self, 
        error_type: str, 
        message: str, 
        details: Optional[Union[Dict[str, Any], List[Dict[str, Any]]]] = None
    ) -> Dict[str, Any]:
        """
        Build standardized error response.
        
        Args:
            error_type: Type of error
            message: Error message
            details: Additional error details
            
        Returns:
            Error response dictionary
        """
        return {
            "status": "error",
            "error": error_type,
            "message": message,
            "details": details,
            "timestamp": datetime.utcnow().isoformat() + "Z"
        }
    
    @staticmethod
    def _load_config(config_file: str) -> Dict[str, Any]:
        """Load configuration from YAML file."""
        from utils.config import load_config
        return load_config(config_file)
    
    def close(self):
        """Clean up resources. Kafka producer flushes per-produce, so there
        is nothing to close explicitly."""
        return None