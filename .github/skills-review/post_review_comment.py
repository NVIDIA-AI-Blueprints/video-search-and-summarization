#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Post or update the sticky Skills-Review PR comment.

Thin fork of .github/skills-nv-base/post_comment.py: the comment body is
composed by consolidate.py (read from $COMMENT_BODY); this module only
prepends the hidden marker and upserts it (PATCH the existing marked comment,
else POST). Manual-mode (no PR) falls back to $GITHUB_STEP_SUMMARY.

Advisory: every failure is surfaced as a ::warning and the script exits 0 —
posting the review must never fail the PR.

Env:
  GITHUB_TOKEN, GITHUB_REPOSITORY, PR_NUMBER   (PR mode)
  COMMENT_BODY        path to the body markdown (default: review-comment.md)
  GITHUB_STEP_SUMMARY (manual-mode fallback target)
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional

MARKER = "<!-- skills-review-bot:v1 -->"


def _gh_request(method: str, url: str, body: Optional[dict] = None) -> dict:
    token = os.environ["GITHUB_TOKEN"]
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        url, data=data, method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    with urllib.request.urlopen(req) as resp:
        return {} if resp.status == 204 else json.loads(resp.read())


def find_sticky_comment(repo: str, pr: str) -> Optional[int]:
    url = f"https://api.github.com/repos/{repo}/issues/{pr}/comments?per_page=100"
    while url:
        req = urllib.request.Request(url, headers={
            "Authorization": f"Bearer {os.environ['GITHUB_TOKEN']}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        })
        with urllib.request.urlopen(req) as resp:
            comments = json.loads(resp.read())
            link = resp.headers.get("Link") or ""
        for c in comments:
            if MARKER in (c.get("body") or ""):
                return c["id"]
        url = None
        for piece in link.split(","):
            if 'rel="next"' in piece:
                s, e = piece.find("<") + 1, piece.find(">", piece.find("<") + 1)
                if s > 0 and e > s:
                    url = piece[s:e]
                break
    return None


def upsert_comment(repo: str, pr: str, body: str) -> None:
    existing = find_sticky_comment(repo, pr)
    if existing is not None:
        _gh_request("PATCH",
                    f"https://api.github.com/repos/{repo}/issues/comments/{existing}",
                    {"body": body})
        print(f"::notice::Updated sticky comment id={existing}", flush=True)
    else:
        _gh_request("POST",
                    f"https://api.github.com/repos/{repo}/issues/{pr}/comments",
                    {"body": body})
        print("::notice::Posted new sticky comment", flush=True)


def marked_body() -> str:
    """The comment body from $COMMENT_BODY, with the sticky marker prepended."""
    path = os.environ.get("COMMENT_BODY", "review-comment.md")
    try:
        body = Path(path).read_text()
    except OSError as exc:
        print(f"::warning::skills-review: comment body {path} unreadable: {exc}",
              flush=True)
        body = "## 🔬 Skills Review\n\n_(report body unavailable)_"
    return f"{MARKER}\n{body}"


def _write_step_summary(body: str) -> bool:
    path = os.environ.get("GITHUB_STEP_SUMMARY", "").strip()
    if not path:
        return False
    try:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(body)
            if not body.endswith("\n"):
                fh.write("\n")
            fh.write("\n")
        return True
    except OSError as exc:
        print(f"::warning::Could not write GITHUB_STEP_SUMMARY ({path}): {exc}",
              flush=True)
        return False


def main() -> int:
    pr = os.environ.get("PR_NUMBER", "").strip()
    repo = os.environ.get("GITHUB_REPOSITORY", "").strip()
    token = os.environ.get("GITHUB_TOKEN", "")
    body = marked_body()

    if not pr:
        if _write_step_summary(body):
            print("Manual mode: review appended to $GITHUB_STEP_SUMMARY", flush=True)
        else:
            print("::warning::PR_NUMBER unset and no step summary; printing body",
                  flush=True)
            print(body, flush=True)
        return 0

    if not repo or not token:
        print("::warning::GITHUB_REPOSITORY or GITHUB_TOKEN missing; cannot post",
              flush=True)
        return 0

    try:
        upsert_comment(repo, pr, body)
    except urllib.error.HTTPError as exc:
        print(f"::warning::Could not post/update comment ({exc.code} {exc.reason}): "
              f"{exc.read().decode(errors='replace')[:200]}", flush=True)
    except Exception as exc:  # advisory: never fail the gate
        print(f"::warning::Comment poster failed: {exc!r}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
