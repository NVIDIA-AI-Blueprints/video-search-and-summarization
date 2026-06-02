# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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
import redis
import json

from mdx.analytics.core.schema.config import AppConfig
from mdx.analytics.core.stream.sink.sink_base import Sink
from typing import Any
from collections.abc import Callable, Mapping

logger = logging.getLogger(__name__)


class SinkRedisStream(Sink):

    def __init__(self, config: AppConfig) -> None:
        """Initialize the Redis Stream sink with configuration.

        :param AppConfig config: Application configuration containing Redis stream settings.
        """

        self._config = config.redisStream
        self._get_stream = config.get_redis_stream
        self._conn: redis.Redis | None = None


    def write(
        self,
        dest_key: str,
        messages: list[Any],
        value_serializer: Callable,
        key_extractor: Callable | None = None,
        key_serializer: Callable | None = None,
        headers: Mapping[str, str | bytes] | None = None
    ) -> None:
        """Write multiple messages to a Redis stream with optional key extraction and serialization.

        :param str dest_key: The destination key to identify the Redis stream.
        :param list[Any] messages: List of messages to write to the stream.
        :param Callable value_serializer: Function to serialize message values.
        :param Callable | None key_extractor: Function to extract keys from messages. Defaults to None.
        :param Callable | None key_serializer: Function to serialize extracted keys. Defaults to None.
        :param Mapping[str, str | bytes] | None headers: Optional headers to include with messages.
        :raises ValueError: If the Redis stream with the specified dest_key cannot be found.
        """

        stream = self._get_stream(dest_key)
        if not stream:
            raise ValueError(f"Could not find a redis stream with key: {dest_key}")

        producer = self._get_producer()

        for message in messages:

            key = None

            if key_extractor:
                key = key_extractor(message)
                if key_serializer:
                    key = key_serializer(key)

            producer.xadd(
                name = stream,
                fields = {
                    'key': key if key else b'',
                    'value': value_serializer(message),
                    'headers': self._serialize_headers(headers)
                },
                maxlen = self._config.producer.maxLen,
                approximate= True
            )


    def write_msg(
        self,
        dest_key: str,
        message: bytes,
        key: bytes | None,
        headers: Mapping[str, str | bytes] | None = None
    ) -> None:
        """Write a single pre-serialized message to a Redis stream.

        :param str dest_key: The destination key to identify the Redis stream.
        :param bytes message: The pre-serialized message to write.
        :param bytes | None key: Optional key associated with the message.
        :param Mapping[str, str | bytes] | None headers: Optional headers to include with the message.
        :raises ValueError: If the Redis stream with the specified dest_key cannot be found.
        """

        stream = self._get_stream(dest_key)
        if not stream:
            raise ValueError(f"Could not find a redis stream with key: {dest_key}")

        producer = self._get_producer()

        producer.xadd(
            name = stream,
            fields = {
                'key': key if key else b'',
                'value': message,
                'headers': self._serialize_headers(headers)
            },
            maxlen = self._config.producer.maxLen,
            approximate= True
        )


    def _serialize_headers(self, headers: Mapping[str, str | bytes] | None = None) -> str:
        """Serialize headers mapping to a JSON string.

        :param Mapping[str, str | bytes] | None headers: Optional headers mapping containing string or bytes values.
        :return str: JSON-serialized string representation of the headers.
        """

        if not headers:
            return '{}'

        payload_headers = { k: v.decode('utf-8') if isinstance(v, bytes) else v for k, v in headers.items() }
        return json.dumps(payload_headers)


    def close(self) -> None:
        """Close the Redis connection if it exists."""

        if self._conn:
            self._conn.close()


    def _get_producer(self) -> redis.Redis:
        """Get the Redis connection, creating it if it doesn't exist.

        :return redis.Redis: The Redis connection instance.
        """

        if not self._conn:

            self._conn = redis.Redis(
                host = self._config.host,
                port = self._config.port,
                db = self._config.db or 0
            )

        return self._conn
