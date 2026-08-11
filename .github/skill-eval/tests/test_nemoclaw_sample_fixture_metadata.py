# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Regression tests for explicit NemoClaw sample-fixture task metadata."""

from __future__ import annotations

import importlib.util
import json
import tomllib
from dataclasses import dataclass
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[3]
STAGED_SAMPLE_ROOT = Path("/tmp/vss-sample-data/dev-profile-sample-data")


@dataclass(frozen=True)
class FixtureCase:
    skill: str
    spec_name: str
    platform: str
    profile: str
    output_parts: tuple[str, ...]
    expected_step_count: int
    expected_samples: dict[int, list[str]]


CASES = (
    FixtureCase(
        skill="vss-ask-video",
        spec_name="base_profile_video_understanding.json",
        platform="L40S",
        profile="base",
        output_parts=("base", "l40s"),
        expected_step_count=5,
        expected_samples={2: ["warehouse_safety_0001.mp4"]},
    ),
    FixtureCase(
        skill="vss-generate-video-report",
        spec_name="base_profile_report.json",
        platform="RTXPRO6000BW",
        profile="base",
        output_parts=("base", "rtxpro6000bw"),
        expected_step_count=8,
        expected_samples={2: ["warehouse_safety_0001.mp4"]},
    ),
    FixtureCase(
        skill="vss-manage-alerts",
        spec_name="alerts_vlm_real_time.json",
        platform="L40S",
        profile="alerts",
        output_parts=("alerts_vlm_real_time", "l40s-remote-all"),
        expected_step_count=4,
        expected_samples={2: ["warehouse_sample.mp4"]},
    ),
)


def _load_adapter(skill: str):
    adapter_path = (
        REPO_ROOT / ".github/skill-eval/adapters" / skill / "generate.py"
    )
    module_name = skill.replace("-", "_") + "_fixture_adapter"
    spec = importlib.util.spec_from_file_location(module_name, adapter_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _stub_skill_dirs(root: Path, declared_skills: list[str]) -> dict[str, Path]:
    names = set(declared_skills)
    names.update(
        {
            "vss-deploy-profile",
            "vss-manage-video-io-storage",
            "vss-query-analytics",
        }
    )
    paths: dict[str, Path] = {}
    for name in names:
        skill_dir = root / name
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(f"# {name}\n", encoding="utf-8")
        paths[name] = skill_dir
    return paths


def _generate_dataset(
    case: FixtureCase,
    adapter,
    spec: dict,
    skill_dirs: dict[str, Path],
    output_root: Path,
) -> Path:
    primary = skill_dirs[case.skill]
    deploy = skill_dirs["vss-deploy-profile"]
    video_io = skill_dirs["vss-manage-video-io-storage"]

    if case.skill == "vss-manage-alerts":
        rendered_spec = adapter._substitute(
            spec,
            {"platform": case.platform, "mode": "remote-all"},
        )
        adapter.generate_platform_mode(
            platform=case.platform,
            mode="remote-all",
            spec=spec,
            rendered_spec=rendered_spec,
            output_root=output_root,
            skill_dir=primary,
            deploy_skill_dir=deploy,
            spec_stem=Path(case.spec_name).stem,
        )
    elif case.skill == "vss-generate-video-report":
        adapter.generate_task(
            case.platform,
            case.profile,
            spec,
            output_root,
            primary,
            deploy,
            video_io,
            skill_dirs["vss-query-analytics"],
        )
    else:
        adapter.generate_task(
            case.platform,
            case.profile,
            spec,
            output_root,
            primary,
            deploy,
            video_io,
        )

    return output_root.joinpath(*case.output_parts)


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.skill)
def test_generated_tasks_stage_only_explicit_nemoclaw_samples(
    case: FixtureCase,
    tmp_path: Path,
) -> None:
    """Each generated task must mirror only its own explicit spec metadata."""
    spec_path = REPO_ROOT / "skills" / case.skill / "evals" / case.spec_name
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    declared_samples = {
        index: expect["nemoclaw_sample_files"]
        for index, expect in enumerate(spec["expects"], 1)
        if "nemoclaw_sample_files" in expect
    }
    assert declared_samples == case.expected_samples

    spec["_source_path"] = str(spec_path)
    adapter = _load_adapter(case.skill)
    skill_dirs = _stub_skill_dirs(tmp_path / "skills", spec["skills"])
    generated_root = _generate_dataset(
        case,
        adapter,
        spec,
        skill_dirs,
        tmp_path / "datasets",
    )
    step_dirs = sorted(
        generated_root.glob("step-*"),
        key=lambda path: int(path.name.removeprefix("step-")),
    )
    assert len(step_dirs) == case.expected_step_count

    generated_samples: dict[int, list[str]] = {}
    for index, step_dir in enumerate(step_dirs, 1):
        metadata = tomllib.loads(
            (step_dir / "task.toml").read_text(encoding="utf-8")
        )["metadata"]
        if "nemoclaw_sample_files" in metadata:
            generated_samples[index] = metadata["nemoclaw_sample_files"]

    assert generated_samples == case.expected_samples


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.skill)
def test_explicit_fixture_steps_name_the_exact_staged_source(
    case: FixtureCase,
    tmp_path: Path,
) -> None:
    """Fixture-bearing prompts must use the trusted staged /tmp source."""
    spec_path = REPO_ROOT / "skills" / case.skill / "evals" / case.spec_name
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    spec["_source_path"] = str(spec_path)
    adapter = _load_adapter(case.skill)
    skill_dirs = _stub_skill_dirs(tmp_path / "skills", spec["skills"])
    generated_root = _generate_dataset(
        case,
        adapter,
        spec,
        skill_dirs,
        tmp_path / "datasets",
    )

    for step_index, filenames in case.expected_samples.items():
        instruction = (
            generated_root / f"step-{step_index}" / "instruction.md"
        ).read_text(encoding="utf-8")
        source_query = spec["expects"][step_index - 1]["query"]
        for filename in filenames:
            staged_path = str(STAGED_SAMPLE_ROOT / filename)
            assert staged_path in source_query
            assert staged_path in instruction
        assert "substitut" in instruction.lower()


def test_manage_alerts_vlm_packages_video_io_onboarding_skill(
    tmp_path: Path,
) -> None:
    """The live-camera task must include the delegated NVStreamer/VIOS skill."""
    case = next(item for item in CASES if item.skill == "vss-manage-alerts")
    spec_path = REPO_ROOT / "skills" / case.skill / "evals" / case.spec_name
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    assert "vss-manage-video-io-storage" in spec["skills"]

    spec["_source_path"] = str(spec_path)
    adapter = _load_adapter(case.skill)
    skill_dirs = _stub_skill_dirs(tmp_path / "skills", spec["skills"])
    generated_root = _generate_dataset(
        case,
        adapter,
        spec,
        skill_dirs,
        tmp_path / "datasets",
    )

    for step_dir in generated_root.glob("step-*"):
        assert (
            step_dir
            / "skills"
            / "vss-manage-video-io-storage"
            / "SKILL.md"
        ).is_file()
