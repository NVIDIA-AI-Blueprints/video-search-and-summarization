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

"""
Main-process listener consuming dynamic-config messages from
``mdx-notification`` and persisting them as JSON files in :data:`CONFIG_DIR`
for the per-worker :class:`ConfigFileMonitor` watchdogs to pick up.

Pipeline (per inbound ``upsert``)
---------------------------------

1. Monotonic timestamp check -- skip messages older than what we've already
   persisted (defense against re-consume after a Kafka offset reset).
2. :func:`validate` (from :mod:`config_validator`) -- shape, scope, and per-item
   validation. ``failure`` (which now also covers "nothing applicable") short-
   circuits to an ``ack`` with no write and no apply.
3. Atomic file write -- the **filtered applied subset** is staged into
   ``.<name>.tmp`` and ``os.rename``\\ d into ``CONFIG_DIR``. Workers' watchdog
   sees the move and applies. Write failures ack ``failure`` with an
   ``IOError`` message; main has not yet mutated.
4. :class:`ConfigApplier.apply` -- merge the same applied subset into main's
   live ``AppConfig``. Setters are infallible at this point because validation
   already vetted the items.
5. ``ack`` with the validation status (``success`` / ``partial-success``) plus
   :meth:`AppConfig.to_mutable_snapshot` so the video analytics api sees main's
   resulting view.

Bootstrap flow (Flow B ``upsert-all``)
--------------------------------------

The bootstrap reply from the video analytics api carries the **full** config
(including read-only sections like ``kafka``). We extract just ``app`` +
``sensors`` before validating and treat any ``failure`` status as a no-op
(log + skip). ``partial-success`` is accepted -- the valid subset is persisted
and applied. **No ack is emitted** for Flow B; the bootstrap is silent on the
wire.

Bootstrap is **additive**: items present in main's existing config that the
bootstrap reply does not mention are preserved. Removing items via bootstrap
is intentionally not supported (a future ``delete`` event would handle that).

Bootstrap failure-mode contract
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

There are three ways bootstrap can fail to apply the latest config, and
they're handled differently because their pod-to-pod consistency
implications differ:

* **Network/timeout failure** (video analytics api unreachable, no reply
  within ``bootstrap_timeout``) -- log a warning and continue with the
  disk baseline. Every replica hits the same failure consistently, so
  the deployment stays internally consistent.
* **Validator rejection** (web-api returned a payload our validator
  refuses) -- log error and continue with the disk baseline. Same
  payload reaches every replica, so the rejection is consistent.
* **Disk write failure on this replica** (``OSError`` from
  ``os.rename`` -- disk full, permission lost, mount gone) -- this is
  *pod-specific*. Other replicas with healthy disks have successfully
  applied the latest config; continuing on the disk baseline here would
  leave this replica uniquely behind the rest of the deployment.
  :meth:`_handle_upsert_all` captures the exception in
  ``bootstrap_disk_failure`` and :meth:`start` raises a fatal
  ``RuntimeError`` to abort startup. The orchestrator (k8s, docker
  ``--restart``) is expected to retry, and either succeeds on a
  transient I/O hiccup or surfaces a real disk problem via the pod's
  restart count.

Atomic writes
-------------

Each file is staged into ``.<name>.tmp`` and ``os.rename``\\ d into place.
Workers' :class:`ConfigFileMonitor` uses ``on_moved`` for the production
path and filters dotfiles + non-``.json`` paths so the staging file is
never observable.

Filename convention
-------------------

``<event_type>-config-<iso_timestamp>.json`` -- e.g.
``upsert-config-2026-04-29T10_30_15.123456Z.json``. The action prefix is
informational (kept for audit/debugging); workers no longer dispatch on it
because both flows funnel into the same :meth:`ConfigApplier.apply` path.
Body is the **filtered applied subset** (``{"app": [...], "sensors": [...]}``).
"""

