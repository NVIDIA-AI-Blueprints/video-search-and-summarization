#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Unit tests for plan_matrix.build_matrix — the diff -> dispatch rules.

The filesystem-touching helpers (specs_for_skill / adapter_exists /
spec_platform_config) are stubbed so the assertions don't drift as the real
skills/ tree gains or loses specs.

Run:
    python3 -m pytest .github/skill-eval/tests/test_plan_matrix.py -v
Or directly:
    python3 .github/skill-eval/tests/test_plan_matrix.py
"""
from __future__ import annotations

import importlib.util
import inspect
import os
import tempfile
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


class SkillFilePaths(unittest.TestCase):
    def test_returns_sorted_paths_starting_from_skills_dir(self):
        with tempfile.TemporaryDirectory() as td:
            skills_dir = Path(td) / "skills"
            alpha = skills_dir / "alpha" / "SKILL.md"
            beta = skills_dir / "beta" / "SKILL.md"
            nested = skills_dir / "gamma" / "nested" / "SKILL.md"
            noise = skills_dir / "delta" / "README.md"

            for p in (alpha, beta, nested, noise):
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text("test\n")

            self.assertEqual(
                plan_matrix.list_skill_file_paths(skills_dir),
                [
                    "skills/alpha/SKILL.md",
                    "skills/beta/SKILL.md",
                    "skills/gamma/nested/SKILL.md",
                ],
            )

    def test_returns_empty_list_when_skills_dir_is_missing(self):
        with tempfile.TemporaryDirectory() as td:
            self.assertEqual(
                plan_matrix.list_skill_file_paths(Path(td) / "missing-skills"),
                [],
            )


class RunsOnLabels(unittest.TestCase):
    """Spec hardware declaration -> runner label set.

    Parity target is run_leg.pool_candidates, which reads
    `int(metadata.get("gpu_count", 1) or 0)` and guards its GPU-type
    filter with `if required_count > 0 and required_type`.
    """

    def test_single_gpu_platform(self):
        self.assertEqual(
            plan_matrix.runs_on_labels("L40S", {"gpu_count": 1}),
            ["self-hosted", "vss-eval", "gpu-l40s", "gpus-1"],
        )

    def test_two_gpu_platform(self):
        self.assertEqual(
            plan_matrix.runs_on_labels("RTXPRO6000BW", {"gpu_count": 2}),
            ["self-hosted", "vss-eval", "gpu-rtxpro6000bw", "gpus-2"],
        )

    def test_absent_gpu_count_defaults_to_one(self):
        """pool_candidates' `metadata.get("gpu_count", 1)` default."""
        self.assertEqual(
            plan_matrix.runs_on_labels("L40S", {"modes": ["remote-all"]}),
            ["self-hosted", "vss-eval", "gpu-l40s", "gpus-1"],
        )
        self.assertEqual(
            plan_matrix.runs_on_labels("L40S", None),
            ["self-hosted", "vss-eval", "gpu-l40s", "gpus-1"],
        )

    def test_zero_gpu_count_drops_the_platform_label_too(self):
        """GPU-independent legs must not be pinned to a GPU box.

        pool_candidates only applies its type filter when
        `required_count > 0`, so a zero-GPU spec accepts any RUNNING box
        regardless of the platform it names. Emitting `gpu-rtxpro6000bw`
        here would be *more* restrictive than today's placement.
        """
        self.assertEqual(
            plan_matrix.runs_on_labels("RTXPRO6000BW", {"gpu_count": 0}),
            ["self-hosted", "vss-eval"],
        )
        self.assertEqual(
            plan_matrix.runs_on_labels("ANY", {"gpu_count": 0}),
            ["self-hosted", "vss-eval"],
        )

    def test_null_or_garbage_gpu_count_is_zero(self):
        """pool_candidates' trailing `or 0` on a present-but-falsy value."""
        for raw in (None, "", "two"):
            self.assertEqual(
                plan_matrix.runs_on_labels("L40S", {"gpu_count": raw}),
                ["self-hosted", "vss-eval"],
                raw,
            )

    def test_any_platform_carries_no_gpu_type_label(self):
        self.assertEqual(
            plan_matrix.runs_on_labels("ANY", {"gpu_count": 1}),
            ["self-hosted", "vss-eval", "gpus-1"],
        )

    def test_unknown_platform_falls_back_to_a_normalised_slug(self):
        self.assertEqual(
            plan_matrix.runs_on_labels("GB200 NVL", {"gpu_count": 1}),
            ["self-hosted", "vss-eval", "gpu-gb200-nvl", "gpus-1"],
        )

    def test_every_known_platform_has_a_label(self):
        for platform in ("H100", "L40S", "RTXPRO6000BW", "DGX-SPARK", "IGX-THOR"):
            labels = plan_matrix.runs_on_labels(platform, {"gpu_count": 1})
            self.assertEqual(len(labels), 4, platform)
            self.assertTrue(labels[2].startswith("gpu-"), platform)


