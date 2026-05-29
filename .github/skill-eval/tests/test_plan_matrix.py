#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Unit tests for plan_matrix.build_matrix — the diff -> dispatch rules.

The filesystem-touching helpers (specs_for_skill / adapter_exists /
spec_platforms) are stubbed so the assertions don't drift as the real
skills/ tree gains or loses specs.

Run:
    python3 -m pytest .github/skill-eval/tests/test_plan_matrix.py -v
Or directly:
    python3 .github/skill-eval/tests/test_plan_matrix.py
"""
from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "plan_matrix", Path(__file__).resolve().parents[1] / "plan_matrix.py"
)
plan_matrix = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(plan_matrix)

# A fake universe: skill -> list of (spec_path, eval_dir, stem).
FAKE_SPECS = {
    "vss-summarize-video": [
        ("skills/vss-summarize-video/evals/a.json", "evals", "a"),
        ("skills/vss-summarize-video/evals/b.json", "evals", "b"),
    ],
    "vss-search-archive": [
        ("skills/vss-search-archive/evals/search.json", "evals", "search"),
    ],
    "vss-no-adapter": [
        ("skills/vss-no-adapter/evals/only.json", "evals", "only"),
    ],
}
SKILLS_WITH_ADAPTERS = {"vss-summarize-video", "vss-search-archive"}


class BuildMatrix(unittest.TestCase):
    def setUp(self):
        self._orig_specs = plan_matrix.specs_for_skill
        self._orig_adapter = plan_matrix.adapter_exists
        self._orig_platforms = plan_matrix.spec_platforms
        self._orig_isfile = plan_matrix.Path.is_file

        plan_matrix.specs_for_skill = lambda s: FAKE_SPECS.get(s, [])
        plan_matrix.adapter_exists = lambda s: s in SKILLS_WITH_ADAPTERS
        plan_matrix.spec_platforms = lambda p: "L40S"
        # All explicitly-changed spec paths in these tests "exist".
        plan_matrix.Path.is_file = lambda self: True  # type: ignore

    def tearDown(self):
        plan_matrix.specs_for_skill = self._orig_specs
        plan_matrix.adapter_exists = self._orig_adapter
        plan_matrix.spec_platforms = self._orig_platforms
        plan_matrix.Path.is_file = self._orig_isfile

    def _stems(self, include):
        return sorted(leg["spec_stem"] for leg in include)

    def test_single_spec_change_dispatches_only_that_spec(self):
        inc = plan_matrix.build_matrix(["skills/vss-summarize-video/evals/a.json"])
        self.assertEqual(self._stems(inc), ["a"])
        self.assertEqual(inc[0]["kind"], "eval")

    def test_skill_nonspec_change_dispatches_all_specs(self):
        inc = plan_matrix.build_matrix(["skills/vss-summarize-video/SKILL.md"])
        self.assertEqual(self._stems(inc), ["a", "b"])

    def test_adapter_change_dispatches_all_specs(self):
        inc = plan_matrix.build_matrix(
            [".github/skill-eval/adapters/vss-summarize-video/generate.py"]
        )
        self.assertEqual(self._stems(inc), ["a", "b"])

    def test_spec_plus_skill_file_dedupes(self):
        inc = plan_matrix.build_matrix([
            "skills/vss-summarize-video/evals/a.json",
            "skills/vss-summarize-video/SKILL.md",
        ])
        self.assertEqual(self._stems(inc), ["a", "b"])  # a appears once

    def test_harness_only_change_is_empty(self):
        for f in (
            ".github/skill-eval/skills_eval_agent.py",
            ".github/skill-eval/AGENTS.md",
            ".github/skill-eval/verifiers/generic_judge.py",
            ".github/skill-eval/envs/brev_env.py",
            ".github/workflows/skills-eval.yml",
            "README.md",
        ):
            self.assertEqual(plan_matrix.build_matrix([f]), [], f)

    def test_missing_adapter_collapses_to_one_leg(self):
        inc = plan_matrix.build_matrix(["skills/vss-no-adapter/SKILL.md"])
        self.assertEqual(len(inc), 1)
        self.assertEqual(inc[0]["kind"], "missing_adapter")
        self.assertEqual(inc[0]["slug"], "vss-no-adapter__missing-adapter")

    def test_mixed_skills_sorted_and_scoped(self):
        inc = plan_matrix.build_matrix([
            "skills/vss-search-archive/evals/search.json",
            "skills/vss-summarize-video/SKILL.md",
            ".github/skill-eval/verifiers/generic_judge.py",  # noise
        ])
        self.assertEqual(self._stems(inc), ["a", "b", "search"])

    def test_every_leg_has_a_safe_slug(self):
        inc = plan_matrix.build_matrix(["skills/vss-summarize-video/SKILL.md"])
        for leg in inc:
            self.assertRegex(leg["slug"], r"^[A-Za-z0-9_-]+$")


if __name__ == "__main__":
    unittest.main(verbosity=2)
