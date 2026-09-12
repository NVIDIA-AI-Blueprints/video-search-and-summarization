#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Poll a downstream CI pipeline and report per-job progress.

Runs inline right after ``trigger_downstream_pipeline.py`` in the same
GitHub Actions job. Reads the pipeline / project ids from env (set by
the trigger step via ``$GITHUB_OUTPUT``), then polls the downstream
API every ``POLL_INTERVAL_SECONDS`` (default 120s) until the pipeline
reaches a terminal state.

Reporting rules (printed once per job, no duplicates):

* ``SUCCESS: <job name>`` when a job transitions to status ``success``.
* ``SKIPPED: <job name>`` when a job opted out of running via the
  conventional gate-skip exit code (``exit_code: 75``) while configured
  with ``allow_failure: true``. The downstream API still records the
  job as ``failed`` in that case, but our convention is to treat
  ``exit_code == 75`` as a deliberate skip.
* ``ALLOWED_FAILURE: <job name>`` when a job fails with any other exit
  code while still configured with ``allow_failure: true``.
* ``FAIL: <job name>`` when any non-``allow_failure`` job reaches status
  ``failed``.
* ``CANCELED: <job name>`` when a job is canceled.

``FAIL`` / ``CANCELED`` are announcements, not verdicts: polling
continues until the pipeline itself reaches a terminal state, and the
exit status is then decided from that final snapshot. A single job
failing no longer discards the result of every job still running
alongside it, and because the verdict is re-read at the end, a job that
failed and was retried into a pass is not held against the pipeline.
The check still goes red for any job that is genuinely failed or
canceled once the pipeline is done.

Not every blocking failure is the pull request's fault, though, and the
report used to render them identically: a 15-second git abort in
``get_sources`` on a dirty runner and a genuine product assertion both
came out as ``FAIL: <job>``, with the downstream URL masked so the
author could not click through and see which. So the terminal snapshot
classifies each blocking failure (see ``infra_failure_cause``):

* **Infrastructure** - the downstream CI system could not run the job.
  Reported as ``DOWNSTREAM_INFRA``, in words that say plainly this is
  not a verdict on the pull request, and *not* fatal on its own. A
  non-secret triage handle is printed so a maintainer can find the run.
* **Product** - everything else. Still a hard red.

Classification has to prove infrastructure; anything unproven stays a
product failure. A false green hides a regression, a false red costs a
retry.

Resolving the exit code is non-trivial: many downstream API versions
do NOT include ``exit_code`` in either the pipeline-jobs listing or
the per-job detail endpoint, so we fall back to fetching the job's
text trace and parsing the runner's terminal
``ERROR: Job failed: exit code <N>`` line. Resolution per job id is
cached for the lifetime of the poller.

Exit codes:

* ``0`` - pipeline finished with no failures, or its only blocking
  failures were classified as downstream infrastructure.
* ``1`` - the finished pipeline had a product failure or a canceled
  job, or the poller timed out (see ``MAX_POLL_DURATION_SECONDS``).

Retried jobs are handled by de-duping on ``name`` and keeping only
the latest attempt (highest ``id``).
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from typing import Any
from urllib.error import ContentTooShortError
from urllib.error import HTTPError
from urllib.error import URLError
from urllib.parse import quote
from urllib.request import Request
from urllib.request import urlopen

HTTP_ERRORS: tuple[type[BaseException], ...] = (
    HTTPError,
    URLError,
    ContentTooShortError,
    json.JSONDecodeError,
)

TERMINAL_PIPELINE_STATUSES = {"success", "failed", "canceled", "skipped"}
IN_PROGRESS_JOB_STATUSES = {
    "created",
    "waiting_for_resource",
    "preparing",
    "pending",
    "running",
    "scheduled",
    "manual",
}

# Conventional shell exit code used by gated downstream jobs to opt
# out of running (e.g. "the change does not touch this submodule, skip
# me"). The job script exits 75 and is configured with
# ``allow_failure: true``, so the API marks it
# ``failed + allow_failure: true``. We treat this exact combination as
# a skip. 75 is ``EX_TEMPFAIL`` from ``<sysexits.h>`` and is not a
# value emitted by bash/shell on its own (1, 2, 126, 127, 128+), so it
# is an unambiguous, machine-readable marker.
GATE_SKIP_EXIT_CODE = 75


def emit_error(message: str) -> None:
    print(f"::error::{message}", file=sys.stderr)


