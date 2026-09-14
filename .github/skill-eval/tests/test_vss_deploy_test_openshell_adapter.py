# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""OpenShell vss-deploy-test-openshell tasks gate on gpu_count only."""

from __future__ import annotations

import importlib.util
from pathlib import Path

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
