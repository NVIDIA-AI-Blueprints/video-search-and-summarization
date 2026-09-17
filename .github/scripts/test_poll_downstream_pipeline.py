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


def drive(ticks: list[tuple[list[dict[str, Any]], str]], *, env: dict[str, str] | None = None) -> Run:
    """Run ``main()`` against ``ticks``, a list of ``(jobs, pipeline_status)``.

    The last tick repeats forever, which is what lets a test assert the
    timeout path without waiting for it.
    """
    reads = {"n": 0, "current": 0}

    def fake_fetch_all_jobs(*_args, **_kwargs):
        # main() fetches jobs first, then the pipeline, once per tick.
        # Pin the tick index here so both reads see the same snapshot.
        reads["current"] = min(reads["n"], len(ticks) - 1)
        reads["n"] += 1
        return list(ticks[reads["current"]][0])

    def fake_fetch_pipeline(*_args, **_kwargs):
        return {"status": ticks[reads["current"]][1]}

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
