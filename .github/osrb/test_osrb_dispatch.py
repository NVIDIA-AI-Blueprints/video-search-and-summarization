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
# The OSRB tooling lives in .github/osrb. The generic downstream-pipeline
# helpers it dispatches through are shared with other workflows and stayed
# in .github/scripts, so the two directories are named separately here.
SCRIPTS = DIRECTORY.parent / "scripts"
WORKFLOW = DIRECTORY.parent / "workflows" / "osrb-review.yml"
SCAN_WORKFLOW = DIRECTORY.parent / "workflows" / "osrb-scan.yml"
DEVELOPER_GUIDE = DIRECTORY / "OSRB_REVIEW.md"


def load_python(name: str, path: Path):
    loader = SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    assert spec
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


trigger = load_python("trigger_downstream", SCRIPTS / "trigger_downstream_pipeline.py")
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
            f"https://github.com/{repo}/blob/main/.github/osrb/OSRB_REVIEW.md",
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
            "python3 .github/osrb/test_osrb_dispatch.py",
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
                re.findall(r'require_env\(\s*"([A-Z0-9_]+)"', (SCRIPTS / helper).read_text())
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

    def test_every_osrb_test_file_is_run_by_ci(self) -> None:
        """A test nobody runs is worse than no test: it reads as coverage.

        Enumerated from the directory rather than a hard-coded list, so a new
        test_*.py added here has to be wired into ci.yml to land.
        """
        ci = (DIRECTORY.parent / "workflows" / "ci.yml").read_text()
        missing = sorted(
            path.name
            for path in DIRECTORY.glob("test_*.py")
            if f".github/osrb/{path.name}" not in ci
        )
        self.assertEqual(missing, [], f"ci.yml never runs {missing}")

    def test_state_job_uploads_a_separate_artifact(self) -> None:
        """Compliance files must not join the artifact the GitLab job fetches.

        The private pipeline downloads `license-diff` and reads what is in it.
        Folding the state report into that artifact changes the payload of a
        consumer this repository cannot see or test.
        """
        workflow = SCAN_WORKFLOW.read_text()
        self.assertIn("name: osrb-compliance\n", workflow)
        license_upload = workflow.split("name: license-diff\n", 1)[1].split("- name:", 1)[0]
        self.assertNotIn("osrb-compliance", license_upload)

    def test_state_job_fails_on_drift_and_only_on_drift(self) -> None:
        """MODULE_UNSUBMITTED / APPROVED_NOT_PRESENT are pre-existing state.

        They describe the OSRB record, not what this pull request did, so a
        contributor cannot clear them. Gating on either makes the check red for
        everyone forever, which is how a gate stops being read.
        """
        workflow = SCAN_WORKFLOW.read_text()
        gate = workflow.split("- name: Report drift from the approved baseline", 1)[1]
        for verdict in ("NOT_APPROVED", "VERSION_DRIFT", "LICENSE_DRIFT", "USAGE_DRIFT"):
            self.assertIn(f"steps.compare.outputs.{verdict.lower()}", gate, verdict)
        for reported_only in ("module_unsubmitted", "approved_not_present"):
            self.assertNotIn(f"steps.compare.outputs.{reported_only}", gate, reported_only)

    def test_state_comparison_cannot_fail_the_job(self) -> None:
        """It reports; it must not gate. A gate that cannot fail is worse.

        A ratchet was implemented here and removed after review found four
        independent ways it could never fire -- a cross-job `steps.ctx`
        reference that resolved to "" (and `git show ":path"` reads the INDEX
        rather than failing), a baseline computed from the PR's own
        approved.csv, count-netting that let a new GPL-3.0 package in as long
        as another finding was removed, and an ungated catch-all bucket for
        unknown modules. Until identity-based comparison replaces it, this step
        must not pretend to gate.
        """
        workflow = SCAN_WORKFLOW.read_text()
        step = workflow.split("- name: Report drift from the approved baseline", 1)[1]
        step = step.split("- name:", 1)[0]
        self.assertNotIn("exit 1", step)
        self.assertNotIn("status=1", step)
        self.assertNotIn("::error", step)
        # and the dead reference that caused the bug must not come back.
        # Comment lines are stripped first: the postmortem above names the bad
        # expression on purpose, and matching it there would make this test
        # fail for documenting the very thing it guards against.
        self.assertNotIn("compare_base", workflow)
        job_two = workflow.split("dependency-inventory:", 1)[1]
        live = "\n".join(
            line for line in job_two.splitlines() if not line.lstrip().startswith("#")
        )
        self.assertNotIn("steps.ctx.outputs", live)

    def test_inventory_drift_gate_prints_the_refresh_command(self) -> None:
        """The error has to carry the fix; a diff alone leaves the reader guessing."""
        workflow = SCAN_WORKFLOW.read_text()
        gate = workflow.split("- name: Fail when the committed inventory is stale", 1)[1]
        gate = gate.split("- name:", 1)[0]
        self.assertIn("osrb_inventory.py", gate)
        # --previous is what makes the refresh reproduce the committed file.
        # An instruction that omits it sends the contributor round the loop
        # again with a diff that never converges.
        self.assertIn("--previous ", gate)
        self.assertIn("--output .github/osrb/inventory.csv", gate)

    def test_scan_keeps_the_artifact_and_csv_names_the_private_pipeline_reads(self) -> None:
        """The GitLab OSRB job fetches these by name; a rename fails open.

        Nothing in this repository can observe that consumer, so the only
        protection against a well-meaning tidy-up is this assertion.
        """
        workflow = SCAN_WORKFLOW.read_text()
        self.assertIn("name: license-diff\n", workflow)
        self.assertIn("--output license-diff.csv", workflow)


    def test_scan_cancels_stale_pr_runs(self) -> None:
        scan = SCAN_WORKFLOW.read_text()
        self.assertIn("group: osrb-scan-${{ github.ref }}", scan)
        self.assertIn("cancel-in-progress: true", scan)

    def test_cancelled_scan_does_not_start_osrb_review(self) -> None:
        """A superseded CSV must not launch the private reviewer.

        The scan is still allowed to *fail* (non-empty diffs fail on develop)
        and OSRB Review must still run in that case -- so the guard is
        `!= cancelled`, never `== success`. Ported from develop's
        license-diff variant of the same tests.
        """
        workflow = WORKFLOW.read_text()
        self.assertIn(
            "github.event.workflow_run.conclusion != 'cancelled'",
            workflow,
        )
        self.assertNotIn(
            "github.event.workflow_run.conclusion == 'success'",
            workflow,
        )


