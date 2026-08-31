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

import json
import logging
import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from mdx.kafka_message_broker import KafkaMessageBroker
from mdx.source.source_base import SourceBase
from mdx.stream_message import StreamMessage

# Record-key alignment guardrail (guarded so a minimal env cannot
# break the consume path).
try:  # pragma: no cover - exercised indirectly
    from metrics import recorder as _metrics
except Exception:  # pragma: no cover
    _metrics = None


def _classify_key_alignment(key: Any, value: Any) -> str:
    """Classify whether a Kafka record ``key`` aligns with the payload sensorId.

    Dedup determinism relies on the producer keying records by ``sensorId``
    so a cohort always lands on one consumer (the partition-key contract).
    Returns ``"yes"`` when the record key matches the payload's sensorId,
    ``"no"`` when it clearly does not, and ``"unknown"`` when it cannot be
    determined (missing key, non-JSON/protobuf payload). Best-effort and
    fully defensive — never raises.
    """
    try:
        if key is None:
            return "unknown"
        key_str = key.decode("utf-8") if isinstance(key, (bytes, bytearray)) else str(key)
        key_str = key_str.strip()
        if not key_str:
            return "unknown"
        if isinstance(value, (bytes, bytearray)):
            try:
                value = value.decode("utf-8")
            except Exception:
                return "unknown"
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except Exception:
                return "unknown"
        if not isinstance(value, dict):
            return "unknown"
        # Incident payloads carry top-level ``sensorId``; alert/anomaly
        # payloads carry the id nested under ``sensor.id`` (mirrors the
        # anomaly cohort-key builder). Support both so the guardrail covers
        # both cohort classes (the partition-key contract).
        sensor_id = value.get("sensorId")
        if not sensor_id:
            sensor = value.get("sensor")
            if isinstance(sensor, dict):
                sensor_id = sensor.get("id")
        if not sensor_id:
            return "unknown"
        # Producer keys records by sensorId; every cohort key is a superset
        # beginning with sensorId, so alignment holds when the record key is
        # (or begins with) the payload sensorId.
        return "yes" if key_str == str(sensor_id) or key_str.startswith(str(sensor_id)) else "no"
    except Exception:
        return "unknown"


