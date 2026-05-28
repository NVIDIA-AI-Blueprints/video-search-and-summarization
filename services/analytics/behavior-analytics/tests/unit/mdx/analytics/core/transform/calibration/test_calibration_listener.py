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

"""Unit tests for the simplified :class:`CalibrationListener`."""

import logging
import os
import shutil
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

from mdx.analytics.core.schema.config import AppConfig
from mdx.analytics.core.schema.models import Notification, StreamMessage
from mdx.analytics.core.transform.calibration.calibration_listener import (
    CalibrationListener,
    deserialize_calibration_message,
)
from mdx.analytics.core.utils.util import convert_datetime_to_iso_8601_with_z_suffix


class _ListenerTestBase(unittest.TestCase):
    """Shared setup that points the listener at a per-test temp dir."""

    def setUp(self) -> None:
        self.config = MagicMock(spec=AppConfig)
        self.calibration_dir = Path(f"/tmp/test_calibration_{os.getpid()}_{id(self)}")
        # Make sure we start clean even if a previous run leaked state.
        if self.calibration_dir.exists():
            shutil.rmtree(self.calibration_dir)

        self._patcher = patch(
            "mdx.analytics.core.transform.calibration.calibration_listener.CALIBRATION_DIR",
            str(self.calibration_dir),
        )
        self._patcher.start()

        self.notification_ts = datetime.now(timezone.utc)
        # Default fixture uses `delete` so the minimal payload
        # `{"sensors": [{"id": "cam1"}]}` passes listener-side schema
        # validation without dragging in the full upsert schema.
        self.notification_consumer_fn = lambda: [
            Notification(
                event_type="delete",
                message='{"sensors": [{"id": "cam1"}]}',
                timestamp=self.notification_ts,
            )
        ]
        self.listener = CalibrationListener(self.config, self.notification_consumer_fn)

    def tearDown(self) -> None:
        self.listener.running = False
        if self.calibration_dir.exists():
            shutil.rmtree(self.calibration_dir)
        self._patcher.stop()


class TestCalibrationListenerInit(_ListenerTestBase):
    """Initialization wiring."""

    def test_initialization_creates_directory(self) -> None:
        self.assertIsInstance(self.listener, CalibrationListener)
        self.assertTrue(self.calibration_dir.exists())
        self.assertEqual(self.listener.config, self.config)
        # Threads not started until start().
        self.assertIsNone(self.listener.consumer_thread)
        self.assertIsNone(self.listener.pruner_thread)
        self.assertFalse(self.listener.running)


