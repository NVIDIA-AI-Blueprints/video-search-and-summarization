#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Generate Harbor tasks for the vss-run-gym-eval skill.

The vss-run-gym-eval skill scores a VSS deployment with NVIDIA NeMo Gym.
Its eval specs exercise two offline capabilities that require NO running
deployment and NO GPU:

1. **delta_adds_only_the_runner** — compose the Gym evaluation delta on a
   Foundation into `_builds/gym-eval-check/` without deploying, and verify
   the delta matches the contract (one extra service key, no modifications
   to checked-in files).

2. **image_gate_rejects_unsafe_tag** — run the image gate against a known
   codec-bearing tag (`26.05`) and verify it correctly rejects the tag
   without pulling the image.

Both specs declare `gpu_count: 0` and platform `ANY`, so the task runs on
whichever pool box the coordinator picks (no GPU requirement). The adapter
generates one task per (spec, platform) combination.

Directory layout:
    <output_root>/base/<platform_short>/
        task.toml
        instruction.md
        tests/test.sh
        tests/<spec_name>              (the spec JSON)
        tests/generic_judge.py
        solution/solve.sh
        skills/vss-run-gym-eval/          (full skill copy)
        skills/vss-build-vision-agent/    (required — delta.md links into it)
        skills/vss-deploy-profile/        (optional, for agent context)
        environment/Dockerfile

Usage from the repository root:
    python3 .github/skill-eval/adapters/vss-run-gym-eval/generate.py \\
        --output-dir /tmp/skill-eval/datasets/<leg-slug>/<run_id> \\
        --skill-dir skills/vss-run-gym-eval \\
        --spec skills/vss-run-gym-eval/evals/delta_adds_only_the_runner.json \\
        --platform ANY
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from pathlib import Path

# The harness checks out the repository here. Spec checks and the gold
# solution must both address the tree by absolute path: the agent's cwd is
# whatever the pool box hands it (observed: /home/shadeform), so a check
# written as `test -f _builds/...` resolves against the wrong directory and
# fails an otherwise correct run.
HARNESS_REPO_ROOT = "$HOME/video-search-and-summarization"

# ---------------------------------------------------------------------------
# Platforms — gpu_count=0 specs accept any pool box
# ---------------------------------------------------------------------------

PLATFORMS: dict[str, dict] = {
    "ANY": {
        "short_name": "any",
        "gpu_type": "",
        "gpu_count": 0,
        "min_vram_per_gpu": 0,
        "brev_search": "",
    },
}

DEFAULT_PLATFORM = "ANY"

# Prepended to every instruction.md so the skill's own HITL bypass
# clause fires. Skills default to "ask the user" before /vss-deploy-profile; in CI
# there's no user, so without this preamble the agent either stalls or
# falls through to a localhost default.
PREAMBLE = (
    "You are running inside a non-interactive evaluation harness. "
    "You are pre-authorized to deploy prerequisites autonomously — "
    "do not pause to ask for confirmation on `/vss-deploy-profile` or any other "
    "setup action the trial requires."
)


# ---------------------------------------------------------------------------
# Generation
# ---------------------------------------------------------------------------

GENERIC_JUDGE = Path(__file__).resolve().parents[2] / "verifiers" / "generic_judge.py"

# `{{repo_root}}` only — `{{.Names}}` and friends in the specs' docker
# --format strings are left alone, since `\w+` does not match a leading dot.
_PLACEHOLDER = re.compile(r"\{\{\s*(\w+)\s*\}\}")


def _substitute_spec(spec: dict, platform: str) -> dict:
    """Resolve {{repo_root}} / {{platform}} in every string in the spec.

    The rendered copy is what lands in tests/ — copying the spec verbatim
    would ship unresolved placeholders to the judge, which then evaluates
    checks against a literal `{{repo_root}}` path that cannot exist.
    """
    substitutions = {"platform": platform, "repo_root": HARNESS_REPO_ROOT}

    def _sub(value):
        if isinstance(value, str):
            return _PLACEHOLDER.sub(
                lambda m: str(substitutions.get(m.group(1), m.group(0))), value
            )
        if isinstance(value, list):
            return [_sub(v) for v in value]
        if isinstance(value, dict):
            return {k: _sub(v) for k, v in value.items()}
        return value

    return _sub(spec)


