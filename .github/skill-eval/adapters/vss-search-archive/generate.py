#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Generate Harbor tasks for the vss-search-archive skill.

The vss-search-archive skill exercises the host-side NAT-free ``vss`` base
distribution for fused semantic + attribute search across pre-ingested video
sources. Search commands run from the repository checkout as ``uv run --project
"${VSS_REPO_ROOT}/services/agent" --no-dev vss search run --deployment docker
--profile search ...``; they never run
through a container/pod shell or a manually selected search endpoint.
It runs against a **full-remote-model VSS search profile** (deploy mode
= `remote-all`; LLM and underlying VLM inference use remote endpoints, while
the RT-VLM media proxy, Cosmos Embed1, and Elasticsearch remain local on the
GPU host). The first generated step deploys that profile, then uses the
agent-backed upload and completion handshake to seed the two named sample
videos and their search indexes. Later steps reuse that prepared state.

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
        skills/vss-deploy-profile/        (for prerequisite diagnostics)
        skills/vss-manage-video-io-storage/          (the search spec's first checks reference VIOS
                               as the canonical source-list lookup)
        environment/Dockerfile  (FROM scratch; BrevEnvironment takes over)

`<profile>` comes from `spec.profile` (here: `search`). `<k>` is the
1-based index into `expects[]`; single-step specs collapse the step
subdir.

Usage from the repository root:
    python3 .github/skill-eval/adapters/vss-search-archive/generate.py \\
        --output-dir .github/skill-eval/datasets/vss-search-archive \\
        --skill-dir skills/vss-search-archive \\
        --deploy-skill-dir skills/vss-deploy-profile \\
        --video-io-skill-dir skills/vss-manage-video-io-storage
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Platforms — mirrors the vss-manage-video-io-storage/vss-deploy-profile adapters so vss-search-archive runs on the
# same hosts. The spec's `resources.platforms` further filters this set.
# ---------------------------------------------------------------------------

PLATFORMS: dict[str, dict] = {
    "H100":          {"short_name": "h100",          "gpu_type": "H100",         "min_vram_per_gpu": 80, "brev_search": "H100"},
    "L40S":          {"short_name": "l40s",          "gpu_type": "L40S",         "min_vram_per_gpu": 48, "brev_search": "L40S"},
    "RTXPRO6000BW":  {"short_name": "rtxpro6000bw",  "gpu_type": "RTX PRO 6000", "min_vram_per_gpu": 96, "brev_search": "RTX PRO"},
    "DGX-SPARK":     {"short_name": "spark",         "gpu_type": "GB10",         "min_vram_per_gpu": 96, "brev_search": "GB10"},
    "IGX-THOR":      {"short_name": "thor",          "gpu_type": "Thor",         "min_vram_per_gpu": 64, "brev_search": "Thor"},
}

PREAMBLE = (
    "You are running inside a non-interactive evaluation harness. "
    "You are pre-authorized to deploy prerequisites autonomously — do not pause to ask "
    "for confirmation on `/vss-deploy-profile` or any other setup action the trial requires."
)