class RealSpecCorpus(unittest.TestCase):
    """Every spec in skills/ must yield a well-formed label set.

    Unlike BuildMatrix this reads the real tree on purpose: the point is
    that no spec on disk produces a label a runner could never carry.
    """

    def setUp(self):
        self.specs = sorted(
            p for p in (plan_matrix.REPO_ROOT / "skills").glob("*/eval*/*.json")
            if p.name not in plan_matrix.EXCLUDED_SPEC_NAMES
        )
        if not self.specs:
            self.skipTest("no specs on disk")

    def test_every_platform_entry_yields_valid_labels(self):
        seen = 0
        for spec in self.specs:
            rel = str(spec.relative_to(plan_matrix.REPO_ROOT))
            for platform, config in plan_matrix.spec_platform_config(rel).items():
                labels = plan_matrix.runs_on_labels(platform, config)
                seen += 1
                # GitHub matches labels case-insensitively; keep the emitted
                # form strictly lowercase so box registration is unambiguous.
                for label in labels:
                    self.assertRegex(label, r"^[a-z0-9][a-z0-9-]*$", f"{rel} {label}")
                self.assertEqual(labels[:2], list(plan_matrix.BASE_LABELS), rel)
                self.assertLessEqual(
                    sum(1 for x in labels if x.startswith("gpu-")), 1, rel
                )
                self.assertLessEqual(
                    sum(1 for x in labels if x.startswith("gpus-")), 1, rel
                )
        self.assertGreater(seen, 0)

    def test_a_gpu_type_label_never_appears_without_a_count(self):
        """The zero-GPU parity rule, asserted across the real corpus."""
        for spec in self.specs:
            rel = str(spec.relative_to(plan_matrix.REPO_ROOT))
            for platform, config in plan_matrix.spec_platform_config(rel).items():
                labels = plan_matrix.runs_on_labels(platform, config)
                if any(x.startswith("gpu-") for x in labels):
                    self.assertTrue(
                        any(x.startswith("gpus-") for x in labels), f"{rel} {platform}"
                    )