def generate_test_script(step: int, spec_name: str) -> str:
    """Shell wrapper that invokes the generic LLM-as-judge verifier for a
    single step's checks. Harbor reads /logs/verifier/reward.txt."""
    return (
        "#!/bin/bash\n"
        f"# vss-run-gym-eval verifier (step {step}): delegates to the generic\n"
        "# LLM-as-judge (.github/skill-eval/verifiers/generic_judge.py).\n"
        "set -uo pipefail\n"
        "\n"
        'TEST_DIR="$(cd "$(dirname "$0")" && pwd)"\n'
        "python3 -m pip install --quiet 'anthropic>=0.40.0' >/dev/null 2>&1 || true\n"
        "\n"
        'python3 "$TEST_DIR/generic_judge.py" \\\n'
        f'    --spec "$TEST_DIR/{spec_name}" --step {step}\n'
        "exit 0\n"
    )


_SOLVE_DELTA = r"""#!/bin/bash
# Gold solution: compose the Gym evaluation delta exactly as
# references/delta.md specifies. Nothing is pulled and nothing is started.
set -euo pipefail

REPO_ROOT="__REPO_ROOT__"
export VSS_APPS_DIR="${REPO_ROOT}/deploy/docker"
export VSS_DATA_DIR="${VSS_DATA_DIR:-${REPO_ROOT}/data}"
# The runner's image pin is fail-closed (${VAR:?}) with deliberately no
# default, so composition needs a tag even though nothing is pulled here.
export VSS_GYM_EVAL_TAG="${VSS_GYM_EVAL_TAG:-gate-pending}"

# The Foundation's Compose entry point is the ROOT deploy/docker/compose.yml.
# Neither developer-profiles/compose.yml nor the per-profile file works: both
# are fragments, and services the lvs profile depends on (kibana lives in
# services/infra/compose.yml) are only pulled in by the root file. Pointing the
# delta at a fragment yields "service kibana-init-container-lvs depends on
# undefined service kibana" -- the profile-filtering hazard delta.md warns
# about, and it fails identically for the bare Foundation, so a delta built on
# a fragment would look broken when the fragment was the mistake.
FOUNDATION_COMPOSE="${VSS_APPS_DIR}/compose.yml"
FOUNDATION_ENV="${VSS_APPS_DIR}/developer-profiles/dev-profile-lvs/overrides.env"
BUILD_DIR="${REPO_ROOT}/_builds/gym-eval-check"
mkdir -p "${BUILD_DIR}/patches"

# The Foundation's checked-in overrides.env is authoritative for its service
# set. Add exactly one key; remove none.
FOUNDATION_PROFILES=$(grep -E '^COMPOSE_PROFILES=' "${FOUNDATION_ENV}" \
  | tail -1 | cut -d= -f2-)
[ -n "${FOUNDATION_PROFILES}" ] || {
    echo "no COMPOSE_PROFILES in ${FOUNDATION_ENV}"; exit 1; }

cat > "${BUILD_DIR}/patches/gym-eval.yml" <<'YAML'
services:
  gym-eval:
    image: ${VSS_GYM_EVAL_IMAGE:-nvcr.io/nvidia/eval-factory/nemo-gym}:${VSS_GYM_EVAL_TAG:?set only after the image gate in SKILL.md passes}
    profiles: ["gym-eval"]
    container_name: vss-gym-eval
    restart: "no"
    environment:
      - VSS_GYM_EVAL_OUTPUT_DIR=${VSS_GYM_EVAL_OUTPUT_DIR:-/workspace/outputs}
    volumes:
      - ${VSS_DATA_DIR}/gym_eval:/workspace/outputs
YAML

# The checked-in overrides.env ships unresolved host placeholders
# (VSS_APPS_DIR="/path/to/deploy/docker", VSS_DATA_DIR="/path/to/vss-apps-data"),
# so copying it verbatim yields an override.env that only works while this
# script's own exports are in scope. Rewrite them to real paths, or the build
# artifact is not self-contained the way the layout in delta.md implies.
grep -v -E '^(COMPOSE_PROFILES|VSS_APPS_DIR|VSS_DATA_DIR)=' "${FOUNDATION_ENV}" \
    > "${BUILD_DIR}/override.env"
{
    echo "COMPOSE_PROFILES=${FOUNDATION_PROFILES},gym-eval"
    echo "VSS_APPS_DIR=${VSS_APPS_DIR}"
    echo "VSS_DATA_DIR=${VSS_DATA_DIR}"
    echo "VSS_GYM_EVAL_OUTPUT_DIR=/workspace/outputs"
} >> "${BUILD_DIR}/override.env"

cat > "${BUILD_DIR}/compose.yml" <<YAML
include:
  - ${FOUNDATION_COMPOSE}
  - ${BUILD_DIR}/patches/gym-eval.yml
YAML

docker compose --env-file "${BUILD_DIR}/override.env" \
    -f "${BUILD_DIR}/compose.yml" config > "${BUILD_DIR}/resolved.yml"

for artifact in override.env compose.yml resolved.yml patches/gym-eval.yml; do
    [ -f "${BUILD_DIR}/${artifact}" ] || {
        echo "missing build artifact: ${BUILD_DIR}/${artifact}"; exit 1; }
done

# The delta adds exactly one service to the Foundation -- no more, none removed.
DELTA=$(diff \
    <(docker compose --env-file "${FOUNDATION_ENV}" \
        -f "${FOUNDATION_COMPOSE}" config --services 2>/dev/null | sort) \
    <(docker compose --env-file "${BUILD_DIR}/override.env" \
        -f "${BUILD_DIR}/compose.yml" config --services 2>/dev/null | sort) \
    | grep -E '^[<>]' || true)
[ "${DELTA}" = "> gym-eval" ] || {
    echo "delta drifted from its Foundation:"; echo "${DELTA}"; exit 1; }

# Fail-closed pin: with the tag unset, resolution must fail AND say why.
# Accepting any nonzero status would let an unrelated Compose error masquerade
# as the gate working, which is the same fail-open shape this whole spec exists
# to catch. `2>&1 >/dev/null` captures stderr only.
if FAILCLOSED_ERR=$(env -u VSS_GYM_EVAL_TAG docker compose \
        --env-file "${BUILD_DIR}/override.env" \
        -f "${BUILD_DIR}/compose.yml" config --quiet 2>&1 >/dev/null); then
    echo "runner tag is not fail-closed: config succeeded with VSS_GYM_EVAL_TAG unset"
    exit 1
fi
case "${FAILCLOSED_ERR}" in
    *VSS_GYM_EVAL_TAG*) ;;
    *) echo "config failed with the tag unset, but not because of VSS_GYM_EVAL_TAG:"
       echo "${FAILCLOSED_ERR}"; exit 1 ;;
esac

echo "Composed ${BUILD_DIR}: Foundation + gym-eval only, nothing started."
"""

