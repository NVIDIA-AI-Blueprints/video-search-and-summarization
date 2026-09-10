# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""The probe is the only evidence prebake produces, so it gets tested.

Prebake was enabled and then invisible for a day: the deploy runs on the box
and nothing published its output. If this probe silently emits nothing, that
state returns and looks identical to "prebake did not help".
"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import vios_probe  # noqa: E402


class BuildProbeCommand(unittest.TestCase):
    def test_writes_where_harbor_collects(self):
        """Anything not under /logs/artifacts never reaches the tarball."""
        self.assertIn("/logs/artifacts/vios-ready.log",
                      vios_probe.build_probe_command("step-1"))

    def test_sink_is_overridable_for_local_testing(self):
        self.assertIn("VIOSPROBE_SINK", vios_probe.build_probe_command("x"))

    def test_never_exits_non_zero(self):
        """A diagnostic that fails the trial it observes is worse than none."""
        cmd = vios_probe.build_probe_command("x")
        self.assertIn("set +e", cmd)
        self.assertTrue(cmd.rstrip().endswith("exit 0"))

    def test_label_is_quoted(self):
        self.assertIn("'weird label; rm -rf /'",
                      vios_probe.build_probe_command("weird label; rm -rf /"))

    def test_absent_container_is_stated_not_silent(self):
        """A profile without VIOS says nothing about prebake and must not read
        as a zero, but must still leave a trace that the probe ran."""
        cmd = vios_probe.build_probe_command("x")
        self.assertIn("container=absent", cmd)
        self.assertIn("scan=complete", cmd)

    def test_verdict_covers_the_unreadable_case(self):
        """Counting only 'skipping APT' would report an unreadable log as if
        the install had run. Both are counted and neither means unknown."""
        cmd = vios_probe.build_probe_command("x")
        for token in ("verdict=prebaked", "verdict=installed-at-start",
                      "verdict=unknown"):
            self.assertIn(token, cmd)


class ParseProbeLines(unittest.TestCase):
    def test_parses_a_full_line(self):
        got = vios_probe.parse_probe_lines(
            "VIOSPROBE step-1 container=present verdict=prebaked "
            "image_prebaked=yes health=healthy started=2026-08-29T00:00:00Z")
        self.assertEqual(got[0]["verdict"], "prebaked")
        self.assertEqual(got[0]["label"], "step-1")

    def test_ignores_surrounding_noise(self):
        got = vios_probe.parse_probe_lines(
            "docker: some warning\nVIOSPROBE a container=absent\nbye")
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["container"], "absent")

    def test_empty_input_is_empty_not_an_exception(self):
        self.assertEqual(vios_probe.parse_probe_lines(""), [])
        self.assertEqual(vios_probe.parse_probe_lines(None), [])
