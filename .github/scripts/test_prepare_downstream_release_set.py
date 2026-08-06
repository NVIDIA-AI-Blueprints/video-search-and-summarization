#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import base64
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

import prepare_downstream_release_set as module  # noqa: E402
from prepare_downstream_release_set import (  # noqa: E402
    candidate_container_tag,
    downstream_build_type,
    downstream_relevant,
    downstream_variables,
    pr_merge_base_sha,
    spatialai_publish_variables,
)


class GhcrBuildEntriesTest(unittest.TestCase):
    def test_requires_new_ghcr_build(self):
        self.assertTrue(
            module.has_ghcr_build_entries(
                {
                    "images": [
                        {
                            "strategy": "build",
                            "image": "ghcr.io/nvidia/vss-agent",
                        }
                    ]
                }
            )
        )
        self.assertFalse(
            module.has_ghcr_build_entries(
                {
                    "images": [
                        {
                            "strategy": "reuse-pinned",
                            "image": "ghcr.io/nvidia/vss-agent",
                        }
                    ]
                }
            )
        )
        self.assertFalse(
            module.has_ghcr_build_entries(
                {
                    "images": [
                        {
                            "strategy": "build",
                            "image": "nvcr.io/nvidia/vss-agent",
                        }
                    ]
                }
            )
        )


class PrMergeBaseShaTest(unittest.TestCase):
    def test_uses_compare_merge_base_not_target_tip(self):
        target = "b" * 40
        head = "c" * 40
        merge_base = "a" * 40
        api = mock.Mock()
        api.request.side_effect = [
            {"base": {"sha": target}, "head": {"sha": head}},
            {"merge_base_commit": {"sha": merge_base}},
        ]
        self.assertEqual(
            pr_merge_base_sha(api, "NVIDIA-AI-Blueprints/vss", "pull-request/1601"),
            merge_base,
        )
        self.assertEqual(
            api.request.call_args_list,
            [
                mock.call("GET", "/repos/NVIDIA-AI-Blueprints/vss/pulls/1601"),
                mock.call("GET", f"/repos/NVIDIA-AI-Blueprints/vss/compare/{target}...{head}"),
            ],
        )

    def test_invalid_pr_metadata_fails_open_at_the_caller(self):
        api = mock.Mock()
        api.request.return_value = {"base": {"sha": "invalid"}}
        with self.assertRaisesRegex(RuntimeError, "valid base and head SHAs"):
            pr_merge_base_sha(api, "owner/repo", "pull-request/1")


