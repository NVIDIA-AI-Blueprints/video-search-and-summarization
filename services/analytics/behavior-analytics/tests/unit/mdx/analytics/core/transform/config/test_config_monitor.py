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

"""Unit tests for the worker-side :class:`ConfigFileMonitor`."""

import json
import logging
import os
import shutil
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from mdx.analytics.core.transform.config.config_monitor import ConfigFileMonitor


def _moved_event(dest_path: str, is_directory: bool = False) -> MagicMock:
    """Build a MagicMock that quacks like a watchdog FileMovedEvent."""
    evt = MagicMock()
    evt.is_directory = is_directory
    evt.dest_path = dest_path
    return evt


class TestConfigFileMonitorOnMoved(unittest.TestCase):
    """``on_moved`` reads the listener-written file and routes through ``ConfigApplier.apply``.

    The file body is the pre-filtered subset that :class:`ConfigListener` wrote
    after validation, so the worker trusts it -- no re-validation here. The
    only shape check is a cheap ``isinstance(body, dict)`` guard so a corrupt
    or hand-edited non-object file produces a clear log line rather than an
    ``AttributeError``.
    """

    def setUp(self) -> None:
        self.applier = MagicMock()
        self.monitor = ConfigFileMonitor(self.applier)
        self.tmpdir = Path(f"/tmp/test_config_monitor_{os.getpid()}_{id(self)}")
        self.tmpdir.mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        if self.tmpdir.exists():
            shutil.rmtree(self.tmpdir)

    def _write(self, name: str, body: dict) -> Path:
        p = self.tmpdir / name
        p.write_text(json.dumps(body))
        return p

    def test_valid_upsert_file_routes_to_apply(self) -> None:
        path = self._write(
            "upsert-config-2024-01-01T12_00_00.json",
            {"app": [{"name": "behaviorWatermarkSec", "value": "30"}], "sensors": []},
        )
        self.monitor.on_moved(_moved_event(str(path)))
        self.applier.apply.assert_called_once_with([{"name": "behaviorWatermarkSec", "value": "30"}], [])

    def test_valid_upsert_all_file_routes_to_apply(self) -> None:
        """Upsert-all and upsert files funnel into the same apply path."""
        path = self._write(
            "upsert-all-config-2024-01-01T12_00_00.json",
            {
                "app": [{"name": "behaviorWatermarkSec", "value": "30"}],
                "sensors": [{"id": "cam1", "configs": [{"name": "tripwireMinPoints", "value": "5"}]}],
            },
        )
        self.monitor.on_moved(_moved_event(str(path)))
        self.applier.apply.assert_called_once_with(
            [{"name": "behaviorWatermarkSec", "value": "30"}],
            [{"id": "cam1", "configs": [{"name": "tripwireMinPoints", "value": "5"}]}],
        )

    def test_skips_directories(self) -> None:
        self.monitor.on_moved(_moved_event("/anywhere", is_directory=True))
        self.applier.apply.assert_not_called()

    def test_dotfile_skipped(self) -> None:
        """The listener's staging ``.<name>.tmp`` file must be ignored."""
        path = self._write(".upsert-config-2024-01-01T12_00_00.json.tmp", {})
        self.monitor.on_moved(_moved_event(str(path)))
        self.applier.apply.assert_not_called()

    def test_non_json_skipped(self) -> None:
        path = self.tmpdir / "upsert-config-2024-01-01T12_00_00.txt"
        path.write_text("hello")
        self.monitor.on_moved(_moved_event(str(path)))
        self.applier.apply.assert_not_called()

    def test_missing_file_logs_warning(self) -> None:
        bogus = str(self.tmpdir / "upsert-config-vanished.json")
        with self.assertLogs(
            "mdx.analytics.core.transform.config.config_monitor", level=logging.WARNING,
        ) as ctx:
            self.monitor.on_moved(_moved_event(bogus))
        self.assertTrue(any("disappeared during apply" in line for line in ctx.output))
        self.applier.apply.assert_not_called()

    def test_non_dict_body_logs_error(self) -> None:
        """Corrupt / hand-edited non-object file is logged and skipped."""
        path = self.tmpdir / "upsert-config-2024-01-01T12_00_00.json"
        path.write_text(json.dumps([1, 2, 3]))
        with self.assertLogs(
            "mdx.analytics.core.transform.config.config_monitor", level=logging.ERROR,
        ) as ctx:
            self.monitor.on_moved(_moved_event(str(path)))
        self.assertTrue(any("is not a JSON object" in line for line in ctx.output))
        self.applier.apply.assert_not_called()

    def test_unknown_section_in_file_passed_through(self) -> None:
        """The worker no longer re-validates; an unexpected section that
        somehow made it onto disk is harmless because ``apply()`` only reads
        ``app`` and ``sensors``. Anything else is silently ignored.

        (The listener strips forbidden sections before writing, so this
        should never happen in practice.)"""
        path = self._write(
            "upsert-config-2024-01-01T12_00_00.json",
            {"kafka": {}, "app": [], "sensors": []},
        )
        self.monitor.on_moved(_moved_event(str(path)))
        self.applier.apply.assert_called_once_with([], [])

    def test_pre_filtered_file_routed_to_apply_verbatim(self) -> None:
        """Worker trusts the file -- whatever ``app`` / ``sensors`` are in
        the body get passed to ``apply()`` without re-filtering."""
        path = self._write(
            "upsert-config-2024-01-01T12_00_00.json",
            {"app": [{"name": "behaviorWatermarkSec", "value": "30"}], "sensors": []},
        )
        self.monitor.on_moved(_moved_event(str(path)))
        self.applier.apply.assert_called_once_with(
            [{"name": "behaviorWatermarkSec", "value": "30"}], [],
        )

    def test_apply_exception_logged_and_does_not_propagate(self) -> None:
        """If applier.apply raises, we log and return so the watchdog thread keeps running."""
        # Need a payload that survives validation so apply() is reached.
        path = self._write(
            "upsert-config-2024-01-01T12_00_00.json",
            {"app": [{"name": "behaviorWatermarkSec", "value": "30"}]},
        )
        self.applier.apply.side_effect = RuntimeError("boom")
        with self.assertLogs(
            "mdx.analytics.core.transform.config.config_monitor", level=logging.ERROR,
        ) as ctx:
            self.monitor.on_moved(_moved_event(str(path)))  # must not raise
        self.assertTrue(any("failed to apply" in line for line in ctx.output))

    def test_does_not_define_on_created(self) -> None:
        """on_created is intentionally not implemented -- non-atomic writes are not supported."""
        self.assertNotIn("on_created", ConfigFileMonitor.__dict__)


