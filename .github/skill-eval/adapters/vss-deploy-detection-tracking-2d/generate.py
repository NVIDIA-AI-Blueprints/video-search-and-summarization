#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Generate Harbor tasks for the vss-deploy-detection-tracking-2d skill.

The vss-deploy-detection-tracking-2d skill exercises the RTVI-CV perception
microservice. It is standalone — there is no `/vss-deploy-profile`
prerequisite; the skill launches its own `rtvicv-perception-docker`
container via `docker run` from a user-supplied RTVI-CV image.

Two specs ship with the skill:
  - eval/deploy-evals.json — DEPLOY / TEARDOWN flow (no prereq)
  - eval/usage-evals.json  — REST API flow against an already-running
                             container (the coordinator must chain a
                             DEPLOY trial first in the same execution
                             group)

Both specs target `L40S` with mode `standalone`. Each spec's `expects`
list contains multiple steps; the adapter emits a `step-<N>/` subdir
per step so Harbor's dispatch loop runs them in order with
skip-on-prior-fail.

Usage from the repository root:
    python3 .github/skill-eval/adapters/vss-deploy-detection-tracking-2d/generate.py \\
        --output-dir .github/skill-eval/datasets/vss-deploy-detection-tracking-2d \\
        --skill-dir skills/vss-deploy-detection-tracking-2d \\
        --spec skills/vss-deploy-detection-tracking-2d/eval/deploy-evals.json
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from pathlib import Path

PLATFORMS: dict[str, dict] = {
    "H100": {"short_name": "h100", "gpu_type": "H100", "min_vram_per_gpu": 80, "brev_search": "H100"},
    "L40S": {"short_name": "l40s", "gpu_type": "L40S", "min_vram_per_gpu": 48, "brev_search": "L40S"},
    "RTXPRO6000BW": {
        "short_name": "rtxpro6000bw",
        "gpu_type": "RTX PRO 6000",
        "min_vram_per_gpu": 96,
        "brev_search": "RTX PRO",
    },
    "DGX-SPARK": {"short_name": "spark", "gpu_type": "GB10", "min_vram_per_gpu": 96, "brev_search": "GB10"},
    "IGX-THOR": {"short_name": "thor", "gpu_type": "Thor", "min_vram_per_gpu": 64, "brev_search": "Thor"},
}

DEFAULT_PLATFORM = "L40S"
DEFAULT_MODE = "standalone"
DEFAULT_SPEC = "deploy-evals.json"

GENERIC_JUDGE = Path(__file__).resolve().parents[2] / "verifiers" / "generic_judge.py"

PREAMBLE = (
    "You are running inside a non-interactive evaluation harness. "
    "You are pre-authorized to run required setup actions autonomously — "
    "do not pause to ask for confirmation on setup actions the trial requires."
)


def _substitute_spec(spec: dict, platform: str, mode: str) -> dict:
    substitutions = {
        "platform": platform,
        "mode": mode,
        "repo_root": "$HOME/video-search-and-summarization",
    }
    pattern = re.compile(r"\{\{\s*(\w+)\s*\}\}")

    def _sub(value):
        if isinstance(value, str):
            return pattern.sub(lambda m: str(substitutions.get(m.group(1), m.group(0))), value)
        if isinstance(value, list):
            return [_sub(v) for v in value]
        if isinstance(value, dict):
            return {k: _sub(v) for k, v in value.items()}
        return value

    return _sub(spec)


def _platform_modes_from_spec(spec: dict, platform_filter: str | None) -> list[tuple[str, str]]:
    declared = ((spec.get("resources") or {}).get("platforms") or {})
    if not declared:
        declared = {DEFAULT_PLATFORM: {"modes": [DEFAULT_MODE]}}

    tasks: list[tuple[str, str]] = []
    for platform, cfg in declared.items():
        if platform_filter and platform != platform_filter:
            continue
        if platform not in PLATFORMS:
            continue
        for mode in (cfg or {}).get("modes") or [DEFAULT_MODE]:
            tasks.append((platform, mode))
    return tasks or [(platform_filter or DEFAULT_PLATFORM, DEFAULT_MODE)]


def _spec_kind(spec_path: Path) -> str:
    name = spec_path.name.lower()
    if "usage" in name:
        return "usage"
    return "deploy"


