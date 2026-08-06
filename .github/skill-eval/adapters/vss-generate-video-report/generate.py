#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Generate Harbor tasks for the vss-generate-video-report skill.

The vss-generate-video-report skill produces Mode A video reports by calling
an OpenAI-compatible VLM directly with a VIOS clip URL, and Mode B incident
reports through VA-MCP analytics. It never routes report generation through
the VSS agent's ``POST /generate`` endpoint.

The evaluation chain deploys a local base profile for Mode A (integrated CR3
RT-VLM on host port 8018 plus VIOS), then deploys alerts when Mode B needs
analytics. The spec targets **ONE platform** by default (RTXPRO6000BW).
Override with ``--platform``.

## Directory layout

    .github/skill-eval/datasets/vss-generate-video-report/<profile>/<platform>/           (single-step spec)
        task.toml
        instruction.md
        tests/test.sh
        tests/<spec>.json
        tests/generic_judge.py
        solution/solve.sh
        skills/vss-generate-video-report/
        skills/vss-deploy-profile/
        skills/vss-manage-video-io-storage/
        environment/Dockerfile

``<profile>`` comes from ``spec.profile`` (here: ``base``).

Usage from the repository root:
    python3 .github/skill-eval/adapters/vss-generate-video-report/generate.py \\
        --output-dir .github/skill-eval/datasets/vss-generate-video-report \\
        --skill-dir skills/vss-generate-video-report \\
        --deploy-skill-dir skills/vss-deploy-profile \\
        --video-io-skill-dir skills/vss-manage-video-io-storage \\
        --spec skills/vss-generate-video-report/evals/base_profile_report.json
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Platforms — same table as the other adapters; spec.resources.platforms
# narrows it down further.
# ---------------------------------------------------------------------------

PLATFORMS: dict[str, dict] = {
    "H100":         {"short_name": "h100",         "gpu_type": "H100",         "min_vram_per_gpu": 80, "brev_search": "H100"},
    "L40S":         {"short_name": "l40s",         "gpu_type": "L40S",         "min_vram_per_gpu": 48, "brev_search": "L40S"},
    "RTXPRO6000BW": {"short_name": "rtxpro6000bw", "gpu_type": "RTX PRO 6000", "min_vram_per_gpu": 96, "brev_search": "RTX PRO"},
    "DGX-SPARK":    {"short_name": "spark",        "gpu_type": "GB10",         "min_vram_per_gpu": 96, "brev_search": "GB10"},
    "IGX-THOR":     {"short_name": "thor",         "gpu_type": "Thor",         "min_vram_per_gpu": 64, "brev_search": "Thor"},
}

DEFAULT_PLATFORM = "RTXPRO6000BW"
DEFAULT_MIN_ROOT_DISK_GB = 220

# Prepended to every instruction.md so the skill's own HITL bypass clause
# fires.  Skills default to "ask the user" before /vss-deploy-profile; in CI there is no
# user, so without this preamble the agent stalls or falls through to a
# localhost default.
PREAMBLE = (
    "You are running inside a non-interactive evaluation harness. "
    "You are pre-authorized to deploy prerequisites autonomously — "
    "do not pause to ask for confirmation on `/vss-deploy-profile` or any other "
    "setup action the trial requires."
)

LOCAL_RT_VLM_ENV_OVERRIDES = [
    "VLM_MODE=local_shared",
    "VLM_MODEL_TYPE=rtvi",
    "VLM_NAME=nim_nvidia_cosmos3-nano-reasoner_bf16-final",
    "VLM_NAME_SLUG=none",
]

MODE_A_DEPLOYMENT_GUIDANCE = (
    "This Mode A report chain requires the base deployment to preserve its "
    "local integrated CR3 RT-VLM placement. If this trial needs to regenerate "
    "the base compose project (including when a remembered compose ID is no "
    "longer available), call `vss_orchestrator__docker_generate` with "
    f"`profile=base` and `env_overrides={json.dumps(LOCAL_RT_VLM_ENV_OVERRIDES)}` "
    "exactly. Never regenerate the base profile with `profile=base` alone or "
    "inherit the notebook's remote VLM coordinator defaults. A forced refresh "
    "must use the compose ID returned by that override-aware generation."
)

