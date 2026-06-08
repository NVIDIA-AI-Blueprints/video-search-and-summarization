#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Compute the skills-review dispatch matrix.

Pure Python, no LLM. Fans out one leg per (changed-skill x paradigm) so the
six review paradigms run independently and in parallel via the Actions matrix.
Prints `matrix` and `has_targets` to $GITHUB_OUTPUT.

This is the skill-dir-granularity sibling of .github/skill-eval/plan_matrix.py;
it deliberately reuses that module's change-detection contract (the
`FETCH_HEAD...HEAD` cumulative-diff, the CHANGED_FILES test override, the
slug/duplicate guards) but drops the eval-specific spec/platform/adapter
expansion — a static review fans by skill dir, not by (spec, platform).

Rules:
  - any changed file under skills/<skill>/  -> review <skill>
  - matrix leg = (skill, paradigm) for each PARADIGM
  - a harness-only diff (no skills/** change) yields an empty matrix and the
    review job is skipped (validate the harness via workflow_dispatch).

Env:
    PR_BASE               base branch, diffed as FETCH_HEAD...HEAD (pull_request)
    MANUAL_SKILLS_FILTER  workflow_dispatch: `*` (all), a comma list, or a JSON
                          array of skill-dir names — reviewed instead of diffing
    CHANGED_FILES         optional newline-separated override (tests / local)
    GITHUB_OUTPUT         optional; key=value lines are appended here
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

# .github/skills-review/plan_review_matrix.py -> parents[2] = repo root
REPO_ROOT = Path(__file__).resolve().parents[2]
SKILLS_DIR = REPO_ROOT / "skills"

# Any tracked file under a skill dir puts the whole skill in review scope.
SKILL_FILE_RE = re.compile(r"^skills/([^/]+)/")
# A skill-dir name accepted from the workflow_dispatch input (path-escape guard).
SAFE_SKILL_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_-]*$")
# A leg's slug names its artifact (skills-review-<slug>) and its scratch path;
# enforce the token so a future name with a space/slash/colon fails the plan
# loudly instead of corrupting an artifact name or escaping a path.
SAFE_SLUG_RE = re.compile(r"^[A-Za-z0-9_-]+$")

# The six review paradigms (each its own parallel matrix leg). Order is the
# triage-report order; it does not affect parallelism.
PARADIGMS = [
    "review",            # VSS /review correctness rubric (diff-based)
    "gstack-review",     # gstack review rubric (SQL/trust-boundary/structure)
    "codex",             # codex adversarial second opinion
    "ce-code-review",    # compound-engineering code review (native JSON)
    "ce-doc-review",     # compound-engineering doc review (SKILL.md as a doc)
    "best-practices",    # Anthropic skill-authoring rubric (whole-skill read)
]


def list_changed_files() -> list[str]:
    """Changed files in the cumulative PR diff (base...head).

    Mirrors plan_matrix.list_changed_files() for the diff + CHANGED_FILES
    paths. Uses a local `git diff FETCH_HEAD...HEAD` (three-dot = merge-base
    ..head) rather than the GitHub compare API (which caps `.files` at 300).
    The manual workflow_dispatch path is handled in resolve_skills() instead.
    """
    override = os.environ.get("CHANGED_FILES")
    if override is not None:
        return [ln.strip() for ln in override.splitlines() if ln.strip()]

    base = os.environ["PR_BASE"]
    subprocess.run(
        ["git", "-C", str(REPO_ROOT), "fetch", "--no-tags", "--quiet",
         "origin", base],
        check=True,
    )
    out = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "diff", "--name-only", "FETCH_HEAD...HEAD"],
        check=True, capture_output=True, text=True,
    ).stdout
    return [ln.strip() for ln in out.splitlines() if ln.strip()]


def changed_skills(changed: list[str]) -> list[str]:
    """Sorted, deduped skill-dir names from a changed-file list (live dirs only)."""
    out: set[str] = set()
    for f in changed:
        m = SKILL_FILE_RE.match(f)
        # Only review live skill dirs — a deleted skill still shows in the diff.
        if m and (SKILLS_DIR / m.group(1)).is_dir():
            out.add(m.group(1))
    return sorted(out)


def parse_manual(manual: str) -> list[str]:
    """Resolve a workflow_dispatch `skills` input to validated skill names.

    Accepts three forms: `*` (all skill dirs), a JSON array `["a","b"]`, or a
    comma list `a,b` (or a single name). Every name is validated against the
    path-escape guard and confirmed to exist — a typo fails the plan loudly
    rather than emitting an empty matrix the review job would silently skip.
    """
    manual = manual.strip()
    if manual == "*":
        return sorted(p.name for p in SKILLS_DIR.iterdir() if p.is_dir())
    if manual.startswith("["):
        try:
            names = json.loads(manual)
        except ValueError as exc:
            raise ValueError(f"skills input is not valid JSON: {manual!r}") from exc
        if not isinstance(names, list):
            raise ValueError(f"skills JSON must be an array, got {type(names).__name__}")
    else:
        names = [s.strip() for s in manual.split(",") if s.strip()]

    for n in names:
        if not isinstance(n, str) or not SAFE_SKILL_RE.fullmatch(n):
            raise ValueError(
                f"unsafe skill name {n!r}: expected a skill-dir name "
                f"([A-Za-z0-9_-], not starting with '-')"
            )
        if not (SKILLS_DIR / n).is_dir():
            raise ValueError(
                f"skills/{n}/ does not exist on this ref — check the skill name"
            )
    return sorted(set(names))


def resolve_skills() -> list[str]:
    """The set of skills to review, from whichever trigger fired."""
    manual = os.environ.get("MANUAL_SKILLS_FILTER", "").strip()
    if manual:
        return parse_manual(manual)
    return changed_skills(list_changed_files())


def build_matrix(skills: list[str]) -> list[dict]:
    """Cartesian (skill x paradigm) -> one leg each."""
    include: list[dict] = []
    for skill in sorted(set(skills)):
        for paradigm in PARADIGMS:
            include.append({
                "skill": skill,
                "paradigm": paradigm,
                "slug": f"{skill}__{paradigm}",
                "name": f"{skill} · {paradigm}",
            })
    return include


def emit(include: list[dict]) -> None:
    # Fail fast on an unsafe slug before it names an artifact or scratch path.
    for leg in include:
        if not SAFE_SLUG_RE.match(leg["slug"]):
            raise ValueError(
                f"unsafe leg slug {leg['slug']!r}: skill / paradigm must match "
                f"[A-Za-z0-9_-] (the slug names the workflow artifact and the "
                f"scratch path)."
            )
    # Fail fast on a duplicate slug (would clobber a sibling leg's artifact).
    seen: set[str] = set()
    for leg in include:
        if leg["slug"] in seen:
            raise ValueError(f"duplicate leg slug {leg['slug']!r}")
        seen.add(leg["slug"])

    matrix = json.dumps({"include": include}, separators=(",", ":"))
    has_targets = "true" if include else "false"

    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a") as fh:
            fh.write(f"matrix={matrix}\n")
            fh.write(f"has_targets={has_targets}\n")

    print(f"has_targets={has_targets}")
    print(f"legs={len(include)}")
    for leg in include:
        print(f"  - {leg['name']}")
    print(f"matrix={matrix}")


def main() -> int:
    skills = resolve_skills()
    print(f"skills to review ({len(skills)}): {', '.join(skills) or '(none)'}",
          file=sys.stderr)
    emit(build_matrix(skills))
    return 0


if __name__ == "__main__":
    sys.exit(main())
