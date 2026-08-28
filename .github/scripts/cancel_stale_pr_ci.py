#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Cancel in-flight CI on ``pull-request/<N>`` after the source PR moves.

PR CI is gated on copy-pr-bot updating the mirror (``/ok to test``). Until
that push, GitHub concurrency cannot see a new run and will not cancel the
old one. This helper is the source-PR side of that: on synchronize or close,
cancel mirror runs that are not testing the current source head.

copy-pr-bot often pushes a merge of the approved SHA into the PR base, so
the run's tree is the merge result, not the source-head tree. A current run
is one whose commit, or one of its parents, is the source SHA or shares its
tree (fork copies rewrite SHAs).
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable, NamedTuple

GITHUB_API = "https://api.github.com"
# GitHub's workflow-run status filter. requested/pending are runs that have
# been created but have not reached a runner yet.
ACTIVE_STATUSES = frozenset(
    {"in_progress", "pending", "queued", "requested", "waiting"}
)


class CancelError(RuntimeError):
    """A GitHub API call failed."""


class CommitInfo(NamedTuple):
    sha: str
    tree: str | None
    parent_shas: tuple[str, ...]


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


def parse_commit(payload: dict[str, Any], sha: str) -> CommitInfo:
    tree = (payload.get("commit") or {}).get("tree") or {}
    tree_sha = tree.get("sha")
    parents = tuple(
        str(parent["sha"])
        for parent in payload.get("parents") or []
        if parent.get("sha")
    )
    return CommitInfo(
        sha=str(payload.get("sha") or sha),
        tree=str(tree_sha) if tree_sha else None,
        parent_shas=parents,
    )


def load_commit(
    repo: str,
    sha: str,
    cache: dict[str, CommitInfo | None],
    get: Callable[..., Any] = github,
) -> CommitInfo | None:
    if sha in cache:
        return cache[sha]
    try:
        payload = get("GET", repo, f"/commits/{urllib.parse.quote(sha)}")
    except CancelError as exc:
        print(f"Could not resolve commit {sha}: {exc}", file=sys.stderr)
        cache[sha] = None
        return None
    info = parse_commit(payload, sha)
    cache[sha] = info
    return info


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
            try:
                payload = get("GET", repo, path)
            except CancelError as exc:
                if "HTTP 422" in str(exc):
                    break
                raise
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


def mirrors_current_source(
    *,
    source_sha: str,
    source_tree: str | None,
    run_sha: str,
    run_tree: str | None,
    parent_shas: tuple[str, ...],
    parent_trees: tuple[str | None, ...],
) -> bool:
    """True when the run is CI for the current source head.

    Direct copy: same SHA or same tree. Merge-into-base mirror: the source
    SHA (or a same-tree fork copy of it) is a parent of the merge commit.
    """
    if source_sha and source_sha in {run_sha, *parent_shas}:
        return True
    if not source_tree:
        return False
    trees = {run_tree, *parent_trees}
    trees.discard(None)
    return source_tree in trees


def should_cancel_run(
    *,
    run: dict[str, Any],
    matches_source: bool,
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
    return not matches_source


def cancel_run(repo: str, run_id: int, post: Callable[..., Any] = github) -> None:
    try:
        post("POST", repo, f"/actions/runs/{run_id}/cancel")
    except CancelError as exc:
        message = str(exc)
        if "HTTP 409" in message:
            print(f"Run {run_id} already completing; skip cancel")
            return
        raise


def run_matches_source(
    repo: str,
    source_sha: str,
    source_tree: str | None,
    head_sha: str,
    cache: dict[str, CommitInfo | None],
    get: Callable[..., Any] = github,
) -> bool:
    if not head_sha:
        return False
    run_commit = load_commit(repo, head_sha, cache, get)
    if run_commit is None:
        return True
    parent_trees: list[str | None] = []
    for parent_sha in run_commit.parent_shas:
        parent = load_commit(repo, parent_sha, cache, get)
        parent_trees.append(None if parent is None else parent.tree)
    return mirrors_current_source(
        source_sha=source_sha,
        source_tree=source_tree,
        run_sha=run_commit.sha,
        run_tree=run_commit.tree,
        parent_shas=run_commit.parent_shas,
        parent_trees=tuple(parent_trees),
    )


def main() -> int:
    repo = require_env("GITHUB_REPOSITORY")
    pr_number = require_env("PR_NUMBER")
    source_sha = os.environ.get("SOURCE_SHA", "").strip()
    closed = os.environ.get("PR_CLOSED", "").strip().lower() in {"1", "true", "yes"}
    this_run_raw = os.environ.get("GITHUB_RUN_ID", "").strip()
    this_run_id = int(this_run_raw) if this_run_raw.isdigit() else None
    branch = f"pull-request/{pr_number}"
    cache: dict[str, CommitInfo | None] = {}

    source_commit = load_commit(repo, source_sha, cache) if source_sha else None
    source_tree = None if source_commit is None else source_commit.tree
    if not closed and source_commit is None:
        print(
            "Source commit unavailable; not cancelling (avoid killing a live "
            "mirror copy of a fork SHA)."
        )
        return 0

    cancelled = 0
    for run in list_active_runs(repo, branch):
        run_id = run.get("id")
        if not isinstance(run_id, int):
            continue
        head_sha = str(run.get("head_sha") or "")
        matches = run_matches_source(
            repo, source_sha, source_tree, head_sha, cache
        )
        if not should_cancel_run(
            run=run,
            matches_source=matches,
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