def _instruction_intro(kind: str, platform: str) -> str:
    if kind == "usage":
        return (
            f"Use the `/vss-deploy-detection-tracking-2d` skill against the RTVI-CV "
            f"container already running on this `{platform}` host "
            "(`http://localhost:9000/api/v1` must respond). The eval harness "
            "started that container in an earlier trial; do not redeploy it, "
            "invoke `/vss-deploy-profile`, or call `scripts/dev-profile.sh`.\n"
            "\n"
            "### MANDATORY container-alive precheck — run this Bash command FIRST, before reading the query\n"
            "\n"
            "```bash\n"
            "if ! curl -sf --max-time 3 http://localhost:9000/api/v1/live >/dev/null 2>&1; then\n"
            "  docker start rtvicv-perception-docker >/dev/null 2>&1 \\\n"
            "    || docker restart rtvicv-perception-docker >/dev/null 2>&1 \\\n"
            "    || true\n"
            "  for i in $(seq 1 60); do\n"
            "    curl -sf --max-time 2 http://localhost:9000/api/v1/ready >/dev/null 2>&1 && break\n"
            "    sleep 1\n"
            "  done\n"
            "fi\n"
            "```\n"
            "\n"
            "This precheck is idempotent and no-ops when the container is already healthy. "
            "Run it before EVERY query in this trial — a prior `fakesink` deploy may have "
            "let the DeepStream pipeline EOF at the end of its source list, exiting the "
            "container. The precheck restarts it cheaply. If after 60 s the API is still "
            "unreachable, report the failure and stop — do NOT redeploy."
        )
    return (
        f"Use the `/vss-deploy-detection-tracking-2d` skill on this `{platform}` host "
        "to deploy or tear down the RTVI-CV perception microservice via the skill's "
        "own `docker run` flow against a user-supplied RTVI-CV image. Do not invoke "
        "`/vss-deploy-profile`, `scripts/dev-profile.sh`, or any full VSS profile."
    )


def generate_test_script(step: int, spec_name: str) -> str:
    return (
        "#!/bin/bash\n"
        f"# vss-deploy-detection-tracking-2d verifier (step {step}): delegates to the\n"
        "# generic LLM-as-judge (.github/skill-eval/verifiers/generic_judge.py).\n"
        "set -uo pipefail\n"
        "\n"
        'TEST_DIR="$(cd "$(dirname "$0")" && pwd)"\n'
        "python3 -m pip install --quiet 'anthropic>=0.40.0' >/dev/null 2>&1 || true\n"
        "\n"
        'python3 "$TEST_DIR/generic_judge.py" \\\n'
        f'    --spec "$TEST_DIR/{spec_name}" --step {step}\n'
        "exit 0\n"
    )


def generate_solve_script(platform: str, kind: str) -> str:
    if kind == "usage":
        return (
            "#!/bin/bash\n"
            f"# Gold solution: vss-deploy-detection-tracking-2d (usage) on {platform}\n"
            "set -euo pipefail\n"
            "\n"
            "curl -sf --connect-timeout 5 "
            "http://localhost:9000/api/v1/ready >/dev/null || {\n"
            "    echo 'RTVI-CV is not ready — cannot solve usage task'\n"
            "    exit 1\n"
            "}\n"
            "echo 'RTVI-CV is live — verifier will drive the API assertions.'\n"
        )
    return (
        "#!/bin/bash\n"
        f"# Gold solution: vss-deploy-detection-tracking-2d (deploy) on {platform}\n"
        "# The verifier judges the agent's deploy actions against the spec's checks;\n"
        "# the solver simply asserts the container is reachable after the agent runs.\n"
        "set -euo pipefail\n"
        "\n"
        "curl -sf --connect-timeout 5 "
        "http://localhost:9000/api/v1/ready >/dev/null \\\n"
        "    && echo 'RTVI-CV is reachable — deploy succeeded.' \\\n"
        "    || echo 'RTVI-CV not reachable — verifier will report the gap.'\n"
    )


