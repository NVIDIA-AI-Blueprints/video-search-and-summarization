# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Root-disk contracts for the NemoClaw-compatible Harbor adapters."""

from __future__ import annotations

import importlib.util
import json
import tomllib
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[3]


def _load_adapter(skill: str):
    adapter_path = (
        REPO_ROOT / ".github/skill-eval/adapters" / skill / "generate.py"
    )
    spec = importlib.util.spec_from_file_location(
        f"{skill.replace('-', '_')}_disk_contract", adapter_path
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    ("skill", "spec_name", "platform", "profile"),
    (
        (
            "vss-ask-video",
            "base_profile_video_understanding.json",
            "L40S",
            "base",
        ),
        (
            "vss-query-analytics",
            "query_analytics.json",
            "RTXPRO6000BW",
            "alerts",
        ),
        (
            "vss-summarize-video",
            "lvs_api_ops.json",
            "RTXPRO6000BW",
            "lvs",
        ),
    ),
)
def test_generated_tasks_require_220_gb_root_disk(
    skill: str,
    spec_name: str,
    platform: str,
    profile: str,
    tmp_path: Path,
) -> None:
    adapter = _load_adapter(skill)
    spec_path = REPO_ROOT / "skills" / skill / "evals" / spec_name
    raw_spec = json.loads(spec_path.read_text(encoding="utf-8"))
    raw_spec["_source_path"] = str(spec_path)

    skill_dir = REPO_ROOT / "skills" / skill
    deploy_skill_dir = REPO_ROOT / "skills/vss-deploy-profile"
    args = [
        platform,
        profile,
        raw_spec,
        tmp_path,
        skill_dir,
        deploy_skill_dir,
    ]
    if skill != "vss-query-analytics":
        args.append(REPO_ROOT / "skills/vss-manage-video-io-storage")
    adapter.generate_task(*args)

    platform_short = adapter.PLATFORMS[platform]["short_name"]
    task_files = sorted((tmp_path / profile / platform_short).rglob("task.toml"))
    assert task_files
    assert {
        tomllib.loads(path.read_text(encoding="utf-8"))["metadata"][
            "min_root_disk_gb"
        ]
        for path in task_files
    } == {220}
