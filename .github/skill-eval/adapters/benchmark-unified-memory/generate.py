#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Generate three Harbor setup tasks plus one four-turn task per VideoMME-v2 group."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import sys

SKILL_EVAL_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SKILL_EVAL_ROOT))

from benchmark.prompts import render_question_prompt  # noqa: E402
from benchmark.spec import load_benchmark_spec  # noqa: E402
from benchmark.video_mme_v2 import load_video_mme_v2  # noqa: E402

PREAMBLE = (
    "You are running inside a non-interactive evaluation harness. You are pre-authorized to deploy "
    "prerequisites autonomously — do not pause to ask for confirmation on `/vss-deploy-profile` or any "
    "other setup action the trial requires."
)
PLATFORMS = {
    "RTXPRO6000BW": {
        "short_name": "rtxpro6000bw",
        "gpu_type": "RTX PRO 6000",
        "min_vram_gb_per_gpu": 96,
        "brev_search": "RTX PRO",
    }
}


def _copy_skills(step_dir: Path, repo_root: Path, names: tuple[str, ...]) -> None:
    discovered: dict[str, Path] = {}
    for skill_md in (repo_root / "skills").rglob("SKILL.md"):
        name = skill_md.parent.name
        if name in discovered:
            raise ValueError(f"duplicate skill leaf name: {name}")
        discovered[name] = skill_md.parent
    for name in names:
        source = discovered.get(name)
        if source is None:
            raise ValueError(f"declared skill does not exist: {name}")
        shutil.copytree(source, step_dir / "skills" / name)


def _task_toml(
    *,
    name: str,
    description: str,
    platform: str,
    step_index: int,
    step_count: int,
    check_count: int,
) -> str:
    config = PLATFORMS[platform]
    return "\n".join(
        [
            "[task]",
            f'name = "nvidia-vss/{name}"',
            f'description = "{description}"',
            'keywords = ["benchmark-unified-memory", "video-mme-v2", "openclaw"]',
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
            'skill = "benchmark-unified-memory"',
            f'platform = "{platform}"',
            f'gpu_type = "{config["gpu_type"]}"',
            f'brev_search = "{config["brev_search"]}"',
            f'min_vram_gb_per_gpu = {config["min_vram_gb_per_gpu"]}',
            f"step_index = {step_index}",
            f"step_count = {step_count}",
            f"check_count = {check_count}",
        ]
    ) + "\n"


