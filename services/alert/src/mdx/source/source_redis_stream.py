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
    coerce_tuning,
    require_redis_endpoint,
    resolve_redis_config,
)
from mdx.redis_stream_envelope import extract_envelope, message_id_to_epoch_ms
from mdx.source.source_base import SourceBase
# Shared partition-key guardrail: it reports whether the envelope key matches
# the payload sensorId, which dedup cohort affinity depends on regardless of
# transport.
from mdx.source.source_utils import record_key_alignment, record_source_drop
from mdx.stream_message import StreamMessage
from mdx.stream_routing import (
    HEARTBEAT_KIND, SUPPORTED_KINDS, canonical_kind, require_distinct_streams,
    require_kind_coverage, require_stream_map, require_stream_name,
)
# The field the pipeline reads an event's kind back out of, which is what makes a
# payload declaring one kind on another kind's stream a contradiction rather than
# a detail.
from utils.event_utils import EVENT_KIND_FIELD

DEFAULT_BLOCK_MS = 100
DEFAULT_COUNT = 10
DEFAULT_ERROR_BACKOFF_SECONDS = 1.0
#: Floor under ``error_backoff``. This backoff is the only thing pacing the
#: consume loop while Redis is refusing commands -- out of memory, or an ACL
#: without ``XREADGROUP`` -- because a refused read returns instantly. At zero
#: it paces nothing, which is the hot loop it exists to prevent, so a zero (or a
#: negative, which ``time.sleep`` raises on) is floored rather than honoured.
MIN_ERROR_BACKOFF_SECONDS = 0.05
#: New consumer groups start at ``$`` (new entries only) to match the Kafka
#: source's ``auto_offset_reset: latest``.
DEFAULT_START_ID = "$"
#: ``transport`` label on the read-path drop counter.
SOURCE_TRANSPORT = "redis_stream"
#: How often to sweep for entries stranded in a dead consumer's pending list,
#: and to clear out the records those consumers left behind. A floor, not a
#: schedule: it runs on the poll after the interval has elapsed.
DEFAULT_RECLAIM_INTERVAL_SECONDS = 30.0
#: How long a consumer record must have been idle, with nothing pending, before
#: the sweep removes it.
#:
#: A record is created by being named in an ``XREADGROUP`` and removed by nothing
#: — there is no session for the server to expire, which is the difference from a
#: Kafka group. This consumer's name carries its PID, so every restart and every
#: forked pipeline child leaves one more record in the group, each of which
#: ``XAUTOCLAIM`` and ``XINFO`` then walk. Nothing here is urgent, so the
#: threshold is set where it cannot be confused for a live consumer: one polls
#: every ``block_time`` milliseconds, so an hour of silence is four orders of
#: magnitude past ordinary.
DEFAULT_CONSUMER_TTL_MS = 3_600_000
#: Wall-clock ceiling on the consumer-record release in :meth:`close`.
#:
#: Shutdown housekeeping, not shutdown work: everything it does, the idle sweep
#: does later. So it gets whatever is left of a few seconds and no more, well
#: inside the smallest grace period any of the deployment profiles allow.
RELEASE_BUDGET_SECONDS = 5.0

#: Re-exported: the kind vocabulary and the map rules live in
#: :mod:`mdx.stream_routing` because the terminal sink and configuration
#: validation apply the same ones, and they disagreed while each had its own copy.


class _UnreadablePayload(Exception):
    """The JSON parser did not finish, for a reason that is not "not JSON".

    Private, and deliberately not raised out of the source: it exists to carry
    one entry's outcome from the decoder to the ledger, where the entry is
    rejected and acked like any other undecodable one. Nothing outside this
    module needs to know the difference, and the read loop must not.
    """


