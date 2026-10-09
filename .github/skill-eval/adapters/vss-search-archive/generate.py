#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Generate Harbor tasks for the vss-search-archive skill.

The vss-search-archive skill exercises the host-side NAT-free ``vss`` base
distribution for search across pre-ingested video sources. Search commands run
the ``vss`` CLI on PATH (installed from the repository checkout's
``libs/vss/cli``) as ``vss search run <embed|attribute|fusion|object|tag> ...``;
endpoints come from the deployment recorded by ``vss configure``, so they never
run through a container/pod shell or a manually selected search endpoint. The
skill is search-only: ingestion and deletion are owned by
``vss-manage-video-io-storage`` and appear only as persisted setup/cleanup steps
here.
It runs against a **full-remote-model VSS search profile** (deploy mode
= `remote-all`; LLM and underlying VLM inference use remote endpoints, while
the RT-VLM media proxy, Cosmos Embed1, and Elasticsearch remain local on the
GPU host). The first generated step deploys and validates that profile. The
second persisted step uses ``vss vios add`` to register the two named
sample videos; the deployment's mounted notification config fans them out and seeds their search indexes. Later steps reuse
that prepared state.

Every ``expects[]`` entry carries a ``role`` (``setup``, ``search``, or
``cleanup``) that is copied to the generated ``task.toml`` so a reporting view
can score search-role steps separately from deployment/fixture setup and
terminal cleanup. Harbor's ordinary per-step scoring is unchanged.

Mirrors the vss-manage-video-io-storage adapter's shape — single-task-per-platform, step-chained
under the spec's prerequisite profile name. The platform comes exclusively
from `resources.platforms`; this spec pins RTXPRO6000BW with two GPUs for the
ingest workload even though the host-side search itself is an Elasticsearch
query.

## Directory layout

    .github/skill-eval/datasets/vss-search-archive/<profile>/<platform>/step-<k>/
        task.toml
        instruction.md
        tests/test.sh
        tests/<spec>.json
        tests/generic_judge.py
        solution/solve.sh
        skills/vss-search-archive/  (full skill copy)
        skills/vss-build-vision-ai/        (for prerequisite diagnostics)
        skills/vss-manage-video-io-storage/          (the search spec's first checks reference VIOS
                               as the canonical source-list lookup)
        skills/vss-ask-video/              (confirmed search-result verification)

`<profile>` comes from `spec.profile` (here: `search`). `<k>` is the
1-based index into `expects[]`; single-step specs collapse the step
subdir.

Usage from the repository root:
    python3 .github/skill-eval/adapters/vss-search-archive/generate.py \\
        --output-dir .github/skill-eval/datasets/vss-search-archive \\
        --skill-dir skills/operations/vss-search-archive \\
        --deploy-skill-dir skills/vss-build-vision-ai \\
        --video-io-skill-dir skills/operations/vss-manage-video-io-storage \\
        --ask-video-skill-dir skills/operations/vss-ask-video
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Platforms — mirrors the vss-manage-video-io-storage/vss-build-vision-ai adapters so vss-search-archive runs on the
# same hosts. The spec's `resources.platforms` further filters this set.
# ---------------------------------------------------------------------------

PLATFORMS: dict[str, dict] = {
    "H100": {
        "short_name": "h100",
        "gpu_type": "H100",
        "min_vram_per_gpu": 80,
        "brev_search": "H100",
    },
    "L40S": {
        "short_name": "l40s",
        "gpu_type": "L40S",
        "min_vram_per_gpu": 48,
        "brev_search": "L40S",
    },
    "RTXPRO6000BW": {
        "short_name": "rtxpro6000bw",
        "gpu_type": "RTX PRO 6000",
        "min_vram_per_gpu": 96,
        "brev_search": "RTX PRO",
    },
    "DGX-SPARK": {
        "short_name": "spark",
        "gpu_type": "GB10",
        "min_vram_per_gpu": 96,
        "brev_search": "GB10",
    },
    "IGX-THOR": {
        "short_name": "thor",
        "gpu_type": "Thor",
        "min_vram_per_gpu": 64,
        "brev_search": "Thor",
    },
}

# A persisted Harbor chain runs as: deploy + ingest (setup), the search and
# contract cases (search), then terminal fixture deletion (cleanup). The
# adapter validates this sequence and copies each role into task metadata so a
# reporting view can score search-role steps separately from setup and cleanup.
ROLES: tuple[str, ...] = ("setup", "search", "cleanup")