SETUP_PREAMBLE = (
    PREAMBLE
    + " Deploy the prerequisites and ingest only the two sample sources explicitly named "
    "by this setup step. Set `VSS_REPO_ROOT=\"${VSS_REPO_ROOT:-$HOME/video-search-and-summarization}\"`, "
    "require `${VSS_REPO_ROOT}/services/agent/pyproject.toml` to exist, and work from "
    "that checkout. After deployment, do not "
    "claim readiness until `curl -sf --max-time 15 http://localhost:8000/health` succeeds and "
    "`docker inspect -f '{{.State.Running}}' vss-agent redis` reports `true` for both containers; "
    "if either check fails, inspect `docker ps` and `docker logs vss-agent` first. Before completing "
    "setup on Brev, make `BREV_ENV_ID` from `/etc/environment` and any platform-provided "
    "`BREV_LINK_DOMAIN` available to the existing deployment workflow. Let that workflow "
    "select the secure-link domain; never construct a Brev hostname yourself. After local "
    "agent and VST health succeeds, require the fully expanded `VST_EXTERNAL_URL` from "
    "runtime `generated.env` to use HTTPS with neither localhost nor an internal IP, and "
    "prove a bounded GET through that exact public origin reaches VST before downloading "
    "or ingesting sample media. Stop if routing cannot be repaired within the bounded setup deadline. "
    "Do not rewrite media URLs after search to compensate for invalid deployment output. "
    "Before completing "
    "setup, prove the project-local host CLI is available with `cd \"${VSS_REPO_ROOT}\" && "
    "uv run --project \"${VSS_REPO_ROOT}/services/agent\" --no-dev vss search run --help`. "
    "If checkout validation fails, report it and stop. Do not look for a global `vss` executable. "
    "Before readiness or cleanup queries, execute the skill's single `RUNTIME_JSON` resolver, which "
    "uses `discover_docker`, `discover_docker_host_endpoints`, and "
    "`RuntimeSnapshot.from_config_file`. Use its distinct `video_embed_index`, `behavior_index`, "
    "and `raw_index` values; `ELASTIC_SEARCH_INDEX` is only the embedding index and must never be "
    "used for behavior or raw queries. For this pinned search profile the resolved values must be "
    "`mdx-embed-filtered-2025-01-01`, `mdx-behavior-2025-01-01`, and "
    "`mdx-raw-2025-01-01`, respectively. Before uploading, make setup idempotent on reused hosts: "
    "list VST through the discovered `vst_url` and, if present, agent-delete any exact or deduplicated remnants "
    "of the two eval fixtures (`warehouse_sample`, `warehouse-ladder`, and the legacy "
    "`sample-warehouse-ladder` alias), require successful cleanup, and wait until they are absent. "
    "For each fixture, use the skill's mandatory three-step file ingestion flow exactly: "
    "POST the filename to `${AGENT_URL}/api/v1/videos`, POST the file bytes to the returned "
    "VST upload URL, then POST the upload result to "
    "`${AGENT_URL}/api/v1/videos/${SENSOR}/complete` and validate that separate response. "
    "Never call the deprecated single-step `PUT /api/v1/videos-for-search/{filename}` route. "
    "After each validated `/complete`, poll the exact readiness tuples independently: embedding "
    "index with `sensor.id.keyword=<resolved VST UUID>`, behavior index with "
    "`sensor.id.keyword=<canonical source name>`, and raw index with "
    "`sensorId.keyword=<canonical source name>`. Print all three index names and final counts."
)