import json
import logging
import os
import tempfile
import threading
import time
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from mdx.analytics.core.constants import (
    CONFIG_DIR,
    CONFIG_PRUNE_INTERVAL_SECONDS,
    CONFIG_RETAIN_SECONDS,
    NOTIFICATION_EPOCH_FLOOR,
)
from mdx.analytics.core.schema.config import AppConfig
from mdx.analytics.core.schema.models import ConfigMessage, StreamMessage
from mdx.analytics.core.stream.source.source_base import BytesStrDeserializer
from mdx.analytics.core.transform.config.config_applier import ApplyResult, ConfigApplier
from mdx.analytics.core.transform.config.config_publisher import (
    ConfigPublisher,
    EVENT_TYPE_ACK,
    EVENT_TYPE_REQUEST_CONFIG,
    NOTIFICATION_MESSAGE_KEY,
)
from mdx.analytics.core.transform.config.config_validator import (
    validate,
    validate_envelope,
)
from mdx.analytics.core.utils.util import convert_datetime_to_iso_8601_with_z_suffix

logger = logging.getLogger(__name__)


# Event types that travel as ``event.type`` headers on inbound notifications.
EVENT_TYPE_UPSERT: str = "upsert"
EVENT_TYPE_UPSERT_ALL: str = "upsert-all"
_KNOWN_EVENT_TYPES: frozenset[str] = frozenset(
    {EVENT_TYPE_UPSERT, EVENT_TYPE_UPSERT_ALL, EVENT_TYPE_ACK, EVENT_TYPE_REQUEST_CONFIG}
)

# Reference-id prefix conventions used by the deserializer's per-action
# acceptance filter (applied only for ``upsert-all`` / ``upsert``).
_VIDEO_ANALYTICS_API_REF_PREFIX: str = "video-analytics-api-"
_BEHAVIOR_ANALYTICS_REF_PREFIX: str = "behavior-analytics-"

# Maps the active sourceType (returned by ``AppConfig.get_app_config("sourceType")``)
# to the literal ``reference-id`` we accept from a direct-publisher on that
# layer. The wire reference-id is short and shared across messages; the
# deserializer makes it unique by appending the broker's record timestamp
# (epoch ms). Used only for ``upsert`` -- ``upsert-all`` does not have a
# direct-publisher path.
_SOURCE_TYPE_TO_REFERENCE_ID: dict[str, str] = {
    "kafka": "kafka",
    "redisStream": "redis",
    "mqtt": "mqtt",
}

# Default time the bootstrap publish blocks waiting for an ``upsert-all``
# reply from the video analytics api. After this, the deployment continues
# with whatever was loaded from disk at process start; the video analytics
# api may still arrive late and will be applied through the normal upsert
# path.
DEFAULT_BOOTSTRAP_TIMEOUT_SEC: float = 15.0

# Upper bound on how long :meth:`ConfigListener.start` waits for the
# consumer thread's first ``source.poll`` to return before publishing
# ``request-config`` anyway. Cold Kafka clusters typically finish partition
# assignment in 1-3s; 10s leaves comfortable headroom without deadlocking
# startup if the broker is unreachable.
READY_TIMEOUT: float = 10.0



