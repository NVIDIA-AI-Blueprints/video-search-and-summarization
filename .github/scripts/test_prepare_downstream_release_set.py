#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

import prepare_downstream_release_set as module  # noqa: E402
from prepare_downstream_release_set import (  # noqa: E402
    candidate_container_tag,
    downstream_relevant,
    downstream_variables,
    pr_merge_base_sha,
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
    def test_derives_candidate_tag_from_ref_and_sha(self):
        commit = "a" * 40
        for ref, expected in (
            ("develop", "develop-" + "a" * 12),
            ("pull-request/1396", "pr-1396-" + "a" * 12),
        ):
            with self.subTest(ref=ref):
                self.assertEqual(candidate_container_tag(ref, commit), expected)

    def test_rejects_ref_without_shared_candidate_set(self):
        with self.assertRaisesRegex(ValueError, "does not publish"):
            candidate_container_tag("release/3.2", "a" * 40)

    def test_rejects_short_sha(self):
        with self.assertRaisesRegex(ValueError, "40-hex"):
            candidate_container_tag("develop", "abc123")

    def test_emits_only_what_downstream_reads(self):
        variables = downstream_variables("pull-request/1396", "a" * 40)
        self.assertEqual(variables["BUILD_TYPE"], "ghcr-acceptance")
        self.assertEqual(
            variables["VSS_CONTAINER_TAG"], "pr-1396-" + "a" * 12
        )
        # The release set itself is deliberately not sent: ci-vss-oss has no
        # consumer for it. Assert its absence so a reintroduction is caught.
        self.assertNotIn("VSS_RELEASE_SET_ID", variables)
        self.assertNotIn("VSS_RELEASE_SET_B64", variables)

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


class WorkflowSeparationTest(unittest.TestCase):
    def test_sdu_has_an_independent_workflow_and_handoff(self):
        workflows = Path(__file__).resolve().parents[1] / "workflows"
        main = (workflows / "ci.yml").read_text()
        sdu = (workflows / "spatialai-data-utils.yml").read_text()

        self.assertNotIn("spatialai-data-utils-test", main)
        self.assertNotIn("SPATIALAI_PACKAGE_VERSION_SUFFIX", main)
        self.assertIn("name: Spatial AI Data Utils", sdu)
        self.assertIn("name: Gate", sdu)
        self.assertIn('suffix = f".dev0+g{tree_sha[:12]}"', sdu)
        self.assertIn("DOWNSTREAM_REF: main", sdu)
        self.assertIn('"SPATIALAI_PIPELINE": "true"', sdu)
        sonar = (workflows / "sonarqube.yml").read_text()
        match = re.search(
            r"^          - name: spatialai-data-utils\n(?P<entry>(?:            .*\n)+)",
            sonar,
            flags=re.MULTILINE,
        )
        self.assertIsNotNone(match, "SDU SonarQube matrix entry is missing")
        assert match is not None
        entry = match.group("entry")
        self.assertIn(
            "TEGRASW_METROPOLIS_spatialai-data-utils_video-search-and-summarization",
            entry,
        )
        self.assertIn(
            "sources: libs/analytics/spatialai-data-utils/spatialai_data_utils",
            entry,
        )
        self.assertIn(
            "tests: libs/analytics/spatialai-data-utils/tests",
            entry,
        )
        self.assertIn('python_version: "3.13"', entry)

    def test_release_set_preparation_has_no_sdu_transport(self):
        script = Path(module.__file__).read_text()
        self.assertNotIn("SPATIALAI_", script)


if __name__ == "__main__":
    unittest.main(verbosity=2)