def emit_warning(message: str) -> None:
    print(f"::warning::{message}", file=sys.stderr)


def add_mask(value: str) -> None:
    if value:
        print(f"::add-mask::{value}")


def require_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        emit_error(f"Missing {name}")
        raise SystemExit(1)
    return value


def api_base_url(raw_url: str) -> str:
    base = raw_url.rstrip("/")
    if not base.endswith("/api/v4"):
        base = f"{base}/api/v4"
    return base


def api_request(action: str, url: str, token: str) -> Any:
    request = Request(
        url,
        headers={
            "PRIVATE-TOKEN": token,
            "Accept": "application/json",
            "User-Agent": "poll-downstream-pipeline",
        },
    )
    try:
        with urlopen(request, timeout=30) as response:
            payload = response.read().decode("utf-8")
    except HTTPError as exc:
        # Drop response body: error payloads can echo the URL.
        _ = exc.read()
        emit_warning(f"{action} failed with status {exc.code}")
        raise
    except (URLError, ContentTooShortError) as exc:
        _ = exc
        emit_warning(f"{action} failed due to a connection error")
        raise

    if not payload:
        return None
    try:
        return json.loads(payload)
    except json.JSONDecodeError:
        emit_warning(f"{action} returned unparseable JSON")
        raise


def fetch_pipeline(base_url: str, token: str, project_id: int, pipeline_id: int) -> dict[str, Any] | None:
    url = f"{base_url}/projects/{project_id}/pipelines/{pipeline_id}"
    try:
        response = api_request("Pipeline lookup", url, token)
    except HTTP_ERRORS:
        return None
    return response if isinstance(response, dict) else None


def fetch_all_jobs(base_url: str, token: str, project_id: int, pipeline_id: int) -> list[dict[str, Any]]:
    """Return every job for a pipeline, walking pagination."""
    jobs: list[dict[str, Any]] = []
    page = 1
    per_page = 100
    while True:
        url = (
            f"{base_url}/projects/{project_id}/pipelines/{pipeline_id}/jobs"
            f"?per_page={per_page}&page={page}"
        )
        try:
            response = api_request("Pipeline jobs lookup", url, token)
        except HTTP_ERRORS:
            return jobs
        if not isinstance(response, list) or not response:
            break
        jobs.extend([j for j in response if isinstance(j, dict)])
        if len(response) < per_page:
            break
        page += 1
        # Defensive: never walk more than 50 pages (5000 jobs).
        if page > 50:
            emit_warning("Stopped paginating jobs at page 50")
            break
    return jobs


def _job_exit_code(job: dict[str, Any]) -> int | None:
    """Return the shell exit code reported for a job, or ``None`` if
    the field is missing/null/non-integer.

    The downstream API populates ``exit_code`` only when the job's
    script actually ran and exited (i.e. ``status == "failed"`` from a
    script failure). Successful jobs typically report
    ``exit_code: null``.
    """
    raw = job.get("exit_code")
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _job_id(job: dict[str, Any]) -> int | None:
    raw = job.get("id")
    try:
        return int(raw) if raw is not None else None
    except (TypeError, ValueError):
        return None


def fetch_job_detail(
    base_url: str,
    token: str,
    project_id: int,
    job_id: int,
) -> dict[str, Any] | None:
    """Fetch a single job's detail payload.

    The pipeline-level ``/pipelines/:pid/jobs`` listing intentionally
    omits a number of fields (notably ``exit_code`` on some API
    versions). On versions that do return ``exit_code`` in this
    payload, this is enough to classify the job.
    """
    url = f"{base_url}/projects/{project_id}/jobs/{job_id}"
    try:
        response = api_request("Job detail lookup", url, token)
    except HTTP_ERRORS:
        return None
    return response if isinstance(response, dict) else None


# Runner-emitted terminal line, e.g.:
#   "ERROR: Job failed: exit code 75"
# (the "ERROR:" portion may be wrapped in ANSI color escape codes,
# but the literal text is always present).
_TRACE_EXIT_CODE_RE = re.compile(r"Job failed: exit code (\d+)")


