# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Keep every generated Harbor task bounded by an agent timeout."""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
ADAPTERS_ROOT = REPO_ROOT / ".github/skill-eval/adapters"
AGENT_TIMEOUT_LINE = "timeout_sec = 600.0"


def _task_templates(generator: Path) -> list[tuple[int, list[str | None]]]:
    """Return list literals used to assemble task.toml documents."""
    tree = ast.parse(generator.read_text(), filename=str(generator))
    templates: list[tuple[int, list[str | None]]] = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.List):
            continue
        lines = [
            element.value
            if isinstance(element, ast.Constant) and isinstance(element.value, str)
            else None
            for element in node.elts
        ]
        if "[task]" in lines and "[environment]" in lines:
            templates.append((node.lineno, lines))

    return templates


def test_every_adapter_task_template_declares_agent_timeout() -> None:
    """Inventory all generators and require the base timeout in every template."""
    generators = sorted(ADAPTERS_ROOT.rglob("generate.py"))
    assert generators, f"no adapter generators found under {ADAPTERS_ROOT}"

    for generator in generators:
        relative_path = generator.relative_to(REPO_ROOT)
        templates = _task_templates(generator)
        assert templates, f"{relative_path} has no recognizable task.toml template"

        for line_number, lines in templates:
            location = f"{relative_path}:{line_number}"
            assert lines.count("[agent]") == 1, (
                f"{location} must declare exactly one [agent] section"
            )
            assert lines.count(AGENT_TIMEOUT_LINE) == 1, (
                f"{location} must declare exactly one {AGENT_TIMEOUT_LINE!r}"
            )

            agent_index = lines.index("[agent]")
            environment_index = lines.index("[environment]")
            assert lines[agent_index : agent_index + 3] == [
                "[agent]",
                AGENT_TIMEOUT_LINE,
                "",
            ], f"{location} must keep the agent timeout section intact"
            assert lines.index("[task]") < agent_index < environment_index, (
                f"{location} must place [agent] between [task] and [environment]"
            )
