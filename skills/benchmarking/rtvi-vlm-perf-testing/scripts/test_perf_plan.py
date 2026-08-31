######################################################################################################
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
######################################################################################################

import unittest

import container_guard
import perf_plan


def valid_plan():
    return {
        "schema_version": 1,
        "name": "capacity",
        "benchmark_command": ["python3", "benchmark.py"],
        "environment": {
            "code_commit": "abc",
            "container_digest": "sha256:123",
            "runtime_policy": "fresh_per_run",
            "clean_source_identity": "abc",
            "cache_policy": "empty",
            "model_revision": "model-a",
            "hardware": "H100",
            "precision": "bf16",
        },
        "paths": {"output": "out", "scratch": "scratch", "mutable_cache": "cache"},
        "workload": {
            "media": "media-hash",
            "prompt": "prompt-hash",
            "input_tokens": 2048,
            "output_tokens": 1,
            "load_unit": "independent_live_stream",
            "source_identity_policy": "unique_per_stream",
            "source_identity_count": 132,
            "session_reuse": False,
        },
        "measurement": {
            "claim": "capacity_ceiling",
            "warmup_runs": 1,
            "repetitions": 3,
            "metrics": ["p95_latency_ms"],
        },
        "capacity_envelope": {
            "claim": "capacity_ceiling",
            "boundary_policy": "highest_stable_and_first_unstable",
            "stability_window_seconds": 120,
            "success_criteria": {
                "min_success_rate": 1.0,
                "max_p95_latency_ms": 5000,
                "zero_cross_scenario_residue": True,
            },
            "admission_policy": {
                "mode": "enforced",
                "controller": "gpu-memory-guard",
                "limits_source": "inside-runtime-evidence",
            },
            "observation_sources": ["client", "server", "engine", "gpu", "cleanup"],
            "fatal_markers": ["EngineDeadError", "ResourceInUse"],
        },
        "scenarios": [
            {
                "name": "2k-osl1",
                "reference_maximum": 132,
                "offset": 3,
                "add_stream_count": 1,
                "binary_search_refinement": True,
            }
        ],
    }


class PerfPlanTests(unittest.TestCase):
    def test_resolves_plan_and_rejects_stale_runtime(self):
        resolved = perf_plan.resolve_plan(valid_plan())
        self.assertEqual(resolved["resolved_scenarios"][0]["initial_stream_count"], 129)

        stale = valid_plan()
        stale["environment"]["runtime_policy"] = "reuse_existing"
        with self.assertRaisesRegex(ValueError, "fresh_per_run"):
            perf_plan.resolve_plan(stale)

    def test_concurrency_plan_uses_only_concurrency_flags_and_checks_sources(self):
        plan = valid_plan()
        plan["measurement"]["claim"] = "canary"
        del plan["capacity_envelope"]
        plan["workload"]["source_identity_count"] = 1
        plan["scenarios"] = [{"name": "concurrency", "concurrency_levels": [1]}]

        scenario = perf_plan.resolve_plan(plan)["resolved_scenarios"][0]
        self.assertEqual(
            scenario["argv"],
            [
                "python3",
                "benchmark.py",
                "--scenario",
                "concurrency",
                "--concurrency-levels",
                "1",
            ],
        )

        plan["scenarios"][0]["concurrency_levels"] = [2]
        with self.assertRaisesRegex(ValueError, "source_identity_count"):
            perf_plan.resolve_plan(plan)


class ContainerGuardTests(unittest.TestCase):
    def test_project_name_is_stable_and_collision_safe(self):
        first = container_guard.project_for_run("rtvi-canary.1")
        self.assertEqual(first, container_guard.project_for_run("rtvi-canary.1"))
        self.assertNotEqual(first, container_guard.project_for_run("rtvi_canary1"))
        self.assertLessEqual(len(first), 63)

    def test_only_matching_harness_ownership_is_removable(self):
        run_id = "rtvi-harness-canary-20260828T183951Z"
        project = container_guard.project_for_run(run_id)
        records = [
            {
                "Id": "owned-compose",
                "Name": f"/{project}-rtvi-server-1",
                "Config": {"Labels": {container_guard.COMPOSE_PROJECT_LABEL: project}},
            },
            {
                "Id": "owned-media",
                "Name": f"/{project}-mediamtx",
                "Config": {"Labels": {container_guard.RUN_ID_LABEL: run_id}},
            },
        ]
        self.assertEqual(
            container_guard.owned_container_ids(records, run_id, project, []),
            ["owned-compose", "owned-media"],
        )

        conflict = {
            "Id": "unowned",
            "Name": f"/{project}-publisher",
            "Config": {"Labels": {}},
        }
        with self.assertRaisesRegex(ValueError, "refusing unlabeled container"):
            container_guard.owned_container_ids(
                [conflict], run_id, project, [f"{project}-publisher"]
            )

        original = container_guard._find_records
        container_guard._find_records = lambda *_: [conflict]
        try:
            with self.assertRaisesRegex(ValueError, "refusing unlabeled container"):
                container_guard.find_owned_containers(
                    run_id, project, (name for name in [f"{project}-publisher"])
                )
        finally:
            container_guard._find_records = original


if __name__ == "__main__":
    unittest.main()