def fetch_job_trace(
    base_url: str,
    token: str,
    project_id: int,
    job_id: int,
) -> str | None:
    """Fetch a job's raw text trace, or ``None`` if it is unavailable."""
    url = f"{base_url}/projects/{project_id}/jobs/{job_id}/trace"
    request = Request(
        url,
        headers={
            "PRIVATE-TOKEN": token,
            "Accept": "text/plain",
            "User-Agent": "poll-downstream-pipeline",
        },
    )
    try:
        with urlopen(request, timeout=30) as response:
            return response.read().decode("utf-8", errors="replace")
    except (HTTPError, URLError, ContentTooShortError) as exc:
        emit_warning(f"Job trace lookup failed: {exc}")
        return None


def fetch_job_trace_exit_code(
    base_url: str,
    token: str,
    project_id: int,
    job_id: int,
) -> int | None:
    """Fetch the job's text trace and parse the runner's terminal
    "Job failed: exit code <N>" line.

    Used as a fallback when neither the listing nor the per-job
    detail endpoint surfaces ``exit_code``. We only call this for
    ``failed + allow_failure: true`` jobs and cache the result, so the
    extra request cost is bounded.
    """
    payload = fetch_job_trace(base_url, token, project_id, job_id)
    if payload is None:
        return None

    # The runner always prints this near the end. Use the LAST match
    # so a literal occurrence of the phrase earlier in the log (e.g.
    # echoed by user code) cannot mask the runner's terminal line.
    matches = _TRACE_EXIT_CODE_RE.findall(payload)
    if not matches:
        return None
    try:
        return int(matches[-1])
    except (TypeError, ValueError):
        return None


def resolve_exit_code(
    job: dict[str, Any],
    base_url: str,
    token: str,
    project_id: int,
    cache: dict[int, int | None],
) -> int | None:
    """Best-effort resolve a job's ``exit_code``.

    Resolution order:

    1. Listing payload (free, but most API versions don't include it).
    2. Per-job detail endpoint (some API versions include it).
    3. Job trace endpoint (parsed from the runner's terminal line).

    Results are cached by job id so a given job is resolved at most
    once for the lifetime of the poller.
    """
    direct = _job_exit_code(job)
    if direct is not None:
        return direct

    job_id = _job_id(job)
    if job_id is None:
        return None

    if job_id in cache:
        return cache[job_id]

    detail = fetch_job_detail(base_url, token, project_id, job_id)
    resolved = _job_exit_code(detail) if isinstance(detail, dict) else None
    if resolved is None:
        resolved = fetch_job_trace_exit_code(base_url, token, project_id, job_id)
    cache[job_id] = resolved
    return resolved


