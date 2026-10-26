#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Tests for preview_docs_href.py.

Run directly (no pytest dependency):
``python3 .github/scripts/test_preview_docs_href.py``.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
import unittest
from pathlib import Path

SCRIPT = Path(__file__).with_name("preview_docs_href.py")
SPEC = importlib.util.spec_from_file_location("preview_docs_href", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MOD = importlib.util.module_from_spec(SPEC)
sys.modules["preview_docs_href"] = MOD
SPEC.loader.exec_module(MOD)

PUBLISHED = "https://nvidia-preview-pr-2633.docs.buildwithfern.com/vss"


class PreviewDocsHrefTest(unittest.TestCase):
    def test_develop_pr_appends_dev(self) -> None:
        self.assertEqual(
            MOD.preview_docs_href(PUBLISHED),
            f"{PUBLISHED}/dev",
        )

    def test_strips_trailing_slash_before_dev(self) -> None:
        self.assertEqual(
            MOD.preview_docs_href(f"{PUBLISHED}/"),
            f"{PUBLISHED}/dev",
        )

    def test_release_pr_keeps_unversioned_url(self) -> None:
        self.assertEqual(MOD.preview_docs_href(PUBLISHED, "3.4.0"), PUBLISHED)

    def test_empty_release_is_dev(self) -> None:
        self.assertEqual(MOD.preview_docs_href(PUBLISHED, ""), f"{PUBLISHED}/dev")
        self.assertEqual(MOD.preview_docs_href(PUBLISHED, None), f"{PUBLISHED}/dev")

    def test_empty_url_raises(self) -> None:
        with self.assertRaises(ValueError):
            MOD.preview_docs_href("")
        with self.assertRaises(ValueError):
            MOD.preview_docs_href("/")

    def test_cli_develop(self) -> None:
        result = subprocess.run(
            [sys.executable, str(SCRIPT), PUBLISHED],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, f"{PUBLISHED}/dev\n")

    def test_cli_release(self) -> None:
        result = subprocess.run(
            [sys.executable, str(SCRIPT), PUBLISHED, "3.4.0"],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, f"{PUBLISHED}\n")

    def test_cli_rejects_bad_args(self) -> None:
        for args in ((), (PUBLISHED, "3.4.0", "extra"), ("",)):
            with self.subTest(args=args):
                result = subprocess.run(
                    [sys.executable, str(SCRIPT), *args],
                    text=True,
                    capture_output=True,
                    check=False,
                )
                self.assertEqual(result.returncode, 2, result.stderr)


if __name__ == "__main__":
    unittest.main()