def _record_key_alignment(key: Any, value: Any) -> None:
    if _metrics is None:
        return
    try:
        _metrics.inc_record_key_alignment(_classify_key_alignment(key, value))
    except Exception:  # pragma: no cover - metrics must never break consume
        pass


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
        self._revoke_hook = None
        self._assignment_change_hook = None

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

    def await_ready(self, timeout: float = 60.0) -> bool:
        """Create every source consumer, then wait for all their assignments.

        Every consumer is created before any of them is waited on. They share
        a group, so each subscription forces a rebalance that only completes
        once all members poll; creating and waiting one at a time leaves the
        earlier consumers unpolled and the rebalance never finishes.
        """
        for topic in self.source_topics:
            self._ensure_consumer(topic)

        consumers = [
            self.topic_consumer_map[topic]
            for topic in self.source_topics
            if topic in self.topic_consumer_map
        ]
        if self.kafka_message_broker.await_assignments(consumers, timeout):
            return True

        for topic in self.source_topics:
            consumer = self.topic_consumer_map.get(topic)
            if consumer is None or self.kafka_message_broker.assignment_decided(consumer):
                continue
            logging.warning(
                "Consumer for topic %s was given no assignment in group %s "
                "within %.0fs; records published before it is assigned may "
                "be skipped",
                topic, self.groupId, timeout,
            )
        return False

    def is_ready(self) -> bool:
        """Whether every source consumer currently holds a decided assignment.

        Goes false again while a rebalance is in flight, which is what makes
        it usable as live state rather than a latch that can only ever be set.
        """
        if not self.topic_consumer_map:
            return False
        return all(
            self.kafka_message_broker.assignment_decided(self.topic_consumer_map[topic])
            for topic in self.source_topics
            if topic in self.topic_consumer_map
        )

    def assigned_partition_count(self) -> int:
        return sum(
            len(self.kafka_message_broker.owned_partitions(consumer))
            for consumer in self.topic_consumer_map.values()
        )

    def set_assignment_change_hook(self, hook) -> None:
        """Register what to run whenever the assignment is decided or taken.

        Safe to call after the consumers exist, for the same reason as
        ``set_revoke_hook``.
        """
        self._assignment_change_hook = hook

    def buffered_for(self, partitions) -> int:
        """Records read for ``partitions`` that no stage has been given yet."""
        return self.kafka_message_broker.buffered_for(partitions)

    def set_revoke_hook(self, hook) -> None:
        """Register what to run when partitions are taken away.

        Set after construction because the pipeline that owns the in-flight
        accounting is built around this source, not before it. Registration
        order does not matter: the consumers dereference the hook when the
        callback fires rather than capturing it when they subscribe.
        """
        self._revoke_hook = hook

    def _ensure_consumer(self, topic: str) -> None:
        """Create and cache a consumer for the given topic if not already present."""
        if topic not in self.topic_consumer_map:
            # Looked up when the callback fires, not captured here. Consumers
            # used to be created on first read, so a hook registered any time
            # before that reached all of them; they are now created up front,
            # which silently turned a hook registered afterwards into a no-op.
            self.topic_consumer_map[topic] = self.kafka_message_broker.get_consumer(
                topic, self.groupId,
                on_revoke=lambda partitions: (
                    self._revoke_hook(partitions) if self._revoke_hook else None
                ),
                on_assignment_change=lambda: (
                    self._assignment_change_hook() if self._assignment_change_hook else None
                ),
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
        Read data from kafka and return batches, one per source partition.

        Shape: [ { 'kind': 'incident'|'alert', 'topic': ..., 'partition': int,
                   'messages': [(key, value, kafka_ts_ms), ...],
                   'kafka_consumed_at': ..., 'kafka_published_at': ... }, ... ]

        Split per partition rather than per kind so the partition a message
        came from survives into dispatch. Work in flight has to be
        attributable to a partition for a rebalance to be able to drain it
        before that partition moves to another member.
        """
        partition_to_messages: Dict[Any, List[Any]] = {}
        earliest_kafka_ts_ms: int = None
        for topic in self.source_topics:
            self._ensure_consumer(topic)
            consumer = self.topic_consumer_map[topic]
            topic_messages = self.kafka_message_broker.get_consumed_messages(consumer)

            kind = self.topic_to_kind.get(topic, 'unknown')

            for partition_key, msgs in topic_messages.items():
                if not msgs:
                    continue
                # Keys are "<topic>-<partition>" and topic names contain
                # hyphens, so take the partition off the right.
                try:
                    partition = int(str(partition_key).rsplit('-', 1)[1])
                except (IndexError, ValueError):
                    partition = -1
                partition_to_messages.setdefault((kind, topic, partition), []).extend(msgs)
                if self.kafka_message_broker.was_revoked(consumer, topic, partition):
                    # Read before a revoke that the same poll delivered. The
                    # offsets are already committed so the records are not
                    # dropped, but the drain has already run and could not have
                    # counted them: this member finishes them while the new
                    # owner works the same sensors. Bounded by max_poll_records
                    # per partition per rebalance, and counted so the residual
                    # is measurable rather than silent.
                    # Through the guarded module import, not a bare one: this
                    # module deliberately tolerates a environment without the
                    # metrics package, and an ImportError raised here would
                    # leave the consume loop for good.
                    if _metrics is not None:
                        _metrics.inc_records_read_after_revoke(len(msgs))
                # Track earliest kafka timestamp in batch (producer timestamp)
                for msg in msgs:
                    if len(msg) >= 3 and msg[2] is not None and msg[2] > 0:
                        if earliest_kafka_ts_ms is None or msg[2] < earliest_kafka_ts_ms:
                            earliest_kafka_ts_ms = msg[2]
                    # Surface producer-side partition-key drift.
                    if len(msg) >= 2:
                        _record_key_alignment(msg[0], msg[1])

        # Capture timestamp AFTER all messages consumed from all topics
        kafka_consumed_at = datetime.now(timezone.utc).isoformat()

        # Convert earliest kafka timestamp to ISO format
        kafka_published_at = None
        if earliest_kafka_ts_ms:
            kafka_published_at = datetime.fromtimestamp(earliest_kafka_ts_ms / 1000, tz=timezone.utc).isoformat()

        batches: List[Dict[str, Any]] = []
        for (kind, topic, partition), msgs in partition_to_messages.items():
            if msgs:
                batches.append({
                    'kind': kind,
                    'topic': topic,
                    'partition': partition,
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