def latest_attempt_per_name(jobs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The downstream API returns every attempt of a retried job.
    Keep only the latest (highest ``id``) per job name."""
    by_name: dict[str, dict[str, Any]] = {}
    for job in jobs:
        name = str(job.get("name") or "")
        if not name:
            continue
        existing = by_name.get(name)
        try:
            job_id = int(job.get("id") or 0)
            existing_id = int(existing.get("id") or 0) if existing else -1
        except (TypeError, ValueError):
            job_id = 0
            existing_id = -1
        if existing is None or job_id > existing_id:
            by_name[name] = job
    return list(by_name.values())


def blocking_jobs(
    jobs: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split ``jobs`` into the failed and canceled ones that must fail
    the check, ignoring failures the pipeline marked ``allow_failure``.

    Called on the final snapshot rather than accumulated across ticks so
    the verdict reflects each job's last attempt.
    """
    failed: list[dict[str, Any]] = []
    canceled: list[dict[str, Any]] = []
    for job in jobs:
        status = str(job.get("status") or "").lower()
        if status == "failed" and not bool(job.get("allow_failure")):
            failed.append(job)
        elif status == "canceled":
            canceled.append(job)
    return failed, canceled


# Failure reasons the downstream API attributes to its own runners rather
# than to the job's script. Deliberately excludes `job_execution_timeout`:
# a job that ran to its wall clock may well have been hung by the product.
INFRA_FAILURE_REASONS = frozenset({"runner_system_failure", "stuck_or_timeout_failure"})

# A `preflight` job that dies this fast never reached the product: those
# jobs resolve credentials and check out sources, so a sub-minute-and-a-half
# failure is environment, not code.
PREFLIGHT_INFRA_MAX_SECONDS = 90

# The runner brackets each phase of a job with a marker it writes into the
# trace: `section_start:<unix ts>:<section name>`. `step_script` is the
# section that runs the job's own `script:`, so a finished trace without it
# proves the job died in the runner's own setup (get_sources, restore_cache,
# artifact download) before a single line of product code ran.
_STEP_SCRIPT_SECTION_RE = re.compile(r"section_start:\d+:step_script")


def failed_before_step_script(
    job: dict[str, Any],
    base_url: str,
    token: str,
    project_id: int,
    cache: dict[int, bool],
) -> bool:
    """Whether the job's trace shows it never entered ``step_script``.

    Returns ``False`` when the trace cannot be fetched or read, so an API
    problem can never upgrade a product failure into an infra failure.
    """
    job_id = _job_id(job)
    if job_id is None:
        return False
    if job_id in cache:
        return cache[job_id]
    trace = fetch_job_trace(base_url, token, project_id, job_id)
    # An empty trace is not evidence of anything: the runner may simply
    # have failed to upload it.
    verdict = bool(trace) and not _STEP_SCRIPT_SECTION_RE.search(trace or "")
    cache[job_id] = verdict
    return verdict


def infra_failure_cause(
    job: dict[str, Any],
    base_url: str,
    token: str,
    project_id: int,
    cache: dict[int, bool],
) -> str | None:
    """Why a failed job looks like downstream infrastructure rather than
    the product under test, or ``None`` if it does not.

    Conservative by construction: every branch here has to *prove*
    infrastructure, and anything unproven stays a product failure and
    keeps the check red. A false green hides a real regression; a false
    red costs a retry.
    """
    reason = str(job.get("failure_reason") or "").strip().lower()
    if reason in INFRA_FAILURE_REASONS:
        # `describe_job` already prints the reason, so name the consequence.
        return "reported by the downstream CI system, not by the job's script"
    if reason != "script_failure":
        return None

    duration = job.get("duration")
    stage = str(job.get("stage") or "").strip().lower()
    if (
        "preflight" in stage
        and isinstance(duration, (int, float))
        and not isinstance(duration, bool)
        and duration < PREFLIGHT_INFRA_MAX_SECONDS
    ):
        return f"{reason} after {_format_hms(duration)} in stage {stage}"

    if failed_before_step_script(job, base_url, token, project_id, cache):
        return "failed before step_script, so no product code ran"
    return None


def partition_infra_failures(
    failed: list[dict[str, Any]],
    base_url: str,
    token: str,
    project_id: int,
    cache: dict[int, bool],
) -> tuple[list[tuple[dict[str, Any], str]], list[dict[str, Any]]]:
    """Split blocking failures into ``(infra, product)``.

    ``infra`` pairs each job with the cause string that classified it.
    """
    infra: list[tuple[dict[str, Any], str]] = []
    product: list[dict[str, Any]] = []
    for job in failed:
        cause = infra_failure_cause(job, base_url, token, project_id, cache)
        if cause:
            infra.append((job, cause))
        else:
            product.append(job)
    return infra, product


def triage_handle(pipeline_id: int, pipeline: dict[str, Any]) -> str:
    """A non-secret identifier a human can use to find the downstream run.

    The downstream host and project path are masked, so the author cannot
    click through. These identifiers are not secrets: the pipeline IID is
    already printed by the trigger step, and the GitHub run id / attempt
    are the prefix of the ``VSS_TRIGGER_CORRELATION_ID`` sent with the
    trigger, which is what a maintainer greps for downstream.
    """
    iid = pipeline.get("iid") or pipeline_id
    parts = [f"downstream pipeline IID {iid}"]
    correlation = os.environ.get("VSS_TRIGGER_CORRELATION_ID", "").strip()
    if not correlation:
        run_id = os.environ.get("GITHUB_RUN_ID", "").strip()
        attempt = os.environ.get("GITHUB_RUN_ATTEMPT", "1").strip() or "1"
        correlation = f"gh-{run_id}-{attempt}-*" if run_id else ""
    if correlation:
        parts.append(f"VSS_TRIGGER_CORRELATION_ID {correlation}")
    return ", ".join(parts)


def describe_job(job: dict[str, Any]) -> str:
    """One-line job description for the failure report.

    Carries the stage, duration and failure reason because those are
    what separate a job that ran the product and failed from one that
    died in seconds before it started - a distinction the bare job name
    cannot make.
    """
    name = str(job.get("name") or "<unnamed>")
    details: list[str] = []
    stage = str(job.get("stage") or "").strip()
    if stage:
        details.append(f"stage {stage}")
    duration = job.get("duration")
    if isinstance(duration, (int, float)) and not isinstance(duration, bool):
        details.append(_format_hms(duration))
    reason = str(job.get("failure_reason") or "").strip()
    if reason:
        details.append(reason)
    if not details:
        return name
    return f"{name} ({', '.join(details)})"


def write_summary(lines: list[str]) -> None:
    path = os.environ.get("GITHUB_STEP_SUMMARY", "").strip()
    if not path:
        return
    with open(path, "a", encoding="utf-8") as summary_file:
        summary_file.write("\n".join(lines) + "\n")


def _format_hms(seconds: float) -> str:
    seconds = max(0, int(seconds))
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours:d}h{minutes:02d}m{secs:02d}s"
    return f"{minutes:d}m{secs:02d}s"