class BuildMatrix(unittest.TestCase):
    def setUp(self):
        self._orig_specs = plan_matrix.specs_for_skill
        self._orig_adapter = plan_matrix.adapter_exists
        self._orig_platforms = plan_matrix.spec_platform_config
        self._orig_isfile = plan_matrix.Path.is_file

        plan_matrix.specs_for_skill = lambda s: FAKE_SPECS.get(s, [])
        plan_matrix.adapter_exists = lambda s: s in SKILLS_WITH_ADAPTERS
        # One platform per spec by default; overridden in the multi test.
        plan_matrix.spec_platform_config = lambda p: {"L40S": {"gpu_count": 1}}
        # All explicitly-changed spec paths in these tests "exist".
        plan_matrix.Path.is_file = lambda self: True  # type: ignore

    def tearDown(self):
        plan_matrix.specs_for_skill = self._orig_specs
        plan_matrix.adapter_exists = self._orig_adapter
        plan_matrix.spec_platform_config = self._orig_platforms
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

    def test_changed_evals_json_falls_through_to_whole_skill(self):
        # `evals.json` (plural) is a legacy aggregate index, not a spec, so a
        # changed evals.json must not dispatch as its own leg. It falls through
        # to whole-skill scope like any other non-spec file under the skill.
        inc = plan_matrix.build_matrix(
            ["skills/vss-summarize-video/evals/evals.json"]
        )
        self.assertEqual(self._stems(inc), ["a", "b"])
        self.assertNotIn("evals", self._stems(inc))

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
        # Commits an adapter, runs no trial — must not claim a GPU.
        self.assertEqual(inc[0]["runs_on"], ["self-hosted", "vss-eval"])

    def test_every_leg_carries_runs_on(self):
        inc = plan_matrix.build_matrix([
            "skills/vss-summarize-video/SKILL.md",
            "skills/vss-no-adapter/SKILL.md",
        ])
        self.assertTrue(inc)
        for leg in inc:
            self.assertIn("runs_on", leg)
            self.assertEqual(leg["runs_on"][:2], ["self-hosted", "vss-eval"])

    def test_runs_on_tracks_the_spec_declaration(self):
        plan_matrix.spec_platform_config = lambda p: {
            "L40S": {"gpu_count": 1},
            "RTXPRO6000BW": {"gpu_count": 2},
        }
        inc = plan_matrix.build_matrix(["skills/vss-search-archive/evals/search.json"])
        self.assertEqual(
            {leg["platform"]: leg["runs_on"] for leg in inc},
            {
                "L40S": ["self-hosted", "vss-eval", "gpu-l40s", "gpus-1"],
                "RTXPRO6000BW": [
                    "self-hosted", "vss-eval", "gpu-rtxpro6000bw", "gpus-2",
                ],
            },
        )

    def test_slug_carries_platform(self):
        inc = plan_matrix.build_matrix(["skills/vss-search-archive/evals/search.json"])
        self.assertEqual(len(inc), 1)
        self.assertEqual(inc[0]["platform"], "L40S")
        self.assertEqual(inc[0]["slug"], "vss-search-archive__search__L40S")

    def test_multi_platform_spec_fans_into_one_leg_per_platform(self):
        plan_matrix.spec_platform_config = lambda p: {
            "L40S": {"gpu_count": 1},
            "RTXPRO6000BW": {"gpu_count": 2},
        }
        inc = plan_matrix.build_matrix(["skills/vss-search-archive/evals/search.json"])
        self.assertEqual(
            sorted(leg["slug"] for leg in inc),
            ["vss-search-archive__search__L40S",
             "vss-search-archive__search__RTXPRO6000BW"],
        )

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

    def test_large_changeset_not_truncated(self):
        # Guards the >300-file path at the planner level: build_matrix must
        # process the entire changed-file list. The GitHub compare API caps
        # its .files array at 300; plan_matrix now diffs locally
        # (see list_changed_files), and build_matrix itself has no cap.
        changed = [f"skills/vss-summarize-video/evals/s{i}.json" for i in range(400)]
        inc = plan_matrix.build_matrix(changed)
        self.assertEqual(len(inc), 400)
        self.assertTrue(all(leg["kind"] == "eval" for leg in inc))


class SpecsForSkill(unittest.TestCase):
    """specs_for_skill globs the real tree, so it's tested against a temp
    skills/ dir (not the stubbed FAKE_SPECS above)."""

    def test_skips_aggregate_evals_json_index(self):
        import tempfile

        orig_root = plan_matrix.REPO_ROOT
        with tempfile.TemporaryDirectory() as td:
            d = Path(td) / "skills" / "foo" / "evals"
            d.mkdir(parents=True)
            (d / "deploy.json").write_text("{}")        # real spec object
            (d / "evals.json").write_text("[]")          # legacy array index
            plan_matrix.REPO_ROOT = Path(td)
            try:
                specs = plan_matrix.specs_for_skill("foo")
            finally:
                plan_matrix.REPO_ROOT = orig_root
        stems = sorted(s[2] for s in specs)
        self.assertEqual(stems, ["deploy"])
        self.assertNotIn("evals", stems)


