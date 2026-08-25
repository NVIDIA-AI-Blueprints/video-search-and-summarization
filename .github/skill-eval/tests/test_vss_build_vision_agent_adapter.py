#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Regression tests for vss-build-vision-agent task metadata."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import tomllib

ADAPTER = (
    Path(__file__).resolve().parents[1]
    / "adapters"
    / "vss-build-vision-agent"
    / "generate.py"
)
SPEC = importlib.util.spec_from_file_location("vss_build_vision_agent_adapter", ADAPTER)
adapter = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(adapter)


def test_a40_task_carries_per_gpu_fleet_metadata(tmp_path):
    output = tmp_path / "datasets"
    spec = {
        "_source_path": "profile_in_1.json",
        "profile": "in-1",
        "resources": {"platforms": {"A40": {"gpu_count": 2}}},
        "runtime_deploy": False,
        "expects": [{"query": "Build the profile.", "checks": []}],
    }

    adapter.generate_task(
        "A40",
        spec,
        output,
        tmp_path / "missing-skill",
        None,
        None,
        None,
        None,
        None,
    )

    task = tomllib.loads((output / "profile_in_1" / "a40" / "task.toml").read_text())
    assert task["metadata"]["gpu_count"] == 2
    assert task["metadata"]["min_vram_gb_per_gpu"] == 48
    assert task["metadata"]["min_root_disk_gb"] == 220