_SOLVE_IMAGE_GATE = r"""#!/bin/bash
# Gold solution: run the SKILL.md image gate against a tag that must be
# rejected, and assert it rejects without pulling the image.
set -uo pipefail

REPO=nvidia/eval-factory/nemo-gym
TAG=26.05

run_gate() (
    set -euo pipefail
    TOK=$(curl -fsS "https://nvcr.io/proxy_auth?scope=repository:${REPO}:pull" \
        | jq -er .token) || { echo "GATE FAIL: no registry token"; exit 1; }
    AMD=$(curl -fsS -H "Authorization: Bearer $TOK" \
        -H "Accept: application/vnd.docker.distribution.manifest.list.v2+json, application/vnd.oci.image.index.v1+json" \
        "https://nvcr.io/v2/${REPO}/manifests/${TAG}" \
        | jq -r '.manifests[]? | select(.platform.architecture=="amd64" and .platform.os=="linux") | .digest' | head -1)
    [ -n "$AMD" ] || { echo "GATE FAIL: no linux/amd64 manifest"; exit 1; }
    CFG=$(curl -fsS -H "Authorization: Bearer $TOK" \
        -H "Accept: application/vnd.docker.distribution.manifest.v2+json, application/vnd.oci.image.manifest.v1+json" \
        "https://nvcr.io/v2/${REPO}/manifests/${AMD}" | jq -r '.config.digest')
    [ -n "$CFG" ] || { echo "GATE FAIL: no config blob digest"; exit 1; }
    BLOB=$(curl -fsSL -H "Authorization: Bearer $TOK" \
        "https://nvcr.io/v2/${REPO}/blobs/${CFG}") \
        || { echo "GATE FAIL: config blob fetch failed"; exit 1; }
    [ -n "$BLOB" ] || { echo "GATE FAIL: empty config blob"; exit 1; }
    CREATED=$(echo "$BLOB" | jq -er '.created') \
        || { echo "GATE FAIL: no .created"; exit 1; }
    HIST_TOTAL=$(echo "$BLOB" | jq -r '.history | length')
    HIST_READABLE=$(echo "$BLOB" | jq -r '[.history[] | select(type=="object") | .created_by | select(type=="string" and length > 0)] | length')
    [ "${HIST_TOTAL:-0}" -gt 0 ] || { echo "GATE FAIL: empty .history"; exit 1; }
    [ "${HIST_READABLE:-0}" -eq "${HIST_TOTAL}" ] \
        || { echo "GATE FAIL: history not inspectable"; exit 1; }
    CODEC_LAYERS=$(echo "$BLOB" | jq -r '.history[].created_by // empty' \
        | grep -cE 'ffmpeg|libav|x264|x265' || true)
    echo "created: ${CREATED}"
    echo "codec-installing layers: ${CODEC_LAYERS}"
    if [ "${CODEC_LAYERS}" -ne 0 ]; then
        echo "$BLOB" | jq -r '.history[].created_by // empty' \
            | grep -E 'ffmpeg|libav|x264|x265' | head -1 \
            | sed 's/^/matched layer: /' || true
    fi
    FIX_EPOCH=$(date -u -d '2026-08-12' +%s)
    CREATED_EPOCH=$(date -u -d "${CREATED}" +%s) \
        || { echo "GATE FAIL: unparseable .created"; exit 1; }
    [ "${CREATED_EPOCH}" -ge "${FIX_EPOCH}" ] \
        || { echo "GATE FAIL: build predates the codec-removal cutoff -- do not pull ${TAG}"; exit 1; }
    [ "${CODEC_LAYERS}" -eq 0 ] \
        || { echo "GATE FAIL: ${CODEC_LAYERS} codec layer(s) -- do not pull ${TAG}"; exit 1; }
    echo "GATE PASS"
)

OUT=$(run_gate); RC=$?
echo "${OUT}"

# A rejection only counts if the gate actually READ the metadata. Every failure
# branch prints "GATE FAIL", so without these markers an expired token, a
# registry outage or a missing jq is indistinguishable from a correct verdict on
# a codec-bearing image -- the oracle would report success for a run that
# established nothing.
for marker in '^created: ' '^codec-installing layers: ' '^matched layer: '; do
    echo "${OUT}" | grep -q "${marker}" || {
        echo "gate produced no '${marker}' line -- infrastructure failure, not a verdict"
        exit 1; }
done

# This tag MUST be rejected, and for one of the two reasons the gate exists for.
[ "${RC}" -ne 0 ] || { echo "gate accepted ${TAG}, which it must reject"; exit 1; }
echo "${OUT}" | grep -qE 'GATE FAIL: (build predates|[0-9]+ layer\(s\) install codec|[0-9]+ codec)' || {
    echo "gate failed, but not on provenance -- wrong reason:"; echo "${OUT}"; exit 1; }

# ...and it must have reached that verdict on metadata alone. A docker CLI that
# cannot list images proves nothing, so treat that as fatal rather than as
# evidence of absence.
DOCKER_IMAGES=$(docker images --format '{{.Repository}}') || {
    echo "cannot list docker images -- cannot establish that nothing was pulled"
    exit 1; }
case "${DOCKER_IMAGES}" in
    *nemo-gym*) echo "a nemo-gym image is present -- the gate must not pull"; exit 1 ;;
esac

echo "Gate correctly rejected ${TAG} without pulling it."
"""