class DownstreamVariablesTest(unittest.TestCase):
    def test_derives_candidate_tag_from_release_set_source(self):
        commit = "a" * 40
        for ref, expected in (
            ("develop", "develop-" + "a" * 12),
            ("pull-request/1396", "pr-1396-" + "a" * 12),
        ):
            with self.subTest(ref=ref):
                self.assertEqual(
                    candidate_container_tag(
                        {"source": {"commit": commit, "ref": ref}}
                    ),
                    expected,
                )

    def test_rejects_ref_without_shared_candidate_set(self):
        with self.assertRaisesRegex(ValueError, "does not publish"):
            candidate_container_tag(
                {"source": {"commit": "a" * 40, "ref": "release/3.2"}}
            )

    def test_encodes_exact_release_set_for_acceptance(self):
        release_set = {
            "schema_version": 1,
            "release_set_id": "sha256:" + "1" * 64,
            "source": {"commit": "a" * 40, "ref": "pull-request/1396"},
            "images": [{"name": "vss-agent"}],
        }
        variables = downstream_variables(release_set)
        self.assertEqual(variables["BUILD_TYPE"], "ghcr-acceptance")
        self.assertEqual(
            variables["VSS_CONTAINER_TAG"], "pr-1396-" + "a" * 12
        )
        self.assertEqual(
            variables["VSS_RELEASE_SET_ID"], release_set["release_set_id"]
        )
        decoded = json.loads(base64.b64decode(variables["VSS_RELEASE_SET_B64"]))
        self.assertEqual(decoded, release_set)

    def test_main_with_release_set_file_performs_no_network(self):
        sha = "a" * 40
        release_set = {
            "source": {"commit": sha, "ref": "pull-request/1396"},
            "release_set_id": "sha256:" + "1" * 64,
            "images": [],
        }
        with tempfile.TemporaryDirectory() as tmp:
            release_path = Path(tmp) / "release-set.json"
            release_output_path = Path(tmp) / "handoff/release-set.json"
            env_path = Path(tmp) / "github.env"
            output_path = Path(tmp) / "github.output"
            release_path.write_text(json.dumps(release_set))
            argv = [
                "prepare_downstream_release_set.py",
                "--sha",
                sha,
                "--release-set",
                str(release_path),
                "--release-set-output",
                str(release_output_path),
            ]
            with mock.patch("sys.argv", argv), mock.patch.dict(
                os.environ,
                {
                    "GITHUB_ENV": str(env_path),
                    "GITHUB_OUTPUT": str(output_path),
                },
                clear=True,
            ), mock.patch.object(
                module, "validate_release_set", return_value=[]
            ), mock.patch.object(
                # Pin the gate: its real inputs depend on git state, which
                # differs between a local worktree and a CI checkout.
                module, "downstream_relevant", return_value=(False, "pinned")
            ), mock.patch.object(module, "download_release_set") as download:
                self.assertEqual(module.main(), 0)
                download.assert_not_called()
            self.assertIn("DOWNSTREAM_EXTRA_VARIABLES_JSON", env_path.read_text())
            self.assertIn(
                '"VSS_CONTAINER_TAG":"pr-1396-' + "a" * 12 + '"',
                env_path.read_text(),
            )
            self.assertEqual(
                output_path.read_text(),
                "has_ghcr_build_entries=false\n"
                "run_downstream=false\n"
                "publish_spatialai_data_utils=false\n"
                "spatialai_package_version_suffix=\n"
                "spatialai_data_utils_tree_sha=\n",
            )
            self.assertEqual(json.loads(release_output_path.read_text()), release_set)

    def test_unrelated_develop_run_triggers_lightweight_sdu_reconciliation(self):
        sha = "a" * 40
        tree_sha = "b" * 40
        suffix = f".dev123+t{tree_sha}.g{sha[:12]}.gh.r1"
        release_set = {
            "source": {"commit": sha, "ref": "develop"},
            "release_set_id": "sha256:" + "1" * 64,
            "images": [],
        }
        with tempfile.TemporaryDirectory() as tmp:
            release_path = Path(tmp) / "release-set.json"
            env_path = Path(tmp) / "github.env"
            output_path = Path(tmp) / "github.output"
            release_path.write_text(json.dumps(release_set))
            argv = [
                "prepare_downstream_release_set.py",
                "--sha",
                sha,
                "--ref-name",
                "develop",
                "--release-set",
                str(release_path),
                "--spatialai-package-version-suffix",
                suffix,
                "--spatialai-data-utils-tree-sha",
                tree_sha,
            ]
            with mock.patch("sys.argv", argv), mock.patch.dict(
                os.environ,
                {
                    "GITHUB_ENV": str(env_path),
                    "GITHUB_OUTPUT": str(output_path),
                },
                clear=True,
            ), mock.patch.object(
                module, "validate_release_set", return_value=[]
            ), mock.patch.object(
                module, "resolve_diff_base", return_value=(sha, "pinned")
            ), mock.patch.object(
                module, "changed_paths", return_value=["docs/readme.md"]
            ), mock.patch.object(
                module, "load_inventory", return_value={"images": []}
            ):
                self.assertEqual(module.main(), 0)

            env_text = env_path.read_text()
            payload = env_text.split("<<EOF\n", 1)[1].split("\nEOF", 1)[0]
            variables = json.loads(payload)
            self.assertEqual(variables["BUILD_TYPE"], "spatialai-reconcile")
            self.assertEqual(
                variables["SPATIALAI_DATA_UTILS_RECONCILE"], "true"
            )
            self.assertEqual(variables["SPATIALAI_DATA_UTILS_PUBLISH"], "false")
            self.assertEqual(variables["SPATIALAI_SOURCE_TREE_SHA"], tree_sha)
            self.assertIn("run_downstream=true\n", output_path.read_text())



INVENTORY = {
    "images": [
        {"name": "vss-agent", "source_path": "services/agent", "ghcr_build": True},
        {"name": "vss-rt-cv", "source_path": "services/rt-cv",
         "trigger_downstream_from_source": True},
        {"name": "vss-configurator", "source_path": "services/configurators"},
    ]
}


class DownstreamGateTest(unittest.TestCase):
    """(source changed AND (ghcr_build OR opt-in)) OR deploy/ changed."""

    def test_ghcr_source_change_runs(self):
        run, why = downstream_relevant(["services/agent/app.py"], INVENTORY)
        self.assertTrue(run)
        self.assertIn("vss-agent", why)

    def test_opted_in_non_ghcr_source_change_runs(self):
        run, why = downstream_relevant(["services/rt-cv/x.cpp"], INVENTORY)
        self.assertTrue(run)
        self.assertIn("vss-rt-cv", why)

    def test_unflagged_source_change_does_not_run(self):
        run, _ = downstream_relevant(["services/configurators/a.py"], INVENTORY)
        self.assertFalse(run)

    def test_deploy_change_runs_without_any_source_change(self):
        run, why = downstream_relevant(["deploy/docker/containers.env"], INVENTORY)
        self.assertTrue(run)
        self.assertIn("deploy/", why)

    def test_unrelated_change_does_not_run(self):
        run, _ = downstream_relevant(["docs/readme.md", "skills/x/SKILL.md"], INVENTORY)
        self.assertFalse(run)

    def test_unresolvable_diff_runs_rather_than_skips(self):
        run, why = downstream_relevant(None, INVENTORY)
        self.assertTrue(run)
        self.assertIn("unavailable", why)

    def test_source_path_prefix_is_not_matched_loosely(self):
        run, _ = downstream_relevant(["services/agent-extras/x.py"], INVENTORY)
        self.assertFalse(run)


