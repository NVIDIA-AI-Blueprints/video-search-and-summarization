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