class ListChangedFiles(unittest.TestCase):
    def test_uses_local_git_diff_not_compare_api(self):
        """Guards the >300-file fix: changed files come from a local
        `git diff FETCH_HEAD...HEAD`, never the GitHub compare API (whose
        `.files` array caps at 300 and would silently drop changed skills
        on large PRs)."""
        calls: list[list[str]] = []

        class _R:
            stdout = "skills/vss-x/evals/y.json\n"

        def fake_run(cmd, *a, **k):
            calls.append(list(cmd))
            return _R()

        orig_run = plan_matrix.subprocess.run
        orig_changed = os.environ.pop("CHANGED_FILES", None)
        os.environ["PR_BASE"] = "develop"
        plan_matrix.subprocess.run = fake_run  # type: ignore[assignment]
        try:
            files = plan_matrix.list_changed_files()
        finally:
            plan_matrix.subprocess.run = orig_run  # type: ignore[assignment]
            if orig_changed is not None:
                os.environ["CHANGED_FILES"] = orig_changed

        self.assertEqual(files, ["skills/vss-x/evals/y.json"])
        flat = " ".join(" ".join(c) for c in calls)
        self.assertIn("git", flat)
        self.assertIn("diff", flat)
        self.assertIn("FETCH_HEAD...HEAD", flat)
        self.assertNotIn("compare", flat)

    def test_manual_filter_enumerates_specs_without_git(self):
        """Manual sweep (MANUAL_SKILLS_FILTER set) enumerates the chosen
        skill's specs instead of diffing, so build_matrix fans them per
        (spec, platform) like a push — and no git diff is run."""
        calls: list[list[str]] = []

        def fake_run(cmd, *a, **k):
            calls.append(list(cmd))

            class _R:
                stdout = ""

            return _R()

        orig_run = plan_matrix.subprocess.run
        orig_specs = plan_matrix.specs_for_skill
        orig_changed = os.environ.pop("CHANGED_FILES", None)
        plan_matrix.subprocess.run = fake_run  # type: ignore[assignment]
        # Use a real skill dir so the existence guard passes; specs_for_skill
        # is stubbed so the assertion stays stable as the tree changes.
        plan_matrix.specs_for_skill = lambda s: (
            [("skills/vss-manage-alerts/evals/a.json", "evals", "a"),
             ("skills/vss-manage-alerts/evals/b.json", "evals", "b")]
            if s == "vss-manage-alerts" else []
        )
        os.environ["MANUAL_SKILLS_FILTER"] = "vss-manage-alerts"
        try:
            files = plan_matrix.list_changed_files()
        finally:
            plan_matrix.subprocess.run = orig_run  # type: ignore[assignment]
            plan_matrix.specs_for_skill = orig_specs
            os.environ.pop("MANUAL_SKILLS_FILTER", None)
            if orig_changed is not None:
                os.environ["CHANGED_FILES"] = orig_changed

        self.assertEqual(files, ["skills/vss-manage-alerts/evals/a.json",
                                 "skills/vss-manage-alerts/evals/b.json"])
        self.assertEqual(calls, [])  # manual mode never invokes git

    def test_manual_filter_unknown_skill_raises(self):
        """A typo'd / non-existent skill filter fails the plan loudly instead
        of emitting a silent empty matrix the eval job skips."""
        orig_changed = os.environ.pop("CHANGED_FILES", None)
        os.environ["MANUAL_SKILLS_FILTER"] = "vss-this-skill-does-not-exist-xyz"
        try:
            with self.assertRaises(ValueError):
                plan_matrix.list_changed_files()
        finally:
            os.environ.pop("MANUAL_SKILLS_FILTER", None)
            if orig_changed is not None:
                os.environ["CHANGED_FILES"] = orig_changed


class EmitSlugSafety(unittest.TestCase):
    def test_emit_rejects_unsafe_slug(self):
        """A slug with chars outside [A-Za-z0-9_-] — which would corrupt the
        workflow artifact name or escape a scratch/results path — must fail
        the plan loudly, not slip through."""
        bad = [{
            "skill": "x", "spec_path": "skills/x/evals/a b.json",
            "spec_stem": "a b", "platform": "L40S", "kind": "eval",
            "slug": "x__a b__L40S", "name": "x · a b · L40S",
        }]
        with self.assertRaises(ValueError):
            plan_matrix.emit(bad)

    def test_emit_rejects_duplicate_slug(self):
        """Two specs resolving to the same slug (e.g. the same stem in both
        `evals/` and the legacy `eval/` of one skill) would clobber each
        other's results dir + artifact name — must fail the plan."""
        dup = [
            {"skill": "x", "spec_path": "skills/x/evals/foo.json",
             "spec_stem": "foo", "platform": "L40S", "kind": "eval",
             "slug": "x__foo__L40S", "name": "x · foo · L40S"},
            {"skill": "x", "spec_path": "skills/x/eval/foo.json",
             "spec_stem": "foo", "platform": "L40S", "kind": "eval",
             "slug": "x__foo__L40S", "name": "x · foo · L40S"},
        ]
        with self.assertRaises(ValueError):
            plan_matrix.emit(dup)

    def test_emit_accepts_safe_slug(self):
        ok = [{
            "skill": "x", "spec_path": "skills/x/evals/a.json",
            "spec_stem": "a", "platform": "L40S", "kind": "eval",
            "slug": "x__a__L40S", "name": "x · a · L40S",
        }]
        orig = os.environ.pop("GITHUB_OUTPUT", None)  # don't write a real output file
        try:
            plan_matrix.emit(ok)  # should not raise
        finally:
            if orig is not None:
                os.environ["GITHUB_OUTPUT"] = orig