def deserialize_config_message(
    msg: StreamMessage,
    source_type: str | None = None,
) -> dict[str, Any] | None:
    """
    Convert a raw :class:`StreamMessage` into a wire-envelope ``dict``.

    Returns a plain dict shaped like the :class:`ConfigMessage` fields
    (``event_type``, ``reference_id``, ``timestamp``, ``config``,
    ``status``, ``error``) -- the pydantic construction happens later in
    :func:`validate_envelope` so a shape violation can be turned into a
    structured ``failure`` ack instead of a silent drop.

    Filters by Kafka ``key`` so calibration messages on the same topic are
    not handled here. Returns ``None`` for messages this listener does not
    handle.

    Wire format (must match :class:`ConfigPublisher`):

    * ``key`` -- ``b"behavior-analytics-config"``.
    * ``headers["event.type"]`` -- one of the four known types.
    * ``headers["reference-id"]`` -- correlation id.
    * ``value`` -- JSON ``{"status": ..., "config": ..., "error": ...}``.

    Per-action reference-id acceptance filter (applied only to ``upsert-all``
    and ``upsert``; ``ack`` / ``request-config`` skip it):

    * ``upsert-all`` -- ``reference-id`` must start with
      ``"video-analytics-api-"`` (web-api originated) or
      ``"behavior-analytics-"`` (this app's own bootstrap reply).
    * ``upsert`` -- accept ``"video-analytics-api-"`` prefix verbatim;
      OR if ``source_type`` is provided and ``reference-id`` matches the
      configured source-type literal (``"kafka"`` / ``"redis"`` /
      ``"mqtt"``, mapping ``redisStream`` -> ``redis``), accept and
      rewrite ``reference-id`` to ``f"{ref}-{msg.timestamp}"`` (epoch ms)
      so downstream dispatch sees a unique value per record.

    Anything else for these two event types is logged at WARNING and the
    message is dropped.

    :param StreamMessage msg: Raw message from the source's ``poll``.
    :param str | None source_type: Active source type from
        ``AppConfig.get_app_config("sourceType")``. Required only for
        direct-publisher ``upsert`` messages; pass ``None`` (default) when
        you only need to accept web-api / bootstrap traffic.
    :return dict | None: Decoded envelope dict ready for
        :func:`validate_envelope`, or ``None`` if the message is not for
        this listener (wrong key, missing headers, bad JSON, unknown
        event.type, or unrecognized reference-id for an upsert event).
    """
    if msg.key is None:
        return None
    try:
        key_str = BytesStrDeserializer(msg.key_bytes)
    except Exception:
        return None
    if key_str != NOTIFICATION_MESSAGE_KEY:
        return None

    raw_headers = msg.headers or {}
    event_type_raw = raw_headers.get("event.type")
    reference_id_raw = raw_headers.get("reference-id")
    if event_type_raw is None or reference_id_raw is None:
        logger.warning(
            f"dropping config message: missing event.type or reference-id header "
            f"(headers={list(raw_headers.keys())})"
        )
        return None
    try:
        event_type = BytesStrDeserializer(event_type_raw)
        reference_id = BytesStrDeserializer(reference_id_raw)
    except Exception as e:
        logger.warning(f"dropping config message: header decode failed: {e}")
        return None

    if event_type not in _KNOWN_EVENT_TYPES:
        logger.warning(f"dropping config message: unknown event.type={event_type!r}")
        return None

    # Per-action reference-id acceptance filter. Only ``upsert-all`` and
    # ``upsert`` are filtered here; ``ack`` and ``request-config`` skip
    # this block (they're filtered later at dispatch via debug-log).
    if event_type == EVENT_TYPE_UPSERT_ALL:
        if not (
            reference_id.startswith(_VIDEO_ANALYTICS_API_REF_PREFIX)
            or reference_id.startswith(_BEHAVIOR_ANALYTICS_REF_PREFIX)
        ):
            logger.warning(
                f"dropping config message: unrecognized reference-id "
                f"{reference_id!r} for event.type={event_type} (expected "
                f"{_VIDEO_ANALYTICS_API_REF_PREFIX!r} or "
                f"{_BEHAVIOR_ANALYTICS_REF_PREFIX!r} prefix)"
            )
            return None
    elif event_type == EVENT_TYPE_UPSERT:
        if reference_id.startswith(_VIDEO_ANALYTICS_API_REF_PREFIX):
            # Web-api originated -- accept the reference-id as-is.
            pass
        elif (
            source_type is not None
            and reference_id == _SOURCE_TYPE_TO_REFERENCE_ID.get(source_type)
        ):
            # Direct publisher on the active source layer. Make the
            # reference-id unique downstream by appending the broker
            # record timestamp (epoch ms).
            reference_id = f"{reference_id}-{int(msg.timestamp)}"
        else:
            logger.warning(
                f"dropping config message: unrecognized reference-id "
                f"{reference_id!r} for event.type={event_type} "
                f"(sourceType={source_type!r})"
            )
            return None

    if msg.value is None:
        logger.warning(
            f"dropping config message: empty value (event.type={event_type}, ref={reference_id})"
        )
        return None
    try:
        body = json.loads(BytesStrDeserializer(msg.value_bytes))
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        logger.warning(
            f"dropping config message: invalid JSON body (event.type={event_type}, ref={reference_id}): {e}"
        )
        return None
    if not isinstance(body, dict):
        logger.warning(
            f"dropping config message: body is not a JSON object (event.type={event_type}, ref={reference_id})"
        )
        return None

    # Kafka record timestamp is ms-epoch (int). -1 means "not set" -- fall
    # back to wall clock to keep filenames unique even in that pathological case.
    # An out-of-range value (broker bug / corruption) raises and we drop the
    # message; mirrors :func:`deserialize_calibration_message` which guards
    # its ISO-8601 header parse the same way.
    ts_ms = msg.timestamp if msg.timestamp >= 0 else int(time.time() * 1000)
    try:
        ts = datetime.fromtimestamp(ts_ms / 1000.0, tz=timezone.utc)
    except (OverflowError, ValueError, OSError) as e:
        logger.warning(
            f"dropping config message: malformed timestamp {ts_ms!r} "
            f"(event.type={event_type}, ref={reference_id}): {e}"
        )
        return None

    # Use the parsed JSON body as the base of the envelope and overlay the
    # header-derived fields. This preserves any extra top-level keys the
    # producer happened to include; :func:`validate_envelope` is the single
    # gate that catches unrecognized keys and turns them into a structured
    # ``failure`` ack (instead of silently swallowing them here).
    message_dict: dict[str, Any] = dict(body)
    message_dict["event_type"] = event_type
    message_dict["reference_id"] = reference_id
    message_dict["timestamp"] = ts
    return message_dict


