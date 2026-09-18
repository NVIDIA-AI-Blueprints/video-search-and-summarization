#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""The sizing platform comes from the card, and one table answers for all.

A count-only OpenShell leg is placed by fleet label plus `gpus-N`, so the
SKU a spec declares is not the SKU the leg landed on. These tests pin the
two properties that keep that safe: the platform is read off `nvidia-smi`,
and an unrecognised card blocks instead of falling back to a guess.
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path
from unittest import mock

SKILL_EVAL_ROOT = Path(__file__).resolve().parents[1]
if str(SKILL_EVAL_ROOT) not in sys.path:
    sys.path.insert(0, str(SKILL_EVAL_ROOT))

import live_platform  # noqa: E402


def _load_openshell_adapter():
    path = (
        SKILL_EVAL_ROOT
        / "adapters"
        / "vss-deploy-test-openshell"
        / "generate.py"
    )
    spec = importlib.util.spec_from_file_location("openshell_adapter", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_blackwell_server_card_is_not_read_as_an_older_sku() -> None:
    assert (
        live_platform.detect_platform(["NVIDIA RTX PRO 6000 Blackwell"])
        == "RTXPRO6000BW"
    )
    assert live_platform.detect_platform(["NVIDIA H200"]) == "H200"
    assert live_platform.detect_platform(["NVIDIA L40S"]) == "L40S"


def test_live_l40s_is_blocked_for_openshell_sizing() -> None:
    platform, error = live_platform.resolve_from_names(None, ["NVIDIA L40S"])
    assert platform is None
    assert "this OpenShell runner also has the l40s label" in error


def test_unrecognised_card_blocks_rather_than_guessing() -> None:
    platform, error = live_platform.resolve_from_names(None, ["NVIDIA T4"])
    assert platform is None
    assert "unrecognised GPU" in error
    assert "NVIDIA T4" in error


def test_unreadable_gpu_blocks_rather_than_guessing() -> None:
    platform, error = live_platform.resolve_from_names(None, [])
    assert platform is None
    assert "nvidia-smi" in error


def test_explicit_request_overrides_the_live_card() -> None:
    """The operator override exists for cards the table does not cover."""
    platform, error = live_platform.resolve_from_names("L40S", ["NVIDIA T4"])
    assert (platform, error) == ("L40S", None)


def test_cli_prints_the_platform_and_fails_closed() -> None:
    script = str(SKILL_EVAL_ROOT / "live_platform.py")
    with mock.patch.object(
        live_platform, "live_gpu_names", return_value=["NVIDIA H200"]
    ):
        assert live_platform.main() == 0
    with mock.patch.object(
        live_platform, "live_gpu_names", return_value=["NVIDIA T4"]
    ):
        assert live_platform.main() == 3
    # The workflow calls it as a script, so the module must be runnable.
    assert subprocess.run(
        [sys.executable, script], capture_output=True, text=True
    ).returncode in (0, 3)


def test_openshell_adapter_resolves_from_the_same_table() -> None:
    """One token table, so a new card cannot be known to only one caller."""
    adapter = _load_openshell_adapter()
    assert adapter._LIVE_GPU_TOKENS is live_platform.LIVE_GPU_TOKENS
    with mock.patch.object(
        adapter, "live_gpu_names", return_value=["NVIDIA RTX PRO 6000"]
    ):
        assert adapter.resolve_sizing_platform(None) == ("RTXPRO6000BW", None)
