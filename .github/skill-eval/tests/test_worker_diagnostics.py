#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Tests for bounded pre-cleanup CV worker diagnostics."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import worker_diagnostics  # noqa: E402


class CaptureWorkerDiagnostics(unittest.TestCase):
    def test_writes_run_scoped_report_without_printing_logs(self):
        payload = {"schema": 1, "container": "vss-rtvi-cv",
                   "logs": {"stderr_tail": "CUDA driver mismatch"}}
        result = subprocess.CompletedProcess(
            [], 0, "WORKER_DIAGNOSTICS=" + json.dumps(payload) + "\nbox-a\n", "",
        )
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(
            worker_diagnostics.subprocess, "run", return_value=result
        ) as execute:
            root = Path(tmp) / "results" / "42728769233"
            worker_diagnostics.capture_worker_diagnostics(
                "box-a", root, repo_root=Path(tmp),
                harbor_requirement="harbor==0.20.0", harbor_env=lambda _: {},
            )
            report = root / "worker-diagnostics" / "vss-rtvi-cv.json"
            self.assertEqual(json.loads(report.read_text()), payload)
            self.assertEqual(report.stat().st_mode & 0o777, 0o600)
            self.assertEqual(execute.call_args.args[0][-3], "box-a")
            self.assertIn("python3 -c", execute.call_args.args[0][-2])

    def test_rejects_transport_failure_without_echoing_stderr(self):
        result = subprocess.CompletedProcess([], 255, "", "SECRET_IN_SSH_STDERR")
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(
            worker_diagnostics.subprocess, "run", return_value=result
        ):
            root = Path(tmp) / "results"
            with self.assertRaisesRegex(RuntimeError, "transport exited 255") as caught:
                worker_diagnostics.capture_worker_diagnostics(
                    "box-a", root, repo_root=Path(tmp),
                    harbor_requirement="harbor==0.20.0", harbor_env=lambda _: {},
                )
            self.assertNotIn("SECRET_IN_SSH_STDERR", str(caught.exception))
            self.assertFalse(root.exists())

    def test_rejects_non_json_and_unknown_schema(self):
        for output in (
            "not-json", "WORKER_DIAGNOSTICS=not-json\nbox-a\n",
            "WORKER_DIAGNOSTICS=" + json.dumps({"schema": 2}) + "\nbox-a\n",
            "WORKER_DIAGNOSTICS={}\nWORKER_DIAGNOSTICS={}\n",
        ):
            with self.subTest(output=output), tempfile.TemporaryDirectory() as tmp:
                with mock.patch.object(
                    worker_diagnostics.subprocess, "run",
                    return_value=subprocess.CompletedProcess([], 0, output, ""),
                ):
                    with self.assertRaises(RuntimeError):
                        worker_diagnostics.capture_worker_diagnostics(
                            "box-a", Path(tmp) / "results", repo_root=Path(tmp),
                            harbor_requirement="harbor==0.20.0", harbor_env=lambda _: {},
                        )

    def test_remote_script_bounds_logs_and_whitelists_inspect_fields(self):
        commands = []

        def fake_run(args, capture_output, timeout):
            commands.append(args)
            if args[0] == "nvidia-smi":
                return subprocess.CompletedProcess(args, 0, b"L40S, 550.0\n", b"")
            if args[1] == "inspect":
                info = {"Config": {"Image": "cv:image", "Env": [
                    "CUDA_VERSION=12.9", "NVIDIA_API_KEY=do-not-retain",
                ]}, "State": {"ExitCode": 1, "OOMKilled": False},
                        "Image": "sha256:abc", "RestartCount": 0}
                return subprocess.CompletedProcess(args, 0, json.dumps([info]).encode(), b"")
            return subprocess.CompletedProcess(args, 0, b"", b"x" * 70000)

        with mock.patch.object(subprocess, "run", side_effect=fake_run), \
             mock.patch("builtins.print") as printed:
            exec(worker_diagnostics._REMOTE_SCRIPT, {"__name__": "__main__"})
        receipt = printed.call_args.args[0]
        self.assertTrue(receipt.startswith("WORKER_DIAGNOSTICS="))
        report = json.loads(receipt.removeprefix("WORKER_DIAGNOSTICS="))
        self.assertEqual(report["state"]["exit_code"], 1)
        self.assertEqual(report["state"]["cuda_metadata"], ["CUDA_VERSION=12.9"])
        self.assertNotIn("do-not-retain", printed.call_args.args[0])
        self.assertEqual(len(report["logs"]["stderr_tail"]), 65536)
        self.assertEqual(commands[-1][:4], ("docker", "logs", "--timestamps", "--tail"))


if __name__ == "__main__":
    unittest.main()