class TestProcessNotifications(_ListenerTestBase):
    """``process_notifications`` is the validate + write path."""

    def test_writes_valid_notification(self) -> None:
        self.listener.process_notifications(self.notification_consumer_fn())

        ts_str = convert_datetime_to_iso_8601_with_z_suffix(self.notification_ts)
        expected = self.calibration_dir / f"delete-calibration-{ts_str}.json".replace(":", "_")
        self.assertTrue(expected.exists())
        self.assertEqual(expected.read_text(), '{"sensors": [{"id": "cam1"}]}')
        # Timestamp advanced.
        self.assertEqual(self.listener.last_insert_timestamp, self.notification_ts)

    def test_empty_list_is_noop(self) -> None:
        initial_ts = self.listener.last_insert_timestamp
        self.listener.process_notifications([])
        self.assertEqual(list(self.calibration_dir.iterdir()), [])
        self.assertEqual(self.listener.last_insert_timestamp, initial_ts)

    def test_old_timestamp_skipped(self) -> None:
        future_ts = datetime(2099, 1, 1, tzinfo=timezone.utc)
        self.listener.last_insert_timestamp = future_ts
        old_notification = Notification(
            event_type="delete",
            message='{"sensors": [{"id": "cam1"}]}',
            timestamp=datetime(2024, 1, 1, tzinfo=timezone.utc),
        )
        self.listener.process_notifications([old_notification])
        self.assertEqual(list(self.calibration_dir.iterdir()), [])
        self.assertEqual(self.listener.last_insert_timestamp, future_ts)

    def test_falsy_notification_skipped(self) -> None:
        """A None entry in the batch is silently skipped (not an error)."""
        self.listener.process_notifications([None])  # type: ignore[list-item]
        self.assertEqual(list(self.calibration_dir.iterdir()), [])

    def test_consume_loop_swallows_process_exception(self) -> None:
        """If process_notifications raises, the loop logs and keeps running."""
        # First call -> returns one notification but process_notifications crashes;
        # subsequent calls return empty. Exception must be logged and not propagated.
        attempts = {"n": 0}

        def consume_fn() -> list:
            attempts["n"] += 1
            if attempts["n"] == 1:
                return [
                    Notification(
                        event_type="upsert",
                        message='{"sensors": [{"id": "cam1"}]}',
                        timestamp=datetime.now(timezone.utc),
                    )
                ]
            return []

        self.listener.notification_consumer_fn = consume_fn
        with patch.object(
            self.listener, "process_notifications", side_effect=RuntimeError("boom")
        ):
            with self.assertLogs(
                "mdx.analytics.core.transform.calibration.calibration_listener",
                level=logging.ERROR,
            ) as ctx:
                self.listener.start()
                time.sleep(0.05)
                self.listener.close()
        self.assertTrue(any("process failed" in line for line in ctx.output))

    def test_multiple_notifications_in_order(self) -> None:
        notifications = [
            Notification(
                event_type="delete",
                message=f'{{"sensors": [{{"id": "cam{i}"}}]}}',
                timestamp=datetime(2024, 1, 1 + i, 12, tzinfo=timezone.utc),
            )
            for i in range(3)
        ]
        self.listener.process_notifications(notifications)
        self.assertEqual(len(list(self.calibration_dir.iterdir())), 3)
        # Final timestamp = latest.
        self.assertEqual(self.listener.last_insert_timestamp, notifications[-1].timestamp)

    def test_atomic_write_leaves_no_tmp(self) -> None:
        """Successful writes leave no .tmp staging file behind."""
        self.listener.process_notifications(self.notification_consumer_fn())
        leftovers = [p for p in self.calibration_dir.iterdir() if p.name.endswith(".tmp")]
        self.assertEqual(leftovers, [])

    def test_atomic_write_cleans_tmp_on_rename_failure(self) -> None:
        """If os.rename fails, the .tmp file is unlinked and no final file appears."""
        with patch(
            "mdx.analytics.core.transform.calibration.calibration_listener.os.rename",
            side_effect=OSError("simulated"),
        ):
            with self.assertLogs(
                "mdx.analytics.core.transform.calibration.calibration_listener",
                level=logging.ERROR,
            ):
                self.listener.process_notifications(self.notification_consumer_fn())
        # No .json AND no .tmp survive.
        contents = list(self.calibration_dir.iterdir())
        self.assertEqual(contents, [])
        # Timestamp was NOT advanced -- next iteration will retry.
        self.assertNotEqual(self.listener.last_insert_timestamp, self.notification_ts)

    def test_schema_invalid_notification_rejected_at_listener(self) -> None:
        """Listener rejects schema-violating payloads BEFORE the write.

        Payload is missing the required ``id`` on the lone sensor entry,
        which trips the minimal delete-schema validator. The listener
        must log the rejection, NOT create any file, and NOT advance the
        watermark (so a corrected republish with a fresh timestamp can
        still get through).
        """
        initial_ts = self.listener.last_insert_timestamp
        bad = Notification(
            event_type="delete",
            message='{"sensors": [{"type": "camera"}]}',  # missing id
            timestamp=self.notification_ts,
        )
        with self.assertLogs(
            "mdx.analytics.core.transform.calibration.calibration_listener",
            level=logging.WARNING,
        ) as ctx:
            self.listener.process_notifications([bad])
        self.assertEqual(list(self.calibration_dir.iterdir()), [])
        self.assertEqual(self.listener.last_insert_timestamp, initial_ts)
        self.assertTrue(any("rejecting invalid calibration payload" in line for line in ctx.output))

    def test_malformed_json_rejected_at_listener(self) -> None:
        """A non-JSON message body is logged and skipped without writing."""
        initial_ts = self.listener.last_insert_timestamp
        bad = Notification(
            event_type="delete",
            message="not-valid-json{",
            timestamp=self.notification_ts,
        )
        with self.assertLogs(
            "mdx.analytics.core.transform.calibration.calibration_listener",
            level=logging.WARNING,
        ) as ctx:
            self.listener.process_notifications([bad])
        self.assertEqual(list(self.calibration_dir.iterdir()), [])
        self.assertEqual(self.listener.last_insert_timestamp, initial_ts)
        self.assertTrue(any("rejecting non-JSON" in line for line in ctx.output))

    def test_unknown_action_rejected_at_listener(self) -> None:
        """An action prefix the validator doesn't recognize is rejected."""
        initial_ts = self.listener.last_insert_timestamp
        bad = Notification(
            event_type="bogus-action",
            message='{"sensors": [{"id": "cam1"}]}',
            timestamp=self.notification_ts,
        )
        with self.assertLogs(
            "mdx.analytics.core.transform.calibration.calibration_listener",
            level=logging.WARNING,
        ) as ctx:
            self.listener.process_notifications([bad])
        self.assertEqual(list(self.calibration_dir.iterdir()), [])
        self.assertEqual(self.listener.last_insert_timestamp, initial_ts)
        self.assertTrue(any("rejecting invalid calibration payload" in line for line in ctx.output))


