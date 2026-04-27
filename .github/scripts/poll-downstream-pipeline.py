#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Poll a downstream CI pipeline and report per-job progress.

Runs inline right after ``trigger-downstream-pipeline.sh`` in the same
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
  ``failed`` - the script exits 1 immediately.
* ``CANCELED: <job name>`` when a job is canceled - the script exits 1
  immediately.

Resolving the exit code is non-trivial: many downstream API versions
do NOT include ``exit_code`` in either the pipeline-jobs listing or
the per-job detail endpoint, so we fall back to fetching the job's
text trace and parsing the runner's terminal
``ERROR: Job failed: exit code <N>`` line. Resolution per job id is
cached for the lifetime of the poller.

Exit codes:

* ``0`` - pipeline finished with no failures.
* ``1`` - a failing / canceled job was observed, or the poller timed
  out (see ``MAX_POLL_DURATION_SECONDS``).

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
            payload = response.read().decode("utf-8", errors="replace")
    except (HTTPError, URLError, ContentTooShortError) as exc:
        emit_warning(f"Job trace lookup failed: {exc}")
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

    raw_id = job.get("id")
    try:
        job_id = int(raw_id) if raw_id is not None else None
    except (TypeError, ValueError):
        job_id = None
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
    # Per-job exit-code cache (keyed by job id). Populated lazily when
    # we hit a `failed + allow_failure: true` job and the listing
    # payload doesn't carry `exit_code` (the listing endpoint never
    # does, but the per-job endpoint does).
    exit_code_cache: dict[int, int | None] = {}
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
                print(f"FAIL: {name}")
                print("::endgroup::")
                write_summary([
                    "### Downstream pipeline result",
                    "",
                    f"- Failed job: `{name}`",
                    f"- Successful jobs so far: {len(seen_success)}",
                ])
                return 1

            if status == "canceled":
                print(f"CANCELED: {name}")
                print("::endgroup::")
                write_summary([
                    "### Downstream pipeline result",
                    "",
                    f"- Canceled job: `{name}`",
                    f"- Successful jobs so far: {len(seen_success)}",
                ])
                return 1

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

        if pipeline_status in TERMINAL_PIPELINE_STATUSES:
            # Pipeline is terminal but we didn't detect a specific failing
            # job above. This can happen with pipeline-level configuration
            # errors (e.g. an invalid pipeline config) that the
            # downstream API surfaces on the pipeline itself rather
            # than on a specific job.
            emit_error(f"Downstream pipeline ended with status '{pipeline_status}' and no failing job was observed")
            return 1

        if time.monotonic() - start > max_duration:
            emit_error(
                f"Polling timed out after {_format_hms(time.monotonic() - start)} "
                f"(pipeline status: '{pipeline_status}')"
            )
            return 1

        time.sleep(poll_interval)


if __name__ == "__main__":
    raise SystemExit(main())
