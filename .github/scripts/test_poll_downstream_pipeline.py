#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import contextlib
import importlib.util
import io
import os
import tempfile
import unittest
from pathlib import Path
from typing import Any
from typing import ClassVar
from unittest import mock

SCRIPT = Path(__file__).with_name("poll-downstream-pipeline.py")
SPEC = importlib.util.spec_from_file_location("poll_downstream_pipeline", SCRIPT)
assert SPEC and SPEC.loader
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)

BASE_ENV = {
    "DOWNSTREAM_CI_URL": "https://gitlab.example",
    "DOWNSTREAM_CI_TOKEN": "token",
    "DOWNSTREAM_PROJECT_PATH": "group/project",
    "DOWNSTREAM_PIPELINE_ID": "66683019",
    "DOWNSTREAM_PROJECT_ID": "283373",
    "POLL_INTERVAL_SECONDS": "120",
    "MAX_POLL_DURATION_SECONDS": "14400",
}


def job(
    name: str,
    status: str,
    *,
    job_id: int = 0,
    allow_failure: bool = False,
    duration: float | None = None,
    stage: str = "test",
    failure_reason: str | None = None,
) -> dict[str, Any]:
    return {
        "id": job_id or abs(hash((name, status))) % 100000,
        "name": name,
        "status": status,
        "allow_failure": allow_failure,
        "duration": duration,
        "stage": stage,
        "failure_reason": failure_reason,
    }


class Run:
    """Result of driving ``main()`` over a scripted sequence of ticks."""

    def __init__(self, exit_code: int, stdout: str, stderr: str, summary: str, ticks_read: int):
        self.exit_code = exit_code
        self.stdout = stdout
        self.stderr = stderr
        self.summary = summary
        self.ticks_read = ticks_read


def drive(
    ticks: list[tuple[list[dict[str, Any]], str]],
    *,
    env: dict[str, str] | None = None,
    traces: dict[str, str] | None = None,
    pipeline_iid: str = "",
) -> Run:
    """Run ``main()`` against ``ticks``, a list of ``(jobs, pipeline_status)``.

    The last tick repeats forever, which is what lets a test assert the
    timeout path without waiting for it.

    ``traces`` maps job name to that job's trace text. Names absent from
    it have no trace available, which is the conservative default the
    classifier has to fall back on.
    """
    reads = {"n": 0, "current": 0}
    by_id = {j["id"]: j["name"] for jobs, _ in ticks for j in jobs}

    def fake_fetch_job_trace(_base, _token, _project, job_id):
        return (traces or {}).get(by_id.get(job_id, ""))

    def fake_fetch_all_jobs(*_args, **_kwargs):
        # main() fetches jobs first, then the pipeline, once per tick.
        # Pin the tick index here so both reads see the same snapshot.
        reads["current"] = min(reads["n"], len(ticks) - 1)
        reads["n"] += 1
        return list(ticks[reads["current"]][0])

    def fake_fetch_pipeline(*_args, **_kwargs):
        pipeline = {"status": ticks[reads["current"]][1]}
        if pipeline_iid:
            pipeline["iid"] = pipeline_iid
        return pipeline

    # Advance the clock by the poll interval on each sleep so a test that
    # never reaches a terminal state still hits MAX_POLL_DURATION_SECONDS.
    clock = {"t": 0.0}

    def fake_sleep(seconds: float) -> None:
        clock["t"] += seconds

    with tempfile.TemporaryDirectory() as tmp:
        summary_path = Path(tmp) / "summary.md"
        full_env = {**BASE_ENV, **(env or {}), "GITHUB_STEP_SUMMARY": str(summary_path)}
        out, err = io.StringIO(), io.StringIO()
        with (
            mock.patch.dict(os.environ, full_env, clear=False),
            mock.patch.object(module, "fetch_all_jobs", fake_fetch_all_jobs),
            mock.patch.object(module, "fetch_pipeline", fake_fetch_pipeline),
            mock.patch.object(module, "fetch_job_trace", fake_fetch_job_trace),
            mock.patch.object(module.time, "sleep", fake_sleep),
            mock.patch.object(module.time, "monotonic", lambda: clock["t"]),
            contextlib.redirect_stdout(out),
            contextlib.redirect_stderr(err),
        ):
            exit_code = module.main()
        summary = summary_path.read_text(encoding="utf-8") if summary_path.exists() else ""
    return Run(exit_code, out.getvalue(), err.getvalue(), summary, reads["n"])


