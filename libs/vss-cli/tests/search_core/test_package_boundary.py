# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Package-boundary tests for lib.search_core."""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import tomllib
from pathlib import Path


def test_bare_search_core_import_is_lightweight() -> None:
    env = dict(os.environ)
    pythonpath = env.get("PYTHONPATH")
    src_path = str(Path(__file__).resolve().parents[2] / "src")
    env["PYTHONPATH"] = src_path if not pythonpath else f"{src_path}{os.pathsep}{pythonpath}"

    code = """
import sys
import lib.search_core
heavy = [m for m in ("elasticsearch", "aiohttp", "langchain_core", "nat") if m in sys.modules]
assert heavy == [], heavy
"""
    subprocess.run([sys.executable, "-B", "-c", code], check=True, env=env)


def test_old_vss_agents_search_core_namespace_is_removed() -> None:
    try:
        spec = importlib.util.find_spec("vss_agents.search_core")
    except ModuleNotFoundError:
        # The standalone wheel deliberately does not install vss_agents at all.
        spec = None
    assert spec is None


def test_removed_search_core_modules_have_no_compatibility_shims() -> None:
    assert importlib.util.find_spec("lib.search_core.cli") is None
    assert importlib.util.find_spec("lib.search_core.clients.vst") is None
    assert importlib.util.find_spec("lib.search_core.clients.vlm_openai") is None


def test_reusable_vst_and_vlm_packages_do_not_import_search_core() -> None:
    env = dict(os.environ)
    pythonpath = env.get("PYTHONPATH")
    src_path = str(Path(__file__).resolve().parents[2] / "src")
    env["PYTHONPATH"] = src_path if not pythonpath else f"{src_path}{os.pathsep}{pythonpath}"

    for package in ("lib.vst", "lib.vlm"):
        code = f"import sys; import {package}; assert 'lib.search_core' not in sys.modules"
        subprocess.run([sys.executable, "-B", "-c", code], check=True, env=env)


def test_foundation_retry_does_not_import_aiohttp() -> None:
    env = dict(os.environ)
    pythonpath = env.get("PYTHONPATH")
    src_path = str(Path(__file__).resolve().parents[2] / "src")
    env["PYTHONPATH"] = src_path if not pythonpath else f"{src_path}{os.pathsep}{pythonpath}"

    code = "import sys; import lib._foundation.retry; assert 'aiohttp' not in sys.modules"
    subprocess.run([sys.executable, "-B", "-c", code], check=True, env=env)


def test_distribution_is_nvidia_nat_torch_and_langchain_free_by_default() -> None:
    package_root = Path(__file__).resolve().parents[2]
    with (package_root / "pyproject.toml").open("rb") as stream:
        project = tomllib.load(stream)["project"]
    dependencies = {
        str(requirement).split("[", 1)[0].split("=", 1)[0].split(">", 1)[0].lower()
        for requirement in project["dependencies"]
    }

    assert {"nvidia-nat", "torch", "langchain", "langchain-core"}.isdisjoint(dependencies)
    assert project["scripts"]["vss-cli"] == "lib.cli:main"


def test_optional_knowledge_backends_are_owned_by_standalone_distribution() -> None:
    package_root = Path(__file__).resolve().parents[2]
    with (package_root / "pyproject.toml").open("rb") as stream:
        optional = tomllib.load(stream)["project"]["optional-dependencies"]

    assert {"frag-lib", "langchain", "llama-index"} <= optional.keys()
    assert all(
        "vss-agent" not in requirement.lower() for requirements in optional.values() for requirement in requirements
    )
