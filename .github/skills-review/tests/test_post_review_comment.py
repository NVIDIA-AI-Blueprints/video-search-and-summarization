#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Tests for post_review_comment — marker prepend, upsert dispatch, fallbacks.

The GitHub HTTP layer is stubbed; these assert the advisory behavior.

Run:
    python3 .github/skills-review/tests/test_post_review_comment.py
"""
from __future__ import annotations

import importlib.util
import os
import tempfile
import unittest
from pathlib import Path

_DIR = Path(__file__).resolve().parents[1]
_SPEC = importlib.util.spec_from_file_location(
    "post_review_comment", _DIR / "post_review_comment.py")
prc = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(prc)


class MarkedBody(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        for k in ("COMMENT_BODY", "PR_NUMBER", "GITHUB_REPOSITORY",
                  "GITHUB_TOKEN", "GITHUB_STEP_SUMMARY"):
            os.environ.pop(k, None)

    def tearDown(self):
        self._tmp.cleanup()
        for k in ("COMMENT_BODY", "PR_NUMBER", "GITHUB_REPOSITORY",
                  "GITHUB_TOKEN", "GITHUB_STEP_SUMMARY"):
            os.environ.pop(k, None)

    def test_marker_prepended(self):
        p = Path(self._tmp.name) / "body.md"
        p.write_text("## report body")
        os.environ["COMMENT_BODY"] = str(p)
        body = prc.marked_body()
        self.assertTrue(body.startswith(prc.MARKER))
        self.assertIn("## report body", body)

    def test_missing_body_graceful(self):
        os.environ["COMMENT_BODY"] = str(Path(self._tmp.name) / "nope.md")
        body = prc.marked_body()
        self.assertIn(prc.MARKER, body)  # still has marker, no crash

    def test_manual_mode_writes_step_summary(self):
        body_p = Path(self._tmp.name) / "body.md"
        body_p.write_text("findings here")
        summ = Path(self._tmp.name) / "summary.md"
        os.environ.update(COMMENT_BODY=str(body_p), GITHUB_STEP_SUMMARY=str(summ))
        # no PR_NUMBER -> manual mode
        rc = prc.main()
        self.assertEqual(rc, 0)
        self.assertIn("findings here", summ.read_text())
        self.assertIn(prc.MARKER, summ.read_text())

    def test_pr_mode_upserts(self):
        body_p = Path(self._tmp.name) / "body.md"
        body_p.write_text("findings")
        os.environ.update(COMMENT_BODY=str(body_p), PR_NUMBER="42",
                          GITHUB_REPOSITORY="o/r", GITHUB_TOKEN="t")
        calls = {}
        orig = prc.upsert_comment
        prc.upsert_comment = lambda repo, pr, body: calls.update(repo=repo, pr=pr, body=body)
        try:
            rc = prc.main()
        finally:
            prc.upsert_comment = orig
        self.assertEqual(rc, 0)
        self.assertEqual(calls["pr"], "42")
        self.assertTrue(calls["body"].startswith(prc.MARKER))

    def test_pr_mode_missing_token_no_crash(self):
        body_p = Path(self._tmp.name) / "body.md"
        body_p.write_text("findings")
        os.environ.update(COMMENT_BODY=str(body_p), PR_NUMBER="42")  # no repo/token
        self.assertEqual(prc.main(), 0)  # advisory: never raises


if __name__ == "__main__":
    unittest.main(verbosity=2)
