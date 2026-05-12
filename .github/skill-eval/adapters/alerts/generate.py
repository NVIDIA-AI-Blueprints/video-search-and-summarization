#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Generate Harbor tasks for the alerts skill.

The alerts skill exercises the VSS **alerts** profile end-to-end:
deploy, onboard a sample video via NVStreamer, register the sensor in
VIOS, start a VLM real-time alert through the VSS Agent, and poll
VA-MCP for incidents.

The spec (`alerts_vlm_real_time.json`) declares:
    profile = "alerts"        → deploy step is prepended in the dataset
    resources.platforms:
        L40S: modes: [remote-all]

Because the alerts real-time (VLM) mode runs `rtvi-vlm` locally (a
continuous GPU-backed inference loop) alongside NVStreamer + VIOS +
Kafka, the trial requires a **GPU** even in `remote-all` placement.
The `remote-all` label refers to LLM/VLM NIM placement (both remote),
but RT-CV / `rtvi-vlm` is always local on the alerts profile.

Directory layout (one platform × mode per directory):

    datasets/alerts/<spec_stem>/<platform_short>-<mode>/
        step-1/
            instruction.md, task.toml, tests/, solution/, skills/, environment/
        step-2/
            ...
        step-3/
            ...
        step-4/
            ...

Usage:
    python3 generate.py \\
        --output-dir /tmp/skill-eval/datasets/alerts \\
        --skill-dir   skills/alerts \\
        --deploy-skill-dir skills/deploy \\
        --spec        skills/alerts/eval/alerts_vlm_real_time.json
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

GENERIC_JUDGE = Path(__file__).resolve().parents[2] / "verifiers" / "generic_judge.py"

# Prepended to every instruction.md so the skill's bypass clause fires.
PREAMBLE = (
    "You are running inside a non-interactive evaluation harness. "
    "You are pre-authorized to deploy prerequisites autonomously — "
    "do not pause to ask for confirmation on `/deploy` or any other "
    "setup action the trial requires."
)

# ---------------------------------------------------------------------------
# Platforms
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
}

DEFAULT_PLATFORM = "L40S"
DEFAULT_SPEC = "alerts_vlm_real_time.json"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SUBST_RE = re.compile(r"\{\{\s*(\w+)\s*\}\}")


def _substitute(value: object, subs: dict[str, str]) -> object:
    """Replace {{key}} placeholders in strings/lists/dicts."""
    if isinstance(value, str):
        return _SUBST_RE.sub(lambda m: str(subs.get(m.group(1), m.group(0))), value)
    if isinstance(value, list):
        return [_substitute(v, subs) for v in value]
    if isinstance(value, dict):
        return {k: _substitute(v, subs) for k, v in value.items()}
    return value


def _platform_modes(spec: dict, platform_filter: str | None) -> list[tuple[str, str]]:
    declared: dict = ((spec.get("resources") or {}).get("platforms") or {})
    tasks: list[tuple[str, str]] = []
    for platform, cfg in declared.items():
        if platform_filter and platform != platform_filter:
            continue
        if platform not in PLATFORMS:
            print(
                f"  WARN  unknown platform '{platform}' in spec — skipped",
                file=sys.stderr,
            )
            continue
        for mode in (cfg or {}).get("modes") or ["remote-all"]:
            tasks.append((platform, mode))
    return tasks or [(platform_filter or DEFAULT_PLATFORM, "remote-all")]


# ---------------------------------------------------------------------------
# Generation helpers
# ---------------------------------------------------------------------------

def _test_sh(step_idx: int, spec_name: str) -> str:
    return (
        "#!/bin/bash\n"
        f"# alerts verifier step {step_idx}: delegates to generic LLM-as-judge.\n"
        "set -uo pipefail\n"
        "\n"
        'TEST_DIR="$(cd "$(dirname "$0")" && pwd)"\n'
        "python3 -m pip install --quiet 'anthropic>=0.40.0' >/dev/null 2>&1 || true\n"
        "\n"
        'python3 "$TEST_DIR/generic_judge.py" \\\n'
        f'    --spec "$TEST_DIR/{spec_name}" --step {step_idx}\n'
        "exit 0\n"
    )


def _solve_sh(platform: str, mode: str) -> str:
    return (
        "#!/bin/bash\n"
        f"# Gold solution stub: alerts on {platform}/{mode}\n"
        "# The verifier drives the assertions directly.\n"
        "set -euo pipefail\n"
        "\n"
        "curl -sf --connect-timeout 5 http://localhost:8000/docs >/dev/null || {\n"
        "    echo 'VSS alerts stack is not deployed — cannot solve'\n"
        "    exit 1\n"
        "}\n"
        "echo 'VSS alerts stack is live — verifier will drive the queries.'\n"
    )