PREAMBLE = (
    "You are running inside a non-interactive evaluation harness. "
    "You are pre-authorized to deploy prerequisites autonomously — do not pause to ask "
    "for confirmation on `/vss-build-vision-ai` or any other setup action the trial requires."
)

OPERATION_PREAMBLE = (
    PREAMBLE + " The search profile "
    "and evaluation fixtures were prepared by the preceding deployment and ingestion steps. Do not redeploy "
    "the profile and do not ingest or re-ingest any source during this step. Use the `vss` CLI; "
    'if `vss` is not on PATH, install it from the host checkout with `uv tool install "${VSS_REPO_ROOT:-$HOME/video-search-and-summarization}/libs/vss/cli"`. '
    "List registered sources with `vss vios list` (it reads the origin "
    "`vss configure` recorded, so it takes no endpoint); do not hand-build "
    "`/vst/api/v1/sensor/list` or assume a fixed port. If "
    "the requested source is not registered, follow the skill's missing-source rule: list "
    "registered sources, report the missing source, and stop without silently substituting "
    "another source or invoking the search CLI. When it is missing, end by asking the user to clarify "
    "the source or explicitly request ingestion; that clarification is not a request for setup "
    "confirmation. For a resolved search request, decompose the request, "
    "preserve the exact original user sentence and follow the bundled search skill's dispatcher, "
    "path-specific flags, and configuration-refresh rules. Build the invocation in a bash array, "
    "capture stdout and exit status separately, and branch on the CLI's typed exit status: "
    "exit 0 carries the completed result; exit 6 carries usable hits only when `data` exists. "
    "With exit 6 and `data`, report the hits and supplied limitation once; without `data`, report "
    "the supplied failure without inventing hits. Do not rerun or retry an individual stage. "
    "On exit 2, read the selected path's `--help` once and correct the invalid invocation; "
    "report other typed failures and stop. Read `search_messages` when present. "
    "The CLI automatically attempts critic verification when the configured deployment "
    "exposes VST and RT-VLM; preserve each hit's `critic_result.result` as confirmed, rejected, or "
    "unverified, reporting a null `critic_result` as unverified. "
    "A missing or failed verifier is fail-open and must not fail retrieval. Do not download "
    "or visually inspect screenshot pixels during the search query. Report each hit's returned media URL when one is present -- it may be empty when VST is unavailable, and when present it carries the scheme, host, and port of the origin `vss configure` recorded; URL availability "
    "is not visual evidence. For nonempty results, clearly summarize the CLI evidence fields; a particular heading "
    "or prose layout is not required. Never paste raw JSON "
    "into the reply. Say that "
    "similarity scores are retrieval evidence rather than visual confirmation. Use a verification question only under the all-unverified rule: offer it only when every hit in the nonempty displayed result set remains unverified; if any hit is confirmed "
    "or rejected, do not offer fallback verification. If the host command "
    "fails, report its error and stop instead of substituting another search interface. This step is a search step; do not run deletion or an Elasticsearch count here."
)

VERIFICATION_PREAMBLE = (
    PREAMBLE
    + " The search profile and fixtures remain prepared from earlier steps. This is an explicit "
    "confirmation for the supplied synthetic, unverified bounded hit. Do not rerun "
    "search, deploy, ingest, delete, or inspect screenshot pixels. Use the supplied synthetic file-search interval and source of the unverified hit; load the search-result verification reference to map it onto the recorded file timeline while preserving its duration, then resolve only that bounded clip through the configured origin. Invoke the bundled "
    "vss-ask-video skill through its ordinary pre-resolved `VIDEO_URL` path. Ask it to evaluate only "
    "that clip against the complete supplied visual intent and return the structured result contract. "
    "Validate `result`, boolean `criteria_met`, nonempty `evidence`, and `media_evaluated: true`. Make one "
    "VLM request, with at most one additional request solely to repair malformed structured output; never retry "
    "a semantic verdict. "
    "A 401, 403 or connection error from the VLM IS the technical endpoint failure named below — not a "
    "configuration puzzle to solve. Do not retry it against another host, port, auth header or endpoint: "
    "that is what turns one request into seven. Stop requesting and take the permitted path instead. "
    "A semantic `unverified` is a completed visual check. Screenshot inspection is permitted only "
    "after a technical clip, endpoint, media, or model failure, and must be labeled representative-image "
    "evidence. Keep the final response implementation-neutral."
)

KUBERNETES_INGRESS_CONTRACT_PREAMBLE = (
    PREAMBLE
    + " This step is a read-only Kubernetes Ingress contract check. Do not deploy, "
    "execute the example commands, inspect a cluster, or reuse earlier deployment state. "
    "Use the bundled skill to explain the requested host-side workflow and missing-route behavior."
)

