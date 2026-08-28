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

Wire format on **publish** is the MDX stream envelope used by every other Redis
Streams producer and consumer in this repository (behavior-analytics, VIOS,
rt-cv-bev-fusion, the Logstash ``redis_stream`` input plugin)::

    XADD <stream> * key <sensorId> value <payload> headers <json>

``value`` carries the payload — protobuf bytes for the MDX schema streams.
Sticking to this envelope is what lets Alert MS read the incident and alert
streams that behavior-analytics writes, and lets Logstash read the
VLM-enhanced streams Alert MS writes.

On **read** two formats have to be decoded, and which field holds the event
body is a fixed contract rather than a guess — see
:data:`PAYLOAD_FIELD_PRECEDENCE`.
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Dict, Iterable, List, Optional, Tuple

import redis

# Guarded so a minimal environment without the metrics package cannot break the
# publish path.
try:  # pragma: no cover - exercised indirectly
    from metrics import recorder as _metrics
except Exception:  # pragma: no cover
    _metrics = None

logger = logging.getLogger(__name__)

DEFAULT_HOST = "localhost"
DEFAULT_PORT = 6379
#: Trimming is **off** unless an operator asks for it. The streams Alert MS
#: publishes to belong to the deployment, not to Alert MS, and a MAXLEN on every
#: XADD makes normal successful output delete a customer's older entries — a
#: retention decision this service is in no position to make on their behalf.
#: Set ``redis.maxlen`` to opt in.
DEFAULT_MAXLEN = 0
DEFAULT_SOCKET_TIMEOUT = 30
#: How long an entry must sit unacknowledged in another consumer's pending list
#: before this one may claim it. Longer than any single VLM verification so a
#: working consumer is never raced for its own work.
DEFAULT_RECLAIM_MIN_IDLE_MS = 300_000

#: Publish retries attempted before a payload is dropped. A redisStream sink has
#: no second destination, so an XADD lost to a broker blip is an already-verified
#: verdict gone for good. Kept small: the caller is on the consume path and the
#: source has already acked, so blocking here stalls the batch behind it.
DEFAULT_PUBLISH_RETRIES = 2
DEFAULT_PUBLISH_RETRY_BACKOFF = 0.1

#: Canonical MDX envelope fields.
KEY_FIELD = b"key"
PAYLOAD_FIELD = b"value"
HEADERS_FIELD = b"headers"

#: Ordered field precedence for locating the event body on the read path.
#: Publishing always uses ``value``.
#:
#: Two envelope formats reach this source, and both have to decode to the same
#: answer every time:
#:
#: 1. the **MDX envelope** — ``key`` / ``value`` / ``headers`` — where ``value``
#:    holds the body. This is what behavior-analytics and VIOS publish.
#: 2. the **JSON envelope** — ``data`` / ``timestamp`` / ``metadata`` — where
#:    ``data`` holds the body and ``metadata`` is a sidecar of attributes
#:    describing it. This is what RT-VLM and the pre-MDX Alert Redis prototype
#:    publish.
#:
#: ``metadata`` is accepted because it is RT-VLM's ``REDIS_PAYLOAD_KEY``
#: default, but it is deliberately **last**: an entry carrying both ``data`` and
#: ``metadata`` has a body *and* a sidecar, and reading the sidecar as the event
#: yields a payload that decodes but describes nothing the pipeline can verify.
#: Precedence rather than first-match-wins is what makes that unambiguous.
PAYLOAD_FIELD_PRECEDENCE: Tuple[bytes, ...] = (
    PAYLOAD_FIELD,
    b"data",
    b"payload",
    b"metadata",
)

#: Retained for callers that imported the previous name. Same contract, minus
#: the canonical ``value`` field which is tried first regardless.
FALLBACK_PAYLOAD_FIELDS: Tuple[bytes, ...] = PAYLOAD_FIELD_PRECEDENCE[1:]


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