class AbortOnFirstFailureRegressionTest(unittest.TestCase):
    """The behaviour this change exists to remove.

    Reproduces pipeline 66683019: ``eval-warehouse-bp-wh-2d`` died in 14.7s
    in ``get_sources`` on a dirty runner while every other job was still
    running. The poller used to return at that first tick, two minutes in,
    discarding the ~46 minutes of product testing that then passed.

    No trace is available for the failing job in these ticks, so the infra
    classifier cannot prove infrastructure and the failure stays red. That
    fail-closed default is asserted on its own in ``InfraClassificationTest``.
    """

    TICKS: ClassVar[list[tuple[list[dict[str, Any]], str]]] = [
        (
            [
                job("eval-warehouse-bp-wh-2d", "failed", duration=14.7, failure_reason="script_failure"),
                job("test-base", "running"),
                job("test-search", "running"),
                job("test-alerts-realtime", "running"),
            ],
            "running",
        ),
        (
            [
                job("eval-warehouse-bp-wh-2d", "failed", duration=14.7, failure_reason="script_failure"),
                job("test-base", "success", duration=713),
                job("test-search", "running"),
                job("test-alerts-realtime", "running"),
            ],
            "running",
        ),
        (
            [
                job("eval-warehouse-bp-wh-2d", "failed", duration=14.7, failure_reason="script_failure"),
                job("test-base", "success", duration=713),
                job("test-search", "success", duration=2770),
                job("test-alerts-realtime", "success", duration=1028),
            ],
            "failed",
        ),
    ]

    def test_polls_past_the_first_failure_to_the_end(self):
        run = drive(self.TICKS)
        # Every tick was read: the poller did not bail at the first FAIL.
        self.assertEqual(run.ticks_read, 3)
        # The jobs that finished after the failure were still reported.
        for name in ("test-base", "test-search", "test-alerts-realtime"):
            self.assertIn(f"SUCCESS: {name}", run.stdout)

    def test_still_red_and_names_the_failing_job(self):
        run = drive(self.TICKS)
        self.assertEqual(run.exit_code, 1)
        self.assertIn("FAIL: eval-warehouse-bp-wh-2d", run.stdout)
        self.assertIn("eval-warehouse-bp-wh-2d", run.stderr)

    def test_failure_report_carries_duration_and_reason(self):
        # A 14s death in `get_sources` reads differently from a product
        # failure, so the report has to show more than the job name.
        run = drive(self.TICKS)
        self.assertIn("0m14s", run.stderr)
        self.assertIn("script_failure", run.stderr)
        self.assertIn("eval-warehouse-bp-wh-2d", run.summary)
        self.assertIn("**Succeeded jobs:** 3", run.summary)

    def test_failure_announced_once_not_once_per_tick(self):
        run = drive(self.TICKS)
        self.assertEqual(run.stdout.count("FAIL: eval-warehouse-bp-wh-2d"), 1)


class GenuineFailureIsStillFatalTest(unittest.TestCase):
    """The property that must not regress: a real failing job goes red."""

    def test_single_failed_job_fails_the_check(self):
        run = drive([([job("test-search", "failed", duration=2770)], "failed")])
        self.assertEqual(run.exit_code, 1)
        self.assertIn("test-search", run.stderr)

    def test_every_failing_job_is_reported_not_just_the_first(self):
        run = drive([
            (
                [
                    job("test-lvs", "failed", duration=20),
                    job("eval-warehouse-bp-wh-2d", "failed", duration=14.7),
                    job("test-base", "success", duration=713),
                ],
                "failed",
            )
        ])
        self.assertEqual(run.exit_code, 1)
        self.assertIn("test-lvs", run.stderr)
        self.assertIn("eval-warehouse-bp-wh-2d", run.stderr)
        self.assertIn("2 failed", run.stdout)

    def test_failure_alongside_a_success_pipeline_status_still_fails(self):
        # Never trust a 'success' pipeline status over a failed job in the
        # snapshot: fail closed.
        run = drive([([job("test-base", "failed", duration=713)], "success")])
        self.assertEqual(run.exit_code, 1)

    def test_terminal_pipeline_with_no_failing_job_fails_closed(self):
        # Pipeline-level config errors, and snapshots truncated by an API
        # error, must not read as a pass.
        run = drive([([], "failed")])
        self.assertEqual(run.exit_code, 1)
        self.assertIn("no failing job was observed", run.stderr)

    def test_canceled_job_fails_the_check(self):
        run = drive([([job("test-base", "canceled", duration=5)], "canceled")])
        self.assertEqual(run.exit_code, 1)
        self.assertIn("canceled", run.stderr.lower())

    def test_canceled_job_does_not_abort_the_poll_early(self):
        run = drive([
            ([job("test-base", "canceled"), job("test-search", "running")], "running"),
            ([job("test-base", "canceled"), job("test-search", "success", duration=2770)], "canceled"),
        ])
        self.assertEqual(run.ticks_read, 2)
        self.assertEqual(run.exit_code, 1)
        self.assertIn("SUCCESS: test-search", run.stdout)


