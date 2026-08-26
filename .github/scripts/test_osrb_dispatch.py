#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Focused tests for the public-to-private OSRB dispatch boundary."""

from __future__ import annotations

import importlib.util
import os
import re
import unittest
from importlib.machinery import SourceFileLoader
from pathlib import Path
from unittest import mock

DIRECTORY = Path(__file__).parent
WORKFLOW = DIRECTORY.parent / "workflows" / "osrb-review.yml"
SCAN_WORKFLOW = DIRECTORY.parent / "workflows" / "osrb-scan.yml"
DEVELOPER_GUIDE = DIRECTORY.parent / "OSRB_REVIEW.md"


def load_python(name: str, path: Path):
    loader = SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    assert spec
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


trigger = load_python("trigger_downstream", DIRECTORY / "trigger_downstream_pipeline.py")
check = load_python("osrb_check", DIRECTORY / "osrb_check.py")


class DispatchTests(unittest.TestCase):
    def test_extra_variables_are_string_map(self) -> None:
        with mock.patch.dict(
            os.environ,
            {"DOWNSTREAM_EXTRA_VARIABLES_JSON": '{"OSRB_REVIEW":"true","PR":"42"}'},
            clear=False,
        ):
            self.assertEqual(
                trigger.extra_pipeline_variables(),
                {"OSRB_REVIEW": "true", "PR": "42"},
            )

    def test_extra_variables_reject_non_string_values(self) -> None:
        with mock.patch.dict(
            os.environ,
            {"DOWNSTREAM_EXTRA_VARIABLES_JSON": '{"PR":42}'},
            clear=False,
        ), self.assertRaises(SystemExit):
            trigger.extra_pipeline_variables()

    def test_check_external_id_is_private_pipeline_scoped(self) -> None:
        self.assertEqual(check.EXTERNAL_PREFIX, "gitlab-osrb:")

    def test_check_links_to_public_developer_instructions(self) -> None:
        repo = "NVIDIA-AI-Blueprints/video-search-and-summarization"
        self.assertEqual(
            check.guide_url(repo),
            f"https://github.com/{repo}/blob/main/.github/OSRB_REVIEW.md",
        )
        self.assertIn(
            "[How to respond to this check]",
            check.summary_with_guide(repo, "Review passed."),
        )

    def test_dispatch_passes_canonical_license_diff_run_url(self) -> None:
        workflow = WORKFLOW.read_text()
        self.assertIn(
            '"GITHUB_LICENSE_RUN_URL":"${{ github.event.workflow_run.html_url }}"',
            workflow,
        )
        self.assertIn('LICENSE_RUN_URL: ${{ github.event.workflow_run.html_url }}', workflow)
        self.assertIn('--run-url "$LICENSE_RUN_URL"', workflow)

    def test_check_is_created_before_anything_that_can_fail(self) -> None:
        """A failure before the check exists would be an invisible fail-open.

        `workflow_run` jobs are not listed in the pull request, so if no check
        run is created the gate simply does not happen and nothing says so.
        """
        workflow = WORKFLOW.read_text()
        steps = re.findall(r"^      - name: (.+)$", workflow, re.M)
        self.assertEqual(steps[1], "Start OSRB check", steps)

        start_block = workflow.split("- name: Start OSRB check", 1)[1]
        start_block = start_block.split("- name:", 1)[0]
        self.assertNotIn("if:", start_block, "Start OSRB check must be unconditional")

    def test_every_path_resolves_the_check(self) -> None:
        """No branch may leave the check stuck in_progress with nothing to close it."""
        workflow = WORKFLOW.read_text()
        complete_block = workflow.split("- name: Complete OSRB check", 1)[1]
        self.assertIn("if: always()\n", complete_block)
        self.assertNotIn("always() && steps.pr.outputs.skip", complete_block)

        mark_block = workflow.split("- name: Mark release-branch review as not applicable", 1)[1]
        mark_block = mark_block.split("- name:", 1)[0]
        self.assertIn("continue-on-error: true", mark_block)

    def test_complete_publishes_even_when_the_check_was_never_started(self) -> None:
        calls: list[tuple[str, str, dict | None]] = []

        def fake_github(method: str, repo: str, path: str, payload: dict | None = None):
            calls.append((method, path, payload))
            return {"check_runs": []} if method == "GET" else {}

        with mock.patch.object(check, "github", fake_github):
            check.complete("owner/repo", "deadbeef", "https://run", "gitlab-osrb:1", False, "why")

        self.assertEqual([call[0] for call in calls], ["GET", "POST"])
        created = calls[1][2] or {}
        self.assertEqual(created["head_sha"], "deadbeef")
        self.assertEqual(created["conclusion"], "failure")
        self.assertEqual(created["external_id"], "gitlab-osrb:1")

    def test_dispatch_job_wiring_is_exercised_in_ci(self) -> None:
        self.assertIn(
            "python3 .github/scripts/test_osrb_dispatch.py",
            (DIRECTORY.parent / "workflows" / "ci.yml").read_text(),
        )

    def test_workflow_supplies_every_variable_the_helpers_require(self) -> None:
        """The poller reads DOWNSTREAM_PROJECT_PATH even when given a project id.

        ci.yml supplies the shared downstream variables at job level, so both
        the trigger and the poll step inherit them. Setting them per step is
        what left the poller a variable short.
        """
        workflow = WORKFLOW.read_text()
        required: set[str] = set()
        for helper in ("trigger_downstream_pipeline.py", "poll-downstream-pipeline.py"):
            required.update(
                re.findall(r'require_env\(\s*"([A-Z0-9_]+)"', (DIRECTORY / helper).read_text())
            )
        missing = sorted(name for name in required if name not in workflow)
        self.assertEqual(missing, [], f"workflow never sets {missing}")

    def test_dispatch_does_not_choose_the_private_pipeline_code_ref(self) -> None:
        workflow = WORKFLOW.read_text()
        for variable in ("OSRB_CODE_REF", "OSRB_ALLOW_UNREVIEWED_CODE"):
            self.assertNotIn(variable, workflow)

    def test_github_output_explains_developer_actions(self) -> None:
        guide = DEVELOPER_GUIDE.read_text()
        scan_workflow = SCAN_WORKFLOW.read_text()
        self.assertIn("### OSRB Review fails or is inconclusive", guide)
        self.assertIn("Do not paste private ticket comments", guide)
        self.assertIn("### What to do", scan_workflow)
        self.assertIn("Developer instructions", scan_workflow)

    def test_workflow_run_trigger_matches_the_scan_workflow_name(self) -> None:
        """A mismatch here disables the whole OSRB gate and says nothing.

        `workflow_run` matches on the upstream workflow's `name:`, not its
        filename, so renaming one file without the other produces no error,
        no check run, and no comment -- every pull request just stops being
        reviewed. Parsed by hand because CI has no YAML library.
        """
        name_lines = [
            line for line in SCAN_WORKFLOW.read_text().splitlines()
            if line.startswith("name:")
        ]
        self.assertEqual(name_lines, ["name: OSRB Scan"], name_lines)
        scan_name = name_lines[0].split(":", 1)[1].strip()

        trigger = [
            line.strip() for line in WORKFLOW.read_text().splitlines()
            if line.strip().startswith("workflows:")
        ]
        self.assertEqual(len(trigger), 1, trigger)

        # Membership, not equality. The listener deliberately accepts the old
        # name as well, because `workflow_run` is loaded from the DEFAULT
        # branch: until this file lands there, a mirror emitting the new name
        # matches nothing and the gate silently does not run. Asserting an
        # exact list would forbid that transition list; what actually must hold
        # is that the producer's name is one the listener accepts.
        accepted = {
            part.strip().strip('"\'')
            for part in trigger[0].split("[", 1)[1].rsplit("]", 1)[0].split(",")
        }
        self.assertIn(scan_name, accepted, (scan_name, accepted))

    def test_scan_keeps_the_artifact_and_csv_names_the_private_pipeline_reads(self) -> None:
        """The GitLab OSRB job fetches these by name; a rename fails open.

        Nothing in this repository can observe that consumer, so the only
        protection against a well-meaning tidy-up is this assertion.
        """
        workflow = SCAN_WORKFLOW.read_text()
        self.assertIn("name: license-diff\n", workflow)
        self.assertIn("--output license-diff.csv", workflow)


if __name__ == "__main__":
    unittest.main()
