#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Unit tests for plan_review_matrix — changed-skill x paradigm dispatch.

SKILLS_DIR is pointed at a temp fake skill tree so the assertions don't drift
as the real skills/ tree changes.

Run:
    python3 -m pytest .github/skills-review/tests/test_plan_review_matrix.py -v
Or directly:
    python3 .github/skills-review/tests/test_plan_review_matrix.py
"""
from __future__ import annotations

import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "plan_review_matrix", Path(__file__).resolve().parents[1] / "plan_review_matrix.py"
)
prm = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(prm)

N_PARADIGMS = len(prm.PARADIGMS)


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        skills = Path(self._tmp.name) / "skills"
        for name in ("vss-ask-video", "vss-manage-alerts", "vss-deploy-profile"):
            (skills / name).mkdir(parents=True)
        (skills / "README.md").write_text("not a skill dir\n")  # top-level file
        self._orig = prm.SKILLS_DIR
        prm.SKILLS_DIR = skills
        # isolate env across tests
        for k in ("CHANGED_FILES", "MANUAL_SKILLS_FILTER", "PR_BASE", "GITHUB_OUTPUT"):
            os.environ.pop(k, None)

    def tearDown(self):
        prm.SKILLS_DIR = self._orig
        self._tmp.cleanup()
        for k in ("CHANGED_FILES", "MANUAL_SKILLS_FILTER", "PR_BASE", "GITHUB_OUTPUT"):
            os.environ.pop(k, None)


class ChangedSkills(Base):
    def test_single_skill_file(self):
        self.assertEqual(prm.changed_skills(["skills/vss-ask-video/SKILL.md"]),
                         ["vss-ask-video"])

    def test_multi_skill_dedupe(self):
        files = [
            "skills/vss-ask-video/SKILL.md",
            "skills/vss-ask-video/references/x.md",   # same skill twice
            "skills/vss-manage-alerts/evals/a.json",
        ]
        self.assertEqual(prm.changed_skills(files),
                         ["vss-ask-video", "vss-manage-alerts"])

    def test_deleted_dir_excluded(self):
        # a file under a skill dir that no longer exists is skipped (live dirs only)
        self.assertEqual(prm.changed_skills(["skills/vss-gone/SKILL.md"]), [])

    def test_top_level_skills_file_ignored(self):
        # skills/README.md has no trailing-slash dir segment -> not a skill
        self.assertEqual(prm.changed_skills(["skills/README.md"]), [])

    def test_harness_only_diff_empty(self):
        self.assertEqual(
            prm.changed_skills([".github/skills-review/plan_review_matrix.py"]), [])


class BuildMatrix(Base):
    def test_cartesian(self):
        inc = prm.build_matrix(["vss-ask-video", "vss-manage-alerts"])
        self.assertEqual(len(inc), 2 * N_PARADIGMS)
        slugs = {leg["slug"] for leg in inc}
        self.assertIn("vss-ask-video__review", slugs)
        self.assertIn("vss-manage-alerts__best-practices", slugs)
        # every leg carries skill + paradigm + name
        for leg in inc:
            self.assertEqual(set(leg), {"skill", "paradigm", "slug", "name"})

    def test_empty(self):
        self.assertEqual(prm.build_matrix([]), [])


class ParseManual(Base):
    def test_star_all_dirs_only(self):
        # '*' enumerates dirs; the README.md file is excluded
        self.assertEqual(prm.parse_manual("*"),
                         ["vss-ask-video", "vss-deploy-profile", "vss-manage-alerts"])

    def test_csv(self):
        self.assertEqual(prm.parse_manual("vss-ask-video, vss-manage-alerts"),
                         ["vss-ask-video", "vss-manage-alerts"])

    def test_single(self):
        self.assertEqual(prm.parse_manual("vss-ask-video"), ["vss-ask-video"])

    def test_json_array(self):
        self.assertEqual(prm.parse_manual('["vss-ask-video","vss-deploy-profile"]'),
                         ["vss-ask-video", "vss-deploy-profile"])

    def test_path_escape_rejected(self):
        with self.assertRaises(ValueError):
            prm.parse_manual("../etc")

    def test_missing_dir_rejected(self):
        with self.assertRaises(ValueError):
            prm.parse_manual("vss-nonexistent")

    def test_bad_json_rejected(self):
        with self.assertRaises(ValueError):
            prm.parse_manual("[not valid json")


class Emit(Base):
    def _run_emit(self, include):
        out = Path(self._tmp.name) / "gh_output"
        os.environ["GITHUB_OUTPUT"] = str(out)
        prm.emit(include)
        return dict(
            line.split("=", 1) for line in out.read_text().splitlines() if "=" in line
        )

    def test_has_targets_and_matrix(self):
        kv = self._run_emit(prm.build_matrix(["vss-ask-video"]))
        self.assertEqual(kv["has_targets"], "true")
        matrix = json.loads(kv["matrix"])
        self.assertEqual(len(matrix["include"]), N_PARADIGMS)

    def test_empty_has_targets_false(self):
        kv = self._run_emit([])
        self.assertEqual(kv["has_targets"], "false")
        self.assertEqual(json.loads(kv["matrix"]), {"include": []})

    def test_duplicate_slug_raises(self):
        dup = [{"skill": "a", "paradigm": "review", "slug": "x", "name": "n"},
               {"skill": "b", "paradigm": "review", "slug": "x", "name": "n"}]
        with self.assertRaises(ValueError):
            prm.emit(dup)

    def test_unsafe_slug_raises(self):
        bad = [{"skill": "a", "paradigm": "p", "slug": "a/b", "name": "n"}]
        with self.assertRaises(ValueError):
            prm.emit(bad)


class ResolveSkills(Base):
    def test_changed_files_env(self):
        os.environ["CHANGED_FILES"] = "skills/vss-ask-video/SKILL.md\nskills/README.md"
        self.assertEqual(prm.resolve_skills(), ["vss-ask-video"])

    def test_manual_overrides_diff(self):
        os.environ["MANUAL_SKILLS_FILTER"] = "vss-manage-alerts"
        os.environ["CHANGED_FILES"] = "skills/vss-ask-video/SKILL.md"
        self.assertEqual(prm.resolve_skills(), ["vss-manage-alerts"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
