#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import importlib.util
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

SCRIPT = Path(__file__).with_name("diagnose_sonarqube_failure.py")
SPEC = importlib.util.spec_from_file_location("diagnose_sonarqube_failure", SCRIPT)
assert SPEC and SPEC.loader
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


class SonarQubeFailureDiagnosticTest(unittest.TestCase):
    def result(self, returncode: int) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(["curl"], returncode, "", "")

    def test_no_route_is_classified_as_transient_infrastructure(self):
        level, message = module.classify(self.result(7))
        self.assertEqual(level, "error")
        self.assertIn("transient NVIDIA SonarQube infrastructure", message)
        self.assertIn("not a repository code finding", message)
        self.assertIn("gate red", message)

    def test_reachable_server_defers_to_scanner_result(self):
        level, message = module.classify(self.result(0))
        self.assertEqual(level, "notice")
        self.assertIn("repository-specific failure", message)
        self.assertNotIn("infrastructure error", message)

    def test_unknown_probe_failure_is_not_misclassified(self):
        level, message = module.classify(self.result(99))
        self.assertEqual(level, "warning")
        self.assertIn("unclassified", message)

    def test_probe_does_not_emit_or_raise_on_curl_failure(self):
        calls = []

        def runner(command, **kwargs):
            calls.append((command, kwargs))
            return self.result(7)

        result = module.probe("https://sonar.invalid/", runner=runner)
        self.assertEqual(result.returncode, 7)
        command, kwargs = calls[0]
        self.assertEqual(command[-1], "https://sonar.invalid/api/v2/analysis/version")
        self.assertFalse(kwargs["check"])

    def test_main_is_diagnostic_only_and_writes_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            summary = Path(tmp) / "summary.md"
            output = StringIO()
            with redirect_stdout(output):
                returncode = module.main(["--host-url", "", "--summary", str(summary)])
            self.assertEqual(returncode, 0)
            self.assertIn("could not be classified", output.getvalue())
            self.assertIn("could not be classified", summary.read_text())


if __name__ == "__main__":
    unittest.main(verbosity=2)
