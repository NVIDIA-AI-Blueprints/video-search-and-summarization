#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Compute the skills-eval dispatch matrix from a PR diff.

Pure Python, no LLM. The only side effect is a local `git diff` against
the base ref to list changed files (skipped when CHANGED_FILES is
provided, which the unit tests use). Prints `matrix` and `has_targets`
to $GITHUB_OUTPUT so the workflow can fan out one `eval` leg per spec.

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
leg (that leg's agent commits the one adapter to the PR branch), so N specs
of an adapterless skill don't race to commit it N times.

Env:
    PR_BASE        base branch, e.g. develop (diffed as FETCH_HEAD...HEAD)
    MANUAL_SKILLS_FILTER  workflow_dispatch sweep: a skill-dir name or `*`
                   (all skills) — enumerates those specs instead of diffing,
                   so the matrix fans per-(spec, platform) like a push
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
# A leg's slug names its artifact (skills-eval-results-…-<slug>-…) and its
# scratch/results paths (/tmp/skill-eval/results/<slug>/…). Skill dirs, spec
# stems, and platform keys are safe today, but enforce the token so a future
# name with a space/slash/colon fails the plan loudly instead of silently
# corrupting an artifact name or escaping a path.
SAFE_SLUG_RE = re.compile(r"^[A-Za-z0-9_-]+$")

# `evals.json` (plural stem) is a legacy aggregate index — a JSON *array* of
# scenarios, not a dispatchable spec object. It has no `resources.platforms`,
# so spec_platforms() would choke on it (list has no .get), and the agent can't
# run it as a single spec. Real specs are named per scenario (deploy.json,
# routing.json, …). Skip `evals.json` everywhere a spec is discovered so it
# never becomes a matrix leg.
EXCLUDED_SPEC_NAMES = frozenset({"evals.json"})


def list_changed_files() -> list[str]:
    """Changed files in the cumulative PR diff (base...mirror head).

    Uses a local `git diff` rather than the GitHub compare API: the
    compare endpoint caps its `.files` array at 300 entries (and
    `--paginate` pages only the commits, not the files), so a PR touching
    >300 files would silently drop changed skills/specs and skip
    evaluating them. `git diff` has no such cap. The `plan` job checks out
    the mirror with fetch-depth: 0, so the merge-base is present; we fetch
    the base tip and diff `FETCH_HEAD...HEAD` (three-dot = merge-base..head,
    matching the old `base...mirror` compare semantics).
    """
    override = os.environ.get("CHANGED_FILES")
    if override is not None:
        return [ln.strip() for ln in override.splitlines() if ln.strip()]

    # Manual full-sweep (workflow_dispatch): there's no diff. Enumerate the
    # chosen skill(s)' specs so build_matrix fans them per-(spec,platform)
    # exactly like a push — this replaces the legacy single-agent sweep.
    # `*` sweeps every skill; otherwise a bare skill-dir name.
    manual = os.environ.get("MANUAL_SKILLS_FILTER")
    if manual:
        # workflow_dispatch input — guard against path escape before it
        # reaches specs_for_skill (which globs REPO_ROOT/skills/<filter>/…).
        if manual != "*" and not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_-]*", manual):
            raise ValueError(
                f"unsafe MANUAL_SKILLS_FILTER {manual!r}: expected a skill-dir "
                f"name ([A-Za-z0-9_-]) or '*'"
            )
        # Fail loud on a typo'd / renamed skill rather than emitting an empty
        # matrix that the eval job silently skips (the removed manual-sweep
        # job errored here too).
        if manual != "*" and not (REPO_ROOT / "skills" / manual).is_dir():
            raise ValueError(
                f"MANUAL_SKILLS_FILTER {manual!r}: skills/{manual}/ does not "
                f"exist on this ref — check the skill name"
            )
        skills = (
            sorted(p.name for p in (REPO_ROOT / "skills").iterdir() if p.is_dir())
            if manual == "*" else [manual]
        )
        return [sp for sk in skills for sp, _, _ in specs_for_skill(sk)]

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


def specs_for_skill(skill: str) -> list[tuple[str, str, str]]:
    """All (spec_path, eval_dir, stem) for a skill, sorted, existing only."""
    found: list[tuple[str, str, str]] = []
    for eval_dir in ("evals", "eval"):
        d = REPO_ROOT / "skills" / skill / eval_dir
        if not d.is_dir():
            continue
        for p in sorted(d.glob("*.json")):
            if p.name in EXCLUDED_SPEC_NAMES:
                continue
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
        # A changed `evals.json` is not a spec; let it fall through to the
        # whole-skill rule below (and specs_for_skill keeps it out of that
        # expansion too) rather than dispatching it as its own leg.
        if m and Path(f).name not in EXCLUDED_SPEC_NAMES:
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
            # One leg commits the single adapter for the whole skill.
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
    # Fail fast on an unsafe slug before anything downstream consumes it as
    # an artifact name or filesystem path.
    for leg in include:
        if not SAFE_SLUG_RE.match(leg["slug"]):
            raise ValueError(
                f"unsafe leg slug {leg['slug']!r}: skill / spec stem / "
                f"platform key must match [A-Za-z0-9_-] (the slug names the "
                f"workflow artifact and the scratch/results paths). Rename "
                f"the offending spec file or resources.platforms key."
            )

    # Fail fast on a duplicate slug. Two legs sharing a slug would clobber
    # each other's /tmp/skill-eval/results/<slug>/ dir and collide on the
    # upload-artifact name (v4 rejects duplicate names in one run). The slug
    # omits the eval dir, so the same stem in both `evals/` and the legacy
    # `eval/` of one skill collides; surface it here so the author drops the
    # stale spec rather than silently losing a leg's results.
    seen: dict[str, str] = {}
    for leg in include:
        prev = seen.get(leg["slug"])
        if prev is not None:
            raise ValueError(
                f"duplicate leg slug {leg['slug']!r}: {prev!r} and "
                f"{leg['spec_path']!r} resolve to the same slug (artifact "
                f"name + scratch path). Likely the same stem in both `evals/` "
                f"and the legacy `eval/` of one skill — remove the stale one."
            )
        seen[leg["slug"]] = leg["spec_path"]

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
