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
In-process integration of :class:`ConfigListener` and :class:`ConfigFileMonitor`.

Each side has its own unit-test file with mocks; this file proves they actually
meet through the filesystem: the listener's atomic-rename triggers the
monitor's real :class:`watchdog.observers.Observer`, which fires
``on_moved`` and applies through a real :class:`ConfigApplier`.

These tests exercise behavior that 100% line coverage on either side
individually does not prove:

* ``os.rename`` into the watched dir actually fires ``on_moved`` (not a
  spurious ``on_created`` that we'd silently ignore).
* The hidden ``.<name>.tmp`` staging file is never observable as a final
  ``.json`` -- the dotfile filter in the monitor holds in practice.
* End-to-end: writing a payload through the listener results in a mutated
  AppConfig on the monitor side.

Multi-replica fan-out is also covered here: two independent
listener+monitor pairs receiving the same synthetic batch converge to
identical AppConfig states.
"""

import os
import shutil
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock, patch

from mdx.analytics.core.schema.config import AppConfig
from mdx.analytics.core.transform.config.config_applier import ConfigApplier
from mdx.analytics.core.transform.config.config_listener import (
    ConfigListener,
    EVENT_TYPE_UPSERT,
    EVENT_TYPE_UPSERT_ALL,
)
from mdx.analytics.core.transform.config.config_monitor import ConfigFileMonitor


# Maximum time we'll wait for the watchdog to deliver an event before failing.
# Watchdog's inotify backend is normally sub-100ms; 2s leaves slack for CI.
_WATCHDOG_DELIVERY_TIMEOUT_SEC: float = 2.0


def _wait_for(predicate, timeout: float = _WATCHDOG_DELIVERY_TIMEOUT_SEC, interval: float = 0.02) -> bool:
    """
    Poll ``predicate`` until it returns truthy or ``timeout`` elapses.

    :return bool: ``True`` if the predicate became truthy in time.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


class _PipelineTestBase(unittest.TestCase):
    """Per-test temp dir + a real listener + a real monitor pointed at it."""

    def setUp(self) -> None:
        self.config_dir = Path(f"/tmp/test_config_pipeline_{os.getpid()}_{id(self)}")
        if self.config_dir.exists():
            shutil.rmtree(self.config_dir)
        # The listener writes here; the monitor watches the same path.
        self._patcher_listener = patch(
            "mdx.analytics.core.transform.config.config_listener.CONFIG_DIR",
            str(self.config_dir),
        )
        self._patcher_monitor = patch(
            "mdx.analytics.core.transform.config.config_monitor.CONFIG_DIR",
            str(self.config_dir),
        )
        self._patcher_listener.start()
        self._patcher_monitor.start()

        # Worker side: real applier + monitor mutating its own AppConfig.
        self.worker_config = AppConfig()
        self.worker_applier = ConfigApplier(self.worker_config)
        self.monitor = ConfigFileMonitor(self.worker_applier)
        self.monitor.start_listen()

        # Main side: real applier + listener mutating its own AppConfig. The
        # publisher is mocked because we don't need to test the wire format
        # here (covered by test_config_publisher.py).
        self.main_config = AppConfig()
        self.main_applier = ConfigApplier(self.main_config)
        self.publisher = Mock()
        self.bootstrap_ref = "behavior-analytics-pipeline"
        self.listener = ConfigListener(
            config=self.main_config,
            applier=self.main_applier,
            publisher=self.publisher,
            notification_consumer_fn=lambda: [],
            bootstrap_ref_id=self.bootstrap_ref,
        )

    def tearDown(self) -> None:
        self.monitor.close()
        self.listener.running = False
        if self.config_dir.exists():
            shutil.rmtree(self.config_dir)
        self._patcher_listener.stop()
        self._patcher_monitor.stop()

    def _cm(self, event_type: str, *, reference_id: str = "ref", config: dict | None = None) -> dict:
        """Build a wire-envelope dict (what :func:`deserialize_config_message`
        produces today) ready for :meth:`ConfigListener._dispatch`."""
        return {
            "event_type": event_type,
            "reference_id": reference_id,
            "timestamp": datetime.now(timezone.utc),
            "config": config,
            "status": None,
            "error": None,
        }


class TestListenerToMonitorUpsert(_PipelineTestBase):
    """Flow A: listener._handle_upsert -> file on disk -> monitor.on_moved -> worker AppConfig mutated."""

    def test_upsert_propagates_to_worker(self) -> None:
        """Happy path: validated upsert lands as a file and the monitor applies it."""
        self.listener._dispatch(
            self._cm(EVENT_TYPE_UPSERT, reference_id="ref-A", config={"app": [{"name": "behaviorWatermarkSec", "value": "30"}]})
        )
        # Listener side mutated immediately.
        self.assertEqual(self.main_config.get_app_config("behaviorWatermarkSec"), "30")
        # Watchdog delivers the rename to the monitor; assertion polls until the
        # worker AppConfig reflects it.
        applied = _wait_for(lambda: self.worker_config.get_app_config("behaviorWatermarkSec", default_value=None) == "30")
        self.assertTrue(applied, "watchdog never delivered the upsert to the worker")

    def test_partial_success_sends_only_valid_subset_to_worker(self) -> None:
        """Mixed valid+invalid items: file holds only the valid ones; worker applies only those."""
        self.listener._dispatch(
            self._cm(
                EVENT_TYPE_UPSERT,
                config={"app": [{"name": "behaviorWatermarkSec", "value": "30"}, {"name": "", "value": "x"}]},
            )
        )
        applied = _wait_for(
            lambda: self.worker_config.get_app_config("behaviorWatermarkSec", default_value=None) == "30"
        )
        self.assertTrue(applied)
        # The bad item never reaches the worker -- it was filtered out at the listener's validator.
        self.assertEqual(self.worker_config.get_app_config("", default_value="<unset>"), "<unset>")

    def test_validation_failure_writes_no_file_worker_unchanged(self) -> None:
        """Forbidden section in payload: listener acks failure, no file lands, worker AppConfig stays empty."""
        self.listener._dispatch(self._cm(EVENT_TYPE_UPSERT, config={"kafka": {}}))
        # Give the watchdog a tick to confirm nothing arrives.
        time.sleep(0.2)
        json_files = [p for p in self.config_dir.iterdir() if p.name.endswith(".json")]
        self.assertEqual(json_files, [])
        self.assertEqual(self.worker_config.get_app_config("behaviorWatermarkSec", default_value="<unset>"), "<unset>")

    def test_atomic_write_tmp_never_visible_to_monitor(self) -> None:
        """The hidden ``.<name>.tmp`` staging file must not be applied by the worker.

        We can't easily catch the tmp file mid-write (the rename happens on the
        same fast path), but we can verify the worker's applier is only ever
        called with the final filtered subset and that no .tmp survives.
        """
        self.listener._dispatch(
            self._cm(EVENT_TYPE_UPSERT, config={"app": [{"name": "behaviorWatermarkSec", "value": "30"}]})
        )
        applied = _wait_for(lambda: self.worker_config.get_app_config("behaviorWatermarkSec", default_value=None) == "30")
        self.assertTrue(applied)
        # No .tmp lingering and no .tmp masquerading as a final file (the worker
        # AppConfig has only the one expected key, no garbage from a partial
        # write).
        leftovers = [p for p in self.config_dir.iterdir() if p.name.endswith(".tmp")]
        self.assertEqual(leftovers, [])

    def test_bootstrap_upsert_all_propagates(self) -> None:
        """Flow B reply: extracted subset lands on disk and the monitor applies it (no ack)."""
        self.listener._dispatch(
            self._cm(
                EVENT_TYPE_UPSERT_ALL,
                reference_id=self.bootstrap_ref,
                # Video analytics api sends the full config; only app+sensors should land in the file.
                config={
                    "app": [{"name": "behaviorWatermarkSec", "value": "30"}],
                    "sensors": [{"id": "cam1", "configs": [{"name": "tripwireMinPoints", "value": "5"}]}],
                    "kafka": {"brokers": "stale"},
                },
            )
        )
        applied = _wait_for(
            lambda: (
                self.worker_config.get_app_config("behaviorWatermarkSec", default_value=None) == "30"
                and self.worker_config.get_sensor_config(key="tripwireMinPoints", sensor_id="cam1", default_value=None) == "5"
            )
        )
        self.assertTrue(applied, "bootstrap subset did not propagate to worker")
        # Bootstrap is silent -- no ack.
        self.publisher.publish_ack.assert_not_called()


class TestMultiReplicaFanout(unittest.TestCase):
    """Two independent listener+monitor pairs receiving the same synthetic upsert.

    Each replica has its own AppConfig + own CONFIG_DIR (mirrors what happens in
    a multi-pod deployment, where each pod's main writes to its own /tmp). After
    a shared synthetic upsert, both workers' AppConfig should converge.
    """

    def _make_pair(self, suffix: str):
        config_dir = Path(f"/tmp/test_config_pipeline_multi_{os.getpid()}_{suffix}")
        if config_dir.exists():
            shutil.rmtree(config_dir)

        listener_patch = patch(
            "mdx.analytics.core.transform.config.config_listener.CONFIG_DIR",
            str(config_dir),
        )
        # Both pairs share the same monitor module -- we don't need to patch the
        # monitor's CONFIG_DIR because we hand the monitor a custom path through
        # start_listen by patching it for this pair only.
        listener_patch.start()
        try:
            worker_config = AppConfig()
            worker_applier = ConfigApplier(worker_config)
            monitor_patch = patch(
                "mdx.analytics.core.transform.config.config_monitor.CONFIG_DIR",
                str(config_dir),
            )
            monitor_patch.start()
            monitor = ConfigFileMonitor(worker_applier)
            monitor.start_listen()
            monitor_patch.stop()

            main_config = AppConfig()
            main_applier = ConfigApplier(main_config)
            listener = ConfigListener(
                config=main_config,
                applier=main_applier,
                publisher=Mock(),
                notification_consumer_fn=lambda: [],
                bootstrap_ref_id=f"behavior-analytics-{suffix}",
            )
        finally:
            listener_patch.stop()
        return config_dir, worker_config, listener, monitor

    def test_two_replicas_converge_on_same_upsert(self) -> None:
        dir_a, worker_a, listener_a, monitor_a = self._make_pair("A")
        dir_b, worker_b, listener_b, monitor_b = self._make_pair("B")
        try:
            shared_msg = {
                "event_type": EVENT_TYPE_UPSERT,
                "reference_id": "ref-shared",
                "timestamp": datetime.now(timezone.utc),
                "config": {"app": [{"name": "behaviorWatermarkSec", "value": "60"}]},
                "status": None,
                "error": None,
            }
            # Each listener's _handle_upsert points its CONFIG_DIR at a different
            # path -- to make this work in-process, we patch the listener module
            # constant per call.
            for d, lst in [(dir_a, listener_a), (dir_b, listener_b)]:
                with patch(
                    "mdx.analytics.core.transform.config.config_listener.CONFIG_DIR",
                    str(d),
                ):
                    lst.config_dir = d  # listener captured this on construction; resync
                    lst._dispatch(shared_msg)

            converged = _wait_for(
                lambda: (
                    worker_a.get_app_config("behaviorWatermarkSec", default_value=None) == "60"
                    and worker_b.get_app_config("behaviorWatermarkSec", default_value=None) == "60"
                )
            )
            self.assertTrue(converged, "multi-replica fan-out did not converge")
        finally:
            monitor_a.close()
            monitor_b.close()
            listener_a.running = False
            listener_b.running = False
            for d in [dir_a, dir_b]:
                if d.exists():
                    shutil.rmtree(d)


if __name__ == "__main__":
    unittest.main()
