#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Tests for publish_fern_docs.sh's production/preview interface.

Run directly (no pytest dependency):
``python3 .github/scripts/test_publish_fern_docs.py``.
A stub ``fern`` on PATH records its arguments, so nothing is published. Only
paths that publish on the first attempt are exercised, so no retry sleeps run.
"""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).with_name("publish_fern_docs.sh")
STUB = """#!/usr/bin/env bash
echo "$*" >> "$STUB_CALLS"
echo "Published docs to https://example.test/vss (took 1s)"
"""


class PublishInterfaceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = Path(tempfile.mkdtemp())
        bin_dir = self.dir / "bin"
        bin_dir.mkdir()
        (bin_dir / "fern").write_text(STUB, encoding="utf-8")
        (bin_dir / "fern").chmod(0o755)
        self.calls = self.dir / "calls"
        self.output = self.dir / "output"
        self.env = {
            **os.environ,
            "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
            "STUB_CALLS": str(self.calls),
            "GITHUB_OUTPUT": str(self.output),
            "GITHUB_STEP_SUMMARY": str(self.dir / "summary"),
        }

    def run_script(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [str(SCRIPT), *args],
            cwd=self.dir,
            env=self.env,
            text=True,
            capture_output=True,
            check=False,
        )

    def fern_calls(self) -> list[str]:
        return self.calls.read_text(encoding="utf-8").splitlines() if self.calls.exists() else []

    def assert_published(self, args: tuple[str, ...], expected_fern_args: str) -> None:
        result = self.run_script(*args)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.fern_calls(), [expected_fern_args])
        self.assertEqual(
            self.output.read_text(encoding="utf-8"), "url=https://example.test/vss\n"
        )

    def test_no_arguments_publishes_production(self) -> None:
        self.assert_published((), "generate --docs")

    def test_preview_without_id_lets_fern_generate_one(self) -> None:
        self.assert_published(("--preview",), "generate --docs --preview")

    def test_preview_with_id_updates_that_preview(self) -> None:
        self.assert_published(("--preview", "pr-12"), "generate --docs --preview --id pr-12")

    def test_malformed_arguments_never_invoke_fern(self) -> None:
        for args in (("--preview", ""), ("pr-12",), ("--preview", "a", "b"), ("--prod",)):
            with self.subTest(args=args):
                result = self.run_script(*args)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertEqual(self.fern_calls(), [])
                self.assertFalse(self.output.exists())


if __name__ == "__main__":
    unittest.main()
