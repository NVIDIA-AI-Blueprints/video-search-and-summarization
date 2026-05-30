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

"""
Listen to ``mdx-notification`` for calibration updates and persist them as
JSON files in :data:`CALIBRATION_DIR` for the per-worker
:class:`CalibrationBase` watchdog observers to pick up.

Design notes
------------
The listener does **not** delete or move files after writing. Workers race
each other to apply the same file in their own process; if main moved the
file out from under a slow worker (the previous design), that worker
silently lost the update. Instead, files stay in ``CALIBRATION_DIR``; a
separate pruner thread deletes anything older than
:data:`CALIBRATION_RETAIN_SECONDS` so disk usage is bounded without
introducing a move-vs-read race.

Writes are atomic: each notification is staged into a hidden ``.tmp`` file
in the same directory, then ``os.rename``\\ d into place. Workers' watchdog
filters dotfiles and non-``.json`` paths and uses ``on_moved`` to detect the
final rename, so a partial write is never observable.

Each notification is schema-validated at the listener **before** the file
is written, so a malformed payload never lands in ``CALIBRATION_DIR``.
The same validator runs again on the worker side (``CalibrationBase.
reload_data``) as defense-in-depth for any file that bypasses the
listener (out-of-band ``mv`` into ``CALIBRATION_DIR``, startup
``--calibration <path>`` load).
"""

import json
import logging
import os
import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path
from collections.abc import Callable

from mdx.analytics.core.constants import (
    CALIBRATION_ACTION_DELETE,
    CALIBRATION_ACTION_UPSERT,
    CALIBRATION_ACTION_UPSERT_ALL,
    CALIBRATION_DIR,
    CALIBRATION_PRUNE_INTERVAL_SECONDS,
    CALIBRATION_RETAIN_SECONDS,
    NOTIFICATION_EPOCH_FLOOR,
)
from mdx.analytics.core.schema.config import AppConfig
from mdx.analytics.core.schema.models import Notification, StreamMessage
from mdx.analytics.core.stream.source.source_base import BytesStrDeserializer
from mdx.analytics.core.transform.calibration.calibration_validator import (
    CalibrationValidationError,
    validate as validate_calibration,
)
from mdx.analytics.core.utils.util import convert_datetime_to_iso_8601_with_z_suffix

logger = logging.getLogger(__name__)

# Kafka key on the shared `mdx-notification` topic that marks a record as
# a calibration message (vs dynamic-config or other notification kinds).
CALIBRATION_NOTIFICATION_KEY: str = "calibration"
_KNOWN_CALIBRATION_EVENT_TYPES: frozenset[str] = frozenset(
    {CALIBRATION_ACTION_UPSERT_ALL, CALIBRATION_ACTION_UPSERT, CALIBRATION_ACTION_DELETE}
)


def deserialize_calibration_message(msg: StreamMessage) -> Notification | None:
    """
    Convert a raw :class:`StreamMessage` into a calibration :class:`Notification`.

    Wire contract (parallel to :func:`deserialize_config_message` for
    dynamic-config):

    * ``key`` -- literal string ``"calibration"`` (filters out non-calibration
      records on the shared ``mdx-notification`` topic).
    * ``headers["event.type"]`` -- one of ``"upsert-all"``, ``"upsert"``,
      ``"delete"``.
    * ``headers["timestamp"]`` -- ISO-8601 UTC timestamp (with ``Z`` or
      ``+00:00`` suffix). Used as the file-naming key on disk and as the
      monotonic high-water mark.
    * ``value`` -- raw JSON body, stored verbatim in
      :attr:`Notification.message` (no parsing here -- the validator and
      worker parse on apply).

    On any header decode error, malformed timestamp, unknown event type,
    or missing required field, the function logs at ``WARNING`` and
    returns ``None`` so the listener's outer loop drops the message.

    :param StreamMessage msg: Raw record from the source's ``poll``.
    :return Notification | None: Decoded notification, or ``None`` if the
        record is not for this listener (wrong key) or fails any header /
        value check.
    """
    if msg.key is None:
        return None
    try:
        key_str = BytesStrDeserializer(msg.key_bytes)
    except UnicodeDecodeError:
        return None
    if key_str != CALIBRATION_NOTIFICATION_KEY:
        return None

    raw_headers = msg.headers or {}
    event_type_raw = raw_headers.get("event.type")
    timestamp_raw = raw_headers.get("timestamp")
    if event_type_raw is None or timestamp_raw is None:
        logger.warning(
            f"dropping calibration message: missing event.type or timestamp header "
            f"(headers={list(raw_headers.keys())})"
        )
        return None
    try:
        event_type = BytesStrDeserializer(event_type_raw)
        timestamp_str = BytesStrDeserializer(timestamp_raw)
    except UnicodeDecodeError as e:
        logger.warning(f"dropping calibration message: header decode failed: {e}")
        return None

    if event_type not in _KNOWN_CALIBRATION_EVENT_TYPES:
        logger.warning(f"dropping calibration message: unknown event.type={event_type!r}")
        return None

    try:
        timestamp = datetime.fromisoformat(timestamp_str)
    except ValueError as e:
        logger.warning(
            f"dropping calibration message: malformed timestamp {timestamp_str!r}: {e}"
        )
        return None

    if msg.value is None:
        logger.warning(
            f"dropping calibration message: empty value (event.type={event_type})"
        )
        return None
    try:
        message_str = BytesStrDeserializer(msg.value_bytes)
    except UnicodeDecodeError as e:
        logger.warning(f"dropping calibration message: value decode failed: {e}")
        return None

    return Notification(event_type=event_type, timestamp=timestamp, message=message_str)


