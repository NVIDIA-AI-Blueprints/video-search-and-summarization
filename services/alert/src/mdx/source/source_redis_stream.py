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

"""Redis Streams source for the Alert event bridge.

Optional alternative to :class:`mdx.source.source_kafka.SourceKafka`, selected
with ``event_bridge.sourceType: redisStream``. Kafka remains the default.

The batch shape returned by :meth:`read_data` is identical to the Kafka
source's so ``AnomalyEnhancer.process_anomalies`` and ``process_batch_vlm``
need no transport-specific handling. Both payload encodings the MDX envelope
carries are supported: protobuf entries are emitted as Kafka-style
``(key, value, timestamp_ms)`` tuples and take the existing protobuf decode
path, while JSON entries are emitted as JSON strings.
"""

import json
import logging
import os
import socket
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from mdx.redis_stream_broker import (
    RedisStreamBroker,
    extract_envelope,
    message_id_to_epoch_ms,
    resolve_redis_config,
)
from mdx.source.source_base import SourceBase
# Reuse the Kafka source's partition-key guardrail: it reports whether the
# envelope key matches the payload sensorId, which dedup cohort affinity
# depends on regardless of transport.
from mdx.source.source_kafka import _record_key_alignment
from mdx.stream_message import StreamMessage

DEFAULT_BLOCK_MS = 100
DEFAULT_COUNT = 10
DEFAULT_ERROR_BACKOFF_SECONDS = 1.0
#: New consumer groups start at ``$`` (new entries only) to match the Kafka
#: source's ``auto_offset_reset: latest``.
DEFAULT_START_ID = "$"