class TestConfigFileMonitorLifecycle(unittest.TestCase):
    """start_listen / close manage the watchdog Observer."""

    def setUp(self) -> None:
        self.applier = MagicMock()
        self.tmpdir = Path(f"/tmp/test_config_monitor_lc_{os.getpid()}_{id(self)}")
        if self.tmpdir.exists():
            shutil.rmtree(self.tmpdir)
        self._patcher = patch(
            "mdx.analytics.core.transform.config.config_monitor.CONFIG_DIR",
            str(self.tmpdir),
        )
        self._patcher.start()
        self.monitor = ConfigFileMonitor(self.applier)

    def tearDown(self) -> None:
        if self.tmpdir.exists():
            shutil.rmtree(self.tmpdir)
        self._patcher.stop()

    def test_start_listen_creates_dir_and_starts_observer(self) -> None:
        self.monitor.start_listen()
        try:
            self.assertTrue(self.tmpdir.exists())
            self.assertIsNotNone(self.monitor._observer)
            self.assertTrue(self.monitor._observer.is_alive())
        finally:
            self.monitor.close()

    def test_close_stops_observer(self) -> None:
        self.monitor.start_listen()
        self.monitor.close()
        self.assertFalse(self.monitor._observer.is_alive())

    def test_close_without_start_is_safe(self) -> None:
        self.monitor.close()  # observer is None; should not raise

    def test_close_observer_join_timeout_logs_warning(self) -> None:
        """If observer.join times out, a warning is logged but close() returns."""
        mock_observer = MagicMock()
        mock_observer.is_alive.return_value = True
        self.monitor._observer = mock_observer

        with self.assertLogs(
            "mdx.analytics.core.transform.config.config_monitor", level=logging.WARNING,
        ) as ctx:
            self.monitor.close()
        mock_observer.stop.assert_called_once()
        self.assertTrue(any("did not terminate within 5s" in line for line in ctx.output))


if __name__ == "__main__":
    unittest.main()
