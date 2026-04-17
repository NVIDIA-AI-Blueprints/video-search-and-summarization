#!/usr/bin/env python3
"""Reconcile GitHub commit statuses with downstream GitLab pipelines.

Iterates recent commits on configured branches, finds the ones with a
pending ``ci/downstream-pipeline`` status, and queries GitLab for the
linked pipeline. If the pipeline is in a terminal state, the GitHub
commit status is flipped to success or failure; if still running, it is
left pending for a future manual invocation to pick up.

Designed to run on demand (``workflow_dispatch``). Exit code:

* ``0`` when every inspected pipeline reached SUCCESS.
* ``1`` when any inspected pipeline FAILED or is still NOT_READY, or
  when none could be looked up.

Each invocation is idempotent: already-resolved commit statuses are
skipped.
"""

from __future__ import annotations

import json
import os
import re
import sys
from typing import Any
from urllib.error import ContentTooShortError
from urllib.error import HTTPError
from urllib.error import URLError
from urllib.parse import quote
from urllib.request import Request
from urllib.request import urlopen

DOWNSTREAM_STATUS_CONTEXT = "ci/downstream-pipeline"
PIPELINE_ID_PATTERN = re.compile(r"pipeline_id=(\d+)")

# GitLab pipeline statuses we consider non-terminal. When the GitLab
# pipeline is in any of these, we leave the GitHub status pending and
# defer to the next cron tick.
GITLAB_IN_PROGRESS_STATUSES = {
    "created",
    "waiting_for_resource",
    "preparing",
    "pending",
    "running",
    "scheduled",
    "manual",
}
GITLAB_SUCCESS_STATUSES = {"success"}
GITLAB_FAILURE_STATUSES = {"failed", "canceled", "skipped"}

# Exceptions that ``http_request`` may raise. Listed as a tuple so callers
# can swallow them without catching bare ``Exception``.
HTTP_ERRORS: tuple[type[BaseException], ...] = (
    HTTPError,
    URLError,
    ContentTooShortError,
    json.JSONDecodeError,
)


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


def http_request(
    action: str,
    url: str,
    headers: dict[str, str],
    data: bytes | None = None,
    method: str | None = None,
) -> Any:
    request = Request(url, data=data, headers=headers, method=method)
    try:
        with urlopen(request) as response:
            payload = response.read().decode("utf-8")
    except HTTPError as exc:
        # Discard the response body: upstream error messages often echo
        # the URL or project path, both of which are treated as secrets.
        _ = exc.read()
        emit_warning(f"{action} failed with status {exc.code}")
        raise
    except (URLError, ContentTooShortError) as exc:
        # Intentionally do not include str(exc); URLError stringifies the
        # target URL, which must not appear in logs.
        _ = exc
        emit_warning(f"{action} failed due to a connection error")
        raise

    if not payload:
        return None
    try:
        return json.loads(payload)
    except json.JSONDecodeError as exc:
        _ = exc
        emit_warning(f"{action} returned unparseable JSON")
        raise


def github_headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "check-downstream-pipeline",
    }


def gitlab_headers(token: str) -> dict[str, str]:
    return {
        "PRIVATE-TOKEN": token,
        "Accept": "application/json",
        "User-Agent": "check-downstream-pipeline",
    }


def list_recent_commits(repo: str, branch: str, github_token: str, limit: int = 30) -> list[str]:
    url = f"https://api.github.com/repos/{repo}/commits?sha={quote(branch, safe='')}&per_page={limit}"
    try:
        response = http_request("List commits", url, github_headers(github_token))
    except HTTP_ERRORS:
        return []
    if not isinstance(response, list):
        return []
    return [item["sha"] for item in response if isinstance(item, dict) and "sha" in item]


def get_pending_status(repo: str, sha: str, github_token: str) -> dict[str, Any] | None:
    url = f"https://api.github.com/repos/{repo}/commits/{sha}/statuses?per_page=100"
    try:
        statuses = http_request("List statuses", url, github_headers(github_token))
    except HTTP_ERRORS:
        return None
    if not isinstance(statuses, list):
        return None
    # GitHub orders statuses newest-first. The first entry for our context
    # is the current state.
    for status in statuses:
        if (
            isinstance(status, dict)
            and status.get("context") == DOWNSTREAM_STATUS_CONTEXT
        ):
            if status.get("state") == "pending":
                return status
            # Most recent state for this context is terminal; nothing to do.
            return None
    return None


def update_github_status(
    repo: str,
    sha: str,
    github_token: str,
    state: str,
    target_url: str,
    description: str,
) -> bool:
    url = f"https://api.github.com/repos/{repo}/statuses/{sha}"
    body = json.dumps(
        {
            "state": state,
            "target_url": target_url,
            "description": description[:140],
            "context": DOWNSTREAM_STATUS_CONTEXT,
        }
    ).encode("utf-8")
    headers = github_headers(github_token)
    headers["Content-Type"] = "application/json"
    try:
        http_request("Update status", url, headers, data=body, method="POST")
    except HTTP_ERRORS:
        return False
    return True


def parse_pipeline_id(description: str | None) -> int | None:
    if not description:
        return None
    match = PIPELINE_ID_PATTERN.search(description)
    if not match:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None


def fetch_gitlab_project_id(base_url: str, token: str, project_path: str) -> int | None:
    encoded = quote(project_path, safe="")
    try:
        response = http_request(
            "GitLab project lookup",
            f"{base_url}/projects/{encoded}",
            gitlab_headers(token),
        )
    except HTTP_ERRORS:
        return None
    if not isinstance(response, dict):
        return None
    try:
        return int(response["id"])
    except (KeyError, TypeError, ValueError):
        return None


