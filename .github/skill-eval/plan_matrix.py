#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Compute the skills-eval dispatch matrix from a PR diff.

Pure Python, no LLM. The only side effect is one `gh api …/compare` call
to list changed files (skipped when CHANGED_FILES is provided, which the
unit tests use). Prints `matrix` and `has_targets` to $GITHUB_OUTPUT so
the workflow can fan out one `eval` leg per spec.

Rules (see docs/matrix-dispatch-design.md):
  - skills/<skill>/evals/<spec>.json (or legacy eval/) changed
        -> dispatch just that (skill, spec)
  - any other skills/<skill>/** file changed (SKILL.md, references, ...)
        -> dispatch every spec under <skill>
  - .github/skill-eval/adapters/<skill>/** changed
        -> dispatch every spec under <skill>
  - harness files (envs/, verifiers/, skills_eval_agent.py, AGENTS.md,
    plan_matrix.py, skills-eval.yml) match no rule, so a harness-only
    diff yields an empty matrix and the eval job is skipped. Validate
    those via the manual workflow_dispatch sweep.

A skill whose adapter is missing collapses to a single `missing_adapter`
leg (that leg's agent raises the one bot-PR), so N specs of an adapterless
skill don't race to open N duplicate bot-PRs.

Env:
    PR_REPO        owner/repo (for the compare API)
    PR_BASE        base branch, e.g. develop
    PR_NUMBER      PR number -> compares base...pull-request/<N>
    CHANGED_FILES  optional newline-separated override (tests / local)
    GITHUB_OUTPUT  optional; when set, key=value lines are appended here
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

# .github/skill-eval/plan_matrix.py -> parents[2] = repo root
REPO_ROOT = Path(__file__).resolve().parents[2]
ADAPTERS_DIR = Path(__file__).resolve().parent / "adapters"

# A spec lives at skills/<skill>/evals/<stem>.json. `eval/` (singular) is
# the legacy location still accepted until every skill migrates.
SPEC_RE = re.compile(r"^skills/([^/]+)/(evals|eval)/([^/]+)\.json$")
# Any other tracked file under a skill dir -> the whole skill is in scope.
SKILL_FILE_RE = re.compile(r"^skills/([^/]+)/")
# An adapter edit re-scopes its whole skill (the adapter feeds every spec).
ADAPTER_RE = re.compile(r"^\.github/skill-eval/adapters/([^/]+)/")


def list_changed_files() -> list[str]:
    """Changed files in the cumulative PR diff (base...mirror head)."""
    override = os.environ.get("CHANGED_FILES")
    if override is not None:
        return [ln.strip() for ln in override.splitlines() if ln.strip()]

    repo = os.environ["PR_REPO"]
    base = os.environ["PR_BASE"]
    pr = os.environ["PR_NUMBER"]
    out = subprocess.run(
        ["gh", "api", "--paginate",
         f"repos/{repo}/compare/{base}...pull-request/{pr}",
         "--jq", ".files[].filename"],
        check=True, capture_output=True, text=True,
    ).stdout
    return [ln.strip() for ln in out.splitlines() if ln.strip()]


def specs_for_skill(skill: str) -> list[tuple[str, str, str]]:
    """All (spec_path, eval_dir, stem) for a skill, sorted, existing only."""
    found: list[tuple[str, str, str]] = []
    for eval_dir in ("evals", "eval"):
        d = REPO_ROOT / "skills" / skill / eval_dir
        if not d.is_dir():
            continue
        for p in sorted(d.glob("*.json")):
            rel = p.relative_to(REPO_ROOT).as_posix()
            found.append((rel, eval_dir, p.stem))
    return found


def adapter_exists(skill: str) -> bool:
    return (ADAPTERS_DIR / skill / "generate.py").is_file()


def spec_platforms(spec_path: str) -> list[str]:
    """Sorted platform keys from a spec's resources.platforms.

    One matrix leg is emitted per platform (the slug carries it), so a
    two-platform spec fans into two legs. A malformed or platform-less
    spec yields [] — the plan emits a single platform-less leg so the
    agent surfaces the `missing_platforms_declaration` blocker rather
    than the plan crashing.
    """
    try:
        data = json.loads((REPO_ROOT / spec_path).read_text())
        platforms = data.get("resources", {}).get("platforms", {})
        return sorted(platforms) if isinstance(platforms, dict) else []
    except (OSError, ValueError):
        return []


def build_matrix(changed: list[str]) -> list[dict]:
    # Explicitly-changed specs vs. skills pulled in wholesale by a non-spec
    # (or adapter) change. A spec reached by both paths appears once.
    changed_specs: set[str] = set()      # spec_path
    whole_skills: set[str] = set()       # skill name

    for f in changed:
        m = SPEC_RE.match(f)
        if m:
            changed_specs.add(f)
            continue
        m = SKILL_FILE_RE.match(f)
        if m:
            whole_skills.add(m.group(1))
            continue
        m = ADAPTER_RE.match(f)
        if m:
            whole_skills.add(m.group(1))
            # else: harness file or unrelated path -> contributes nothing.

    # Resolve to a de-duped (skill, spec_path) target set.
    targets: dict[str, tuple[str, str]] = {}  # spec_path -> (skill, eval_dir, stem) flattened
    target_meta: dict[str, dict] = {}

    def add_spec(skill: str, spec_path: str, eval_dir: str, stem: str) -> None:
        if spec_path in target_meta:
            return
        target_meta[spec_path] = {
            "skill": skill,
            "spec_path": spec_path,
            "spec_stem": stem,
            "eval_dir": eval_dir,
        }

    for spec_path in sorted(changed_specs):
        m = SPEC_RE.match(spec_path)
        skill, eval_dir, stem = m.group(1), m.group(2), m.group(3)
        # A deleted spec still shows in the diff; only dispatch live files.
        if (REPO_ROOT / spec_path).is_file():
            add_spec(skill, spec_path, eval_dir, stem)

    for skill in sorted(whole_skills):
        for spec_path, eval_dir, stem in specs_for_skill(skill):
            add_spec(skill, spec_path, eval_dir, stem)

    # Group surviving targets by skill so we can collapse adapterless skills.
    by_skill: dict[str, list[dict]] = {}
    for meta in target_meta.values():
        by_skill.setdefault(meta["skill"], []).append(meta)

    include: list[dict] = []
    for skill in sorted(by_skill):
        if not adapter_exists(skill):
            # One leg raises the single bot-PR for the whole skill.
            include.append({
                "skill": skill,
                "spec_path": "",
                "spec_stem": "missing-adapter",
                "platform": "",
                "kind": "missing_adapter",
                # `slug` is the unique per-leg key: path scope + artifact
                # name. For a real trial it's skill__spec_stem__platform.
                "slug": f"{skill}__missing-adapter",
                "name": f"{skill} · missing-adapter",
            })
            continue
        for meta in sorted(by_skill[skill], key=lambda m: m["spec_path"]):
            platforms = spec_platforms(meta["spec_path"]) or [""]
            for platform in platforms:
                plat_tag = platform or "no-platform"
                include.append({
                    "skill": skill,
                    "spec_path": meta["spec_path"],
                    "spec_stem": meta["spec_stem"],
                    "eval_dir": meta["eval_dir"],
                    "platform": platform,
                    "kind": "eval",
                    "slug": f"{skill}__{meta['spec_stem']}__{plat_tag}",
                    "name": f"{skill} · {meta['spec_stem']} · {plat_tag}",
                })
    return include


def emit(include: list[dict]) -> None:
    matrix = json.dumps({"include": include}, separators=(",", ":"))
    has_targets = "true" if include else "false"

    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a") as fh:
            fh.write(f"matrix={matrix}\n")
            fh.write(f"has_targets={has_targets}\n")

    # Human-readable trace for the Actions log.
    print(f"has_targets={has_targets}")
    print(f"legs={len(include)}")
    for leg in include:
        print(f"  - {leg['name']}  [{leg['kind']}]")
    print(f"matrix={matrix}")


def main() -> int:
    changed = list_changed_files()
    print(f"changed files ({len(changed)}):", file=sys.stderr)
    for f in changed:
        print(f"  {f}", file=sys.stderr)
    emit(build_matrix(changed))
    return 0


if __name__ == "__main__":
    sys.exit(main())
