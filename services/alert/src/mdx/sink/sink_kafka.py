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

import logging
from typing import List, Callable
from mdx.kafka_message_broker import KafkaMessageBroker
from mdx.sink.sink_base import SinkBase
from mdx.stream_message import StreamMessage
from mdx.protobuf.ext_pb2 import Behavior as nvSchemaBehavior
import json

class KafkaSink(SinkBase):
    def __init__(self, config: dict):
        """Initialize the Kafka Sink."""
        super().__init__(config)
        self.logger = logging.getLogger(self.__class__.__name__)
        self.kafka_message_broker = KafkaMessageBroker(config)
        self._producer = None
        
        # Support both legacy and new configuration formats
        if 'event_bridge' in config and 'kafka_sink' in config['event_bridge']:
            kafka_config = config['event_bridge']['kafka_sink']
            self.enhanced_anomaly_topic = kafka_config['topics']['enhanced_anomaly']
            self.incidents_topic = kafka_config['topics']['incidents']
        else:
            # Legacy configuration
            self.enhanced_anomaly_topic = config['kafka'].get('enhanced_anomaly_topic')
            self.incidents_topic = config['kafka'].get('incidents_topic')

    @property
    def producer(self):
        """The Kafka producer, created on first write rather than at construction.

        This sink carries validation-error responses only; VLM-verified results
        go to the separately-selected ``vlm_enhanced_sink``. A deployment that
        publishes results to Redis while leaving ``sinkType`` at its ``kafka``
        default therefore may never write through this sink at all — and
        connecting eagerly meant it held an open producer against a broker it
        does not otherwise use, or, in a Redis-only deployment, against one that
        is not running.
        """
        if self._producer is None:
            self._producer = self.kafka_message_broker.get_producer()
        return self._producer

    def write_data(self, data: List[dict], message_transform_func: Callable[[dict], nvSchemaBehavior]) -> None:
        """
        Publishes data to a Kafka topic after transforming it to Protobuf format using the provided transform function.

        Args:
            data (List[dict]): List of JSON data to be published.
            message_transform_func (Callable[[dict], nvSchemaBehavior]): Function to transform JSON to Protobuf.
        """
        for item in data:
            try:
                proto_message = message_transform_func(item)
                #self.logger.debug(f"Protobuf message before serialization: {proto_message}")
                    # Convert to JSON string
                json_message_str = json.dumps(item)

                self.producer.produce(
                    topic=self.enhanced_anomaly_topic,
                    value=proto_message.SerializeToString(),
                    key=item.get("sensor", {}).get("id", "")
                )
                self.logger.debug(f"Published message with key: {item.get('sensor', {}).get('id', '')}")

            except Exception as e:
                self.logger.error(f"Failed to publish message: {e}", exc_info=True)

        self.producer.flush()

    def write(self, messages: List[StreamMessage]) -> None:
        """Write StreamMessage objects to Kafka topic"""
        if not messages:
            return
            
        for message in messages:
            try:
                # Convert StreamMessage to JSON and publish
                json_str = message.to_json()
                key = message.get_field('sensor_id', message.id)
                
                self.producer.produce(
                    topic=self.enhanced_anomaly_topic,
                    value=json_str.encode('utf-8'),
                    key=str(key)
                )
                self.logger.debug(f"Published StreamMessage {message.id} with key: {key}")
                
            except Exception as e:
                self.logger.error(f"Failed to publish StreamMessage {message.id}: {e}")
                continue
        
        self.producer.flush()

    def write_msg(self, messages: List[bytes]) -> None:
        """Write raw byte messages to Kafka topic"""
        if not messages:
            return
            
        for i, message in enumerate(messages):
            try:
                self.producer.produce(
                    topic=self.enhanced_anomaly_topic,
                    value=message,
                    key=str(i)
                )
                self.logger.debug(f"Published raw message {i}")
                
            except Exception as e:
                self.logger.error(f"Failed to publish raw message {i}: {e}")
                continue
        
        self.producer.flush()

    def write_incidents(self, messages: List[StreamMessage]) -> None:
        """Write incident messages to dedicated topic"""
        if not messages:
            return
            
        for message in messages:
            try:
                # Convert StreamMessage to JSON and publish to incidents topic
                json_str = message.to_json()
                key = message.get_field('sensor_id', message.id)
                
                self.producer.produce(
                    topic=self.incidents_topic,
                    value=json_str.encode('utf-8'),
                    key=str(key)
                )
                self.logger.debug(f"Published incident {message.id} with key: {key}")
                
            except Exception as e:
                self.logger.error(f"Failed to publish incident {message.id}: {e}")
                continue
        
        self.producer.flush()

    def close(self) -> None:
        """
        Closes the Kafka producer.

        Reads ``_producer`` rather than the property, so shutting down a sink
        that was never written to does not construct one. Going through the
        property here undid the laziness above at the one moment it matters: the
        pipeline closes this sink on every shutdown, so a Redis-only deployment
        built a producer against an absent broker on its way out.
        """
        if self._producer is not None:
            self._producer.flush()

    def write_incident_data(self, data: List[dict], message_transform_func: Callable = None) -> None:
        """
        Write incident data directly to the incidents topic.
        
        Args:
            data (List[dict]): List of JSON data to be published
            message_transform_func (Callable): Function to transform data before publishing
        """
        for item in data:
            try:
                if message_transform_func:
                    message = message_transform_func(item)
                    value = message.SerializeToString()
                else:
                    value = json.dumps(item).encode('utf-8')

                self.producer.produce(
                    topic=self.incidents_topic,  # Use stored topic name
                    value=value,
                    key=item.get("sensor", {}).get("id", "")
                )
                self.logger.debug(f"Published incident with key: {item.get('sensor', {}).get('id', '')}")

            except Exception as e:
                self.logger.error(f"Failed to publish incident: {e}", exc_info=True)

        self.producer.flush()