class RetryTest(unittest.TestCase):
    def test_job_retried_into_a_pass_is_not_held_against_the_pipeline(self):
        # Verdict comes from the final snapshot, so the first attempt's
        # failure does not survive a successful retry.
        run = drive([
            ([job("test-base", "failed", job_id=1, duration=20)], "running"),
            (
                [
                    job("test-base", "failed", job_id=1, duration=20),
                    job("test-base", "success", job_id=2, duration=713),
                ],
                "success",
            ),
        ])
        self.assertEqual(run.exit_code, 0)
        self.assertIn("FAIL: test-base", run.stdout)
        self.assertIn("SUCCESS: test-base", run.stdout)

    def test_job_retried_into_a_failure_still_fails(self):
        run = drive([
            (
                [
                    job("test-base", "success", job_id=1, duration=700),
                    job("test-base", "failed", job_id=2, duration=713),
                ],
                "failed",
            )
        ])
        self.assertEqual(run.exit_code, 1)


class GreenPathTest(unittest.TestCase):
    def test_all_success_exits_zero(self):
        run = drive([
            ([job("test-base", "running")], "running"),
            ([job("test-base", "success", duration=713)], "success"),
        ])
        self.assertEqual(run.exit_code, 0)
        self.assertIn("SUCCESS: test-base", run.stdout)
        self.assertIn("- **Outcome:** success", run.summary)

    def test_gate_skip_exit_75_is_a_skip_not_a_failure(self):
        gate = job("brev-script-base", "failed", allow_failure=True, duration=3)
        with mock.patch.object(module, "resolve_exit_code", lambda *_a, **_k: 75):
            run = drive([([gate, job("test-base", "success", duration=713)], "success")])
        self.assertEqual(run.exit_code, 0)
        self.assertIn("SKIPPED: brev-script-base", run.stdout)

    def test_allowed_failure_does_not_fail_the_check(self):
        allowed = job("helm chart parity tests", "failed", allow_failure=True, duration=29)
        with mock.patch.object(module, "resolve_exit_code", lambda *_a, **_k: 1):
            run = drive([([allowed, job("test-base", "success", duration=713)], "success")])
        self.assertEqual(run.exit_code, 0)
        self.assertIn("ALLOWED_FAILURE: helm chart parity tests", run.stdout)


class TimeoutTest(unittest.TestCase):
    def test_timeout_is_still_fatal_and_bounded_by_max_duration(self):
        # Pipeline never terminates; the last tick repeats. 14400s at a
        # 120s interval bounds this at 120 ticks.
        run = drive([([job("test-base", "running")], "running")])
        self.assertEqual(run.exit_code, 1)
        self.assertIn("Polling timed out", run.stderr)
        self.assertLessEqual(run.ticks_read, 122)

    def test_timeout_names_failures_it_already_saw(self):
        run = drive([
            (
                [
                    job("eval-warehouse-bp-wh-2d", "failed", duration=14.7),
                    job("test-search", "running"),
                ],
                "running",
            )
        ])
        self.assertEqual(run.exit_code, 1)
        self.assertIn("Polling timed out", run.stderr)
        self.assertIn("eval-warehouse-bp-wh-2d", run.stderr)

    def test_interval_and_max_duration_are_honoured(self):
        run = drive(
            [([job("test-base", "running")], "running")],
            env={"POLL_INTERVAL_SECONDS": "60", "MAX_POLL_DURATION_SECONDS": "600"},
        )
        self.assertEqual(run.exit_code, 1)
        # 600s at 60s per tick: 10 sleeps, so 11-12 snapshots.
        self.assertLessEqual(run.ticks_read, 12)
        self.assertGreaterEqual(run.ticks_read, 10)

    def test_a_pipeline_that_terminates_does_not_wait_for_the_timeout(self):
        run = drive([([job("test-base", "success", duration=713)], "success")])
        self.assertEqual(run.exit_code, 0)
        self.assertEqual(run.ticks_read, 1)