def generate_task(
    platform: str,
    mode: str,
    spec: dict,
    spec_path: Path,
    output_root: Path,
    skill_dir: Path,
) -> None:
    pspec = PLATFORMS[platform]
    platform_short = pspec["short_name"]
    expects = spec.get("expects") or []
    spec_name = spec_path.name
    kind = _spec_kind(spec_path)
    rendered_spec = _substitute_spec(spec, platform, mode)
    dataset_group = kind

    for idx, expect in enumerate(rendered_spec.get("expects") or [], 1):
        step_dir = output_root / dataset_group / f"{platform_short}-{mode}"
        if len(expects) > 1:
            step_dir = step_dir / f"step-{idx}"
        step_dir.mkdir(parents=True, exist_ok=True)

        instruction = [
            PREAMBLE,
            "",
            _instruction_intro(kind, platform),
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
        (step_dir / "instruction.md").write_text("\n".join(instruction) + "\n")

        step_suffix = f"-step-{idx}" if len(expects) > 1 else ""
        meta_lines = [
            "[task]",
            f'name = "nvidia-vss/vss-deploy-detection-tracking-2d-{dataset_group}-{platform_short}-{mode}{step_suffix}"',
            f'description = "RTVI-CV {kind} query {idx}/{len(expects)} on {platform}/{mode}"',
            f'keywords = ["vss-deploy-detection-tracking-2d", "rtvi-cv", "{dataset_group}", "{platform}", "{mode}"]',
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
            'skill = "vss-deploy-detection-tracking-2d"',
            f'deployment = "{kind}"',
            f'platform = "{platform}"',
            f'mode = "{mode}"',
            f'gpu_type = "{pspec["gpu_type"]}"',
            f'brev_search = "{pspec["brev_search"]}"',
            "gpu_count = 1",
            f'min_vram_gb_per_gpu = {pspec["min_vram_per_gpu"]}',
            "min_root_disk_gb = 60",
            f"step_index = {idx}",
            f"step_count = {len(expects)}",
            f"check_count = {len(expect.get('checks') or [])}",
        ]
        if kind == "usage":
            meta_lines.append("requires_running_rtvicv = true")
        meta_lines.append("")
        (step_dir / "task.toml").write_text("\n".join(meta_lines))

        env_dir = step_dir / "environment"
        env_dir.mkdir(exist_ok=True)
        (env_dir / "Dockerfile").write_text("FROM scratch\n")

        tests_dir = step_dir / "tests"
        tests_dir.mkdir(exist_ok=True)
        (tests_dir / "test.sh").write_text(generate_test_script(idx, spec_name))
        if GENERIC_JUDGE.exists():
            shutil.copy(GENERIC_JUDGE, tests_dir / "generic_judge.py")
        (tests_dir / spec_name).write_text(json.dumps(rendered_spec, indent=2))

        solution_dir = step_dir / "solution"
        solution_dir.mkdir(exist_ok=True)
        (solution_dir / "solve.sh").write_text(generate_solve_script(platform, kind))

        dst = step_dir / "skills" / "vss-deploy-detection-tracking-2d"
        if dst.exists():
            shutil.rmtree(dst)
        if skill_dir.exists():
            # Selective copy: SKILL.md + scripts/ only. Omit references/ and
            # eval/ so the per-step payload stays under brev exec's 128 KB
            # MAX_ARG_STRLEN limit (upload encodes the tarball as a single
            # shell argument). The agent drives scripts/ directly; references
            # are explanatory and not needed during automated eval runs.
            dst.mkdir(parents=True, exist_ok=True)
            skill_md = skill_dir / "SKILL.md"
            if skill_md.exists():
                shutil.copy2(skill_md, dst / "SKILL.md")
            scripts_src = skill_dir / "scripts"
            if scripts_src.exists():
                shutil.copytree(scripts_src, dst / "scripts")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--skill-dir", required=True)
    parser.add_argument(
        "--spec",
        default=None,
        help=f"Path to spec file (default: <skill-dir>/eval/{DEFAULT_SPEC})",
    )
    parser.add_argument("--platform", default=None, choices=list(PLATFORMS.keys()))
    args = parser.parse_args()

    output_root = Path(args.output_dir)
    skill_dir = Path(args.skill_dir)
    spec_path = Path(args.spec) if args.spec else (skill_dir / "eval" / DEFAULT_SPEC)

    if not spec_path.exists():
        print(f"spec not found: {spec_path}", file=sys.stderr)
        sys.exit(1)

    spec = json.loads(spec_path.read_text())
    spec["_source_path"] = str(spec_path)
    tasks = _platform_modes_from_spec(spec, args.platform)
    kind = _spec_kind(spec_path)

    print("=== Inputs ===")
    print(f"  output_dir   : {output_root}")
    print(f"  skill_dir    : {skill_dir}")
    print(f"  spec         : {spec_path}")
    print(f"  spec kind    : {kind}")
    print(f"  tasks        : {tasks}")
    print(f"  queries      : {len(spec.get('expects', []))}")
    print(f"  total checks : {sum(len(q.get('checks', [])) for q in spec.get('expects', []))}")
    print()

    for platform, mode in tasks:
        print(
            f"  GEN  vss-deploy-detection-tracking-2d/{kind}/"
            f"{PLATFORMS[platform]['short_name']}-{mode}"
        )
        generate_task(platform, mode, spec, spec_path, output_root, skill_dir)

    print()
    print(f"Generated {len(tasks)} task(s) under {output_root}/{kind}/")


if __name__ == "__main__":
    main()
