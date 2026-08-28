#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Cancel a downstream GitLab pipeline that this GitHub job already started.

Used when the GitHub Actions job is cancelled (PR closed, superseded
mirror push, or a human cancel). The poller is killed; GitLab would
otherwise keep running.

GitHub only publishes a step's ``GITHUB_OUTPUT`` when that step finishes.
The trigger also fsyncs a handoff file. If cancel hits after GitLab has
created the pipeline but before the id is on disk, cleanup lists recent
pipelines on the recorded ref and matches this trigger attempt's own
correlation token — never the ref/SHA pair, which a concurrent run of the
same commit would also carry. A sibling pipeline that finishes or 404s
while its variables are fetched is skipped so discovery can still reach
this run's pipeline. Transient 5xx or connection errors while listing
pipelines are retried; they do not abort the search on the first blip.
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime
from datetime import timedelta
from datetime import timezone
from pathlib import Path
from typing import Any
from urllib.error import ContentTooShortError
from urllib.error import HTTPError
from urllib.error import URLError
from urllib.parse import quote
from urllib.request import Request
from urllib.request import urlopen

sys.path.insert(0, str(Path(__file__).resolve().parent))

from trigger_downstream_pipeline import add_mask  # noqa: E402
from trigger_downstream_pipeline import api_base_url  # noqa: E402
from trigger_downstream_pipeline import connection_error_detail  # noqa: E402
from trigger_downstream_pipeline import CORRELATION_VARIABLE  # noqa: E402
from trigger_downstream_pipeline import emit_error  # noqa: E402
from trigger_downstream_pipeline import handoff_path  # noqa: E402
from trigger_downstream_pipeline import require_env  # noqa: E402

ACTIVE_PIPELINE_STATUSES = (
    "created",
    "waiting_for_resource",
    "preparing",
    "pending",
    "running",
    "scheduled",
)

# Listing/discovery should retry these rather than abort the outer search loop.
TRANSIENT_HTTP = frozenset({408, 429, 500, 502, 503, 504})


class GitLabTransientError(Exception):
    """GitLab was unreachable or returned a transient error; retry discovery."""


def cancel_pipeline(
    base_url: str,
    token: str,
    pipeline_id: int,
    *,
    project_id: int | None = None,
    project_path: str = "",
    open_func: Any = urlopen,
) -> str:
    """POST GitLab's pipeline cancel. 404/409 mean it is already gone."""
    project = encode_project(project_id=project_id, project_path=project_path)
    url = f"{base_url}/projects/{project}/pipelines/{int(pipeline_id)}/cancel"
    request = Request(
        url,
        data=b"",
        method="POST",
        headers={
            "PRIVATE-TOKEN": token,
            "Accept": "application/json",
        },
    )
    try:
        with open_func(request) as response:
            response.read()
    except HTTPError as exc:
        if exc.code in {404, 409}:
            return f"already finished ({exc.code})"
        emit_error(f"Pipeline cancel failed with status {exc.code}")
        raise SystemExit(1) from exc
    except (URLError, ContentTooShortError) as exc:
        emit_error(
            "Pipeline cancel failed due to a connection error: "
            + connection_error_detail(exc)
        )
        raise SystemExit(1) from exc
    return "cancelled"


def encode_project(*, project_id: int | None = None, project_path: str = "") -> str:
    if project_id is not None:
        return str(int(project_id))
    if project_path:
        return quote(project_path, safe="")
    emit_error("Need a GitLab project id or project path to cancel")
    raise SystemExit(1)


def load_handoff() -> dict[str, str]:
    path = Path(handoff_path())
    if not path.is_file():
        return {}
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}
    if not isinstance(loaded, dict):
        return {}
    payload: dict[str, str] = {}
    for key, value in loaded.items():
        if isinstance(key, str) and isinstance(value, (str, int)) and str(value):
            payload[key] = str(value)
    return payload


def resolve_pipeline_ids() -> tuple[str, str]:
    """Prefer step env; fall back to the trigger step's fsynced handoff file."""
    env_project = os.environ.get("DOWNSTREAM_PROJECT_ID", "").strip()
    env_pipeline = os.environ.get("DOWNSTREAM_PIPELINE_ID", "").strip()
    if env_project and env_pipeline:
        return env_project, env_pipeline
    payload = load_handoff()
    project_id = env_project or payload.get("project_id", "")
    pipeline_id = env_pipeline or payload.get("pipeline_id", "")
    return project_id, pipeline_id