RTSP_LIVE_STREAM_CONTRACT_PREAMBLE = (
    PREAMBLE
    + " This step is a read-only live-stream search contract check. Do not deploy, "
    "ingest, add or delete a stream, execute examples, or reuse earlier deployment state. "
    "Use the bundled skill to show the requested host-side workflow and explain partition selection."
)

CLEANUP_PREAMBLE = (
    PREAMBLE
    + " This step is terminal evaluation cleanup, not a search-skill capability. Do not run search. "
    "Resolve the fixture source and save its UUID and canonical name first, then require "
    "`vss vios delete --type video --sensor <name>` to succeed using the resolved name (the "
    "deployment's `camera_remove` webhooks withdraw consumers and clean the anchor indexes). "
    "Never use the Agent `DELETE /api/v1/videos/<id>` or a bare VIOS/storage DELETE. Confirm absence "
    "with `vss vios list` after the CLI returns; do not wrap it in a retry loop. The evaluation verifier "
    "makes the bounded read-only checks that "
    "the source is gone from VST and that the distinct embedding, behavior, and raw index tuples reach "
    "zero; the agent does not build an Elasticsearch endpoint or poll an index itself."
)


# ---------------------------------------------------------------------------
# Generation
# ---------------------------------------------------------------------------
def _peer_skill_dir(skill_dir: Path, name: str) -> Path | None:
    """Resolve a peer skill by its leaf directory name.

    A skill lives at either skills/<name>/ or skills/<category>/<name>/, and a
    peer may sit in a different category than the caller — so look beside the
    caller first, then across the categories one level up.
    """
    beside = skill_dir.parent / name
    if beside.is_dir():
        return beside
    for category in sorted(skill_dir.parent.parent.glob("*")):
        nested = category / name
        if nested.is_dir():
            return nested
    return None


# Scripted Elasticsearch tuple verification for persisted setup and cleanup.
# Webhook indexing and withdrawal are asynchronous. Keep their bounded waits
# in the verifier, never in the search skill or the evaluated agent's prompt.
_ES_TUPLE_PROBE = """es_count() {
  curl -fsS --connect-timeout 5 --max-time 15 -H 'Content-Type: application/json' "${ES_URL%/}/$1/_count" -d "$(jq -cn --arg f "$2" --arg v "$3" '{query:{term:{($f):$v}}}')" | jq -er '.count | numbers'
}
bounded_vios_list() {
  local remaining=$((deadline - SECONDS))
  (( remaining > 0 )) || return 124
  timeout --kill-after=5s "${remaining}s" vss vios list
}
CONFIG_JSON=$(vss configure show 2>/dev/null) || { echo "vss not configured — cannot verify fixture indexing" >&2; exit 1; }
ES_URL=$(printf '%s' "${CONFIG_JSON}" | jq -er '.services.elasticsearch.url') || { echo "ES url missing from config" >&2; exit 1; }
EMBED_IDX=mdx-embed-filtered-2025-01-01
BEHAV_IDX=mdx-behavior-2025-01-01
RAW_IDX=mdx-raw-2025-01-01
state_file() {
  local key origin
  origin=$(printf '%s' "${CONFIG_JSON}" | jq -er '.base_url') || return 1
  key=$(printf '%s\\0%s\\0%s' "${origin}" "${GITHUB_RUN_ID:-local}" "${EVAL_SLUG:-vss-search-archive}" | sha256sum | cut -d' ' -f1) || return 1
  printf '/tmp/skill-eval/fixture-state/%s/ladder.uuid\\n' "${key}"
}
"""

