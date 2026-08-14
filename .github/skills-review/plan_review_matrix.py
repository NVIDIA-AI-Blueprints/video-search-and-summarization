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

# A changed file is attributed to its owning skill by discover_skills() +
# skill_for_file() below (both flat skills/<name>/ and nested skills/<category>/<name>/).
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


def discover_skills() -> dict[str, Path]:
    """Map leaf skill-name -> skill dir for every dir under skills/ that holds a
    SKILL.md. Supports flat (skills/<name>/) and one category level
    (skills/<category>/<name>/). Leaf names are the identity and must be unique."""
    out: dict[str, Path] = {}
    if not SKILLS_DIR.is_dir():
        return out
    for md in sorted(SKILLS_DIR.rglob("SKILL.md")):
        d = md.parent
        rel = d.relative_to(SKILLS_DIR)
        if any(part.startswith((".", "_")) for part in rel.parts):
            continue
        if d.name in out and out[d.name] != d:
            raise ValueError(
                f"duplicate skill name {d.name!r}: {out[d.name]} and {d} — "
                f"skill leaf names must be unique across categories"
            )
        out[d.name] = d
    return out


def skill_for_file(path: str, skills: dict[str, Path]) -> str | None:
    """The skill (leaf name) that owns a repo-relative changed file — the skill
    dir that is the longest ancestor of the file. None if outside any live skill
    (a deleted skill's dir is absent from `skills`, so its files are ignored)."""
    if not path.startswith("skills/"):
        return None
    # Resolve the file against SKILLS_DIR's parent so it shares one root with
    # the discovered skill dirs.
    abs_file = SKILLS_DIR.parent / path
    best: str | None = None
    best_depth = -1
    for name, d in skills.items():
        try:
            abs_file.relative_to(d)
        except ValueError:
            continue
        if len(d.parts) > best_depth:
            best, best_depth = name, len(d.parts)
    return best


def changed_skills(changed: list[str]) -> list[str]:
    """Sorted, deduped skill leaf-names from a changed-file list (live dirs only)."""
    skills = discover_skills()
    return sorted({s for f in changed if (s := skill_for_file(f, skills))})


def parse_manual(manual: str) -> list[str]:
    """Resolve a workflow_dispatch `skills` input to validated skill names.

    Accepts three forms: `*` (all skill dirs), a JSON array `["a","b"]`, or a
    comma list `a,b` (or a single name). Every name is validated against the
    path-escape guard and confirmed to exist — a typo fails the plan loudly
    rather than emitting an empty matrix the review job would silently skip.
    """
    manual = manual.strip()
    skills = discover_skills()
    if manual == "*":
        return sorted(skills)
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
        if n not in skills:
            raise ValueError(
                f"skill {n!r} not found under skills/ on this ref — check the skill name"
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
