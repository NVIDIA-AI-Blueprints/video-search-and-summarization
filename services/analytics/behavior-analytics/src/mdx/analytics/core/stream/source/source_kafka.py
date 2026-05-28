# SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError

from confluent_kafka import Consumer
from mdx.analytics.core.schema.config import AppConfig
from mdx.analytics.core.stream.source.source_base import Source
from mdx.analytics.core.schema.models import StreamMessage

logger = logging.getLogger(__name__)

# Wall-clock bound on how long the caller waits for Consumer.close() before
# abandoning the worker thread. librdkafka's close() has no timeout knob, so
# this is enforced externally via a worker thread + future.result(timeout).
CLOSE_TIMEOUT_SECONDS = 5.0


class SourceKafka(Source):
    """Kafka source implementation for reading messages from Kafka topics.
    
    This class provides functionality to read messages from Kafka topics using
    the confluent-kafka library. It manages multiple consumers and handles
    connection retry logic.
    """

    def __init__(self, config: AppConfig) -> None:
        """Initialize the Kafka source with application configuration.
        
        :param AppConfig config: Application configuration containing Kafka settings
            including brokers, consumer settings, and topic mappings.
        """

        self._config = config.kafka
        self._get_topic = config.get_kafka_topic
        self._consumers: dict[str, Consumer] = {}


    def read(
        self,
        src_key: str,
        group_id_suffix: str | None = None
    ) -> list[StreamMessage]:
        """Read messages from a Kafka topic identified by the source key.
        
        :param str src_key: The source key used to identify the Kafka topic.
        :param group_id_suffix: Optional suffix to append to the group ID.
            Defaults to None.
        :param str | None group_id_suffix: Optional suffix to append to the consumer group ID.
        :return list[StreamMessage]: List of stream messages read from the Kafka topic.
            Each message contains key, value, and headers.
        :raises ValueError: If no Kafka topic is found for the provided src_key.
        """

        topic = self._get_topic(src_key)
        if not topic:
            raise ValueError(f"Could not find a kafka topic with key: {src_key}")

        group_id = self._get_group_id(topic, self._config.group, group_id_suffix)
        consumer = self._get_consumer(topic, group_id)

        messages = consumer.consume(num_messages = self._config.consumer.maxPollRecords, timeout = self._config.consumer.timeout)

        result = []
        for msg in messages:

            if msg is not None:
    
                if not msg.error():
                    stream_msg = StreamMessage(key = msg.key(), value = msg.value(), headers = dict(msg.headers() or []), timestamp = msg.timestamp()[1])
                    result.append(stream_msg)

                else:
                    logger.error(f"Failed to consume message: {msg.error()}")

        return result


    def close(self) -> None:
        """Close all Kafka consumers and clear the consumers cache.
        
        This method should be called when shutting down to properly release
        Kafka consumer resources.
        """

        for consumer in self._consumers.values():
            self._close_consumer_with_timeout(consumer)

        self._consumers.clear()

    def _close_consumer_with_timeout(self, consumer: Consumer, timeout: float = CLOSE_TIMEOUT_SECONDS) -> bool:
        """Close consumer with a hard wall-clock bound on the caller's wait.

        :param Consumer consumer: Consumer to close.
        :param float timeout: Seconds to wait before abandoning the close.
            Defaults to ``CLOSE_TIMEOUT_SECONDS``.
        :return bool: ``True`` if close finished within the timeout (including
            the case where close raised — the worker still terminated).
            ``False`` if the timeout fired and the worker thread was abandoned.

        Implementation note: we deliberately do *not* use
        ``with ThreadPoolExecutor(...) as executor:``. ``__exit__`` calls
        ``shutdown(wait=True)`` which would block until the (potentially hung)
        worker thread finishes, defeating the timeout. We manage shutdown
        explicitly with ``wait=False`` so the caller's wait is bounded even
        when ``consumer.close()`` itself never returns.
        """

        def close_consumer():
            try:
                consumer.unsubscribe()
                consumer.close()
                logger.info(f"Consumer {id(consumer)} closed successfully")
            except Exception as e:
                logger.warning(f"Failed to close consumer {id(consumer)}: {e}", exc_info=True)

        executor = ThreadPoolExecutor(max_workers=1)
        try:
            executor.submit(close_consumer).result(timeout=timeout)
            return True
        except FutureTimeoutError:
            logger.warning(
                f"Consumer {id(consumer)} close timed out after {timeout} seconds; "
                f"abandoning thread (will leak until process exit)"
            )
            return False
        finally:
            executor.shutdown(wait=False)

    def _on_assign(self, group_id):
        """Create a callback function for partition assignment events.
        
        :param str group_id: The consumer group ID to associate with the consumer.
        :return callable: A callback function that handles partition assignment.
        """

        def on_assign(consumer, partitions):
            self._consumers[group_id] = consumer
            logger.info(
                f"Kafka consumer created and partitions assigned\n"
                f"  Brokers: {self._config.brokers}\n"
                f"  Consumer ID: {id(consumer)}\n"
                f"  Group ID: {group_id}\n"
                f"  Partitions: {partitions}"
            )
        return on_assign

    def _wait_for_assignment(self, consumer, group_id, max_attempts=100, interval_sec=0.1):
        """Wait for the consumer to be assigned partitions within the specified attempts.
        
        :param Consumer consumer: The Kafka consumer to wait for partition assignment.
        :param str group_id: The consumer group ID to check for assignment.
        :param int max_attempts: Maximum number of polling attempts. Defaults to 100.
        :param float interval_sec: Time interval in seconds between polling attempts. Defaults to 0.1.
        :return bool: True if partitions are assigned successfully, False otherwise.
        """

        for _ in range(max_attempts):
            # triggers group join and assignment
            consumer.poll(interval_sec)
            if self._consumers.get(group_id):
                return True
        return False

    def _get_consumer(self, topic: str, group_id: str) -> Consumer:
        """Get or create a Kafka consumer for the specified topic and group ID.
        
        This method implements retry logic for consumer creation and subscription.
        It attempts to create a consumer up to 3 times with 30-second intervals
        between attempts.
        
        :param str topic: The Kafka topic name to subscribe to.
        :param str group_id: The consumer group ID for the consumer.
        :return Consumer: A configured Kafka consumer subscribed to the specified topic.
        :raises Exception: If consumer creation fails after maximum retry attempts,
            or if no partitions are assigned to the consumer.
        """

        if not (consumer := self._consumers.get(group_id)):
            conf = {
                'bootstrap.servers': self._config.brokers,
                'group.id': group_id,
                'auto.offset.reset': self._config.consumer.autoOffsetReset,
                'enable.auto.commit': self._config.consumer.enableAutoCommit,
                'max.poll.interval.ms': self._config.consumer.maxPollIntervalMs,
                'max.partition.fetch.bytes': self._config.consumer.maxPartitionFetchBytes,
                'fetch.max.bytes': self._config.consumer.fetchMaxBytes,
            }

            max_attempts = self._config.consumer.retryMaxAttempts
            wait_between_attempts = self._config.consumer.retryIntervalSec

            for attempt in range(1, (max_attempts + 1)):
                logger.info(f"Creating Kafka consumer ({attempt} of {max_attempts})")
                consumer = Consumer(conf)
                try:
                    consumer.subscribe([topic], on_assign=self._on_assign(group_id))

                    if self._wait_for_assignment(consumer, group_id):
                        return consumer
                    logger.info(f"No partitions assigned at attempt {attempt} for topic: {topic} and group: {group_id}")

                except Exception as e:
                    logger.warning(
                        f"Error during consumer creation attempt {attempt}: {str(e)}",
                        exc_info=True
                    )

                # If close hangs the broker is genuinely unreachable; retrying just
                # leaks more threads. Bail out and let the container restart handle it.
                if not self._close_consumer_with_timeout(consumer):
                    raise RuntimeError(
                        f"FATAL - Consumer close hung on attempt {attempt}; aborting retry. "
                        f"Broker likely unreachable.\n"
                        f"  Brokers: {self._config.brokers}\n"
                        f"  Topic: {topic}\n"
                        f"  Group ID: {group_id}"
                    )

                if attempt < max_attempts:
                    logger.info(f"Waiting {wait_between_attempts} seconds before retrying...")
                    time.sleep(wait_between_attempts)

            logger.error(
                f"FATAL - Failed to initialize consumer after {max_attempts} attempts\n"
                f"  Brokers: {self._config.brokers}\n"
                f"  Topic: {topic}\n"
                f"  Group ID: {group_id}\n"
                f"  Max attempts: {max_attempts}"
            )
            raise RuntimeError("Consumer initialization failed")

        return self._consumers[group_id]
