#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""OpenShell-only matrix planner tests."""
from __future__ import annotations
import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "openshell_plan_matrix",
    Path(__file__).resolve().parents[1] / "openshell" / "plan_matrix.py",
)
plan_matrix = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(plan_matrix)

FAKE_SPECS = {
    "vss-matrix-fixture": [
        ("skills/vss-matrix-fixture/evals/a.json", "evals", "a"),
        ("skills/vss-matrix-fixture/evals/b.json", "evals", "b"),
    ],
}
SKILLS_WITH_ADAPTERS = {"vss-matrix-fixture"}

class OpenshellGpuFleet(unittest.TestCase):
    """OPENSHELL_GPU_FLEET routes work by GPU demand."""

    def setUp(self):
        self._orig_specs = plan_matrix.specs_for_skill
        self._orig_adapter = plan_matrix.adapter_exists
        self._orig_platforms = plan_matrix.spec_platform_config
        self._orig_isfile = plan_matrix.Path.is_file
        plan_matrix.specs_for_skill = lambda s: FAKE_SPECS.get(s, [])
        plan_matrix.adapter_exists = lambda s: s in SKILLS_WITH_ADAPTERS
        plan_matrix.spec_platform_config = lambda p: {"L40S": {"gpu_count": 1}}
        plan_matrix.Path.is_file = lambda self: True  # type: ignore
        os.environ["OPENSHELL_GPU_FLEET"] = "1"

    def tearDown(self):
        plan_matrix.specs_for_skill = self._orig_specs
        plan_matrix.adapter_exists = self._orig_adapter
        plan_matrix.spec_platform_config = self._orig_platforms
        plan_matrix.Path.is_file = self._orig_isfile
        os.environ.pop("OPENSHELL_GPU_FLEET", None)

    @staticmethod
    def _requirements(
        *,
        gpu_count=1,
        min_vram=16,
        codec=False,
        multi_gpu=False,
        blackwell=False,
        profiles=("A16",),
    ):
        return {
            "gpu_count": gpu_count,
            "min_vram_gb_per_gpu": min_vram,
            "requires_video_codec": codec,
            "multi_gpu_capable": multi_gpu,
            "requires_blackwell": blackwell,
            "supported_hardware_profiles": list(profiles),
        }

    def test_a16_and_a40_labels_are_cohort_specific(self):
        a16 = plan_matrix.runs_on_labels("A16", {"gpu_count": 1})
        self.assertIn("openshell-a16-active", a16)
        self.assertIn(plan_matrix.OPENSHELL_RUNNER_LABEL, a16)
        self.assertIn("gpu-nvidia-a16", a16)
        self.assertIn("vram-15gb", a16)
        self.assertNotIn("vram-16gb", a16)
        self.assertIn("gpus-1", a16)
        self.assertEqual(
            plan_matrix.runs_on_labels("A16", {"gpu_count": 2}),
            list(plan_matrix.SKIP_RUNNER),
        )

        a40_1g = plan_matrix.runs_on_labels("A40", {"gpu_count": 1})
        a40_2g = plan_matrix.runs_on_labels("A40", {"gpu_count": 2})
        self.assertIn("openshell-a40-active", a40_1g)
        self.assertIn("gpu-nvidia-a40", a40_2g)
        self.assertIn("vram-46gb", a40_1g)
        self.assertIn("vram-46gb", a40_2g)
        self.assertNotIn("vram-48gb", a40_1g)
        self.assertNotIn("vram-48gb", a40_2g)
        self.assertIn("gpus-1", a40_1g)
        self.assertIn("gpus-2", a40_2g)

        h200 = plan_matrix.runs_on_labels("H200", {"gpu_count": 1})
        self.assertIn("openshell-h200-active", h200)
        self.assertIn(plan_matrix.OPENSHELL_RUNNER_LABEL, h200)
        self.assertIn("gpu-h200", h200)
        self.assertIn("gpu-nvidia-h200", h200)
        self.assertIn("gpus-1", h200)
        self.assertNotIn("gpu-rtxpro6000bw", h200)
        h200_2g = plan_matrix.runs_on_labels("H200", {"gpu_count": 2})
        self.assertIn("openshell-h200-active", h200_2g)
        self.assertIn("gpus-2", h200_2g)
        self.assertNotIn("gpus-1", h200_2g)
        self.assertEqual(
            plan_matrix.runs_on_labels("H200", {"gpu_count": 3}),
            list(plan_matrix.SKIP_RUNNER),
        )

    def test_capacity_accounting_matches_replacement_topology(self):
        self.assertEqual(
            {cohort.name: cohort.capacity for cohort in plan_matrix.OPENSHELL_COHORTS},
            {
                "a16-1g": 8,
                "a40-1g": 4,
                "a40-2g": 2,
                "h200-1g": 8,
                "h200-2g": 4,
                "rtxpro6000-2g": 4,
            },
        )
        self.assertEqual(
            sum(cohort.capacity for cohort in plan_matrix.OPENSHELL_COHORTS),
            30,
        )

    def test_openshell_job_labels_are_not_sku_specific(self):
        one = plan_matrix.openshell_job_labels(1)
        two = plan_matrix.openshell_job_labels(2)
        self.assertEqual(
            one,
            ["vss-skill-eval-gpu", "openshell-runner", "openshell", "gpus-1"],
        )
        self.assertEqual(
            two,
            ["vss-skill-eval-gpu", "openshell-runner", "openshell", "gpus-2"],
        )
        sku = {
            "h200", "a16", "a40", "rtx-pro-6000",
            "gpu-h200", "gpu-nvidia-h200", "gpu-rtxpro6000bw",
            "gpu-a16", "gpu-a40", "gpu-nvidia-a16", "gpu-nvidia-a40",
            "openshell-h200-active", "openshell-a16-active",
            "openshell-a40-active", "openshell-rtxpro6000-active",
            "vram-15gb", "vram-46gb", "video-codec",
        }
        self.assertFalse(sku & set(one + two))
        self.assertEqual(plan_matrix.openshell_job_labels(3), list(plan_matrix.SKIP_RUNNER))
        self.assertEqual(plan_matrix.openshell_placement_tag(1), "gpus-1")
        self.assertEqual(plan_matrix.openshell_placement_tag(2), "gpus-2")
        self.assertEqual(
            plan_matrix.openshell_placement_tag(
                1, {"requires_blackwell": True, "gpu_count": 1}
            ),
            "gpus-1",
        )
        for labels in (
            plan_matrix.OPENSHELL_A16_LABELS,
            plan_matrix.OPENSHELL_A40_LABELS,
            plan_matrix.OPENSHELL_H200_LABELS,
            plan_matrix.OPENSHELL_RTXPRO6000_LABELS,
        ):
            self.assertIn(plan_matrix.OPENSHELL_RUNNER_LABEL, labels)
        for cohort in plan_matrix.OPENSHELL_COHORTS:
            self.assertIn(plan_matrix.OPENSHELL_RUNNER_LABEL, cohort.labels)

    def test_only_gpu_count_is_consumed(self):
        """A SKU declaration cannot move, block, or size an OpenShell leg."""
        for requirements in (
            self._requirements(min_vram=15, codec=True),
            # Demands no OpenShell cohort satisfies, and a profile with no
            # checked-in `hw-*.env`: both used to block, and neither is
            # read any more.
            self._requirements(min_vram=100000, profiles=("A16",)),
            self._requirements(profiles=("GB200-NVL72",)),
        ):
            spec = dict(requirements)
            self.assertEqual(
                plan_matrix.openshell_placement_tag(spec["gpu_count"]),
                "gpus-1",
            )
            self.assertEqual(
                plan_matrix.openshell_job_labels(spec["gpu_count"]),
                ["vss-skill-eval-gpu", "openshell-runner", "openshell", "gpus-1"],
            )

    def test_metadata_gate_is_gpu_count_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            original_root = plan_matrix.REPO_ROOT
            plan_matrix.REPO_ROOT = root
            try:
                def write(name, payload):
                    (root / name).write_text(json.dumps(payload))
                    return name

                # A spec carrying nothing but its GPU demand is complete.
                bare = write("bare.json", {"openshell": {"gpu_count": 2}})
                requirements, error = plan_matrix.openshell_requirements(bare)
                self.assertIsNone(error)
                self.assertEqual(requirements["gpu_count"], 2)

                # A demand that no label set can express still fails closed.
                for bad in (0, 3, "1", True, None):
                    name = write("bad.json", {"openshell": {"gpu_count": bad}})
                    _, error = plan_matrix.openshell_requirements(name)
                    self.assertEqual(error, "openshell.gpu_count must be 1 or 2", bad)

                missing = write("missing.json", {"openshell": {}})
                _, error = plan_matrix.openshell_requirements(missing)
                self.assertIn("missing gpu_count", error)

                # Optional documentation fields are still type-checked, so a
                # typo is visible rather than silently inert.
                typo = write(
                    "typo.json",
                    {"openshell": {"gpu_count": 1, "requires_blackwell": "yes"}},
                )
                _, error = plan_matrix.openshell_requirements(typo)
                self.assertEqual(error, "openshell.requires_blackwell must be boolean")
            finally:
                plan_matrix.REPO_ROOT = original_root

    def test_all_current_specs_have_complete_fresh_metadata(self):
        skill_evals = (
            plan_matrix.REPO_ROOT / "skills" / "vss-deploy-test-openshell"
        )
        for spec in sorted(
            p
            for p in skill_evals.glob("eval*/*.json")
            if p.name not in plan_matrix.EXCLUDED_SPEC_NAMES
        ):
            relative = spec.relative_to(plan_matrix.REPO_ROOT).as_posix()
            requirements, error = plan_matrix.openshell_requirements(relative)
            self.assertIsNone(error, relative)
            self.assertIsNotNone(requirements, relative)
            self.assertIn(requirements["gpu_count"], (1, 2), relative)

    def test_future_matrix_uses_one_leg_per_spec_across_all_cohorts(self):
        current_specs = plan_matrix.specs_for_skill
        current_adapter = plan_matrix.adapter_exists
        current_platforms = plan_matrix.spec_platform_config
        plan_matrix.specs_for_skill = self._orig_specs
        plan_matrix.adapter_exists = self._orig_adapter
        plan_matrix.spec_platform_config = self._orig_platforms
        try:
            legs = plan_matrix.build_matrix(plan_matrix.list_skill_file_paths())
        finally:
            plan_matrix.specs_for_skill = current_specs
            plan_matrix.adapter_exists = current_adapter
            plan_matrix.spec_platform_config = current_platforms
        self.assertEqual(len(legs), 27)
        self.assertEqual(len({leg["spec_path"] for leg in legs}), 27)
        counts = {
            key: sum((leg.get("cohort") or "brev") == key for leg in legs)
            for key in {(leg.get("cohort") or "brev") for leg in legs}
        }
        self.assertEqual(counts, {"openshell": 27})
        self.assertEqual(sum(leg["local_gpu"] for leg in legs), 27)
        # Every OpenShell leg travels without a SKU: no platform for the
        # adapter to size from, and no hardware profile for the workflow to
        # export. The guest's own card decides both.
        openshell = [
            leg
            for leg in legs
            if leg.get("cohort") == plan_matrix.OPENSHELL_COHORT_TAG
        ]
        self.assertEqual(len(openshell), 27)
        for leg in openshell:
            self.assertEqual(leg["platform"], "", leg["slug"])
            self.assertEqual(leg["hardware_profile"], "", leg["slug"])
            self.assertIn(leg["gpu_count"], (1, 2), leg["slug"])
            requirements, error = plan_matrix.openshell_requirements(
                leg["spec_path"]
            )
            self.assertIsNone(error)
            self.assertEqual(
                leg["runs_on"],
                plan_matrix.openshell_job_labels(
                    leg["gpu_count"], requirements
                ),
                leg["slug"],
            )

    def test_vdr_quickstart_is_pinned_to_rtx_pro_blackwell(self):
        path = (
            "skills/vss-deploy-test-openshell/evals/"
            "vdr_1_quickstart_vision_agent.json"
        )
        current_adapter = plan_matrix.adapter_exists
        current_platforms = plan_matrix.spec_platform_config
        plan_matrix.adapter_exists = self._orig_adapter
        plan_matrix.spec_platform_config = self._orig_platforms
        try:
            legs = plan_matrix.build_matrix([path])
        finally:
            plan_matrix.adapter_exists = current_adapter
            plan_matrix.spec_platform_config = current_platforms
        self.assertEqual(len(legs), 1)
        leg = legs[0]
        self.assertEqual(leg["spec_stem"], "vdr_1_quickstart_vision_agent")
        self.assertEqual(
            leg["name"],
            "vss-deploy-test-openshell · vdr_1_quickstart_vision_agent · gpus-1",
        )
        self.assertEqual(
            leg["slug"],
            "vss-deploy-test-openshell__vdr_1_quickstart_vision_agent__gpus-1",
        )
        self.assertIn("gpu-rtxpro6000bw", leg["runs_on"])
        self.assertIn("openshell-rtxpro6000-active", leg["runs_on"])

    def test_codec_light_spec_explicitly_allows_a16(self):
        path = (
            "skills/operations/vss-manage-video-io-storage/evals/vios_ops.json"
        )
        if not (plan_matrix.REPO_ROOT / path).is_file():
            self.skipTest("vios eval spec not present")
        requirements, error = plan_matrix.openshell_requirements(path)
        if error:
            self.skipTest(error)
        self.assertTrue(requirements.get("requires_video_codec"))
        self.assertLessEqual(requirements.get("min_vram_gb_per_gpu", 0), 16)
        self.assertEqual(requirements.get("supported_hardware_profiles"), ["A16"])

    def test_hardware_profile_identity_is_never_substituted(self):
        for profile in ("A16", "A40", "H200", "RTXPRO6000BW"):
            self.assertEqual(plan_matrix.hardware_profile_for(profile), profile)

    def test_harness_only_diff_emits_count_only_smoke_leg(self):
        inc = plan_matrix.build_matrix([".github/workflows/skills-eval.yml"])
        self.assertEqual(len(inc), 1)
        self.assertEqual(inc[0]["skill"], "vss-deploy-test-openshell")
        self.assertEqual(inc[0]["spec_stem"], "base")
        self.assertEqual(inc[0]["gpu_count"], 1)
        self.assertEqual(inc[0]["slug"], "vss-deploy-test-openshell__base__gpus-1")
        self.assertEqual(inc[0]["name"], "vss-deploy-test-openshell · base · gpus-1")
        self.assertNotIn("rtxpro6000-2g", inc[0]["slug"])
        self.assertEqual(inc[0]["platform"], "")
        self.assertEqual(inc[0]["hardware_profile"], "")
        self.assertEqual(inc[0]["cohort"], plan_matrix.OPENSHELL_COHORT_TAG)
        self.assertEqual(
            inc[0]["runs_on"],
            plan_matrix.openshell_job_labels(inc[0]["gpu_count"]),
        )
        self.assertIn(plan_matrix.OPENSHELL_RUNNER_LABEL, inc[0]["runs_on"])
        self.assertNotIn("openshell-rtxpro6000-active", inc[0]["runs_on"])
        self.assertNotIn("gpu-rtxpro6000bw", inc[0]["runs_on"])
        self.assertNotIn("openshell-h200-active", inc[0]["runs_on"])
        self.assertNotIn("gpu-h200", inc[0]["runs_on"])
        self.assertEqual(inc[0]["kind"], "eval")

    def test_missing_metadata_is_visible_and_not_replaced_by_smoke(self):
        original = plan_matrix.openshell_requirements
        orig_adapter = plan_matrix.adapter_exists
        plan_matrix.openshell_requirements = lambda _path: (
            None,
            "missing openshell capability metadata",
        )
        plan_matrix.adapter_exists = lambda s: s == "vss-deploy-test-openshell"
        try:
            inc = plan_matrix.build_matrix(
                ["skills/vss-deploy-test-openshell/evals/base.json"]
            )
        finally:
            plan_matrix.openshell_requirements = original
            plan_matrix.adapter_exists = orig_adapter
        self.assertEqual(len(inc), 1)
        self.assertEqual(inc[0]["kind"], "not_run_infra_acquisition")
        self.assertEqual(inc[0]["skill"], "vss-deploy-test-openshell")
        self.assertIn("missing openshell capability metadata", inc[0]["skip_reason"])

    def test_other_skills_are_ignored_by_openshell_planner(self):
        orig_adapter = plan_matrix.adapter_exists
        plan_matrix.adapter_exists = lambda s: s == "vss-search-archive"
        try:
            inc = plan_matrix.build_matrix(
                ["skills/operations/vss-search-archive/evals/search.json"]
            )
        finally:
            plan_matrix.adapter_exists = orig_adapter
        self.assertEqual(inc, [])


    def test_openshell_pr_keeps_changed_file_scope(self):
        changed = ["skills/operations/vss-search-archive/SKILL.md"]
        seen: list[list[str]] = []
        orig_changed = plan_matrix.list_changed_files
        orig_all = plan_matrix.list_skill_file_paths
        orig_build = plan_matrix.build_matrix
        orig_emit = plan_matrix.emit
        orig_daily = os.environ.pop("DAILY_RUN", None)
        plan_matrix.list_changed_files = lambda: changed
        plan_matrix.list_skill_file_paths = lambda: self.fail(
            "PR route must not enumerate unrelated skills"
        )
        plan_matrix.build_matrix = lambda files: seen.append(files) or []
        plan_matrix.emit = lambda include: None
        try:
            self.assertEqual(plan_matrix.main(), 0)
        finally:
            plan_matrix.list_changed_files = orig_changed
            plan_matrix.list_skill_file_paths = orig_all
            plan_matrix.build_matrix = orig_build
            plan_matrix.emit = orig_emit
            if orig_daily is not None:
                os.environ["DAILY_RUN"] = orig_daily
        self.assertEqual(seen, [changed])

    def test_control_plane_is_split_inside_one_workflow(self):
        root = plan_matrix.REPO_ROOT
        workflow = (root / ".github/workflows/skills-eval.yml").read_text()
        self.assertIn("infrastructure:", workflow)
        self.assertIn("brev_plan:", workflow)
        self.assertIn("openshell_plan:", workflow)
        self.assertIn(".github/skill-eval/plan_matrix.py", workflow)
        self.assertIn(".github/skill-eval/openshell/plan_matrix.py", workflow)
        self.assertFalse(
            (root / ".github/workflows/skills-eval-openshell.yml").exists()
        )

    def test_openshell_runtime_does_not_import_brev_backend(self):
        root = plan_matrix.REPO_ROOT / ".github/skill-eval/openshell"
        for name in ("plan_matrix.py", "run_leg.py", "env.py"):
            text = (root / name).read_text()
            self.assertNotIn("from envs.brev_env import", text, name)
            self.assertNotIn("import envs.brev_env", text, name)


if __name__ == "__main__":
    unittest.main(verbosity=2)
