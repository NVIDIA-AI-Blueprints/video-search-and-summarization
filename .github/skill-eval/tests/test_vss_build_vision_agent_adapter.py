#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Regression tests for vss-build-vision-agent task metadata."""

from __future__ import annotations

import importlib.util
import tomllib
from pathlib import Path


ADAPTER = (
    Path(__file__).resolve().parents[1]
    / "adapters"
    / "vss-build-vision-agent"
    / "generate.py"
)
SPEC = importlib.util.spec_from_file_location("vss_build_vision_agent_adapter", ADAPTER)
adapter = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(adapter)


def test_h200_task_carries_fleet_selection_metadata(tmp_path):
    output = tmp_path / "datasets"
    spec = {
        "_source_path": "profile_in_1.json",
        "profile": "in-1",
        "resources": {"platforms": {"H200": {"gpu_count": 1}}},
        "runtime_deploy": False,
        "expects": [{"query": "Build the profile.", "checks": []}],
    }

    adapter.generate_task(
        "H200",
        spec,
        output,
        tmp_path / "missing-skill",
        None,
        None,
        None,
        None,
        None,
    )

    task = tomllib.loads(
        (output / "profile_in_1" / "h200" / "task.toml").read_text()
    )
    assert task["metadata"]["gpu_count"] == 1
    assert task["metadata"]["min_root_disk_gb"] == 220