def parse_gitlab_time(value: str) -> datetime | None:
    text = value.strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def created_at_is_recent(created_at: str, started_at: str, slack_seconds: int = 60) -> bool:
    created = parse_gitlab_time(created_at)
    started = parse_gitlab_time(started_at)
    if created is None or started is None:
        return True
    return created >= started - timedelta(seconds=slack_seconds)


def pipeline_variables_match(variables: list[Any], correlation_id: str) -> bool:
    """Match only this trigger attempt's own correlation token.

    Matching on ref + submodule SHA alone would also match a concurrent
    run of the same commit and cancel work this job does not own.
    """
    if not correlation_id:
        return False
    for item in variables:
        if not isinstance(item, dict):
            continue
        if (
            item.get("key") == CORRELATION_VARIABLE
            and str(item.get("value") or "") == correlation_id
        ):
            return True
    return False


def matching_pipeline_ids(
    pipelines: list[Any],
    variables_by_id: dict[int, list[Any]],
    *,
    correlation_id: str,
    started_at: str,
) -> list[int]:
    found: list[int] = []
    for pipe in pipelines:
        if not isinstance(pipe, dict):
            continue
        pid = pipe.get("id")
        if not isinstance(pid, int):
            continue
        if not created_at_is_recent(str(pipe.get("created_at") or ""), started_at):
            continue
        if pipeline_variables_match(variables_by_id.get(pid) or [], correlation_id):
            found.append(pid)
    return found


def gitlab_json(
    url: str,
    token: str,
    *,
    method: str = "GET",
    data: bytes | None = None,
    open_func: Any = urlopen,
    ignore_http: frozenset[int] | None = None,
    skip_connection_errors: bool = False,
    retryable: bool = False,
) -> Any:
    request = Request(
        url,
        data=data,
        method=method,
        headers={
            "PRIVATE-TOKEN": token,
            "Accept": "application/json",
        },
    )
    try:
        with open_func(request) as response:
            body = response.read().decode("utf-8")
    except HTTPError as exc:
        if ignore_http and exc.code in ignore_http:
            return []
        if retryable and exc.code in TRANSIENT_HTTP:
            raise GitLabTransientError(
                f"GitLab request failed with status {exc.code}"
            ) from exc
        emit_error(f"GitLab request failed with status {exc.code}")
        raise SystemExit(1) from exc
    except (URLError, ContentTooShortError) as exc:
        if skip_connection_errors:
            return []
        if retryable:
            raise GitLabTransientError(
                "GitLab request failed due to a connection error: "
                + connection_error_detail(exc)
            ) from exc
        emit_error(
            "GitLab request failed due to a connection error: "
            + connection_error_detail(exc)
        )
        raise SystemExit(1) from exc
    if not body:
        return None
    try:
        return json.loads(body)
    except json.JSONDecodeError as exc:
        emit_error("GitLab returned an unexpected response")
        raise SystemExit(1) from exc


def list_ref_pipelines(
    base_url: str,
    token: str,
    project: str,
    ref: str,
    open_func: Any = urlopen,
) -> list[Any]:
    found: list[Any] = []
    for status in ACTIVE_PIPELINE_STATUSES:
        quoted_ref = quote(ref, safe="")
        url = (
            f"{base_url}/projects/{project}/pipelines"
            f"?ref={quoted_ref}&status={status}&per_page=20&order_by=id&sort=desc"
        )
        payload = gitlab_json(url, token, open_func=open_func, retryable=True)
        if isinstance(payload, list):
            found.extend(payload)
    return found


# HTTP statuses that mean "this candidate is gone or temporarily unreadable".
# 401/403 are not included: a token that cannot read variables must fail
# cleanup, not look like "no matching pipeline".
CANDIDATE_SKIP_HTTP = frozenset(
    {400, 404, 408, 409, 410, 422, 429, 500, 502, 503, 504}
)