def _base_step(step_dir: Path, skills: tuple[str, ...], repo_root: Path) -> None:
    (step_dir / "environment").mkdir(parents=True)
    (step_dir / "environment" / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
    (step_dir / "tests").mkdir()
    (step_dir / "solution").mkdir()
    (step_dir / "solution" / "solve.sh").write_text("#!/bin/bash\nexit 0\n", encoding="utf-8")
    _copy_skills(step_dir, repo_root, skills)


def _setup_test_script(step: int, *, verify_videos: bool) -> str:
    deterministic = (
        'if ! uv run --project "$HOME/video-search-and-summarization/services/agent" --no-dev --extra cli --with pyarrow '
        'python "$TEST_DIR/verify_video_setup.py" --dataset "$TEST_DIR/dataset.parquet"; then\n'
        '  printf "0.0\\n" > /logs/verifier/reward.txt\n'
        '  printf \'{"passed":false,"rationale":"deterministic video setup verification failed"}\\n\' '
        '> /logs/verifier/judge.json\n'
        '  exit 0\n'
        'fi\n'
        if verify_videos else ""
    )
    return f'''#!/bin/bash
set -uo pipefail
TEST_DIR="$(cd "$(dirname "$0")" && pwd)"
{deterministic}python3 -m pip install --quiet 'anthropic>=0.40.0' >/dev/null 2>&1 || true
python3 "$TEST_DIR/generic_judge.py" --spec "$TEST_DIR/setup-spec.json" --step {step}
'''


def _group_instruction(group) -> str:  # noqa: ANN001
    envelope = {
        "kind": "unified-memory-group",
        "group_id": group.group_id,
        "turns": [
            {
                "case_id": case.task.case_id,
                "prompt": render_question_prompt(group, case, first=index == 0),
            }
            for index, case in enumerate(group.cases)
        ],
    }
    return f"{PREAMBLE}\n\n<!-- unified-memory-group\n{json.dumps(envelope)}\n-->\n"


def _resolve_input(skill_dir: Path, relative: str) -> Path:
    if Path(relative).is_absolute():
        raise ValueError(f"benchmark input path must be relative: {relative}")
    resolved = (skill_dir / relative).resolve()
    try:
        resolved.relative_to(skill_dir)
    except ValueError as exc:
        raise ValueError(f"benchmark input escapes skill directory: {relative}") from exc
    return resolved


def generate(spec_path: Path, skill_dir: Path, output_root: Path, platform: str) -> None:
    spec = load_benchmark_spec(spec_path)
    if platform not in spec.resources.platforms:
        raise ValueError(f"platform {platform} is not declared by the benchmark spec")
    repo_root = Path(__file__).resolve().parents[4]
    skill_dir = skill_dir.resolve()
    dataset_path = _resolve_input(skill_dir, spec.dataset.path)
    memory_dir = _resolve_input(skill_dir, spec.memory.directory)
    if not dataset_path.is_file():
        raise FileNotFoundError(dataset_path)
    if not memory_dir.is_dir():
        raise FileNotFoundError(memory_dir)
    dataset = load_video_mme_v2(dataset_path)
    if not dataset.groups:
        raise ValueError("benchmark dataset is empty")

    platform_dir = output_root / "benchmark" / PLATFORMS[platform]["short_name"]
    if platform_dir.exists():
        shutil.rmtree(platform_dir)
    total_steps = len(spec.setup) + len(dataset.groups)
    setup_spec = {
        "expects": [{"query": step.query, "checks": list(step.checks)} for step in spec.setup]
    }

    for index, setup in enumerate(spec.setup, 1):
        step_dir = platform_dir / f"step-{index}"
        _base_step(step_dir, spec.skills, repo_root)
        (step_dir / "instruction.md").write_text(f"{PREAMBLE}\n\n{setup.query}\n", encoding="utf-8")
        (step_dir / "task.toml").write_text(
            _task_toml(
                name=f"benchmark-unified-memory-setup-{index}-{PLATFORMS[platform]['short_name']}",
                description=setup.name,
                platform=platform,
                step_index=index,
                step_count=total_steps,
                check_count=len(setup.checks),
            ),
            encoding="utf-8",
        )
        tests = step_dir / "tests"
        (tests / "setup-spec.json").write_text(json.dumps(setup_spec, indent=2) + "\n", encoding="utf-8")
        shutil.copy(SKILL_EVAL_ROOT / "verifiers" / "generic_judge.py", tests)
        if index == 2:
            shutil.copy(dataset_path, tests / "dataset.parquet")
            shutil.copy(Path(__file__).with_name("verify_video_setup.py"), tests)
        (tests / "test.sh").write_text(_setup_test_script(index, verify_videos=index == 2), encoding="utf-8")

    for offset, group in enumerate(dataset.groups, 1):
        step_index = len(spec.setup) + offset
        step_dir = platform_dir / f"step-{step_index}"
        _base_step(step_dir, spec.skills, repo_root)
        (step_dir / "instruction.md").write_text(_group_instruction(group), encoding="utf-8")
        (step_dir / "task.toml").write_text(
            _task_toml(
                name=f"benchmark-unified-memory-{group.group_id}-{PLATFORMS[platform]['short_name']}",
                description=f"VideoMME-v2 group {group.group_id}",
                platform=platform,
                step_index=step_index,
                step_count=total_steps,
                check_count=1,
            ),
            encoding="utf-8",
        )
        tests = step_dir / "tests"
        group_data = {
            "group_id": group.group_id,
            "group_type": group.group_type.value,
            "group_structure": group.group_structure,
            "answers": {case.task.case_id: case.ground_truth.label for case in group.cases},
            "minimum": spec.scoring.minimum,
            "final_group": offset == len(dataset.groups),
            "expected_group_ids": [item.group_id for item in dataset.groups],
        }
        (tests / "group.json").write_text(json.dumps(group_data, indent=2) + "\n", encoding="utf-8")
        shutil.copy(Path(__file__).with_name("verify_group.py"), tests)
        shutil.copytree(SKILL_EVAL_ROOT / "benchmark", tests / "benchmark")
        (tests / "test.sh").write_text(
            '#!/bin/bash\nset -uo pipefail\nTEST_DIR="$(cd "$(dirname "$0")" && pwd)"\n'
            'uv run --python 3.12 python "$TEST_DIR/verify_group.py" '
            '--group "$TEST_DIR/group.json"\n',
            encoding="utf-8",
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--skill-dir", required=True, type=Path)
    parser.add_argument("--spec", required=True, type=Path)
    parser.add_argument("--platform", choices=tuple(PLATFORMS), default="RTXPRO6000BW")
    args = parser.parse_args()
    generate(args.spec, args.skill_dir, args.output_dir, args.platform)


if __name__ == "__main__":
    main()