_INGEST_ASSERT = """
deadline=$((SECONDS + 900))
while :; do
  SENSORS=$(bounded_vios_list 2>/dev/null) || { echo "vss vios list failed or exceeded fixture deadline" >&2; exit 1; }
  SAMPLE_UUID=$(printf '%s' "${SENSORS}" | jq -r '.sensors[] | select(.name == "warehouse_sample") | .sensor_id // empty')
  LADDER_UUID=$(printf '%s' "${SENSORS}" | jq -r '.sensors[] | select(.name == "warehouse-ladder") | .sensor_id // empty')
  if [ -n "${SAMPLE_UUID}" ] && [ -n "${LADDER_UUID}" ] &&
     SAMPLE_EMBED=$(es_count "${EMBED_IDX}" sensor.id.keyword "${SAMPLE_UUID}") &&
     LADDER_EMBED=$(es_count "${EMBED_IDX}" sensor.id.keyword "${LADDER_UUID}") &&
     LADDER_BEHAVIOR=$(es_count "${BEHAV_IDX}" sensor.id.keyword "warehouse-ladder") &&
     LADDER_RAW=$(es_count "${RAW_IDX}" sensorId.keyword "warehouse-ladder") &&
     (( SAMPLE_EMBED > 0 && LADDER_EMBED > 0 && LADDER_BEHAVIOR > 0 && LADDER_RAW > 0 )); then
    printf 'fixture indexed: embed=%s,%s behavior=%s raw=%s\\n' "${SAMPLE_EMBED}" "${LADDER_EMBED}" "${LADDER_BEHAVIOR}" "${LADDER_RAW}"
    break
  fi
  if (( SECONDS >= deadline )); then
    echo "fixture indexing did not converge within 900s" >&2
    exit 1
  fi
  sleep 5
done
# Refresh the local inventory after the lazy indexes are observed, so later
# search steps see the raw index and the judge can inspect the distinct names.
BASE_URL=$(printf '%s' "${CONFIG_JSON}" | jq -er '.base_url') || exit 1
vss configure --base-url "${BASE_URL}" >/dev/null 2>&1 || exit 1
CONFIG_JSON=$(vss configure show) || exit 1
printf '%s' "${CONFIG_JSON}" | jq -e --arg a "${EMBED_IDX}" --arg b "${BEHAV_IDX}" --arg c "${RAW_IDX}" '[.services.elasticsearch.indices[]] | (index($a) != null and index($b) != null and index($c) != null)' >/dev/null || exit 1
STATE_FILE=$(state_file) || exit 1
mkdir -p "$(dirname "${STATE_FILE}")" || exit 1
printf '%s\\n' "${LADDER_UUID}" > "${STATE_FILE}" || exit 1

"""

_CLEANUP_ASSERT = """
STATE_FILE=$(state_file) || exit 1
[ -r "${STATE_FILE}" ] || { echo "missing ladder UUID from fixture setup" >&2; exit 1; }
LADDER_UUID=$(cat "${STATE_FILE}") || exit 1
[[ "${LADDER_UUID}" =~ ^[A-Za-z0-9_-]+$ ]] || { echo "invalid saved ladder UUID" >&2; exit 1; }
deadline=$((SECONDS + 600))
while :; do
  SENSORS=$(bounded_vios_list 2>/dev/null) || { echo "vss vios list failed or exceeded fixture deadline" >&2; exit 1; }
  LADDER_PRESENT=$(printf '%s' "${SENSORS}" | jq -r 'any(.sensors[]; .name == "warehouse-ladder")')
  if [ "${LADDER_PRESENT}" = false ] &&
     LADDER_EMBED=$(es_count "${EMBED_IDX}" sensor.id.keyword "${LADDER_UUID}") &&
     LADDER_BEHAVIOR=$(es_count "${BEHAV_IDX}" sensor.id.keyword "warehouse-ladder") &&
     LADDER_RAW=$(es_count "${RAW_IDX}" sensorId.keyword "warehouse-ladder") &&
     (( LADDER_EMBED == 0 && LADDER_BEHAVIOR == 0 && LADDER_RAW == 0 )); then
    printf 'cleanup confirmed: vst_present=%s embed=%s behavior=%s raw=%s\\n' "${LADDER_PRESENT}" "${LADDER_EMBED}" "${LADDER_BEHAVIOR}" "${LADDER_RAW}"
    break
  fi
  if (( SECONDS >= deadline )); then
    echo "fixture cleanup did not converge within 600s" >&2
    exit 1
  fi
  sleep 5
done

"""


def generate_test_script(step: int, spec_name: str, scenario: str | None = None) -> str:
    """Verifier for one step's checks.

    The persisted setup (ingest) and cleanup (delete) steps prepend a scripted,
    read-only Elasticsearch tuple check to the generic LLM judge. The agent is
    never told to poll ES, so the verifier owns that readiness/convergence
    evidence and fails fast when indexing is incomplete (setup) or cleanup has
    not converged (cleanup). Search and contract steps delegate entirely to the
    generic judge.
    """
    judge = (
        'python3 "$TEST_DIR/generic_judge.py" \\\n'
        f'    --spec "$TEST_DIR/{spec_name}" --step {step}\n'
    )
    if scenario == "ingest-search-fixtures":
        probe = _ES_TUPLE_PROBE + _INGEST_ASSERT
    elif scenario == "delete-search-fixture":
        probe = _ES_TUPLE_PROBE + _CLEANUP_ASSERT
    else:
        probe = ""
    label = "scripted ES tuple check + " if probe else ""
    return (
        "#!/bin/bash\n"
        f"# vss-search-archive verifier (step {step}): {label}"
        "generic LLM-as-judge (.github/skill-eval/verifiers/generic_judge.py).\n"
        "set -uo pipefail\n"
        "\n"
        'TEST_DIR="$(cd "$(dirname "$0")" && pwd)"\n'
        "python3 -m pip install --quiet 'anthropic>=0.40.0' >/dev/null 2>&1 || true\n"
        "\n" + probe + judge
    )


