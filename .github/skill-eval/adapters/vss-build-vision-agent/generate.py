#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Generate Harbor tasks for the vss-build-vision-agent skill.

The skill translates a natural-language capability description into either a
stock developer-profile deployment or a delta overlay on exactly one current
developer profile. Build artifacts live under `_builds/<name>/` and always
contain `override.env`, `compose.yml`, and a directly deployable
`resolved.yml`. Optional service-definition patches live under `patches/`.

The specs exercise profile routing, canonical service selection, lean artifact
generation, Compose validation, and optional runtime deployment. The spec's
`profile` field is only the build-directory label recorded by the harness; it
is never a Compose profile.

## Platform topology

    "2xRTXPro" → g7e.12xlarge with 2× RTX PRO 6000 Blackwell
                  (gpu_type="RTX PRO 6000", gpu_count=2, min_vram=96 GB/GPU)
                  Pool member: vss-eval-rtx-2g

## Directory layout

    .github/skill-eval/datasets/vss-build-vision-agent/<spec_stem>/<platform_short>/
        task.toml
        instruction.md
        tests/test.sh
        tests/<spec>.json              (rendered — {{platform}}/{{repo_root}} substituted)
        tests/generic_judge.py
        solution/solve.sh
        skills/vss-build-vision-agent/   (full skill copy)
        skills/vss-deploy-dense-captioning/   (bundled for dense-captioning checks)
        skills/vss-deploy-detection-tracking-2d/ (bundled for RT-CV checks)
        skills/vss-deploy-video-embedding/   (bundled for RT-Embed checks)
        skills/vss-summarize-video/      (bundled for LVS summarize API checks)
        environment/Dockerfile           (FROM scratch; BrevEnvironment takes over)

Usage from the repository root:
    python3 .github/skill-eval/adapters/vss-build-vision-agent/generate.py \\
        --output-dir .github/skill-eval/datasets/vss-build-vision-agent \\
        --skill-dir skills/vss-build-vision-agent \\
        --vios-skill-dir skills/vss-manage-video-io-storage \\
        --rtvi-skill-dir skills/vss-deploy-dense-captioning \\
        --rtcv-skill-dir skills/vss-deploy-detection-tracking-2d \\
        --rtembed-skill-dir skills/vss-deploy-video-embedding \\
        --summarize-skill-dir skills/vss-summarize-video \\
        --spec skills/vss-build-vision-agent/eval/profile_in_1_streaming_dense_captions.json
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Platform table — maps spec platform keys to brev_env task.toml metadata.
# The "2xRTXPro" key is specific to this skill (requires 2 GPUs for the
# full IN-1 stack: RT-VLM in-process + SDRC + VIOS).
# ---------------------------------------------------------------------------

HARNESS_REPO_ROOT = "$HOME/video-search-and-summarization"

_GYM_PREAMBLE = (
    "You are running inside a non-interactive evaluation harness. Work "
    "autonomously and do not pause for confirmation. This task is OFFLINE: "
    "do not deploy anything, do not start a container, and do not pull an image."
)

PLATFORMS: dict[str, dict] = {
    # Offline specs (the Gym evaluation overlay) need no GPU and no
    # particular box; the spec declares gpu_count 0 and the coordinator
    # picks whatever is free.
    "ANY": {
        "short_name":       "any",
        "gpu_type":         "",
        "gpu_count":        0,
        "min_vram_per_gpu": 0,
        "brev_search":      "",
        "min_root_disk_gb": 0,
    },
    # Primary target for vss-build-vision-agent IN-1
    # Key matches the spec's resources.platforms declaration ("RTXPRO6000BW")
    # and the platform naming convention shared by the VSS eval adapters.
    "RTXPRO6000BW": {
        "short_name":       "rtxpro6000bw",
        "gpu_type":         "RTX PRO 6000",
        "gpu_count":        1,
        "min_vram_per_gpu": 96,
        "brev_search":      "RTX PRO",
        "min_root_disk_gb": 220,
    },
    # Secondary — keep common names usable from CLI if needed
    "H100": {
        "short_name":       "h100",
        "gpu_type":         "H100",
        "gpu_count":        2,
        "min_vram_per_gpu": 80,
        "brev_search":      "H100",
        "min_root_disk_gb": 220,
    },
    "L40S": {
        "short_name":       "l40s",
        "gpu_type":         "L40S",
        "gpu_count":        2,
        "min_vram_per_gpu": 48,
        "brev_search":      "L40S",
        "min_root_disk_gb": 220,
    },
}

