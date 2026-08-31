#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Locate and await the Build Dev Images run that publishes a commit's GHCR set.

Shared by the downstream gate. Polls the workflow-run API for the run matching an
exact commit and ref, and reports success only on a terminal successful conclusion.
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

API_ROOT = "https://api.github.com"
MAX_API_RESPONSE_BYTES = 64 * 1024 * 1024


class SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Never forward the GitHub bearer token to artifact storage hosts."""

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> urllib.request.Request | None:
        redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
        if redirected is None:
            return None
        source = urllib.parse.urlsplit(req.full_url)
        destination = urllib.parse.urlsplit(newurl)
        if (source.scheme, source.netloc) != (
            destination.scheme,
            destination.netloc,
        ):
            redirected.remove_header("Authorization")
        return redirected


_URL_OPENER = urllib.request.build_opener(SafeRedirectHandler())


def safe_urlopen(request: urllib.request.Request, timeout: int) -> Any:
    return _URL_OPENER.open(request, timeout=timeout)


def enforce_memory_ceiling() -> None:
    """Keep a broken CI helper from exhausting its runner or developer host."""
    try:
        import resource
    except ImportError:
        return
    raw_limit = os.environ.get("GHCR_CANDIDATE_MEMORY_LIMIT_GB", "10").strip()
    try:
        limit_gb = float(raw_limit)
    except ValueError as exc:
        raise ValueError(
            "GHCR_CANDIDATE_MEMORY_LIMIT_GB must be numeric"
        ) from exc
    if limit_gb <= 0:
        return
    requested = int(limit_gb * 1024**3)
    soft, hard = resource.getrlimit(resource.RLIMIT_AS)
    if soft != resource.RLIM_INFINITY:
        requested = min(requested, soft)
    if hard != resource.RLIM_INFINITY:
        requested = min(requested, hard)
    resource.setrlimit(resource.RLIMIT_AS, (requested, requested))


class GitHubApi:
    def __init__(self, token: str, open_func: Any = None):
        self.open_func = open_func or safe_urlopen
        self.headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "User-Agent": "vss-ghcr-candidate-reporter",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    def request(
        self, method: str, path_or_url: str, payload: dict[str, Any] | None = None
    ) -> Any:
        url = (
            path_or_url
            if path_or_url.startswith("https://")
            else f"{API_ROOT}{path_or_url}"
        )
        data = json.dumps(payload).encode() if payload is not None else None
        headers = (
            dict(self.headers)
            if urllib.parse.urlsplit(url).netloc == "api.github.com"
            else {"User-Agent": self.headers["User-Agent"]}
        )
        if data is not None:
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with self.open_func(request, timeout=60) as response:
                body = response.read(MAX_API_RESPONSE_BYTES + 1)
        except urllib.error.HTTPError as exc:
            raise RuntimeError(
                f"GitHub API {method} failed with status {exc.code}"
            ) from exc
        if len(body) > MAX_API_RESPONSE_BYTES:
            raise RuntimeError(
                f"GitHub API response exceeded {MAX_API_RESPONSE_BYTES} bytes"
            )
        content_type = response.headers.get_content_type()
        return json.loads(body) if content_type == "application/json" else body


#: Conclusions that mean the companion build will never produce a release set.
#: Anything else (None while queued or in progress, or a value we do not
#: recognise) is treated as "keep waiting", so an unfamiliar conclusion can only
#: cost time, never a false failure.
#: ``action_required`` is deliberately absent: approval can resume the same run.
TERMINAL_FAILURE_CONCLUSIONS = frozenset(
    {"failure", "cancelled", "timed_out", "startup_failure", "stale",
     "neutral", "skipped"}
)


def select_release_set_run(
    runs: list[dict[str, Any]], sha: str, ref_name: str
) -> dict[str, Any] | None:
    """Newest Build Dev Images run for this exact commit and ref, any conclusion.

    Deliberately not filtered to successes. The caller has to tell three states
    apart: not created yet, still running, and finished unsuccessfully. Filtering
    here collapsed the last two into "keep polling", so a failed companion build
    was indistinguishable from one that had not started and the caller waited out
    its whole budget against a run that would never appear.

    Newest wins so that a rerun supersedes the attempt it replaced.
    """
    matches = [
        run
        for run in runs
        if run.get("head_sha") == sha and run.get("head_branch") == ref_name
    ]
    if not matches:
        return None
    return max(
        matches,
        key=lambda run: (
            str(run.get("created_at") or ""),
            run.get("run_number") or 0,
            run.get("id") or 0,
        ),
    )


def await_build_run(
    api: GitHubApi,
    repository: str,
    sha: str,
    ref_name: str,
    attempts: int,
    interval_seconds: int,
) -> dict[str, Any]:
    """Block until Build Dev Images succeeds for this exact commit and ref.

    Returns the run. Raises on a terminal failure conclusion, or once the
    polling budget is exhausted. The run's success is the completion signal:
    the release set it publishes is consumed inside that workflow, not here.
    """
    query = urllib.parse.urlencode(
        {"head_sha": sha, "branch": ref_name, "per_page": 100}
    )
    run: dict[str, Any] | None = None
    for attempt in range(1, attempts + 1):
        payload = api.request(
            "GET",
            f"/repos/{repository}/actions/workflows/build-dev-images.yml/runs?{query}",
        )
        run = select_release_set_run(payload.get("workflow_runs", []), sha, ref_name)
        if run is not None:
            status = run.get("status")
            conclusion = run.get("conclusion")
            if status != "completed":
                # Queued, in progress, or a re-run in flight. A GitHub re-run
                # reuses the run id and resets status, so an earlier failure
                # must not abort the attempt that replaced it.
                conclusion = None
            if conclusion == "success":
                break
            if conclusion in TERMINAL_FAILURE_CONCLUSIONS:
                # Fail now rather than polling out the budget. The companion run
                # has finished and will never publish a release set.
                raise RuntimeError(
                    f"GHCR build run {run.get('id')} for {sha[:12]} on {ref_name} "
                    f"concluded {conclusion!r}; its GHCR images were not published. "
                    f"See {run.get('html_url') or 'the Build Dev Images run'}."
                )
            # Present but not finished: keep waiting.
        if attempt < attempts:
            state = "not ready" if run is None else f"{run.get('status')}"
            print(
                f"GHCR build run for {sha[:12]} is {state}; "
                f"retrying in {interval_seconds}s ({attempt}/{attempts})",
                flush=True,
            )
            time.sleep(interval_seconds)
        run = None
    if run is None:
        raise RuntimeError(
            f"no GHCR build run for {sha} on {ref_name} reached a successful "
            f"conclusion after {attempts} polling attempts at "
            f"{interval_seconds}s intervals"
        )

    return run


