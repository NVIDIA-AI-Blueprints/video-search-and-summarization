#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import importlib.util
import json
import os
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
        "expects": [{"query": "Build it", "checks": ["Build artifacts exist"]}],
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
    rendered_spec = json.loads((tests / "example.json").read_text())
    rendered_check = rendered_spec["expects"][0]["checks"][0]

    assert "set -euo pipefail" in script
    assert '--build-dir "$REPO_ROOT/_builds/in-1"' in script
    assert "--out /logs/verifier/build-artifacts.json" in script
    assert "/logs/verifier/build-artifacts.json" in rendered_check
    assert "deterministic evidence" in rendered_check


def test_generated_verifier_stops_when_inspector_fails(tmp_path: Path) -> None:
    adapter = _load(ADAPTER, "vss_build_vision_ai_fail_fast")
    tests = tmp_path / "tests"
    tests.mkdir()
    marker = tmp_path / "judge-ran"
    script = tests / "test.sh"
    script.write_text(adapter.generate_test_script(1, "example.json", "in-1"))
    (tests / "build_artifact_inspector.py").write_text("raise SystemExit(42)\n")
    (tests / "generic_judge.py").write_text(
        f"from pathlib import Path\nPath({str(marker)!r}).touch()\n"
    )

    result = subprocess.run(
        ["bash", str(script)],
        check=False,
        env={**os.environ, "HOME": str(tmp_path)},
    )

    assert result.returncode == 42
    assert not marker.exists()