class OpenshellRtxpro6000Only(unittest.TestCase):
    """OPENSHELL_RTXPRO6000_ONLY routes RTXPRO6000BW to the OpenShell cohort."""

    def setUp(self):
        self._orig_specs = plan_matrix.specs_for_skill
        self._orig_adapter = plan_matrix.adapter_exists
        self._orig_platforms = plan_matrix.spec_platform_config
        self._orig_isfile = plan_matrix.Path.is_file
        plan_matrix.specs_for_skill = lambda s: FAKE_SPECS.get(s, [])
        plan_matrix.adapter_exists = lambda s: s in SKILLS_WITH_ADAPTERS
        plan_matrix.spec_platform_config = lambda p: {"L40S": {"gpu_count": 1}}
        plan_matrix.Path.is_file = lambda self: True  # type: ignore
        os.environ["OPENSHELL_RTXPRO6000_ONLY"] = "1"

    def tearDown(self):
        plan_matrix.specs_for_skill = self._orig_specs
        plan_matrix.adapter_exists = self._orig_adapter
        plan_matrix.spec_platform_config = self._orig_platforms
        plan_matrix.Path.is_file = self._orig_isfile
        os.environ.pop("OPENSHELL_RTXPRO6000_ONLY", None)

    def test_zero_gpu_count_stays_on_openshell(self):
        self.assertEqual(
            plan_matrix.runs_on_labels("RTXPRO6000BW", {"gpu_count": 0}),
            [
                "vss-skill-eval-gpu",
                "openshell",
                "rtx-pro-6000",
                "gpu-rtxpro6000bw",
                "openshell-rtxpro6000-active",
                "gpus-1",
            ],
        )
        self.assertNotIn("ubuntu-24.04", plan_matrix.runs_on_labels(
            "RTXPRO6000BW", {"gpu_count": 0}
        ))

    def test_dedicated_labels_for_rtxpro6000(self):
        self.assertEqual(
            plan_matrix.runs_on_labels("RTXPRO6000BW", {"gpu_count": 1}),
            [
                "vss-skill-eval-gpu",
                "openshell",
                "rtx-pro-6000",
                "gpu-rtxpro6000bw",
                "openshell-rtxpro6000-active",
                "gpus-1",
            ],
        )

    def test_other_platforms_are_omitted(self):
        plan_matrix.spec_platform_config = lambda p: {
            "L40S": {"gpu_count": 1},
            "RTXPRO6000BW": {"gpu_count": 2},
        }
        inc = plan_matrix.build_matrix(
            ["skills/vss-search-archive/evals/search.json"]
        )
        self.assertEqual([leg["platform"] for leg in inc], ["RTXPRO6000BW"])
        self.assertEqual(
            inc[0]["runs_on"],
            [
                "vss-skill-eval-gpu",
                "openshell",
                "rtx-pro-6000",
                "gpu-rtxpro6000bw",
                "openshell-rtxpro6000-active",
                "gpus-2",
            ],
        )

    def test_harness_only_diff_emits_smoke_leg(self):
        # build_matrix() itself still has a smoke fallback when given a
        # harness-only file list. `/ok to test` goes through main(), which
        # enumerates every skill file when OPENSHELL_RTXPRO6000_ONLY is set.
        inc = plan_matrix.build_matrix(
            [".github/workflows/skills-eval.yml"]
        )
        self.assertEqual(len(inc), 1)
        self.assertEqual(inc[0]["skill"], "vss-deploy-profile")
        self.assertEqual(inc[0]["spec_stem"], "base")
        self.assertEqual(inc[0]["platform"], "RTXPRO6000BW")
        self.assertEqual(inc[0]["kind"], "eval")

    def test_openshell_main_enumerates_all_skills(self):
        src = inspect.getsource(plan_matrix.main)
        self.assertIn("OPENSHELL_RTXPRO6000_ONLY", src)
        self.assertIn("list_skill_file_paths", src)


if __name__ == "__main__":
    unittest.main(verbosity=2)