class TestPruner(_ListenerTestBase):
    """``_prune_old_files`` deletes files older than the retention window."""

    def _touch(self, name: str, age_seconds: float) -> Path:
        """Create a file in calibration_dir whose mtime is ``age_seconds`` in the past."""
        p = self.calibration_dir / name
        p.write_text("{}")
        past = time.time() - age_seconds
        os.utime(p, (past, past))
        return p

    def test_prunes_old_files_only(self) -> None:
        from mdx.analytics.core.transform.calibration.calibration_listener import (
            CALIBRATION_RETAIN_SECONDS,
        )
        old = self._touch("old.json", age_seconds=CALIBRATION_RETAIN_SECONDS + 60)
        recent = self._touch("recent.json", age_seconds=10)

        self.listener._prune_old_files()

        self.assertFalse(old.exists())
        self.assertTrue(recent.exists())

    def test_empty_dir_is_noop(self) -> None:
        # Should not raise.
        self.listener._prune_old_files()

    def test_unlink_oserror_logged_and_continues(self) -> None:
        """If unlink fails on one file, the loop continues with the rest."""
        from mdx.analytics.core.transform.calibration.calibration_listener import (
            CALIBRATION_RETAIN_SECONDS,
        )
        bad = self._touch("bad.json", age_seconds=CALIBRATION_RETAIN_SECONDS + 60)
        good = self._touch("good.json", age_seconds=CALIBRATION_RETAIN_SECONDS + 60)

        original_unlink = Path.unlink

        def selective_unlink(self, *args, **kwargs):
            if self.name == "bad.json":
                raise OSError("simulated permission denied")
            return original_unlink(self, *args, **kwargs)

        with patch.object(Path, "unlink", selective_unlink):
            with self.assertLogs(
                "mdx.analytics.core.transform.calibration.calibration_listener",
                level=logging.WARNING,
            ) as ctx:
                self.listener._prune_old_files()
        self.assertTrue(any("failed to prune" in line for line in ctx.output))
        # Good file was still pruned despite the bad one failing.
        self.assertFalse(good.exists())

    def test_directories_are_skipped(self) -> None:
        subdir = self.calibration_dir / "subdir"
        subdir.mkdir()
        old_file = self._touch("old.json", age_seconds=999999)

        self.listener._prune_old_files()

        # Subdirectory left alone; old file gone.
        self.assertTrue(subdir.exists())
        self.assertFalse(old_file.exists())