class SourceRedisStream(SourceBase):
    """Redis Streams source implementation."""

    def __init__(self, config: dict):
        super().__init__(config)
        self.logger = logging.getLogger(self.__class__.__name__)

        section = (config.get('event_bridge') or {}).get('redis_source') or {}
        if not section:
            raise ValueError(
                "event_bridge.redis_source must be configured when sourceType is 'redisStream'"
            )

        self.broker = RedisStreamBroker(resolve_redis_config(config, 'redis_source'))

        self.heartbeat_stream: Optional[str] = None
        self.source_streams: List[str] = []
        self.stream_to_kind: Dict[str, str] = {}
        for name, stream in (section.get('streams') or {}).items():
            if not stream:
                continue
            kind = name[: -len('_stream')] if name.endswith('_stream') else name
            if kind == 'heartbeat':
                self.heartbeat_stream = stream
            else:
                self.source_streams.append(stream)
                self.stream_to_kind[stream] = kind

        if not self.source_streams:
            raise ValueError(
                "event_bridge.redis_source.streams must define at least one non-heartbeat stream"
            )

        self.consumer_group = section.get('consumer_group')
        if not self.consumer_group:
            raise ValueError("event_bridge.redis_source.consumer_group must be configured")

        # Unique per replica so scaled-out deployments share the group without
        # stealing each other's pending entries.
        self.consumer_name = f"alert-bridge-{socket.gethostname()}-{os.getpid()}"

        consumer_config = section.get('consumer_config') or {}
        self.count = int(consumer_config.get('count', DEFAULT_COUNT))
        self.block_ms = int(consumer_config.get('block_time', DEFAULT_BLOCK_MS))
        self.start_id = str(consumer_config.get('start_id', DEFAULT_START_ID))
        self._error_backoff = float(
            consumer_config.get('error_backoff', DEFAULT_ERROR_BACKOFF_SECONDS)
        )

        self.logger.info(
            "Redis Streams source reading %s as group '%s' (consumer '%s')",
            self.stream_to_kind, self.consumer_group, self.consumer_name,
        )
        self._ensure_groups()

    def _ensure_groups(self) -> bool:
        """Assert the consumer group exists on every configured stream."""
        streams = list(self.source_streams)
        if self.heartbeat_stream:
            streams.append(self.heartbeat_stream)
        results = [
            self.broker.ensure_group(stream, self.consumer_group, self.start_id)
            for stream in streams
        ]
        return all(results)

    def _read_entries(self, streams: List[str]) -> List[Tuple[str, bytes, Dict[Any, Any]]]:
        """Read pending-free new entries, backing off when Redis is unreachable.

        ``XREADGROUP`` blocks for ``block_ms`` so an idle stream does not spin
        the consume loop. A broker outage returns immediately, though, so the
        backoff sleep is what keeps the loop from becoming a hot loop.
        """
        if not self._ensure_groups():
            time.sleep(self._error_backoff)
            return []
        return self.broker.read_group(
            streams=streams,
            group=self.consumer_group,
            consumer=self.consumer_name,
            count=self.count,
            block_ms=self.block_ms,
        )

    def _ack(self, acks: Dict[str, List[Any]]) -> None:
        for stream, message_ids in acks.items():
            self.broker.ack(stream, self.consumer_group, message_ids)

    @staticmethod
    def _decode_json_payload(payload: bytes) -> Optional[dict]:
        """Return the decoded JSON object, or ``None`` when ``payload`` is not JSON.

        Used to tell the two MDX payload encodings apart: protobuf never parses
        as a JSON object, so a successful parse means the producer published
        JSON text.
        """
        try:
            decoded = json.loads(payload)
        except (ValueError, UnicodeDecodeError):
            return None
        return decoded if isinstance(decoded, dict) else None

    def read_data(self) -> List[Any]:
        """Read new entries and return batches grouped by kind and encoding.

        Shape matches ``SourceKafka.read_data()``:
        ``[{'kind': 'incident'|'alert', 'messages': [...], 'kafka_consumed_at': ...,
        'kafka_published_at': ...}, ...]``. ``messages`` holds Kafka-style
        tuples for protobuf payloads and JSON strings for JSON payloads; a
        single poll never mixes the two within one batch because
        ``process_batch_vlm`` dispatches on the batch's element type.
        """
        entries = self._read_entries(self.source_streams)
        if not entries:
            return []

        # (kind, encoding) -> messages
        grouped: Dict[Tuple[str, str], List[Any]] = {}
        acks: Dict[str, List[Any]] = {}
        earliest_published_ms: Optional[int] = None

        for stream, message_id, fields in entries:
            acks.setdefault(stream, []).append(message_id)

            payload, key, _ = extract_envelope(fields)
            if payload is None:
                self.logger.warning(
                    "Skipping Redis entry %r on '%s': no payload field", message_id, stream
                )
                continue

            published_ms = message_id_to_epoch_ms(message_id)
            if published_ms and (earliest_published_ms is None or published_ms < earliest_published_ms):
                earliest_published_ms = published_ms

            kind = self.stream_to_kind.get(stream, 'unknown')
            _record_key_alignment(key, payload)

            if self._decode_json_payload(payload) is not None:
                grouped.setdefault((kind, 'json'), []).append(payload.decode('utf-8'))
            else:
                grouped.setdefault((kind, 'protobuf'), []).append((key, payload, published_ms))

        # Acked once the batch is built, matching the Kafka source's
        # commit-on-consume (at-most-once) semantics.
        self._ack(acks)

        consumed_at = datetime.now(timezone.utc).isoformat()
        published_at = (
            datetime.fromtimestamp(earliest_published_ms / 1000, tz=timezone.utc).isoformat()
            if earliest_published_ms
            else None
        )

        return [
            {
                'kind': kind,
                'messages': messages,
                'kafka_consumed_at': consumed_at,
                'kafka_published_at': published_at,
            }
            for (kind, _encoding), messages in grouped.items()
            if messages
        ]

    def read(self) -> List[bytes]:
        """Read raw payload bytes from the configured streams."""
        entries = self._read_entries(self.source_streams)
        payloads: List[bytes] = []
        acks: Dict[str, List[Any]] = {}
        for stream, message_id, fields in entries:
            acks.setdefault(stream, []).append(message_id)
            payload, _key, _headers = extract_envelope(fields)
            if payload is not None:
                payloads.append(payload)
        self._ack(acks)
        return payloads

    def poll(self) -> List[StreamMessage]:
        """Read and deserialize JSON entries into StreamMessage objects."""
        return self._poll_streams(self.source_streams)

    def poll_heartbeats(self) -> List[StreamMessage]:
        """Read heartbeat entries."""
        if not self.heartbeat_stream:
            return []
        return self._poll_streams([self.heartbeat_stream])

    def _poll_streams(self, streams: List[str]) -> List[StreamMessage]:
        entries = self._read_entries(streams)
        messages: List[StreamMessage] = []
        acks: Dict[str, List[Any]] = {}
        for stream, message_id, fields in entries:
            acks.setdefault(stream, []).append(message_id)
            try:
                messages.append(
                    StreamMessage.from_redis_stream(stream, message_id, fields, 'request_schema.yaml')
                )
            except Exception as exc:
                self.logger.error("Error processing Redis entry %r on '%s': %s", message_id, stream, exc)
        self._ack(acks)
        return messages

    def close(self) -> None:
        """Release the Redis connection."""
        self.broker.close()
