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

PLATFORMS: dict[str, dict] = {
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
    "H200": {
        "short_name":       "h200",
        "gpu_type":         "H200",
        "gpu_count":        1,
        "min_vram_per_gpu": 141,
        "brev_search":      "H200",
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
    runtime_deploy = bool(spec.get("runtime_deploy", True))
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
            PREAMBLE,
            "",
            f"Use the `/vss-build-vision-agent` skill for the "
            f"`{build_profile}` profile on `{platform}`. "
            "Work from `$HOME/video-search-and-summarization` (the VSS repository root).",
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
