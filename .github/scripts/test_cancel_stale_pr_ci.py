#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

SCRIPT = Path(__file__).with_name("cancel_stale_pr_ci.py")
WORKFLOW = Path(__file__).resolve().parent.parent / "workflows" / "cancel-stale-pr-ci.yml"
SPEC = importlib.util.spec_from_file_location("cancel_stale_pr_ci", SCRIPT)
assert SPEC and SPEC.loader
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


def run(*, run_id: int = 1, status: str = "in_progress", head_sha: str = "a" * 40) -> dict:
    return {"id": run_id, "status": status, "head_sha": head_sha, "name": "CI"}


class ShouldCancelTest(unittest.TestCase):
    def test_closed_pr_cancels_active_runs(self):
        self.assertTrue(
            module.should_cancel_run(
                run=run(),
                source_tree="new",
                run_tree="new",
                closed=True,
                this_run_id=99,
            )
        )

    def test_does_not_cancel_self(self):
        self.assertFalse(
            module.should_cancel_run(
                run=run(run_id=99),
                source_tree="new",
                run_tree="old",
                closed=True,
                this_run_id=99,
            )
        )

    def test_same_tree_is_kept_even_when_sha_differs(self):
        """copy-pr-bot may rewrite the commit; the tree is what CI is testing."""
        self.assertFalse(
            module.should_cancel_run(
                run=run(),
                source_tree="same-tree",
                run_tree="same-tree",
                closed=False,
                this_run_id=99,
            )
        )

    def test_stale_tree_is_cancelled(self):
        self.assertTrue(
            module.should_cancel_run(
                run=run(),
                source_tree="after-rebase",
                run_tree="before-rebase",
                closed=False,
                this_run_id=99,
            )
        )

    def test_unknown_tree_is_not_cancelled(self):
        self.assertFalse(
            module.should_cancel_run(
                run=run(),
                source_tree=None,
                run_tree="before-rebase",
                closed=False,
                this_run_id=99,
            )
        )

    def test_completed_run_is_ignored(self):
        self.assertFalse(
            module.should_cancel_run(
                run=run(status="completed"),
                source_tree="new",
                run_tree="old",
                closed=False,
                this_run_id=99,
            )
        )


class WorkflowTest(unittest.TestCase):
    def test_pull_request_target_does_not_checkout_the_pr_head(self):
        text = WORKFLOW.read_text()
        self.assertIn("pull_request_target:", text)
        self.assertIn("persist-credentials: false", text)
        self.assertNotIn("ref: ${{ github.event.pull_request.head", text)
        self.assertIn("actions: write", text)

    def test_ci_runs_these_tests(self):
        ci = (Path(__file__).resolve().parent.parent / "workflows" / "ci.yml").read_text()
        self.assertIn("python3 .github/scripts/test_cancel_stale_pr_ci.py", ci)


if __name__ == "__main__":
    unittest.main()