class TriageWiringTests(unittest.TestCase):
    """Wiring assertions for the triage job in osrb-scan.yml.

    The triage agent is report-only by contract: the failure modes worth
    pinning are the silent ones — the job not running exactly when it is
    needed, an agent error turning the workflow red, the auto-commit widening
    past one file, or the triage output leaking into the artifact the private
    GitLab pipeline fetches by name.
    """

    @staticmethod
    def _job() -> str:
        return SCAN_WORKFLOW.read_text().split("\n  triage:", 1)[1]

    @staticmethod
    def _step(job: str, name: str) -> str:
        block = job.split(f"- name: {name}", 1)[1]
        return block.split("- name:", 1)[0]

    def test_triage_needs_both_pipelines_and_survives_their_failure(self) -> None:
        """A red delta gate is exactly the run with new dependencies to triage.

        Without `always()`, `needs:` skips this job the moment the osrb-scan
        job fails — which it does, by design, on every PR that adds a
        dependency. The triage would then only ever run when it has nothing
        to say.
        """
        job = self._job()
        self.assertIn("needs: [osrb-scan, dependency-inventory]", job)
        condition = job.split("if: >-", 1)[1].split("permissions:", 1)[0]
        self.assertIn("always()", condition)
        # cancelled is different: a superseded push must not post a stale comment.
        self.assertIn("needs.osrb-scan.result != 'cancelled'", condition)
        self.assertIn("needs.dependency-inventory.result != 'cancelled'", condition)
        # release-branch PRs upload no delta CSV, by design (job 1 skips).
        self.assertIn("needs.osrb-scan.outputs.skip != 'true'", condition)

    def test_agent_and_commit_failures_cannot_red_the_workflow(self) -> None:
        """Report-only stage: the delta gate stays the blocking check."""
        job = self._job()
        for name in (
            "Install claude-agent-sdk",
            "Run the triage agent",
            "Commit agent-triaged licences to the mirror branch",
        ):
            self.assertIn("continue-on-error: true", self._step(job, name), name)

    def test_missing_api_key_degrades_to_skip_agent_never_fails(self) -> None:
        agent = self._step(self._job(), "Run the triage agent")
        self.assertIn("--skip-agent", agent)
        # Shell default, because the secret may be unset: an empty env var
        # must not reach the SDK as a model name.
        self.assertIn('ANTHROPIC_MODEL="${ANTHROPIC_MODEL:-claude-opus-5}"', agent)

    def test_comment_posts_on_every_path_with_its_own_marker(self) -> None:
        job = self._job()
        post = self._step(job, "Post or update the triage PR comment")
        self.assertIn("if: always()", post)
        self.assertIn("<!-- osrb-triage -->", post)
        # and the job 1 comment keeps its marker — the two must never collide.
        self.assertIn("<!-- license-diff-osrb -->", SCAN_WORKFLOW.read_text())
        # Comment lines stripped first: the workflow documents the internal
        # reviewer's marker on purpose; only live code must never scan for it.
        live = "\n".join(
            line for line in job.splitlines() if not line.lstrip().startswith("#")
        )
        self.assertNotIn("hinton-osrb-review", live)

    def test_auto_commit_is_guarded_and_touches_only_the_inventory(self) -> None:
        """The writable surface is two columns of one file, checked before git."""
        commit = self._step(self._job(), "Commit agent-triaged licences to the mirror branch")
        self.assertEqual(
            re.findall(r"^\s*git add (.+)$", commit, re.M),
            [".github/osrb/inventory.csv"],
        )
        # The guard runs before anything is staged; a guard failure aborts.
        self.assertLess(commit.index("--check-inventory-diff"), commit.index("git add"))
        # DCO + bot identity, same handling as skill-eval's adapter commit.
        self.assertIn("git commit -s", commit)
        self.assertIn('git config user.name "github-actions[bot]"', commit)
        self.assertIn("chore(osrb): agent-triaged licences for", commit)
        # Only on a clean agent exit: 2/3 mean rejected/truncated output.
        self.assertIn("steps.agent.outcome == 'success'", commit)

    def test_triage_uploads_join_compliance_never_the_license_diff_artifact(self) -> None:
        """The private GitLab pipeline fetches `license-diff` by name; its
        payload must not change. Triage may only download it."""
        job = self._job()
        uploads = job.split("uses: actions/upload-artifact")[1:]
        self.assertEqual(len(uploads), 1, "triage must upload exactly one artifact")
        upload = uploads[0].split("- name:", 1)[0]
        self.assertIn("name: osrb-compliance", upload)
        # v4 artifacts are immutable: without overwrite the second upload of
        # the run fails and the triage outputs are silently absent.
        self.assertIn("overwrite: true", upload)
        for artifact_file in ("triage-comment.md", "triage-verdicts.json"):
            self.assertIn(artifact_file, job, artifact_file)


if __name__ == "__main__":
    unittest.main()
