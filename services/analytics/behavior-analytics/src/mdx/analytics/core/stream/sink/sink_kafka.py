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

from confluent_kafka import Producer
from mdx.analytics.core.schema.config import AppConfig
from mdx.analytics.core.stream.sink.sink_base import Sink
from typing import Any
from collections.abc import Callable, Mapping

logger = logging.getLogger(__name__)


class SinkKafka(Sink):

    def __init__(self, config: AppConfig) -> None:
        """Initialize the Kafka sink with configuration.
        
        :param AppConfig config: Application configuration containing Kafka settings.
        """

        self._config = config.kafka
        self._get_topic = config.get_kafka_topic
        self._producer: Producer | None = None


    def write(
        self,
        dest_key: str,
        messages: list[Any],
        value_serializer: Callable,
        key_extractor: Callable | None = None,
        key_serializer: Callable | None = None,
        headers: Mapping[str, str | bytes] | None = None
    ) -> None:
        """Write multiple messages to a Kafka topic with optional key extraction and serialization.
        
        :param str dest_key: Key to identify the target Kafka topic.
        :param list[Any] messages: List of messages to write to Kafka.
        :param Callable value_serializer: Function to serialize message values.
        :param Callable | None, optional key_extractor: Function to extract keys from messages.
        :param Callable | None, optional key_serializer: Function to serialize message keys.
        :param Mapping[str, str | bytes] | None headers: Optional headers to include with messages.
        
        :raises ValueError: If no Kafka topic is found for the given dest_key.
        """

        topic = self._get_topic(dest_key)
        if not topic:
            raise ValueError(f"Could not find a kafka topic with key: {dest_key}")

        producer = self._get_producer()

        for message in messages:

            key = None

            if key_extractor:
                key = key_extractor(message)
                if key_serializer:
                    key = key_serializer(key)

            producer.produce(
                topic = topic,
                key = key,
                value = value_serializer(message),
                on_delivery = self._delivery_callback,
                headers = list(headers.items()) if headers else []
            )

        producer.poll(0)


    def write_msg(
        self,
        dest_key: str,
        message: bytes,
        key: bytes | None,
        headers: Mapping[str, str | bytes] | None = None
    ) -> None:
        """Write a single message to a Kafka topic.
        
        :param str dest_key: Key to identify the target Kafka topic.
        :param bytes message: Message content to write to Kafka.
        :param bytes | None key: Optional message key.
        :param Mapping[str, str | bytes] | None headers: Optional headers to include with the message.
        
        :raises ValueError: If no Kafka topic is found for the given dest_key.
        """

        topic = self._get_topic(dest_key)
        if not topic:
            raise ValueError(f"Could not find a kafka topic with key: {dest_key}")

        producer = self._get_producer()

        producer.produce(
            topic = topic,
            key = key,
            value = message,
            on_delivery = self._delivery_callback,
            headers = list(headers.items()) if headers else []
        )

        producer.poll(0)


    def close(self) -> None:
        """Close the Kafka producer and flush any pending messages."""

        if self._producer:
            self._producer.flush()


    def _get_producer(self) -> Producer:
        """Get or create the Kafka producer instance.
        
        :return Producer: Configured Confluent Kafka producer instance.
        """

        if not self._producer:

            self._producer = Producer({
                'bootstrap.servers': self._config.brokers,
                'linger.ms': self._config.producer.lingerMs,
                'batch.size': self._config.producer.batchSize,
                'message.max.bytes': self._config.producer.messageMaxBytes
            })

        return self._producer


    def _delivery_callback(self, err, msg):
        """Callback function for Kafka message delivery confirmation.
        
        :param err: Error object if delivery failed, None if successful.
        :param msg: Message object containing delivery details.
        """

        if err is not None:
            logger.warning(f"Message delivery failed: {err} for message: {msg}")

        else:
            logger.debug(f"Message delivered to {msg.topic()} [{msg.partition()}]")