class BlockingJobsTest(unittest.TestCase):
    def test_allow_failure_is_not_blocking(self):
        failed, canceled = module.blocking_jobs([
            job("a", "failed", allow_failure=True),
            job("b", "failed"),
            job("c", "canceled"),
            job("d", "success"),
            job("e", "skipped"),
            job("f", "running"),
        ])
        self.assertEqual([j["name"] for j in failed], ["b"])
        self.assertEqual([j["name"] for j in canceled], ["c"])

    def test_canceled_is_blocking_even_when_allow_failure(self):
        # A cancel is an interrupted run, not a tolerated outcome.
        failed, canceled = module.blocking_jobs([job("a", "canceled", allow_failure=True)])
        self.assertEqual(failed, [])
        self.assertEqual([j["name"] for j in canceled], ["a"])

    def test_status_case_is_ignored(self):
        failed, _ = module.blocking_jobs([job("a", "FAILED")])
        self.assertEqual([j["name"] for j in failed], ["a"])


# A runner brackets every phase it runs with a `section_start` marker. A
# trace that stops after `get_sources` never reached the job's own script.
TRACE_NO_STEP_SCRIPT = (
    "section_start:1757600000:resolve_secrets\r\x1b[0K"
    "section_end:1757600002:resolve_secrets\r\x1b[0K"
    "section_start:1757600002:get_sources\r\x1b[0K"
    "fatal: could not read Username for 'https://gitlab.example': No such device or address\n"
    "section_end:1757600019:get_sources\r\x1b[0K"
    "ERROR: Job failed: exit code 1\n"
)
TRACE_WITH_STEP_SCRIPT = (
    "section_start:1757600002:get_sources\r\x1b[0K"
    "section_end:1757600019:get_sources\r\x1b[0K"
    "section_start:1757600020:step_script\r\x1b[0K"
    "$ pytest tests/\nE   assert 3 == 4\n"
    "section_end:1757603120:step_script\r\x1b[0K"
    "ERROR: Job failed: exit code 1\n"
)


