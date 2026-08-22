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

"""Redis Streams transport primitives.

Peer of :mod:`mdx.kafka_message_broker`: owns connection handling and the
raw XADD / XREADGROUP / XACK calls so the source and sink modules only deal
with Alert payload semantics.

Wire format is the MDX stream envelope used by every other Redis Streams
producer and consumer in this repository (behavior-analytics, VIOS,
rt-cv-bev-fusion, the Logstash ``redis_stream`` input plugin)::

    XADD <stream> MAXLEN ~ <n> * key <sensorId> value <payload> headers <json>

``value`` carries the payload — protobuf bytes for the MDX schema streams.
Sticking to this envelope is what lets Alert MS read the incident and alert
streams that behavior-analytics writes, and lets Logstash read the
VLM-enhanced streams Alert MS writes.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, Iterable, List, Optional, Tuple

import redis

logger = logging.getLogger(__name__)

DEFAULT_HOST = "localhost"
DEFAULT_PORT = 6379
DEFAULT_MAXLEN = 10000
DEFAULT_SOCKET_TIMEOUT = 30

#: Canonical MDX envelope fields.
KEY_FIELD = b"key"
PAYLOAD_FIELD = b"value"
HEADERS_FIELD = b"headers"

#: Alternate payload fields accepted on the read path only. ``metadata`` is the
#: RT-VLM default (``REDIS_PAYLOAD_KEY``); ``data`` and ``payload`` were used by
#: the pre-MDX Alert Redis prototype. Publishing always uses ``value``.
FALLBACK_PAYLOAD_FIELDS: Tuple[bytes, ...] = (b"metadata", b"data", b"payload")


def resolve_redis_config(
    config: Dict[str, Any],
    section: Optional[str] = None,
    override: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Merge the top-level ``redis`` block with per-component overrides.

    Mirrors how Kafka resolves connection settings: the top-level ``redis``
    block holds the connection (the analogue of ``kafka.bootstrap_servers``)
    while ``event_bridge.redis_source`` / ``event_bridge.redis_sink`` hold the
    stream names and may override any connection field.

    Args:
        config: Full service configuration dictionary.
        section: Key under ``event_bridge`` to overlay, e.g. ``redis_source``.
        override: Explicit overlay applied last, for components that live
            outside ``event_bridge`` such as ``vlm_enhanced_sink``.

    Returns:
        Merged settings dictionary. Never ``None``.
    """
    merged: Dict[str, Any] = dict(config.get("redis") or {})
    for overlay in ((config.get("event_bridge") or {}).get(section) if section else None, override):
        if overlay:
            merged.update({k: v for k, v in overlay.items() if v is not None})
    return merged


def message_id_to_epoch_ms(message_id: Any) -> Optional[int]:
    """Extract the millisecond timestamp encoded in a Redis stream entry ID.

    Redis stream IDs are ``<ms>-<seq>``, so the publish time is available
    without the producer having to stamp it. This is the Redis analogue of the
    Kafka record timestamp and feeds the same end-to-end latency metrics.
    """
    if message_id is None:
        return None
    try:
        raw = message_id.decode("utf-8") if isinstance(message_id, (bytes, bytearray)) else str(message_id)
        ms = int(raw.split("-", 1)[0])
        return ms if ms > 0 else None
    except (ValueError, AttributeError):
        return None


def extract_envelope(fields: Dict[Any, Any]) -> Tuple[Optional[bytes], Optional[bytes], Dict[str, Any]]:
    """Split a stream entry's field map into ``(payload, key, headers)``.

    Tolerates both ``bytes`` and ``str`` field names so the helper works
    whether or not the caller enabled ``decode_responses``.
    """
    if not fields:
        return None, None, {}

    normalized: Dict[bytes, Any] = {}
    for name, value in fields.items():
        if isinstance(name, str):
            name = name.encode("utf-8")
        normalized[name] = value

    payload = normalized.get(PAYLOAD_FIELD)
    if payload is None:
        for candidate in FALLBACK_PAYLOAD_FIELDS:
            if candidate in normalized:
                payload = normalized[candidate]
                break

    if isinstance(payload, str):
        payload = payload.encode("utf-8")

    key = normalized.get(KEY_FIELD)
    if isinstance(key, str):
        key = key.encode("utf-8")

    headers: Dict[str, Any] = {}
    raw_headers = normalized.get(HEADERS_FIELD)
    if raw_headers:
        try:
            if isinstance(raw_headers, (bytes, bytearray)):
                raw_headers = raw_headers.decode("utf-8")
            decoded = json.loads(raw_headers)
            if isinstance(decoded, dict):
                headers = decoded
        except (ValueError, UnicodeDecodeError):
            logger.debug("Ignoring non-JSON headers field on Redis stream entry")

    return payload, key, headers


