#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Contract tests for review_agent — a leg ALWAYS writes a schema-valid artifact.

The LLM engines are stubbed (no SDK / no network), so these assert the
harness contract, not the model output.

Run:
    python3 .github/skills-review/tests/test_review_agent.py
"""
from __future__ import annotations

import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path

_DIR = Path(__file__).resolve().parents[1]
_SPEC = importlib.util.spec_from_file_location("review_agent", _DIR / "review_agent.py")
ra = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(ra)

# created under a temp REPO_ROOT in setUp — the contract test must not depend on
# which skills exist on the current branch (main's skills/ tree differs from develop's).
FAKE_SKILL = "vss-test-skill"


class ExtractFindings(unittest.TestCase):
    def test_findings_object(self):
        t = 'prose\n{"findings": [{"file": "f", "title": "t", "severity": "high"}]}'
        self.assertEqual(len(ra.extract_findings(t)), 1)

    def test_bare_array(self):
        t = '[{"file": "f", "title": "t", "severity": "low"}]'
        self.assertEqual(len(ra.extract_findings(t)), 1)

    def test_trailing_block_wins(self):
        t = '{"findings": [1,2,3]}\n... revised ...\n{"findings": [{"x": 1}]}'
        self.assertEqual(ra.extract_findings(t), [{"x": 1}])

    def test_prose_only(self):
        self.assertEqual(ra.extract_findings("no json here"), [])


class RunLegContract(unittest.TestCase):
    def setUp(self):
        self._orig = dict(ra.ENGINE_FN)
        # point REPO_ROOT at a temp tree with a fake skill so the contract test
        # doesn't depend on which skills exist on the current branch.
        self._orig_root = ra.REPO_ROOT
        self._tmp = tempfile.TemporaryDirectory()
        d = Path(self._tmp.name) / "skills" / FAKE_SKILL
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text("# fake skill\n")
        ra.REPO_ROOT = Path(self._tmp.name)

    def tearDown(self):
        ra.ENGINE_FN.clear()
        ra.ENGINE_FN.update(self._orig)
        ra.REPO_ROOT = self._orig_root
        self._tmp.cleanup()

    def _patch(self, fn):
        for k in ra.ENGINE_FN:
            ra.ENGINE_FN[k] = fn

    def test_ok(self):
        self._patch(lambda *a, **k: [
            {"file": "skills/x/SKILL.md", "line": 3, "title": "bug", "severity": "P0"}])
        art = ra.run_leg(FAKE_SKILL, "review")
        self.assertEqual(art["status"], "ok")
        self.assertEqual(len(art["findings"]), 1)
        self.assertEqual(art["findings"][0]["severity"], "critical")  # normalized

    def test_skipped(self):
        def boom(*a, **k):
            raise ra.SkippedLeg("codex not installed")
        self._patch(boom)
        art = ra.run_leg(FAKE_SKILL, "codex")
        self.assertEqual(art["status"], "skipped")
        self.assertEqual(art["findings"], [])

    def test_failed_on_exception(self):
        def boom(*a, **k):
            raise RuntimeError("sdk exploded")
        self._patch(boom)
        art = ra.run_leg(FAKE_SKILL, "best-practices")
        self.assertEqual(art["status"], "failed")
        self.assertEqual(art["findings"], [])

    def test_missing_skill_dir_skipped(self):
        self._patch(lambda *a, **k: [])
        art = ra.run_leg("vss-does-not-exist", "review")
        self.assertEqual(art["status"], "skipped")

    def test_main_writes_valid_artifact(self):
        self._patch(lambda *a, **k: [
            {"file": "skills/x/SKILL.md", "title": "t", "severity": "medium"}])
        with tempfile.TemporaryDirectory() as d:
            os.environ.update(EVAL_SKILL=FAKE_SKILL, EVAL_PARADIGM="review",
                              REVIEW_OUT_DIR=d, PR_BASE="origin/develop")
            try:
                rc = ra.main()
            finally:
                for k in ("EVAL_SKILL", "EVAL_PARADIGM", "REVIEW_OUT_DIR", "PR_BASE"):
                    os.environ.pop(k, None)
            self.assertEqual(rc, 0)
            art = json.loads((Path(d) / f"review-{FAKE_SKILL}__review.json").read_text())
            self.assertEqual(set(art) >= {"skill", "paradigm", "status", "findings"}, True)
            self.assertEqual(art["skill"], FAKE_SKILL)
            self.assertEqual(art["findings"][0]["paradigm"], "review")


if __name__ == "__main__":
    unittest.main(verbosity=2)
