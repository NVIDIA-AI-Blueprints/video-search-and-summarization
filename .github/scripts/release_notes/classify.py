# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Classify candidate references with Claude.

The deterministic regex pass (patterns.py) is recall-biased: comments are
scanned, so a reviewer writing "similar to bug 6123456" produces a
candidate that must NOT be cross-posted. A per-PR agent call classifies
every candidate as addressed / related / mentioned_only. The agent is
constrained to the regex's candidate set — it annotates, never invents.

Fail-open: if the API is unavailable for a PR, its candidates fall back
to "related" (recall over precision — a stray note entry is cheaper than
a silently dropped fix) and the notes flag the PR as unclassified.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from .collect import PullRequest

MODEL = "claude-opus-4-8"

CLASSIFICATION_SCHEMA = {
    "type": "object",
    "properties": {
        "classifications": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "key": {"type": "string"},
                    "classification": {
                        "type": "string",
                        "enum": ["addressed", "related", "mentioned_only"],
                    },
                    "reason": {"type": "string"},
                },
                "required": ["key", "classification", "reason"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["classifications"],
    "additionalProperties": False,
}

PROMPT = """\
You are classifying issue-tracker references found on a merged pull request so \
that QA release notes only credit the PR with issues it actually worked on.

Pull request #{number}: {title}
Branch: {branch}
Author: {author}

Candidate references (kind, key, surface it was found on, and the line it \
appeared in):
{candidates}

Classify EVERY candidate key exactly once:
- "addressed": the PR fixes/implements/closes this issue (e.g. "fixes NVBug \
6217188", the bug id in the branch name or title of a fix PR).
- "related": materially connected to the PR's change but not its primary \
target (e.g. a tracking parent, a partially-addressed issue).
- "mentioned_only": incidental discussion — comparisons ("similar to bug X"), \
unrelated context, pasted logs, references to other work.

Titles/branch names referencing an id are almost always addressed. Reviewer \
comments referencing a different id than the title/branch are usually \
mentioned_only unless the discussion says the PR handles it.\
"""


@dataclass
class Verdict:
    kind: str  # "jira" | "nvbug"
    key: str
    classification: str  # "addressed" | "related" | "mentioned_only"
    reason: str


def _candidate_lines(pr: PullRequest) -> str:
    return "\n".join(
        f"- {ref.kind} {ref.key} (on {ref.surface}): {ref.context!r}" for ref in pr.references
    )


def classify_pr(pr: PullRequest, client: "object | None") -> tuple[list[Verdict], bool]:
    """Return (verdicts, classified_by_agent) for one PR's candidates."""
    unique: dict[str, str] = {}  # key -> kind
    for ref in pr.references:
        unique.setdefault(ref.key, ref.kind)
    if not unique:
        return [], True
    if client is None:
        return _fallback(unique, "agent unavailable"), False

    import anthropic

    prompt = PROMPT.format(
        number=pr.number,
        title=pr.title,
        branch=pr.branch,
        author=pr.author,
        candidates=_candidate_lines(pr),
    )
    try:
        response = client.messages.create(
            model=MODEL,
            max_tokens=16000,
            thinking={"type": "adaptive"},
            output_config={"format": {"type": "json_schema", "schema": CLASSIFICATION_SCHEMA}},
            messages=[{"role": "user", "content": prompt}],
        )
    except anthropic.APIError as err:
        print(f"WARNING: classification failed for PR #{pr.number}: {err}")
        return _fallback(unique, f"classification error: {type(err).__name__}"), False

    if response.stop_reason == "refusal":
        return _fallback(unique, "classification refused"), False
    text = next((block.text for block in response.content if block.type == "text"), "")
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return _fallback(unique, "unparseable classification output"), False

    by_key = {item["key"]: item for item in parsed.get("classifications", [])}
    verdicts = []
    for key, kind in unique.items():
        item = by_key.get(key)
        if item is None:
            verdicts.append(Verdict(kind, key, "related", "not classified by agent"))
        else:
            verdicts.append(Verdict(kind, key, item["classification"], item["reason"]))
    return verdicts, True


def _fallback(unique: dict[str, str], reason: str) -> list[Verdict]:
    return [Verdict(kind, key, "related", reason) for key, kind in unique.items()]


def make_client() -> "object | None":
    """Anthropic client, or None when no key is configured (fail-open)."""
    import os

    if not os.environ.get("ANTHROPIC_API_KEY", "").strip():
        print("WARNING: ANTHROPIC_API_KEY not set — all candidates default to 'related'")
        return None
    import anthropic

    return anthropic.Anthropic()
