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
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from mdx.anomaly.kafka_message_broker import KafkaMessageBroker
from mdx.anomaly.source.source_base import SourceBase
from mdx.anomaly.stream_message import StreamMessage


class MockKafkaMessage:
    """Mock Kafka message for compatibility with StreamMessage"""
    
    def __init__(self, key, value, partition, offset):
        self._key = key
        self._value = value
        self._partition = partition
        self._offset = offset
    
    def key(self):
        return self._key
    
    def value(self):
        return self._value
    
    def partition(self):
        return self._partition
    
    def offset(self):
        return self._offset


class SourceKafka(SourceBase):
    def __init__(self, config: dict):
        super().__init__(config)
        self.topic_consumer_map = {}
        self.kafka_message_broker = KafkaMessageBroker(config)

        kafka_cfg = config.get('event_bridge', {}).get('kafka_source', {})
        topics_cfg = kafka_cfg.get("topics")

        if topics_cfg:
            self.groupId = kafka_cfg.get('group_id')
            if not self.groupId:
                raise ValueError("event_bridge.kafka_source.group_id must be configured")

            self.heartbeat_topic: Optional[str] = None
            self.source_topics: List[str] = []
            self.topic_to_kind: Dict[str, str] = {}

            for name, topic in topics_cfg.items():
                if name == 'heartbeat':
                    self.heartbeat_topic = topic
                elif topic:
                    self.source_topics.append(topic)
                    self.topic_to_kind[topic] = name

            if not self.source_topics:
                raise ValueError("At least one non-heartbeat topic must be configured")
        else:
            # Legacy configuration
            self.anomaly_topic = config['kafka']["anomalyTopic"]
            self.groupId = config['kafka']['group_id']
            self.heartbeat_topic = config['kafka'].get('heartbeat_topic', 'its-streaming-heartbeats')
            self.source_topics = [self.anomaly_topic]
            self.topic_to_kind = {self.anomaly_topic: 'anomaly'}

    def _ensure_consumer(self, topic: str) -> None:
        """Create and cache a consumer for the given topic if not already present."""
        if topic not in self.topic_consumer_map:
            self.topic_consumer_map[topic] = self.kafka_message_broker.get_consumer(
                topic, self.groupId
            )

    # def read_from_topic(self, topic: str, message_transfer_func: Optional[Callable] = None) -> List[Any]:
    #     """
    #     Read data from kafka topic, optionally transform messages via message_transfer_func

    #     :param str topic: a kafka topic name
    #     :param Optional[Callable] message_transfer_func: optional function to transfer messages
    #     :return: list of messages (transformed or original)
    #     :rtype: List[Any]
    #     """
    #     print(f"Reading from topic: {topic}")
    #     if topic not in self.topic_consumer_map:
    #         self.topic_consumer_map[topic] = self.kafka_message_broker.get_consumer(
    #             topic, self.groupId)
    #     consumer = self.topic_consumer_map[topic]
    #     original_messages = self.kafka_message_broker.get_consumed_messages(
    #         consumer)
    #     results = list()
        
    #     for partition, msgs in original_messages.items():
    #         logging.debug(
    #             f"Processing partition ID {partition.partition} which has {len(msgs)} messages")
    #         if message_transfer_func:
    #             transferred_messages = message_transfer_func(msgs)
    #             results.extend(transferred_messages)
    #         else:
    #             results.extend(msgs)
                
    #     return results

    def read(self) -> List[bytes]:
        """Read raw messages from all configured topics."""
        try:
            results: List[bytes] = []
            for topic in self.source_topics:
                self._ensure_consumer(topic)
                consumer = self.topic_consumer_map[topic]
                original_messages = self.kafka_message_broker.get_consumed_messages(consumer)

                for partition, msgs in original_messages.items():
                    for _, value, *__ in msgs:  # Ignore key and kafka_ts_ms
                        if isinstance(value, bytes):
                            results.append(value)
                        else:
                            results.append(str(value).encode('utf-8'))
            return results
        except Exception as e:
            logging.error(f"Error reading raw messages from Kafka: {e}")
            return []

    def poll(self) -> List[StreamMessage]:
        """Read and deserialize messages into StreamMessage format"""
        try:
            results: List[StreamMessage] = []
            for topic in self.source_topics:
                self._ensure_consumer(topic)
                consumer = self.topic_consumer_map[topic]
                original_messages = self.kafka_message_broker.get_consumed_messages(consumer)

                for partition, msgs in original_messages.items():
                    for key, value, *_ in msgs:  # Ignore kafka_ts_ms if present
                        try:
                            mock_msg = MockKafkaMessage(key, value, partition.partition, 0)
                            stream_msg = StreamMessage.from_kafka_message(mock_msg, 'request_schema.yaml')
                            results.append(stream_msg)
                        except Exception as e:
                            logging.error(f"Error creating StreamMessage from Kafka message: {e}")
                            continue
            return results
        except Exception as e:
            logging.error(f"Error polling messages from Kafka: {e}")
            return []

    def poll_heartbeats(self) -> List[StreamMessage]:
        """Read heartbeat messages"""
        try:
            if not self.heartbeat_topic:
                return []

            if self.heartbeat_topic not in self.topic_consumer_map:
                self.topic_consumer_map[self.heartbeat_topic] = self.kafka_message_broker.get_consumer(
                    self.heartbeat_topic, self.groupId)
            
            consumer = self.topic_consumer_map[self.heartbeat_topic]
            original_messages = self.kafka_message_broker.get_consumed_messages(consumer)
            
            results = []
            for partition, msgs in original_messages.items():
                for key, value, *_ in msgs:  # Ignore kafka_ts_ms if present
                    try:
                        # Create StreamMessage for heartbeat
                        stream_msg = StreamMessage.from_json_with_schema(
                            value.decode('utf-8') if isinstance(value, bytes) else str(value),
                            'request_schema.yaml'
                        )
                        results.append(stream_msg)
                    except Exception as e:
                        logging.error(f"Error creating heartbeat StreamMessage: {e}")
                        continue
            
            return results
        except Exception as e:
            logging.error(f"Error polling heartbeat messages: {e}")
            return []

    def read_data(self) -> List[Any]:
        """
        Read data from kafka and return aggregated batches per kind.
        Shape: [ { 'kind': 'incident'|'alert', 'messages': [(key, value, kafka_ts_ms), ...], 'kafka_consumed_at': ..., 'kafka_published_at': ... }, ... ]
        """
        kind_to_messages: Dict[str, List[Any]] = {}
        earliest_kafka_ts_ms: int = None
        for topic in self.source_topics:
            self._ensure_consumer(topic)
            consumer = self.topic_consumer_map[topic]
            topic_messages = self.kafka_message_broker.get_consumed_messages(consumer)

            kind = self.topic_to_kind.get(topic, 'unknown')
            if kind not in kind_to_messages:
                kind_to_messages[kind] = []

            for _, msgs in topic_messages.items():
                if not msgs:
                    continue
                kind_to_messages[kind].extend(msgs)
                # Track earliest kafka timestamp in batch (producer timestamp)
                for msg in msgs:
                    if len(msg) >= 3 and msg[2] is not None and msg[2] > 0:
                        if earliest_kafka_ts_ms is None or msg[2] < earliest_kafka_ts_ms:
                            earliest_kafka_ts_ms = msg[2]

        # Capture timestamp AFTER all messages consumed from all topics
        kafka_consumed_at = datetime.now(timezone.utc).isoformat()

        # Convert earliest kafka timestamp to ISO format
        kafka_published_at = None
        if earliest_kafka_ts_ms:
            kafka_published_at = datetime.fromtimestamp(earliest_kafka_ts_ms / 1000, tz=timezone.utc).isoformat()

        # Build list of batches without partition/topic metadata
        batches: List[Dict[str, Any]] = []
        for kind, msgs in kind_to_messages.items():
            if msgs:
                batches.append({
                    'kind': kind,
                    'messages': msgs,
                    'kafka_consumed_at': kafka_consumed_at,
                    'kafka_published_at': kafka_published_at,
                })
        return batches

    # def read_data_legacy(self, event_type: Optional[str] = None) -> List[Any]:
    #     """
    #     Read data from kafka raw topic

    #     :param Optional[Callable] message_transfer_func: optional function to transfer messages
    #     :return: list of messages (transformed or original)
    #     :rtype: List[Any]
    #     """
    #     # Simple JSON message processor for non-protobuf messages
    #     def json_message_processor(msgs):
    #         print(f"Processing messages: {msgs}")
    #         results = []
    #         for key, value in msgs:
    #             print('')
    #             print(f"Processing message: {key}, {value}")
    #             try:
    #                 # Decode bytes to string (JSON messages)
    #                 if isinstance(value, bytes):
    #                     results.append(value.decode('utf-8'))
    #                 else:
    #                     results.append(str(value))
    #             except Exception as e:
    #                 logging.error(f"Error decoding message: {e}")
    #         return results

    #     def protobuf_anomaly_to_json_string(msgs):
    #         """
    #         Convert protobuf message to JSON string.
            
    #         Args:
    #             anomaly_pb: Serialized protobuf message
            
    #         Returns:
    #             JSON string representation of the protobuf message
    #         """
    #         result = []
    #         for _, anomaly_pb in msgs:
    #             try:
    #                 proto_message = nvSchemaIncident()
    #                 # # Choose appropriate protobuf class based on message type
    #                 # if message_type.lower() == 'incident':
    #                 #     proto_message = nvSchemaIncident()
    #                 # else:  # Default to Behavior
    #                 #     proto_message = nvSchemaBehavior()
                    
    #                 # Parse the serialized Protobuf message
    #                 proto_message.ParseFromString(anomaly_pb)
    #                 message_json = json_format.MessageToJson(proto_message, always_print_fields_with_no_presence=True)

    #             except anomaly_pb.DecodeError as e:
    #                 logging.error("Failed to parse Protobuf message: %s", e)
    #                 # Log part of the input for inspection
    #                 logging.debug("Message content (truncated): %s", anomaly_pb[:100])
    #                 raise
    #             result.append(message_json)
    #         return result

    #     # Use JSON processor if no custom transfer function provided
    #     if event_type is None:
    #         message_transfer_func = json_message_processor
    #     elif event_type == 'Incident':
    #         message_transfer_func = protobuf_anomaly_to_json_string
            
    #     return self.read_from_topic(self.anomaly_topic, message_transfer_func)

    def read_heartbeats(self, message_transfer_func: Optional[Callable] = None) -> List[Any]:
        """
        Read heartbeat messages from kafka heartbeat topic

        :param Optional[Callable] message_transfer_func: optional function to transfer messages
        :return: list of heartbeat messages (transformed or original)
        :rtype: List[Any]
        """
        return self.read_from_topic(self.heartbeat_topic, message_transfer_func)

    def close(self) -> None:
        """
        Close consumers

        :return: None
        """
        for _, consumer in self.topic_consumer_map.items():
            consumer.close()
