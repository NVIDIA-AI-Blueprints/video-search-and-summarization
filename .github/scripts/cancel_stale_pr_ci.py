#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Cancel in-flight CI on ``pull-request/<N>`` after the source PR moves.

PR CI is gated on copy-pr-bot updating the mirror (``/ok to test``). Until
that push, GitHub concurrency cannot see a new run and will not cancel the
old one. This helper is the source-PR side of that: on synchronize or close,
cancel mirror runs whose tree is no longer the source head.

Tree, not commit SHA: the bot may copy a fork onto a new commit with the
same tree. Cancelling by SHA would kill the run that just started.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable

GITHUB_API = "https://api.github.com"
ACTIVE_STATUSES = frozenset({"in_progress", "queued", "waiting"})


class CancelError(RuntimeError):
    """A GitHub API call failed."""


def require_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise CancelError(f"{name} is not configured")
    return value


def github(
    method: str,
    repo: str,
    path: str,
    payload: dict[str, Any] | None = None,
) -> Any:
    data = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(
        f"{GITHUB_API}/repos/{repo}{path}",
        data=data,
        method=method,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {require_env('GITHUB_TOKEN')}",
            "Content-Type": "application/json",
            "User-Agent": "vss-cancel-stale-pr-ci/1.0",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            body = response.read()
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read(500).decode("utf-8", errors="replace")
        raise CancelError(
            f"{method} {path} returned HTTP {exc.code}: {detail}"
        ) from exc
    except urllib.error.URLError as exc:
        raise CancelError(f"{method} {path} failed: {exc.reason}") from exc


def commit_tree_sha(repo: str, sha: str, get: Callable[..., Any] = github) -> str | None:
    try:
        payload = get("GET", repo, f"/commits/{urllib.parse.quote(sha)}")
    except CancelError as exc:
        print(f"Could not resolve tree for {sha}: {exc}", file=sys.stderr)
        return None
    tree = (payload.get("commit") or {}).get("tree") or {}
    tree_sha = tree.get("sha")
    return str(tree_sha) if tree_sha else None


def list_active_runs(
    repo: str,
    branch: str,
    get: Callable[..., Any] = github,
) -> list[dict[str, Any]]:
    seen: set[int] = set()
    runs: list[dict[str, Any]] = []
    quoted = urllib.parse.quote(branch, safe="")
    for status in sorted(ACTIVE_STATUSES):
        page = 1
        while page <= 20:
            path = (
                f"/actions/runs?branch={quoted}&status={status}"
                f"&per_page=100&page={page}"
            )
            payload = get("GET", repo, path)
            batch = payload.get("workflow_runs") or []
            for run in batch:
                run_id = run.get("id")
                if not isinstance(run_id, int) or run_id in seen:
                    continue
                if run.get("status") in ACTIVE_STATUSES:
                    seen.add(run_id)
                    runs.append(run)
            if len(batch) < 100:
                break
            page += 1
    return runs


def should_cancel_run(
    *,
    run: dict[str, Any],
    source_tree: str | None,
    run_tree: str | None,
    closed: bool,
    this_run_id: int | None,
) -> bool:
    run_id = run.get("id")
    if this_run_id is not None and run_id == this_run_id:
        return False
    if run.get("status") not in ACTIVE_STATUSES:
        return False
    if closed:
        return True
    if not source_tree or not run_tree:
        return False
    return run_tree != source_tree


def cancel_run(repo: str, run_id: int, post: Callable[..., Any] = github) -> None:
    try:
        post("POST", repo, f"/actions/runs/{run_id}/cancel")
    except CancelError as exc:
        message = str(exc)
        if "HTTP 409" in message:
            print(f"Run {run_id} already completing; skip cancel")
            return
        raise


def main() -> int:
    repo = require_env("GITHUB_REPOSITORY")
    pr_number = require_env("PR_NUMBER")
    source_sha = os.environ.get("SOURCE_SHA", "").strip()
    closed = os.environ.get("PR_CLOSED", "").strip().lower() in {"1", "true", "yes"}
    this_run_raw = os.environ.get("GITHUB_RUN_ID", "").strip()
    this_run_id = int(this_run_raw) if this_run_raw.isdigit() else None
    branch = f"pull-request/{pr_number}"

    source_tree = commit_tree_sha(repo, source_sha) if source_sha else None
    if not closed and source_tree is None:
        print(
            "Source tree unavailable; not cancelling (avoid killing a live "
            "mirror copy of a fork SHA)."
        )
        return 0

    cancelled = 0
    for run in list_active_runs(repo, branch):
        run_id = run.get("id")
        if not isinstance(run_id, int):
            continue
        head_sha = str(run.get("head_sha") or "")
        run_tree = commit_tree_sha(repo, head_sha) if head_sha else None
        if not should_cancel_run(
            run=run,
            source_tree=source_tree,
            run_tree=run_tree,
            closed=closed,
            this_run_id=this_run_id,
        ):
            continue
        print(f"Cancelling run {run_id} ({run.get('name')}) sha={head_sha[:12]}")
        cancel_run(repo, run_id)
        cancelled += 1
    print(f"Cancelled {cancelled} stale run(s) on {branch}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except CancelError as exc:
        print(exc, file=sys.stderr)
        raise SystemExit(1) from exc
