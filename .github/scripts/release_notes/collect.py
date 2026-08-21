# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Collect the Drop Window and the PRs merged inside it.

The Drop Window is tag-to-tag: everything reachable from the new trigger
tag but not from the *nearest previous* weekly drop tag in its
ancestry. Never a fixed time lookback — multiple drops can land in one
week (hotfix re-tags get small incremental windows) and a lookback would
double-count or leave gaps.

Requires a git clone with tag history (``git clone --filter=tree:0`` is
enough); PR metadata comes from the GitHub REST API.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone

from . import github_api
from .patterns import Reference, scan_surfaces

# Weekly QA drop tags only: dev-YY.MM.N with an optional hotfix suffix
# (dev-26.07.3, dev-26.06.3-1). Other dev-* tags and v* GA tags neither
# trigger notes nor act as window boundaries.
TRIGGER_TAG_RE = re.compile(r"^dev-\d{2}\.\d{2}\.\d+(-\d+)?$")
PR_NUMBER_RE = re.compile(r"\((?:PR )?#(\d+)\)")
BOT_AUTHOR_RE = re.compile(r"(\[bot\]$|^svc-|^copy-pr-bot)", re.IGNORECASE)
MAX_CLOSED_PR_PAGES = 15  # 1500 most recently updated closed PRs


@dataclass
class PullRequest:
    number: int
    title: str
    author: str
    branch: str
    url: str
    merged_at: str
    is_bot: bool
    labels: list[str] = field(default_factory=list)
    references: list[Reference] = field(default_factory=list)


@dataclass
class DropWindow:
    tag: str
    previous_tag: str | None
    commit_count: int
    pull_requests: list[PullRequest]


def _git(git_dir: str, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", git_dir, *args], capture_output=True, text=True, check=True
    ).stdout.strip()


def previous_trigger_tag(git_dir: str, tag: str) -> str | None:
    """The *chronologically* previous weekly drop tag, if any.

    Chronological (not ancestry) selection is deliberate: the ancestry-
    nearest tag can skip over the actual previous QA drop and make
    consecutive drop windows overlap or gap. Consecutive drop tags by
    creation time partition the timeline exactly once.
    """
    raw = _git(
        git_dir, "for-each-ref", "refs/tags", "--sort=creatordate",
        "--format=%(refname:short)%00%(creatordate:iso-strict)",
    )
    tags = [tuple(line.split("\x00", 1)) for line in raw.splitlines() if "\x00" in line]
    trigger_tags = [(name, date) for name, date in tags if TRIGGER_TAG_RE.match(name)]
    own_date = next((date for name, date in trigger_tags if name == tag), None)
    if own_date is None:
        raise SystemExit(f"tag {tag!r} is not a weekly drop tag (dev-YY.MM.N[-h])")
    older = [name for name, date in trigger_tags if name != tag and date < own_date]
    return older[-1] if older else None


def _is_ancestor(git_dir: str, ancestor: str, descendant: str) -> bool:
    result = subprocess.run(
        ["git", "-C", git_dir, "merge-base", "--is-ancestor", ancestor, descendant],
        capture_output=True,
    )
    return result.returncode == 0


def window_commits(git_dir: str, tag: str, previous_tag: str | None) -> list[tuple[str, str]]:
    """(sha, subject) pairs in the window, newest first.

    Pure ancestry range when the previous tag is an ancestor (the normal
    case); otherwise (e.g. a boundary tag cut from a release branch)
    fall back to slicing the tag's own lineage by the boundary tags'
    commit dates.
    """
    if previous_tag and _is_ancestor(git_dir, previous_tag, tag):
        raw = _git(git_dir, "log", "--format=%H%x00%s", f"{previous_tag}..{tag}")
        return [tuple(line.split("\x00", 1)) for line in raw.splitlines() if "\x00" in line]
    lower = tag_commit_date(git_dir, previous_tag) if previous_tag else None
    raw = _git(git_dir, "log", "--format=%H%x00%cI%x00%s", tag)
    out: list[tuple[str, str]] = []
    for line in raw.splitlines():
        parts = line.split("\x00", 2)
        if len(parts) == 3 and (lower is None or _parse_iso(parts[1]) > lower):
            out.append((parts[0], parts[2]))
    return out


def tag_commit_date(git_dir: str, rev: str) -> datetime:
    """Committer date of the commit a tag points at (timezone-aware)."""
    return _parse_iso(_git(git_dir, "log", "-1", "--format=%cI", rev))