def generate_solve_script(platform: str, spec_stem: str) -> str:
    """Gold solution — a real oracle, one per spec.

    These specs are offline (no deployment, no GPU), but "offline" is not
    "nothing to do": the delta spec's checks assert build artifacts that
    only exist once the delta is composed, and the gate spec's checks
    assert a verdict that only exists once the gate has run. A solve
    script that merely echoes cannot satisfy either, so it would report
    the task unsolvable regardless of the skill's quality.
    """
    if spec_stem.startswith("delta_"):
        body = _SOLVE_DELTA.replace("__REPO_ROOT__", HARNESS_REPO_ROOT)
    elif spec_stem.startswith("image_gate_"):
        body = _SOLVE_IMAGE_GATE
    else:
        raise SystemExit(
            f"no gold solution for spec '{spec_stem}' — add one to "
            f"{Path(__file__).name} rather than shipping a stub that "
            f"cannot satisfy the spec's checks"
        )
    return body.replace(
        "#!/bin/bash\n",
        f"#!/bin/bash\n# Gold solution: vss-run-gym-eval/{spec_stem} on {platform}\n",
        1,
    )


def generate_task(platform: str, spec: dict, output_root: Path,
                  skill_dir: Path, deploy_skill_dir: Path | None) -> None:
    """Emit one Harbor task directory per entry in spec['expects'].
    Single-step specs collapse to a flat `base/<platform_short>/`."""
    pspec = PLATFORMS[platform]
    platform_short = pspec["short_name"]
    spec_name = Path(spec.get("_source_path", "spec.json")).name or "spec.json"
    spec_stem = Path(spec_name).stem
    rendered_spec = _substitute_spec(spec, platform)
    expects = rendered_spec.get("expects") or []

    for idx, expect in enumerate(expects, 1):
        step_dir = output_root / "base" / platform_short
        if len(expects) > 1:
            step_dir = step_dir / f"step-{idx}"
        step_dir.mkdir(parents=True, exist_ok=True)

        # instruction.md — ONE step's query + environment notes ONLY.
        lines = [
            PREAMBLE,
            "",
            f"Use the `/vss-run-gym-eval` skill. Work from "
            f"`{HARNESS_REPO_ROOT}` (the VSS repository root) — the shell "
            f"you start in is not the checkout.",
            "",
            f"## Query {idx} of {len(expects)}",
            "",
            expect.get("query", ""),
            "",
            "Run autonomously without prompting for confirmation.",
            "",
        ]
        (step_dir / "instruction.md").write_text("\n".join(lines) + "\n")

        # task.toml
        step_suffix = f"-step-{idx}" if len(expects) > 1 else ""
        meta_lines = [
            "[task]",
            f'name = "nvidia-vss/vss-run-gym-eval-base-{platform_short}{step_suffix}"',
            f'description = "vss-run-gym-eval query {idx}/{len(expects)} on {platform}"',
            f'keywords = ["vss-run-gym-eval", "nemo-gym", "base", "{platform}"]',
            "",
            "[agent]",
            "timeout_sec = 600.0",
            "",
            "[environment]",
            'skills_dir = "/skills"',
            "",
            "[verifier.env]",
            'ANTHROPIC_API_KEY = "${ANTHROPIC_API_KEY}"',
            'ANTHROPIC_BASE_URL = "${ANTHROPIC_BASE_URL}"',
            'ANTHROPIC_MODEL = "${ANTHROPIC_MODEL}"',
            "",
            "[metadata]",
            f'skill = "vss-run-gym-eval"',
            f'platform = "{platform}"',
            f'gpu_type = "{pspec["gpu_type"]}"',
            f'gpu_count = {pspec["gpu_count"]}',
            f'brev_search = "{pspec["brev_search"]}"',
            f'min_vram_gb_per_gpu = {pspec["min_vram_per_gpu"]}',
            f"step_index = {idx}",
            f"step_count = {len(expects)}",
            f"check_count = {len(expect.get('checks') or [])}",
            "",
        ]
        (step_dir / "task.toml").write_text("\n".join(meta_lines))

        # environment/
        env_dir = step_dir / "environment"
        env_dir.mkdir(exist_ok=True)
        (env_dir / "Dockerfile").write_text("FROM scratch\n")

        # tests/ — wrapper + generic judge + spec
        tests_dir = step_dir / "tests"
        tests_dir.mkdir(exist_ok=True)
        (tests_dir / "test.sh").write_text(generate_test_script(idx, spec_name))
        if GENERIC_JUDGE.exists():
            shutil.copy(GENERIC_JUDGE, tests_dir / "generic_judge.py")
        # Write the RENDERED spec, never a verbatim copy: the checks address
        # the checkout by absolute path via {{repo_root}}, and an unrendered
        # placeholder would have the judge test a path that cannot exist.
        (tests_dir / spec_name).write_text(json.dumps(rendered_spec, indent=2))

        # solution/
        solution_dir = step_dir / "solution"
        solution_dir.mkdir(exist_ok=True)
        (solution_dir / "solve.sh").write_text(
            generate_solve_script(platform, spec_stem)
        )

        # skills/ — the skill under test plus the siblings it points at.
        #
        # vss-build-vision-agent is not optional context: references/delta.md
        # links to ../../vss-build-vision-agent/references/composition.md for
        # the delta contract this skill documents an exception to. Without it
        # bundled alongside, that relative link dangles and the agent cannot
        # read the contract it is being asked to depart from.
        siblings = skill_dir.parent
        for src, name in (
            (skill_dir, "vss-run-gym-eval"),
            (siblings / "vss-build-vision-agent", "vss-build-vision-agent"),
            (deploy_skill_dir or siblings / "vss-deploy-profile",
             "vss-deploy-profile"),
        ):
            if src and src.exists():
                dst = step_dir / "skills" / name
                if dst.exists():
                    shutil.rmtree(dst)
                shutil.copytree(src, dst)
            elif name == "vss-build-vision-agent":
                raise SystemExit(
                    f"{name} not found at {src} — references/delta.md links "
                    f"into it, so the generated task would ship a broken "
                    f"cross-skill reference"
                )

        # Harbor executes these directly.
        for script in (tests_dir / "test.sh", solution_dir / "solve.sh"):
            script.chmod(0o755)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--output-dir", required=True,
        help="Dataset output root",
    )
    parser.add_argument(
        "--skill-dir", required=True,
        help="Path to skills/vss-run-gym-eval",
    )
    parser.add_argument(
        "--deploy-skill-dir", default=None,
        help="Path to skills/vss-deploy-profile (optional — included for agent context)",
    )
    parser.add_argument(
        "--spec", default=None,
        help="Path to the eval spec JSON "
             "(default: auto-detect from <skill-dir>/evals/)",
    )
    parser.add_argument(
        "--platform", default=None,
        choices=list(PLATFORMS.keys()),
        help=f"Generate for this platform only (default: {DEFAULT_PLATFORM})",
    )
    args = parser.parse_args()

    output_root = Path(args.output_dir)
    skill_dir = Path(args.skill_dir)
    deploy_skill_dir = Path(args.deploy_skill_dir) if args.deploy_skill_dir else None

    if args.spec:
        spec_path = Path(args.spec)
    else:
        # Auto-detect: pick the first spec with the required structure
        evals_dir = skill_dir / "evals"
        if not evals_dir.exists():
            evals_dir = skill_dir / "eval"
        candidates = sorted(evals_dir.glob("*.json")) if evals_dir.exists() else []
        spec_path = None
        for cand in candidates:
            try:
                data = json.loads(cand.read_text())
                if isinstance(data, dict) and "expects" in data and "resources" in data:
                    spec_path = cand
                    break
            except (json.JSONDecodeError, OSError):
                continue
        if not spec_path:
            print("No evaluable spec found under the skill's evals/ directory",
                  file=sys.stderr)
            sys.exit(1)

    if not spec_path.exists():
        print(f"spec not found: {spec_path}", file=sys.stderr)
        sys.exit(1)
    spec = json.loads(spec_path.read_text())
    # Routing evals (list format) are not harness specs
    if isinstance(spec, list):
        print(f"spec {spec_path} is a routing eval (list), not a harness spec",
              file=sys.stderr)
        sys.exit(1)
    spec["_source_path"] = str(spec_path)

    platform = args.platform or DEFAULT_PLATFORM
    platforms = [platform]

    print("=== Inputs ===")
    print(f"  output_dir   : {output_root}")
    print(f"  skill_dir    : {skill_dir}")
    print(f"  spec         : {spec_path}")
    print(f"  platforms    : {platforms}")
    print(f"  queries      : {len(spec.get('expects', []))}")
    print(f"  total checks : {sum(len(q.get('checks', [])) for q in spec.get('expects', []))}")
    print()
    for plat in platforms:
        task_id = PLATFORMS[plat]["short_name"]
        print(f"  GEN  vss-run-gym-eval/base/{task_id}")
        generate_task(plat, spec, output_root, skill_dir, deploy_skill_dir)
    print()
    print(f"Generated {len(platforms)} task(s) under {output_root}/base/")


if __name__ == "__main__":
    main()
