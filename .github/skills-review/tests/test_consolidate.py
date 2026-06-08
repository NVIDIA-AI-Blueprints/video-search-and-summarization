#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Unit tests for consolidate — leg loading + report/comment composition.

Run:
    python3 -m pytest .github/skills-review/tests/test_consolidate.py -v
Or directly:
    python3 .github/skills-review/tests/test_consolidate.py
"""
from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

_DIR = Path(__file__).resolve().parents[1]
_SPEC = importlib.util.spec_from_file_location("consolidate", _DIR / "consolidate.py")
con = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(con)
rf = con.rf


def leg(skill, paradigm, findings):
    return {"skill": skill, "paradigm": paradigm, "status": "ok", "findings": findings}


class LoadLegs(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _write(self, slug, obj):
        (self.dir / f"review-{slug}.json").write_text(json.dumps(obj))

    def test_loads_and_normalizes(self):
        self._write("a__review", leg("a", "review", [
            {"file": "skills/a/SKILL.md", "line": 5, "title": "ghost path deployments", "severity": "critical"}]))
        self._write("a__codex", leg("a", "codex", [
            {"file": "skills/a/SKILL.md", "line": 5, "title": "deployments path is a ghost dir", "severity": "[P1]"}]))
        out = con.load_legs(self.dir)
        self.assertEqual(len(out), 2)
        # order follows filename sort, not insertion — assert the set
        self.assertEqual({f["severity"] for f in out}, {"critical", "high"})  # [P1] -> high
        self.assertEqual({f["paradigm"] for f in out}, {"review", "codex"})

    def test_skips_malformed(self):
        (self.dir / "review-bad.json").write_text("{not json")
        self._write("ok__review", leg("a", "review", [
            {"file": "f", "title": "t", "severity": "low"}]))
        self._write("nometa.json", {"findings": []})  # not review-*.json -> ignored anyway
        out = con.load_legs(self.dir)
        self.assertEqual(len(out), 1)

    def test_leg_missing_skill_skipped(self):
        (self.dir / "review-x.json").write_text(json.dumps({"paradigm": "review", "findings": []}))
        self.assertEqual(con.load_legs(self.dir), [])


class Reports(unittest.TestCase):
    def _consolidated(self):
        findings = []
        # critical, 2 paradigms agree (-> high confidence, still verify because critical)
        findings += rf.normalize("review", [
            {"file": "skills/a/SKILL.md", "line": 142, "title": "container vss-foo-alerts does not exist",
             "severity": "critical", "category": "deploy-breaking"}], skill="a")
        findings += rf.normalize("ce-code-review", [
            {"file": "skills/a/SKILL.md", "line": 142, "title": "vss-foo-alerts container missing",
             "severity": "P0", "category": "deploy-breaking"}], skill="a")
        # lone-lens critical (-> verify)
        findings += rf.normalize("codex", [
            {"file": "skills/b/SKILL.md", "line": 3, "title": "invented /v1/chat HITL flow",
             "severity": "critical", "category": "api-contract"}], skill="b")
        # corroborated medium (agreement 2, no verify)
        findings += rf.normalize("best-practices", [
            {"file": "skills/a/SKILL.md", "line": 1, "title": "missing error handling section",
             "severity": "medium", "category": "packaging-style"}], skill="a")
        findings += rf.normalize("ce-doc-review", [
            {"file": "skills/a/SKILL.md", "line": 1, "title": "no error handling section present",
             "severity": "medium", "category": "tone"}], skill="a")
        # low singleton (long tail)
        findings += rf.normalize("review", [
            {"file": "skills/a/x.md", "line": 9, "title": "redis tag stale", "severity": "low"}], skill="a")
        return rf.consolidate(findings)

    def test_consolidation_shape(self):
        c = self._consolidated()
        # 4 consolidated: container(crit,agree2), hitl(crit,agree1), error-handling(med,agree2), redis(low,agree1)
        self.assertEqual(len(c), 4)
        self.assertEqual(c[0]["severity"], "critical")     # ranked first
        container = next(x for x in c if "container" in x["title"])
        self.assertEqual(container["agreement"], 2)
        self.assertTrue(container["needs_verification"])   # critical
        eh = next(x for x in c if "error handling" in x["title"])
        self.assertEqual(eh["agreement"], 2)
        self.assertFalse(eh["needs_verification"])         # medium + agreement 2

    def test_comment_compact(self):
        import re
        body = con.build_comment(self._consolidated(), run_url="http://run")
        self.assertIn("6-paradigm consolidation", body)
        self.assertIn("Critical / high", body)
        self.assertIn("Verify before acting", body)
        self.assertIn("http://run", body)
        # the low singleton stays OUT of the compact comment (corroborated>=2 only)
        self.assertNotIn("redis tag stale", body)
        # headline "N need verification" must equal what the Verify list shows
        # (2 criticals here; the low single-lens redis is needs_verification too
        # but is not displayed, so it must not be counted).
        m = re.search(r"\*\*(\d+) need verification\*\*", body)
        self.assertIsNotNone(m)
        shown = body.split("Verify before acting")[1].count("\n- ")
        self.assertEqual(int(m.group(1)), shown)
        self.assertEqual(shown, 2)

    def test_markdown_buckets_and_verify(self):
        md = con.build_markdown(self._consolidated())
        self.assertIn("Deploy-breaking", md)
        self.assertIn("⚠️ VERIFY", md)                     # criticals flagged
        self.assertIn("redis tag stale", md)               # full report keeps the tail
        self.assertIn("Raised by 2 members", md)           # merged members listed

    def test_empty(self):
        self.assertIn("No findings", con.build_comment([]))
        self.assertEqual(json.loads(json.dumps([])), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
