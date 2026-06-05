#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Generate Harbor tasks for the vss-query-analytics skill.

The vss-query-analytics skill answers **read-only** analytics questions
(incidents, metrics, sensor data) by routing through the VA-MCP server
(port 9901), backed by Elasticsearch. It must NOT trigger deploys, call
live VLM endpoints, or POST to ``/generate``.

The spec (``skills/vss-query-analytics/evals/query_analytics.json``)'s
**first** ``expects[]`` query deploys the VSS alerts profile in real-time
mode via ``/vss-deploy-profile`` in the trial's own first turn; the
remaining queries then read analytics over the VA-MCP server it brings
up. The harness no longer pre-deploys anything (the
``_ensure_prerequisite_deployed`` hook + ``active-deploy`` marker were
removed), so no ``profile`` / ``requires_deployed_vss`` /
``prerequisite_deploy_mode`` metadata is emitted.

Because VA-MCP queries are HTTP/JSON-RPC against a running stack —
GPU-independent at the skill level — the spec targets **ONE platform**
(L40S — cheapest available host) via ``resources.platforms``. Override
with ``--platform``.

## Directory layout

    <output-dir>/alerts/<platform>/                       (multi-step spec)
        step-<k>/
            task.toml
            instruction.md
            tests/test.sh
            tests/query_analytics.json
            tests/generic_judge.py
            solution/solve.sh
            skills/vss-query-analytics/
            skills/vss-deploy-profile/
            environment/Dockerfile

``<profile>`` comes from ``spec.profile`` (here: ``alerts``).

The five ``expects`` entries are independent read-only queries. They are
emitted as a step-chain (``step-1`` .. ``step-N``) following the adapter
convention; the coordinator's dispatch loop runs them in order. No step
depends on state established by a prior step, so the skip-on-prior-fail
behaviour of the dispatch loop is harmless here.

Usage from the repository root:
    python3 .github/skill-eval/adapters/vss-query-analytics/generate.py \\
        --output-dir <scratch>/datasets/vss-query-analytics/query_analytics \\
        --skill-dir skills/vss-query-analytics \\
        --deploy-skill-dir skills/vss-deploy-profile \\
        --spec skills/vss-query-analytics/evals/query_analytics.json
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Platforms — mirrors the other adapters; spec.resources.platforms narrows.
# ---------------------------------------------------------------------------

PLATFORMS: dict[str, dict] = {
    "H100":         {"short_name": "h100",         "gpu_type": "H100",         "min_vram_per_gpu": 80, "brev_search": "H100"},
    "L40S":         {"short_name": "l40s",         "gpu_type": "L40S",         "min_vram_per_gpu": 48, "brev_search": "L40S"},
    "RTXPRO6000BW": {"short_name": "rtxpro6000bw", "gpu_type": "RTX PRO 6000", "min_vram_per_gpu": 96, "brev_search": "RTX PRO"},
    "DGX-SPARK":    {"short_name": "spark",        "gpu_type": "GB10",         "min_vram_per_gpu": 96, "brev_search": "GB10"},
    "IGX-THOR":     {"short_name": "thor",         "gpu_type": "Thor",         "min_vram_per_gpu": 64, "brev_search": "Thor"},
}

DEFAULT_PLATFORM = "L40S"

# Prepended to every instruction.md so the skill's own HITL bypass clause
# fires. Skills default to "ask the user" before /vss-deploy-profile; in CI
# there is no user, so without this preamble the agent stalls or falls
# through to a localhost default.
PREAMBLE = (
    "You are running inside a non-interactive evaluation harness. "
    "You are pre-authorized to deploy prerequisites autonomously — "
    "do not pause to ask for confirmation on `/vss-deploy-profile` or any other "
    "setup action the trial requires."
)

GENERIC_JUDGE = Path(__file__).resolve().parents[2] / "verifiers" / "generic_judge.py"


# ---------------------------------------------------------------------------
# Generation helpers
# ---------------------------------------------------------------------------

