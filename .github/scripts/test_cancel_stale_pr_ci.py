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

SOURCE = "s" * 40
BASE = "b" * 40
MERGE = "m" * 40
SOURCE_TREE = "stree"
MERGE_TREE = "mtree"
BASE_TREE = "btree"
OLD_SOURCE = "o" * 40
OLD_TREE = "otree"


def run(*, run_id: int = 1, status: str = "in_progress", head_sha: str = MERGE) -> dict:
    return {"id": run_id, "status": status, "head_sha": head_sha, "name": "CI"}


class MirrorsCurrentSourceTest(unittest.TestCase):
    def test_exact_sha_copy(self):
        self.assertTrue(
            module.mirrors_current_source(
                source_sha=SOURCE,
                source_tree=SOURCE_TREE,
                run_sha=SOURCE,
                run_tree=SOURCE_TREE,
                parent_shas=(BASE,),
                parent_trees=(BASE_TREE,),
            )
        )

    def test_fork_copy_same_tree_different_sha(self):
        self.assertTrue(
            module.mirrors_current_source(
                source_sha=SOURCE,
                source_tree=SOURCE_TREE,
                run_sha="c" * 40,
                run_tree=SOURCE_TREE,
                parent_shas=(),
                parent_trees=(),
            )
        )

    def test_merge_into_base_keeps_current_run(self):
        """CPR-bot merge result tree differs from the source head tree."""
        self.assertTrue(
            module.mirrors_current_source(
                source_sha=SOURCE,
                source_tree=SOURCE_TREE,
                run_sha=MERGE,
                run_tree=MERGE_TREE,
                parent_shas=(BASE, SOURCE),
                parent_trees=(BASE_TREE, SOURCE_TREE),
            )
        )

    def test_merge_of_old_head_is_stale(self):
        self.assertFalse(
            module.mirrors_current_source(
                source_sha=SOURCE,
                source_tree=SOURCE_TREE,
                run_sha=MERGE,
                run_tree=MERGE_TREE,
                parent_shas=(BASE, OLD_SOURCE),
                parent_trees=(BASE_TREE, OLD_TREE),
            )
        )

    def test_fork_merge_matches_parent_tree(self):
        copied = "k" * 40
        self.assertTrue(
            module.mirrors_current_source(
                source_sha=SOURCE,
                source_tree=SOURCE_TREE,
                run_sha=MERGE,
                run_tree=MERGE_TREE,
                parent_shas=(BASE, copied),
                parent_trees=(BASE_TREE, SOURCE_TREE),
            )
        )


class ShouldCancelTest(unittest.TestCase):
    def test_closed_pr_cancels_active_runs(self):
        self.assertTrue(
            module.should_cancel_run(
                run=run(),
                matches_source=True,
                closed=True,
                this_run_id=99,
            )
        )

    def test_closed_pr_does_not_cancel_tag_cleanup(self):
        self.assertFalse(
            module.should_cancel_run(
                run=run() | {"name": "Cleanup PR Tags"},
                matches_source=True,
                closed=True,
                this_run_id=99,
            )
        )

    def test_does_not_cancel_self(self):
        self.assertFalse(
            module.should_cancel_run(
                run=run(run_id=99),
                matches_source=False,
                closed=True,
                this_run_id=99,
            )
        )

    def test_current_run_is_kept(self):
        self.assertFalse(
            module.should_cancel_run(
                run=run(),
                matches_source=True,
                closed=False,
                this_run_id=99,
            )
        )

    def test_stale_run_is_cancelled(self):
        self.assertTrue(
            module.should_cancel_run(
                run=run(),
                matches_source=False,
                closed=False,
                this_run_id=99,
            )
        )

    def test_requested_and_pending_are_active(self):
        self.assertIn("requested", module.ACTIVE_STATUSES)
        self.assertIn("pending", module.ACTIVE_STATUSES)
        for status in ("requested", "pending"):
            self.assertTrue(
                module.should_cancel_run(
                    run=run(status=status),
                    matches_source=False,
                    closed=False,
                    this_run_id=99,
                )
            )

    def test_completed_run_is_ignored(self):
        self.assertFalse(
            module.should_cancel_run(
                run=run(status="completed"),
                matches_source=False,
                closed=False,
                this_run_id=99,
            )
        )