class TestStartClose(_ListenerTestBase):
    """Lifecycle: ``start`` spins up two threads, ``close`` shuts them down."""

    def test_start_runs_two_threads(self) -> None:
        self.listener.start()
        try:
            self.assertTrue(self.listener.running)
            self.assertTrue(self.listener.consumer_thread.is_alive())
            self.assertTrue(self.listener.pruner_thread.is_alive())
        finally:
            self.listener.close()

    def test_close_sets_running_false_and_joins(self) -> None:
        self.listener.start()
        self.listener.close()
        self.assertFalse(self.listener.running)
        # Threads exit promptly (consumer_thread loops on its consume_fn,
        # which is a fast lambda; pruner_thread checks running every 1s).
        self.assertFalse(self.listener.consumer_thread.is_alive())

    def test_close_without_start_is_safe(self) -> None:
        # Should not raise.
        self.listener.close()

    def test_pruner_loop_runs_prune_pass(self) -> None:
        """The pruner loop's while-body actually invokes _prune_old_files at least once."""
        # Patch the interval to 1 (so one second of sleep brings us past the inner loop).
        with patch(
            "mdx.analytics.core.transform.calibration.calibration_listener."
            "CALIBRATION_PRUNE_INTERVAL_SECONDS",
            1,
        ):
            with patch.object(self.listener, "_prune_old_files") as mock_prune:
                self.listener.start()
                # Wait long enough for the pruner to complete one iteration.
                time.sleep(1.5)
                self.listener.close()
        mock_prune.assert_called()

    def test_consume_loop_swallows_exceptions(self) -> None:
        """A misbehaving consumer fn is logged and the loop continues."""
        attempts = {"n": 0}

        def flaky() -> list:
            attempts["n"] += 1
            if attempts["n"] == 1:
                raise RuntimeError("kafka hiccup")
            return []

        self.listener.notification_consumer_fn = flaky
        with self.assertLogs(
            "mdx.analytics.core.transform.calibration.calibration_listener",
            level=logging.ERROR,
        ):
            self.listener.start()
            time.sleep(0.05)  # let the loop iterate past the exception
            self.listener.close()
        self.assertGreaterEqual(attempts["n"], 2)


def _calibration_msg(
    *,
    key: bytes | None = b"calibration",
    event_type: bytes | None = b"upsert",
    timestamp: bytes | None = b"2026-05-15T10:00:00+00:00",
    value: bytes | None = b'{"sensors": [{"id": "cam1"}]}',
    extra_headers: dict[str, bytes] | None = None,
) -> StreamMessage:
    """Convenience builder mirroring what a Kafka calibration record looks like."""
    headers: dict[str, bytes] = {}
    if event_type is not None:
        headers["event.type"] = event_type
    if timestamp is not None:
        headers["timestamp"] = timestamp
    if extra_headers:
        headers.update(extra_headers)
    return StreamMessage(key=key, value=value, headers=headers)


