# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Render the release-notes markdown document.

Structure (per the pipeline spec): header (tag, window, counts) → JIRA
list → NVBugs list → Unreferenced PRs (the compliance scoreboard that
decides when the warn-first PR check flips to required) → bot PRs as a
one-line count.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .classify import Verdict
from .collect import DropWindow, PullRequest

REPORTED = ("addressed", "related")


@dataclass
class NotesData:
    window: DropWindow
    verdicts: dict[int, list[Verdict]]  # PR number -> verdicts
    unclassified: set[int] = field(default_factory=set)  # PRs where the agent fell back

    def issue_index(self, kind: str) -> dict[str, list[tuple[PullRequest, Verdict]]]:
        """issue key -> [(pr, verdict), ...] for addressed/related refs."""
        index: dict[str, list[tuple[PullRequest, Verdict]]] = {}
        for pr in self.window.pull_requests:
            for verdict in self.verdicts.get(pr.number, []):
                if verdict.kind == kind and verdict.classification in REPORTED:
                    index.setdefault(verdict.key, []).append((pr, verdict))
        return index

    def unreferenced(self) -> list[PullRequest]:
        return [
            pr
            for pr in self.window.pull_requests
            if not pr.is_bot
            and not any(
                v.classification in REPORTED for v in self.verdicts.get(pr.number, [])
            )
        ]

    def bot_prs(self) -> list[PullRequest]:
        return [pr for pr in self.window.pull_requests if pr.is_bot]


def _pr_line(pr: PullRequest, verdict: Verdict | None = None) -> str:
    suffix = f" — {verdict.classification}" if verdict else ""
    return f"- PR #{pr.number} [{pr.title}]({pr.url}) by @{pr.author}, merged {pr.merged_at[:10]}{suffix}"


def render(data: NotesData) -> str:
    window = data.window
    human_prs = [pr for pr in window.pull_requests if not pr.is_bot]
    jira_index = data.issue_index("jira")
    nvbug_index = data.issue_index("nvbug")
    unreferenced = data.unreferenced()
    bots = data.bot_prs()

    lines = [
        f"# QA Drop Release Notes — {window.tag}",
        "",
        f"- **Window**: `{window.previous_tag or '(first drop)'}` → `{window.tag}` "
        f"({window.commit_count} commits)",
        f"- **PRs**: {len(human_prs)} ({len(bots)} bot PRs excluded below)",
        f"- **JIRA issues**: {len(jira_index)} — **NVBugs**: {len(nvbug_index)} — "
        f"**Unreferenced PRs**: {len(unreferenced)}",
        "",
        "## JIRA",
        "",
    ]
    if jira_index:
        for key in sorted(jira_index):
            lines.append(f"### {key}")
            lines.extend(_pr_line(pr, verdict) for pr, verdict in jira_index[key])
            lines.append("")
    else:
        lines.extend(["_No JIRA references in this window._", ""])

    lines.extend(["## NVBugs", ""])
    if nvbug_index:
        for key in sorted(nvbug_index, key=int):
            lines.append(f"### NVBug {key}")
            lines.extend(_pr_line(pr, verdict) for pr, verdict in nvbug_index[key])
            lines.append("")
    else:
        lines.extend(["_No NVBugs references in this window._", ""])

    lines.extend(
        [
            "## Unreferenced PRs",
            "",
            "_Merged without a JIRA/NVBugs reference — QA has no tracker for these._",
            "",
        ]
    )
    if unreferenced:
        lines.extend(_pr_line(pr) for pr in unreferenced)
    else:
        lines.append("_None — every PR carried a reference._")
    lines.append("")

    if bots:
        lines.append(f"_{len(bots)} bot PRs (helm-sync, mirrors, …) omitted._")
    if data.unclassified:
        numbers = ", ".join(f"#{n}" for n in sorted(data.unclassified))
        lines.append(
            f"_⚠ References on {numbers} were not agent-classified (defaulted to 'related')._"
        )
    lines.append("")
    return "\n".join(lines)


def issue_comment(kind: str, key: str, prs: list[tuple[PullRequest, Verdict]], tag: str) -> str:
    """Short cross-post comment for one issue, one drop."""
    heading = f"The following PR(s) referencing this issue are included in QA drop {tag}:"
    body = [heading, ""]
    for pr, verdict in prs:
        body.append(
            f"- PR #{pr.number}: {pr.title} ({pr.url}) — merged {pr.merged_at[:10]}, "
            f"{verdict.classification}"
        )
    return "\n".join(body)
