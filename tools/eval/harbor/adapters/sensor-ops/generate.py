#!/usr/bin/env python3
"""Generate Harbor tasks for the sensor-ops skill.

Sensor-ops exercises VIOS (VST) API calls — upload video, extract
snapshot URL, extract clip URL, etc. — against a **pre-deployed VSS
base profile**. It does NOT deploy VSS itself.

## Harbor chaining / dependencies

Harbor has no native mechanism to express inter-task dependencies
(`TaskConfig` lacks a `depends_on` / `prerequisites` field). Each
task is independent — Harbor runs exactly one task per trial on a
clean environment.

Chaining a sensor-ops trial *after* a deploy trial is a
**coordinator-level** concern: the coordinator arranges that the
target Brev instance already has VSS running before dispatching the
sensor-ops trial. Our eval plan already models this with
`execution_groups[<id>].queue_order` (sequential tasks on the same
instance share state).

To chain: in the run plan, put deploy tasks first in a group's queue,
then sensor-ops tasks for the same platform. Each sensor-ops task's
`task.toml [metadata]` records `requires_deployed_vss=true` so a
validator can refuse to dispatch it in isolation.

## Directory layout

    datasets/sensor-ops/base/<platform>/
        task.toml
        instruction.md
        tests/test.sh
        tests/test_base_profile_ops.py    (copied from skill)
        solution/solve.sh
        skills/sensor-ops/                (full skill copy)
        environment/Dockerfile            (FROM scratch; BrevEnvironment takes over)

One task per platform. All platforms share the same verifier — only
the `gpu_type` / `brev_search` / resource hints in task.toml differ,
matching the deploy-adapter convention.

Usage:
    python3 generate.py --output-dir ../../datasets/sensor-ops \\
        --skill-dir ../../../../../skills/sensor-ops \\
        --deploy-skill-dir ../../../../../skills/deploy \\
        --video-url https://videos.pexels.com/video-files/6079421/6079421-sd_640_360_24fps.mp4
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Platforms — mirrors the deploy adapter so sensor-ops runs on the same hosts
# ---------------------------------------------------------------------------

PLATFORMS: dict[str, dict] = {
    "H100":          {"short_name": "h100",          "gpu_type": "H100",         "min_vram_per_gpu": 80, "brev_search": "H100"},
    "L40S":          {"short_name": "l40s",          "gpu_type": "L40S",         "min_vram_per_gpu": 48, "brev_search": "L40S"},
    "RTXPRO6000BW":  {"short_name": "rtxpro6000bw",  "gpu_type": "RTX PRO 6000", "min_vram_per_gpu": 96, "brev_search": "RTX PRO"},
    "DGX-SPARK":     {"short_name": "spark",         "gpu_type": "GB10",         "min_vram_per_gpu": 96, "brev_search": "GB10"},
    "IGX-THOR":      {"short_name": "thor",          "gpu_type": "Thor",         "min_vram_per_gpu": 64, "brev_search": "Thor"},
}

DEFAULT_VIDEO_URL = (
    "https://videos.pexels.com/video-files/6079421/6079421-sd_640_360_24fps.mp4"
)
DEFAULT_VIDEO_NAME = "warehouse_forklift_pexels_6079421"


# ---------------------------------------------------------------------------
# Generation
# ---------------------------------------------------------------------------

def generate_instruction(spec: dict, platform: str, video_url: str) -> str:
    """Instruction tells the agent to run through the queries using
    the /sensor-ops skill, one by one, against the already-deployed VSS."""
    queries = spec.get("expects", [])
    lines = [
        "Use the `/sensor-ops` skill to drive VIOS API calls against the "
        f"VSS base profile already deployed on this `{platform}` host.",
        "",
        "**Prerequisite:** VSS base profile must be running (VST reachable "
        "at `http://localhost:30888/vst/api/v1/sensor/version`). If it's "
        "not, fail early and report — do NOT deploy VSS yourself.",
        "",
        f"The test video is available at: `{video_url}`  ",
        "Download it to `/tmp/` first if it's not already local.",
        "",
        "## Queries to run (sequentially)",
        "",
    ]
    for i, q in enumerate(queries, 1):
        lines.append(f"### Query {i}")
        lines.append("")
        lines.append(q.get("query", ""))
        lines.append("")
        lines.append("Expected outcome (the verifier will check these "
                     "independently — do your best to satisfy them via the "
                     "skill):")
        for c in q.get("checks", []):
            lines.append(f"- {c}")
        lines.append("")
    lines += [
        "## Environment notes",
        "",
        spec.get("env", ""),
        "",
        "Run autonomously without prompting for confirmation.",
        "",
    ]
    return "\n".join(lines) + "\n"


def generate_test_script(spec: dict) -> str:
    """Shell wrapper that invokes test_base_profile_ops.py and maps its
    per-check PASS/FAIL lines into the reward.txt tally Harbor expects."""
    total_checks_hint = sum(len(q.get("checks", []))
                            for q in spec.get("expects", []))
    lines = [
        "#!/bin/bash",
        "# sensor-ops verifier: runs the VIOS queries from the skill's",
        "# base_profile_ops.json and tallies per-check PASS/FAIL results.",
        "set -uo pipefail",
        "",
        "mkdir -p /logs/verifier",
        "",
        "TEST_DIR=\"$(cd \"$(dirname \"$0\")\" && pwd)\"",
        'PROBE="$TEST_DIR/test_base_profile_ops.py"',
        "",
        "# Resolve the warehouse video: reuse the download that the deploy",
        "# skill's test_base.py caches at /tmp/vss_test_videos/, otherwise",
        "# pull it ourselves.",
        'VIDEO="/tmp/vss_test_videos/' + DEFAULT_VIDEO_NAME + '.mp4"',
        'if [ ! -f "$VIDEO" ]; then',
        f'    mkdir -p /tmp/vss_test_videos',
        f'    curl -sfL -o "$VIDEO" "{DEFAULT_VIDEO_URL}" '
        f'|| echo "WARN: could not fetch sample video"',
        "fi",
        "",
        "python3 -m pip install --quiet urllib3 2>/dev/null || true",
        "",
        "# Capture probe output so we can tally PASS/FAIL lines.",
        'OUT="/logs/verifier/sensor_ops_probe.out"',
        'python3 "$PROBE" \\',
        '    --vst-url "${VST_URL:-http://localhost:30888}" \\',
        '    --video-path "$VIDEO" \\',
        '    --brev-link-prefix "${BREV_LINK_PREFIX:-}" \\',
        '    --brev-env-id "${BREV_ENV_ID:-}" | tee "$OUT"',
        'PROBE_RC=${PIPESTATUS[0]}',
        "",
        "# Tally from the probe output (PASS: / FAIL: prefixes).",
        'PASS=$(grep -c "^PASS: " "$OUT" 2>/dev/null || echo 0)',
        'FAIL=$(grep -c "^FAIL: " "$OUT" 2>/dev/null || echo 0)',
        'TOTAL=$((PASS + FAIL))',
        "",
        'echo ""',
        'echo "=== Results: $PASS passed, $FAIL failed (of $TOTAL) ==="',
        'echo "  probe exit code: $PROBE_RC"',
        f'echo "  expected check count from spec: ~{total_checks_hint}"',
        "",
        'if [ "$TOTAL" -gt 0 ]; then',
        '    python3 -c "print($PASS / $TOTAL)" > /logs/verifier/reward.txt \\',
        "        || echo 0 > /logs/verifier/reward.txt",
        "else",
        "    echo 0 > /logs/verifier/reward.txt",
        "fi",
        "",
        "exit 0",
    ]
    return "\n".join(lines) + "\n"


def generate_solve_script(platform: str) -> str:
    """Gold solution — assumes VSS is already deployed; the oracle just
    re-runs the verifier (there's no separate 'solve' action for a
    probe-style task since the agent's job is driving the API, which
    the verifier does independently)."""
    return (
        "#!/bin/bash\n"
        f"# Gold solution: sensor-ops on {platform}\n"
        "# The verifier drives the VIOS queries directly — the solution\n"
        "# script simply asserts VSS is live, then defers to the verifier.\n"
        "set -euo pipefail\n"
        "\n"
        "curl -sf --connect-timeout 5 "
        "${VST_URL:-http://localhost:30888}/vst/api/v1/sensor/version "
        ">/dev/null || {\n"
        "    echo 'VSS is not deployed — cannot solve sensor-ops task'\n"
        "    exit 1\n"
        "}\n"
        "echo 'VSS is live — verifier will drive the queries.'\n"
    )


def generate_task(platform: str, spec: dict, output_root: Path,
                  skill_dir: Path, deploy_skill_dir: Path | None,
                  video_url: str) -> None:
    pspec = PLATFORMS[platform]
    task_id = pspec["short_name"]
    task_dir = output_root / "base" / task_id
    task_dir.mkdir(parents=True, exist_ok=True)

    # instruction.md
    (task_dir / "instruction.md").write_text(
        generate_instruction(spec, platform, video_url)
    )

    # task.toml
    n_queries = len(spec.get("expects", []))
    n_checks = sum(len(q.get("checks", [])) for q in spec.get("expects", []))
    meta_lines = [
        "[task]",
        f'name = "nvidia-vss/sensor-ops-base-{task_id}"',
        f'description = "Sensor-ops (VIOS) queries against a deployed VSS base on {platform}"',
        f'keywords = ["sensor-ops", "vios", "base", "{platform}"]',
        "",
        "[environment]",
        'skills_dir = "/skills"',
        "",
        "[metadata]",
        'skill = "sensor-ops"',
        'profile = "base"',
        f'platform = "{platform}"',
        f'gpu_type = "{pspec["gpu_type"]}"',
        f'brev_search = "{pspec["brev_search"]}"',
        f'min_vram_gb_per_gpu = {pspec["min_vram_per_gpu"]}',
        "# The sensor-ops task assumes VSS base is already deployed on the",
        "# target host. Harbor doesn't express dependencies natively — the",
        "# coordinator must run a deploy task first on the same Brev",
        "# instance (same group, earlier in queue_order).",
        "requires_deployed_vss = true",
        f'query_count = {n_queries}',
        f'check_count = {n_checks}',
        "",
    ]
    (task_dir / "task.toml").write_text("\n".join(meta_lines))

    # environment/
    env_dir = task_dir / "environment"
    env_dir.mkdir(exist_ok=True)
    (env_dir / "Dockerfile").write_text("FROM scratch\n")

    # tests/
    tests_dir = task_dir / "tests"
    tests_dir.mkdir(exist_ok=True)
    (tests_dir / "test.sh").write_text(generate_test_script(spec))
    probe_src = skill_dir / "scripts" / "test_base_profile_ops.py"
    if probe_src.exists():
        shutil.copy(probe_src, tests_dir / "test_base_profile_ops.py")
    spec_src = skill_dir / "eval" / "base_profile_ops.json"
    if spec_src.exists():
        shutil.copy(spec_src, tests_dir / "base_profile_ops.json")

    # solution/
    solution_dir = task_dir / "solution"
    solution_dir.mkdir(exist_ok=True)
    (solution_dir / "solve.sh").write_text(generate_solve_script(platform))

    # skills/ — copy sensor-ops + deploy so agent has the deploy skill
    # available to diagnose if VSS isn't running.
    for src, name in ((skill_dir, "sensor-ops"), (deploy_skill_dir, "deploy")):
        if src and src.exists():
            dst = task_dir / "skills" / name
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
                        help="Dataset output root (e.g. tools/eval/harbor/datasets/sensor-ops)")
    parser.add_argument("--skill-dir", required=True,
                        help="Path to skills/sensor-ops")
    parser.add_argument("--deploy-skill-dir", default=None,
                        help="Path to skills/deploy (optional — included for agent debug)")
    parser.add_argument("--spec", default=None,
                        help="Path to base_profile_ops.json "
                             "(default: <skill-dir>/eval/base_profile_ops.json)")
    parser.add_argument("--platform", default=None,
                        choices=list(PLATFORMS.keys()),
                        help="Generate only for this platform (default: all)")
    parser.add_argument("--video-url", default=DEFAULT_VIDEO_URL,
                        help="Public .mp4 URL used by the verifier "
                             "(default: Pexels warehouse forklift)")
    args = parser.parse_args()

    output_root = Path(args.output_dir)
    skill_dir = Path(args.skill_dir)
    deploy_skill_dir = Path(args.deploy_skill_dir) if args.deploy_skill_dir else None
    spec_path = Path(args.spec) if args.spec else (skill_dir / "eval" / "base_profile_ops.json")

    if not spec_path.exists():
        print(f"spec not found: {spec_path}", file=sys.stderr)
        sys.exit(1)
    spec = json.loads(spec_path.read_text())

    platforms = [args.platform] if args.platform else list(PLATFORMS.keys())
    print(f"=== Inputs ===")
    print(f"  output_dir   : {output_root}")
    print(f"  skill_dir    : {skill_dir}")
    print(f"  spec         : {spec_path}")
    print(f"  video_url    : {args.video_url}")
    print(f"  platforms    : {platforms}")
    print(f"  queries      : {len(spec.get('expects', []))}")
    print(f"  total checks : {sum(len(q.get('checks', [])) for q in spec.get('expects', []))}")
    print()
    for platform in platforms:
        task_id = PLATFORMS[platform]["short_name"]
        print(f"  GEN  sensor-ops/base/{task_id}")
        generate_task(platform, spec, output_root, skill_dir,
                      deploy_skill_dir, args.video_url)
    print()
    print(f"Generated {len(platforms)} task(s) under {output_root}/base/")
    print()
    print("Note: these tasks assume VSS base is already deployed on the target")
    print("Brev instance. Chain them after a deploy task in the same")
    print("execution_group's queue_order (see tools/eval/harbor/plans/).")


if __name__ == "__main__":
    main()