def _resolve_secret(cfg: Dict[str, Any], name: str) -> Optional[str]:
    """Read a credential from a file or environment variable before the config.

    A customer-managed Redis needs a password, and the only place the plain
    ``redis.password`` key can come from is the rendered service config — which
    is a ConfigMap in Helm and a bind-mounted file in Compose, neither of which
    is a secret. ``<name>_file`` reads a mounted Secret and ``<name>_env`` reads
    an injected environment variable, so the credential never has to appear in
    non-secret configuration.

    Precedence is file, then environment, then the inline value. The inline key
    still works — existing deployments and local runs depend on it — but it is
    last so adding a Secret to one overrides it without also having to blank it.
    """
    path = cfg.get(f"{name}_file")
    if path:
        try:
            with open(path, "r", encoding="utf-8") as handle:
                # Trailing newline: `kubectl create secret --from-literal` and
                # `echo > file` both add one, and Redis would reject it as part
                # of the password.
                secret = handle.read().strip()
            if secret:
                return secret
            logger.warning("Redis %s_file '%s' is empty; falling back", name, path)
        except OSError as exc:
            logger.error("Could not read Redis %s_file '%s': %s", name, path, exc)

    env_name = cfg.get(f"{name}_env")
    if env_name:
        secret = (os.environ.get(str(env_name)) or "").strip()
        if secret:
            return secret
        logger.warning(
            "Redis %s_env names '%s' but it is unset or empty; falling back",
            name, env_name,
        )

    return cfg.get(name) or None


