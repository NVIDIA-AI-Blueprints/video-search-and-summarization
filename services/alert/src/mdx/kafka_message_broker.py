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

from typing import Any, Dict, List, Tuple, Optional

from confluent_kafka import Consumer, KafkaError, KafkaException, Producer

# Configure centralized logging from config.yaml
from utils.logging_config import setup_logging, get_logger
setup_logging()
logger = get_logger(__name__)


class KafkaMessageBroker:
    """
    Module for Kafka message broker abstraction using Confluent Kafka
    """

    def __init__(self, kafkaConfig: dict) -> None:
        self.config = kafkaConfig
        self.batch_commit = bool(kafkaConfig.get('kafka', {}).get('batch_commit', False))

    def get_consumer(self, topic: str, group_id: str) -> Consumer:
        """
        Creates a Confluent Kafka consumer.

        :param topic: The topic to subscribe to.
        :param group_id: The consumer group ID.
        :return: Configured Kafka consumer.
        :rtype: Consumer
        """
        consumer_config = {
            'bootstrap.servers': self.config['kafka']['bootstrap_servers'],
            'group.id': group_id,
            'auto.offset.reset': self.config['kafka']['auto_offset_reset'],
            'enable.auto.commit': self.config['kafka']['enable_auto_commit'],
            'max.poll.interval.ms': self.config['kafka']['max_poll_interval_ms'],
            'session.timeout.ms': self.config['kafka'].get('session_timeout_ms', 300000), 
            'heartbeat.interval.ms': self.config['kafka'].get('heartbeat_interval_ms', 300000)
        }
        consumer = Consumer(consumer_config)
        consumer.subscribe([topic])
        return consumer

    def get_producer(self) -> Producer:
        """
        Creates a Confluent Kafka producer.

        :return: Configured Kafka producer.
        :rtype: Producer
        """
        producer_config = {
            'bootstrap.servers': self.config['kafka']['bootstrap_servers']
        }
        return Producer(producer_config)

    def _commit_pending(self, consumer: Consumer, pending: Dict[Tuple[str, int], Any]) -> None:
        """Commit the highest offset seen for each partition in the batch."""
        for msg in pending.values():
            try:
                consumer.commit(msg)
            except KafkaException as ke:
                logger.error(f"Failed to commit batched offset: {ke}")

    def get_consumed_messages(self, consumer: Consumer, batch_size: Optional[int] = None) -> Dict[str, List[Tuple[str, str]]]:
        """
        Consumes a batch of messages from a Kafka topic and manually commits the offsets.

        With ``kafka.batch_commit`` enabled the commit is deferred to the end of
        the batch instead of being issued per message. Offsets are monotonic
        within a partition and every intermediate message is already in the
        returned batch, so committing the highest offset per partition is
        equivalent to committing each in turn.

        The flush below happens before this returns, so callers never receive
        uncommitted messages: batching does not make the pipeline
        at-least-once, it opens a redelivery window of one poll batch, entered
        only when a crash lands inside the loop (see README "Crash and replay
        semantics").

        :param consumer: The Confluent Kafka consumer.
        :param batch_size: The number of messages to consume in a single batch. Defaults to kafka.max_poll_records.
        :return: A dictionary with partition keys and lists of consumed messages (key, value).
        :rtype: Dict[str, List[Tuple[str, str]]]
        """
        messages = {}
        pending_commits: Dict[Tuple[str, int], Any] = {}
        try:
            # Resolve effective batch size from argument or configuration
            effective_batch_size = batch_size if batch_size is not None else self.config['kafka'].get('max_poll_records', 10)

            for _ in range(effective_batch_size):  # Loop to fetch up to `batch_size` messages
                msg = consumer.poll(
                    timeout=self.config['kafka']['poll_timeout'] / 1000
                )
                if msg is None:
                    break  # No message available, stop polling this topic

                if msg.error():
                    if msg.error().code() == KafkaError._PARTITION_EOF:
                        logger.debug(
                            "End of partition reached, continuing polling.")
                        continue  # End of partition, keep polling
                    else:
                        raise KafkaException(msg.error())
                else:
                    partition_key = f"{msg.topic()}-{msg.partition()}"
                    if partition_key not in messages:
                        messages[partition_key] = []
                    # msg.timestamp() returns (timestamp_type, timestamp_ms)
                    # timestamp_type: 0=not available, 1=create time, 2=log append time
                    ts_type, kafka_timestamp_ms = msg.timestamp()
                    if ts_type == 0:
                        logger.debug("Kafka message has no timestamp available")
                        kafka_timestamp_ms = None
                    messages[partition_key].append((msg.key(), msg.value(), kafka_timestamp_ms))
                    if self.batch_commit:
                        pending_commits[(msg.topic(), msg.partition())] = msg
                    else:
                        # Manually commit the message offset
                        try:
                            consumer.commit(msg)
                        except KafkaException as ke:
                            logger.error(f"Failed to commit offset: {ke}")

        except KafkaException as e:
            logger.error(f"Kafka error: {e}")
        finally:
            if pending_commits:
                self._commit_pending(consumer, pending_commits)

        return messages