class _EntryLedger:
    """Which entries have been decided, and what deciding against one means.

    Three read paths each accumulated their own ack batches and each spelled out
    what to do with an entry they could not use, five times between them. The
    duplication was not the worst of it: what to do with a rejected entry is a
    single delivery-semantics decision, and having it restated at every drop site
    meant it could not be changed, reviewed or even stated in one place.

    So it is stated here. A rejected entry is **acknowledged**, which drops it.
    The alternative -- leaving it pending for the reclaim sweep -- costs more
    than it buys for the things actually rejected here: an entry with no payload,
    on an unmapped stream, or failing schema validation fails the same way on
    every redelivery, so leaving it pending means an entry that can never be
    decoded is retried for the life of the deployment while the consumer's
    pending list grows behind it. Changing that decision is one line, in one
    place, which is the point.

    Acking only on a decision, rather than on arrival, is the other half. An
    entry acked before it has been examined is unrecoverable -- out of the
    pending list, never offered by XREADGROUP again -- so every path out of a
    read loop has to say which it was.
    """

    def __init__(self, logger: logging.Logger) -> None:
        self._logger = logger
        self._acks: Dict[str, List[Any]] = {}

    def accept(self, stream: str, message_id: Any) -> None:
        """Record an entry as used."""
        self._acks.setdefault(stream, []).append(message_id)

    def reject(self, stream: str, message_id: Any, reason: str,
               detail: str) -> None:
        """Record an entry as unusable, and apply the policy above.

        ``reason`` is the metric label -- a closed set, see
        ``SOURCE_DROP_REASONS`` -- and ``detail`` is the human half. Both are
        required: the count is what makes a misconfigured producer visible on a
        dashboard, and the line is what says which entry and why.
        """
        self._logger.warning(
            "Dropping Redis entry %r on '%s': %s", message_id, stream, detail,
        )
        record_source_drop(SOURCE_TRANSPORT, reason)
        self.accept(stream, message_id)

    @property
    def decided(self) -> Dict[str, List[Any]]:
        """Ack batches per stream, for :meth:`SourceRedisStream._ack`."""
        return self._acks


