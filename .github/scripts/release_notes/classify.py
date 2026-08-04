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
import os
import re
from dataclasses import dataclass

from .collect import PullRequest

# Default is the first-party alias; the ci-vss-oss job overrides this to the
# NVIDIA Inference Hub's routed id (e.g. "azure/anthropic/claude-opus-4-8").
DEFAULT_MODEL = "claude-opus-4-8"

# JSON is enforced by prompt, not by output_config.format: the Inference Hub
# workspace this runs under has structured outputs disabled
# ("structured_outputs not supported in your workspace").
JSON_SHAPE = (
    '{"classifications": [{"key": "<exact key as listed>", '
    '"classification": "addressed|related|mentioned_only", "reason": "<short>"}]}'
)

FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)

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
mentioned_only unless the discussion says the PR handles it.

Respond with ONLY a JSON object (no markdown fences, no prose) of the shape:
{json_shape}
The "key" field must echo the candidate key EXACTLY as listed above (e.g. \
"6217188" or "VIA-2035" — without the kind prefix).\
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
        json_shape=JSON_SHAPE,
    )
    try:
        response = client.messages.create(
            model=os.environ.get("RELEASE_NOTES_CLAUDE_MODEL", DEFAULT_MODEL),
            max_tokens=16000,
            thinking={"type": "adaptive"},
            messages=[{"role": "user", "content": prompt}],
        )
    except anthropic.APIError as err:
        print(f"WARNING: classification failed for PR #{pr.number}: {err}")
        return _fallback(unique, f"classification error: {type(err).__name__}"), False

    if response.stop_reason == "refusal":
        return _fallback(unique, "classification refused"), False
    text = next((block.text for block in response.content if block.type == "text"), "")
    try:
        parsed = json.loads(FENCE_RE.sub("", text).strip())
    except json.JSONDecodeError:
        return _fallback(unique, "unparseable classification output"), False

    items = [item for item in parsed.get("classifications", []) if isinstance(item, dict)]
    verdicts = []
    for key, kind in unique.items():
        # Exact key echo is instructed; tolerate a "nvbug 6217188"-style echo
        # via a boundary-aware match (a bare substring test would let a
        # shorter key claim a longer key's verdict, e.g. 621718 vs 6217188).
        item = next((i for i in items if i.get("key") == key), None)
        if item is None:
            boundary = re.compile(rf"(?<![\w.-]){re.escape(key)}(?![\w.-])", re.IGNORECASE)
            item = next((i for i in items if boundary.search(str(i.get("key", "")))), None)
        if item is None or item.get("classification") not in (
            "addressed",
            "related",
            "mentioned_only",
        ):
            verdicts.append(Verdict(kind, key, "related", "not classified by agent"))
        else:
            verdicts.append(Verdict(kind, key, item["classification"], item.get("reason", "")))
    return verdicts, True


def _fallback(unique: dict[str, str], reason: str) -> list[Verdict]:
    return [Verdict(kind, key, "related", reason) for key, kind in unique.items()]


def make_client() -> "object | None":
    """Anthropic client, or None when no key is configured (fail-open).

    The SDK reads ANTHROPIC_BASE_URL from the environment, which is how
    the ci-vss-oss job points this at the NVIDIA Inference Hub gateway
    (https://inference-api.nvidia.com) instead of the first-party API.
    """
    if not os.environ.get("ANTHROPIC_API_KEY", "").strip():
        print("WARNING: ANTHROPIC_API_KEY not set — all candidates default to 'related'")
        return None
    import anthropic

    base = os.environ.get("ANTHROPIC_BASE_URL", "https://api.anthropic.com")
    model = os.environ.get("RELEASE_NOTES_CLAUDE_MODEL", DEFAULT_MODEL)
    print(f"classifier: {model} via {base}")
    return anthropic.Anthropic()
