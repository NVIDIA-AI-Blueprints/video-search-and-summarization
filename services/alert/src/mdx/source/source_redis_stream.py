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
# Shared partition-key guardrail: it reports whether the envelope key matches
# the payload sensorId, which dedup cohort affinity depends on regardless of
# transport.
from mdx.source.source_utils import record_key_alignment, record_source_drop
from mdx.stream_message import StreamMessage

DEFAULT_BLOCK_MS = 100
DEFAULT_COUNT = 10
DEFAULT_ERROR_BACKOFF_SECONDS = 1.0
#: New consumer groups start at ``$`` (new entries only) to match the Kafka
#: source's ``auto_offset_reset: latest``.
DEFAULT_START_ID = "$"
#: ``transport`` label on the read-path drop counter.
SOURCE_TRANSPORT = "redis_stream"
#: How often to sweep for entries stranded in a dead consumer's pending list.
#: Only runs on an otherwise-idle poll, so this is a floor, not a schedule.
DEFAULT_RECLAIM_INTERVAL_SECONDS = 30.0

#: Event kinds the pipeline can actually decode. The kind comes from the
#: configured stream key and selects the protobuf schema downstream, where
#: anything that is not ``incident`` is decoded as a Behavior — so a typo in a
#: stream key does not fail, it silently decodes every incident with the wrong
#: schema. Validating the key at construction is what turns that into an error
#: an operator sees at boot.
#:
#: ``anomaly`` is the legacy spelling of ``alert`` carried by the pre-
#: ``event_bridge`` configuration layout; it decodes as a Behavior, same as
#: ``alert``, and is accepted so an existing config keeps working.
SUPPORTED_KINDS = ("incident", "alert", "anomaly")
HEARTBEAT_KIND = "heartbeat"


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
            if kind == HEARTBEAT_KIND:
                self.heartbeat_stream = stream
                continue
            if kind not in SUPPORTED_KINDS:
                raise ValueError(
                    f"event_bridge.redis_source.streams has an unsupported key "
                    f"'{name}'. The key names the event kind and selects the "
                    f"decode schema, so it must be one of "
                    f"{', '.join(SUPPORTED_KINDS)} (or '{HEARTBEAT_KIND}')."
                )
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
        self._reclaim_interval = float(
            consumer_config.get('reclaim_interval', DEFAULT_RECLAIM_INTERVAL_SECONDS)
        )
        self._last_reclaim_at = 0.0
        # False once a group assertion or read fails, so readiness can tell an
        # unreachable broker from an idle stream. Starts True and is corrected
        # by the constructor's own group assertion below.
        self._groups_ready = True

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
        self._groups_ready = all(results)
        return self._groups_ready

    def is_ready(self) -> bool:
        """Whether this consumer currently holds a usable connection and group.

        Overrides the base source's unconditional ``True``. Redis has no
        assignment to wait for, but it does have a connection, and an
        unreachable broker yields the same empty entry list as an idle stream —
        so without this the process publishes itself as ready and ``/health``
        answers 200 while nothing is being consumed at all.
        """
        return self._groups_ready and self.broker.connection_healthy is not False

    def await_ready(self, timeout: float = 60.0) -> bool:
        """Wait for Redis to answer and the consumer group to exist.

        The startup analogue of the Kafka source's group join: the caller turns
        a ``False`` into a failed start, so a deployment pointed at a Redis that
        is not there fails visibly instead of idling.
        """
        deadline = time.monotonic() + max(timeout, 0.0)
        attempt = 0
        while True:
            attempt += 1
            if self.broker.ping() and self._ensure_groups():
                return True
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                self.logger.error(
                    "Redis at %s:%s did not accept consumer group '%s' within "
                    "%.0fs (%d attempt(s)); nothing will be consumed",
                    self.broker.host, self.broker.port, self.consumer_group,
                    timeout, attempt,
                )
                return False
            time.sleep(min(self._error_backoff, remaining))

    def _read_entries(self, streams: List[str]) -> List[Tuple[str, bytes, Dict[Any, Any]]]:
        """Read new entries, plus anything a dead consumer left pending.

        ``XREADGROUP`` blocks for ``block_ms`` so an idle stream does not spin
        the consume loop. A broker outage returns immediately, though, so the
        backoff sleep is what keeps the loop from becoming a hot loop.

        The reclaim sweep runs only when the read came back empty: reclaimed
        entries are by definition not urgent, and doing it on every poll would
        add an XAUTOCLAIM per stream to the hot path for no benefit.
        """
        if not self._ensure_groups():
            time.sleep(self._error_backoff)
            return []
        entries = self.broker.read_group(
            streams=streams,
            group=self.consumer_group,
            consumer=self.consumer_name,
            count=self.count,
            block_ms=self.block_ms,
        )
        if entries:
            return entries
        return self._reclaim_stale(streams)

    def _reclaim_stale(self, streams: List[str]) -> List[Tuple[str, bytes, Dict[Any, Any]]]:
        """Claim entries stranded in another consumer's pending list.

        Throttled to ``reclaim_interval``. Returns them in read order so the
        caller decodes and acks them by exactly the same path as new entries.
        """
        if self._reclaim_interval <= 0:
            return []
        now = time.monotonic()
        if now - self._last_reclaim_at < self._reclaim_interval:
            return []
        self._last_reclaim_at = now

        reclaimed: List[Tuple[str, bytes, Dict[Any, Any]]] = []
        for stream in streams:
            reclaimed.extend(
                self.broker.claim_stale(
                    stream=stream,
                    group=self.consumer_group,
                    consumer=self.consumer_name,
                    count=self.count,
                )
            )
        return reclaimed

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

        def accept(stream: str, message_id: Any) -> None:
            """Mark an entry as decided, and therefore safe to acknowledge.

            Called at each terminal outcome rather than on arrival. The
            distinction matters because an entry acked before it has been
            examined is unrecoverable — it is out of the pending list and
            XREADGROUP will never offer it again — so an early ack turns any
            path that fails to reach acceptance into silent loss. Every
            ``continue`` below therefore has to say what it decided.
            """
            acks.setdefault(stream, []).append(message_id)

        for stream, message_id, fields in entries:
            payload, key, _ = extract_envelope(fields)
            if payload is None:
                # Decided: unusable, and replaying it forever would wedge the
                # consumer behind it. Counted so a producer writing the wrong
                # envelope is visible as more than a log line.
                self.logger.warning(
                    "Skipping Redis entry %r on '%s': no payload field", message_id, stream
                )
                record_source_drop(SOURCE_TRANSPORT, "no_payload")
                accept(stream, message_id)
                continue

            kind = self.stream_to_kind.get(stream)
            if kind not in SUPPORTED_KINDS:
                # Only reachable for a reclaimed entry from a stream that has
                # since been removed from the configuration. Decoding it would
                # mean guessing a schema, so drop it rather than route it wrong.
                self.logger.warning(
                    "Skipping Redis entry %r on '%s': stream is not mapped to a "
                    "supported event kind", message_id, stream,
                )
                record_source_drop(SOURCE_TRANSPORT, "unmapped_kind")
                accept(stream, message_id)
                continue

            published_ms = message_id_to_epoch_ms(message_id)
            if published_ms and (earliest_published_ms is None or published_ms < earliest_published_ms):
                earliest_published_ms = published_ms

            record_key_alignment(key, payload)

            if self._decode_json_payload(payload) is not None:
                grouped.setdefault((kind, 'json'), []).append(payload.decode('utf-8'))
            else:
                grouped.setdefault((kind, 'protobuf'), []).append((key, payload, published_ms))
            accept(stream, message_id)

        # Acked once every entry has been decided, matching the Kafka source's
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
            payload, _key, _headers = extract_envelope(fields)
            if payload is not None:
                payloads.append(payload)
            else:
                record_source_drop(SOURCE_TRANSPORT, "no_payload")
            acks.setdefault(stream, []).append(message_id)
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
            try:
                messages.append(
                    StreamMessage.from_redis_stream(stream, message_id, fields, 'request_schema.yaml')
                )
            except Exception as exc:
                self.logger.error("Error processing Redis entry %r on '%s': %s", message_id, stream, exc)
                record_source_drop(SOURCE_TRANSPORT, "undecodable")
            # Acked either way: decoded, or a poison pill that would otherwise
            # be redelivered on every poll for the life of the deployment.
            acks.setdefault(stream, []).append(message_id)
        self._ack(acks)
        return messages

    def close(self) -> None:
        """Release the Redis connection."""
        self.broker.close()