GENERIC_JUDGE = Path(__file__).resolve().parents[2] / "verifiers" / "generic_judge.py"


# ---------------------------------------------------------------------------
# Generation helpers
# ---------------------------------------------------------------------------

def generate_test_script(step: int, spec_name: str) -> str:
    """Shell wrapper that invokes the generic LLM-as-judge verifier for
    a single step's checks.  Harbor reads /logs/verifier/reward.txt."""
    return (
        "#!/bin/bash\n"
        f"# vss-generate-video-report verifier (step {step}): delegates to the generic\n"
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


def generate_solve_script(platform: str) -> str:
    """Reference smoke stub for the local Mode A prerequisites."""
    return (
        "#!/bin/bash\n"
        f"# Reference prerequisite smoke check on {platform}\n"
        "# The verifier judges the requested operation. This script only\n"
        "# confirms the base profile's VIOS and integrated RT-VLM endpoints.\n"
        "set -euo pipefail\n"
        "\n"
        'HOST_IP="${HOST_IP:-localhost}"\n'
        "\n"
        "curl -sf --connect-timeout 5 "
        '"http://${HOST_IP}:30888/vst/api/v1/sensor/version" '
        ">/dev/null || {\n"
        "    echo 'VIOS is not reachable — cannot solve report task'\n"
        "    exit 1\n"
        "}\n"
        "curl -sf --connect-timeout 5 "
        '"http://${HOST_IP}:8018/v1/models" '
        ">/dev/null || {\n"
        "    echo 'Base RT-VLM is not reachable — cannot solve report task'\n"
        "    exit 1\n"
        "}\n"
        "echo 'VIOS and base RT-VLM are live — verifier will inspect the requested operation.'\n"
    )


def _platforms_from_spec(spec: dict) -> list[str]:
    declared = ((spec.get("resources") or {}).get("platforms") or {})
    if not declared:
        return [DEFAULT_PLATFORM]
    return [p for p in declared if p in PLATFORMS] or [DEFAULT_PLATFORM]


def _nemoclaw_sample_files(expect: dict) -> list[str]:
    """Return the sample fixtures explicitly requested by one eval step."""
    raw = expect.get("nemoclaw_sample_files") or []
    if not isinstance(raw, list) or not all(
        isinstance(value, str) for value in raw
    ):
        raise ValueError("nemoclaw_sample_files must be a JSON array of strings")
    return raw


def _substitute_spec(spec: dict, platform: str) -> dict:
    """Render supported placeholders recursively and reject spec drift."""
    substitutions = {
        "platform": platform,
        "repo_root": "$HOME/video-search-and-summarization",
    }
    pattern = re.compile(r"\{\{\s*(\w+)\s*\}\}")

    def _substitute(value):
        if isinstance(value, str):
            return pattern.sub(
                lambda match: str(
                    substitutions.get(match.group(1), match.group(0))
                ),
                value,
            )
        if isinstance(value, list):
            return [_substitute(item) for item in value]
        if isinstance(value, dict):
            return {key: _substitute(item) for key, item in value.items()}
        return value

    rendered = _substitute(spec)
    # Docker Go templates legitimately contain the bare control token
    # ``{{end}}``; it is not an eval placeholder.
    unresolved = sorted(
        set(pattern.findall(json.dumps(rendered))) - {"end"}
    )
    if unresolved:
        raise ValueError(
            "unresolved eval placeholders: " + ", ".join(unresolved)
        )
    return rendered


# ---------------------------------------------------------------------------
# Task generation
# ---------------------------------------------------------------------------

def generate_task(
    platform: str,
    profile: str,
    spec: dict,
    output_root: Path,
    skill_dir: Path,
    deploy_skill_dir: Path | None,
    video_io_skill_dir: Path | None,
    query_analytics_skill_dir: Path | None = None,
) -> None:
    """Emit one Harbor task directory per entry in spec['expects'] — i.e.
    step-<k>/ subdirs under ``<profile>/<platform_short>/`` per AGENTS.md § 4.
    Single-step specs collapse to a flat ``<profile>/<platform_short>/``."""
    pspec = PLATFORMS[platform]
    platform_short = pspec["short_name"]
    rendered_spec = _substitute_spec(spec, platform)
    expects = rendered_spec.get("expects") or []
    spec_name = Path(spec.get("_source_path", "spec.json")).name or "spec.json"
    gpu_count = int(
        ((rendered_spec.get("resources") or {}).get("platforms") or {})
        .get(platform, {})
        .get("gpu_count", 1)
    )
    if gpu_count < 1:
        raise ValueError(
            "vss-generate-video-report local RT-VLM tasks require gpu_count >= 1"
        )

    for idx, expect in enumerate(expects, 1):
        step_dir = output_root / profile / platform_short
        if len(expects) > 1:
            step_dir = step_dir / f"step-{idx}"
        step_dir.mkdir(parents=True, exist_ok=True)

        # instruction.md — ONE step's query + environment notes ONLY.
        # Never leak the verifier's checks[] into the instruction so the
        # agent can't write to the test rather than do the actual work.
        step_suffix = f"-step-{idx}" if len(expects) > 1 else ""
        lines = [PREAMBLE, ""]
        # Steps 1-5 are the ordered Mode A prefix. Each Harbor step starts a
        # fresh agent, and the generic NemoClaw wrapper may refresh a warm
        # worker with force_recreate=true. Keep the local CR3 placement in
        # every step's instruction so a missing/stale compose ID cannot fall
        # back to the notebook's remote coordinator defaults mid-chain.
        if idx <= 5:
            lines.extend([MODE_A_DEPLOYMENT_GUIDANCE, ""])
        lines.extend(
            [
                f"## Query {idx} of {len(expects)}",
                "",
                expect.get("query", ""),
                "",
                "Run autonomously without prompting for confirmation.",
                "",
            ]
        )
        (step_dir / "instruction.md").write_text("\n".join(lines) + "\n")

        # task.toml
        meta_lines = [
            "[task]",
            f'name = "nvidia-vss/vss-generate-video-report-{profile}-{platform_short}{step_suffix}"',
            f'description = "vss-generate-video-report query {idx}/{len(expects)} on {platform}"',
            f'keywords = ["vss-generate-video-report", "generate", "{profile}", "{platform}"]',
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
            # ANTHROPIC_MODEL gives the verifier's judge model cascade
            # (JUDGE_MODEL → ANTHROPIC_MODEL → literal) a working fallback
            # when JUDGE_MODEL is unset. Forwarding a literal default for
            # JUDGE_MODEL would bake it in and short-circuit the cascade.
            'ANTHROPIC_MODEL = "${ANTHROPIC_MODEL}"',
            "",
            "[metadata]",
            'skill = "vss-generate-video-report"',
            f'platform = "{platform}"',
            f'gpu_type = "{pspec["gpu_type"]}"',
            f'brev_search = "{pspec["brev_search"]}"',
            f'min_vram_gb_per_gpu = {pspec["min_vram_per_gpu"]}',
            f"gpu_count = {gpu_count}",
            f"min_root_disk_gb = {DEFAULT_MIN_ROOT_DISK_GB}",
            "# Mode A uses the base profile's local integrated CR3 RT-VLM and VIOS.",
            "# Mode B may transition the same worker to alerts for VA-MCP analytics.",
            f"step_index = {idx}",
            f"step_count = {len(expects)}",
            f"check_count = {len(expect.get('checks') or [])}",
            "",
        ]
        sample_files = _nemoclaw_sample_files(expect)
        if sample_files:
            meta_lines.insert(
                -1,
                f"nemoclaw_sample_files = {json.dumps(sample_files)}",
            )
        (step_dir / "task.toml").write_text("\n".join(meta_lines))

        # environment/ placeholder (BrevEnvironment takes over)
        env_dir = step_dir / "environment"
        env_dir.mkdir(exist_ok=True)
        (env_dir / "Dockerfile").write_text("FROM scratch\n")

        # tests/ — wrapper + generic judge + spec copy
        tests_dir = step_dir / "tests"
        tests_dir.mkdir(exist_ok=True)
        (tests_dir / "test.sh").write_text(generate_test_script(idx, spec_name))
        if GENERIC_JUDGE.exists():
            shutil.copy(GENERIC_JUDGE, tests_dir / "generic_judge.py")
        # The verifier must see the same rendered platform/repository values
        # as instruction.md. Writing the source spec here would leave
        # {{platform}} unresolved and grade a different contract.
        (tests_dir / spec_name).write_text(json.dumps(rendered_spec, indent=2))

        # solution/
        solution_dir = step_dir / "solution"
        solution_dir.mkdir(exist_ok=True)
        (solution_dir / "solve.sh").write_text(generate_solve_script(platform))

        # skills/ — vss-generate-video-report + deploy + VIOS (the spec env mentions pre-uploading
        # a sample warehouse video via vss-manage-video-io-storage before running report checks).
        copies = [
            (skill_dir,        "vss-generate-video-report"),
            (deploy_skill_dir, "vss-deploy-profile"),
            (video_io_skill_dir,   "vss-manage-video-io-storage"),
            (query_analytics_skill_dir, "vss-query-analytics"),
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
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--output-dir", required=True,
        help="Dataset output root (e.g. .github/skill-eval/datasets/vss-generate-video-report)",
    )
    parser.add_argument(
        "--skill-dir", required=True,
        help="Path to skills/vss-generate-video-report",
    )
    parser.add_argument(
        "--deploy-skill-dir", default=None,
        help="Path to skills/vss-deploy-profile (optional — included for agent diagnosis)",
    )
    parser.add_argument(
        "--video-io-skill-dir", dest="video_io_skill_dir", default=None,
        help="Path to skills/vss-manage-video-io-storage (optional — spec env references VIOS video upload)",
    )
    parser.add_argument("--vios-skill-dir", dest="video_io_skill_dir", help=argparse.SUPPRESS)
    if any(arg == "--vios-skill-dir" or arg.startswith("--vios-skill-dir=") for arg in sys.argv[1:]):
        print("WARNING: --vios-skill-dir is deprecated; use --video-io-skill-dir.", file=sys.stderr)
    parser.add_argument(
        "--query-analytics-skill-dir", default=None,
        help="Path to skills/vss-query-analytics (optional — spec steps 6-8 use /vss-query-analytics)",
    )
    parser.add_argument(
        "--spec", default=None,
        help="Path to spec JSON (default: <skill-dir>/evals/base_profile_report.json)",
    )
    parser.add_argument(
        "--platform", default=None, choices=list(PLATFORMS.keys()),
        help=f"Generate for one platform only (overrides spec.resources.platforms; "
             f"default: {DEFAULT_PLATFORM})",
    )
    args = parser.parse_args()

    output_root = Path(args.output_dir)
    skill_dir = Path(args.skill_dir)
    deploy_skill_dir = Path(args.deploy_skill_dir) if args.deploy_skill_dir else None
    video_io_skill_dir = Path(args.video_io_skill_dir) if args.video_io_skill_dir else None
    query_analytics_skill_dir = Path(args.query_analytics_skill_dir) if args.query_analytics_skill_dir else None
    spec_path = (
        Path(args.spec)
        if args.spec
        else (skill_dir / "evals" / "base_profile_report.json")
    )

    if not spec_path.exists():
        print(f"spec not found: {spec_path}", file=sys.stderr)
        sys.exit(1)
    spec = json.loads(spec_path.read_text())
    spec["_source_path"] = str(spec_path)

    profile = spec.get("profile", "base")
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
        print(f"  GEN  vss-generate-video-report/{profile}/{task_id}")
        generate_task(
            platform, profile, spec, output_root, skill_dir,
            deploy_skill_dir, video_io_skill_dir, query_analytics_skill_dir,
        )
    print()
    print(f"Generated {len(platforms)} platform(s) under {output_root}/{profile}/")
    print()
    print("Note: the task chain deploys the required base/alerts profiles on the target")
    print("Brev instance and seeds the sample warehouse video through VIOS as requested.")


if __name__ == "__main__":
    main()