DEFAULT_PLATFORM = "RTXPRO6000BW"

# Prepended to every instruction.md because the eval runner cannot answer
# interactive deployment confirmations.
PREAMBLE = (
    "You are running inside a non-interactive evaluation harness. "
    "You are pre-authorized to deploy prerequisites autonomously — "
    "do not pause to ask for confirmation on `/vss-deploy-profile` or any other "
    "setup action the trial requires."
)

GENERIC_JUDGE = Path(__file__).resolve().parents[2] / "verifiers" / "generic_judge.py"


# ---------------------------------------------------------------------------
# Template substitution
# ---------------------------------------------------------------------------

def _substitute_spec(spec: dict, platform: str) -> dict:
    """Replace {{platform}} and {{repo_root}} placeholders in every string
    field of the spec. Returns a fully-resolved copy suitable for tests/."""
    substitutions = {
        "platform": platform,
        "repo_root": "$HOME/video-search-and-summarization",
    }
    pattern = re.compile(r"\{\{\s*(\w+)\s*\}\}")

    _LEGACY_REPO = "/home/ubuntu/video-search-and-summarization"
    _PORTABLE_REPO = "$HOME/video-search-and-summarization"

    def _sub(value):
        if isinstance(value, str):
            rendered = pattern.sub(
                lambda m: str(substitutions.get(m.group(1), m.group(0))),
                value,
            )
            return rendered.replace(_LEGACY_REPO, _PORTABLE_REPO)
        if isinstance(value, list):
            return [_sub(v) for v in value]
        if isinstance(value, dict):
            return {k: _sub(v) for k, v in value.items()}
        return value

    return _sub(spec)


# ---------------------------------------------------------------------------
# Per-file generators
# ---------------------------------------------------------------------------

def generate_test_script(step: int, spec_name: str) -> str:
    """Shell wrapper invoking the generic LLM-as-judge verifier for one step.
    Harbor reads /logs/verifier/reward.txt."""
    return (
        "#!/bin/bash\n"
        f"# vss-build-vision-agent verifier (step {step}): delegates to generic_judge.\n"
        "set -uo pipefail\n"
        "\n"
        'TEST_DIR="$(cd "$(dirname "$0")" && pwd)"\n'
        "python3 -m pip install --quiet 'anthropic>=0.40.0' >/dev/null 2>&1 || true\n"
        "\n"
        'python3 "$TEST_DIR/generic_judge.py" \\\n'
        f'    --spec "$TEST_DIR/{spec_name}" --step {step}\n'
    )


# ---------------------------------------------------------------------------
# Gym evaluation overlay gold solutions (references/services/gym/)
#
# These two specs are offline: no deployment, no GPU, platform ANY. Offline is
# not the same as nothing to do -- the delta spec's checks assert build
# artifacts that exist only once the delta is composed, and the gate spec's
# checks assert a verdict that exists only once the gate has run. A solve
# script that merely echoes cannot satisfy either.
# ---------------------------------------------------------------------------