def generate_solve_script(platform: str, role: str) -> str:
    """Gold solution — assumes the search profile is already deployed and the
    sample videos are ingested. The verifier drives the assertions
    independently against the host-side ``vss`` command and its output.

    A search-role step depends on the CLI being configured and able to run, not
    on the VSS Agent being healthy — search reaches Elasticsearch and VST
    through the CLI, never the Agent. Setup and cleanup roles do depend on a
    live deployment, so they keep the Agent health precondition."""
    header = f"# Gold solution: vss-search-archive ({role}) on {platform}\n"
    if role == "search":
        return (
            "#!/bin/bash\n" + header + "set -euo pipefail\n"
            "\n"
            'VSS_REPO_ROOT="${VSS_REPO_ROOT:-$HOME/video-search-and-summarization}"\n'
            'export PATH="$HOME/.local/bin:$PATH"\n'
            'command -v vss >/dev/null || uv tool install "${VSS_REPO_ROOT}/libs/vss/cli"\n'
            "vss configure show >/dev/null 2>&1 || {\n"
            "    echo 'vss is not configured — cannot solve a search task' >&2\n"
            "    exit 4\n"
            "}\n"
            "vss search run --help >/dev/null\n"
            "echo 'vss CLI is configured and search is available.'\n"
        )
    return (
        "#!/bin/bash\n" + header + "set -euo pipefail\n"
        "\n"
        "curl -sf --connect-timeout 5 "
        "${VSS_AGENT_URL:-http://localhost:8000}/health "
        ">/dev/null || {\n"
        "    echo 'VSS agent is not deployed — cannot solve this setup/cleanup task'\n"
        "    exit 1\n"
        "}\n"
        'VSS_REPO_ROOT="${VSS_REPO_ROOT:-$HOME/video-search-and-summarization}"\n'
        'test -f "${VSS_REPO_ROOT}/libs/vss/pyproject.toml" || {\n'
        '    echo "VSS checkout not found at ${VSS_REPO_ROOT}; set VSS_REPO_ROOT explicitly"\n'
        "    exit 1\n"
        "}\n"
        'cd "${VSS_REPO_ROOT}"\n'
        'PROFILE_DIR="${VSS_REPO_ROOT}/deploy/docker/developer-profiles/dev-profile-search"\n'
        'test -f "${PROFILE_DIR}/.env" -a -f "${PROFILE_DIR}/generated.env" || {\n'
        '    echo "Search profile requires .env and runtime generated.env"\n'
        "    exit 1\n"
        "}\n"
        'export PATH="$HOME/.local/bin:$PATH"\n'
        'command -v vss >/dev/null || uv tool install "${VSS_REPO_ROOT}/libs/vss/cli"\n'
        "vss search run --help >/dev/null\n"
        "echo 'VSS agent and the host vss CLI are ready.'\n"
    )


GENERIC_JUDGE = Path(__file__).resolve().parents[2] / "verifiers" / "generic_judge.py"


def _platforms_from_spec(spec: dict) -> list[str]:
    """Return declared platforms, rejecting unsupported adapter targets."""
    declared = spec["resources"]["platforms"]
    unsupported = sorted(set(declared) - set(PLATFORMS))
    if unsupported:
        raise ValueError(f"unsupported platform(s): {', '.join(unsupported)}")
    return list(declared)


def _validate_role_sequence(expects: list) -> None:
    """Every expect has a role in ROLES, and the sequence is
    setup, setup, search..., cleanup (one terminal cleanup)."""
    if len(expects) < 3:
        raise ValueError(
            "a persisted chain needs at least deploy + one search + cleanup"
        )
    roles = []
    for index, expect in enumerate(expects, 1):
        role = expect.get("role")
        if role not in ROLES:
            raise ValueError(
                f"spec.expects[{index}].role must be one of {ROLES}, got {role!r}"
            )
        roles.append(role)
    expected = ["setup", "setup"] + ["search"] * (len(expects) - 3) + ["cleanup"]
    if roles != expected:
        raise ValueError(
            f"spec.expects[].role sequence must be setup, setup, search..., cleanup; got {roles}"
        )