class CalibrationListener:
    """
    Drain calibration notifications from ``mdx-notification`` into
    :data:`CALIBRATION_DIR` for per-worker watchdogs to consume.

    :ivar AppConfig config: Application configuration (currently unused
        directly here but kept for symmetry with other listeners).
    :ivar Callable notification_consumer_fn: Zero-arg callable that returns
        ``list[Notification]`` -- wired by ``app_runner._read_notifications``
        to a Kafka source poll.
    :ivar Path calibration_dir: Where validated notifications are written.
    :ivar datetime last_insert_timestamp: Monotonic high-water mark of
        already-persisted notification timestamps; older arrivals are
        skipped (Kafka usually delivers in order, but this is cheap defense
        against a re-consume after offset reset).
    :ivar bool running: Loop-control flag; set to False by ``close()``.
    :ivar threading.Thread consumer_thread: Background thread that polls
        ``notification_consumer_fn`` and writes files.
    :ivar threading.Thread pruner_thread: Background thread that deletes
        files older than :data:`CALIBRATION_RETAIN_SECONDS`.
    """

    def __init__(self, config: AppConfig, notification_consumer_fn: Callable) -> None:
        """
        Initialize the listener (does not start threads -- call :meth:`start`).

        :param AppConfig config: Application configuration.
        :param Callable notification_consumer_fn: Zero-arg callable returning
            a batch of :class:`Notification` instances.
        :return: None
        """
        self.config = config
        self.notification_consumer_fn = notification_consumer_fn
        self.calibration_dir = Path(CALIBRATION_DIR)
        self.calibration_dir.mkdir(parents=True, exist_ok=True)
        # Initialize at the Unix epoch so the very first notification is accepted.
        self.last_insert_timestamp = NOTIFICATION_EPOCH_FLOOR
        self.running = False
        self.consumer_thread: threading.Thread | None = None
        self.pruner_thread: threading.Thread | None = None


    def start(self) -> None:
        """
        Start the consumer + pruner threads.

        :return: None
        """
        self.running = True
        self.consumer_thread = threading.Thread(
            target=self._consume_loop, name="CalibrationListener.consume", daemon=True
        )
        self.pruner_thread = threading.Thread(
            target=self._prune_loop, name="CalibrationListener.prune", daemon=True
        )
        self.consumer_thread.start()
        self.pruner_thread.start()


    def close(self) -> None:
        """
        Stop both background threads.

        Threads were started as daemons, so process exit would drop them
        anyway; we still join with a short timeout to flush any in-flight
        log output cleanly.

        :return: None
        """
        self.running = False
        if self.consumer_thread is not None:
            self.consumer_thread.join(timeout=5.0)
        if self.pruner_thread is not None:
            self.pruner_thread.join(timeout=5.0)


    def process_notifications(self, notifications: list[Notification]) -> None:
        """
        Persist a batch of notifications to disk.

        Each notification is:

        1. Filtered against ``last_insert_timestamp`` -- older arrivals
           skipped (typically only happens after a Kafka offset reset).
        2. Parsed as JSON and validated against the per-action schema
           (full schema for ``upsert-all`` / ``upsert``, minimal inline
           schema for ``delete``). Malformed JSON or schema violations
           are logged and the notification is **not** written. The
           watermark is left unchanged so a corrected republish under a
           new timestamp still makes it through.
        3. Written atomically: a hidden ``.<name>.tmp`` file in
           ``calibration_dir`` is filled and ``os.rename``\\ d to the final
           ``<action>-calibration-<iso>.json`` name. Workers' watchdog
           ``on_moved`` handler is what triggers their reload; the partial
           ``.tmp`` is never visible because the watchdog filter rejects
           dotfiles.

        :param list[Notification] notifications: Batch from the consumer.
        :return: None
        """
        for notification in notifications:
            if not notification:
                continue
            insertion_time = notification.timestamp
            action = notification.event_type
            if self.last_insert_timestamp >= insertion_time:
                continue

            try:
                payload = json.loads(notification.message)
            except (json.JSONDecodeError, TypeError) as e:
                logger.warning(
                    f"rejecting non-JSON calibration notification at listener: "
                    f"action={action} timestamp={insertion_time} error={e}"
                )
                continue
            try:
                validate_calibration(payload, action)
            except CalibrationValidationError as e:
                logger.warning(
                    f"rejecting invalid calibration payload at listener: "
                    f"action={action} timestamp={insertion_time} -- {e}"
                )
                continue

            insertion_time_str = convert_datetime_to_iso_8601_with_z_suffix(insertion_time)
            f_name = f"{action}-calibration-{insertion_time_str}.json".replace(":", "_")
            target = self.calibration_dir / f_name

            try:
                self._atomic_write(target, notification.message)
            except OSError as e:
                logger.error(f"failed to write calibration file {target}: {e}")
                continue

            self.last_insert_timestamp = insertion_time
            logger.info(
                f"calibration notification written: action={action} "
                f"timestamp={insertion_time} path={target}"
            )


    @staticmethod
    def _atomic_write(target: Path, content: str) -> None:
        """
        Write ``content`` to ``target`` atomically.

        Stages the bytes into a hidden ``.<name>.tmp`` sibling (same
        filesystem, so the subsequent ``os.rename`` is POSIX-atomic) with
        ``fsync`` for durability, then renames into place. If anything goes
        wrong, the temp file is best-effort unlinked and the exception
        propagates.

        :param Path target: Final filename to materialize.
        :param str content: JSON body to write.
        :return: None
        :raises OSError: If the staging write or rename fails.
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


    def _consume_loop(self) -> None:
        """
        Poll ``notification_consumer_fn`` repeatedly and process each batch.

        Per-iteration exceptions are logged and swallowed so a transient
        broker hiccup does not kill the consumer thread.
        """
        while self.running:
            try:
                notifications = self.notification_consumer_fn()
            except Exception as e:
                logger.exception(f"calibration listener consume failed: {e}")
                continue
            try:
                self.process_notifications(notifications)
            except Exception as e:
                logger.exception(f"calibration listener process failed: {e}")


    def _prune_loop(self) -> None:
        """
        Periodically delete files older than :data:`CALIBRATION_RETAIN_SECONDS`.

        Sleeps in 1-second increments so :meth:`close` can shut the thread
        down promptly. Failures (file gone, permission denied, etc.) are
        logged and ignored.
        """
        while self.running:
            for _ in range(CALIBRATION_PRUNE_INTERVAL_SECONDS):
                if not self.running:
                    return
                time.sleep(1)
            self._prune_old_files()


    def _prune_old_files(self) -> None:
        """One pass: unlink anything older than the retention window."""
        cutoff = time.time() - CALIBRATION_RETAIN_SECONDS
        pruned = 0
        for f in self.calibration_dir.iterdir():
            if not f.is_file():
                continue
            try:
                if f.stat().st_mtime < cutoff:
                    f.unlink()
                    pruned += 1
            except OSError as e:
                logger.warning(f"failed to prune {f}: {e}")
        if pruned > 0:
            logger.debug(
                f"pruned {pruned} calibration file(s) older than "
                f"{CALIBRATION_RETAIN_SECONDS}s"
            )
