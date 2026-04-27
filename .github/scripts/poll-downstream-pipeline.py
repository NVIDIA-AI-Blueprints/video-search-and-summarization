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
* ``ALLOWED_FAILURE: <job name>`` when a job fails but has
  ``allow_failure: true`` (i.e. GitLab still counts it as non-fatal).
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


def main() -> int:
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
    start = time.monotonic()
    tick = 0

    while True:
        tick += 1
        jobs = fetch_all_jobs(base_url, token, project_id, pipeline_id)
        pipeline = fetch_pipeline(base_url, token, project_id, pipeline_id) or {}

        for job in latest_attempt_per_name(jobs):
            name = str(job.get("name") or "<unnamed>")
            status = str(job.get("status") or "").lower()
            allow_failure = bool(job.get("allow_failure"))

            if status == "failed" and not allow_failure:
                print(f"FAIL: {name}")
                write_summary([
                    "### Downstream pipeline result",
                    "",
                    f"- Failed job: `{name}`",
                    f"- Successful jobs so far: {len(seen_success)}",
                ])
                return 1

            if status == "canceled":
                print(f"CANCELED: {name}")
                write_summary([
                    "### Downstream pipeline result",
                    "",
                    f"- Canceled job: `{name}`",
                    f"- Successful jobs so far: {len(seen_success)}",
                ])
                return 1

            if status == "failed" and allow_failure:
                if name not in seen_allowed_failure:
                    seen_allowed_failure.add(name)
                    print(f"ALLOWED_FAILURE: {name}")

            if status == "success":
                if name not in seen_success:
                    seen_success.add(name)
                    print(f"SUCCESS: {name}")

        pipeline_status = str(pipeline.get("status") or "").lower()

        if pipeline_status == "success":
            print(
                f"Downstream pipeline #{pipeline_id} finished: "
                f"{len(seen_success)} succeeded, {len(seen_allowed_failure)} allowed failures"
            )
            summary = [
                "### Downstream pipeline result",
                "",
                "- **Outcome:** success",
                f"- **Succeeded jobs:** {len(seen_success)}",
            ]
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

        elapsed = time.monotonic() - start
        if elapsed > max_duration:
            emit_error(
                f"Polling timed out after {int(elapsed)}s "
                f"(pipeline status: '{pipeline_status}')"
            )
            return 1

        time.sleep(poll_interval)


if __name__ == "__main__":
    raise SystemExit(main())