class RedisStreamBroker:
    """Connection-managing wrapper around the Redis Streams commands.

    The client is created lazily and rebuilt after a connection error so a
    Redis restart does not require an Alert MS restart. Read failures are
    reported to the caller rather than raised, because the consume loop must
    survive a broker outage.
    """

    def __init__(self, redis_config: Dict[str, Any]) -> None:
        cfg = dict(redis_config or {})
        self.host: str = cfg.get("host") or DEFAULT_HOST
        self.port: int = int(cfg.get("port") or DEFAULT_PORT)
        self.db: int = int(cfg.get("db") or 0)
        self.password: Optional[str] = cfg.get("password") or None
        self.username: Optional[str] = cfg.get("username") or None
        self.maxlen: Optional[int] = self._coerce_maxlen(cfg.get("maxlen", DEFAULT_MAXLEN))
        self.approximate_trim: bool = bool(cfg.get("approximate_trim", True))
        self._socket_timeout = cfg.get("socket_timeout", DEFAULT_SOCKET_TIMEOUT)
        self._socket_connect_timeout = cfg.get("socket_connect_timeout", DEFAULT_SOCKET_TIMEOUT)
        self._client: Optional[redis.Redis] = None
        self._ensured_groups: set = set()
        self.logger = logging.getLogger(self.__class__.__name__)

    @staticmethod
    def _coerce_maxlen(value: Any) -> Optional[int]:
        """Return a positive MAXLEN cap, or ``None`` to disable trimming."""
        try:
            maxlen = int(value)
        except (TypeError, ValueError):
            return DEFAULT_MAXLEN
        return maxlen if maxlen > 0 else None

    @property
    def client(self) -> redis.Redis:
        """Return the live client, creating it on first use."""
        if self._client is None:
            # decode_responses stays off: payloads are protobuf bytes.
            self._client = redis.Redis(
                host=self.host,
                port=self.port,
                db=self.db,
                username=self.username,
                password=self.password,
                decode_responses=False,
                socket_timeout=self._socket_timeout,
                socket_connect_timeout=self._socket_connect_timeout,
                retry_on_timeout=True,
            )
            self.logger.info(
                "Redis Streams client configured for %s:%s (db=%s)",
                self.host, self.port, self.db,
            )
        return self._client

    def _reset_client(self) -> None:
        """Drop the client and any cached group state so the next call reconnects.

        Consumer groups are re-asserted after a reconnect because the Redis
        instance may have been replaced (or its data flushed) while we were
        disconnected.
        """
        self._ensured_groups.clear()
        client, self._client = self._client, None
        if client is None:
            return
        try:
            client.close()
        except Exception:  # pragma: no cover - best-effort teardown
            pass

    def ping(self) -> bool:
        """Verify connectivity. Returns ``False`` instead of raising."""
        try:
            self.client.ping()
            return True
        except Exception as exc:
            self.logger.error("Redis ping failed for %s:%s: %s", self.host, self.port, exc)
            self._reset_client()
            return False

    def ensure_group(self, stream: str, group: str, start_id: str = "$") -> bool:
        """Create the consumer group (and stream) if it does not exist.

        ``start_id`` defaults to ``$`` (new entries only) to match the Kafka
        source's ``auto_offset_reset: latest``. Pass ``0-0`` to replay history.
        """
        cache_key = (stream, group)
        if cache_key in self._ensured_groups:
            return True
        try:
            self.client.xgroup_create(stream, group, id=start_id, mkstream=True)
            self.logger.info("Created consumer group '%s' on stream '%s'", group, stream)
        except redis.exceptions.ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                self.logger.error("Failed to create consumer group '%s' on '%s': %s", group, stream, exc)
                return False
            self.logger.debug("Consumer group '%s' already exists on '%s'", group, stream)
        except redis.exceptions.RedisError as exc:
            self.logger.error("Redis unavailable while creating group '%s' on '%s': %s", group, stream, exc)
            self._reset_client()
            return False

        self._ensured_groups.add(cache_key)
        return True

    def read_group(
        self,
        streams: Iterable[str],
        group: str,
        consumer: str,
        count: int,
        block_ms: int,
    ) -> List[Tuple[str, bytes, Dict[Any, Any]]]:
        """Read new entries for ``group`` across ``streams`` in one round trip.

        Returns:
            Flat list of ``(stream_name, message_id, fields)`` tuples. Empty on
            timeout or when the broker is unreachable.

        Raises:
            redis.exceptions.RedisError: never — connection and response errors
                are logged and surfaced as an empty result so the consume loop
                keeps running.
        """
        stream_list = [s for s in streams if s]
        if not stream_list:
            return []

        try:
            response = self.client.xreadgroup(
                groupname=group,
                consumername=consumer,
                streams={stream: ">" for stream in stream_list},
                count=count,
                block=block_ms,
            )
        except redis.exceptions.ConnectionError as exc:
            self.logger.error("Redis connection lost while reading streams %s: %s", stream_list, exc)
            self._reset_client()
            return []
        except redis.exceptions.TimeoutError:
            self.logger.debug("Redis read timed out with no new entries")
            return []
        except redis.exceptions.ResponseError as exc:
            # NOGROUP means the stream or group vanished (e.g. FLUSHDB); drop
            # the cache so the next poll recreates it.
            if "NOGROUP" in str(exc):
                self.logger.warning("Consumer group missing on read, will recreate: %s", exc)
                self._ensured_groups.clear()
            else:
                self.logger.error("Redis rejected XREADGROUP on %s: %s", stream_list, exc)
            return []
        except redis.exceptions.RedisError as exc:
            self.logger.error("Redis error while reading streams %s: %s", stream_list, exc)
            return []

        entries: List[Tuple[str, bytes, Dict[Any, Any]]] = []
        for stream_name, messages in response or []:
            if isinstance(stream_name, (bytes, bytearray)):
                stream_name = stream_name.decode("utf-8")
            for message_id, fields in messages or []:
                entries.append((stream_name, message_id, fields))
        return entries

    def ack(self, stream: str, group: str, message_ids: List[Any]) -> None:
        """Acknowledge ``message_ids`` so they leave the pending entries list."""
        if not message_ids:
            return
        try:
            self.client.xack(stream, group, *message_ids)
        except redis.exceptions.ConnectionError as exc:
            self.logger.error("Redis connection lost while acking %s entries on '%s': %s",
                              len(message_ids), stream, exc)
            self._reset_client()
        except redis.exceptions.RedisError as exc:
            self.logger.error("Failed to ack %s entries on '%s': %s", len(message_ids), stream, exc)

    def add(
        self,
        stream: str,
        payload: bytes,
        key: Any = b"",
        headers: Optional[Dict[str, Any]] = None,
    ) -> Optional[bytes]:
        """Publish ``payload`` to ``stream`` using the MDX envelope.

        Returns:
            The generated entry ID, or ``None`` if the write failed.
        """
        if isinstance(key, str):
            key = key.encode("utf-8")
        elif key is None:
            key = b""

        fields = {
            KEY_FIELD: key,
            PAYLOAD_FIELD: payload,
            HEADERS_FIELD: json.dumps(headers or {}),
        }

        kwargs: Dict[str, Any] = {}
        if self.maxlen is not None:
            kwargs["maxlen"] = self.maxlen
            kwargs["approximate"] = self.approximate_trim

        try:
            return self.client.xadd(stream, fields, **kwargs)
        except redis.exceptions.ConnectionError as exc:
            self.logger.error("Redis connection lost while writing to '%s': %s", stream, exc)
            self._reset_client()
            return None
        except redis.exceptions.RedisError as exc:
            self.logger.error("Failed to write to Redis stream '%s': %s", stream, exc)
            return None

    def close(self) -> None:
        """Release the connection."""
        if self._client is None:
            return
        try:
            self._client.close()
            self.logger.info("Redis Streams connection closed")
        except Exception as exc:  # pragma: no cover - best-effort teardown
            self.logger.error("Error closing Redis connection: %s", exc)
        finally:
            self._client = None
            self._ensured_groups.clear()