def fetch_gitlab_pipeline(
    base_url: str,
    token: str,
    project_id: int,
    pipeline_id: int,
) -> dict[str, Any] | None:
    try:
        response = http_request(
            "GitLab pipeline lookup",
            f"{base_url}/projects/{project_id}/pipelines/{pipeline_id}",
            gitlab_headers(token),
        )
    except HTTP_ERRORS:
        return None
    return response if isinstance(response, dict) else None


def classify(pipeline: dict[str, Any]) -> tuple[str, str]:
    """Return ``(result, note)`` where result is one of
    ``NOT_READY``, ``SUCCESS``, ``FAIL``, based purely on the GitLab
    pipeline status. Non-terminal states defer to the next cron tick.
    """
    status = str(pipeline.get("status") or "").lower()

    if status in GITLAB_SUCCESS_STATUSES:
        return "SUCCESS", status
    if status in GITLAB_FAILURE_STATUSES:
        return "FAIL", status
    if status in GITLAB_IN_PROGRESS_STATUSES:
        return "NOT_READY", status
    # Unknown / missing status - treat as not ready so a human can investigate.
    return "NOT_READY", f"unknown status '{status}'"


def append_summary(lines: list[str]) -> None:
    path = os.environ.get("GITHUB_STEP_SUMMARY", "").strip()
    if not path:
        return
    with open(path, "a", encoding="utf-8") as summary_file:
        summary_file.write("\n".join(lines) + "\n")


def main() -> int:
    raw_url = require_env("DOWNSTREAM_CI_URL")
    base_url = api_base_url(raw_url)
    gitlab_token = require_env("DOWNSTREAM_CI_TOKEN")
    project_path = require_env("DOWNSTREAM_PROJECT_PATH")
    github_token = require_env("GITHUB_TOKEN")
    repo = require_env("GITHUB_REPOSITORY")

    # Mask the downstream host, API base, project path, and every path
    # segment. These must not appear in job logs or step summaries.
    for value in (raw_url, base_url, gitlab_token, project_path):
        add_mask(value)
    for segment in project_path.split("/"):
        add_mask(segment)

    branches_env = os.environ.get("FOLLOWUP_BRANCHES", "main,develop").strip()
    branches = [b.strip() for b in branches_env.split(",") if b.strip()]
    commit_limit = int(os.environ.get("FOLLOWUP_COMMIT_LIMIT", "30"))

    project_id = fetch_gitlab_project_id(base_url, gitlab_token, project_path)
    if project_id is None:
        emit_error("Could not resolve downstream project id")
        return 1

    seen_shas: set[str] = set()
    summary_rows: list[str] = []
    # Exit code policy: 0 only if every inspected pipeline reached
    # SUCCESS. Any FAIL or NOT_READY (still running, unknown, etc.)
    # causes the workflow run itself to fail.
    non_success_count = 0
    checked_count = 0

    for branch in branches:
        for sha in list_recent_commits(repo, branch, github_token, limit=commit_limit):
            if sha in seen_shas:
                continue
            seen_shas.add(sha)

            pending = get_pending_status(repo, sha, github_token)
            if pending is None:
                continue

            pipeline_id = parse_pipeline_id(str(pending.get("description") or ""))
            pipeline_url = str(pending.get("target_url") or "")
            if pipeline_id is None:
                emit_warning(f"{sha[:8]}: pending status missing pipeline_id marker; skipping")
                non_success_count += 1
                continue

            pipeline = fetch_gitlab_pipeline(base_url, gitlab_token, project_id, pipeline_id)
            if pipeline is None:
                emit_warning(f"{sha[:8]}: could not fetch pipeline {pipeline_id}")
                non_success_count += 1
                continue

            checked_count += 1
            result, note = classify(pipeline)
            effective_url = str(pipeline.get("web_url") or pipeline_url)
            # Mask the pipeline web_url before anything else could echo it.
            if effective_url:
                add_mask(effective_url)

            # Log identifiers only: commit short SHA, GitLab pipeline id,
            # classification, and note. No URLs, no project paths.
            print(f"{sha[:8]} pipeline {pipeline_id}: {result} ({note})")
            summary_rows.append(f"| `{sha[:8]}` | {pipeline_id} | {result} | {note} |")

            if result == "SUCCESS":
                update_github_status(
                    repo, sha, github_token,
                    state="success",
                    target_url=effective_url,
                    description=f"pipeline_id={pipeline_id} downstream pipeline passed",
                )
            elif result == "FAIL":
                non_success_count += 1
                update_github_status(
                    repo, sha, github_token,
                    state="failure",
                    target_url=effective_url,
                    description=f"pipeline_id={pipeline_id} downstream pipeline {note} - see logs",
                )
            else:  # NOT_READY
                non_success_count += 1
                # Leave the commit status as pending; next manual run can retry.

    if checked_count == 0:
        append_summary([
            "### Downstream follow-up",
            "",
            "No pending downstream pipelines were found.",
        ])
    else:
        header = [
            "### Downstream follow-up",
            "",
            "| Commit | Pipeline | Result | Note |",
            "| --- | --- | --- | --- |",
        ]
        footer = [
            "",
            "Click through the `ci/downstream-pipeline` commit status on the commit to open the pipeline.",
        ]
        append_summary(header + summary_rows + footer)

    return 1 if non_success_count > 0 else 0


if __name__ == "__main__":
    raise SystemExit(main())
