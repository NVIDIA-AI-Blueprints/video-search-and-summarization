#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Warn-first tracking-reference check for PRs (tracking-ref-check.yml).

Scans the PR's title, description, comments, commit messages, and branch
name for a JIRA (VIA-<n>) or NVBugs reference — the same patterns the QA
drop release-notes pipeline uses. PRs without one get a
``needs-tracking-ref`` label and a bot comment asking the author to add
a reference to the description. Always exits 0 (non-blocking): with ~2%
of PRs carrying a reference today, a required check would halt the repo.
Flip the workflow to a required check once compliance is healthy.

Exemptions: bot authors (filtered in the workflow) and PRs carrying the
``no-tracking-ref`` label (deliberate opt-out, e.g. pure chores).

SECURITY: runs under pull_request_target — reads PR metadata via the
REST API only and must never execute PR code.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from release_notes import github_api  # noqa: E402
from release_notes.patterns import scan_surfaces  # noqa: E402

LABEL = "needs-tracking-ref"
OPT_OUT_LABEL = "no-tracking-ref"
COMMENT_MARKER = "<!-- vss-tracking-ref-check -->"
# NOTE: examples deliberately use <id> placeholders that do NOT match the
# reference regexes — this comment becomes a scanned surface on the next
# run, and a real-format example would satisfy the check by itself.
COMMENT_BODY = f"""{COMMENT_MARKER}
No JIRA (`VIA-<id>`) or NVBugs reference found on this PR.

QA drop release notes attribute every change to its tracker — without a
reference this PR lands in the "Unreferenced PRs" section and QA has no
issue to verify it against. Please add the JIRA issue or NVBug id to the
PR **description** (e.g. `Fixes NVBug <id>` or `VIA-<id>`).

If this change genuinely has no tracker (pure chore), apply the
`{OPT_OUT_LABEL}` label instead. This check is informational and does not
block merging.
"""
RESOLVED_BODY = f"{COMMENT_MARKER}\n✅ Tracking reference found — thanks!"


def main() -> int:
    repo = os.environ["GITHUB_REPOSITORY"]
    number = int(os.environ["PR_NUMBER"])

    pr = github_api.get(f"/repos/{repo}/pulls/{number}")
    labels = {label.get("name", "") for label in pr.get("labels") or []}
    if OPT_OUT_LABEL in labels:
        print(f"PR #{number} opted out via '{OPT_OUT_LABEL}' label")
        return 0

    surfaces: list[tuple[str, str]] = [
        ("title", pr.get("title") or ""),
        ("body", pr.get("body") or ""),
        ("branch", (pr.get("head") or {}).get("ref") or ""),
    ]
    # Fetched once — reused below to locate our own marker comment
    # (Greptile P2: avoid paginating the same list twice).
    comments = github_api.get_paginated(f"/repos/{repo}/issues/{number}/comments")
    for comment in comments:
        # Bot comments (this check's own reminder, review bots, …) are not
        # author intent — scanning them would let the reminder satisfy itself.
        if (comment.get("user") or {}).get("type") == "Bot":
            continue
        surfaces.append(("comment", comment.get("body") or ""))
    for commit in github_api.get_paginated(f"/repos/{repo}/pulls/{number}/commits"):
        message = (commit.get("commit") or {}).get("message") or ""
        subject, _, rest = message.partition("\n")
        surfaces.append(("commit_subject", subject))
        if rest.strip():
            surfaces.append(("commit_message", rest))

    references = scan_surfaces(surfaces)
    bot_comment = next(
        (c for c in comments if COMMENT_MARKER in (c.get("body") or "")), None
    )

    if references:
        found = ", ".join(sorted({f"{r.kind}:{r.key}" for r in references}))
        print(f"PR #{number} has tracking reference(s): {found}")
        if LABEL in labels:
            github_api.request(
                "DELETE", f"/repos/{repo}/issues/{number}/labels/{LABEL}"
            )
        if bot_comment and RESOLVED_BODY not in (bot_comment.get("body") or ""):
            github_api.request(
                "PATCH",
                f"/repos/{repo}/issues/comments/{bot_comment['id']}",
                {"body": RESOLVED_BODY},
            )
        return 0

    print(f"PR #{number} has no tracking reference — labeling (non-blocking)")
    if LABEL not in labels:
        github_api.request("POST", f"/repos/{repo}/issues/{number}/labels", {"labels": [LABEL]})
    if bot_comment is None:
        github_api.request(
            "POST", f"/repos/{repo}/issues/{number}/comments", {"body": COMMENT_BODY}
        )
    elif RESOLVED_BODY in (bot_comment.get("body") or ""):
        # Reference was removed again (e.g. description edited) — re-warn.
        github_api.request(
            "PATCH",
            f"/repos/{repo}/issues/comments/{bot_comment['id']}",
            {"body": COMMENT_BODY},
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