def _task_toml(
    *,
    platform: str,
    mode: str,
    profile: str,
    step_idx: int,
    step_count: int,
    check_count: int,
    pspec: dict,
    spec_stem: str,
    step_suffix: str,
    prerequisite_deploy_mode: str,
) -> str:
    short = pspec["short_name"]
    lines = [
        "[task]",
        f'name = "nvidia-vss/alerts-{spec_stem}-{short}-{mode}{step_suffix}"',
        f'description = "Alerts VLM real-time step {step_idx}/{step_count} on {platform}/{mode}"',
        f'keywords = ["alerts", "vlm", "real-time", "{platform}", "{mode}"]',
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
        'skill = "alerts"',
        f'profile = "{profile}"',
        f'platform = "{platform}"',
        f'mode = "{mode}"',
        f'gpu_type = "{pspec["gpu_type"]}"',
        f'brev_search = "{pspec["brev_search"]}"',
        # alerts real-time requires a GPU even in remote-all (rtvi-vlm runs
        # locally as a continuous VLM inference loop)
        "gpu_count = 1",
        f'min_vram_gb_per_gpu = {pspec["min_vram_per_gpu"]}',
        "min_root_disk_gb = 200",
        "requires_deployed_vss = true",
        f'prerequisite_deploy_mode = "{prerequisite_deploy_mode}"',
        f"step_index = {step_idx}",
        f"step_count = {step_count}",
        f"check_count = {check_count}",
        "",
    ]
    return "\n".join(lines)


