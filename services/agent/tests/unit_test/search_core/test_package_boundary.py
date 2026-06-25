# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Package-boundary tests for lib.search_core."""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import subprocess
import sys


def test_bare_search_core_import_is_lightweight() -> None:
    env = dict(os.environ)
    pythonpath = env.get("PYTHONPATH")
    src_path = str(Path(__file__).resolve().parents[3] / "src")
    env["PYTHONPATH"] = src_path if not pythonpath else f"{src_path}{os.pathsep}{pythonpath}"

    code = """
import sys
import lib.search_core
heavy = [m for m in ("elasticsearch", "aiohttp", "langchain_core", "nat") if m in sys.modules]
assert heavy == [], heavy
"""
    subprocess.run([sys.executable, "-B", "-c", code], check=True, env=env)


def test_old_vss_agents_search_core_namespace_is_removed() -> None:
    assert importlib.util.find_spec("vss_agents.search_core") is None
