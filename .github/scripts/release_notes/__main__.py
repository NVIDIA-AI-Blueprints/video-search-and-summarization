# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Release-notes pipeline entrypoint (runs downstream on ci-vss-oss).

Usage (from a clone of the repo with tag history):

    python -m release_notes \
        --repo NVIDIA-AI-Blueprints/video-search-and-summarization \
        --tag dev-26.07.3 --tracker-bug 6470596 \
        --output release-notes-dev-26.07.3.md [--dry-run]

Env: RELEASE_NOTES_GITHUB_TOKEN (or GITHUB_TOKEN), JIRA_TOKEN,
ANTHROPIC_API_KEY (optional — fail-open), nvbugs-cli pre-authenticated.
"""

from __future__ import annotations

import argparse
import sys

from .classify import classify_pr, make_client
from .collect import collect
from .post import post_jira_comment, post_nvbug_comment
from .render import NotesData, issue_comment, render


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="release_notes")
    parser.add_argument("--repo", required=True, help="owner/name of the GitHub repo")
    parser.add_argument("--tag", required=True, help="the weekly drop tag (dev-YY.MM.N[-h])")
    parser.add_argument("--tracker-bug", required=True, help="QA Drop Tracker NVBug id")
    parser.add_argument("--git-dir", default=".", help="path to a clone with tag history")
    parser.add_argument("--output", default=None, help="write notes markdown here")
    parser.add_argument(
        "--dry-run", action="store_true", help="render notes but post nothing anywhere"
    )
    args = parser.parse_args(argv)

    window = collect(args.repo, args.git_dir, args.tag)
    print(
        f"window {window.previous_tag or '(none)'}..{args.tag}: "
        f"{window.commit_count} commits, {len(window.pull_requests)} PRs"
    )

    client = make_client()
    verdicts, unclassified = {}, set()
    for pr in window.pull_requests:
        if pr.is_bot:
            continue
        pr_verdicts, by_agent = classify_pr(pr, client)
        verdicts[pr.number] = pr_verdicts
        if pr_verdicts and not by_agent:
            unclassified.add(pr.number)

    data = NotesData(window=window, verdicts=verdicts, unclassified=unclassified)
    notes = render(data)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as handle:
            handle.write(notes)
        print(f"wrote {args.output}")
    else:
        print(notes)

    failures = 0
    for key, prs in sorted(data.issue_index("jira").items()):
        failures += _attempt(post_jira_comment, key, issue_comment("jira", key, prs, args.tag), args)
    for key, prs in sorted(data.issue_index("nvbug").items(), key=lambda kv: int(kv[0])):
        failures += _attempt(
            post_nvbug_comment, key, issue_comment("nvbug", key, prs, args.tag), args
        )
    # Full notes to the standing QA Drop Tracker — QA's single subscription point.
    failures += _attempt(post_nvbug_comment, args.tracker_bug, notes, args)

    if failures:
        print(f"ERROR: {failures} post(s) failed")
        return 1
    return 0


def _attempt(poster, key: str, body: str, args: argparse.Namespace) -> int:
    try:
        print(poster(key, body, args.tag, args.dry_run))
        return 0
    except Exception as err:  # keep going — one bad issue must not sink the drop notes
        print(f"WARNING: posting to {key} failed: {err}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
