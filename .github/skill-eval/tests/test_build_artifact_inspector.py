#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
INSPECTOR = ROOT / ".github/skill-eval/verifiers/build_artifact_inspector.py"
ADAPTER = ROOT / ".github/skill-eval/adapters/vss-build-vision-ai/generate.py"


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_inspector_parses_assignments_and_ignores_comments(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    build = repo / "_builds/test"
    build.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (build / "override.env").write_text(
        "# FOUNDATION=wrong\nFOUNDATION=lvs\n"
        "# COMPOSE_PROFILES=wrong\nexport COMPOSE_PROFILES='kafka,rtvi-vlm'\n"
    )
    (build / "compose.yml").write_text("services: {}\n")
    protected = repo / "deploy/docker/generated.txt"
    protected.parent.mkdir(parents=True)
    protected.write_text("stale\n")

    evidence = _load(INSPECTOR, "build_artifact_inspector").inspect(repo, build)

    assert evidence["foundation"] == "lvs"
    assert evidence["compose_profiles"] == ["kafka", "rtvi-vlm"]
    assert evidence["deploy_docker_changes"] == ["?? deploy/docker/generated.txt"]
    assert evidence["artifacts"] == {
        "override.env": True,
        "compose.yml": True,
        "resolved.yml": False,
    }


def test_adapter_bundles_and_invokes_inspector(tmp_path: Path) -> None:
    adapter = _load(ADAPTER, "vss_build_vision_ai_adapter")
    spec = {
        "_source_path": "example.json",
        "profile": "in-1",
        "resources": {"platforms": {"RTXPRO6000BW": {}}},
        "expects": [{"query": "Build it", "checks": ["Artifacts exist."]}],
    }
    skill = tmp_path / "skill"
    skill.mkdir()
    (skill / "SKILL.md").write_text("test\n")

    adapter.generate_task(
        "RTXPRO6000BW",
        spec,
        tmp_path / "out",
        skill,
        None,
        None,
        None,
        None,
        None,
    )

    tests = tmp_path / "out/example/rtxpro6000bw/tests"
    assert (tests / "build_artifact_inspector.py").is_file()
    script = (tests / "test.sh").read_text()
    assert '--build-dir "$REPO_ROOT/_builds/in-1"' in script
    assert "--out /logs/verifier/build-artifacts.json" in script
    rendered = (tests / "example.json").read_text()
    assert "Artifacts exist. Prefer /logs/verifier/build-artifacts.json" in rendered