def _parse_iso(ts: str) -> datetime:
    """Parse ISO-8601 with either a Z or a numeric offset.

    Git %cI dates carry the committer's local offset while GitHub API
    timestamps are UTC Z — comparing the raw strings lexicographically is
    chronologically wrong across offsets, so all boundary comparisons go
    through timezone-aware datetimes.
    """
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def pr_numbers_for_window(
    repo: str,
    git_dir: str,
    tag: str,
    previous_tag: str | None,
    commits: list[tuple[str, str]],
) -> set[int]:
    """Find the PRs whose merge landed inside the Drop Window.

    SHA-based mapping does not work in this repo: a post-merge signing
    bot rewrites just-merged commits (to attach NVSkills validation
    signatures) and force-pushes the base branch, so a merged PR's
    ``merge_commit_sha`` frequently dangles while its commits reach the
    tag lineage under new SHAs with preserved subjects.

    The window is therefore evaluated at PR granularity: a PR belongs to
    the drop when it merged into an integration base branch strictly
    after the previous trigger tag's commit and at-or-before this tag's
    commit (still tag-to-tag — the boundaries are the tag commits, never
    a fixed lookback). ``merge_commit_sha`` membership and subject
    markers like ``(#N)`` / ``(PR #N)`` are kept as supplements, and PRs
    with no subject overlap in the window are logged for visibility.
    """
    window_shas = {sha for sha, _ in commits}
    window_subjects = {subject for _, subject in commits}
    upper = tag_commit_date(git_dir, tag)
    lower = (
        tag_commit_date(git_dir, previous_tag)
        if previous_tag
        else datetime.min.replace(tzinfo=timezone.utc)
    )

    numbers: set[int] = set()
    for _, subject in commits:
        for found in PR_NUMBER_RE.findall(subject):
            numbers.add(int(found))

    page_url: str | None = (
        f"{github_api.API_ROOT}/repos/{repo}/pulls"
        "?state=closed&sort=updated&direction=desc&per_page=100"
    )
    pages = 0
    while page_url and pages < MAX_CLOSED_PR_PAGES:
        parsed, headers = github_api.request("GET", page_url)
        pages += 1
        for pr in parsed or []:
            merged_at = pr.get("merged_at") or ""
            if not merged_at:
                continue
            merged_dt = _parse_iso(merged_at)
            base = (pr.get("base") or {}).get("ref") or ""
            in_window = lower < merged_dt <= upper and _is_integration_base(base)
            if in_window or pr.get("merge_commit_sha") in window_shas:
                number = int(pr["number"])
                numbers.add(number)
                head_subject = pr.get("title") or ""
                if (
                    pr.get("merge_commit_sha") not in window_shas
                    and head_subject not in window_subjects
                ):
                    print(f"note: PR #{number} matched by merge time only ({merged_at})")
        # Stop once a whole page is older than the window's lower bound.
        if parsed and all(
            _parse_iso(pr["updated_at"]) < lower for pr in parsed if pr.get("updated_at")
        ):
            break
        page_url = github_api._next_link(headers.get("Link", ""))
    return numbers


def _is_integration_base(base: str) -> bool:
    return base in ("develop", "main") or base.startswith(("dev/", "release/"))


def fetch_pull_request(repo: str, number: int) -> PullRequest | None:
    """Fetch one PR and scan all decided surfaces for candidate references.

    Surfaces (per the pipeline spec): title, description, review
    conversation comments, commit messages, and branch name.
    """
    pr = github_api.get(f"/repos/{repo}/pulls/{number}")
    if not pr or not pr.get("merged_at"):
        return None
    surfaces: list[tuple[str, str]] = [
        ("title", pr.get("title") or ""),
        ("body", pr.get("body") or ""),
        ("branch", (pr.get("head") or {}).get("ref") or ""),
    ]
    for comment in github_api.get_paginated(f"/repos/{repo}/issues/{number}/comments"):
        # Bot comments are not author intent — in particular the
        # tracking-ref reminder bot's own comment must not count as a
        # reference source (nor should review-bot chatter).
        if (comment.get("user") or {}).get("type") == "Bot":
            continue
        surfaces.append(("comment", comment.get("body") or ""))
    for commit in github_api.get_paginated(f"/repos/{repo}/pulls/{number}/commits"):
        message = (commit.get("commit") or {}).get("message") or ""
        subject, _, rest = message.partition("\n")
        surfaces.append(("commit_subject", subject))
        if rest.strip():
            surfaces.append(("commit_message", rest))
    author = (pr.get("user") or {}).get("login") or "unknown"
    return PullRequest(
        number=number,
        title=pr.get("title") or "",
        author=author,
        branch=(pr.get("head") or {}).get("ref") or "",
        url=pr.get("html_url") or "",
        merged_at=pr.get("merged_at") or "",
        is_bot=bool(BOT_AUTHOR_RE.search(author)),
        labels=[label.get("name", "") for label in pr.get("labels") or []],
        references=scan_surfaces(surfaces),
    )


def collect(repo: str, git_dir: str, tag: str) -> DropWindow:
    previous_tag = previous_trigger_tag(git_dir, tag)
    commits = window_commits(git_dir, tag, previous_tag)
    numbers = pr_numbers_for_window(repo, git_dir, tag, previous_tag, commits)
    pull_requests = [
        pr for number in sorted(numbers) if (pr := fetch_pull_request(repo, number)) is not None
    ]
    return DropWindow(
        tag=tag,
        previous_tag=previous_tag,
        commit_count=len(commits),
        pull_requests=pull_requests,
    )