class InfraClassificationTest(unittest.TestCase):
    """A downstream failure the CI system caused must not read as a
    verdict on the pull request.

    Motivating pair: GitLab pipelines 67253826 and 67393139 emitted the
    same ``FAIL: <job>`` shape as run 34573793808 (a genuine product
    failure) for a 17-second git plumbing error where no product code ran.
    With the downstream URL masked as a secret, the author had nothing to
    tell them apart.
    """

    def _run(self, failing, *, traces=None, extra=()):
        return drive(
            [([failing, *extra, job("test-base", "success", duration=713)], "failed")],
            traces=traces,
            pipeline_iid="67253826",
        )

    def test_runner_system_failure_is_not_a_product_failure(self):
        run = self._run(job("test-search", "failed", duration=8, failure_reason="runner_system_failure"))
        self.assertEqual(run.exit_code, 0)
        self.assertIn("DOWNSTREAM_INFRA", run.stdout)
        self.assertIn("runner_system_failure", run.stdout)

    def test_stuck_or_timeout_failure_is_not_a_product_failure(self):
        run = self._run(job("test-search", "failed", duration=3600, failure_reason="stuck_or_timeout_failure"))
        self.assertEqual(run.exit_code, 0)
        self.assertIn("DOWNSTREAM_INFRA", run.stdout)

    def test_fast_preflight_script_failure_is_infra(self):
        run = self._run(
            job("fetch_vault_creds", "failed", duration=17, stage="preflight", failure_reason="script_failure")
        )
        self.assertEqual(run.exit_code, 0)
        self.assertIn("DOWNSTREAM_INFRA", run.stdout)
        self.assertIn("fetch_vault_creds", run.stdout)

    def test_slow_preflight_script_failure_stays_red(self):
        # Long enough that it may well have run something real.
        run = self._run(
            job("lint_eval_scripts", "failed", duration=240, stage="preflight", failure_reason="script_failure")
        )
        self.assertEqual(run.exit_code, 1)
        self.assertIn("lint_eval_scripts", run.stderr)

    def test_failure_before_step_script_is_infra(self):
        # The 17s get_sources abort: the trace proves no product code ran.
        failing = job(
            "eval-warehouse-bp-wh-2d",
            "failed",
            job_id=901,
            duration=17,
            stage="blueprint eval docker-compose",
            failure_reason="script_failure",
        )
        run = self._run(failing, traces={"eval-warehouse-bp-wh-2d": TRACE_NO_STEP_SCRIPT})
        self.assertEqual(run.exit_code, 0)
        self.assertIn("DOWNSTREAM_INFRA", run.stdout)
        self.assertIn("no product code ran", run.stdout)

    def test_failure_after_step_script_stays_red(self):
        failing = job(
            "test-search",
            "failed",
            job_id=902,
            duration=3113,
            stage="blueprint eval docker-compose",
            failure_reason="script_failure",
        )
        run = self._run(failing, traces={"test-search": TRACE_WITH_STEP_SCRIPT})
        self.assertEqual(run.exit_code, 1)
        self.assertNotIn("DOWNSTREAM_INFRA", run.stdout)
        self.assertIn("test-search", run.stderr)

    def test_unavailable_trace_fails_closed(self):
        # An API problem must never upgrade a product failure into infra.
        run = self._run(job("test-search", "failed", duration=17, failure_reason="script_failure"))
        self.assertEqual(run.exit_code, 1)

    def test_job_execution_timeout_stays_red(self):
        # An 80-minute job that hit its wall clock may have been hung by
        # the product, so this is not classified as infrastructure.
        run = self._run(job("test-search", "failed", duration=4800, failure_reason="job_execution_timeout"))
        self.assertEqual(run.exit_code, 1)
        self.assertIn("job_execution_timeout", run.stderr)

    def test_infra_alongside_a_product_failure_is_still_red(self):
        run = self._run(
            job("fetch_vault_creds", "failed", duration=17, stage="preflight", failure_reason="script_failure"),
            extra=[job("test-search", "failed", duration=3113, failure_reason="job_execution_timeout")],
        )
        self.assertEqual(run.exit_code, 1)
        self.assertIn("1 failed, 1 infrastructure", run.stdout)
        # The infra job is still surfaced, just not as the verdict.
        self.assertIn("fetch_vault_creds", run.stderr)
        self.assertIn("not a product failure", run.stderr)

    def test_infra_alongside_a_canceled_job_is_still_red(self):
        run = self._run(
            job("fetch_vault_creds", "failed", duration=17, stage="preflight", failure_reason="script_failure"),
            extra=[job("test-base-2", "canceled", duration=5)],
        )
        self.assertEqual(run.exit_code, 1)

    def test_infra_report_says_it_is_not_a_verdict_on_the_pr(self):
        run = self._run(job("test-search", "failed", duration=8, failure_reason="runner_system_failure"))
        self.assertIn("NOT a verdict on this pull request", run.stdout)
        self.assertIn("not a product failure", run.summary)

    def test_infra_report_carries_a_non_secret_triage_handle(self):
        run = drive(
            [([job("test-search", "failed", duration=8, failure_reason="runner_system_failure")], "failed")],
            env={"GITHUB_RUN_ID": "34573793808", "GITHUB_RUN_ATTEMPT": "1"},
            pipeline_iid="67253826",
        )
        self.assertIn("67253826", run.stdout)
        self.assertIn("gh-34573793808-1", run.stdout)
        self.assertIn("67253826", run.summary)
        # The handle itself carries no masked value: a masked string is
        # redacted in the runner log, which would defeat the purpose.
        handle = module.triage_handle(66683019, {"iid": "67253826"})
        for key in ("DOWNSTREAM_CI_URL", "DOWNSTREAM_PROJECT_PATH", "DOWNSTREAM_CI_TOKEN"):
            self.assertNotIn(BASE_ENV[key], handle)

    def test_allowed_failures_are_untouched_by_classification(self):
        gate = job("brev-script-base", "failed", allow_failure=True, duration=3, failure_reason="script_failure")
        with mock.patch.object(module, "resolve_exit_code", lambda *_a, **_k: 75):
            run = drive([([gate, job("test-base", "success", duration=713)], "success")])
        self.assertEqual(run.exit_code, 0)
        self.assertIn("SKIPPED: brev-script-base", run.stdout)
        self.assertNotIn("DOWNSTREAM_INFRA", run.stdout)