def generate_platform_mode(
    *,
    platform: str,
    mode: str,
    spec: dict,
    rendered_spec: dict,
    output_root: Path,
    skill_dir: Path,
    deploy_skill_dir: Path | None,
    spec_stem: str,
) -> None:
    pspec = PLATFORMS[platform]
    short = pspec["short_name"]
    expects = rendered_spec.get("expects") or []
    profile: str = str(spec.get("profile", "alerts"))
    # prerequisite_deploy_mode drives the /deploy -m flag the coordinator
    # injects before this task. Read from spec, fall back to `real-time`.
    prerequisite_deploy_mode: str = str(
        spec.get("deploy_mode") or spec.get("prerequisite_deploy_mode") or "real-time"
    )

    platform_dir = output_root / spec_stem / f"{short}-{mode}"
    platform_dir.mkdir(parents=True, exist_ok=True)

    n = len(expects)
    for idx, expect in enumerate(expects, 1):
        step_dir = platform_dir / f"step-{idx}" if n > 1 else platform_dir
        step_dir.mkdir(parents=True, exist_ok=True)
        step_suffix = f"-step-{idx}" if n > 1 else ""

        # ---- instruction.md ------------------------------------------------
        # Per-step scoping: step 1 deploys (host is bare); steps 2+ run
        # against the already-deployed stack. We deliberately do NOT include
        # the spec's `env` field — it's a multi-step narrative ("deploy then
        # add then verify then poll") that, when pasted into a single step's
        # instruction, reads as a directive to run the whole chain in this
        # trial. Each step's `query` already carries the step-specific intent;
        # static host context lives in the leading paragraph below.
        if idx == 1:
            leading = [
                f"Use the `/alerts` skill (and `/deploy` as needed) on this bare `{platform}` host.",
                "Docker + NVIDIA Container Toolkit are available, `NGC_CLI_API_KEY` is set,",
                "and the remote LLM/VLM endpoints are configured via "
                "`LLM_REMOTE_URL` / `LLM_REMOTE_MODEL` / `VLM_REMOTE_URL` / `VLM_REMOTE_MODEL`.",
            ]
        else:
            leading = [
                f"Use the `/alerts` skill on this `{platform}` host.",
                "The VSS **alerts** profile is already deployed in **real-time (VLM)** mode "
                "with `remote-all` placement (deployed by step 1).",
            ]

        instruction_lines = [
            PREAMBLE,
            "",
            *leading,
            "",
            (f"## Query (step {idx} of {n})" if n > 1 else "## Query"),
            "",
            expect.get("query", ""),
            "",
        ]
        if n > 1:
            instruction_lines.append(
                f"Complete only step {idx} and stop. The remaining steps run "
                "as separate trials — do not pre-execute them in this one."
            )
            instruction_lines.append("")
        instruction_lines.append("Run autonomously without prompting for confirmation.")
        instruction_lines.append("")
        (step_dir / "instruction.md").write_text("\n".join(instruction_lines) + "\n")

        # ---- task.toml -----------------------------------------------------
        toml_content = _task_toml(
            platform=platform,
            mode=mode,
            profile=profile,
            step_idx=idx,
            step_count=len(expects),
            check_count=len(expect.get("checks") or []),
            pspec=pspec,
            spec_stem=spec_stem,
            step_suffix=step_suffix,
            prerequisite_deploy_mode=prerequisite_deploy_mode,
        )
        (step_dir / "task.toml").write_text(toml_content)

        # ---- environment/ --------------------------------------------------
        env_dir = step_dir / "environment"
        env_dir.mkdir(exist_ok=True)
        (env_dir / "Dockerfile").write_text("FROM scratch\n")

        # ---- tests/ --------------------------------------------------------
        tests_dir = step_dir / "tests"
        tests_dir.mkdir(exist_ok=True)
        spec_filename = f"{spec_stem}.json"
        (tests_dir / "test.sh").write_text(_test_sh(idx, spec_filename))
        if GENERIC_JUDGE.exists():
            shutil.copy(GENERIC_JUDGE, tests_dir / "generic_judge.py")
        # Write the rendered spec so the judge can read checks[] at verify time
        (tests_dir / spec_filename).write_text(json.dumps(rendered_spec, indent=2))

        # ---- solution/ -----------------------------------------------------
        solution_dir = step_dir / "solution"
        solution_dir.mkdir(exist_ok=True)
        (solution_dir / "solve.sh").write_text(_solve_sh(platform, mode))

        # ---- skills/ -------------------------------------------------------
        for src_dir, skill_name in [
            (skill_dir, "alerts"),
            (deploy_skill_dir, "deploy"),
        ]:
            if src_dir and src_dir.exists():
                dst = step_dir / "skills" / skill_name
                if dst.exists():
                    shutil.rmtree(dst)
                shutil.copytree(src_dir, dst)

    print(
        f"  GEN  alerts/{spec_stem}/{short}-{mode}  "
        f"({len(expects)} steps, "
        f"{sum(len(e.get('checks') or []) for e in expects)} checks)"
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--output-dir", required=True,
                        help="Dataset output root (e.g. /tmp/skill-eval/datasets/alerts)")
    parser.add_argument("--skill-dir", required=True,
                        help="Path to skills/alerts")
    parser.add_argument("--deploy-skill-dir", default=None,
                        help="Path to skills/deploy (included so agent can diagnose issues)")
    parser.add_argument("--spec", default=None,
                        help=f"Path to spec JSON (default: <skill-dir>/eval/{DEFAULT_SPEC})")
    parser.add_argument("--platform", default=None,
                        choices=list(PLATFORMS.keys()),
                        help="Generate for this platform only")
    args = parser.parse_args()

    output_root = Path(args.output_dir)
    skill_dir = Path(args.skill_dir)
    deploy_skill_dir = Path(args.deploy_skill_dir) if args.deploy_skill_dir else None
    spec_path = (
        Path(args.spec) if args.spec
        else (skill_dir / "eval" / DEFAULT_SPEC)
    )

    if not spec_path.exists():
        print(f"spec not found: {spec_path}", file=sys.stderr)
        sys.exit(1)

    spec: dict = json.loads(spec_path.read_text())
    spec["_source_path"] = str(spec_path)

    # Spec_stem = filename without .json
    spec_stem = spec_path.stem  # e.g. "alerts_vlm_real_time"

    # Substitute {{platform}} and {{mode}} at generation time using
    # the first platform/mode from the matrix as defaults; the adapter
    # renders per-task below.
    tasks = _platform_modes(spec, args.platform)

    print("=== Inputs ===")
    print(f"  output_dir       : {output_root}")
    print(f"  skill_dir        : {skill_dir}")
    print(f"  spec             : {spec_path}")
    print(f"  profile          : {spec.get('profile', '(none)')}")
    print(f"  deploy_mode      : {spec.get('deploy_mode', '(none — defaults to real-time)')}")
    print(f"  tasks            : {tasks}")
    print(f"  queries          : {len(spec.get('expects', []))}")
    print(
        f"  total checks     : "
        f"{sum(len(q.get('checks', [])) for q in spec.get('expects', []))}"
    )
    print()

    for platform, mode in tasks:
        subs = {"platform": platform, "mode": mode}
        rendered_spec = _substitute(spec, subs)
        generate_platform_mode(
            platform=platform,
            mode=mode,
            spec=spec,
            rendered_spec=rendered_spec,
            output_root=output_root,
            skill_dir=skill_dir,
            deploy_skill_dir=deploy_skill_dir,
            spec_stem=spec_stem,
        )

    print()
    print(f"Generated {len(tasks)} platform×mode combinations under {output_root}/")


if __name__ == "__main__":
    main()