class ConfigListener:
    """
    Main-process listener for dynamic-config messages.

    Each deployment has exactly one :class:`ConfigListener` (constructed in
    :class:`AppRunner`). The listener consumes notifications, validates +
    persists them via the file pipeline, and emits an ``ack`` (Flow A only).

    :ivar AppConfig config: Main's live config. Used for the post-apply
        snapshot in the ack payload and as the mutation target for
        ``applier``.
    :ivar ConfigApplier applier: Mutates ``config`` with already-validated
        items. No validation responsibility -- the listener runs the validator
        first.
    :ivar ConfigPublisher publisher: Emits ``request-config`` and ``ack``.
    :ivar Callable notification_consumer_fn: Zero-arg callable returning
        ``list[dict | None]`` -- wired to ``source.poll`` with
        :func:`deserialize_config_message` as the message deserializer.
        Each non-``None`` element is a wire-envelope dict that
        :func:`validate_envelope` turns into a :class:`ConfigMessage`.
    :ivar str bootstrap_ref_id: Stable ``"behavior-analytics-<uuid>"`` for
        Flow B targeting; the video analytics api echoes it on the
        ``upsert-all`` reply.
    :ivar Path config_dir: :data:`CONFIG_DIR`, created on construction.
    :ivar datetime last_insert_timestamp: Monotonic write-side high-water
        mark (cheap defense against re-consume after Kafka offset reset).
    :ivar threading.Event consumer_ready_event: Set by
        :meth:`_consume_loop` after its first ``source.poll`` returns.
        :meth:`start` waits on this before publishing ``request-config`` so
        the bootstrap reply can't race partition assignment.
    """

    def __init__(
        self,
        config: AppConfig,
        applier: ConfigApplier,
        publisher: ConfigPublisher,
        notification_consumer_fn: Callable[[], list[dict | None]],
        bootstrap_ref_id: str,
        bootstrap_timeout: float = DEFAULT_BOOTSTRAP_TIMEOUT_SEC,
    ) -> None:
        """
        Initialize the listener (does not start threads; call :meth:`start`).

        :param AppConfig config: Main's live config; same instance the applier
            mutates. Used to take the post-apply snapshot for the ack.
        :param ConfigApplier applier: Mutator bound to ``config``.
        :param ConfigPublisher publisher: Emits ``request-config`` at startup
            and ``ack`` after each ``upsert``.
        :param Callable notification_consumer_fn: Wired by ``app_runner`` to
            ``source.poll(src_key="notification", msg_deserializer=deserialize_config_message,
            group_id_suffix=<replica-tag>)``.
        :param str bootstrap_ref_id: Pre-generated stable identifier for this
            main process. Format ``"behavior-analytics-<uuid>"``.
        :param float bootstrap_timeout: How long :meth:`start` blocks waiting
            for the bootstrap reply.
        :return: None
        """
        self.config = config
        self.applier = applier
        self.publisher = publisher
        self.notification_consumer_fn = notification_consumer_fn
        self.bootstrap_ref_id = bootstrap_ref_id
        self.bootstrap_timeout = bootstrap_timeout
        self.bootstrap_done_event = threading.Event()
        # Set by ``_consume_loop`` after the first ``source.poll`` returns.
        # The first poll blocks internally during partition assignment (Kafka
        # source: ``_get_consumer`` -> ``_wait_for_assignment``); when it
        # returns, the consumer is subscribed, assigned, and seeked to the
        # latest offset. ``start()`` waits on this before publishing
        # ``request-config`` so the bootstrap reply can't race assignment.
        self.consumer_ready_event = threading.Event()
        # Captured by :meth:`_handle_upsert_all` if the file write for a
        # successful bootstrap reply fails (disk full, permission lost,
        # mount gone, etc.). :meth:`start` checks this flag after
        # ``bootstrap_done_event`` fires and raises to abort startup --
        # continuing on the disk baseline while peer replicas successfully
        # applied the latest config would put this replica uniquely behind
        # the rest of the deployment.
        self.bootstrap_disk_failure: OSError | None = None
        self.config_dir = Path(CONFIG_DIR)
        self.config_dir.mkdir(parents=True, exist_ok=True)
        # Initialize at the Unix epoch so the very first notification is accepted.
        self.last_insert_timestamp = NOTIFICATION_EPOCH_FLOOR
        self.running = False
        self.consumer_thread: threading.Thread | None = None
        self.pruner_thread: threading.Thread | None = None


    def start(self) -> None:
        """
        Start the consumer + pruner threads, wait for the consumer to be
        fully ready, then publish ``request-config`` and block up to
        ``bootstrap_timeout`` for the ``upsert-all`` reply.

        Order matters: the Kafka consumer subscribes lazily on the first
        ``source.poll`` call inside :meth:`_consume_loop`, and that first
        call internally blocks until partitions are assigned and seeked to
        the latest offset (see ``source_kafka._wait_for_assignment``). If we
        published ``request-config`` before assignment finished, the reply
        could land at an offset older than the consumer's eventual seek
        position and be silently missed -- the bootstrap would then time
        out and the deployment would continue on the disk baseline.

        Rather than guess assignment latency with a sleep, we wait on
        :attr:`consumer_ready_event`, which :meth:`_consume_loop` sets after
        its first ``source.poll`` returns. At that point the consumer is
        fully ready and any subsequent publish lands at an offset the
        consumer will read. ``READY_TIMEOUT`` is a safety net so a broken
        broker can't deadlock startup; if it fires we publish anyway and
        rely on the existing ``bootstrap_timeout`` to surface the failure.

        :return: None
        """
        if self.running:
            logger.debug("ConfigListener.start() called but already running; no-op")
            return
        self.running = True

        self.consumer_thread = threading.Thread(
            target=self._consume_loop, name="ConfigListener.consume", daemon=True,
        )
        self.pruner_thread = threading.Thread(
            target=self._prune_loop, name="ConfigListener.prune", daemon=True,
        )
        self.consumer_thread.start()
        self.pruner_thread.start()

        # Block until the consumer's first source.poll() has returned --
        # that's when partition assignment has completed and the consumer
        # has seeked to latest. 10s is well above the typical 1-3s cold
        # cluster assignment window; a longer wait would just mask broker
        # issues that bootstrap_timeout would already catch.
        if not self.consumer_ready_event.wait(timeout=READY_TIMEOUT):
            logger.warning(
                f"consumer not ready after {READY_TIMEOUT}s; publishing "
                f"request-config anyway -- bootstrap may time out"
            )

        # Flow B initiation: ask the video analytics api for the latest verified config.
        self.publisher.publish_request_config(self.bootstrap_ref_id)

        if self.bootstrap_done_event.wait(timeout=self.bootstrap_timeout):
            if self.bootstrap_disk_failure is not None:
                # Disk write failed for a successful bootstrap reply.
                # Continuing on the disk baseline would create config
                # inconsistency with peer replicas that successfully
                # applied the latest config -- crash and let the
                # orchestrator (k8s / docker --restart) retry.
                raise RuntimeError(
                    f"FATAL: bootstrap config write failed "
                    f"(ref={self.bootstrap_ref_id}); aborting startup to "
                    f"avoid config inconsistency with peer replicas"
                ) from self.bootstrap_disk_failure
            logger.info(f"bootstrap completed via upsert-all reply (ref={self.bootstrap_ref_id})")
        else:
            logger.warning(
                f"bootstrap timed out after {self.bootstrap_timeout}s; "
                f"continuing with disk baseline config (ref={self.bootstrap_ref_id})"
            )


    def close(self) -> None:
        """
        Stop both background threads.

        :return: None
        """
        self.running = False
        # Wake any pending bootstrap wait (start() may still be blocking).
        self.bootstrap_done_event.set()
        if self.consumer_thread is not None:
            self.consumer_thread.join(timeout=5.0)
        if self.pruner_thread is not None:
            self.pruner_thread.join(timeout=5.0)


    def _consume_loop(self) -> None:
        """
        Poll the source repeatedly and dispatch each decoded message.

        After the first ``source.poll`` returns -- success or exception --
        we set :attr:`consumer_ready_event` so :meth:`start` can publish
        ``request-config``. The first poll blocks internally during Kafka
        partition assignment; setting the event after it returns means the
        consumer is fully subscribed and seeked. We set on exception too
        so a permanently-broken broker doesn't deadlock startup; the
        bootstrap_timeout in :meth:`start` will then surface the failure.

        Per-iteration exceptions are caught and logged so a transient broker
        hiccup or a malformed message doesn't kill the listener thread.
        """
        while self.running:
            try:
                messages = self.notification_consumer_fn()
            except Exception as e:
                logger.exception(f"config listener: notification consume failed: {e}")
                self.consumer_ready_event.set()
                continue
            self.consumer_ready_event.set()
            for envelope in messages:
                if not self.running:  # pragma: no cover (race: close() flipped flag mid-batch)
                    break
                try:
                    self._dispatch(envelope)
                except Exception as e:
                    logger.exception(f"config listener: dispatch failed: {e}")


    def _dispatch(self, envelope: dict | None) -> None:
        """
        Route one decoded wire envelope.

        Routing order matters:

        1. ``None`` (deserializer rejected the record) -- silently drop.
        2. ``ack`` / ``request-config`` -- this app's own outgoing or a
           peer's bootstrap. Log debug and drop. We deliberately skip the
           validator here so legitimate extras the producer adds to acks
           (e.g. diagnostic metadata) do not generate warning noise.
        3. ``upsert`` / ``upsert-all`` -- run :func:`validate_envelope`.
           A shape violation on ``upsert`` becomes a structured ``failure``
           ack back to web-api (silent drop would leave the producer
           hanging). A shape violation on ``upsert-all`` is logged and
           dropped silently (the bootstrap path is ack-less by design).

        :param dict | None envelope: One element from the polled batch
            -- the dict returned by :func:`deserialize_config_message`,
            or ``None`` for messages the deserializer filtered out.
        :return: None
        """
        if envelope is None:
            return

        event_type = envelope.get("event_type")
        if event_type not in (EVENT_TYPE_UPSERT, EVENT_TYPE_UPSERT_ALL):
            # ack and request-config: own outgoing or peer's bootstrap. Ignore.
            logger.debug(
                f"ignoring inbound {event_type!r} (ref={envelope.get('reference_id', '')!r})"
            )
            return

        msg, shape_error = validate_envelope(envelope)
        if msg is None:
            ref = envelope.get("reference_id", "")
            if event_type == EVENT_TYPE_UPSERT:
                # Producer is owed a structured failure ack.
                self.publisher.publish_ack(
                    ref, ApplyResult(status="failure", error=shape_error),
                )
            else:  # EVENT_TYPE_UPSERT_ALL is silent by contract.
                logger.warning(
                    f"dropping upsert-all: {shape_error} (ref={ref!r})"
                )
            return

        if event_type == EVENT_TYPE_UPSERT:
            self._handle_upsert(msg)
        else:
            self._handle_upsert_all(msg)


    def _handle_upsert(self, msg: ConfigMessage) -> None:
        """
        Flow A: validate -> write file -> apply on main -> ack.

        Order rationale: the file is the source of truth for workers. If the
        write fails, we have not mutated main, so main and workers stay
        consistent on the previous state and the video analytics api gets
        ``failure``. Only once the file is durable do we mutate main's
        :class:`AppConfig`, then ack with the validation status and the
        post-apply snapshot.

        Validator semantics:

        * ``failure`` (malformed payload, or every item rejected) -- ack
          ``failure`` and skip the write.
        * ``success`` with zero applied items (operator sent an empty
          patch as a no-op) -- ack ``success`` with the current snapshot,
          no file write, no apply.
        * ``success`` / ``partial-success`` with applied items -- write the
          file, apply on main, ack with the post-apply snapshot.

        :param ConfigMessage msg: Decoded inbound ``upsert``.
        :return: None
        """
        logger.info(f"upsert received: msg ={msg}")
        if not self._monotonic_check(msg):
            return

        # ``msg.config`` is guaranteed non-None here -- the envelope gate
        # (:func:`validate_envelope`) rejects null config for upsert.
        v = validate(msg.config)
        if v.status == "failure":
            self.publisher.publish_ack(
                msg.reference_id,
                ApplyResult(status="failure", error=v.error),
            )
            return

        if not v.applied_app and not v.applied_sensors:
            # Zero-item no-op: validator passed but the operator's patch
            # had nothing applicable. Skip the file write (workers have
            # nothing to do) and ack ``success`` with the current snapshot.
            self.publisher.publish_ack(
                msg.reference_id,
                ApplyResult(
                    status=v.status,
                    config=self.config.to_mutable_snapshot(),
                    error=v.error,
                ),
            )
            self.last_insert_timestamp = msg.timestamp
            logger.info(
                f"upsert no-op: ref={msg.reference_id} (empty patch, nothing to apply)"
            )
            return

        try:
            self._write_file(EVENT_TYPE_UPSERT, msg.timestamp, v.applied_app, v.applied_sensors)
        except OSError as e:
            self.publisher.publish_ack(
                msg.reference_id,
                ApplyResult(status="failure", error=f"failed to persist: {e}"),
            )
            return

        self.applier.apply(v.applied_app, v.applied_sensors)
        self.publisher.publish_ack(
            msg.reference_id,
            ApplyResult(
                status=v.status,
                config=self.config.to_mutable_snapshot(),
                error=v.error,
            ),
        )
        self.last_insert_timestamp = msg.timestamp
        logger.info(
            f"upsert persisted: ref={msg.reference_id} status={v.status} timestamp={msg.timestamp}"
        )


    def _handle_upsert_all(self, msg: ConfigMessage) -> None:
        """
        Flow B reply. Apply only if the ``reference-id`` matches our own
        bootstrap id, and only the ``app`` + ``sensors`` subset (the video
        analytics api sends the full config including read-only sections).

        Bootstrap is **silent** -- no ack is published either way. Failures
        land in logs and the deployment continues with the disk baseline.
        ``partial-success`` is treated like ``success``: the valid subset is
        persisted and applied; the rejected items are logged.

        :param ConfigMessage msg: Decoded inbound ``upsert-all``.
        :return: None
        """
        if msg.reference_id != self.bootstrap_ref_id:
            logger.debug(f"ignoring upsert-all targeting another replica (ref={msg.reference_id})")
            return

        logger.info(f"upsert-all received: msg ={msg}")
        try:
            if msg.status == "failure" or msg.config is None:
                logger.warning(
                    f"video analytics api had no config to send (ref={msg.reference_id}); "
                    f"continuing with disk baseline. error={msg.error!r}"
                )
                return

            if not self._monotonic_check(msg):
                return

            # The video analytics api's bootstrap reply carries the full config
            # (kafka, redisStream, mqtt, ...). Extract only the dynamically-
            # mutable subset before validating, since validator's scope check
            # would reject the rest.
            config_subset: dict[str, Any] = {
                "app": msg.config.get("app", []),
                "sensors": msg.config.get("sensors", []),
            }
            v = validate(config_subset)
            if v.status == "failure":
                logger.error(f"bootstrap upsert-all rejected: {v.error}")
                return
            if not v.applied_app and not v.applied_sensors:
                # Zero-item no-op: validator passed but the reply had no
                # applicable items (the video-analytics-api returned the
                # equivalent of an empty patch). Log and continue with the
                # disk baseline -- nothing to apply, no file to write.
                logger.info(
                    f"bootstrap upsert-all has no applicable items "
                    f"(ref={msg.reference_id}); continuing with disk baseline"
                )
                return
            if v.error:
                # partial-success path -- keep the valid subset, log the rest.
                logger.warning(f"bootstrap upsert-all had per-item rejections: {v.error}")

            try:
                self._write_file(EVENT_TYPE_UPSERT_ALL, msg.timestamp, v.applied_app, v.applied_sensors)
                logger.info(f"bootstrap config written: ref={msg.reference_id} status={v.status}")
            except OSError as e:
                # Capture for :meth:`start` to surface as a fatal startup
                # error -- disk failure here is pod-specific (network
                # failure already falls back to disk baseline consistently
                # across peers; disk failure on one pod uniquely leaves it
                # behind the rest of the deployment).
                logger.error(f"failed to persist bootstrap config: {e}")
                self.bootstrap_disk_failure = e
                return

            self.applier.apply(v.applied_app, v.applied_sensors)
            self.last_insert_timestamp = msg.timestamp
            logger.info(f"bootstrap config applied: ref={msg.reference_id} status={v.status}")
            logger.info(f"bootstrap applied config: {json.dumps(self.config.to_mutable_snapshot(), indent=2)}")
        finally:
            # Always release start()'s wait, success or failure.
            self.bootstrap_done_event.set()


    def _monotonic_check(self, msg: ConfigMessage) -> bool:
        """
        Skip messages older than what we've already persisted.

        Defends against re-consume after a Kafka offset reset. Returns True
        if the message is fresh (caller should proceed); False if it should
        be skipped.
        """
        if self.last_insert_timestamp >= msg.timestamp:
            logger.debug(
                f"skipping stale config message (ref={msg.reference_id} "
                f"timestamp={msg.timestamp} <= last_insert={self.last_insert_timestamp})"
            )
            return False
        return True


    def _write_file(
        self,
        event_type: str,
        timestamp: datetime,
        applied_app: list[dict[str, str]],
        applied_sensors: list[dict[str, Any]],
    ) -> None:
        """
        Atomically write the filtered applied subset to a timestamped file.

        Filename: ``<event_type>-config-<iso>.json``. Body: JSON of
        ``{"app": [...], "sensors": [...]}`` -- only the items that survived
        validation. Workers read it and call :meth:`ConfigApplier.apply`
        directly without re-deciding what's valid.

        :param str event_type: ``"upsert"`` or ``"upsert-all"``.
        :param datetime timestamp: Notification's record timestamp.
        :param list applied_app: Validated app items.
        :param list applied_sensors: Validated sensor entries.
        :raises OSError: If the temp-write or rename fails.
        """
        ts_str = convert_datetime_to_iso_8601_with_z_suffix(timestamp)
        f_name = f"{event_type}-config-{ts_str}.json".replace(":", "_")
        target = self.config_dir / f_name
        body = {"app": applied_app, "sensors": applied_sensors}
        self._atomic_write(target, json.dumps(body))
        logger.info(f"config written: action={event_type} path={target}")


    @staticmethod
    def _atomic_write(target: Path, content: str) -> None:
        """
        Write ``content`` to ``target`` atomically.

        Stages bytes into a hidden ``.<name>.tmp`` sibling (same filesystem,
        so the subsequent ``os.rename`` is POSIX-atomic) with ``fsync`` for
        durability, then renames into place.

        :param Path target: Final filename.
        :param str content: JSON body to write.
        :return: None
        :raises OSError: Propagates any write/rename failure (cleanup is
            best-effort).
        """
        fd, tmp_path = tempfile.mkstemp(
            prefix=f".{target.name}.",
            suffix=".tmp",
            dir=str(target.parent),
        )
        try:
            with os.fdopen(fd, "w") as f:
                f.write(content)
                f.flush()
                os.fsync(f.fileno())
            os.rename(tmp_path, target)
        except Exception:
            try:
                os.unlink(tmp_path)
            except FileNotFoundError:  # pragma: no cover (temp file already gone)
                pass
            raise


    def _prune_loop(self) -> None:
        """
        Periodically delete files older than :data:`CONFIG_RETAIN_SECONDS`.

        Sleeps in 1-second increments so :meth:`close` can shut the thread
        down promptly.
        """
        while self.running:
            for _ in range(CONFIG_PRUNE_INTERVAL_SECONDS):
                if not self.running:  # pragma: no cover (race: close() flipped flag inside the inner loop)
                    return
                time.sleep(1)
            self._prune_old_files()


    def _prune_old_files(self) -> None:
        """One pass: unlink anything older than the retention window."""
        cutoff = time.time() - CONFIG_RETAIN_SECONDS
        pruned = 0
        for f in self.config_dir.iterdir():
            if not f.is_file():
                continue
            try:
                if f.stat().st_mtime < cutoff:
                    f.unlink()
                    pruned += 1
            except OSError as e:
                logger.warning(f"failed to prune {f}: {e}")
        if pruned > 0:
            logger.debug(f"pruned {pruned} config file(s) older than {CONFIG_RETAIN_SECONDS}s")