OPERATION_PREAMBLE = (
    PREAMBLE
    + " The search profile "
    "and evaluation fixtures were prepared by the preceding setup step. Do not redeploy "
    "the profile and do not ingest or re-ingest any source during this step. Set "
    "`VSS_REPO_ROOT=\"${VSS_REPO_ROOT:-$HOME/video-search-and-summarization}\"`, require "
    "`${VSS_REPO_ROOT}/services/agent/pyproject.toml` to exist, and work from that checkout. "
    "List registered sources through the prepared deployment's discovered VST/VIOS "
    "connectivity: resolve `vst_url` with `discover_docker_host_endpoints(\"search\")` and "
    "GET its `/vst/api/v1/sensor/list`; do not assume a fixed port. If "
    "the requested source is not registered, follow the skill's missing-source rule: list "
    "registered sources, report the missing source, and stop without silently substituting "
    "another source. Do not test or invoke the search CLI for a missing source. When it is "
    "missing, end by asking the user to clarify the source or explicitly request ingestion; "
    "that clarification is "
    "not a request for setup confirmation. For a resolved search request, decompose the request, "
    "pass the decomposed visual query with `--query` (never as a positional argument), select an "
    "explicit supported search mode, and pass its concrete value with `--search-mode`, "
    "and run the host checkout's project-local `cd \"${VSS_REPO_ROOT}\" && uv run --project "
    "\"${VSS_REPO_ROOT}/services/agent\" --no-dev vss search run` with `--deployment docker "
    "--profile search`, the resolved `--video-source`, `--output json --raw`, and any result "
    "limit stated in the query. Put that fully constructed invocation in a `SEARCH_COMMAND` "
    "bash array and execute it with `if ! SEARCH_JSON=$(\"${SEARCH_COMMAND[@]}\"); then` so only "
    "the command's exact stdout is captured as `SEARCH_JSON`; fail on "
    "a nonzero command status, and use `jq -e` to require a SearchOutput object with a data "
    "array before parsing it. The forklift and ladder queries explicitly require results, so "
    "require a nonempty data array for those two steps; the neon-pink negative retrieval step "
    "may legitimately return zero and must report the exact count without failing. Always "
    "assert that the number of successfully validated hits equals its length. Parse that compact JSON "
    "internally. Do not download or visually inspect screenshot images during a "
    "search query unless it explicitly requests visual verification; offer a "
    "`Verification Step` instead, and never paste raw JSON "
    "into the reply. For nonempty results, use the exact headings `## Video Search Results` "
    "and `## Verification Step`, preserve the CLI evidence fields, and explicitly say that "
    "similarity scores are retrieval evidence rather than visual confirmation. Validate each "
    "media URL with a bounded GET of the exact returned URL without adding a VST `streamId` routing header. "
    "Extract the exact `screenshot_url` from each CLI hit and verify that URL's "
    "scheme, hostname, and effective port match the fully expanded `VST_EXTERNAL_URL` obtained "
    "through the same deployment selector as the search command (do not assume it is exported "
    "in the shell). On this Brev evaluation, independently require both origins to use public "
    "HTTPS and reject HTTP, localhost, single-label/internal hostnames, and private, loopback, "
    "link-local, reserved, or otherwise non-global IP addresses, then GET "
    "that same unmodified returned URL. Never substitute `VST_EXTERNAL_URL`, localhost, or a "
    "reconstructed URL for the returned screenshot URL; "
    "discard the body unless visual inspection is authorized. When inspection is authorized, "
    "save every exact returned screenshot response under `/tmp/`, require each file to be nonempty, "
    "inspect every saved file's pixels, and report a verdict for every hit. For a deletion request, do "
    "not run search; use the skill's agent-backed cleanup workflow. Resolve the agent endpoint and "
    "the distinct embedding, behavior, and raw indexes with the same `RUNTIME_JSON` recipe used "
    "during setup, save the source UUID and canonical name before DELETE, require status `success`, "
    "and poll the exact three index/field/value tuples to zero. Never use the embedding index for "
    "behavior or raw cleanup validation. Do not look for a global executable. If the host command "
    "fails, report its error and stop instead of substituting another search interface."
)

KUBERNETES_INGRESS_CONTRACT_PREAMBLE = (
    PREAMBLE
    + " This step is a read-only Kubernetes Ingress contract check. Do not deploy, "
    "redeploy, execute the example commands, inspect a cluster, or reuse the Docker "
    "deployment from earlier steps. Follow the skill's VSS_PUBLIC_URL branch: source "
    "listing uses the public /vst route and search uses the public Agent /generate "
    "route. Do not use kubectl, port-forward, Service DNS, NodePorts, localhost ports, "
    "direct Elasticsearch/RTVI access, or the host CLI Kubernetes selector."
)


# ---------------------------------------------------------------------------
# Generation
# ---------------------------------------------------------------------------
def generate_test_script(step: int, spec_name: str) -> str:
    """Wrapper that invokes the generic judge for one step's checks."""
    return (
        "#!/bin/bash\n"
        f"# vss-search-archive verifier (step {step}): delegates to the generic\n"
        "# LLM-as-judge (.github/skill-eval/verifiers/generic_judge.py).\n"
        "set -euo pipefail\n"
        "\n"
        'TEST_DIR="$(cd "$(dirname "$0")" && pwd)"\n'
        "python3 -m pip install --quiet 'anthropic>=0.40.0' >/dev/null 2>&1 || true\n"
        "\n"
        'python3 "$TEST_DIR/generic_judge.py" \\\n'
        f'    --spec "$TEST_DIR/{spec_name}" --step {step}\n'
    )