def generate_test_script(step: int, spec_name: str) -> str:
    """Shell wrapper that invokes the generic LLM-as-judge verifier for a
    single step's checks. Harbor reads /logs/verifier/reward.txt."""
    return (
        "#!/bin/bash\n"
        f"# vss-query-analytics verifier (step {step}): delegates to the generic\n"
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
    """Gold solution — assumes the VSS alerts profile is already deployed
    and VA-MCP is reachable. The verifier drives the VA-MCP assertions; the
    solution script just asserts the endpoint is live, then defers."""
    return (
        "#!/bin/bash\n"
        f"# Gold solution: vss-query-analytics on {platform}\n"
        "# The verifier drives the VA-MCP queries directly — the solution\n"
        "# script simply asserts the VA-MCP endpoint is live, then defers.\n"
        "set -euo pipefail\n"
        "\n"
        'code=$(curl -sf --max-time 5 -o /dev/null -w "%{http_code}" '
        '"http://${HOST_IP:-localhost}:9901/mcp" || true)\n'
        'case "$code" in\n'
        "    2*|3*|405) echo \"VA-MCP is live (HTTP $code) — verifier will drive queries.\" ;;\n"
        "    *) echo \"VA-MCP not reachable (HTTP ${code:-000}) — cannot solve analytics task\"; exit 1 ;;\n"
        "esac\n"
    )


def _platforms_from_spec(spec: dict) -> list[str]:
    declared = ((spec.get("resources") or {}).get("platforms") or {})
    if not declared:
        return [DEFAULT_PLATFORM]
    return [p for p in declared if p in PLATFORMS] or [DEFAULT_PLATFORM]


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
) -> None:
    """Emit one Harbor task directory per entry in spec['expects'] — i.e.
    step-<k>/ subdirs under ``<profile>/<platform_short>/`` per AGENTS.md § 4.
    Single-step specs collapse to a flat ``<profile>/<platform_short>/``."""
    pspec = PLATFORMS[platform]
    platform_short = pspec["short_name"]
    expects = spec.get("expects") or []
    spec_name = Path(spec.get("_source_path", "spec.json")).name or "spec.json"

    for idx, expect in enumerate(expects, 1):
        step_dir = output_root / profile / platform_short
        if len(expects) > 1:
            step_dir = step_dir / f"step-{idx}"
        step_dir.mkdir(parents=True, exist_ok=True)

        # instruction.md — ONE step's query + environment notes ONLY.
        # Never leak the verifier's checks[] into the instruction so the
        # agent can't write to the test rather than do the actual work.
        step_suffix = f"-step-{idx}" if len(expects) > 1 else ""
        lines = [
            PREAMBLE,
            "",
            f"Use the `/vss-query-analytics` skill on this `{platform}` host to "
            "answer analytics questions over VA-MCP "
            "(`http://${HOST_IP:-localhost}:9901/mcp`). If a step's query asks you "
            "to deploy first, use `/vss-deploy-profile`; the analytics queries "
            "themselves are **read-only** over VA-MCP and must not trigger deploys "
            "or call live VLM / report endpoints.",
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
        meta_lines = [
            "[task]",
            f'name = "nvidia-vss/vss-query-analytics-{profile}-{platform_short}{step_suffix}"',
            f'description = "vss-query-analytics query {idx}/{len(expects)} on {platform}"',
            f'keywords = ["vss-query-analytics", "analytics", "va-mcp", "{profile}", "{platform}"]',
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
            'skill = "vss-query-analytics"',
            f'platform = "{platform}"',
            f'gpu_type = "{pspec["gpu_type"]}"',
            f'brev_search = "{pspec["brev_search"]}"',
            f'min_vram_gb_per_gpu = {pspec["min_vram_per_gpu"]}',
            # No profile / requires_deployed_vss / prerequisite_deploy_mode:
            # nothing in the harness reads them (the _ensure_prerequisite_deployed
            # pre-deploy hook is gone). The spec's first expects[] query deploys
            # the alerts profile via /vss-deploy-profile in the trial's first turn.
            f"step_index = {idx}",
            f"step_count = {len(expects)}",
            f"check_count = {len(expect.get('checks') or [])}",
            "",
        ]
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
        spec_src = skill_dir / "evals" / spec_name
        if spec_src.exists():
            shutil.copy(spec_src, tests_dir / spec_name)
        else:
            # Fallback: write the in-memory spec so tests/ is complete
            (tests_dir / spec_name).write_text(json.dumps(spec, indent=2))

        # solution/
        solution_dir = step_dir / "solution"
        solution_dir.mkdir(exist_ok=True)
        (solution_dir / "solve.sh").write_text(generate_solve_script(platform))

        # skills/ — vss-query-analytics + deploy (so the agent can diagnose
        # / redeploy if VA-MCP is not live).
        for src, name in ((skill_dir, "vss-query-analytics"),
                          (deploy_skill_dir, "vss-deploy-profile")):
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
        help="Dataset output root (e.g. <scratch>/datasets/vss-query-analytics)",
    )
    parser.add_argument(
        "--skill-dir", required=True,
        help="Path to skills/vss-query-analytics",
    )
    parser.add_argument(
        "--deploy-skill-dir", default=None,
        help="Path to skills/vss-deploy-profile (optional — included for agent diagnosis)",
    )
    parser.add_argument(
        "--spec", default=None,
        help="Path to spec JSON (default: <skill-dir>/evals/query_analytics.json)",
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
    spec_path = (
        Path(args.spec)
        if args.spec
        else (skill_dir / "evals" / "query_analytics.json")
    )

    if not spec_path.exists():
        print(f"spec not found: {spec_path}", file=sys.stderr)
        sys.exit(1)
    spec = json.loads(spec_path.read_text())
    spec["_source_path"] = str(spec_path)

    profile = spec.get("profile", "alerts")
    platforms = [args.platform] if args.platform else _platforms_from_spec(spec)

    print("=== Inputs ===")
    print(f"  output_dir   : {output_root}")
    print(f"  skill_dir    : {skill_dir}")
    print(f"  spec         : {spec_path}")
    print(f"  profile      : {profile}  (dataset-path hint only; not emitted to task.toml)")
    print(f"  platforms    : {platforms}")
    print(f"  queries      : {len(spec.get('expects', []))}")
    print(f"  total checks : {sum(len(q.get('checks', [])) for q in spec.get('expects', []))}")
    print()
    for platform in platforms:
        task_id = PLATFORMS[platform]["short_name"]
        print(f"  GEN  vss-query-analytics/{profile}/{task_id}")
        generate_task(
            platform, profile, spec, output_root, skill_dir, deploy_skill_dir,
        )
    print()
    print(f"Generated {len(platforms)} platform(s) under {output_root}/{profile}/")
    print()
    print("Note: step-1's query deploys the alerts profile via /vss-deploy-profile")
    print("in the trial's first turn; steps 2+ query VA-MCP read-only. The harness")
    print("does not pre-deploy (the _ensure_prerequisite_deployed hook is gone).")


if __name__ == "__main__":
    main()