class SourceRedisStream(SourceBase):
    """Redis Streams source implementation."""

    #: The stream an entry was read on decides whether it is an alert or an
    #: incident, so the pipeline stamps that answer onto the payload rather than
    #: letting terminal routing re-derive it from fields the producer controls.
    kind_is_authoritative = True

    #: Nothing here reports a state change. A broker that goes away after
    #: startup just produces empty reads, so readiness has to be re-evaluated on
    #: a timer or it would stand at whatever was published at boot.
    needs_readiness_polling = True

    def __init__(self, config: dict):
        super().__init__(config)
        self.logger = logging.getLogger(self.__class__.__name__)

        section = (config.get('event_bridge') or {}).get('redis_source') or {}
        if not section:
            raise ValueError(
                "event_bridge.redis_source must be configured when sourceType is 'redisStream'"
            )

        # Checked here as well as in the factory's validate_configuration, so
        # the guard belongs to the component rather than to one route into it.
        # A source built directly -- by a test, or by any caller that does not
        # go through validation first -- otherwise fell back to localhost and
        # then polled it forever, because an unreachable broker is tolerated by
        # design and nothing would ever say why no events arrived.
        merged = resolve_redis_config(config, 'redis_source')
        require_redis_endpoint(merged, "event_bridge.sourceType")
        self.broker = RedisStreamBroker(merged)

        self.heartbeat_stream: Optional[str] = None
        self.source_streams: List[str] = []
        self.stream_to_kind: Dict[str, str] = {}
        self._parse_streams(section.get('streams'))

        self.consumer_group = section.get('consumer_group')
        if not self.consumer_group:
            raise ValueError("event_bridge.redis_source.consumer_group must be configured")

        # Unique per replica so scaled-out deployments share the group without
        # stealing each other's pending entries.
        self.consumer_name = f"alert-bridge-{socket.gethostname()}-{os.getpid()}"

        # Read through the shared coercer rather than a bare int()/float(). A
        # bare cast raises, and these are read in the constructor -- which runs
        # inside a forked pipeline child, so `count: ""` from an unset variable
        # crash-looped the container over a poll size. Same policy as the
        # broker's own knobs: warn, fall back, start.
        consumer_config = section.get('consumer_config') or {}
        setting = 'event_bridge.redis_source.consumer_config'
        self.count = coerce_tuning(
            consumer_config.get('count', DEFAULT_COUNT),
            DEFAULT_COUNT, f'{setting}.count', minimum=1,
        )
        self.block_ms = coerce_tuning(
            consumer_config.get('block_time', DEFAULT_BLOCK_MS),
            DEFAULT_BLOCK_MS, f'{setting}.block_time', minimum=1,
        )
        self.broker.warn_if_block_exceeds_timeout(
            self.block_ms, f'{setting}.block_time',
        )
        self.start_id = str(consumer_config.get('start_id', DEFAULT_START_ID))
        self._error_backoff = coerce_tuning(
            consumer_config.get('error_backoff', DEFAULT_ERROR_BACKOFF_SECONDS),
            DEFAULT_ERROR_BACKOFF_SECONDS, f'{setting}.error_backoff',
            cast=float, minimum=MIN_ERROR_BACKOFF_SECONDS,
        )
        self._reclaim_interval = coerce_tuning(
            consumer_config.get('reclaim_interval', DEFAULT_RECLAIM_INTERVAL_SECONDS),
            DEFAULT_RECLAIM_INTERVAL_SECONDS, f'{setting}.reclaim_interval',
            cast=float,
        )
        self._consumer_ttl_ms = coerce_tuning(
            consumer_config.get('consumer_ttl_ms', DEFAULT_CONSUMER_TTL_MS),
            DEFAULT_CONSUMER_TTL_MS, f'{setting}.consumer_ttl_ms', cast=float,
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

    def _parse_streams(self, streams: Any) -> None:
        """Record which kind each configured stream carries.

        The map rules -- blank values, one stream per key -- are
        :mod:`mdx.stream_routing`'s, applied by every reader of a routing map.
        What is specific to a source, and stays here, is the other direction:
        turning a config key into the event kind that selects a decode schema,
        including the legacy spellings and the heartbeat stream that is not an
        event kind at all.
        """
        setting = "event_bridge.redis_source.streams"
        streams = require_stream_map(streams, setting, SUPPORTED_KINDS)
        # Names first: a blank value is reported as a blank value, rather than
        # reaching the distinctness check where it is not a stream name at all.
        named = {
            name: require_stream_name(
                stream, f"{setting}['{name}']",
                remedy="Remove the key to not consume that kind, or give it a "
                       "stream name.",
            )
            for name, stream in streams.items()
        }
        require_distinct_streams(named, setting)

        for name, stream_name in named.items():
            kind = name[: -len('_stream')] if name.endswith('_stream') else name

            if kind == HEARTBEAT_KIND:
                self.heartbeat_stream = stream_name
                continue

            canonical = canonical_kind(name)
            if canonical not in SUPPORTED_KINDS:
                raise ValueError(
                    f"event_bridge.redis_source.streams has an unsupported key "
                    f"'{name}'. The key names the event kind and selects the "
                    f"decode schema, so it must be one of "
                    f"{', '.join(SUPPORTED_KINDS)} (or '{HEARTBEAT_KIND}')."
                )
            if kind != canonical:
                self.logger.warning(
                    "event_bridge.redis_source.streams['%s'] uses the legacy kind "
                    "name '%s'; it is read as '%s'. Rename the key — the legacy "
                    "spelling predates the event_bridge configuration layout and "
                    "will not be accepted indefinitely.",
                    name, kind, canonical,
                )

            self.source_streams.append(stream_name)
            self.stream_to_kind[stream_name] = canonical

        # Subsumes the "at least one non-heartbeat stream" check this used to
        # make: a map naming only a heartbeat covers neither kind, and is told
        # which keys are missing rather than that something is.
        require_kind_coverage(self.stream_to_kind.values(), setting)

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
        the consume loop. A read the broker *refuses* returns instantly, though,
        so the backoff sleep is what keeps the loop from becoming a hot one.
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
        if not entries and self.broker.connection_healthy is False:
            # An empty read is normally a blocking read that timed out, which
            # has already paced the loop. A refused one -- OOM, or an ACL
            # without XREADGROUP -- comes back with no wait at all, so the
            # consume loop span an error log per iteration on one pinned core
            # for as long as the condition lasted. The connection flag is what
            # tells the two apart, since both hand back an empty list.
            self.logger.debug(
                "Backing off %.1fs: the last Redis command did not succeed",
                self._error_backoff,
            )
            time.sleep(self._error_backoff)
            return []

        # The sweep is deliberately not conditional on an empty read. It was,
        # on the reasoning that reclaimed entries are not urgent and an
        # XAUTOCLAIM per stream does not belong on the hot path -- but under
        # sustained traffic the read is never empty, so the sweep never ran and
        # entries stranded by a dead replica sat there for as long as the load
        # lasted. The interval below is what keeps it off the hot path; every
        # poll but one in three hundred returns here without a round trip.
        reclaimed = self._reclaim_stale(streams)
        return entries + reclaimed if entries else reclaimed

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
        # Same cadence, and only on the streams this sweep just visited: a
        # consumer record is left behind by every process that ever read here,
        # and nothing else removes them. Done after the claim so an entry is
        # rescued before the record that held it can be considered for removal.
        self._prune_dead_consumers(streams)
        return reclaimed

    def _prune_dead_consumers(self, streams: List[str]) -> int:
        """Remove consumer records left behind by processes that are gone.

        Only records that are **idle past the threshold and hold nothing
        pending** are removed. Both conditions matter: ``XGROUP DELCONSUMER``
        discards a consumer's pending entries rather than releasing them, so
        removing one with work outstanding would lose exactly the entries the
        reclaim sweep above exists to rescue — and it runs first for that
        reason.

        Best-effort by design. This is housekeeping; if the server will not
        answer, the group grows and consuming carries on.

        Returns:
            How many records were removed, which is what the tests assert on.
        """
        removed = 0
        for stream in streams:
            for record in self.broker.list_consumers(stream, self.consumer_group):
                name = record.get("name")
                if not name or name == self.consumer_name:
                    continue
                if record.get("pending") or record.get("idle", 0) < self._consumer_ttl_ms:
                    continue
                if self.broker.delete_consumer(
                    stream, self.consumer_group, name,
                ) is None:
                    continue
                removed += 1
                self.logger.info(
                    "Removed consumer record '%s' from group '%s' on '%s': idle "
                    "for %.0fs with nothing pending",
                    name, self.consumer_group, stream,
                    record.get("idle", 0) / 1000,
                )
        return removed

    def _release_own_consumer(self) -> None:
        """Take this process's consumer record out of the group on the way out.

        Otherwise a restart leaves one behind every time -- the record is keyed
        by name, and the name carries this process's PID, so the next start is a
        new consumer and the old record stays for the sweep to notice an hour
        later. Doing it here is what keeps an ordinary restart from leaving
        anything at all.

        Skipped when this consumer still has entries pending, which after a read
        loop that acks before it returns means an ack that did not land. Those
        are the reclaim sweep's to rescue, and deleting the record would discard
        them instead.

        Skipped entirely when the connection is already known to be down, and
        abandoned at the next stream once it has spent
        :data:`RELEASE_BUDGET_SECONDS`. Both because this runs after SIGTERM,
        inside the deployment's grace period: two commands per stream against an
        unreachable host cost a socket timeout each, and the reward for
        overrunning the grace period is SIGKILL -- which loses the rest of the
        shutdown, to tidy up a record the sweep removes anyway.
        """
        if self.broker.connection_healthy is False:
            self.logger.info(
                "Not releasing consumer record '%s': Redis is already "
                "unreachable, so the idle-consumer sweep will remove it",
                self.consumer_name,
            )
            return
        streams = list(self.source_streams)
        if self.heartbeat_stream:
            streams.append(self.heartbeat_stream)
        deadline = time.monotonic() + RELEASE_BUDGET_SECONDS
        for position, stream in enumerate(streams):
            if time.monotonic() >= deadline:
                self.logger.warning(
                    "Giving up on releasing consumer record '%s' after %.0fs; "
                    "the record(s) left on %s will be removed by the "
                    "idle-consumer sweep",
                    self.consumer_name, RELEASE_BUDGET_SECONDS,
                    streams[position:],
                )
                return
            for record in self.broker.list_consumers(stream, self.consumer_group):
                if record.get("name") != self.consumer_name:
                    continue
                if record.get("pending"):
                    self.logger.warning(
                        "Leaving consumer record '%s' on '%s' in place: %s "
                        "entry(ies) are still pending and removing it would "
                        "discard them rather than let another consumer claim "
                        "them",
                        self.consumer_name, stream, record["pending"],
                    )
                else:
                    self.broker.delete_consumer(
                        stream, self.consumer_group, self.consumer_name,
                    )

    def _iso_or_none(self, epoch_ms: Optional[int]) -> Optional[str]:
        """An ISO-8601 UTC string for ``epoch_ms``, or ``None`` if it is not one.

        The value comes from the millisecond half of a stream ID, which a
        producer may set explicitly, so it is producer-controlled input rather
        than a clock reading. :func:`message_id_to_epoch_ms` bounds it; this is
        the second half of the same argument, because the bound is a judgement
        about plausible dates and this conversion is where an implausible one
        would actually raise -- and it runs *after* the batch has been acked, so
        raising here loses every entry in it to buy nothing. A latency stamp is
        worth exactly one log line.
        """
        if not epoch_ms:
            return None
        try:
            return datetime.fromtimestamp(
                epoch_ms / 1000, tz=timezone.utc,
            ).isoformat()
        except (ValueError, OverflowError, OSError):
            self.logger.warning(
                "Stream entry ID carries %s ms, which is not a representable "
                "date; this batch reports no publish time and its end-to-end "
                "latency is not measured", epoch_ms,
            )
            return None

    def _ack(self, acks: Dict[str, List[Any]]) -> None:
        for stream, message_ids in acks.items():
            self.broker.ack(stream, self.consumer_group, message_ids)

    @staticmethod
    def _decode_json_payload(payload: bytes) -> Optional[Tuple[Optional[str], dict]]:
        """The payload's JSON object and its UTF-8 text, or ``None`` if not JSON.

        Used to tell the two MDX payload encodings apart: protobuf never parses
        as a JSON object, so a successful parse means the producer published
        JSON text.

        The text is returned with the object because both are needed and
        decoding twice was a way to crash. ``json.loads`` sniffs a BOM and
        accepts UTF-16 and UTF-32 as well, while the batch this builds holds
        ``str`` -- so a single ``XADD`` of UTF-16 JSON parsed here, passed
        validation, and then raised ``UnicodeDecodeError`` from a second decode
        further down ``read_data``. That escaped the read loop, so nothing in
        the batch was acked, the consume loop died, and the entry came back to
        the next process through the reclaim sweep: one entry, an endless
        restart.

        Returns:
            ``None`` when the payload is not a JSON object -- protobuf takes
            that path. ``(text, object)`` when it is JSON this can hand on, and
            ``(None, object)`` when it is JSON in an encoding this cannot, which
            the caller reports as its own drop reason rather than guessing at a
            transcoding the producer did not ask for.

        Raises:
            _UnreadablePayload: The parser did not finish. Its own outcome, and
                not folded into the ``None`` above: "not JSON" sends the payload
                to the protobuf decoder, which is a lenient parser that will make
                *something* of nested brackets, so folding them together would
                turn a hostile payload into an event the VLM pays to verify.
        """
        try:
            decoded = json.loads(payload)
        except (ValueError, UnicodeDecodeError):
            return None
        except Exception as exc:
            # The parser's documented failures are above; this is for the ones
            # outside them. ~200 KB of nested brackets raises RecursionError,
            # which is not a ValueError, so it escaped the read loop entirely:
            # the batch went unacked, the consume loop died, and the reclaim
            # sweep handed the same entry to the next process. One XADD anybody
            # with write access can publish, and a permanent restart.
            raise _UnreadablePayload(exc) from exc
        if not isinstance(decoded, dict):
            return None

        try:
            return payload.decode('utf-8'), decoded
        except UnicodeDecodeError:
            return None, decoded

    @staticmethod
    def _schema_violation(event: Dict[str, Any]) -> Optional[str]:
        """Return why ``event`` cannot be processed, or ``None`` if it can.

        This is where the read path stops trusting the producer. Any Redis
        client can XADD to a stream Alert MS consumes, so before this an
        arbitrary JSON object — ``{}`` included — was decoded, wrapped in a
        batch and handed to the VLM, which then paid to verify it.

        The check is deliberately one field. A sensor identity is the one thing
        every shape reaching this source carries and the pipeline cannot work
        without: the dedup cohort key is prefixed with it, it is the partition
        key the Kafka transport relies on and the Redis envelope key mirrors,
        and the VST lookup that fetches the footage is addressed by it. Nothing
        else is safe to require here — a top-level ``id`` in particular is not,
        because the ``Incident`` protobuf schema has no such field and the id
        legitimately travels inside ``info``. Validating more than the pipeline
        actually needs would reject valid events, which is worse than the gap
        this closes.

        Whether the payload belongs on the stream it arrived on is
        :meth:`_kind_conflict`'s question, kept separate because it is answerable
        for far fewer payloads than this one and reported under its own reason.
        """
        if not event:
            return "empty object"
        sensor = event.get("sensor")
        sensor_id = event.get("sensorId") or (
            sensor.get("id") if isinstance(sensor, dict) else None
        )
        if not sensor_id:
            return "no 'sensorId' and no 'sensor.id'"
        return None

    @staticmethod
    def _kind_conflict(event: Dict[str, Any], kind: str) -> Optional[str]:
        """Return why ``event`` contradicts ``kind``, or ``None`` if it does not.

        The stream decides an entry's kind, and
        :func:`utils.event_utils.stamp_event_kind` writes that answer over
        ``notification_type`` — so a payload that arrived on the
        wrong stream is not mis-decoded, it is *relabelled*, verified as the kind
        the stream says, and published to that kind's destination. Nothing
        raises, and the verdict looks ordinary wherever it lands.

        What is checked is only a **contradiction**: the payload named a kind,
        and it is not this stream's. Absence is not a contradiction, which is the
        whole reason this can be enforced at all — an alert whose producer omits
        ``notification_type`` is the common case, not an error, and demanding the
        field would reject most of the traffic to catch the little of it that is
        misdirected. Nor is an unrecognized value: it is not a claim about kind
        that this can adjudicate, so it passes and the stream still decides.

        Only JSON payloads reach here. A protobuf payload carries its kind in the
        schema it was serialized with, which cannot be read without choosing a
        schema first — the very thing the stream is consulted for.
        """
        declared = event.get(EVENT_KIND_FIELD)
        if not isinstance(declared, str):
            return None

        declared = declared.strip().lower()
        if declared not in SUPPORTED_KINDS or declared == kind:
            return None

        return (
            f"payload declares {EVENT_KIND_FIELD}='{declared}' but arrived on a "
            f"'{kind}' stream. The stream decides the kind, so processing it "
            f"would verify a {declared} as a {kind} and publish it as one."
        )

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
        ledger = _EntryLedger(self.logger)
        earliest_published_ms: Optional[int] = None

        for stream, message_id, fields in entries:
            payload, key, _ = extract_envelope(fields)
            if not payload:
                # An empty payload field counts the same as a missing one --
                # there is nothing to decode either way.
                ledger.reject(stream, message_id, "no_payload",
                              "no usable payload field")
                continue

            kind = self.stream_to_kind.get(stream)
            if kind not in SUPPORTED_KINDS:
                # Only reachable for a reclaimed entry from a stream that has
                # since been removed from the configuration. Decoding it would
                # mean guessing a schema, so drop it rather than route it wrong.
                ledger.reject(stream, message_id, "unmapped_kind",
                              "stream is not mapped to a supported event kind")
                continue

            try:
                as_json = self._decode_json_payload(payload)
            except _UnreadablePayload as exc:
                # Counted as undecodable, which is what it is: the decoders could
                # not read it. Dropped rather than tried as protobuf, because a
                # payload the JSON parser choked on is textual JSON, and the
                # protobuf decoder is lenient enough to make an event out of it.
                ledger.reject(stream, message_id, "undecodable",
                              f"the JSON parser did not finish it: {exc}")
                continue

            if as_json is not None:
                text, decoded = as_json
                if text is None:
                    # JSON, but not in an encoding the batch can carry. Rejected
                    # rather than transcoded: the pipeline compares payload text
                    # downstream, and a re-encoded copy is no longer the bytes
                    # the producer published.
                    ledger.reject(
                        stream, message_id, "payload_encoding",
                        "JSON payload is not UTF-8 (a BOM says UTF-16 or "
                        "UTF-32); publish it as UTF-8",
                    )
                    continue

                violation = self._schema_violation(decoded)
                if violation:
                    # Parsed, but not an event this pipeline can process.
                    # Rejecting here is what keeps an arbitrary XADD -- or a
                    # `metadata` sidecar decoded as a body -- from reaching
                    # the VLM.
                    ledger.reject(stream, message_id, "schema_invalid", violation)
                    continue

                conflict = self._kind_conflict(decoded, kind)
                if conflict:
                    # Its own reason: "a producer is publishing to the wrong
                    # stream" is a different operator action from "a producer is
                    # publishing malformed events", and on a dashboard the two
                    # are only distinguishable if they are counted apart.
                    ledger.reject(stream, message_id, "kind_mismatch", conflict)
                    continue

            published_ms = message_id_to_epoch_ms(message_id)
            if published_ms and (earliest_published_ms is None or published_ms < earliest_published_ms):
                earliest_published_ms = published_ms

            record_key_alignment(key, payload)

            if as_json is not None:
                grouped.setdefault((kind, 'json'), []).append(text)
            else:
                grouped.setdefault((kind, 'protobuf'), []).append((key, payload, published_ms))
            ledger.accept(stream, message_id)

        # Acked once every entry has been decided, matching the Kafka source's
        # commit-on-consume (at-most-once) semantics.
        self._ack(ledger.decided)

        consumed_at = datetime.now(timezone.utc).isoformat()
        published_at = self._iso_or_none(earliest_published_ms)

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
        ledger = _EntryLedger(self.logger)
        for stream, message_id, fields in entries:
            payload, _key, _headers = extract_envelope(fields)
            if payload is None:
                ledger.reject(stream, message_id, "no_payload",
                              "no usable payload field")
                continue
            payloads.append(payload)
            ledger.accept(stream, message_id)
        self._ack(ledger.decided)
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
        ledger = _EntryLedger(self.logger)
        for stream, message_id, fields in entries:
            try:
                messages.append(
                    StreamMessage.from_redis_stream(stream, message_id, fields, 'request_schema.yaml')
                )
            except Exception as exc:
                ledger.reject(stream, message_id, "undecodable", str(exc))
                continue
            ledger.accept(stream, message_id)
        self._ack(ledger.decided)
        return messages

    def close(self) -> None:
        """Release this consumer's record, then the Redis connection."""
        try:
            self._release_own_consumer()
        except Exception:  # pragma: no cover - shutdown must not raise
            self.logger.debug(
                "Could not release consumer record '%s'", self.consumer_name,
                exc_info=True,
            )
        self.broker.close()
