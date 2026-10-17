# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Package-boundary tests for nvidia_vss.search."""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tomllib

# libs/vss/packages/search/tests/unit_test/search/<this file>
_PACKAGE_ROOT = Path(__file__).resolve().parents[3]
_WORKSPACE_ROOT = Path(__file__).resolve().parents[5]


def _source_env() -> dict[str, str]:
    """PYTHONPATH with both halves of the namespace: core and this package."""
    env = dict(os.environ)
    paths = [str(_PACKAGE_ROOT / "src"), str(_WORKSPACE_ROOT / "src")]
    if env.get("PYTHONPATH"):
        paths.append(env["PYTHONPATH"])
    env["PYTHONPATH"] = os.pathsep.join(paths)
    return env


def test_bare_search_import_is_lightweight() -> None:
    code = """
import sys
import nvidia_vss.search
heavy = [m for m in ("elasticsearch", "aiohttp", "langchain_core", "nat", "click", "vss_cli") if m in sys.modules]
assert heavy == [], heavy
"""
    subprocess.run([sys.executable, "-B", "-c", code], check=True, env=_source_env())


def test_old_agent_search_core_namespace_is_removed() -> None:
    try:
        spec = importlib.util.find_spec("agent.search_core")
    except ModuleNotFoundError:
        # Without the `agent` extra, importing agent itself may fail.
        spec = None
    assert spec is None


def test_removed_search_modules_have_no_compatibility_shims() -> None:
    assert importlib.util.find_spec("nvidia_vss.search_core") is None
    assert importlib.util.find_spec("nvidia_vss.search.clients.vst") is None
    assert importlib.util.find_spec("nvidia_vss.search.clients.vlm_openai") is None
    # The critic is inlined as a single nvidia_vss.search submodule, not split across
    # models/primitives and not a standalone top-level package.
    assert importlib.util.find_spec("nvidia_vss.search.models.critic") is None
    assert importlib.util.find_spec("nvidia_vss.search.primitives.critic") is None
    assert importlib.util.find_spec("nvidia_vss.critic") is None
    assert importlib.util.find_spec("nvidia_vss.search.critic") is not None


def test_cli_group_is_a_plugin_of_this_distribution() -> None:
    with (_PACKAGE_ROOT / "pyproject.toml").open("rb") as stream:
        project = tomllib.load(stream)["project"]
    assert project["name"] == "nvidia-vss-search"
    entry_points = project["entry-points"]
    assert entry_points["vss.commands"] == {"search": "nvidia_vss.search.cli.register:GROUP"}
    assert set(entry_points["vss.command_summaries"]) == {"search"}
    # The group is loaded only by the vss CLI, which brings click and vss_cli;
    # the library itself must not depend on them.
    names = {
        req.split("[", 1)[0].split("=", 1)[0].split(">", 1)[0].split("~", 1)[0].strip()
        for req in project["dependencies"]
    }
    assert names.isdisjoint({"click", "nvidia-vss-cli"})
