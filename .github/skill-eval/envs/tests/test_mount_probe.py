#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Unit tests for the bind-mount probe verdict + parse logic (mount_probe.py).

No Brev box, no Docker — proves the classifier maps observed facts to the
right verdict (so we trust the MOUNTPROBE line the real run emits), and
that the emitted line round-trips through the parser. The live Docker
true-positive/true-negative check lives in prove_mount_probe.sh.

Run:
    python3 -m pytest .github/skill-eval/envs/tests/test_mount_probe.py -v
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from envs import mount_probe as mp  # noqa: E402


class ClassifyTest(unittest.TestCase):
    def test_healthy_when_inodes_match_and_writable(self):
        self.assertEqual(mp.classify_mount(
            container_exists=True, source_exists=True,
            host_inode="12345", container_inode="12345",
            container_links="4", writable=True), "healthy")

    def test_stale_on_inode_mismatch(self):
        # The exact bug: host dir deleted + recreated → new host inode, but
        # the container is still pinned to the old one.
        self.assertEqual(mp.classify_mount(
            container_exists=True, source_exists=True,
            host_inode="99999", container_inode="12345",
            container_links="4", writable=True), "stale")

    def test_stale_on_zero_link_count(self):
        # Container's inode was unlinked on the host (rm -rf) → links 0.
        self.assertEqual(mp.classify_mount(
            container_exists=True, source_exists=True,
            host_inode="12345", container_inode="12345",
            container_links="0", writable=True), "stale")

    def test_ro_mount_is_healthy_not_stale(self):
        # Read-only-by-design mounts (e.g. the RT-VLM's clip_storage) must NOT
        # be flagged stale just for writable=0 — the delete we hunt shows up as
        # links==0 / host-inode mismatch, which this row does not have.
        self.assertEqual(mp.classify_mount(
            container_exists=True, source_exists=True,
            host_inode="12345", container_inode="12345",
            container_links="4", writable=False), "healthy")

    def test_stale_when_container_inode_unreadable(self):
        self.assertEqual(mp.classify_mount(
            container_exists=True, source_exists=True,
            host_inode="12345", container_inode=None,
            container_links=None, writable=True), "stale")

    def test_absent_source(self):
        self.assertEqual(mp.classify_mount(
            container_exists=True, source_exists=False,
            host_inode=None, container_inode=None,
            container_links=None, writable=False), "absent-source")

    def test_no_container(self):
        self.assertEqual(mp.classify_mount(
            container_exists=False, source_exists=False,
            host_inode=None, container_inode=None,
            container_links=None, writable=False), "no-container")


class CommandAndParseTest(unittest.TestCase):
    def test_command_is_fail_loud_and_self_discovering(self):
        cmd = mp.build_probe_command("step-2:before")
        # Never exits non-zero; scans containers; always emits a trailing
        # scan=complete marker so 'no output' != 'no VIOS mounts found'.
        self.assertIn("MOUNTPROBE", cmd)
        self.assertIn("set +e", cmd)
        self.assertIn("docker ps -q", cmd)
        self.assertIn("scan=complete", cmd)
        self.assertIn("step-2:before", cmd)
        # Discovers by source pattern rather than a hardcoded container.
        self.assertIn("data_log/vst", cmd)
        self.assertIn("clip_storage", cmd)
        # Durable sink: brev_env logging is swallowed by harbor, so output
        # must be tee'd to a collected file (overridable for the self-test).
        self.assertIn("MOUNTPROBE_SINK", cmd)
        self.assertIn("/logs/artifacts/mount-probe.log", cmd)
        self.assertIn("tee -a", cmd)

    def test_parse_lines_returns_all_including_scan_marker(self):
        out = ("brev spinner noise\n"
               "MOUNTPROBE step-2:after verdict=healthy container=a source=/x "
               "dest=/d host_inode=5 container_inode=5 links=3 writable=1\n"
               "MOUNTPROBE step-2:after verdict=stale container=b source=/y "
               "dest=/e host_inode=9 container_inode=12 links=0 writable=1\n"
               "MOUNTPROBE step-2:after scan=complete\n"
               "trailing instance-name line\n")
        lines = mp.parse_probe_lines(out)
        self.assertEqual(len(lines), 3)
        self.assertEqual(lines[1]["verdict"], "stale")
        self.assertEqual(lines[1]["links"], "0")
        self.assertEqual(lines[2].get("scan"), "complete")

    def test_parse_line_ignores_scan_marker_takes_last_verdict(self):
        out = ("MOUNTPROBE s:before verdict=healthy container=a\n"
               "MOUNTPROBE s:after verdict=stale container=a\n"
               "MOUNTPROBE s:after scan=complete\n")
        self.assertEqual(mp.parse_probe_line(out)["verdict"], "stale")

    def test_parse_returns_empty_without_marker(self):
        self.assertEqual(mp.parse_probe_lines("no marker\njust noise"), [])
        self.assertIsNone(mp.parse_probe_line("no marker\njust noise"))


if __name__ == "__main__":
    unittest.main()
