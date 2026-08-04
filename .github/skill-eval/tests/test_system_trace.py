#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Tests for aggregate CPU and memory sampling."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import system_trace as mod  # noqa: E402


ROW = (
    "1785618000000000000,32,12.5,4.5,1.0,81.5,0.5,1.2,1.1,1.0,"
    "8192.0,57344.0,65536.0,0.0,0.0"
)


class Output(unittest.TestCase):
    def _remote(self, calls: list[str], hostile: bool = False):
        def run(_instance, command, **_kwargs):
            calls.append(command)
            if "echo PID=$!" in command:
                return "DIR=/tmp/vss-systrace.aB3xY9zQ\nPID=4242"
            return ("API_KEY=CANARYSECRET,1,2,3,4,5,6,7,8,9,10,11,12,13,14\n"
                    if hostile else "") + ROW
        return run

    def test_writes_only_validated_numeric_rows_and_sidecar(self):
        calls: list[str] = []
        with tempfile.TemporaryDirectory() as td, mock.patch.object(
            mod.gpu_trace, "_remote", side_effect=self._remote(calls, hostile=True)
        ):
            with mod.trace(
                "vss-eval-rtx-1g", Path(td), spec_stem="search",
                platform="RTX", skill="vss-search",
            ):
                pass
            csv_path = next((Path(td) / "systemtrace").glob("*.csv"))
            json_path = next((Path(td) / "systemtrace").glob("*.json"))
            body = csv_path.read_text()
            sidecar = json.loads(json_path.read_text())
        self.assertIn(mod.CSV_HEADER, body)
        self.assertIn(ROW, body)
        self.assertNotIn("CANARYSECRET", body)
        self.assertEqual(sidecar["samples"], 1)
        self.assertEqual(sidecar["skill"], "vss-search")

    def test_remote_sampler_reads_only_fixed_aggregate_interfaces(self):
        calls: list[str] = []
        with tempfile.TemporaryDirectory() as td, mock.patch.object(
            mod.gpu_trace, "_remote", side_effect=self._remote(calls)
        ):
            with mod.trace("box", Path(td)):
                pass
        start = calls[0]
        for expected in ("/proc/stat", "/proc/meminfo", "/proc/loadavg"):
            self.assertIn(expected, start)
        for forbidden in (
            "/proc/self", "/proc/[", "environ", "cmdline", "printenv",
            "docker inspect", "ps -e",
        ):
            self.assertNotIn(forbidden, start)
        self.assertIn("timeout -k 10", start)
        self.assertIn("mktemp -d /tmp/vss-systrace.", start)

    def test_cleanup_checks_pid_identity_and_removes_remote_directory(self):
        calls: list[str] = []
        with tempfile.TemporaryDirectory() as td, mock.patch.object(
            mod.gpu_trace, "_remote", side_effect=self._remote(calls)
        ):
            with mod.trace("box", Path(td)):
                pass
        finish = calls[-1]
        self.assertIn("ps -o args= -p 4242", finish)
        self.assertIn("grep -qF -- /tmp/vss-systrace.aB3xY9zQ", finish)
        self.assertRegex(finish, r"grep[^;]+&& kill 4242")
        self.assertIn("rm -rf /tmp/vss-systrace.aB3xY9zQ", finish)


class FailureIsolation(unittest.TestCase):
    def test_sampler_failure_never_prevents_the_body(self):
        ran = []
        with tempfile.TemporaryDirectory() as td, mock.patch.object(
            mod.gpu_trace, "_remote", side_effect=OSError("unreachable")
        ):
            with mod.trace("box", Path(td)):
                ran.append(True)
        self.assertEqual(ran, [True])

    def test_body_exception_still_propagates(self):
        replies = iter([
            "DIR=/tmp/vss-systrace.aB3xY9zQ\nPID=4242",
            ROW,
        ])
        with tempfile.TemporaryDirectory() as td, mock.patch.object(
            mod.gpu_trace, "_remote", side_effect=lambda *_a, **_k: next(replies)
        ):
            with self.assertRaisesRegex(ValueError, "leg failed"):
                with mod.trace("box", Path(td)):
                    raise ValueError("leg failed")


class Guards(unittest.TestCase):
    def test_row_shape_is_exact(self):
        self.assertTrue(mod.ROW_RE.match(ROW))
        self.assertIsNone(mod.ROW_RE.match(ROW + ",1"))
        self.assertIsNone(mod.ROW_RE.match("SECRET," + ROW))

    def test_remote_directory_shape_is_exact(self):
        self.assertTrue(mod.DIR_RE.match("/tmp/vss-systrace.aB3xY9zQ"))
        self.assertIsNone(
            mod.DIR_RE.match("/tmp/vss-systrace.aB3xY9zQ; rm -rf /")
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
