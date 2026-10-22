# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Caller-installed decoder requirement; packaging changes are future work."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path


def test_native_decoders_are_not_mandatory_wheel_dependencies() -> None:
    root = Path(__file__).resolve().parents[2]
    project = tomllib.loads((root / "pyproject.toml").read_text())["project"]
    dependency_names = {
        re.split(r"[<>=!~;\[ ]", dependency, maxsplit=1)[0].lower().replace("_", "-")
        for dependency in project["dependencies"]
    }
    assert not dependency_names & {
        "opencv-python",
        "opencv-python-headless",
        "pynvvideocodec",
    }, (
        "Clients install their decoder separately; remove native decoder requirements "
        "from the base distribution when implementing processing"
    )