_SOLVE_DELTA = r"""#!/bin/bash
# Gold solution: compose the Gym evaluation delta exactly as
# references/services/gym/compose-delta.md specifies. Nothing is pulled or started.
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
    image: ${VSS_GYM_EVAL_IMAGE:-nvcr.io/nvidia/eval-factory/nemo-gym}:${VSS_GYM_EVAL_TAG:?set only after the image gate passes}
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
# script's own exports are in scope.
#
# Rewrite them IN PLACE. Stripping the keys and appending replacements at the
# end looks equivalent and is not: Compose's dotenv loader resolves each entry
# against the shell environment and the entries parsed SO FAR, never against
# later ones. overrides.env defines VSS_APPS_DIR before the entries that
# consume it (VST_CONFIG_PATH, SDR_CONTROLLER_CONFIG_PATH), so moving the
# definition to the end silently resolves those to "/services/vios/configs"
# with an empty prefix -- measured, not assumed. sed keeps the original line
# positions, so the dependency order survives.
sed -E \
    -e "s|^COMPOSE_PROFILES=.*|COMPOSE_PROFILES=${FOUNDATION_PROFILES},gym-eval|" \
    -e "s|^VSS_APPS_DIR=.*|VSS_APPS_DIR=${VSS_APPS_DIR}|" \
    -e "s|^VSS_DATA_DIR=.*|VSS_DATA_DIR=${VSS_DATA_DIR}|" \
    "${FOUNDATION_ENV}" > "${BUILD_DIR}/override.env"

# If a key was absent entirely there is nothing to position against, so append.
for kv in "VSS_APPS_DIR=${VSS_APPS_DIR}" "VSS_DATA_DIR=${VSS_DATA_DIR}" \
          "COMPOSE_PROFILES=${FOUNDATION_PROFILES},gym-eval"; do
    grep -q "^${kv%%=*}=" "${BUILD_DIR}/override.env" \
        || echo "${kv}" >> "${BUILD_DIR}/override.env"
done
echo "VSS_GYM_EVAL_OUTPUT_DIR=/workspace/outputs" >> "${BUILD_DIR}/override.env"

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
# Gold solution: run the references/services/gym/image-gate.md gate against a tag that must be
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
    # `arrays` matches the production gate in SKILL.md. Without it, an object
    # history yields a key count and iterable values, so an uninspectable image
    # reads as clean -- the oracle must not be weaker than the gate it checks.
    HIST_TOTAL=$(echo "$BLOB" | jq -er '.history | arrays | length') \
        || { echo "GATE FAIL: .history absent or not an array"; exit 1; }
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


def generate_solve_script(
    platform: str,
    build_profile: str,
    artifact_expected: bool,
) -> str:
    """Gold solution stub — verifier drives assertions independently;
    solve.sh confirms the resolved artifact contract for build turns."""
    lines = [
        "#!/bin/bash\n"
        f"# Gold solution: vss-build-vision-agent / {build_profile} on {platform}\n"
        "set -euo pipefail\n"
        "\n"
        'REPO_ROOT="${HOME}/video-search-and-summarization"\n'
        f'BUILD_DIR="${{REPO_ROOT}}/_builds/{build_profile}"\n'
        "\n"
    ]
    if artifact_expected:
        lines.extend(
            [
                "for artifact in override.env compose.yml resolved.yml; do\n",
                '    if [ ! -f "${BUILD_DIR}/${artifact}" ]; then\n',
                '        echo "Build output missing: ${BUILD_DIR}/${artifact}"\n',
                "        exit 1\n",
                "    fi\n",
                "done\n",
                'grep -q "^FOUNDATION=" "${BUILD_DIR}/override.env"\n',
                'grep -q "^COMPOSE_PROFILES=" "${BUILD_DIR}/override.env"\n',
                'docker compose -f "${BUILD_DIR}/resolved.yml" config --quiet\n',
                'uv run "${REPO_ROOT}/skills/vss-build-vision-agent/scripts/validate_resolved_yml.py" '
                '"${BUILD_DIR}/resolved.yml" --repo-root "${REPO_ROOT}"\n',
                'echo "Resolved build output found at ${BUILD_DIR}; verifier will drive the assertions."\n',
            ]
        )
    else:
        lines.append(
            'echo "No artifact required for this proposal-only turn; verifier will drive the assertions."\n'
        )
    return "".join(lines)


# ---------------------------------------------------------------------------
# Task generation
# ---------------------------------------------------------------------------

def generate_task(
    platform: str,
    spec: dict,
    output_root: Path,
    skill_dir: Path,
    vios_skill_dir: Path | None,
    rtvi_skill_dir: Path | None,
    rtcv_skill_dir: Path | None,
    rtembed_skill_dir: Path | None,
    summarize_skill_dir: Path | None,
    report_skill_dir: Path | None = None,
) -> None:
    """Emit one Harbor task directory per entry in spec['expects'].
    Multi-step specs produce step-N/ subdirs; single-step specs are flat."""
    pspec = dict(PLATFORMS[platform])  # copy so we can override per-spec
    # Let the spec's resources.platforms[platform].gpu_count override the
    # adapter default — the spec is authoritative for fleet sizing.
    spec_platform_res = (spec.get("resources") or {}).get("platforms", {}).get(platform, {})
    if "gpu_count" in spec_platform_res:
        pspec["gpu_count"] = int(spec_platform_res["gpu_count"])
    platform_short = pspec["short_name"]
    expects = spec.get("expects") or []
    spec_name = Path(spec.get("_source_path", "spec.json")).name or "spec.json"
    # Build profile slug from spec (e.g. "in-1")
    build_profile: str = spec.get("profile", "")
    if not build_profile:
        build_profile = Path(spec_name).stem  # fallback to spec filename stem

    rendered_spec = _substitute_spec(spec, platform)
    # Gym overlay specs are offline: no deployment, no GPU. The adapter's
    # default is a deploying profile build, which is the opposite.
    _is_gym = Path(spec_name).stem.startswith("gym_")
    runtime_deploy = False if _is_gym else bool(spec.get("runtime_deploy", True))
    judge_max_turns = int(spec.get("judge_max_turns", 60))

    # dataset group = spec stem (e.g. "profile_in_1_streaming_dense_captions")
    dataset_group = Path(spec_name).stem

    for idx, expect in enumerate(rendered_spec.get("expects") or [], 1):
        step_dir = output_root / dataset_group / platform_short
        if len(expects) > 1:
            step_dir = step_dir / f"step-{idx}"
        step_dir.mkdir(parents=True, exist_ok=True)

        # ---- instruction.md ------------------------------------------------
        # Note: spec.env notes and query are rendered ({{...}} substituted).
        lines = [
            PREAMBLE if not _is_gym else _GYM_PREAMBLE,
            "",
            (
                "Use the `/vss-build-vision-agent` skill's Gym evaluation overlay "
                "(`references/services/gym.md`). Work from "
                "`$HOME/video-search-and-summarization` (the VSS repository root)."
                if _is_gym else
                f"Use the `/vss-build-vision-agent` skill for the "
                f"`{build_profile}` profile on `{platform}`. "
                "Work from `$HOME/video-search-and-summarization` (the VSS repository root)."
            ),
            "",
            f"## Query {idx} of {len(expects)}",
            "",
            expect.get("query", ""),
            "",
            "## Environment notes",
            "",
            rendered_spec.get("env", ""),
            "",
            "Run autonomously without prompting for confirmation.",
            "",
        ]
        (step_dir / "instruction.md").write_text("\n".join(lines) + "\n")

        # ---- task.toml -----------------------------------------------------
        step_suffix = f"-step-{idx}" if len(expects) > 1 else ""
        task_description = (
            f"Gym evaluation overlay: {build_profile.removeprefix('gym_')}"
            if _is_gym else
            f"Build+deploy {build_profile} profile"
            if runtime_deploy
            else f"Build {build_profile} profile"
        )
        meta_lines = [
            "[task]",
            f'name = "nvidia-vss/vss-build-vision-agent-{dataset_group}-{platform_short}{step_suffix}"',
            f'description = "{task_description} ({idx}/{len(expects)}) on {platform}"',
            f'keywords = ["vss-build-vision-agent", "build", "{build_profile}", "{platform}"]',
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
            # JUDGE_MAX_TURNS bumped from default 25 because the IN-1 spec carries
            # 20 checks — many requiring live service probes (ES, Kafka, VIOS,
            # RT-VLM) and trajectory-derived IDs; standard 25 turns is tight.
            f'JUDGE_MAX_TURNS = "{judge_max_turns}"',
            "",
            "[metadata]",
            'skill = "vss-build-vision-agent"',
            # `profile` is a build-directory label used only for task provenance.
            f'profile = "{build_profile}"',
            f'platform = "{platform}"',
            f'gpu_type = "{pspec["gpu_type"]}"',
            f'gpu_count = {pspec["gpu_count"]}',
            f'brev_search = "{pspec["brev_search"]}"',
            f'min_vram_gb_per_gpu = {pspec["min_vram_per_gpu"]}',
            f'min_root_disk_gb = {pspec["min_root_disk_gb"]}',
            # No requires_deployed_vss — the skill builds itself and deploys only
            # when the spec's runtime checks require it.
            "requires_deployed_vss = false",
            f"runtime_deploy = {str(runtime_deploy).lower()}",
            # No prerequisite_deploy_mode — not an alerts stack trial.
            f"step_index = {idx}",
            f"step_count = {len(expects)}",
            f"check_count = {len(expect.get('checks') or [])}",
            "",
        ]
        (step_dir / "task.toml").write_text("\n".join(meta_lines))

        # ---- environment/ --------------------------------------------------
        env_dir = step_dir / "environment"
        env_dir.mkdir(exist_ok=True)
        (env_dir / "Dockerfile").write_text("FROM scratch\n")

        # ---- tests/ --------------------------------------------------------
        tests_dir = step_dir / "tests"
        tests_dir.mkdir(exist_ok=True)
        (tests_dir / "test.sh").write_text(generate_test_script(idx, spec_name))
        if GENERIC_JUDGE.exists():
            shutil.copy(GENERIC_JUDGE, tests_dir / "generic_judge.py")
        # Ship the rendered spec so the verifier's judge sees substituted paths
        (tests_dir / spec_name).write_text(json.dumps(rendered_spec, indent=2))

        # ---- solution/ -----------------------------------------------------
        solution_dir = step_dir / "solution"
        solution_dir.mkdir(exist_ok=True)
        artifact_expected = expect.get("artifact_expected", True)
        if not isinstance(artifact_expected, bool):
            raise ValueError(
                f"expects[{idx}].artifact_expected must be a JSON boolean"
            )
        # The Gym overlay specs are offline and have their own oracles; the
        # build-profile solve script assumes a deployment and cannot satisfy them.
        _stem = Path(spec_name).stem
        if _stem.startswith("gym_"):
            if "delta" in _stem:
                _solve = _SOLVE_DELTA.replace("__REPO_ROOT__", HARNESS_REPO_ROOT)
            elif "image_gate" in _stem:
                _solve = _SOLVE_IMAGE_GATE
            else:
                raise SystemExit(
                    f"no gold solution for Gym spec '{_stem}' -- add one rather "
                    f"than shipping a stub that cannot satisfy its checks"
                )
            (solution_dir / "solve.sh").write_text(_solve)
        else:
            (solution_dir / "solve.sh").write_text(
                generate_solve_script(platform, build_profile, artifact_expected)
            )

        # Bundle the build skill itself plus the service skills the spec may
        # need after generation.
        profile = str(spec.get("profile", "")).lower()
        spec_text = json.dumps(spec, sort_keys=True).lower()
        skills_to_copy: list[tuple[Path | None, str]] = [
            (skill_dir, "vss-build-vision-agent"),
            (vios_skill_dir, "vss-manage-video-io-storage"),
        ]
        wants_dense_captioning = profile == "in-1" or any(
            token in spec_text
            for token in ("dense-caption", "captioning", "rt-vlm", "vlm-captions")
        )
        wants_rt_cv = any(
            token in spec_text
            for token in (
                "rt-cv",
                "rtdetr",
                "rt-detr",
                "bounding box",
                "object detection",
                "detection/tracking",
                "tracking metadata",
            )
        )
        wants_rt_embed = any(
            token in spec_text
            for token in (
                "rt-embed",
                "rtvi-embed",
                "video-embedding",
                "video embedding",
                "frame embedding",
                "mdx-embed",
                "in-3",
            )
        )
        wants_summarization = any(
            token in spec_text
            for token in (
                "summarization",
                "summarize",
                "lvs",
                "long video summary",
                "video summary",
                "v1/summarize",
            )
        )
        wants_report = any(
            token in spec_text
            for token in (
                "vss-generate-video-report",
                "generate-video-report",
                "video report",
                "sop compliance report",
                "mode c",
            )
        )
        if wants_dense_captioning:
            skills_to_copy.append((rtvi_skill_dir, "vss-deploy-dense-captioning"))
        if wants_rt_cv:
            skills_to_copy.append((rtcv_skill_dir, "vss-deploy-detection-tracking-2d"))
        if wants_rt_embed:
            skills_to_copy.append((rtembed_skill_dir, "vss-deploy-video-embedding"))
        if wants_summarization:
            skills_to_copy.append((summarize_skill_dir, "vss-summarize-video"))
        if wants_report:
            skills_to_copy.append((report_skill_dir, "vss-generate-video-report"))
        skills_root = step_dir / "skills"
        if skills_root.exists():
            shutil.rmtree(skills_root)
        skills_root.mkdir(exist_ok=True)
        for src, name in skills_to_copy:
            if src and src.exists():
                dst = skills_root / name
                shutil.copytree(src, dst)


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
        help="Dataset output root (e.g. .github/skill-eval/datasets/vss-build-vision-agent)",
    )
    parser.add_argument(
        "--skill-dir", required=True,
        help="Path to skills/vss-build-vision-agent",
    )
    parser.add_argument(
        "--vios-skill-dir", default=None,
        help="Path to skills/vss-manage-video-io-storage (bundled for post-deploy VIOS checks)",
    )
    parser.add_argument(
        "--rtvi-skill-dir", default=None,
        help="Path to skills/vss-deploy-dense-captioning (bundled for RT-VLM checks)",
    )
    parser.add_argument(
        "--rtcv-skill-dir", default=None,
        help="Path to skills/vss-deploy-detection-tracking-2d (bundled for RT-CV checks)",
    )
    parser.add_argument(
        "--rtembed-skill-dir", default=None,
        help="Path to skills/vss-deploy-video-embedding (bundled for RT-Embed checks)",
    )
    parser.add_argument(
        "--summarize-skill-dir", default=None,
        help="Path to skills/vss-summarize-video (bundled for LVS summarize API checks)",
    )
    parser.add_argument(
        "--report-skill-dir", default=None,
        help="Path to skills/vss-generate-video-report (bundled for SOP report checks)",
    )
    parser.add_argument(
        "--spec", default=None,
        help="Path to the eval spec JSON (default: <skill-dir>/eval/profile_in_1_streaming_dense_captions.json)",
    )
    parser.add_argument(
        "--platform", default=None,
        choices=list(PLATFORMS.keys()),
        help=f"Generate for this platform only (default: {DEFAULT_PLATFORM})",
    )
    parser.add_argument(
        "--all-platforms", action="store_true",
        help="Fan out across every platform in PLATFORMS",
    )
    args = parser.parse_args()

    output_root = Path(args.output_dir)
    skill_dir = Path(args.skill_dir)
    vios_skill_dir = Path(args.vios_skill_dir) if args.vios_skill_dir else None
    rtvi_skill_dir = Path(args.rtvi_skill_dir) if args.rtvi_skill_dir else None
    rtcv_skill_dir = Path(args.rtcv_skill_dir) if args.rtcv_skill_dir else None
    rtembed_skill_dir = Path(args.rtembed_skill_dir) if args.rtembed_skill_dir else None
    summarize_skill_dir = Path(args.summarize_skill_dir) if args.summarize_skill_dir else None
    report_skill_dir = Path(args.report_skill_dir) if args.report_skill_dir else None
    repo_root = skill_dir.resolve().parents[1]
    if vios_skill_dir is None:
        candidate = repo_root / "skills" / "vss-manage-video-io-storage"
        vios_skill_dir = candidate if candidate.exists() else None
    if rtvi_skill_dir is None:
        candidate = repo_root / "skills" / "vss-deploy-dense-captioning"
        rtvi_skill_dir = candidate if candidate.exists() else None
    if rtcv_skill_dir is None:
        candidate = repo_root / "skills" / "vss-deploy-detection-tracking-2d"
        rtcv_skill_dir = candidate if candidate.exists() else None
    if rtembed_skill_dir is None:
        candidate = repo_root / "skills" / "vss-deploy-video-embedding"
        rtembed_skill_dir = candidate if candidate.exists() else None
    if summarize_skill_dir is None:
        candidate = repo_root / "skills" / "vss-summarize-video"
        summarize_skill_dir = candidate if candidate.exists() else None
    if report_skill_dir is None:
        candidate = repo_root / "skills" / "vss-generate-video-report"
        report_skill_dir = candidate if candidate.exists() else None

    spec_path = (
        Path(args.spec)
        if args.spec
        else (skill_dir / "eval" / "profile_in_1_streaming_dense_captions.json")
    )
    if not spec_path.exists():
        print(f"spec not found: {spec_path}", file=sys.stderr)
        sys.exit(1)

    spec = json.loads(spec_path.read_text())
    spec["_source_path"] = str(spec_path)

    # Determine platforms from spec.resources.platforms filtered by CLI
    spec_platforms = list((spec.get("resources") or {}).get("platforms") or {})
    if args.platform:
        platforms = [args.platform]
    elif args.all_platforms:
        platforms = list(PLATFORMS.keys())
    elif spec_platforms:
        # Use the spec's declared platforms, filtered to known entries
        platforms = [p for p in spec_platforms if p in PLATFORMS]
        if not platforms:
            print(
                f"WARNING: spec platforms {spec_platforms} not in PLATFORMS table — "
                f"using default {DEFAULT_PLATFORM}",
                file=sys.stderr,
            )
            platforms = [DEFAULT_PLATFORM]
    else:
        platforms = [DEFAULT_PLATFORM]

    print("=== Inputs ===")
    print(f"  output_dir   : {output_root}")
    print(f"  skill_dir    : {skill_dir}")
    print(f"  spec         : {spec_path}")
    print(f"  platforms    : {platforms}")
    print(f"  queries      : {len(spec.get('expects', []))}")
    print(f"  total checks : {sum(len(q.get('checks', [])) for q in spec.get('expects', []))}")
    print()

    dataset_group = Path(spec_path.name).stem
    for platform in platforms:
        task_id = PLATFORMS[platform]["short_name"]
        print(f"  GEN  vss-build-vision-agent/{dataset_group}/{task_id}")
        generate_task(
            platform, spec, output_root, skill_dir,
            vios_skill_dir, rtvi_skill_dir, rtcv_skill_dir, rtembed_skill_dir,
            summarize_skill_dir, report_skill_dir,
        )

    print()
    print(f"Generated {len(platforms)} task(s) under {output_root}/{dataset_group}/")


if __name__ == "__main__":
    main()
