# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""A broken plugin is reported, never fatal to help or to other tools."""

from __future__ import annotations

from importlib.metadata import EntryPoint
from typing import Any

from click.testing import CliRunner
import pytest

from nvidia_vision_ai_cli import plugins
from nvidia_vision_ai_cli.app import cli
from nvidia_vision_ai_cli.tool import Tool


class Boom(Tool):
    name = "boom"
    summary = "explodes"

    def __init__(self) -> None:
        raise RuntimeError("constructor failed")

    async def execute(self, inputs: Any, settings: Any) -> Any:
        raise NotImplementedError


class NoModels:
    """Not a Tool at all."""


@pytest.fixture
def broken_plugins(monkeypatch: pytest.MonkeyPatch) -> None:
    real = plugins.entry_points
    extra = [
        EntryPoint("boom", f"{__name__}:Boom", plugins.TOOLS_GROUP),
        EntryPoint("nomodels", f"{__name__}:NoModels", plugins.TOOLS_GROUP),
        EntryPoint("missing", "no_such_module:TOOL", plugins.TOOLS_GROUP),
    ]

    def fake(*, group: str) -> Any:
        found = list(real(group=group))
        return [*found, *extra] if group == plugins.TOOLS_GROUP else found

    monkeypatch.setattr(plugins, "entry_points", fake)


@pytest.mark.parametrize("args", [["--help"], ["configure", "--help"]])
def test_help_survives_broken_plugins(home: Any, broken_plugins: None, args: list[str]) -> None:
    result = CliRunner().invoke(cli, args)
    assert result.exit_code == 0, result.output
    assert "vlm" in result.stdout
    assert "(unavailable)" in result.stdout


@pytest.mark.parametrize(
    ("name", "match"), [("boom", "constructor failed"), ("nomodels", "not a"), ("missing", "failed to load")]
)
def test_running_a_broken_plugin_reports_why(home: Any, broken_plugins: None, name: str, match: str) -> None:
    from conftest import run

    result, out = run(name)
    assert result.exit_code == 1
    assert match in out["error"]["message"]
    result, out = run("configure", name)
    assert result.exit_code == 1


def test_vlm_still_loads_next_to_broken_plugins(broken_plugins: None) -> None:
    assert plugins.load("vlm").name == "vlm"