def _validate_spec(spec: dict) -> None:
    """Validate the adapter-owned subset of the skill-eval spec contract."""
    skills = spec.get("skills")
    if (
        not isinstance(skills, list)
        or not skills
        or not all(isinstance(item, str) and item for item in skills)
    ):
        raise ValueError("spec.skills must be a non-empty list of strings")
    if "vss-search-archive" not in skills:
        raise ValueError("spec.skills must include vss-search-archive")
    profile = spec.get("profile", "search")
    deploy_mode = spec.get("deploy_mode", "remote-all")
    if not isinstance(profile, str) or not profile.strip():
        raise ValueError("spec.profile must be a non-empty string when provided")
    if not isinstance(deploy_mode, str) or not deploy_mode.strip():
        raise ValueError("spec.deploy_mode must be a non-empty string when provided")
    if profile != "search":
        raise ValueError("spec.profile must be search")
    if deploy_mode != "remote-all":
        raise ValueError("spec.deploy_mode must be remote-all")

    resources = spec.get("resources")
    platforms = resources.get("platforms") if isinstance(resources, dict) else None
    if not isinstance(platforms, dict) or not platforms:
        raise ValueError("spec.resources.platforms must be a non-empty map")
    unsupported = sorted(set(platforms) - set(PLATFORMS))
    if unsupported:
        raise ValueError(f"unsupported platform(s): {', '.join(unsupported)}")
    for platform, settings in platforms.items():
        if not isinstance(settings, dict):
            raise ValueError(f"spec.resources.platforms.{platform} must be an object")
        gpu_count = settings.get("gpu_count", 1)
        if (
            isinstance(gpu_count, bool)
            or not isinstance(gpu_count, int)
            or gpu_count <= 0
        ):
            raise ValueError(
                f"spec.resources.platforms.{platform}.gpu_count must be a positive integer"
            )

    expects = spec.get("expects")
    if not isinstance(expects, list) or not expects:
        raise ValueError("spec.expects must be a non-empty list")
    if (
        len(expects) < 2
        or not isinstance(expects[0], dict)
        or expects[0].get("scenario") != "deploy-search-profile"
    ):
        raise ValueError("spec.expects[0] must be the deploy-search-profile scenario")
    if (
        not isinstance(expects[1], dict)
        or expects[1].get("scenario") != "ingest-search-fixtures"
    ):
        raise ValueError("spec.expects[1] must be the ingest-search-fixtures scenario")
    if not isinstance(expects[-1], dict) or expects[-1].get("role") != "cleanup":
        raise ValueError("the final expect must be the cleanup role")
    for index, expect in enumerate(expects, 1):
        if not isinstance(expect, dict):
            raise TypeError(f"spec.expects[{index}] must be an object")
    _validate_role_sequence(expects)
    for index, expect in enumerate(expects, 1):
        if not isinstance(expect.get("query"), str) or not expect["query"].strip():
            raise ValueError(f"spec.expects[{index}].query must be a non-empty string")
        checks = expect.get("checks")
        if (
            not isinstance(checks, list)
            or not checks
            or not all(isinstance(check, str) and check.strip() for check in checks)
        ):
            raise ValueError(
                f"spec.expects[{index}].checks must be a non-empty list of strings"
            )
    if "vss-ask-video" not in skills and any(
        expect.get("scenario") == "confirmed-search-result-verification"
        for expect in expects
    ):
        raise ValueError("verification scenario requires vss-ask-video in spec.skills")


def _validate_rendered_spec(spec: dict) -> None:
    """Reject placeholder drift before writing a runnable dataset."""
    rendered = json.dumps(spec)
    if re.search(r"\{\{[A-Za-z_][A-Za-z0-9_]*\}\}", rendered):
        raise ValueError("rendered spec contains unresolved placeholders")


def _render_spec(value: object, *, platform: str, profile: str) -> object:
    """Return a rendered copy of a spec value without mutating the source spec."""
    if isinstance(value, str):
        return value.replace("{{platform}}", platform).replace("{{profile}}", profile)
    if isinstance(value, list):
        return [
            _render_spec(item, platform=platform, profile=profile) for item in value
        ]
    if isinstance(value, dict):
        return {
            key: _render_spec(item, platform=platform, profile=profile)
            for key, item in value.items()
        }
    return value


