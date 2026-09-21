# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""OpenShell vss-deploy-test-openshell legs carry no SKU.

Placement is fleet labels plus `gpu_count`, so the spec cannot say which
card a leg runs on. Sizing still has to be exact — `hw-<profile>.env`
values are measured per card — so the adapter reads the SKU off the live
GPU and refuses to generate for a card it does not recognise.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from unittest import mock

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
ADAPTER_PATH = (
    REPO_ROOT / ".github/skill-eval/adapters/vss-deploy-test-openshell/generate.py"
)


def _load_adapter():
    spec = importlib.util.spec_from_file_location(
        "vss_deploy_test_openshell_adapter", ADAPTER_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _skill_dir(tmp_path: Path, spec: dict, profile: str = "base") -> Path:
    skill_dir = tmp_path / "skill"
    (skill_dir / "evals").mkdir(parents=True)
    (skill_dir / "evals" / f"{profile}.json").write_text(json.dumps(spec))
    return skill_dir


def test_task_toml_omits_sku_and_vram_gates(tmp_path: Path) -> None:
    adapter = _load_adapter()
    adapter.generate_task(
        "base",
        "H200",
        adapter.PROFILES["base"],
        tmp_path,
        skill_dir=None,
        gpu_count=1,
    )
    text = (tmp_path / "base" / "h200" / "task.toml").read_text()
    assert "gpu_count = 1" in text
    assert "gpu_type =" not in text
    assert "min_vram_gb_per_gpu =" not in text
    assert "brev_search =" not in text


@pytest.mark.parametrize(
    ("names", "expected"),
    [
        (["NVIDIA H200 NVL", "NVIDIA H200 NVL"], "H200"),
        (["NVIDIA RTX PRO 6000 Blackwell Server Edition"], "RTXPRO6000BW"),
        (["NVIDIA A40"], "A40"),
    ],
)
def test_sizing_profile_comes_from_the_live_card(names, expected) -> None:
    adapter = _load_adapter()
    with mock.patch.object(adapter, "live_gpu_names", return_value=names):
        platform, error = adapter.resolve_sizing_platform(None)
    assert (platform, error) == (expected, None)


def test_unrecognised_card_blocks_and_names_it() -> None:
    adapter = _load_adapter()
    with mock.patch.object(adapter, "live_gpu_names", return_value=["NVIDIA T4"]):
        platform, error = adapter.resolve_sizing_platform(None)
    assert platform is None
    assert "NVIDIA T4" in error


def test_unreadable_gpu_blocks() -> None:
    adapter = _load_adapter()
    with mock.patch.object(adapter, "live_gpu_names", return_value=[]):
        platform, error = adapter.resolve_sizing_platform(None)
    assert platform is None
    assert "nvidia-smi" in error


def test_explicit_platform_overrides_detection() -> None:
    """A local run on an unknown box can still name its own profile."""
    adapter = _load_adapter()
    with mock.patch.object(adapter, "live_gpu_names", return_value=["NVIDIA T4"]):
        platform, error = adapter.resolve_sizing_platform("L40S")
    assert (platform, error) == ("L40S", None)


def test_spec_platform_keys_do_not_gate_generation(tmp_path: Path) -> None:
    """The guest that claimed the labels is the guest that gets generated for."""
    adapter = _load_adapter()
    skill_dir = _skill_dir(
        tmp_path,
        {
            "openshell": {"gpu_count": 2},
            "resources": {"platforms": {"H200": {"gpu_count": 2}}},
        },
    )
    included, skipped = adapter.expand_matrix(
        "base", "RTXPRO6000BW", skill_dir=skill_dir
    )
    assert included == [("base", "RTXPRO6000BW", 2)]
    assert skipped == []


def test_gpu_demand_falls_back_to_per_platform_counts(tmp_path: Path) -> None:
    adapter = _load_adapter()
    skill_dir = _skill_dir(
        tmp_path,
        {"resources": {"platforms": {"H200": {"gpu_count": 2}}}},
    )
    assert adapter._spec_gpu_count("base", skill_dir) == (2, None)


def test_conflicting_gpu_demand_is_reported(tmp_path: Path) -> None:
    adapter = _load_adapter()
    skill_dir = _skill_dir(
        tmp_path,
        {
            "resources": {
                "platforms": {
                    "H200": {"gpu_count": 1},
                    "RTXPRO6000BW": {"gpu_count": 2},
                }
            }
        },
    )
    count, reason = adapter._spec_gpu_count("base", skill_dir)
    assert count is None
    assert "conflicting GPU demand" in reason


def test_solve_script_writes_the_resolved_profile() -> None:
    adapter = _load_adapter()
    with mock.patch.dict("os.environ", {"HARDWARE_PROFILE": "H200"}, clear=False):
        script = adapter.generate_solve_script("lvs", "RTXPRO6000BW")
    assert "HARDWARE_PROFILE=RTXPRO6000BW" in script
    assert "HARDWARE_PROFILE=H200" not in script


def test_guest_marker_is_skipped_outside_ci(tmp_path: Path) -> None:
    adapter = _load_adapter()
    assert (
        adapter.write_guest_leg_marker(
            dest_dir=tmp_path,
            environ={"EVAL_SPEC_STEM": "lvs"},
        )
        is None
    )
    assert list(tmp_path.glob("current-leg-*.json")) == []


def test_guest_marker_is_skipped_without_a_spec(tmp_path: Path) -> None:
    adapter = _load_adapter()
    assert (
        adapter.write_guest_leg_marker(
            dest_dir=tmp_path,
            environ={"RUNNER_NAME": "h200-1-g1-abc"},
        )
        is None
    )
    assert list(tmp_path.glob("current-leg-*.json")) == []


def test_guest_marker_names_the_spec_for_a_fleet_probe(tmp_path: Path) -> None:
    adapter = _load_adapter()
    path = adapter.write_guest_leg_marker(
        extra={"hardware_profile": "H200"},
        dest_dir=tmp_path,
        environ={
            "RUNNER_NAME": "h200-2-g3-xyz",
            "EVAL_SKILL": "vss-deploy-test-openshell",
            "EVAL_SPEC_STEM": "lvs",
            "EVAL_SPEC_PATH": "skills/vss-deploy-test-openshell/evals/lvs.json",
            "EVAL_SLUG": "vss-deploy-test-openshell__lvs__gpus-1",
            "GITHUB_RUN_ID": "12345",
            "EVAL_PLATFORM": "",
        },
    )
    assert path == tmp_path / "current-leg-h200-2-g3-xyz.json"
    payload = json.loads(path.read_text())
    assert payload["spec_stem"] == "lvs"
    assert payload["skill"] == "vss-deploy-test-openshell"
    assert payload["slug"] == "vss-deploy-test-openshell__lvs__gpus-1"
    assert payload["run_id"] == "12345"
    assert payload["hardware_profile"] == "H200"
    assert payload["eval_platform"] == ""


def test_guest_marker_write_failure_does_not_raise(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    adapter = _load_adapter()
    blocked = tmp_path / "blocked"
    blocked.write_text("not a directory")
    monkeypatch.setattr(adapter, "_GUEST_LEG_MARKER_DIR", blocked)
    path = adapter.write_guest_leg_marker(
        environ={
            "RUNNER_NAME": "guest-1",
            "EVAL_SPEC_STEM": "search",
        },
    )
    assert path is None


def test_generate_task_copies_build_and_deployment_skills(tmp_path: Path) -> None:
    adapter = _load_adapter()
    skills_root = tmp_path / "skills"
    skill_dir = skills_root / "vss-deploy-test-openshell"
    (skill_dir / "evals").mkdir(parents=True)
    (skill_dir / "evals" / "base.json").write_text(
        json.dumps({"openshell": {"gpu_count": 1}, "expects": [{"query": "x", "checks": ["y"]}]})
    )
    (skill_dir / "SKILL.md").write_text("# openshell\n")
    for name in adapter.ALWAYS_BUNDLED_SKILLS:
        if name == "vss-build-vision-ai":
            dest = skills_root / name
        else:
            dest = skills_root / "deployment" / name
        dest.mkdir(parents=True)
        (dest / "SKILL.md").write_text(f"# {name}\n")
    adapter.generate_task(
        "base",
        "H200",
        adapter.PROFILES["base"],
        tmp_path / "out",
        skill_dir=skill_dir,
        gpu_count=1,
    )
    bundled = tmp_path / "out" / "base" / "h200" / "skills"
    for name in adapter.ALWAYS_BUNDLED_SKILLS:
        assert (bundled / name / "SKILL.md").is_file(), name


def test_generate_task_does_not_write_a_guest_marker(tmp_path: Path) -> None:
    """Harbor task generation stays a dataset write; the marker is CI-only."""
    adapter = _load_adapter()
    with mock.patch.object(adapter, "write_guest_leg_marker") as write_marker:
        adapter.generate_task(
            "base",
            "H200",
            adapter.PROFILES["base"],
            tmp_path,
            skill_dir=None,
            gpu_count=1,
        )
    write_marker.assert_not_called()
    assert list(tmp_path.rglob("current-leg-*.json")) == []


def test_routing_exam_is_an_openshell_profile() -> None:
    adapter = _load_adapter()
    assert "base_profile_video_understanding" in adapter.PROFILES
    assert adapter.deploy_profile("base_profile_video_understanding") == "base"


def test_multi_expect_spec_emits_harbor_steps(tmp_path: Path) -> None:
    adapter = _load_adapter()
    skill_dir = _skill_dir(
        tmp_path,
        {
            "openshell": {"gpu_count": 1},
            "expects": [
                {"query": "Deploy on {{platform}}", "checks": ["up"]},
                {"query": "When did the forklift cross?", "checks": ["10:14 UTC"]},
            ],
        },
        profile="base_profile_video_understanding",
    )
    adapter.generate_task(
        "base_profile_video_understanding",
        "H200",
        adapter.PROFILES["base_profile_video_understanding"],
        tmp_path / "out",
        skill_dir=skill_dir,
        gpu_count=1,
    )
    root = tmp_path / "out" / "base_profile_video_understanding" / "h200"
    assert (root / "step-1" / "instruction.md").is_file()
    assert (root / "step-2" / "instruction.md").is_file()
    assert not (root / "task.toml").exists()
    step1 = (root / "step-1" / "tests" / "test.sh").read_text()
    step2 = (root / "step-2" / "tests" / "test.sh").read_text()
    assert "--step 1" in step1
    assert "--step 2" in step2
    assert "When did the forklift cross?" in (
        root / "step-2" / "instruction.md"
    ).read_text()


def test_single_expect_spec_stays_flat(tmp_path: Path) -> None:
    adapter = _load_adapter()
    adapter.generate_task(
        "base",
        "H200",
        adapter.PROFILES["base"],
        tmp_path,
        skill_dir=None,
        gpu_count=1,
    )
    assert (tmp_path / "base" / "h200" / "task.toml").is_file()
    assert not (tmp_path / "base" / "h200" / "step-1").exists()


def test_main_refreshes_the_marker_with_the_live_profile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    adapter = _load_adapter()
    calls: list[dict] = []

    def _capture(extra=None, dest_dir=None, environ=None):
        calls.append({"extra": extra})
        return tmp_path / "current-leg-guest.json"

    monkeypatch.setattr(adapter, "write_guest_leg_marker", _capture)
    monkeypatch.setattr(
        adapter, "resolve_sizing_platform", lambda requested: ("H200", None)
    )
    monkeypatch.setattr(
        adapter,
        "expand_matrix",
        lambda *args, **kwargs: ([("base", "H200", 1)], []),
    )
    monkeypatch.setattr(adapter, "generate_task", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "generate.py",
            "--output-dir",
            str(tmp_path),
            "--profile",
            "base",
            "--platform",
            "H200",
        ],
    )
    adapter.main()
    assert calls[0]["extra"] is None
    assert calls[-1]["extra"] == {"hardware_profile": "H200"}
