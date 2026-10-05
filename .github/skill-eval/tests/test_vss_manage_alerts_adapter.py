# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""OpenShell dataset generation emits the guest card, not the spec matrix."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
ADAPTER_PATH = REPO_ROOT / ".github/skill-eval/adapters/vss-manage-alerts/generate.py"
SPEC_PATH = (
    REPO_ROOT / "skills/operations/vss-manage-alerts/evals/always_on_operate.json"
)


def _load_adapter():
    spec = importlib.util.spec_from_file_location(
        "vss_manage_alerts_adapter", ADAPTER_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _spec() -> dict:
    return json.loads(SPEC_PATH.read_text())


class _Proc:
    def __init__(self, stdout: str, returncode: int = 0, stderr: str = "") -> None:
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


def _patch_smi(monkeypatch, adapter, stdout: str, returncode: int = 0) -> None:
    monkeypatch.setenv("SKILL_EVAL_LOCAL_GPU_INSTANCE", "openshell-guest")

    def fake_run(cmd, **kwargs):
        assert cmd[:2] == ["nvidia-smi", "--query-gpu=name"]
        return _Proc(stdout, returncode=returncode)

    monkeypatch.setattr(adapter.subprocess, "run", fake_run)


def test_openshell_guest_emits_only_the_detected_card(monkeypatch) -> None:
    adapter = _load_adapter()
    _patch_smi(
        monkeypatch,
        adapter,
        "NVIDIA RTX PRO 6000 Blackwell Workstation Edition\n",
    )

    tasks = adapter._platform_modes(_spec(), None)

    assert tasks == [("RTXPRO6000BW", "remote-all")]


def test_empty_platform_flag_is_the_guest_card(monkeypatch) -> None:
    adapter = _load_adapter()
    _patch_smi(monkeypatch, adapter, "NVIDIA H200\n")

    tasks = adapter._platform_modes(_spec(), "")

    assert tasks == [("H200", "remote-all")]


def test_explicit_platform_ignores_the_guest(monkeypatch) -> None:
    adapter = _load_adapter()
    monkeypatch.setenv("SKILL_EVAL_LOCAL_GPU_INSTANCE", "openshell-guest")

    def fail_run(*args, **kwargs):
        raise AssertionError("nvidia-smi must not run when --platform is set")

    monkeypatch.setattr(adapter.subprocess, "run", fail_run)

    tasks = adapter._platform_modes(_spec(), "H200")

    assert tasks == [("H200", "remote-all")]


def test_without_openshell_env_the_spec_matrix_is_unchanged(monkeypatch) -> None:
    adapter = _load_adapter()
    monkeypatch.delenv("SKILL_EVAL_LOCAL_GPU_INSTANCE", raising=False)

    tasks = adapter._platform_modes(_spec(), None)

    assert tasks == [
        ("H200", "remote-all"),
        ("RTXPRO6000BW", "remote-all"),
    ]


def test_guest_card_missing_from_the_spec_still_generates(monkeypatch) -> None:
    adapter = _load_adapter()
    _patch_smi(monkeypatch, adapter, "NVIDIA L40S\n")
    spec = {"resources": {"platforms": {"H200": {"modes": ["remote-all"]}}}}

    tasks = adapter._platform_modes(spec, None)

    assert tasks == [("L40S", "remote-all")]


def test_unrecognised_gpu_blocks_with_the_detected_name(monkeypatch, capsys) -> None:
    adapter = _load_adapter()
    _patch_smi(monkeypatch, adapter, "NVIDIA A16\n")

    with pytest.raises(SystemExit) as exc:
        adapter._platform_modes(_spec(), None)

    assert exc.value.code == 1
    assert "NVIDIA A16" in capsys.readouterr().err


def test_cli_accepts_an_empty_platform(monkeypatch, tmp_path: Path) -> None:
    adapter = _load_adapter()
    monkeypatch.delenv("SKILL_EVAL_LOCAL_GPU_INSTANCE", raising=False)
    seen: dict[str, str | None] = {}

    def fake_modes(spec, platform_filter):
        seen["filter"] = platform_filter
        return []

    monkeypatch.setattr(adapter, "_platform_modes", fake_modes)
    adapter.main(
        [
            "--output-dir",
            str(tmp_path),
            "--skill-dir",
            str(REPO_ROOT / "skills/operations/vss-manage-alerts"),
            "--spec",
            str(SPEC_PATH),
            "--platform",
            "",
        ]
    )

    assert seen["filter"] is None