def _resolve_tls_options(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Build the redis-py TLS keyword arguments, or ``{}`` when TLS is off.

    Verification is on by default once TLS is enabled: an encrypted connection
    that does not check the certificate protects against nothing an operator who
    asked for TLS was worried about. ``ssl_cert_reqs: none`` is available for a
    self-signed development instance and says so in the config rather than being
    the silent default.
    """
    if not bool(cfg.get("ssl") or cfg.get("tls")):
        return {}

    options: Dict[str, Any] = {
        "ssl": True,
        "ssl_cert_reqs": str(cfg.get("ssl_cert_reqs") or "required").lower(),
    }
    for key in ("ssl_ca_certs", "ssl_ca_path", "ssl_certfile", "ssl_keyfile"):
        value = cfg.get(key)
        if value:
            options[key] = value
    if options["ssl_cert_reqs"] != "none" and not (
        options.get("ssl_ca_certs") or options.get("ssl_ca_path")
    ):
        # Not an error: the system trust store is the right source for a
        # publicly-issued certificate. Worth saying, because a private CA that
        # was configured but not mounted fails at connect with a bare
        # verification error that does not name the missing setting.
        logger.info(
            "Redis TLS is enabled with certificate verification and no "
            "ssl_ca_certs; the system trust store will be used"
        )
    return options


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

    The body is located by walking :data:`PAYLOAD_FIELD_PRECEDENCE` in order, so
    an entry carrying several candidate fields always decodes the same one. See
    that constant for why the order is what it is.

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

    payload = None
    for candidate in PAYLOAD_FIELD_PRECEDENCE:
        if normalized.get(candidate) is not None:
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
        self.password: Optional[str] = _resolve_secret(cfg, "password")
        self.username: Optional[str] = cfg.get("username") or None
        self.tls: Dict[str, Any] = _resolve_tls_options(cfg)
        self.maxlen: Optional[int] = self._coerce_maxlen(cfg.get("maxlen", DEFAULT_MAXLEN))
        self.approximate_trim: bool = bool(cfg.get("approximate_trim", True))
        self._socket_timeout = cfg.get("socket_timeout", DEFAULT_SOCKET_TIMEOUT)
        self._socket_connect_timeout = cfg.get("socket_connect_timeout", DEFAULT_SOCKET_TIMEOUT)
        self.publish_retries: int = self._coerce_retries(cfg.get("publish_retries", DEFAULT_PUBLISH_RETRIES))
        self.publish_retry_backoff: float = self._coerce_backoff(
            cfg.get("publish_retry_backoff", DEFAULT_PUBLISH_RETRY_BACKOFF)
        )
        self.reclaim_min_idle_ms: int = self._coerce_idle_ms(
            cfg.get("reclaim_min_idle_time", DEFAULT_RECLAIM_MIN_IDLE_MS)
        )
        self._client: Optional[redis.Redis] = None
        self._ensured_groups: set = set()
        # None until the first command runs. Distinguishes "not tried yet" from
        # "tried and failed", which readiness needs: an unreachable broker
        # returns the same empty entry list as an idle stream, so without this
        # the source cannot tell the two apart and reports healthy either way.
        self._connection_ok: Optional[bool] = None
        self.logger = logging.getLogger(self.__class__.__name__)

    @property
    def connection_healthy(self) -> Optional[bool]:
        """Whether the last Redis command succeeded; ``None`` before the first."""
        return self._connection_ok

    def _mark_connection(self, ok: bool) -> None:
        """Record the outcome of a command so readiness can read it.

        Logged only on a transition, because the consume loop calls this on
        every poll and a per-poll line would bury the change of state in it.
        """
        if self._connection_ok is ok:
            return
        if ok:
            self.logger.info("Redis at %s:%s is reachable", self.host, self.port)
        self._connection_ok = ok

    @staticmethod
    def _coerce_maxlen(value: Any) -> Optional[int]:
        """Return a positive MAXLEN cap, or ``None`` to leave the stream untrimmed.

        Anything non-positive or unparseable means no trimming: the failure mode
        of guessing a cap is deleting a customer's records, and the failure mode
        of not trimming is a stream that grows until they set a policy on it.
        """
        try:
            maxlen = int(value)
        except (TypeError, ValueError):
            return None
        return maxlen if maxlen > 0 else None

    @staticmethod
    def _coerce_idle_ms(value: Any) -> int:
        """Return a non-negative reclaim idle threshold in milliseconds."""
        try:
            idle = int(value)
        except (TypeError, ValueError):
            return DEFAULT_RECLAIM_MIN_IDLE_MS
        return max(idle, 0)

    @staticmethod
    def _coerce_retries(value: Any) -> int:
        """Return a non-negative retry count; 0 disables retrying."""
        try:
            retries = int(value)
        except (TypeError, ValueError):
            return DEFAULT_PUBLISH_RETRIES
        return max(retries, 0)

    @staticmethod
    def _coerce_backoff(value: Any) -> float:
        """Return a non-negative backoff in seconds."""
        try:
            backoff = float(value)
        except (TypeError, ValueError):
            return DEFAULT_PUBLISH_RETRY_BACKOFF
        return backoff if backoff > 0 else 0.0

    def _record_publish_failure(self, outcome: str) -> None:
        """Report a failed publish attempt, if a metrics recorder is present."""
        if _metrics is None:
            return
        try:
            _metrics.inc_redis_publish_failure(outcome)
        except Exception:  # pragma: no cover - metrics must never break publish
            pass

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
                **self.tls,
            )
            self.logger.info(
                "Redis Streams client configured for %s:%s (db=%s, tls=%s, "
                "auth=%s, trim=%s)",
                self.host, self.port, self.db,
                "on" if self.tls else "off",
                "on" if self.password else "off",
                self.maxlen if self.maxlen else "off",
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
            self._mark_connection(True)
            return True
        except Exception as exc:
            self.logger.error("Redis ping failed for %s:%s: %s", self.host, self.port, exc)
            self._mark_connection(False)
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
                self._mark_connection(False)
                return False
            self.logger.debug("Consumer group '%s' already exists on '%s'", group, stream)
        except redis.exceptions.RedisError as exc:
            self.logger.error("Redis unavailable while creating group '%s' on '%s': %s", group, stream, exc)
            self._mark_connection(False)
            self._reset_client()
            return False

        self._mark_connection(True)
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
            self._mark_connection(False)
            self._reset_client()
            return []
        except redis.exceptions.TimeoutError:
            # A blocking read that expires with nothing to hand back is the
            # normal idle case, not a broken connection.
            self.logger.debug("Redis read timed out with no new entries")
            self._mark_connection(True)
            return []
        except redis.exceptions.ResponseError as exc:
            # NOGROUP means the stream or group vanished (e.g. FLUSHDB); drop
            # the cache so the next poll recreates it. The connection itself is
            # fine, so it does not count against health.
            if "NOGROUP" in str(exc):
                self.logger.warning("Consumer group missing on read, will recreate: %s", exc)
                self._ensured_groups.clear()
                self._mark_connection(True)
            else:
                self.logger.error("Redis rejected XREADGROUP on %s: %s", stream_list, exc)
                self._mark_connection(False)
            return []
        except redis.exceptions.RedisError as exc:
            self.logger.error("Redis error while reading streams %s: %s", stream_list, exc)
            self._mark_connection(False)
            return []

        self._mark_connection(True)
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
            self._mark_connection(True)
        except redis.exceptions.ConnectionError as exc:
            self.logger.error("Redis connection lost while acking %s entries on '%s': %s",
                              len(message_ids), stream, exc)
            self._mark_connection(False)
            self._reset_client()
        except redis.exceptions.RedisError as exc:
            self.logger.error("Failed to ack %s entries on '%s': %s", len(message_ids), stream, exc)
            self._mark_connection(False)

    def claim_stale(
        self,
        stream: str,
        group: str,
        consumer: str,
        count: int,
        min_idle_ms: Optional[int] = None,
    ) -> List[Tuple[str, bytes, Dict[Any, Any]]]:
        """Take over entries another consumer read but never acknowledged.

        ``XREADGROUP ... >`` only ever returns entries no one has seen. An entry
        delivered to a consumer that then died stays in that consumer's pending
        list forever: it is neither redelivered nor visible to a replacement,
        so without this pass a replica lost mid-batch strands its work with no
        upper bound. ``XAUTOCLAIM`` is the reclaim, gated on ``min_idle_ms`` so
        a consumer that is merely slow is not raced for entries it still owns.

        Returns:
            The claimed entries in the same ``(stream, message_id, fields)``
            shape as :meth:`read_group`, so the caller decodes them by one path.
            Empty when nothing is stale or the broker is unreachable.
        """
        idle = self.reclaim_min_idle_ms if min_idle_ms is None else max(int(min_idle_ms), 0)
        try:
            response = self.client.xautoclaim(
                name=stream,
                groupname=group,
                consumername=consumer,
                min_idle_time=idle,
                start_id="0-0",
                count=count,
            )
        except redis.exceptions.ConnectionError as exc:
            self.logger.error("Redis connection lost while reclaiming on '%s': %s", stream, exc)
            self._mark_connection(False)
            self._reset_client()
            return []
        except redis.exceptions.ResponseError as exc:
            # NOGROUP here means the same thing it means on read. Older servers
            # (< 6.2) have no XAUTOCLAIM at all and answer "unknown command";
            # that is a deployment fact, not a fault, so it must not be able to
            # flap the health signal on every poll.
            message = str(exc)
            if "NOGROUP" in message:
                self._ensured_groups.clear()
            else:
                self.logger.warning(
                    "Redis rejected XAUTOCLAIM on '%s'; stale entries will not be "
                    "reclaimed: %s", stream, exc,
                )
            self._mark_connection(True)
            return []
        except redis.exceptions.RedisError as exc:
            self.logger.error("Redis error while reclaiming on '%s': %s", stream, exc)
            self._mark_connection(False)
            return []

        self._mark_connection(True)
        # Redis 6.2 answers (cursor, entries); 7.0 added a third element listing
        # ids that no longer exist. Index rather than unpack so one server
        # version is not a TypeError on the consume path.
        messages = response[1] if isinstance(response, (list, tuple)) and len(response) > 1 else []
        entries: List[Tuple[str, bytes, Dict[Any, Any]]] = []
        for message_id, fields in messages or []:
            # XAUTOCLAIM reports tombstoned ids as (id, None) on some versions;
            # they carry nothing to process but must still be acked, which the
            # caller does from the id.
            entries.append((stream, message_id, fields or {}))
        if entries:
            self.logger.warning(
                "Reclaimed %d entry(ies) on '%s' left pending for more than %dms "
                "by another consumer in group '%s'",
                len(entries), stream, idle, group,
            )
        return entries

    def add(
        self,
        stream: str,
        payload: bytes,
        key: Any = b"",
        headers: Optional[Dict[str, Any]] = None,
    ) -> Optional[bytes]:
        """Publish ``payload`` to ``stream`` using the MDX envelope.

        A failed XADD is retried up to ``publish_retries`` times with a linear
        backoff, because a redisStream sink is the only destination for the
        payload: unlike the read path, there is nothing upstream that will hand
        it to us again. Retries are deliberately few — the caller runs on the
        consume path and the source has already acked.

        Trimming is applied only when ``maxlen`` is configured; see
        :data:`DEFAULT_MAXLEN` for why it is off by default.

        Returns:
            The generated entry ID, or ``None`` if every attempt failed. A
            ``None`` return means the payload was dropped and is counted under
            ``alert_bridge_redis_publish_failures_total{outcome="dropped"}``.
            Callers that have no second destination must treat it as an error
            rather than a completed write.
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

        attempts = self.publish_retries + 1
        for attempt in range(1, attempts + 1):
            try:
                entry_id = self.client.xadd(stream, fields, **kwargs)
            except redis.exceptions.RedisError as exc:
                # ConnectionError is a RedisError subclass; only a broken
                # connection warrants rebuilding the client before retrying.
                if isinstance(exc, redis.exceptions.ConnectionError):
                    self._reset_client()
                self._mark_connection(False)
                if attempt < attempts:
                    self.logger.warning(
                        "Redis write to '%s' failed (attempt %d/%d), retrying: %s",
                        stream, attempt, attempts, exc,
                    )
                    if self.publish_retry_backoff:
                        time.sleep(self.publish_retry_backoff * attempt)
                    continue
                self._record_publish_failure("dropped")
                self.logger.error(
                    "Dropping payload after %d failed Redis writes to '%s': %s",
                    attempts, stream, exc,
                )
                return None

            self._mark_connection(True)
            if attempt > 1:
                self._record_publish_failure("recovered")
                self.logger.warning(
                    "Redis write to '%s' succeeded on attempt %d/%d", stream, attempt, attempts
                )
            return entry_id

        return None  # pragma: no cover - loop always returns

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
