# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Reference-extraction patterns for JIRA issues and NVBugs.

Patterns follow observed usage in this repo's history rather than an
idealized convention: ``VIA-2035``, ``NVBug 6453445``, ``NVBugs 6200918``,
``bug 6311754``, ``nvbug/6292153``, and bare ids in branch names like
``fix/6217188-storage-...``.

Bare numbers (no nvbug/bug prefix) are only trusted on *curated*
surfaces — PR title, branch name, and commit subjects — where they are
near-certainly bug ids. Prose surfaces (PR body, comments, full commit
messages) frequently contain pasted logs, so they require a prefix.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Surfaces where a bare 6-8 digit number counts as an NVBugs reference.
CURATED_SURFACES = frozenset({"title", "branch", "commit_subject"})

JIRA_RE = re.compile(r"\b(VIA-\d+)\b", re.IGNORECASE)
NVBUG_PREFIXED_RE = re.compile(r"\b(?:nv)?bugs?\b[\s/:#-]*(\d{6,8})\b", re.IGNORECASE)
NVBUG_BARE_RE = re.compile(r"\b(\d{6,8})\b")
# Bare 8-digit numbers starting with 20 are dates (e.g. 20260602), not bugs.
DATE_LIKE_RE = re.compile(r"^20\d{6}$")


@dataclass(frozen=True)
class Reference:
    """A candidate tracking reference found on a PR surface."""

    kind: str  # "jira" | "nvbug"
    key: str  # "VIA-2035" or "6217188"
    surface: str  # "title" | "body" | "branch" | "comment" | "commit_subject" | "commit_message"
    context: str  # the line the match was found on (for the classifier)


def _context_line(text: str, start: int) -> str:
    line_start = text.rfind("\n", 0, start) + 1
    line_end = text.find("\n", start)
    if line_end == -1:
        line_end = len(text)
    return text[line_start:line_end].strip()[:300]


def extract(surface: str, text: str) -> list[Reference]:
    """Extract candidate references from one surface's text."""
    if not text:
        return []
    refs: list[Reference] = []
    for match in JIRA_RE.finditer(text):
        refs.append(
            Reference("jira", match.group(1).upper(), surface, _context_line(text, match.start()))
        )
    seen_nvbug_spans: set[int] = set()
    for match in NVBUG_PREFIXED_RE.finditer(text):
        seen_nvbug_spans.add(match.start(1))
        refs.append(Reference("nvbug", match.group(1), surface, _context_line(text, match.start())))
    if surface in CURATED_SURFACES:
        for match in NVBUG_BARE_RE.finditer(text):
            number = match.group(1)
            if match.start(1) in seen_nvbug_spans or DATE_LIKE_RE.match(number):
                continue
            refs.append(Reference("nvbug", number, surface, _context_line(text, match.start())))
    return refs


def scan_surfaces(surfaces: list[tuple[str, str]]) -> list[Reference]:
    """Extract from (surface, text) pairs, deduplicated by (kind, key, surface)."""
    out: list[Reference] = []
    seen: set[tuple[str, str, str]] = set()
    for surface, text in surfaces:
        for ref in extract(surface, text):
            dedupe_key = (ref.kind, ref.key, ref.surface)
            if dedupe_key not in seen:
                seen.add(dedupe_key)
                out.append(ref)
    return out
