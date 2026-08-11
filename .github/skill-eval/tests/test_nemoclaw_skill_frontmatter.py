# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Validate every bundled skill with a real YAML parser before NemoClaw install."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml


REPO_ROOT = Path(__file__).resolve().parents[3]
SKILL_FILES = tuple(sorted((REPO_ROOT / "skills").glob("*/SKILL.md")))


@pytest.mark.parametrize("skill_file", SKILL_FILES, ids=lambda path: path.parent.name)
def test_bundled_skill_frontmatter_is_valid_yaml(skill_file: Path) -> None:
    lines = skill_file.read_text(encoding="utf-8").splitlines()
    assert lines and lines[0] == "---"
    closing_index = lines.index("---", 1)

    frontmatter = yaml.safe_load("\n".join(lines[1:closing_index]))

    assert isinstance(frontmatter, dict)
    assert frontmatter["name"] == skill_file.parent.name
    assert isinstance(frontmatter.get("description"), str)
    assert frontmatter["description"].strip()