def generate_solve_script(platform: str) -> str:
    """Gold solution — assumes the search profile is already deployed and
    the sample videos are ingested. The verifier drives the assertions
    independently against the host-side ``vss`` command and its output."""
    return (
        "#!/bin/bash\n"
        f"# Gold solution: vss-search-archive on {platform}\n"
        "set -euo pipefail\n"
        "\n"
        "curl -sf --connect-timeout 5 "
        "${VSS_AGENT_URL:-http://localhost:8000}/health "
        ">/dev/null || {\n"
        "    echo 'VSS agent is not deployed — cannot solve vss-search-archive task'\n"
        "    exit 1\n"
        "}\n"
        'VSS_REPO_ROOT="${VSS_REPO_ROOT:-$HOME/video-search-and-summarization}"\n'
        'test -f "${VSS_REPO_ROOT}/services/agent/pyproject.toml" || {\n'
        '    echo "VSS checkout not found at ${VSS_REPO_ROOT}; set VSS_REPO_ROOT explicitly"\n'
        "    exit 1\n"
        "}\n"
        'cd "${VSS_REPO_ROOT}"\n'
        'PROFILE_DIR="${VSS_REPO_ROOT}/deploy/docker/developer-profiles/dev-profile-search"\n'
        'test -f "${PROFILE_DIR}/.env" -a -f "${PROFILE_DIR}/generated.env" || {\n'
        '    echo "Search profile requires .env and runtime generated.env"\n'
        "    exit 1\n"
        "}\n"
        'uv run --project "${VSS_REPO_ROOT}/services/agent" --no-dev '
        "vss search run --help >/dev/null\n"
        "echo 'VSS agent and the project-local host CLI are ready.'\n"
    )


GENERIC_JUDGE = Path(__file__).resolve().parents[2] / "verifiers" / "generic_judge.py"


def _platforms_from_spec(spec: dict) -> list[str]:
    """Return declared platforms, rejecting unsupported adapter targets."""
    declared = spec["resources"]["platforms"]
    unsupported = sorted(set(declared) - set(PLATFORMS))
    if unsupported:
        raise ValueError(f"unsupported platform(s): {', '.join(unsupported)}")
    return list(declared)


def _validate_spec(spec: dict) -> None:
    """Validate the adapter-owned subset of the skill-eval spec contract."""
    skills = spec.get("skills")
    if not isinstance(skills, list) or not skills or not all(isinstance(item, str) and item for item in skills):
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
        if isinstance(gpu_count, bool) or not isinstance(gpu_count, int) or gpu_count <= 0:
            raise ValueError(f"spec.resources.platforms.{platform}.gpu_count must be a positive integer")

    expects = spec.get("expects")
    if not isinstance(expects, list) or not expects:
        raise ValueError("spec.expects must be a non-empty list")
    for index, expect in enumerate(expects, 1):
        if not isinstance(expect, dict):
            raise ValueError(f"spec.expects[{index}] must be an object")
        if not isinstance(expect.get("query"), str) or not expect["query"].strip():
            raise ValueError(f"spec.expects[{index}].query must be a non-empty string")
        checks = expect.get("checks")
        if not isinstance(checks, list) or not checks or not all(
            isinstance(check, str) and check.strip() for check in checks
        ):
            raise ValueError(f"spec.expects[{index}].checks must be a non-empty list of strings")


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
        return [_render_spec(item, platform=platform, profile=profile) for item in value]
    if isinstance(value, dict):
        return {
            key: _render_spec(item, platform=platform, profile=profile)
            for key, item in value.items()
        }
    return value


