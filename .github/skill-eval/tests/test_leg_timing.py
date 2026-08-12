#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Unit tests for leg_timing.py.

Run:
    python3 .github/skill-eval/tests/test_leg_timing.py

This module's contract is "record the leg's shape, and under no circumstance
change its verdict", so the cases that matter are the ones that fail when a
safety property is removed. The tests that drive that contract THROUGH
run_leg.main -- the lock-wait label, SIGTERM unwinding, a heartbeat that cannot
start -- live in test_run_leg.py, because they are about the wiring rather than
about this module.

Mutations confirmed to fail this suite:
  * log the phase-end line before restoring the label
  * wait a full interval before the first heartbeat tick
  * write the artifact in place instead of renaming it into position
  * drop the `if not _PHASES` guard so an empty run writes a file
"""
from __future__ import annotations

import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import leg_timing  # noqa: E402 - must follow the sys.path insert above


class PhaseTimings(unittest.TestCase):
    """The leg's own log has no clock, so these lines are the only clock."""

    def setUp(self):
        self._saved = list(leg_timing._PHASES)
        leg_timing._PHASES.clear()

    def tearDown(self):
        leg_timing._PHASES[:] = self._saved

    def test_phase_records_name_and_duration(self):
        with leg_timing.phase("harbor:step-1"):
            pass

        self.assertEqual(len(leg_timing._PHASES), 1)
        entry = leg_timing._PHASES[0]
        self.assertEqual(entry["phase"], "harbor:step-1")
        self.assertGreaterEqual(entry["seconds"], 0.0)
        self.assertGreaterEqual(entry["end_s"], entry["start_s"])

    def test_phase_records_even_when_the_body_raises(self):
        # A leg that dies mid-Harbor is exactly the one worth timing.
        with self.assertRaises(RuntimeError):
            with leg_timing.phase("harbor:step-1"):
                raise RuntimeError("harbor died")

        self.assertEqual([e["phase"] for e in leg_timing._PHASES], ["harbor:step-1"])

    def test_phase_restores_the_previous_phase_for_the_heartbeat(self):
        with leg_timing.phase("outer"):
            with leg_timing.phase("inner"):
                self.assertEqual(leg_timing._CURRENT_PHASE, "inner")
            self.assertEqual(leg_timing._CURRENT_PHASE, "outer")

    def test_write_phase_timings_lands_next_to_the_results(self):
        leg_timing.record_phase("lock-wait", 0.0, 7412.0)
        leg_timing.record_phase("harbor:step-1", 7412.0, 8192.0)

        with tempfile.TemporaryDirectory() as tmp:
            results_root = Path(tmp) / "results" / "slug" / "12345"
            leg_timing.write_phase_timings(results_root)
            payload = json.loads(
                (results_root / leg_timing.PHASE_TIMINGS_NAME).read_text(encoding="utf-8")
            )

        self.assertEqual(
            [(e["phase"], e["seconds"]) for e in payload["phases"]],
            [("lock-wait", 7412.0), ("harbor:step-1", 780.0)],
        )

    def test_write_phase_timings_is_a_no_op_without_phases(self):
        with tempfile.TemporaryDirectory() as tmp:
            results_root = Path(tmp) / "results"
            leg_timing.write_phase_timings(results_root)
            self.assertFalse(results_root.exists())

    def test_write_phase_timings_never_fails_the_leg(self):
        # Best effort by design: a read-only results root must not turn a
        # green leg red over bookkeeping.
        leg_timing.record_phase("lock-wait", 0.0, 1.0)
        with mock.patch.object(
            Path, "mkdir", side_effect=OSError("read-only file system")
        ):
            leg_timing.write_phase_timings(Path("/nonexistent/results"))

    def test_heartbeat_stops_and_does_not_outlive_the_leg(self):
        with mock.patch.object(leg_timing, "HEARTBEAT_SEC", 0.01):
            thread, stop = leg_timing.start_heartbeat()
            self.assertTrue(thread.daemon)
            stop.set()
            thread.join(timeout=5)
        self.assertFalse(thread.is_alive())

    def _ticks(self, seconds: float) -> list[str]:
        emitted: list[str] = []
        with mock.patch.object(leg_timing, "HEARTBEAT_SEC", 0.01), \
                mock.patch.object(leg_timing, "leg_log", emitted.append):
            thread, stop = leg_timing.start_heartbeat()
            time.sleep(seconds)
            stop.set()
            thread.join(timeout=5)
        return emitted

    def test_the_first_tick_does_not_wait_a_whole_interval(self):
        """A leg that dies inside one HEARTBEAT_SEC produced no timing line at
        all, so the shortest legs -- the ones that failed fast -- were exactly
        the ones with nothing to read."""
        with mock.patch.object(leg_timing, "HEARTBEAT_SEC", 3600), \
                mock.patch.object(leg_timing, "leg_log", (emitted := []).append):
            thread, stop = leg_timing.start_heartbeat()
            time.sleep(0.2)
            stop.set()
            thread.join(timeout=5)
        self.assertTrue(emitted, "no heartbeat before the first full interval")

    def test_the_label_is_restored_before_the_end_line_is_logged(self):
        """The begin side flips the label after its line, so the end side must
        restore before its own. Logging first left a window where a tick could
        claim the leg was still in a phase the log had already closed."""
        outer = leg_timing._CURRENT_PHASE
        at_log: list[tuple[str, str]] = []
        with mock.patch.object(leg_timing, "leg_log",
            lambda m: at_log.append((m, leg_timing._CURRENT_PHASE)),
        ):
            with leg_timing.phase("harbor:step-1"):
                pass
        labels_at_end = [p for m, p in at_log if "phase end" in m]
        self.assertEqual(
            labels_at_end, [outer],
            "the end line was logged while the label still named the closed phase",
        )

    def test_every_tick_names_the_phase_the_leg_is_actually_in(self):
        leg_timing.set_phase("startup")
        try:
            leg_timing.set_phase("lock-wait")
            ticks = self._ticks(0.1)
        finally:
            leg_timing.set_phase("startup")
        self.assertTrue(ticks)
        for line in ticks:
            self.assertIn("lock-wait", line)


if __name__ == "__main__":
    unittest.main(verbosity=2)
