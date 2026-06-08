#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Unit tests for review_findings — normalization + consolidation rules.

Run:
    python3 -m pytest .github/skills-review/tests/test_review_findings.py -v
Or directly:
    python3 .github/skills-review/tests/test_review_findings.py
"""
from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "review_findings", Path(__file__).resolve().parents[1] / "review_findings.py"
)
rf = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(rf)


def F(skill, paradigm, file, title, severity, **kw):
    raw = {"file": file, "title": title, "severity": severity, **kw}
    return rf.canonical_finding(raw, skill=skill, paradigm=paradigm)


class NormSeverity(unittest.TestCase):
    def test_words(self):
        for s in ("critical", "high", "medium", "low"):
            self.assertEqual(rf.norm_severity(s), s)

    def test_pcodes(self):
        self.assertEqual(rf.norm_severity("P0"), "critical")
        self.assertEqual(rf.norm_severity("P1"), "high")
        self.assertEqual(rf.norm_severity("P2"), "medium")
        self.assertEqual(rf.norm_severity("P3"), "low")

    def test_marker_brackets(self):
        self.assertEqual(rf.norm_severity("[P1]"), "high")
        self.assertEqual(rf.norm_severity("[P2]"), "medium")

    def test_unknown_defaults_medium(self):
        self.assertEqual(rf.norm_severity("weird"), "medium")
        self.assertEqual(rf.norm_severity(None), "medium")


class Canonical(unittest.TestCase):
    def test_passthrough_ce_json(self):
        raw = {"file": "skills/x/SKILL.md", "line": 10, "title": "Bug",
               "severity": "P0", "category": "security", "confidence": 0.9}
        f = rf.canonical_finding(raw, skill="x", paradigm="ce-code-review")
        self.assertEqual(f["severity"], "critical")
        self.assertEqual(f["category"], "security")
        self.assertEqual(f["line"], 10)
        self.assertEqual(f["confidence"], 0.9)

    def test_missing_title_or_file_dropped(self):
        self.assertIsNone(rf.canonical_finding(
            {"file": "x", "severity": "high"}, skill="x", paradigm="review"))
        self.assertIsNone(rf.canonical_finding(
            {"title": "t", "severity": "high"}, skill="x", paradigm="review"))

    def test_default_category_by_paradigm(self):
        f = rf.canonical_finding({"file": "f", "title": "t", "severity": "low"},
                                 skill="x", paradigm="best-practices")
        self.assertEqual(f["category"], "packaging-style")

    def test_bad_line_and_confidence_coerced(self):
        f = rf.canonical_finding(
            {"file": "f", "title": "t", "severity": "low", "line": "nope", "confidence": "x"},
            skill="x", paradigm="review")
        self.assertEqual(f["line"], 0)
        self.assertEqual(f["confidence"], 0.5)


class Consolidate(unittest.TestCase):
    def test_cross_paradigm_merge(self):
        # 4 paradigms raise the same defect with slightly different wording.
        findings = [
            F("a", "review", "skills/a/SKILL.md", "Container name vss-foo-alerts does not exist", "critical"),
            F("a", "codex", "skills/a/SKILL.md", "container vss-foo-alerts not found in compose", "high"),
            F("a", "ce-code-review", "skills/a/SKILL.md", "vss-foo-alerts container does not exist", "P0"),
            F("a", "gstack-review", "skills/a/SKILL.md", "nonexistent container name vss-foo-alerts", "P1"),
        ]
        out = rf.consolidate(findings)
        self.assertEqual(len(out), 1)
        c = out[0]
        self.assertEqual(c["agreement"], 4)              # distinct paradigms
        self.assertEqual(c["severity"], "critical")      # MAX wins
        self.assertEqual(c["confidence_tier"], "high")
        self.assertTrue(c["needs_verification"])         # critical
        self.assertEqual(len(c["members"]), 4)

    def test_agreement_counts_distinct_paradigms(self):
        # same paradigm twice is NOT corroboration
        findings = [
            F("a", "review", "skills/a/x.md", "stale tag 3.2.0-rc11 should be 3.2.0", "medium"),
            F("a", "review", "skills/a/x.md", "tag 3.2.0-rc11 is stale, use 3.2.0", "medium"),
        ]
        out = rf.consolidate(findings)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["agreement"], 1)
        self.assertTrue(out[0]["needs_verification"])    # agreement == 1

    def test_distinct_defects_not_merged(self):
        findings = [
            F("a", "review", "skills/a/SKILL.md", "wrong port 9000 should be 8000", "high"),
            F("a", "codex", "skills/a/SKILL.md", "ghost path deployments does not exist", "high"),
        ]
        out = rf.consolidate(findings)
        self.assertEqual(len(out), 2)

    def test_needs_verification_matrix(self):
        # lone critical -> verify; 3-paradigm medium -> no verify
        lone_crit = [F("a", "review", "skills/a/f", "lone crit", "critical")]
        self.assertTrue(rf.consolidate(lone_crit)[0]["needs_verification"])
        med3 = [
            F("a", "review", "skills/a/g", "agreed medium issue here", "medium"),
            F("a", "codex", "skills/a/g", "agreed medium issue here too", "medium"),
            F("a", "ce-code-review", "skills/a/g", "the agreed medium issue", "medium"),
        ]
        c = rf.consolidate(med3)[0]
        self.assertEqual(c["agreement"], 3)
        self.assertFalse(c["needs_verification"])

    def test_ranking(self):
        findings = [
            F("a", "review", "skills/a/lo", "low single", "low"),
            F("a", "review", "skills/a/hi", "high agreed issue", "high"),
            F("a", "codex", "skills/a/hi", "high agreed issue indeed", "high"),
            F("a", "review", "skills/a/cr", "critical thing", "critical"),
        ]
        out = rf.consolidate(findings)
        # critical first, then high (agreement 2), then low
        self.assertEqual([c["severity"] for c in out], ["critical", "high", "low"])

    def test_empty(self):
        self.assertEqual(rf.consolidate([]), [])

    def test_fuzzy_threshold_boundary(self):
        # unrelated titles on same (skill,file) stay separate
        findings = [
            F("a", "review", "skills/a/SKILL.md", "redis tag is stale", "low"),
            F("a", "codex", "skills/a/SKILL.md", "missing error handling section", "low"),
        ]
        self.assertEqual(len(rf.consolidate(findings)), 2)


class NormalizeList(unittest.TestCase):
    def test_drops_unusable(self):
        raws = [
            {"file": "f", "title": "ok", "severity": "high"},
            {"file": "f", "severity": "high"},          # no title -> dropped
            "not a dict",                                 # -> dropped
        ]
        out = rf.normalize("review", raws, skill="a")
        self.assertEqual(len(out), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