def generate_task(
    platform: str,
    profile: str,
    spec: dict,
    output_root: Path,
    skill_dir: Path,
    deploy_skill_dir: Path | None,
    video_io_skill_dir: Path | None,
    ask_video_skill_dir: Path | None,
) -> None:
    _validate_spec(spec)
    if platform not in PLATFORMS:
        raise ValueError(f"unsupported platform: {platform}")
    spec_profile = spec.get("profile", "search")
    if profile != spec_profile:
        raise ValueError(
            f"profile {profile!r} does not match spec profile {spec_profile!r}"
        )
    pspec = PLATFORMS[platform]
    platform_short = pspec["short_name"]
    rendered_spec = _render_spec(spec, platform=platform, profile=profile)
    assert isinstance(rendered_spec, dict)
    _validate_rendered_spec(rendered_spec)
    expects = rendered_spec.get("expects") or []
    spec_name = Path(spec.get("_source_path", "spec.json")).name or "spec.json"

    for idx, expect in enumerate(expects, 1):
        step_dir = output_root / profile / platform_short
        if len(expects) > 1:
            step_dir = step_dir / f"step-{idx}"
        step_dir.mkdir(parents=True, exist_ok=True)

        # instruction.md — query + env notes only. Never leak checks[].
        scenario = expect.get("scenario")
        role = expect.get("role", "search")
        if scenario in {"deploy-search-profile", "ingest-search-fixtures"}:
            # Deployment and fixture requirements belong to the reviewed spec.
            preamble = PREAMBLE
        elif scenario == "kubernetes-ingress-contract":
            preamble = KUBERNETES_INGRESS_CONTRACT_PREAMBLE
        elif scenario == "rtsp-live-stream-search-contract":
            preamble = RTSP_LIVE_STREAM_CONTRACT_PREAMBLE
        elif scenario == "confirmed-search-result-verification":
            preamble = VERIFICATION_PREAMBLE
        elif scenario == "delete-search-fixture":
            preamble = CLEANUP_PREAMBLE
        else:
            preamble = OPERATION_PREAMBLE
        lines = [
            preamble,
            "",
            "",
            f"## Query {idx} of {len(expects)} (role: {role})",
            "",
            expect.get("query", ""),
            "",
            "Run autonomously without prompting for confirmation.",
            "",
        ]
        (step_dir / "instruction.md").write_text("\n".join(lines) + "\n")

        # task.toml
        step_suffix = f"-step-{idx}" if len(expects) > 1 else ""
        # Read gpu_count from spec.resources.platforms[platform] (default 1).
        # brev_env.py::_check_instance_matches enforces strict equality, so the
        # task.toml value must match the operator's pool allocation exactly.
        gpu_count = int(
            ((spec.get("resources") or {}).get("platforms") or {})
            .get(platform, {})
            .get("gpu_count", 1)
            or 1
        )

        meta_lines = [
            "[task]",
            f'name = "nvidia-vss/vss-search-archive-{profile}-{platform_short}{step_suffix}"',
            f'description = "vss-search-archive query {idx}/{len(expects)} on {platform}"',
            f'keywords = ["vss-search-archive", "{profile}", "{platform}"]',
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
            'skill = "vss-search-archive"',
            f'platform = "{platform}"',
            f'gpu_type = "{pspec["gpu_type"]}"',
            f'brev_search = "{pspec["brev_search"]}"',
            f"min_vram_gb_per_gpu = {pspec['min_vram_per_gpu']}",
            f"gpu_count = {gpu_count}",
            f"step_index = {idx}",
            f"step_count = {len(expects)}",
            f"check_count = {len(expect.get('checks') or [])}",
            f'role = "{role}"',
            "",
        ]
        (step_dir / "task.toml").write_text("\n".join(meta_lines))

        # environment/
        env_dir = step_dir / "environment"
        env_dir.mkdir(exist_ok=True)
        (env_dir / "Dockerfile").write_text("FROM scratch\n")

        # tests/
        tests_dir = step_dir / "tests"
        tests_dir.mkdir(exist_ok=True)
        (tests_dir / "test.sh").write_text(
            generate_test_script(idx, spec_name, scenario)
        )
        if GENERIC_JUDGE.exists():
            shutil.copy(GENERIC_JUDGE, tests_dir / "generic_judge.py")
        (tests_dir / spec_name).write_text(json.dumps(rendered_spec, indent=2) + "\n")

        # solution/
        solution_dir = step_dir / "solution"
        solution_dir.mkdir(exist_ok=True)
        (solution_dir / "solve.sh").write_text(generate_solve_script(platform, role))

        # skills/ — primary + deploy + VIOS + ask-video. The affirmative
        # verification step must exercise its actual bundled dependency.
        copies = [
            (skill_dir, "vss-search-archive"),
            (deploy_skill_dir, "vss-build-vision-ai"),
            (video_io_skill_dir, "vss-manage-video-io-storage"),
            (ask_video_skill_dir, "vss-ask-video"),
        ]
        for src, name in copies:
            if src and src.exists():
                dst = step_dir / "skills" / name
                if dst.exists():
                    shutil.rmtree(dst)
                shutil.copytree(src, dst)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--output-dir",
        required=True,
        help="Dataset output root (e.g. .github/skill-eval/datasets/vss-search-archive)",
    )
    parser.add_argument(
        "--skill-dir",
        required=True,
        help="Path to skills/operations/vss-search-archive",
    )
    parser.add_argument(
        "--deploy-skill-dir",
        default=None,
        help="Path to skills/vss-build-vision-ai (optional — included for agent debug)",
    )
    parser.add_argument(
        "--video-io-skill-dir",
        dest="video_io_skill_dir",
        default=None,
        help="Path to skills/operations/vss-manage-video-io-storage (optional — referenced by the spec for source-list lookup)",
    )
    parser.add_argument(
        "--ask-video-skill-dir",
        default=None,
        help="Path to skills/operations/vss-ask-video (optional — required by the confirmed verification step)",
    )
    parser.add_argument(
        "--vios-skill-dir", dest="video_io_skill_dir", help=argparse.SUPPRESS
    )
    if any(
        arg == "--vios-skill-dir" or arg.startswith("--vios-skill-dir=")
        for arg in sys.argv[1:]
    ):
        print(
            "WARNING: --vios-skill-dir is deprecated; use --video-io-skill-dir.",
            file=sys.stderr,
        )
    parser.add_argument(
        "--spec",
        default=None,
        help="Path to search.json (default: <skill-dir>/evals/search.json)",
    )
    parser.add_argument(
        "--platform",
        default=None,
        choices=list(PLATFORMS.keys()),
        help="Generate for one platform only (overrides spec.resources.platforms)",
    )
    args = parser.parse_args()

    output_root = Path(args.output_dir)
    skill_dir = Path(args.skill_dir)
    deploy_skill_dir = Path(args.deploy_skill_dir) if args.deploy_skill_dir else None
    video_io_skill_dir = (
        Path(args.video_io_skill_dir)
        if args.video_io_skill_dir
        else _peer_skill_dir(skill_dir, "vss-manage-video-io-storage")
    )
    ask_video_skill_dir = (
        Path(args.ask_video_skill_dir)
        if args.ask_video_skill_dir
        else _peer_skill_dir(skill_dir, "vss-ask-video")
    )
    if ask_video_skill_dir is None or not ask_video_skill_dir.is_dir():
        print(f"ask-video skill not found: {ask_video_skill_dir}", file=sys.stderr)
        sys.exit(1)
    if args.spec:
        spec_path = Path(args.spec)
    else:
        spec_path = skill_dir / "evals" / "search.json"
        if not spec_path.exists():
            legacy = skill_dir / "eval" / "search.json"
            if legacy.exists():
                spec_path = legacy

    if not spec_path.exists():
        print(f"spec not found: {spec_path}", file=sys.stderr)
        sys.exit(1)
    spec = json.loads(spec_path.read_text())
    spec["_source_path"] = str(spec_path)
    _validate_spec(spec)

    profile = spec.get("profile", "search")
    platforms = [args.platform] if args.platform else _platforms_from_spec(spec)

    print("=== Inputs ===")
    print(f"  output_dir   : {output_root}")
    print(f"  skill_dir    : {skill_dir}")
    print(f"  spec         : {spec_path}")
    print(f"  profile      : {profile}")
    print(f"  platforms    : {platforms}")
    print(f"  queries      : {len(spec.get('expects', []))}")
    print(
        f"  total checks : {sum(len(q.get('checks', [])) for q in spec.get('expects', []))}"
    )
    print()
    for platform in platforms:
        task_id = PLATFORMS[platform]["short_name"]
        print(f"  GEN  vss-search-archive/{profile}/{task_id}")
        generate_task(
            platform,
            profile,
            spec,
            output_root,
            skill_dir,
            deploy_skill_dir,
            video_io_skill_dir,
            ask_video_skill_dir,
        )
    print()
    print(f"Generated {len(platforms)} platform(s) under {output_root}/{profile}/")


if __name__ == "__main__":
    main()