class TestDeserializeCalibrationMessage(unittest.TestCase):
    """``deserialize_calibration_message`` is the calibration-side gatekeeper.

    Parallel to ``TestDeserializeConfigMessage`` for dynamic-config; same
    intent (key filter, header decode, structured drop on any malformed
    field) with the calibration wire contract (``key="calibration"``,
    ISO-8601 ``timestamp`` header, action prefix from ``event.type``).
    """

    def test_valid_upsert(self) -> None:
        n = deserialize_calibration_message(_calibration_msg(event_type=b"upsert"))
        self.assertIsNotNone(n)
        self.assertEqual(n.event_type, "upsert")
        self.assertEqual(n.timestamp, datetime(2026, 5, 15, 10, 0, 0, tzinfo=timezone.utc))
        self.assertEqual(n.message, '{"sensors": [{"id": "cam1"}]}')

    def test_valid_upsert_all(self) -> None:
        n = deserialize_calibration_message(_calibration_msg(event_type=b"upsert-all"))
        self.assertIsNotNone(n)
        self.assertEqual(n.event_type, "upsert-all")

    def test_valid_delete(self) -> None:
        n = deserialize_calibration_message(_calibration_msg(event_type=b"delete"))
        self.assertIsNotNone(n)
        self.assertEqual(n.event_type, "delete")

    def test_wrong_key_filtered_silently(self) -> None:
        """Non-calibration keys (e.g. dynamic-config) must return None
        without logging -- they're handled by another deserializer on the
        same topic."""
        with self.assertNoLogs("mdx.analytics.core.transform.calibration.calibration_listener", level=logging.WARNING):
            self.assertIsNone(deserialize_calibration_message(_calibration_msg(key=b"behavior-analytics-config")))

    def test_missing_key_returns_none(self) -> None:
        self.assertIsNone(deserialize_calibration_message(_calibration_msg(key=None)))

    def test_invalid_utf8_in_key_returns_none(self) -> None:
        """Garbled key bytes are dropped silently (not addressed to us)."""
        self.assertIsNone(deserialize_calibration_message(_calibration_msg(key=b"\xff\xfe")))

    def test_missing_event_type_dropped(self) -> None:
        with self.assertLogs("mdx.analytics.core.transform.calibration.calibration_listener", level=logging.WARNING):
            self.assertIsNone(deserialize_calibration_message(_calibration_msg(event_type=None)))

    def test_missing_timestamp_dropped(self) -> None:
        with self.assertLogs("mdx.analytics.core.transform.calibration.calibration_listener", level=logging.WARNING):
            self.assertIsNone(deserialize_calibration_message(_calibration_msg(timestamp=None)))

    def test_unknown_event_type_dropped(self) -> None:
        with self.assertLogs("mdx.analytics.core.transform.calibration.calibration_listener", level=logging.WARNING) as ctx:
            self.assertIsNone(deserialize_calibration_message(_calibration_msg(event_type=b"frobnicate")))
        self.assertTrue(any("unknown event.type" in line for line in ctx.output))

    def test_invalid_utf8_in_header_dropped(self) -> None:
        with self.assertLogs("mdx.analytics.core.transform.calibration.calibration_listener", level=logging.WARNING) as ctx:
            self.assertIsNone(deserialize_calibration_message(_calibration_msg(event_type=b"\xff\xfe")))
        self.assertTrue(any("header decode failed" in line for line in ctx.output))

    def test_malformed_timestamp_dropped(self) -> None:
        with self.assertLogs("mdx.analytics.core.transform.calibration.calibration_listener", level=logging.WARNING) as ctx:
            self.assertIsNone(deserialize_calibration_message(_calibration_msg(timestamp=b"not-a-date")))
        self.assertTrue(any("malformed timestamp" in line for line in ctx.output))

    def test_empty_value_dropped(self) -> None:
        with self.assertLogs("mdx.analytics.core.transform.calibration.calibration_listener", level=logging.WARNING) as ctx:
            self.assertIsNone(deserialize_calibration_message(_calibration_msg(value=None)))
        self.assertTrue(any("empty value" in line for line in ctx.output))

    def test_invalid_utf8_in_value_dropped(self) -> None:
        with self.assertLogs("mdx.analytics.core.transform.calibration.calibration_listener", level=logging.WARNING) as ctx:
            self.assertIsNone(deserialize_calibration_message(_calibration_msg(value=b"\xff\xfe")))
        self.assertTrue(any("value decode failed" in line for line in ctx.output))

    def test_extra_headers_tolerated(self) -> None:
        """Unknown extra headers must NOT cause the message to be dropped --
        forward-compatibility with any new header web-api may add later."""
        n = deserialize_calibration_message(
            _calibration_msg(extra_headers={"priority": b"high"})
        )
        self.assertIsNotNone(n)
        self.assertEqual(n.event_type, "upsert")


if __name__ == "__main__":
    unittest.main()
