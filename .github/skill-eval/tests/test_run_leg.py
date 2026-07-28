#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Unit tests for run_leg.py.

Run:
    python3 .github/skill-eval/tests/test_run_leg.py
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock


_SPEC = importlib.util.spec_from_file_location(
    "run_leg", Path(__file__).resolve().parents[1] / "run_leg.py"
)
run_leg = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = run_leg
_SPEC.loader.exec_module(run_leg)


class DiscoverInvocations(unittest.TestCase):
    def test_discover_single_step_invocation(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            task_dir = root / "alerts_cv" / "rtxpro6000bw"
            task_dir.mkdir(parents=True)
            (task_dir / "task.toml").write_text("step_count = 1\n")

            invocations = run_leg.discover_invocations(root)

        self.assertEqual(len(invocations), 1)
        self.assertEqual(invocations[0].harbor_root.name, "alerts_cv")
        self.assertEqual(invocations[0].include_task_name, "rtxpro6000bw")
        self.assertIsNone(invocations[0].step_index)

    def test_discover_multi_step_invocations(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            platform_dir = root / "foo" / "l40s"
            for step in (1, 2):
                step_dir = platform_dir / f"step-{step}"
                step_dir.mkdir(parents=True)
                (step_dir / "task.toml").write_text("step_count = 2\n")

            invocations = run_leg.discover_invocations(root)

        self.assertEqual(len(invocations), 2)
        self.assertEqual([i.include_task_name for i in invocations], ["step-1", "step-2"])
        self.assertTrue(all(i.harbor_root.name == "l40s" for i in invocations))
        self.assertEqual([i.step_index for i in invocations], [1, 2])
        self.assertEqual([i.step_count for i in invocations], [2, 2])

    def test_discover_multi_chain_invocations(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            for mode in ("remote-all", "standalone"):
                platform_dir = root / "spec" / f"l40s-{mode}"
                for step in (1, 2):
                    step_dir = platform_dir / f"step-{step}"
                    step_dir.mkdir(parents=True)
                    (step_dir / "task.toml").write_text("step_count = 2\n")

            invocations = run_leg.discover_invocations(root)

        self.assertEqual(len(invocations), 4)
        self.assertEqual(
            [(i.chain_key, i.include_task_name) for i in invocations],
            [
                ("spec_l40s-remote-all", "step-1"),
                ("spec_l40s-remote-all", "step-2"),
                ("spec_l40s-standalone", "step-1"),
                ("spec_l40s-standalone", "step-2"),
            ],
        )


class HarborCommand(unittest.TestCase):
    def test_build_command_uses_env_and_v1_suffix(self):
        invocation = run_leg.HarborInvocation(
            harbor_root=Path("/tmp/datasets/alerts_cv"),
            include_task_name="rtxpro6000bw",
            chain_key="alerts_cv_rtxpro6000bw",
        )

        cmd = run_leg.build_harbor_command(
            invocation,
            Path("/tmp/results"),
            "aws/anthropic/bedrock-claude-opus-4-6",
            "https://inference-api.nvidia.com/v1",
        )

        self.assertIn("--include-task-name", cmd)
        self.assertEqual(cmd[cmd.index("--include-task-name") + 1], "rtxpro6000bw")
        self.assertEqual(cmd[cmd.index("-a") + 1], "claude-code")
        self.assertEqual(cmd[cmd.index("--model") + 1], "aws/anthropic/bedrock-claude-opus-4-6")
        self.assertEqual(cmd[cmd.index("--ak") + 1], "api_base=https://inference-api.nvidia.com/v1")
        self.assertEqual(cmd[cmd.index("-o") + 1], "/tmp/results")

    def test_build_command_codex_agent(self):
        invocation = run_leg.HarborInvocation(
            harbor_root=Path("/tmp/datasets/alerts_cv"),
            include_task_name="rtxpro6000bw",
            chain_key="alerts_cv_rtxpro6000bw",
        )

        cmd = run_leg.build_harbor_command(
            invocation,
            Path("/tmp/results"),
            "openai/openai/gpt-5-codex",
            "https://inference-api.nvidia.com/v1",
            "codex",
        )

        # codex runs through the NvCodex subclass (keeps the full model id);
        # endpoint via --ak api_base, key from the env (not on the CLI).
        self.assertEqual(cmd[cmd.index("-a") + 1], "agents.nv_codex:NvCodex")
        self.assertEqual(cmd[cmd.index("--model") + 1], "openai/openai/gpt-5-codex")
        self.assertEqual(cmd[cmd.index("--ak") + 1], "api_base=https://inference-api.nvidia.com/v1")
        # The key must never be passed on the command line.
        self.assertFalse(any("OPENAI_API_KEY" in part for part in cmd))
        self.assertNotIn("CLAUDE_CODE_DISABLE_THINKING=1", cmd)

    def test_build_command_rejects_unknown_agent(self):
        invocation = run_leg.HarborInvocation(
            harbor_root=Path("/tmp/datasets/alerts_cv"),
            include_task_name="rtxpro6000bw",
            chain_key="alerts_cv_rtxpro6000bw",
        )
        with self.assertRaises(ValueError):
            run_leg.build_harbor_command(
                invocation, Path("/tmp/results"), "m", "https://x/v1", "Codex"
            )


class SkipMarkers(unittest.TestCase):
    def test_latest_reward_ignores_prior_chain_reward_when_since_is_set(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            reward = root / "2026-06-04" / "step-1__old" / "verifier" / "reward.txt"
            reward.parent.mkdir(parents=True)
            reward.write_text("1.0\n")
            since = time.time() + 10

            self.assertIsNone(run_leg.latest_reward(root, "step-1", started_at=since))

    def test_write_skip_markers(self):
        with tempfile.TemporaryDirectory() as td:
            scratch = Path(td)
            run_leg.write_skip_markers(
                scratch,
                spec_stem="vios_ops",
                platform="L40S",
                failed_step=2,
                reward="0.2",
                step_count=4,
            )

            step3 = scratch / "skipped-vios_ops-L40S-step-3.txt"
            step4 = scratch / "skipped-vios_ops-L40S-step-4.txt"
            self.assertTrue(step3.exists())
            self.assertTrue(step4.exists())
            self.assertEqual(
                step3.read_text().strip(),
                "skipped (prior-step fail, step=2 reward=0.2)",
            )


class TraceUrls(unittest.TestCase):
    """Regression cover for the blank-Harbor-page bug.

    PR #1254 / run 30284131217 shipped seven trace links whose final
    segment was the `--include-task-name` filter (`step-7`) instead of
    Harbor's `task_name`. The viewer is a client-side SPA, so every one
    of them opened as an empty page instead of erroring.
    """

    # Shape mirrors a real trial's result.json.
    RESULT = {
        "task_name": "nvidia-vss/vss-generate-video-report-base-l40s-step-7",
        "trial_name": "step-7__E6dBECL",
        "source": "l40s",
        "agent_info": {
            "name": "claude-code",
            "model_info": {
                "name": "anthropic/bedrock-claude-opus-4-6",
                "provider": "aws",
            },
        },
    }
    JOB = (
        "vss-generate-video-report__base_profile_report__L40S"
        "__30284131217__2026-07-27__17-16-47"
    )

    def setUp(self):
        self._orig_env = os.environ.get("BREV_ENV_ID")
        os.environ["BREV_ENV_ID"] = "13xh5gpe7"

    def tearDown(self):
        if self._orig_env is None:
            os.environ.pop("BREV_ENV_ID", None)
        else:
            os.environ["BREV_ENV_ID"] = self._orig_env

    def _write_result(self, directory: Path, payload=None) -> Path:
        directory.mkdir(parents=True, exist_ok=True)
        result = directory / "result.json"
        result.write_text(json.dumps(payload if payload is not None else self.RESULT))
        return result

    def test_trace_url_matches_the_viewer_route(self):
        with tempfile.TemporaryDirectory() as td:
            result = self._write_result(Path(td) / "step-7__E6dBECL")
            url = run_leg.trace_url(result, self.JOB)

        self.assertEqual(
            url,
            "https://harbor-13xh5gpe7.brevlab.com/jobs/"
            + self.JOB
            + "/tasks/l40s/claude-code/aws"
            + "/anthropic%2Fbedrock-claude-opus-4-6"
            + "/nvidia-vss%2Fvss-generate-video-report-base-l40s-step-7",
        )

    def test_trace_url_never_ends_in_the_include_task_filter(self):
        """The exact regression: a bare `step-7` tail renders a blank page."""
        with tempfile.TemporaryDirectory() as td:
            result = self._write_result(Path(td) / "step-7__E6dBECL")
            url = run_leg.trace_url(result, self.JOB)

        self.assertFalse(url.endswith("/step-7"))
        self.assertTrue(url.endswith("-step-7"))
        # Slashes inside <model>/<task> must be segments, not path levels.
        self.assertEqual(url.count("%2F"), 2)

    def test_trace_url_none_on_incomplete_result(self):
        with tempfile.TemporaryDirectory() as td:
            partial = dict(self.RESULT)
            partial.pop("task_name")
            result = self._write_result(Path(td) / "step-7__X", partial)

            self.assertIsNone(run_leg.trace_url(result, self.JOB))
            self.assertIsNone(run_leg.trace_url(Path(td) / "missing.json", self.JOB))

    def test_publish_trace_flattens_into_viewer_and_records_url(self):
        invocation = run_leg.HarborInvocation(
            harbor_root=Path("/tmp/datasets/base/l40s"),
            include_task_name="step-7",
            chain_key="base_l40s",
            step_index=7,
            step_count=8,
        )
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            results_root = root / "results" / "leg" / "30284131217"
            trial = results_root / "2026-07-27__17-16-47" / "step-7__E6dBECL"
            self._write_result(trial)
            viewer_root = root / "_viewer"
            orig_viewer = run_leg.VIEWER_ROOT
            run_leg.VIEWER_ROOT = viewer_root
            try:
                url = run_leg.publish_trace(
                    results_root, invocation, 0.0, "leg", "30284131217"
                )
            finally:
                run_leg.VIEWER_ROOT = orig_viewer

            job_dir = viewer_root / "leg__30284131217__2026-07-27__17-16-47"
            # Flattened: the trial sits at the job's top level, with no
            # intervening <date>/ level for the viewer to miss.
            self.assertTrue((job_dir / "step-7__E6dBECL" / "result.json").is_file())
            self.assertFalse((job_dir / "2026-07-27__17-16-47").exists())
            # Copy, not move — the workflow collector still tars results_root.
            self.assertTrue((trial / "result.json").is_file())

            row = (results_root / "trace-urls.tsv").read_text().strip().split("\t")

        self.assertEqual(row[0], "step-7")
        self.assertEqual(row[1], "step-7__E6dBECL")
        self.assertEqual(row[2], url)
        self.assertIn("/jobs/leg__30284131217__2026-07-27__17-16-47/tasks/", url)

    def test_publish_trace_returns_none_when_trial_produced_no_result(self):
        invocation = run_leg.HarborInvocation(
            harbor_root=Path("/tmp/datasets/base/l40s"),
            include_task_name="step-1",
            chain_key="base_l40s",
            step_index=1,
            step_count=8,
        )
        with tempfile.TemporaryDirectory() as td:
            results_root = Path(td) / "results"
            results_root.mkdir(parents=True)

            self.assertIsNone(
                run_leg.publish_trace(results_root, invocation, 0.0, "leg", "1")
            )
            self.assertFalse((results_root / "trace-urls.tsv").exists())


class PoolCandidates(unittest.TestCase):
    FLEET = [
        {"name": "vss-eval-rtx-1g-2", "status": "RUNNING",
         "gpu": "RTX PRO Server 6000", "instance_type": "g7e.4xlarge"},
        {"name": "vss-eval-rtx-1g-3", "status": "STOPPED",
         "gpu": "RTX PRO Server 6000", "instance_type": "g7e.4xlarge"},
        {"name": "vss-eval-rtx-2g-2", "status": "RUNNING",
         "gpu": "RTX PRO Server 6000", "instance_type": "g7e.12xlarge"},
        {"name": "vss-eval-l40s", "status": "RUNNING",
         "gpu": "L40S", "instance_type": "massedcompute_L40Sx2"},
        # gpu flake: catalog refresh returns "-" but instance_type carries it
        {"name": "vss-eval-l40s-2", "status": "RUNNING",
         "gpu": "-", "instance_type": "massedcompute_L40Sx2"},
        {"name": "vss-eval-rtx-2g-VM1b", "status": "RUNNING",
         "gpu": "RTX PRO 6000",
         "instance_type": "registered-external-node", "_registered": True},
        {"name": "not-a-pool-box", "status": "RUNNING",
         "gpu": "RTX PRO Server 6000", "instance_type": "g7e.4xlarge"},
    ]

    def setUp(self):
        self._orig = run_leg._list_pool_instances
        run_leg._list_pool_instances = (
            lambda _skill=None, _spec_stem=None: self.FLEET
        )

    def tearDown(self):
        run_leg._list_pool_instances = self._orig

    def test_filters_running_pool_and_gpu_type(self):
        names = run_leg.pool_candidates(
            {"gpu_type": "RTX PRO 6000", "gpu_count": 1})
        self.assertEqual(
            names,
            [
                "vss-eval-rtx-2g-VM1b",
                "vss-eval-rtx-1g-2",
                "vss-eval-rtx-2g-2",
            ],
        )

    def test_exact_count_hint_sorts_first(self):
        names = run_leg.pool_candidates(
            {"gpu_type": "RTX PRO 6000", "gpu_count": 2})
        self.assertEqual(names[0], "vss-eval-rtx-2g-VM1b")

    def test_gpu_flake_accepted_via_instance_type(self):
        names = run_leg.pool_candidates({"gpu_type": "L40S", "gpu_count": 1})
        self.assertEqual(names, ["vss-eval-l40s", "vss-eval-l40s-2"])

    def test_gpu_count_zero_accepts_any_running_pool_box(self):
        names = run_leg.pool_candidates({"gpu_count": 0})
        self.assertEqual(len(names), 5)
        self.assertNotIn("not-a-pool-box", names)
        self.assertNotIn("vss-eval-rtx-1g-3", names)

    def test_registered_gpu_hint_fails_closed_for_unknown_pool(self):
        self.assertEqual(
            run_leg._registered_gpu_hint("vss-eval-rtx-2g-VM1b"),
            "RTX PRO 6000",
        )
        self.assertEqual(
            run_leg._registered_gpu_hint(
                "vss-eval-geforce-rtx4090-vm1"
            ),
            "GEFORCE RTX 4090",
        )
        self.assertEqual(run_leg._registered_gpu_hint("vss-eval-mystery"), "")

    def test_pool_snapshot_merges_and_normalizes_registered_nodes(self):
        orig_managed = run_leg._list_brev_instances
        orig_registered = run_leg._list_registered_nodes
        try:
            run_leg._list_brev_instances = lambda: [
                {"name": "vss-eval-rtx-2g", "status": "RUNNING"}
            ]
            run_leg._list_registered_nodes = lambda: [
                {"name": "vss-eval-rtx-2g-VM1b", "status": "Connected"},
                # A duplicate must not be added twice.
                {"name": "vss-eval-rtx-2g", "status": "Connected"},
            ]

            with mock.patch.dict(
                run_leg.os.environ,
                {"BREV_REGISTERED_POOL": "vss-eval-rtx-2g-VM1b"},
            ):
                instances = self._orig()
        finally:
            run_leg._list_brev_instances = orig_managed
            run_leg._list_registered_nodes = orig_registered

        self.assertEqual(len(instances), 2)
        registered = instances[1]
        self.assertEqual(registered["status"], "RUNNING")
        self.assertEqual(registered["gpu"], "RTX PRO 6000")
        self.assertTrue(registered["_registered"])

    def test_registered_pool_requires_explicit_allowlist(self):
        orig_managed = run_leg._list_brev_instances
        orig_registered = run_leg._list_registered_nodes
        try:
            run_leg._list_brev_instances = lambda: []
            run_leg._list_registered_nodes = lambda: [
                {"name": "vss-eval-rtx-2g-VM1b", "status": "Connected"},
                {"name": "vss-eval-rtx-2g-skybridge", "status": "Connected"},
            ]
            with mock.patch.dict(
                run_leg.os.environ,
                {
                    "BREV_REGISTERED_POOL":
                        "vss-eval-rtx-2g-VM1b, vss-eval-rtx-2g-VM2b"
                },
            ):
                instances = self._orig()
        finally:
            run_leg._list_brev_instances = orig_managed
            run_leg._list_registered_nodes = orig_registered

        self.assertEqual(
            [instance["name"] for instance in instances],
            ["vss-eval-rtx-2g-VM1b"],
        )

    def test_4090_pool_is_limited_to_approved_skills(self):
        env = {
            "BREV_REGISTERED_POOL": "vss-eval-rtx-2g-VM1b",
            "BREV_RTX4090_POOL": (
                "vss-eval-geforce-rtx4090-vm1,"
                "vss-eval-geforce-rtx4090-vm2"
            ),
        }
        with mock.patch.dict(run_leg.os.environ, env, clear=True):
            approved = run_leg._registered_pool_allowlist(
                "vss-ask-video", "base_profile_video_understanding"
            )
            unapproved = run_leg._registered_pool_allowlist(
                "vss-deploy-profile", "search"
            )

        self.assertEqual(
            approved,
            {
                "vss-eval-rtx-2g-vm1b",
                "vss-eval-geforce-rtx4090-vm1",
                "vss-eval-geforce-rtx4090-vm2",
            },
        )
        self.assertEqual(unapproved, {"vss-eval-rtx-2g-vm1b"})

    def test_4090_test_capabilities_fail_closed(self):
        self.assertTrue(run_leg._rtx4090_supports(
            "vss-deploy-profile", "alerts_cv"
        ))
        self.assertTrue(run_leg._rtx4090_supports(
            "vss-manage-alerts", "subscriptions_lifecycle"
        ))
        self.assertFalse(run_leg._rtx4090_supports(
            "vss-deploy-profile", "search"
        ))
        self.assertFalse(run_leg._rtx4090_supports(
            "vss-deploy-profile", "warehouse"
        ))
        self.assertFalse(run_leg._rtx4090_supports(
            "vss-deploy-dense-captioning", "alerts_profile_api"
        ))
        self.assertFalse(run_leg._rtx4090_supports(
            "vss-deploy-detection-tracking-3d", "deploy"
        ))
        self.assertFalse(run_leg._rtx4090_supports("vss-ask-video", None))

    def test_4090_capability_route_bypasses_rtx_pro_type_only_for_skill(self):
        fleet = [{
            "name": "vss-eval-geforce-rtx4090-vm1",
            "status": "RUNNING",
            "gpu": "GEFORCE RTX 4090",
            "_registered": True,
            "_rtx4090_capability_routed": True,
        }]
        run_leg._list_pool_instances = (
            lambda _skill=None, _spec_stem=None: fleet
        )
        requirements = {"gpu_type": "RTX PRO 6000", "gpu_count": 1}

        approved = run_leg.pool_candidates({
            **requirements,
            "skill": "vss-ask-video",
        }, "base_profile_video_understanding")
        unapproved = run_leg.pool_candidates({
            **requirements,
            "skill": "vss-deploy-dense-captioning",
        }, "alerts_profile_api")

        self.assertEqual(approved, ["vss-eval-geforce-rtx4090-vm1"])
        self.assertEqual(unapproved, [])

    def test_underprovisioned_registered_node_is_filtered(self):
        fleet = [
            {"name": "vss-eval-geforce-rtx4090-vm1", "status": "RUNNING",
             "gpu": "GEFORCE RTX 4090", "_registered": True,
             "_rtx4090_capability_routed": True},
            {"name": "vss-eval-rtx-2g-VM1b", "status": "RUNNING",
             "gpu": "RTX PRO 6000", "_registered": True},
        ]
        run_leg._list_pool_instances = (
            lambda _skill=None, _spec_stem=None: fleet
        )

        names = run_leg.pool_candidates({
            "skill": "vss-ask-video",
            "gpu_type": "RTX PRO 6000",
            "gpu_count": 2,
        }, "base_profile_video_understanding")

        self.assertEqual(names, ["vss-eval-rtx-2g-VM1b"])


class HoldPoolLock(unittest.TestCase):
    def test_claims_first_free_candidate(self):
        import fcntl

        with tempfile.TemporaryDirectory() as tmp:
            lock_dir = Path(tmp)
            # Hold the preferred box's lock as if another leg owns it.
            held = (lock_dir / "box-a.lock").open("a+")
            fcntl.flock(held.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            try:
                with run_leg.hold_pool_lock(
                    lambda: ["box-a", "box-b"], lock_dir, timeout_sec=5
                ) as chosen:
                    self.assertEqual(chosen, "box-b")
            finally:
                held.close()

    def test_times_out_when_all_held(self):
        import fcntl

        with tempfile.TemporaryDirectory() as tmp:
            lock_dir = Path(tmp)
            held = (lock_dir / "box-a.lock").open("a+")
            fcntl.flock(held.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            try:
                start = time.monotonic()
                with self.assertRaises(run_leg.LockTimeoutError):
                    with run_leg.hold_pool_lock(
                        lambda: ["box-a"], lock_dir, timeout_sec=0
                    ):
                        pass
                self.assertLess(time.monotonic() - start, 5)
            finally:
                held.close()

    def test_lock_released_on_exit(self):
        import fcntl

        with tempfile.TemporaryDirectory() as tmp:
            lock_dir = Path(tmp)
            with run_leg.hold_pool_lock(lambda: ["box-a"], lock_dir, 5) as chosen:
                self.assertEqual(chosen, "box-a")
            probe = (lock_dir / "box-a.lock").open("a+")
            try:
                fcntl.flock(probe.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            finally:
                probe.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