class SpatialAiPublishGateTest(unittest.TestCase):
    TREE_SHA = "b" * 40
    COMMIT_SHA = "a" * 40
    SUFFIX = ".dev123+t" + TREE_SHA + ".gaaaaaaaaaaaa.gh.r1"

    def variables(self, changed, ref_name="develop", requested=""):
        return spatialai_publish_variables(
            changed,
            ref_name,
            self.SUFFIX,
            self.TREE_SHA,
            self.COMMIT_SHA,
            requested,
        )

    def test_develop_change_requests_internal_publish(self):
        self.assertEqual(
            self.variables(
                ["libs/analytics/spatialai-data-utils/release/setup.py"]
            ),
            {
                "SPATIALAI_DATA_UTILS_PUBLISH": "true",
                "SPATIALAI_DATA_UTILS_RECONCILE": "true",
                "SPATIALAI_SOURCE_TREE_SHA": self.TREE_SHA,
                "SPATIALAI_PACKAGE_VERSION_SUFFIX": self.SUFFIX,
            },
        )

    def test_pr_change_never_requests_publish(self):
        self.assertEqual(
            self.variables(
                ["libs/analytics/spatialai-data-utils/release/setup.py"],
                "pull-request/1562",
            ),
            {},
        )

    def test_unrelated_develop_change_reconciles_without_immediate_publish(self):
        variables = self.variables(["docs/readme.md"])
        self.assertEqual(variables["SPATIALAI_DATA_UTILS_PUBLISH"], "false")
        self.assertEqual(variables["SPATIALAI_DATA_UTILS_RECONCILE"], "true")
        self.assertEqual(variables["SPATIALAI_SOURCE_TREE_SHA"], self.TREE_SHA)

    def test_every_develop_run_can_recover_a_missing_tree(self):
        self.assertEqual(
            self.variables(["services/agent/app.py"])[
                "SPATIALAI_PACKAGE_VERSION_SUFFIX"
            ],
            self.SUFFIX,
        )

    def test_sdu_only_handoff_uses_lightweight_downstream_pipeline(self):
        self.assertEqual(
            downstream_build_type(False, self.variables(["docs/readme.md"])),
            "spatialai-reconcile",
        )
        self.assertEqual(
            downstream_build_type(True, self.variables(["services/agent/app.py"])),
            "ghcr-acceptance",
        )

    def test_unavailable_develop_diff_fails_open(self):
        self.assertEqual(
            self.variables(None)["SPATIALAI_DATA_UTILS_PUBLISH"],
            "true",
        )

    def test_publish_rejects_missing_or_malformed_suffix(self):
        changed = ["libs/analytics/spatialai-data-utils/README.md"]
        for suffix in ("", "dev123", ".dev0+g0123456789ab.r1"):
            with self.subTest(suffix=suffix), self.assertRaisesRegex(
                ValueError, "version suffix"
            ):
                spatialai_publish_variables(
                    changed,
                    "develop",
                    suffix,
                    self.TREE_SHA,
                    self.COMMIT_SHA,
                )

    def test_publish_rejects_tree_or_commit_identity_mismatch(self):
        changed = ["libs/analytics/spatialai-data-utils/README.md"]
        for tree_sha, commit_sha, message in (
            ("c" * 40, self.COMMIT_SHA, "source tree"),
            (self.TREE_SHA, "c" * 40, "source commit"),
        ):
            with self.subTest(message=message), self.assertRaisesRegex(
                ValueError, message
            ):
                spatialai_publish_variables(
                    changed,
                    "develop",
                    self.SUFFIX,
                    tree_sha,
                    commit_sha,
                )

    def test_publish_rejects_malformed_full_tree_sha(self):
        with self.assertRaisesRegex(ValueError, "full source tree SHA"):
            spatialai_publish_variables(
                ["libs/analytics/spatialai-data-utils/README.md"],
                "develop",
                self.SUFFIX,
                "b" * 12,
                self.COMMIT_SHA,
            )

    def test_explicit_false_keeps_reconciliation_in_handoff_job(self):
        variables = self.variables(None, requested="false")
        self.assertEqual(variables["SPATIALAI_DATA_UTILS_PUBLISH"], "false")
        self.assertEqual(variables["SPATIALAI_DATA_UTILS_RECONCILE"], "true")

    def test_explicit_true_preserves_first_job_decision(self):
        self.assertEqual(
            self.variables(["docs/readme.md"], requested="true")[
                "SPATIALAI_DATA_UTILS_PUBLISH"
            ],
            "true",
        )

    def test_explicit_true_is_rejected_outside_develop(self):
        with self.assertRaisesRegex(ValueError, "only for develop"):
            self.variables(None, "pull-request/1562", "true")


if __name__ == "__main__":
    unittest.main(verbosity=2)