def _tick_status_counts(jobs: list[dict[str, Any]]) -> dict[str, int]:
    """Return a status -> count tally for the current snapshot.

    Uses raw API statuses (success/running/pending/manual/failed/...)
    so each heartbeat reflects what the downstream API is reporting
    right now, independent of the cumulative ``seen_*`` sets used for
    transitions.
    """
    counts: dict[str, int] = {}
    for job in jobs:
        status = str(job.get("status") or "unknown").lower()
        counts[status] = counts.get(status, 0) + 1
    return counts


def _format_status_counts(counts: dict[str, int]) -> str:
    if not counts:
        return "no jobs yet"
    # Stable, readable order: terminal states first, then in-progress.
    order = [
        "success",
        "failed",
        "canceled",
        "skipped",
        "running",
        "pending",
        "manual",
        "scheduled",
        "preparing",
        "waiting_for_resource",
        "created",
    ]
    seen: list[str] = []
    parts: list[str] = []
    for key in order:
        if key in counts:
            parts.append(f"{key}={counts[key]}")
            seen.append(key)
    for key, value in sorted(counts.items()):
        if key not in seen:
            parts.append(f"{key}={value}")
    return ", ".join(parts)


def report_terminal_pipeline(
    pipeline_id: int,
    pipeline_status: str,
    jobs: list[dict[str, Any]],
    seen_success: set[str],
    seen_skipped: set[str],
    seen_allowed_failure: set[str],
    pipeline: dict[str, Any],
    base_url: str,
    token: str,
    project_id: int,
    trace_cache: dict[int, bool],
) -> int:
    """Decide the check's verdict from a terminal pipeline snapshot."""
    failed, canceled = blocking_jobs(jobs)
    infra, product = partition_infra_failures(failed, base_url, token, project_id, trace_cache)
    handle = triage_handle(pipeline_id, pipeline)

    if product or canceled:
        for job in product:
            emit_error(f"Downstream job failed: {describe_job(job)}")
        for job, cause in infra:
            emit_warning(f"Downstream infrastructure failure (not a product failure): {describe_job(job)} - {cause}")
        for job in canceled:
            emit_error(f"Downstream job canceled: {describe_job(job)}")
        print(
            f"Downstream pipeline #{pipeline_id} finished '{pipeline_status}': "
            f"{len(product)} failed, {len(infra)} infrastructure, {len(canceled)} canceled, "
            f"{len(seen_success)} succeeded, "
            f"{len(seen_skipped)} skipped, "
            f"{len(seen_allowed_failure)} allowed failures"
        )
        summary = [
            "### Downstream pipeline result",
            "",
            f"- **Outcome:** {pipeline_status}",
        ]
        if product:
            summary.append(f"- **Failed jobs:** {len(product)}")
            summary.extend(f"  - `{describe_job(job)}`" for job in product)
        if infra:
            summary.append(f"- **Infrastructure failures (not product failures):** {len(infra)}")
            summary.extend(f"  - `{describe_job(job)}` - {cause}" for job, cause in infra)
        if canceled:
            summary.append(f"- **Canceled jobs:** {len(canceled)}")
            summary.extend(f"  - `{describe_job(job)}`" for job in canceled)
        summary.append(f"- **Succeeded jobs:** {len(seen_success)}")
        if seen_skipped:
            summary.append(f"- **Skipped jobs (exit {GATE_SKIP_EXIT_CODE}):** {len(seen_skipped)}")
        if seen_allowed_failure:
            summary.append(f"- **Allowed failures:** {len(seen_allowed_failure)}")
        summary.append(f"- **Triage handle:** {handle}")
        write_summary(summary)
        return 1

    if infra:
        # Every blocking failure was the downstream CI system failing to run
        # the job, not the job finding a problem. Say so in words a PR author
        # can act on, and do not paint the check red for it: a red here reads
        # as "your change is broken", which is exactly what this was not.
        print(f"DOWNSTREAM_INFRA: {len(infra)} job(s) could not be run by the downstream CI system.")
        print(
            "This is NOT a verdict on this pull request - no product code failed. "
            "Any FAIL lines above are these infrastructure failures."
        )
        for job, cause in infra:
            print(f"DOWNSTREAM_INFRA: {describe_job(job)} - {cause}")
        print(f"Ask a CI maintainer to retry the downstream pipeline: {handle}")
        emit_warning(
            f"Downstream pipeline could not run {len(infra)} job(s) for infrastructure reasons; "
            f"this is not a verdict on this pull request ({handle})"
        )
        write_summary(
            [
                "### Downstream pipeline result",
                "",
                "- **Outcome:** infrastructure failure, not a product failure",
                "- The downstream CI system could not run the job(s) below. Nothing here says",
                "  this pull request is broken; the work simply did not run.",
                f"- **Jobs not run:** {len(infra)}",
                *(f"  - `{describe_job(job)}` - {cause}" for job, cause in infra),
                f"- **Succeeded jobs:** {len(seen_success)}",
                f"- **Triage handle:** {handle}",
            ]
        )
        return 0

    if pipeline_status == "success":
        print(
            f"Downstream pipeline #{pipeline_id} finished: "
            f"{len(seen_success)} succeeded, "
            f"{len(seen_skipped)} skipped, "
            f"{len(seen_allowed_failure)} allowed failures"
        )
        summary = [
            "### Downstream pipeline result",
            "",
            "- **Outcome:** success",
            f"- **Succeeded jobs:** {len(seen_success)}",
        ]
        if seen_skipped:
            summary.append(f"- **Skipped jobs (exit {GATE_SKIP_EXIT_CODE}):** {len(seen_skipped)}")
        if seen_allowed_failure:
            summary.append(f"- **Allowed failures:** {len(seen_allowed_failure)}")
        write_summary(summary)
        return 0

    # Terminal, but nothing in the snapshot explains it. This happens with
    # pipeline-level configuration errors the downstream API reports on the
    # pipeline rather than on a job, and with a snapshot truncated by an API
    # error. Fail closed either way.
    emit_error(
        f"Downstream pipeline ended with status '{pipeline_status}' and no failing job was observed"
    )
    return 1


