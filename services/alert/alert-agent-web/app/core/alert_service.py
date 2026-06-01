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
import json
from datetime import datetime
from typing import Dict, Any, Optional, Tuple
import yaml
import redis

from entity_management import EntityValidator
from entity_management.request_entity.models.requests import AlertRequestEntity
from entity_management.request_entity.exceptions import ValidationError, InvalidPayloadError
from mdx.anomaly.event_bridge_factory import EventBridgeFactory
from mdx.anomaly.stream_message import StreamMessage
from mdx.anomaly.kafka_message_broker import KafkaMessageBroker
from utils.schema_util import convert_behavior_to_protobuf_behavior, convert_incident_to_protobuf_incident
from mdx.anomaly.protobuf import Behavior as nvSchemaBehavior
from mdx.anomaly.protobuf import Incident as nvSchemaIncident
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
        
        # Initialize direct Redis client for writing to INPUT stream (not output stream)
        # This allows HTTP alerts to be picked up by the anomaly processor
        self._setup_input_stream_writer()

        # Initialize Kafka producer when configured
        try:
            if self.config.get('event_bridge', {}).get('sourceType') == 'kafka':
                self._setup_kafka_producer()
        except Exception as e:
            # Do not prevent initialization; Kafka path will error at use time if misconfigured
            self.logger.error(f"Kafka producer setup failed: {e}")
        
        self.logger.info("Alert submission service initialized", extra={
            "stream_name": self.input_stream_name,
            "redis_host": f"{self.redis_host}:{self.redis_port}"
        })
    
    def _setup_input_stream_writer(self):
        """Setup Redis client for writing to the input stream where anomaly processor reads from."""
        # Get Redis configuration
        redis_config = self.config['event_bridge']['redis_source']  # Use source config for input stream
        self.redis_host = redis_config['host']
        self.redis_port = redis_config['port']
        self.redis_db = redis_config.get('db', 0)
        
        # Get input stream name (where processor reads from)
        self.input_stream_name = redis_config['streams']['anomaly_stream']
        
        # Create Redis client
        self.redis_client = redis.Redis(
            host=self.redis_host,
            port=self.redis_port,
            db=self.redis_db,
            decode_responses=True
        )
        
        # Test connection
        try:
            self.redis_client.ping()
            self.logger.info(f"Redis connection established to input stream: {self.input_stream_name}")
        except Exception as e:
            self.logger.error(f"Failed to connect to Redis: {e}")
            raise

    def _setup_kafka_producer(self) -> None:
        """Setup Kafka producer for writing Behavior messages to alert topic."""
        broker = KafkaMessageBroker(self.config)
        self.kafka_producer = broker.get_producer()
        topics = self.config.get('event_bridge', {}).get('kafka_source', {}).get('topics', {}) or {}
        self.kafka_alert_topic = topics.get('alert') or 'mdx-alerts'
        self.kafka_incident_topic = topics.get('incident') or 'mdx-incidents'
        self.logger.info("Kafka producer initialized for alert topic", extra={"topic": self.kafka_alert_topic})
    
    async def submit_alert(self, request_data: Dict[str, Any]) -> Tuple[Dict[str, Any], int]:
        """
        Submit an alert through the complete processing pipeline.
        
        Args:
            request_data: HTTP alert submission request as dictionary
            
        Returns:
            Tuple of (response_dict, status_code)
        """
        start_time = datetime.utcnow()
        request_id = request_data.get('id', 'unknown')
        
        try:
            self.logger.info("Processing alert submission", extra={
                "event_id": request_id,
                "sensor_id": request_data.get('sensorId'),
                "alert_type": request_data.get('alert', {}).get('type')
            })
            
            # Step 1: Validate and build entity using existing validator
            # The request_data is already in the format expected by AlertRequestEntity
            entities = self.entity_validator.validate_and_build([request_data])
            
            if not entities:
                raise ValidationError("No valid entities created from request")
            
            entity = entities[0]
            
            # Step 2: Send through event bridge
            queue_type = await self._send_to_event_bridge(entity)
            
            # Step 3: Build success response
            processing_time = datetime.utcnow()
            response = {
                "status": "accepted",
                "id": request_id,
                "message": "Alert queued for processing",
                "timestamp": processing_time.isoformat() + "Z"
            }
            
            self.logger.info("Alert submission successful", extra={
                "event_id": request_id,
                "queue_type": queue_type
            })
            
            return response, 202
            
        except ValidationError as e:
            self.logger.warning("Alert validation failed", extra={
                "event_id": request_id,
                "error": str(e),
                "field_errors": getattr(e, 'field_errors', {})
            })
            return self._build_error_response(
                "validation_failed", 
                f"Request validation failed: {str(e)}", 
                {"field_errors": getattr(e, 'field_errors', {})}
            ), 400
            
        except InvalidPayloadError as e:
            self.logger.warning("Invalid payload format", extra={
                "event_id": request_id,
                "error": str(e)
            })
            return self._build_error_response(
                "invalid_payload", 
                f"Invalid payload format: {str(e)}"
            ), 400
            
        except Exception as e:
            self.logger.error("Alert submission failed", extra={
                "event_id": request_id,
                "error": str(e),
                "error_type": type(e).__name__
            }, exc_info=True)
            return self._build_error_response(
                "internal_error", 
                "Internal server error occurred"
            ), 500
    
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
    async def _send_to_event_bridge(self, entity: AlertRequestEntity) -> str:
        """
        Send entity to Redis input stream where anomaly processor reads from.
        
        Args:
            entity: Validated alert request entity
            
        Returns:
            Queue type used (for response metadata)
        """
        try:
            # Convert entity to message format
            message_data = entity.model_dump(by_alias=True, exclude_none=False)
            #print("[DEBUG] Serialized message_data:", json.dumps(message_data, indent=2))
            # Send to Redis input stream directly
            await self._write_to_input_stream(message_data)
            
            # Return stream type for response
            return "redis_input_stream"
            
        except Exception as e:
            self.logger.error("Failed to send to event bridge", extra={
                "event_id": entity.id,
                "error": str(e)
            })
            raise
    
    async def _write_to_input_stream(self, message_data: Dict[str, Any]) -> None:
        """
        Write message directly to Redis input stream.
        
        Args:
            message_data: Message data to write to input stream
        """
        try:
            # Create StreamMessage for proper formatting
            json_str = json.dumps(message_data)
            stream_message = StreamMessage.from_json(json_str, message_id=message_data.get('id', ''))
            
            # Get Redis stream fields format
            fields = stream_message.to_redis_fields()
            
            # Write directly to input stream
            message_id = self.redis_client.xadd(self.input_stream_name, fields)
            
            self.logger.info(f"Written alert to input stream", extra={
                "stream": self.input_stream_name,
                "redis_message_id": message_id,
                "event_id": message_data.get('id'),
                "sensor_id": message_data.get('sensorId')
            })
            
        except Exception as e:
            self.logger.error(f"Error writing to input stream: {e}")
            raise
    
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
        details: Optional[Dict[str, Any]] = None
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
        with open(config_file, 'r') as file:
            return yaml.safe_load(file)
    
    def close(self):
        """Clean up resources."""
        try:
            if hasattr(self, 'redis_client'):
                self.redis_client.close()
                self.logger.info("Redis client connection closed")
        except Exception as e:
            self.logger.warning(f"Error closing Redis client: {e}") 