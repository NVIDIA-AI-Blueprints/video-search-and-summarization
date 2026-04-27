#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Poll a downstream GitLab pipeline and report per-job progress.

Runs inline right after ``trigger-downstream-pipeline.sh`` in the same
GitHub Actions job. Reads the pipeline / project ids from env (set by
the trigger step via ``$GITHUB_OUTPUT``), then polls GitLab every
``POLL_INTERVAL_SECONDS`` (default 120s) until the pipeline reaches a
terminal state.

Reporting rules (printed once per job, no duplicates):

* ``SUCCESS: <job name>`` when a job transitions to status ``success``.
* ``SKIPPED: <job name>`` when a job opted out of running via the
  conventional gate-skip exit code (``exit_code: 75``) while configured
  with ``allow_failure: true``. GitLab still records the job as
  ``failed`` in that case, but our convention is to treat it as a
  deliberate skip rather than a failure or warning.
* ``ALLOWED_FAILURE: <job name>`` when a job fails for any other reason
  but has ``allow_failure: true`` (i.e. GitLab still counts it as
  non-fatal).
* ``FAIL: <job name>`` when any non-``allow_failure`` job reaches status
  ``failed`` - the script exits 1 immediately.
* ``CANCELED: <job name>`` when a job is canceled - the script exits 1
  immediately.

Exit codes:

* ``0`` - pipeline finished with no failures.
* ``1`` - a failing / canceled job was observed, or the poller timed
  out (see ``MAX_POLL_DURATION_SECONDS``).

Retried jobs (GitLab job retry) are handled by de-duping on ``name`` and
keeping only the latest attempt (highest ``id``).
"""

from __future__ import annotations

import json
import os
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

GITLAB_TERMINAL_PIPELINE_STATUSES = {"success", "failed", "canceled", "skipped"}
GITLAB_IN_PROGRESS_JOB_STATUSES = {
    "created",
    "waiting_for_resource",
    "preparing",
    "pending",
    "running",
    "scheduled",
    "manual",
}

# Conventional shell exit code used by gated downstream jobs to opt out
# of running (e.g. "the change does not touch this submodule, skip me").
# The job script exits 75 and is configured with `allow_failure: true`,
# so GitLab marks it `failed + allow_failure: true`. We treat this exact
# combination as a skip. 75 is `EX_TEMPFAIL` from `<sysexits.h>` and is
# not a value emitted by bash/shell on its own (1, 2, 126, 127, 128+),
# so it is an unambiguous, machine-readable marker.
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


def gitlab_request(action: str, url: str, token: str) -> Any:
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
        # Drop response body: GitLab error payloads can echo the URL.
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
        response = gitlab_request("Pipeline lookup", url, token)
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
            response = gitlab_request("Pipeline jobs lookup", url, token)
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
    """Return the shell exit code reported by GitLab for a job, or
    ``None`` if the field is missing/null/non-integer.

    GitLab populates ``exit_code`` only when the job's script actually
    ran and exited (i.e. ``status == "failed"`` from a script failure).
    Successful jobs typically report ``exit_code: null``.
    """
    raw = job.get("exit_code")
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def latest_attempt_per_name(jobs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """GitLab returns every attempt of a retried job. Keep only the
    latest (highest ``id``) per job name."""
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

    Uses raw GitLab statuses (success/running/pending/manual/failed/...)
    so each heartbeat reflects what GitLab is reporting right now,
    independent of the cumulative ``seen_*`` sets used for transitions.
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
            response = gitlab_request(
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
            exit_code = _job_exit_code(job)

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
                # Gated skips: a job that exited with the well-known
                # `GATE_SKIP_EXIT_CODE` while flagged `allow_failure: true`
                # is interpreted as a deliberate skip rather than a
                # warning. Anything else is still surfaced as an
                # allowed failure.
                if exit_code == GATE_SKIP_EXIT_CODE:
                    if name not in seen_skipped:
                        seen_skipped.add(name)
                        print(f"SKIPPED: {name}")
                elif name not in seen_allowed_failure:
                    seen_allowed_failure.add(name)
                    print(f"ALLOWED_FAILURE: {name}")

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

        if pipeline_status in GITLAB_TERMINAL_PIPELINE_STATUSES:
            # Pipeline is terminal but we didn't detect a specific failing
            # job above. This can happen with pipeline-level configuration
            # errors (e.g. invalid `.gitlab-ci.yml`) that GitLab surfaces
            # on the pipeline itself rather than a job.
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
