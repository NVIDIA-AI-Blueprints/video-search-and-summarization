# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Package-boundary tests for lib.search_core."""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tomllib


def test_bare_search_core_import_is_lightweight() -> None:
    env = dict(os.environ)
    pythonpath = env.get("PYTHONPATH")
    src_path = str(Path(__file__).resolve().parents[4] / "src")
    env["PYTHONPATH"] = src_path if not pythonpath else f"{src_path}{os.pathsep}{pythonpath}"

    code = """
import sys
import vss_core.search_core
heavy = [m for m in ("elasticsearch", "aiohttp", "langchain_core", "nat") if m in sys.modules]
assert heavy == [], heavy
"""
    subprocess.run([sys.executable, "-B", "-c", code], check=True, env=env)


def test_old_agent_search_core_namespace_is_removed() -> None:
    try:
        spec = importlib.util.find_spec("agent.search_core")
    except ModuleNotFoundError:
        # Without the `agent` extra, importing agent itself may fail.
        spec = None
    assert spec is None


def test_removed_search_core_modules_have_no_compatibility_shims() -> None:
    assert importlib.util.find_spec("vss_core.search_core.cli") is None
    assert importlib.util.find_spec("vss_core.search_core.clients.vst") is None
    assert importlib.util.find_spec("vss_core.search_core.clients.vlm_openai") is None
    assert importlib.util.find_spec("vss_core.search_core.models.critic") is None
    assert importlib.util.find_spec("vss_core.search_core.primitives.critic") is None
    assert importlib.util.find_spec("vss_core.critic") is not None


def test_reusable_vst_and_vlm_packages_do_not_import_search_core() -> None:
    env = dict(os.environ)
    pythonpath = env.get("PYTHONPATH")
    src_path = str(Path(__file__).resolve().parents[4] / "src")
    env["PYTHONPATH"] = src_path if not pythonpath else f"{src_path}{os.pathsep}{pythonpath}"

    for package in ("vss_core.vios", "vss_core.vlm", "vss_core.critic"):
        code = f"import sys; import {package}; assert 'vss_core.search_core' not in sys.modules"
        subprocess.run([sys.executable, "-B", "-c", code], check=True, env=env)


def test_foundation_retry_does_not_import_aiohttp() -> None:
    env = dict(os.environ)
    pythonpath = env.get("PYTHONPATH")
    src_path = str(Path(__file__).resolve().parents[4] / "src")
    env["PYTHONPATH"] = src_path if not pythonpath else f"{src_path}{os.pathsep}{pythonpath}"

    code = "import sys; import vss_core._foundation.retry; assert 'aiohttp' not in sys.modules"
    subprocess.run([sys.executable, "-B", "-c", code], check=True, env=env)


def _requirement_names(requirements: list[str]) -> set[str]:
    return {
        str(requirement).split("[", 1)[0].split("=", 1)[0].split(">", 1)[0].split("~", 1)[0].strip().lower()
        for requirement in requirements
    }


def test_distribution_is_nvidia_nat_torch_and_langchain_free_by_default() -> None:
    # parents[4] is the vss_core package root (packages/vss_core).
    package_root = Path(__file__).resolve().parents[4]
    with (package_root / "pyproject.toml").open("rb") as stream:
        project = tomllib.load(stream)["project"]

    assert project["name"] == "nvidia-vss-core"
    assert {"nvidia-nat", "torch", "langchain", "langchain-core"}.isdisjoint(
        _requirement_names(project["dependencies"])
    )


def test_agent_extra_gates_the_nat_stack() -> None:
    # The NAT stack lives in the nvidia-vss-agents distribution, reachable only
    # via the meta `[agent]` extra -- not in nvidia-vss-core.
    agent_root = Path(__file__).resolve().parents[6]  # services/agent
    with (agent_root / "pyproject.toml").open("rb") as stream:
        meta = tomllib.load(stream)["project"]
    meta_extras = meta["optional-dependencies"]
    assert "cli" in meta_extras
    assert "agent" in meta_extras
    # `agent` pulls the agents dist (and bundles the cli dist so the full test
    # suite installs via `--extra agent`); it must not inline the NAT stack.
    assert "nvidia-vss-agents" in meta_extras["agent"]

    with (agent_root / "packages" / "vss_agents" / "pyproject.toml").open("rb") as stream:
        agents = tomllib.load(stream)["project"]
    assert {"nvidia-nat", "torch", "langchain-core"} <= _requirement_names(agents["dependencies"])

    # And the `vss` console script ships from the cli distribution, not core.
    with (agent_root / "packages" / "vss_cli" / "pyproject.toml").open("rb") as stream:
        cli_project = tomllib.load(stream)["project"]
    assert cli_project["scripts"]["vss"] == "vss_cli:main"