class InfraFailureCauseTest(unittest.TestCase):
    """``infra_failure_cause`` in isolation, with no trace available."""

    def cause(self, j, trace=None):
        with mock.patch.object(module, "fetch_job_trace", lambda *_a, **_k: trace):
            return module.infra_failure_cause(j, "https://gitlab.example", "token", 1, {})

    def test_runner_system_failure(self):
        cause = self.cause(job("a", "failed", failure_reason="runner_system_failure"))
        self.assertIn("not by the job's script", cause or "")

    def test_reason_matching_is_case_insensitive(self):
        self.assertIsNotNone(self.cause(job("a", "failed", failure_reason="RUNNER_SYSTEM_FAILURE")))

    def test_missing_failure_reason_is_not_infra(self):
        self.assertIsNone(self.cause(job("a", "failed")))
        self.assertIsNone(self.cause({}))

    def test_preflight_boundary_is_exclusive(self):
        at_limit = job("a", "failed", stage="preflight", failure_reason="script_failure", duration=90)
        under = job("a", "failed", stage="preflight", failure_reason="script_failure", duration=89)
        self.assertIsNone(self.cause(at_limit))
        self.assertIsNotNone(self.cause(under))

    def test_preflight_without_a_duration_is_not_infra(self):
        self.assertIsNone(self.cause(job("a", "failed", stage="preflight", failure_reason="script_failure")))

    def test_empty_trace_is_not_evidence(self):
        # A runner that failed to upload its trace tells us nothing.
        j = job("a", "failed", failure_reason="script_failure", duration=17)
        self.assertIsNone(self.cause(j, trace=""))

    def test_trace_is_fetched_once_per_job(self):
        calls = {"n": 0}

        def counting_fetch(*_a, **_k):
            calls["n"] += 1
            return TRACE_NO_STEP_SCRIPT

        j = job("a", "failed", job_id=7, failure_reason="script_failure", duration=17)
        cache: dict[int, bool] = {}
        with mock.patch.object(module, "fetch_job_trace", counting_fetch):
            for _ in range(3):
                module.infra_failure_cause(j, "https://gitlab.example", "token", 1, cache)
        self.assertEqual(calls["n"], 1)


class PartitionInfraFailuresTest(unittest.TestCase):
    def test_splits_infra_from_product(self):
        infra_job = job("a", "failed", failure_reason="runner_system_failure")
        product_job = job("b", "failed", failure_reason="script_failure", duration=3113)
        with mock.patch.object(module, "fetch_job_trace", lambda *_a, **_k: None):
            infra, product = module.partition_infra_failures(
                [infra_job, product_job], "https://gitlab.example", "token", 1, {}
            )
        self.assertEqual([j["name"] for j, _ in infra], ["a"])
        self.assertEqual([j["name"] for j in product], ["b"])

    def test_pairs_each_infra_job_with_its_cause(self):
        with mock.patch.object(module, "fetch_job_trace", lambda *_a, **_k: None):
            infra, _ = module.partition_infra_failures(
                [job("a", "failed", failure_reason="stuck_or_timeout_failure")],
                "https://gitlab.example",
                "token",
                1,
                {},
            )
        self.assertIn("not by the job's script", infra[0][1])


class DescribeJobTest(unittest.TestCase):
    def test_includes_stage_duration_and_reason(self):
        text = module.describe_job(
            job(
                "eval-warehouse-bp-wh-2d",
                "failed",
                duration=14.706675,
                stage="blueprint eval docker-compose",
                failure_reason="script_failure",
            )
        )
        self.assertIn("eval-warehouse-bp-wh-2d", text)
        self.assertIn("stage blueprint eval docker-compose", text)
        self.assertIn("0m14s", text)
        self.assertIn("script_failure", text)

    def test_survives_a_sparse_payload(self):
        self.assertEqual(module.describe_job({}), "<unnamed>")
        self.assertEqual(
            module.describe_job({"name": "x", "duration": None, "stage": "", "failure_reason": None}),
            "x",
        )

    def test_long_duration_is_readable(self):
        self.assertIn("1h21m40s", module.describe_job(job("test-search", "failed", duration=4900, stage="")))


if __name__ == "__main__":
    unittest.main(verbosity=2)
