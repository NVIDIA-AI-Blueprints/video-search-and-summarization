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

import json
import logging
import random
import time

import redis

from mdx.analytics.core.schema.config import AppConfig
from mdx.analytics.core.schema.models import StreamMessage
from mdx.analytics.core.stream.source.source_base import Source

logger = logging.getLogger(__name__)


class SourceRedisStream(Source):

    def __init__(self, config: AppConfig) -> None:
        """
        Initialize the Redis Stream source with configuration.
        
        :param AppConfig config: Application configuration containing Redis stream settings
        """

        self._config = config.redisStream
        self._get_stream = config.get_redis_stream
        self._consumer_groups = set()
        self._conn = None


    def read(
        self,
        src_key: str,
        group_id_suffix: str | None = None
    ) -> list[StreamMessage]:
        """
        Read messages from a Redis stream using consumer groups.
        
        :param str src_key: The key to identify the Redis stream
        :param str | None group_id_suffix: Optional suffix to append to the consumer group ID
        :return list[StreamMessage]: List of stream messages read from Redis
        :raises ValueError: If the Redis stream with the specified key cannot be found
        """

        stream = self._get_stream(src_key)
        if not stream:
            raise ValueError(f"Could not find a redis stream with key: {src_key}")

        group_id = self._get_group_id(stream, self._config.group, group_id_suffix)
        consumer = self._get_consumer(stream, group_id)

        resp = consumer.xreadgroup(
            groupname = group_id,
            consumername = self._consumer_name,
            block = self._config.consumer.readBlockMs,
            count = self._config.consumer.readCount,
            streams = { stream : '>' }
        )

        offsets = []
        result = []

        if resp:
            for _, messages in resp:    # type: ignore

                for msg_id, msg_payload in messages:

                    offsets.append(msg_id)

                    msg_val = msg_payload.get(b'value')
                    msg_key = msg_payload.get(b'key')
                    msg_headers = msg_payload.get(b'headers')
    
                    if msg_val:

                        stream_msg = StreamMessage(
                            key = msg_key, 
                            value = msg_val, 
                            headers = self._deserialize_headers(msg_headers),
                            timestamp = self._extract_timestamp_from_msg_id(msg_id)
                        )

                        result.append(stream_msg)

            consumer.xack(stream, group_id, *offsets)
 
        return result


    def _deserialize_headers(self, headers: str | bytes) -> dict[str, bytes]:
        """
        Deserialize JSON headers string into a mapping of string keys to byte values.
        
        :param str headers: JSON string containing message headers
        :return Mapping[str, bytes]: Dictionary with string keys and UTF-8 encoded byte values
        """

        msg_headers = json.loads(headers) if headers else dict()
        return { k: v.encode('utf-8') if isinstance(v, str) else v for k, v in msg_headers.items() }

    def _extract_timestamp_from_msg_id(self, msg_id: bytes) -> int:
        """
        Extract the timestamp from a Redis message ID.

        :param bytes msg_id: Redis message ID
        :return int: Timestamp in milliseconds
        """
        try:
            msg_id_str = msg_id.decode('utf-8') if isinstance(msg_id, bytes) else msg_id
            return int(msg_id_str.split('-')[0]) if '-' in msg_id_str else -1
        except Exception as e:
            logger.error(f"Error extracting timestamp from message ID: {e}")
            return -1

    def close(self) -> None:
        """
        Clean up resources by deleting consumers from consumer groups and closing the Redis connection.
        """

        if self._conn:

            for stream, group_id in self._consumer_groups:
                self._conn.xgroup_delconsumer(stream, group_id, self._consumer_name)

            self._conn.close()


    def _get_consumer(self, stream: str, group_id: str) -> redis.Redis:
        """
        Get or create a Redis consumer for the specified stream and consumer group.

        Retries the initial group setup on transient connection failures
        (``redis.ConnectionError``, ``redis.TimeoutError``) using
        ``retryMaxAttempts`` and ``retryIntervalSec`` from the consumer config.

        :param str stream: Name of the Redis stream
        :param str group_id: Consumer group identifier
        :return redis.Redis: Redis connection instance configured as a consumer
        :raises RuntimeError: If the consumer group cannot be set up after
            ``retryMaxAttempts`` connection failures.
        :raises redis.ResponseError: If a non-BUSYGROUP response error is raised
            during group creation (these are not retried — they indicate config
            or permissions problems, not transient outages).
        """

        if not self._conn:

            self._consumer_name = f'c{random.getrandbits(64)}'

            self._conn = redis.Redis(
                host = self._config.host,
                port = self._config.port,
                db = self._config.db or 0
            )
            logger.info(
                f"Successfully seeded Redis streams\n"
                f"  Host: {self._config.host}:{self._config.port}\n"
                f"  Consumer: {self._consumer_name}"
            )

        if (stream, group_id) not in self._consumer_groups:
            self._setup_consumer_group_with_retry(stream, group_id)
            self._consumer_groups.add((stream, group_id))

        return self._conn


    def _setup_consumer_group_with_retry(self, stream: str, group_id: str) -> None:
        """Run XGROUP CREATE + XGROUP CREATECONSUMER, retrying on connection errors.

        :param str stream: Stream name.
        :param str group_id: Consumer group id.
        :raises RuntimeError: If all retry attempts fail with connection errors.
        :raises redis.ResponseError: For non-BUSYGROUP response errors (not retried).
        """

        max_attempts = self._config.consumer.retryMaxAttempts
        wait_between = self._config.consumer.retryIntervalSec
        mk_stream = self._config.consumer.mkstream
        last_err: Exception | None = None

        for attempt in range(1, max_attempts + 1):
            try:
                try:
                    resp = self._conn.xgroup_create(stream, group_id, mkstream = mk_stream)
                    logger.info(
                        f"Created Redis consumer group\n"
                        f"  Stream: {stream}\n"
                        f"  Group ID: {group_id}\n"
                        f"  Auto-create stream: {mk_stream}\n"
                        f"  Response: {resp}"
                    )
                except redis.ResponseError as e:
                    if not str(e).startswith('BUSYGROUP'):  # cg already exists
                        raise

                self._conn.xgroup_createconsumer(stream, group_id, self._consumer_name)
                return

            except (redis.ConnectionError, redis.TimeoutError) as e:
                last_err = e
                logger.warning(
                    f"Redis connection error on attempt {attempt} of {max_attempts}: {e}",
                    exc_info=True,
                )
                if attempt < max_attempts:
                    logger.info(f"Waiting {wait_between} seconds before retrying...")
                    time.sleep(wait_between)

        raise RuntimeError(
            f"FATAL - Failed to set up Redis consumer group after {max_attempts} attempts\n"
            f"  Host: {self._config.host}:{self._config.port}\n"
            f"  Stream: {stream}\n"
            f"  Group ID: {group_id}\n"
            f"  Last error: {last_err}"
        ) from last_err
