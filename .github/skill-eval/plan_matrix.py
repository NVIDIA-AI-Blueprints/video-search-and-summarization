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
    diff yields an empty matrix — except OPENSHELL_RTXPRO6000_ONLY, which
    enumerates every skill (like a daily sweep) so `/ok to test` runs the
    full RTX PRO 6000 matrix. Other SKUs stay skipped.

A skill whose adapter is missing collapses to a single `missing_adapter`
leg (that leg's agent commits the one adapter to the PR branch), so N specs
of an adapterless skill don't race to commit it N times.

Each leg also carries `runs_on`: the runner label set implied by the
spec's own `resources.platforms.<PLATFORM>` block (see runs_on_labels).
This resolves the spec -> hardware mapping at PLAN time, where today
run_leg.py re-derives it at LEG time from `brev ls` under a flock.
Nothing consumes `runs_on` yet — it is emitted so the mapping can be
reviewed against current placement before the GPU boxes are registered
as runners in their own right.

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

# A changed file is attributed to its owning skill by discover_skills() +
# skill_for_file() below (which handle both flat skills/<name>/ and nested
# skills/<category>/<name>/); `eval/` (singular) specs stay accepted via _spec_info.
# An adapter edit re-scopes its whole skill (the adapter feeds every spec); the
# adapters/ tree stays flat, keyed by the skill's leaf name.
ADAPTER_RE = re.compile(r"^\.github/skill-eval/adapters/([^/]+)/")
# A leg's slug names its artifact (skills-eval-results-…-<slug>-…) and its
# scratch/results paths (/tmp/skill-eval/results/<slug>/…). Skill dirs, spec
# stems, and platform keys are safe today, but enforce the token so a future
# name with a space/slash/colon fails the plan loudly instead of silently
# corrupting an artifact name or escaping a path.
SAFE_SLUG_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def discover_skills() -> dict[str, Path]:
    """Map leaf skill-name -> skill dir for every dir under skills/ holding a
    SKILL.md — flat (skills/<name>/) or one category level down
    (skills/<category>/<name>/). Leaf names are the identity and must be unique.
    Adapters stay keyed by this leaf name (the adapters/ tree is flat)."""
    out: dict[str, Path] = {}
    skills_root = REPO_ROOT / "skills"
    if not skills_root.is_dir():
        return out
    for md in sorted(skills_root.rglob("SKILL.md")):
        d = md.parent
        rel = d.relative_to(skills_root)
        if any(part.startswith(".") or part.startswith("_") for part in rel.parts):
            continue
        if d.name in out and out[d.name] != d:
            raise ValueError(
                f"duplicate skill name {d.name!r}: {out[d.name]} and {d} — "
                f"skill leaf names must be unique across categories"
            )
        out[d.name] = d
    return out


def skill_for_file(path: str, skills: dict[str, Path]) -> str | None:
    """Leaf name of the skill that owns a repo-relative file (its longest-ancestor
    skill dir), or None if the file is outside every live skill."""
    if not path.startswith("skills/"):
        return None
    abs_file = REPO_ROOT / path
    best: str | None = None
    best_depth = -1
    for name, d in skills.items():
        try:
            abs_file.relative_to(d)
        except ValueError:
            continue
        if len(d.parts) > best_depth:
            best, best_depth = name, len(d.parts)
    if best is not None:
        return best
    # Not under any discovered skill dir (e.g. a new skill dir not yet on disk):
    # fall back to the first path segment under skills/ (the flat layout).
    parts = path.split("/")
    return parts[1] if len(parts) >= 3 and parts[1] else None


def _spec_info(path: str, skill_reldir: str) -> tuple[str, str] | None:
    """(eval_dir, stem) if `path` is skill_reldir/(evals|eval)/<stem>.json directly."""
    for eval_dir in ("evals", "eval"):
        prefix = f"{skill_reldir}/{eval_dir}/"
        if path.startswith(prefix):
            rest = path[len(prefix):]
            if "/" not in rest and rest.endswith(".json"):
                return eval_dir, rest[:-5]
    return None

# `evals.json` (plural stem) is a legacy aggregate index — a JSON *array* of
# scenarios, not a dispatchable spec object. It has no `resources.platforms`,
# so spec_platforms() would choke on it (list has no .get), and the agent can't
# run it as a single spec. Real specs are named per scenario (deploy.json,
# routing.json, …). Skip `evals.json` everywhere a spec is discovered so it
# never becomes a matrix leg.
EXCLUDED_SPEC_NAMES = frozenset({"evals.json"})

# --- Runner labels -----------------------------------------------------
# Every leg carries a `runs_on` label set derived from the spec's own
# hardware declaration, so the eval job *can* be placed by Actions with
# `runs-on: ${{ matrix.runs_on }}` once the GPU boxes are registered as
# runners in their own right. NOTHING CONSUMES THIS YET — skills-eval.yml
# still pins the coordinator pool and run_leg.py still does fleet
# selection + flock. This computes and publishes the mapping so it can be
# reviewed and diffed against today's placement before any runner moves.

# Labels the GPU boxes themselves would carry. Deliberately NOT
# `vss-skill-eval-runner`: that label is on the coordinator's runner
# processes, which are not the machines the trials run on.
BASE_LABELS: tuple[str, ...] = ("self-hosted", "vss-eval")

# Dedicated 10.86.16.223 OpenShell RTX PRO 6000 cohort. GitHub still
# attaches `self-hosted` to the runner; workflows must not route on that
# label alone. `openshell-rtxpro6000-active` is the activation label:
# register without it (or keep the listener down) until a one-VM canary
# passes. Set OPENSHELL_RTXPRO6000_ONLY=1 to use these labels and skip
# every other GPU SKU on this path.
#
# Post-job destroy/recreate is host-side (gha_idle_recreate.sh watches
# Runner.Worker go idle, then recreate_fleet_vm.sh --apply --start-listener).
# This workflow does not implement KVM/VFIO.
OPENSHELL_RTXPRO6000_LABELS: tuple[str, ...] = (
    "vss-skill-eval-gpu",
    "openshell",
    "rtx-pro-6000",
    "gpu-rtxpro6000bw",
    "openshell-rtxpro6000-active",
)
SKIP_RUNNER = ["ubuntu-24.04"]
SMOKE_SPEC = "skills/vss-deploy-profile/evals/base.json"

# `resources.platforms` key -> GPU-type label. `ANY` is GPU-independent
# and contributes no `gpu-*` label. Keys mirror the PLATFORMS tables in
# .github/skill-eval/adapters/*/generate.py.
PLATFORM_LABELS: dict[str, str | None] = {
    "H100": "gpu-h100",
    "L40S": "gpu-l40s",
    "RTXPRO6000BW": "gpu-rtxpro6000bw",
    "DGX-SPARK": "gpu-dgx-spark",
    "IGX-THOR": "gpu-igx-thor",
    "ANY": None,
}

# run_leg.pool_candidates reads `int(metadata.get("gpu_count", 1) or 0)`:
# an ABSENT declaration means one GPU, while an explicit 0/null means
# GPU-independent. Mirror both so a label set places a leg exactly where
# the runtime selector would have. 15 of the 50 platform entries in
# skills/*/evals/ omit gpu_count today and rely on this default.
DEFAULT_GPU_COUNT = 1


def _platform_label(platform: str) -> str | None:
    """GPU-type label for a `resources.platforms` key."""
    if platform in PLATFORM_LABELS:
        return PLATFORM_LABELS[platform]
    # Unknown key: still emit something deterministic, but say so — a
    # typo'd platform would otherwise produce a label no box carries and
    # the job would queue until GitHub cancels it at 24 h.
    slug = re.sub(r"[^a-z0-9]+", "-", platform.lower()).strip("-")
    print(
        f"warning: unknown platform {platform!r} — no entry in "
        f"PLATFORM_LABELS; emitting {'gpu-' + slug if slug else '(none)'}",
        file=sys.stderr,
    )
    return f"gpu-{slug}" if slug else None


def _gpu_count(config: dict) -> int:
    """Declared GPU demand, matching run_leg's coercion exactly."""
    raw = config.get("gpu_count", DEFAULT_GPU_COUNT)
    try:
        return max(0, int(raw))
    except (TypeError, ValueError):
        # Explicit null / "" / garbage -> 0, same as run_leg's `or 0`.
        return 0


def runs_on_labels(platform: str, config: dict | None) -> list[str]:
    """Runner labels for one leg, from the spec's hardware declaration.

    `gpus-N` is a *demand*: the job asks for exactly N. A box advertises
    every count it can satisfy — a 2-GPU box carries both `gpus-1` and
    `gpus-2` — which is how pool_candidates' "over-provisioned boxes
    remain valid" rule survives a static label set.

    `gpu_count: 0` means GPU-independent and drops BOTH the `gpus-*` and
    the `gpu-*` label, so the leg can land on any box. That mirrors
    pool_candidates exactly: its type filter is guarded by
    `if required_count > 0 and required_type`, so a zero-GPU spec ignores
    the declared platform and "accepts any RUNNING box". 7 of the 50
    platform entries are zero-GPU today (the ANY specs, and
    detection-tracking-3d/routing on RTXPRO6000BW) — under labels they
    stop competing for GPU boxes at all.
    """
    count = _gpu_count(config) if config is not None else DEFAULT_GPU_COUNT
    if os.environ.get("OPENSHELL_RTXPRO6000_ONLY"):
        # RTXPRO6000BW — including gpu_count: 0 routing/calibration-chain —
        # must land on the OpenShell cohort. Never ubuntu-24.04: that runner
        # has no ~/.eval_env and no GPU, so the job fails env-load instead of
        # running or skipping honestly.
        if platform == "RTXPRO6000BW":
            labels = list(OPENSHELL_RTXPRO6000_LABELS)
            labels.append(f"gpus-{count}" if count > 0 else "gpus-1")
            return labels
        return list(SKIP_RUNNER)
    labels = list(BASE_LABELS)
    if count <= 0:
        return labels
    if platform:
        label = _platform_label(platform)
        if label:
            labels.append(label)
    labels.append(f"gpus-{count}")
    return labels


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
        skills_map = discover_skills()
        if manual != "*" and manual not in skills_map:
            raise ValueError(
                f"MANUAL_SKILLS_FILTER {manual!r}: skill not found under skills/ "
                f"on this ref — check the skill name"
            )
        skills = sorted(skills_map) if manual == "*" else [manual]
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


def specs_for_skill(skill: str, skills_map: dict[str, Path] | None = None) -> list[tuple[str, str, str]]:
    """All (spec_path, eval_dir, stem) for a skill, sorted, existing only.

    Resolves the skill's dir via discovery so a nested skills/<category>/<skill>/
    is found; falls back to the flat path for an unknown name."""
    if skills_map is None:
        skills_map = discover_skills()
    base = skills_map.get(skill, REPO_ROOT / "skills" / skill)
    found: list[tuple[str, str, str]] = []
    for eval_dir in ("evals", "eval"):
        d = base / eval_dir
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


def list_skill_file_paths(skills_dir: Path | None = None) -> list[str]:
    """Repo-relative paths to every SKILL.md file under the skills directory."""
    root = skills_dir or (REPO_ROOT / "skills")
    if not root.is_dir():
        return []
    return [
        p.relative_to(root.parent).as_posix()
        for p in sorted(root.rglob("SKILL.md"))
        if p.is_file()
    ]


def spec_platform_config(spec_path: str) -> dict[str, dict]:
    """A spec's `resources.platforms`, mapping key -> its config object.

    Returns {} for anything malformed, platform-less, or unreadable — the
    plan then emits a single platform-less leg so the agent surfaces the
    `missing_platforms_declaration` blocker rather than the plan crashing.
    A non-dict platform value (e.g. `"L40S": null`) yields {} for that key
    so callers can read defaults off it uniformly.
    """
    try:
        data = json.loads((REPO_ROOT / spec_path).read_text())
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    resources = data.get("resources")
    if not isinstance(resources, dict):
        return {}
    platforms = resources.get("platforms")
    if not isinstance(platforms, dict):
        return {}
    return {
        key: (value if isinstance(value, dict) else {})
        for key, value in platforms.items()
    }


def spec_platforms(spec_path: str) -> list[str]:
    """Sorted platform keys from a spec's resources.platforms.

    One matrix leg is emitted per platform (the slug carries it), so a
    two-platform spec fans into two legs.
    """
    return sorted(spec_platform_config(spec_path))


def build_matrix(changed: list[str]) -> list[dict]:
    # Explicitly-changed specs vs. skills pulled in wholesale by a non-spec
    # (or adapter) change. A spec reached by both paths appears once.
    skills_map = discover_skills()
    reldir = {name: d.relative_to(REPO_ROOT).as_posix() for name, d in skills_map.items()}
    changed_specs: dict[str, dict] = {}  # spec_path -> {skill, eval_dir, stem}
    whole_skills: set[str] = set()       # skill leaf name

    for f in changed:
        # A file inside a skill (flat or nested) belongs to that skill. Its
        # owner is the longest-ancestor skill dir, so a category dir with no
        # SKILL.md is never treated as a skill.
        owner = skill_for_file(f, skills_map)
        if owner is not None:
            si = _spec_info(f, reldir.get(owner) or f"skills/{owner}")
            # A changed `evals.json` is not a spec; fall through to whole-skill.
            if si and Path(f).name not in EXCLUDED_SPEC_NAMES:
                changed_specs[f] = {"skill": owner, "eval_dir": si[0], "stem": si[1]}
            else:
                whole_skills.add(owner)
            continue
        m = ADAPTER_RE.match(f)
        if m:
            whole_skills.add(m.group(1))
            # else: harness file or unrelated path -> contributes nothing.

    # Resolve to a de-duped (skill, spec_path) target set.
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
        info = changed_specs[spec_path]
        # A deleted spec still shows in the diff; only dispatch live files.
        if (REPO_ROOT / spec_path).is_file():
            add_spec(info["skill"], spec_path, info["eval_dir"], info["stem"])

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
                # Commits an adapter; runs no trial and needs no GPU.
                "runs_on": (
                    [*OPENSHELL_RTXPRO6000_LABELS, "gpus-1"]
                    if os.environ.get("OPENSHELL_RTXPRO6000_ONLY")
                    else list(BASE_LABELS)
                ),
            })
            continue
        for meta in sorted(by_skill[skill], key=lambda m: m["spec_path"]):
            platform_config = spec_platform_config(meta["spec_path"])
            platforms = sorted(platform_config) or [""]
            if os.environ.get("OPENSHELL_RTXPRO6000_ONLY"):
                platforms = [p for p in platforms if p == "RTXPRO6000BW"]
            for platform in platforms:
                plat_tag = platform or "no-platform"
                labels = runs_on_labels(
                    platform, platform_config.get(platform)
                )
                include.append({
                    "skill": skill,
                    "spec_path": meta["spec_path"],
                    "spec_stem": meta["spec_stem"],
                    "eval_dir": meta["eval_dir"],
                    "platform": platform,
                    "kind": "eval",
                    "slug": f"{skill}__{meta['spec_stem']}__{plat_tag}",
                    "name": f"{skill} · {meta['spec_stem']} · {plat_tag}",
                    "runs_on": labels,
                })
    if os.environ.get("OPENSHELL_RTXPRO6000_ONLY") and not any(
        leg.get("kind") == "eval" for leg in include
    ):
        # Harness-only diffs (no skills/ files) still need a GPU canary.
        # If the input named a skill and every RTXPRO6000BW leg was
        # filtered out, do not substitute vss-deploy-profile/base — that
        # would report Skills Eval success without testing the named skill.
        named_a_skill = any(f.startswith("skills/") for f in changed)
        if not named_a_skill:
            include.append({
                "skill": "vss-deploy-profile",
                "spec_path": SMOKE_SPEC,
                "spec_stem": "base",
                "eval_dir": "evals",
                "platform": "RTXPRO6000BW",
                "kind": "eval",
                "skip_reason": "",
                "slug": "vss-deploy-profile__base__RTXPRO6000BW",
                "name": "vss-deploy-profile · base · RTXPRO6000BW",
                "runs_on": [*OPENSHELL_RTXPRO6000_LABELS, "gpus-1"],
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
        # runs_on is trace only and nothing consumes it yet, so a leg built
        # without it must not fail the plan — unlike slug, which is checked
        # strictly above because downstream paths depend on it.
        runs_on = " ".join(leg.get("runs_on") or []) or "-"
        print(f"  - {leg['name']}  [{leg['kind']}]  runs_on={runs_on}")
    print(f"matrix={matrix}")


def main() -> int:
    DAILY_RUN = os.environ.get("DAILY_RUN")
    # OpenShell `/ok to test` must run every RTX PRO 6000 spec on this
    # fleet, not only the files in the PR diff (and not only the
    # vss-deploy-profile/base smoke fallback). Other SKUs stay skipped
    # inside build_matrix via OPENSHELL_RTXPRO6000_ONLY.
    if DAILY_RUN or os.environ.get("OPENSHELL_RTXPRO6000_ONLY"):
        changed = list_skill_file_paths()
    else:
        changed = list_changed_files()
    print(f"changed files ({len(changed)}):", file=sys.stderr)
    for f in changed:
        print(f"  {f}", file=sys.stderr)
    emit(build_matrix(changed))
    return 0


if __name__ == "__main__":
    sys.exit(main())