class ListActiveRunsTest(unittest.TestCase):
    def test_skips_status_that_github_rejects(self):
        calls: list[str] = []

        def get(_method: str, _repo: str, path: str):
            calls.append(path)
            if "status=requested" in path:
                raise module.CancelError("GET /actions/runs returned HTTP 422: invalid")
            if "status=in_progress" in path:
                return {
                    "workflow_runs": [
                        {"id": 7, "status": "in_progress", "head_sha": MERGE}
                    ]
                }
            return {"workflow_runs": []}

        runs = module.list_active_runs("owner/repo", "pull-request/1900", get)
        self.assertEqual([run["id"] for run in runs], [7])
        self.assertTrue(any("status=requested" in path for path in calls))


class ClosedPrCoverageTest(unittest.TestCase):
    def test_run_belongs_to_pr_via_pull_requests_field(self):
        sonar = {
            "id": 8,
            "name": "SonarQube Analysis",
            "status": "in_progress",
            "head_branch": "feat/foo",
            "pull_requests": [{"number": 1900}],
        }
        self.assertTrue(module.run_belongs_to_pr(sonar, 1900))
        self.assertFalse(module.run_belongs_to_pr(sonar, 1))

    def test_run_belongs_to_pr_after_close_empties_pull_requests(self):
        # The shape the close path actually sees: GitHub drops the PR
        # association the moment the PR closes.
        sonar = {
            "id": 8,
            "name": "SonarQube Analysis",
            "status": "in_progress",
            "head_branch": "feat/foo",
            "head_sha": "a" * 40,
            "pull_requests": [],
        }
        self.assertFalse(module.run_belongs_to_pr(sonar, 1900))
        self.assertTrue(
            module.run_belongs_to_pr(sonar, 1900, source_branch="feat/foo")
        )
        self.assertTrue(
            module.run_belongs_to_pr(sonar, 1900, source_sha="a" * 40)
        )
        self.assertFalse(
            module.run_belongs_to_pr(sonar, 1900, source_branch="feat/other")
        )

    def test_closed_collects_native_pr_event_runs(self):
        def get(_method: str, _repo: str, path: str):
            if "event=pull_request&" in path and "status=in_progress" in path:
                return {
                    "workflow_runs": [
                        {
                            "id": 8,
                            "name": "SonarQube Analysis",
                            "status": "in_progress",
                            "head_branch": "feat/foo",
                            "pull_requests": [],
                        },
                        {
                            "id": 9,
                            "name": "SonarQube Analysis",
                            "status": "in_progress",
                            "head_branch": "feat/other",
                            "pull_requests": [],
                        },
                    ]
                }
            if "branch=pull-request%2F42" in path and "status=in_progress" in path:
                return {
                    "workflow_runs": [
                        {
                            "id": 3,
                            "name": "CI",
                            "status": "in_progress",
                            "head_branch": "pull-request/42",
                        }
                    ]
                }
            return {"workflow_runs": []}

        runs = module.collect_runs_to_consider(
            "owner/repo", 42, True, get, source_branch="feat/foo"
        )
        self.assertEqual(sorted(run["id"] for run in runs), [3, 8])

    def test_open_pr_does_not_scan_native_pr_events(self):
        calls: list[str] = []

        def get(_method: str, _repo: str, path: str):
            calls.append(path)
            return {"workflow_runs": []}

        module.collect_runs_to_consider("owner/repo", 42, False, get)
        self.assertFalse(any("event=pull_request" in path for path in calls))


class WorkflowTest(unittest.TestCase):
    def test_pull_request_target_does_not_checkout_the_pr_head(self):
        text = WORKFLOW.read_text()
        self.assertIn("pull_request_target:", text)
        self.assertIn("persist-credentials: false", text)
        self.assertNotIn("ref: ${{ github.event.pull_request.head", text)
        self.assertIn("actions: write", text)
        self.assertIn("SOURCE_BRANCH: ${{ github.event.pull_request.head.ref }}", text)

    def test_ci_runs_these_tests(self):
        ci = (Path(__file__).resolve().parent.parent / "workflows" / "ci.yml").read_text()
        self.assertIn("python3 .github/scripts/test_cancel_stale_pr_ci.py", ci)


if __name__ == "__main__":
    unittest.main()
