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
LICENSE_WORKFLOW = DIRECTORY.parent / "workflows" / "license-diff.yml"
DEVELOPER_GUIDE = DIRECTORY.parent / "OSRB_REVIEW.md"


def load_python(name: str, path: Path):
    loader = SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    assert spec
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


trigger = load_python("trigger_downstream", DIRECTORY / "trigger-downstream-pipeline.sh")
check = load_python("osrb_check", DIRECTORY / "osrb_check.py")


class DispatchTests(unittest.TestCase):
    def test_extra_variables_are_string_map(self) -> None:
        with mock.patch.dict(
            os.environ,
            {"DOWNSTREAM_EXTRA_VARIABLES_JSON": '{"OSRB_REVIEW":"true","PR":"42"}'},
            clear=False,
        ):
            self.assertEqual(
                trigger.configured_extra_variables(),
                {"OSRB_REVIEW": "true", "PR": "42"},
            )

    def test_extra_variables_reject_non_string_values(self) -> None:
        with mock.patch.dict(
            os.environ,
            {"DOWNSTREAM_EXTRA_VARIABLES_JSON": '{"PR":42}'},
            clear=False,
        ), self.assertRaises(SystemExit):
            trigger.configured_extra_variables()

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
        for helper in ("trigger-downstream-pipeline.sh", "poll-downstream-pipeline.py"):
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
        license_workflow = LICENSE_WORKFLOW.read_text()
        self.assertIn("### OSRB Review fails or is inconclusive", guide)
        self.assertIn("Do not paste private ticket comments", guide)
        self.assertIn("### What to do", license_workflow)
        self.assertIn("Developer instructions", license_workflow)


if __name__ == "__main__":
    unittest.main()
