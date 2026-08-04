#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Publish the public OSRB check around the private downstream pipeline."""

from __future__ import annotations

import argparse
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any

GITHUB_API = "https://api.github.com"
CHECK_NAME = "OSRB Review"
EXTERNAL_PREFIX = "gitlab-osrb:"


class CheckError(RuntimeError):
    """A GitHub check operation failed."""


def guide_url(repo: str) -> str:
    return f"https://github.com/{repo}/blob/main/.github/OSRB_REVIEW.md"


def summary_with_guide(repo: str, summary: str) -> str:
    return f"{summary}\n\n[How to respond to this check]({guide_url(repo)})"


def token() -> str:
    value = os.environ.get("GITHUB_TOKEN", "").strip()
    if not value:
        raise CheckError("GITHUB_TOKEN is not configured")
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
            "Authorization": f"Bearer {token()}",
            "Content-Type": "application/json",
            "User-Agent": "vss-osrb-check/1.0",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            body = response.read()
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read(500).decode("utf-8", errors="replace")
        raise CheckError(f"{method} GitHub check request returned HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise CheckError(f"{method} GitHub check request failed: {exc.reason}") from exc


def find_check(repo: str, sha: str, external_id: str) -> dict[str, Any] | None:
    name = urllib.parse.quote(CHECK_NAME)
    checks = github("GET", repo, f"/commits/{sha}/check-runs?check_name={name}")
    matches = [
        check
        for check in checks.get("check_runs", [])
        if check.get("name") == CHECK_NAME
        and check.get("external_id") == external_id
    ]
    return max(matches, key=lambda check: int(check.get("id", 0)), default=None)


def start(repo: str, sha: str, run_url: str, external_id: str) -> None:
    payload = {
        "name": CHECK_NAME,
        "head_sha": sha,
        "status": "in_progress",
        "details_url": run_url,
        "external_id": external_id,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "output": {
            "title": "Private OSRB review is running",
            "summary": summary_with_guide(
                repo,
                "Wait for this check to finish before merging. "
                "The protected compliance reviewer is evaluating the public License Diff.",
            ),
        },
    }
    existing = find_check(repo, sha, external_id)
    if existing:
        github("PATCH", repo, f"/check-runs/{existing['id']}", payload)
    else:
        github("POST", repo, "/check-runs", payload)


def complete(
    repo: str,
    sha: str,
    run_url: str,
    external_id: str,
    success: bool,
    summary: str,
) -> None:
    check = find_check(repo, sha, external_id)
    if not check:
        raise CheckError(f"no {CHECK_NAME!r} check exists for {sha}")
    github(
        "PATCH",
        repo,
        f"/check-runs/{check['id']}",
        {
            "name": CHECK_NAME,
            "status": "completed",
            "conclusion": "success" if success else "failure",
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "details_url": run_url,
            "output": {
                "title": "OSRB review passed" if success else "OSRB review needs attention",
                "summary": summary_with_guide(repo, summary),
            },
        },
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("start", "complete", "skip"))
    parser.add_argument("--repo", required=True)
    parser.add_argument("--sha", required=True)
    parser.add_argument("--run-url", required=True)
    parser.add_argument("--external-id", required=True)
    parser.add_argument("--success", action="store_true")
    parser.add_argument("--summary", default="")
    args = parser.parse_args()
    if not args.external_id.startswith(EXTERNAL_PREFIX):
        raise CheckError(f"external ID must start with {EXTERNAL_PREFIX!r}")
    if args.command == "start":
        start(args.repo, args.sha, args.run_url, args.external_id)
    elif args.command == "skip":
        start(args.repo, args.sha, args.run_url, args.external_id)
        complete(
            args.repo,
            args.sha,
            args.run_url,
            args.external_id,
            True,
            args.summary or "Not applicable for this target branch.",
        )
    else:
        complete(
            args.repo,
            args.sha,
            args.run_url,
            args.external_id,
            args.success,
            args.summary
            or (
                "The private OSRB review approved this change."
                if args.success
                else "The private OSRB review did not approve this change. See the PR review."
            ),
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