def generate_task(platform: str, profile: str, spec: dict, output_root: Path,
                  skill_dir: Path, deploy_skill_dir: Path | None,
                  video_io_skill_dir: Path | None) -> None:
    _validate_spec(spec)
    if platform not in PLATFORMS:
        raise ValueError(f"unsupported platform: {platform}")
    spec_profile = spec.get("profile", "search")
    if profile != spec_profile:
        raise ValueError(f"profile {profile!r} does not match spec profile {spec_profile!r}")
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
        if expect.get("scenario") == "kubernetes-ingress-contract":
            preamble = KUBERNETES_INGRESS_CONTRACT_PREAMBLE
        else:
            preamble = SETUP_PREAMBLE if idx == 1 else OPERATION_PREAMBLE
        lines = [
            preamble,
            "",
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
            f'min_vram_gb_per_gpu = {pspec["min_vram_per_gpu"]}',
            f'gpu_count = {gpu_count}',
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

        # tests/
        tests_dir = step_dir / "tests"
        tests_dir.mkdir(exist_ok=True)
        (tests_dir / "test.sh").write_text(generate_test_script(idx, spec_name))
        if GENERIC_JUDGE.exists():
            shutil.copy(GENERIC_JUDGE, tests_dir / "generic_judge.py")
        (tests_dir / spec_name).write_text(json.dumps(rendered_spec, indent=2) + "\n")

        # solution/
        solution_dir = step_dir / "solution"
        solution_dir.mkdir(exist_ok=True)
        (solution_dir / "solve.sh").write_text(generate_solve_script(platform))

        # skills/ — primary + deploy (for prereq diagnostics) + VIOS
        # (search spec's first checks reference VIOS for source-list lookup).
        copies = [(skill_dir, "vss-search-archive"),
                  (deploy_skill_dir, "vss-deploy-profile"),
                  (video_io_skill_dir, "vss-manage-video-io-storage")]
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
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--output-dir", required=True,
                        help="Dataset output root (e.g. .github/skill-eval/datasets/vss-search-archive)")
    parser.add_argument("--skill-dir", required=True,
                        help="Path to skills/vss-search-archive")
    parser.add_argument("--deploy-skill-dir", default=None,
                        help="Path to skills/vss-deploy-profile (optional — included for agent debug)")
    parser.add_argument("--video-io-skill-dir", dest="video_io_skill_dir", default=None,
                        help="Path to skills/vss-manage-video-io-storage (optional — referenced by the spec for source-list lookup)")
    parser.add_argument("--vios-skill-dir", dest="video_io_skill_dir", help=argparse.SUPPRESS)
    if any(arg == "--vios-skill-dir" or arg.startswith("--vios-skill-dir=") for arg in sys.argv[1:]):
        print("WARNING: --vios-skill-dir is deprecated; use --video-io-skill-dir.", file=sys.stderr)
    parser.add_argument("--spec", default=None,
                        help="Path to search.json (default: <skill-dir>/evals/search.json)")
    parser.add_argument("--platform", default=None, choices=list(PLATFORMS.keys()),
                        help="Generate for one platform only (overrides spec.resources.platforms)")
    args = parser.parse_args()

    output_root = Path(args.output_dir)
    skill_dir = Path(args.skill_dir)
    deploy_skill_dir = Path(args.deploy_skill_dir) if args.deploy_skill_dir else None
    video_io_skill_dir = Path(args.video_io_skill_dir) if args.video_io_skill_dir else None
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
    print(f"  total checks : {sum(len(q.get('checks', [])) for q in spec.get('expects', []))}")
    print()
    for platform in platforms:
        task_id = PLATFORMS[platform]["short_name"]
        print(f"  GEN  vss-search-archive/{profile}/{task_id}")
        generate_task(platform, profile, spec, output_root, skill_dir,
                      deploy_skill_dir, video_io_skill_dir)
    print()
    print(f"Generated {len(platforms)} platform(s) under {output_root}/{profile}/")


if __name__ == "__main__":
    main()
