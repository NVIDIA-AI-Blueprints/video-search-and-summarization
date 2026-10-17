# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""nvidia_vss stays a PEP 420 namespace package across every workspace member."""

from __future__ import annotations

from pathlib import Path
import tomllib

import nvidia_vss

# libs/vss/tests/unit_test/lib/<this file>
_WORKSPACE_ROOT = Path(__file__).resolve().parents[3]


def _source_roots() -> list[Path]:
    """src/ of the root distribution and of every packages/* member."""
    with (_WORKSPACE_ROOT / "pyproject.toml").open("rb") as stream:
        members = tomllib.load(stream)["tool"]["uv"]["workspace"]["members"]
    roots = [_WORKSPACE_ROOT / "src"]
    for pattern in members:
        roots.extend(path / "src" for path in sorted(_WORKSPACE_ROOT.glob(pattern)) if (path / "src").is_dir())
    return roots


def test_no_member_owns_the_namespace() -> None:
    # One nvidia_vss/__init__.py in any member turns the namespace into a
    # regular package: the other members' modules then vanish from imports
    # in a workspace, and wheels overwrite each other's copy on install.
    roots = _source_roots()
    assert any((root / "nvidia_vss").is_dir() for root in roots[1:]), "expected packages/* members"
    offenders = [
        root / "nvidia_vss" / "__init__.py" for root in roots if (root / "nvidia_vss" / "__init__.py").exists()
    ]
    assert offenders == []


def test_installed_namespace_spans_every_member() -> None:
    assert getattr(nvidia_vss, "__file__", None) is None
    installed = {Path(path).resolve() for path in nvidia_vss.__path__}
    expected = {(root / "nvidia_vss").resolve() for root in _source_roots() if (root / "nvidia_vss").is_dir()}
    assert expected <= installed
