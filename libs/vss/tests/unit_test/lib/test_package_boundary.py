# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Package-boundary tests for the core nvidia_vss distribution."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import tomllib

# libs/vss/tests/unit_test/lib/<this file>
_WORKSPACE_ROOT = Path(__file__).resolve().parents[3]


def _source_env() -> dict[str, str]:
    env = dict(os.environ)
    src_path = str(_WORKSPACE_ROOT / "src")
    env["PYTHONPATH"] = src_path if not env.get("PYTHONPATH") else f"{src_path}{os.pathsep}{env['PYTHONPATH']}"
    return env


def _requirement_names(requirements: list[str]) -> set[str]:
    return {
        str(requirement).split("[", 1)[0].split("=", 1)[0].split(">", 1)[0].split("~", 1)[0].strip().lower()
        for requirement in requirements
    }


def test_media_and_vlm_do_not_import_search() -> None:
    # media and vlm stay independently reusable and must not pull nvidia_vss.search,
    # even in an environment where the search distribution is installed.
    for package in ("nvidia_vss.media", "nvidia_vss.vlm"):
        code = f"import sys; import {package}; assert 'nvidia_vss.search' not in sys.modules"
        subprocess.run([sys.executable, "-B", "-c", code], check=True, env=_source_env())


def test_foundation_retry_does_not_import_aiohttp() -> None:
    code = "import sys; import nvidia_vss._foundation.retry; assert 'aiohttp' not in sys.modules"
    subprocess.run([sys.executable, "-B", "-c", code], check=True, env=_source_env())


def test_distribution_is_nvidia_nat_torch_and_langchain_free_by_default() -> None:
    with (_WORKSPACE_ROOT / "pyproject.toml").open("rb") as stream:
        project = tomllib.load(stream)["project"]

    assert project["name"] == "nvidia-vss-core"
    assert {"nvidia-nat", "torch", "langchain", "langchain-core"}.isdisjoint(
        _requirement_names(project["dependencies"])
    )


def test_agent_extra_gates_the_nat_stack() -> None:
    # The NAT stack lives in the nvidia-vss-agents distribution, reachable only
    # via the meta `[agent]` extra -- not in nvidia-vss-core.
    agent_root = _WORKSPACE_ROOT.parents[1] / "services" / "agent"
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

    # And the `vss` console script ships from the cli distribution, never from core.
    with (_WORKSPACE_ROOT / "cli" / "pyproject.toml").open("rb") as stream:
        cli_project = tomllib.load(stream)["project"]
    assert cli_project["scripts"]["vss"] == "vss_cli:main"