def fetch_pipeline_variables(
    base_url: str,
    token: str,
    project: str,
    pipeline_id: int,
    open_func: Any = urlopen,
) -> list[Any]:
    """Variables for one pipeline, or empty if that candidate cannot be inspected.

    Discovery lists several live pipelines. One of them finishing (or a
    transient 5xx) before ``/variables`` returns must not abort the search
    for this run's correlation token.
    """
    url = f"{base_url}/projects/{project}/pipelines/{pipeline_id}/variables"
    payload = gitlab_json(
        url,
        token,
        open_func=open_func,
        ignore_http=CANDIDATE_SKIP_HTTP,
        skip_connection_errors=True,
    )
    return payload if isinstance(payload, list) else []


def discover_matching_pipeline_ids(
    base_url: str,
    token: str,
    *,
    project: str,
    ref: str,
    correlation_id: str,
    started_at: str,
    open_func: Any = urlopen,
) -> list[int]:
    pipelines = list_ref_pipelines(base_url, token, project, ref, open_func)
    variables_by_id: dict[int, list[Any]] = {}
    for pipe in pipelines:
        if not isinstance(pipe, dict) or not isinstance(pipe.get("id"), int):
            continue
        pid = int(pipe["id"])
        variables_by_id[pid] = fetch_pipeline_variables(
            base_url, token, project, pid, open_func=open_func
        )
    return matching_pipeline_ids(
        pipelines,
        variables_by_id,
        correlation_id=correlation_id,
        started_at=started_at,
    )


def search_matching_pipeline_ids(
    base_url: str,
    token: str,
    *,
    project: str,
    ref: str,
    correlation_id: str,
    started_at: str,
    attempts: int,
    delay: float,
    open_func: Any = urlopen,
    sleep: Any = time.sleep,
) -> list[int]:
    """Retry discovery on transient GitLab errors; fail if they never clear."""
    last_transient: GitLabTransientError | None = None
    ids: list[int] = []
    total = max(1, attempts)
    for attempt in range(total):
        try:
            ids = discover_matching_pipeline_ids(
                base_url,
                token,
                project=project,
                ref=ref,
                correlation_id=correlation_id,
                started_at=started_at,
                open_func=open_func,
            )
            last_transient = None
        except GitLabTransientError as exc:
            last_transient = exc
            ids = []
        if ids:
            return ids
        if attempt + 1 < total and delay > 0:
            sleep(delay)
    if last_transient is not None:
        emit_error(
            "GitLab listing failed after retries: " + str(last_transient)
        )
        raise SystemExit(1) from last_transient
    return []


def main() -> int:
    raw_url = require_env("DOWNSTREAM_CI_URL")
    token = require_env("DOWNSTREAM_CI_TOKEN")
    project_path = os.environ.get("DOWNSTREAM_PROJECT_PATH", "").strip()
    project_id, pipeline_id = resolve_pipeline_ids()
    handoff = load_handoff()
    base_url = api_base_url(raw_url)
    for value in (raw_url, base_url, token, project_path):
        add_mask(value)

    ids: list[int] = []
    if pipeline_id.isdigit():
        ids = [int(pipeline_id)]
    else:
        correlation_id = handoff.get("correlation_id", "")
        ref = handoff.get("ref", "") or os.environ.get("DOWNSTREAM_REF", "").strip()
        started_at = handoff.get("trigger_started_at", "")
        project = encode_project(
            project_id=int(project_id) if project_id.isdigit() else None,
            project_path=project_path,
        ) if (project_id.isdigit() or project_path) else ""
        if not (correlation_id and ref and started_at and project):
            print("No downstream pipeline id; trigger never published one")
            return 0
        add_mask(correlation_id)
        print(
            "Pipeline id missing after cancel; searching recent GitLab "
            f"pipelines on {ref} for this run's correlation token"
        )
        attempts = int(os.environ.get("DOWNSTREAM_CANCEL_SEARCH_ATTEMPTS", "3"))
        delay = float(os.environ.get("DOWNSTREAM_CANCEL_SEARCH_SECONDS", "2"))
        ids = search_matching_pipeline_ids(
            base_url,
            token,
            project=project,
            ref=ref,
            correlation_id=correlation_id,
            started_at=started_at,
            attempts=attempts,
            delay=delay,
        )
        if not ids:
            print("No matching live downstream pipeline found")
            return 0

    numeric_project = int(project_id) if project_id.isdigit() else None
    for pid in ids:
        outcome = cancel_pipeline(
            base_url,
            token,
            pid,
            project_id=numeric_project,
            project_path=project_path,
        )
        print(f"Downstream pipeline {pid}: {outcome}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