def main() -> int:
    # GitHub Actions captures stdout via a pipe, which makes Python's
    # default block-buffered stdout look like nothing is happening for
    # minutes at a time and then emit everything in one burst when the
    # process exits. Force line buffering so each `print()` lands in
    # the runner log as it happens.
    try:
        sys.stdout.reconfigure(line_buffering=True)  # type: ignore[union-attr]
    except (AttributeError, OSError):
        pass

    raw_url = require_env("DOWNSTREAM_CI_URL")
    base_url = api_base_url(raw_url)
    token = require_env("DOWNSTREAM_CI_TOKEN")
    project_path = require_env("DOWNSTREAM_PROJECT_PATH")
    pipeline_id = int(require_env("DOWNSTREAM_PIPELINE_ID"))
    # project_id is emitted by the trigger step; if absent, fall back to
    # a project-path lookup via the same machinery as the trigger script.
    project_id_env = os.environ.get("DOWNSTREAM_PROJECT_ID", "").strip()

    for value in (raw_url, base_url, token, project_path):
        add_mask(value)
    for segment in project_path.split("/"):
        add_mask(segment)

    poll_interval = int(os.environ.get("POLL_INTERVAL_SECONDS", "120"))
    max_duration = int(os.environ.get("MAX_POLL_DURATION_SECONDS", str(240 * 60)))

    if project_id_env:
        try:
            project_id = int(project_id_env)
        except ValueError:
            emit_error("DOWNSTREAM_PROJECT_ID is set but not an integer")
            return 1
    else:
        try:
            response = api_request(
                "Project lookup",
                f"{base_url}/projects/{quote(project_path, safe='')}",
                token,
            )
        except HTTP_ERRORS:
            emit_error("Could not resolve project id")
            return 1
        if not isinstance(response, dict):
            emit_error("Project lookup returned unexpected payload")
            return 1
        project_id = int(response["id"])

    print(
        f"Polling pipeline #{pipeline_id} every {poll_interval}s "
        f"(timeout after {max_duration // 60} min)"
    )

    seen_success: set[str] = set()
    seen_allowed_failure: set[str] = set()
    seen_skipped: set[str] = set()
    # Announcement bookkeeping only: these keep each transition from being
    # printed on every tick. The verdict comes from the terminal snapshot,
    # not from these sets, so a retried job is judged on its last attempt.
    seen_failed: set[str] = set()
    seen_canceled: set[str] = set()
    # Per-job exit-code cache (keyed by job id). Populated lazily when
    # we hit a `failed + allow_failure: true` job and the listing
    # payload doesn't carry `exit_code` (the listing endpoint never
    # does, but the per-job endpoint does).
    exit_code_cache: dict[int, int | None] = {}
    # Per-job "did it reach step_script" cache, populated only on the
    # terminal snapshot when a blocking failure needs classifying.
    trace_cache: dict[int, bool] = {}
    start = time.monotonic()
    tick = 0

    while True:
        tick += 1
        elapsed = time.monotonic() - start
        # Each tick is wrapped in a GitHub Actions log group so the
        # runner UI stays compact while still letting the user expand
        # any individual poll cycle to see what changed.
        print(f"::group::Tick {tick} (elapsed {_format_hms(elapsed)})")

        jobs = fetch_all_jobs(base_url, token, project_id, pipeline_id)
        pipeline = fetch_pipeline(base_url, token, project_id, pipeline_id) or {}
        latest_jobs = latest_attempt_per_name(jobs)

        for job in latest_jobs:
            name = str(job.get("name") or "<unnamed>")
            status = str(job.get("status") or "").lower()
            allow_failure = bool(job.get("allow_failure"))

            if status == "failed" and not allow_failure:
                if name not in seen_failed:
                    seen_failed.add(name)
                    print(f"FAIL: {name}")
                continue

            if status == "canceled":
                if name not in seen_canceled:
                    seen_canceled.add(name)
                    print(f"CANCELED: {name}")
                continue

            if status == "failed" and allow_failure:
                # The listing endpoint omits `exit_code`; resolve it
                # via the per-job detail endpoint (cached) so we can
                # distinguish a gate-skip (exit 75) from an actual
                # allowed failure.
                if name in seen_skipped or name in seen_allowed_failure:
                    continue
                exit_code = resolve_exit_code(job, base_url, token, project_id, exit_code_cache)
                if exit_code == GATE_SKIP_EXIT_CODE:
                    seen_skipped.add(name)
                    print(f"SKIPPED: {name}")
                else:
                    seen_allowed_failure.add(name)
                    suffix = f" (exit {exit_code})" if exit_code is not None else ""
                    print(f"ALLOWED_FAILURE: {name}{suffix}")

            if status == "success":
                if name not in seen_success:
                    seen_success.add(name)
                    print(f"SUCCESS: {name}")

        pipeline_status = str(pipeline.get("status") or "").lower()
        status_counts = _tick_status_counts(latest_jobs)
        # Heartbeat line so the runner shows continuous progress even
        # when no jobs transitioned during this tick.
        print(
            f"[tick {tick}] elapsed={_format_hms(time.monotonic() - start)} "
            f"pipeline={pipeline_status or 'unknown'} "
            f"jobs: {_format_status_counts(status_counts)}"
        )
        print("::endgroup::")

        if pipeline_status in TERMINAL_PIPELINE_STATUSES:
            return report_terminal_pipeline(
                pipeline_id,
                pipeline_status,
                latest_jobs,
                seen_success,
                seen_skipped,
                seen_allowed_failure,
                pipeline,
                base_url,
                token,
                project_id,
                trace_cache,
            )

        if time.monotonic() - start > max_duration:
            emit_error(
                f"Polling timed out after {_format_hms(time.monotonic() - start)} "
                f"(pipeline status: '{pipeline_status}')"
            )
            # Name whatever had already failed, so a timeout report is not
            # silent about failures the poller did observe.
            failed, canceled = blocking_jobs(latest_jobs)
            for job in failed:
                emit_error(f"Downstream job failed before the timeout: {describe_job(job)}")
            for job in canceled:
                emit_error(f"Downstream job canceled before the timeout: {describe_job(job)}")
            return 1

        time.sleep(poll_interval)


if __name__ == "__main__":
    raise SystemExit(main())
