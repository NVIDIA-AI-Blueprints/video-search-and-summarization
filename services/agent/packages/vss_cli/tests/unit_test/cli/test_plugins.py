# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Contract tests for entry-point command-group discovery."""

from __future__ import annotations

from dataclasses import dataclass
import subprocess
import sys
from typing import Any

import click
import pytest

import vss_cli as cli
from vss_cli import plugins
from vss_cli import registry


@dataclass
class _FakeDist:
    name: str


class _FakeEntryPoint:
    """Mimics the slice of importlib.metadata.EntryPoint that plugins.py uses."""

    def __init__(self, name: str, value: str, *, dist: str | None, payload: Any = None) -> None:
        self.name = name
        self.value = value
        self.dist = _FakeDist(dist) if dist else None
        self._payload = payload

    def load(self) -> Any:
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


def _patch_entry_points(monkeypatch: pytest.MonkeyPatch, commands: list[Any], summaries: list[Any]) -> None:
    def fake(*, group: str) -> list[Any]:
        if group == plugins.COMMANDS_GROUP:
            return commands
        if group == plugins.SUMMARIES_GROUP:
            return summaries
        return []

    monkeypatch.setattr(plugins, "entry_points", fake)


class _GoodGroup:
    api_version = plugins.API_VERSION
    name = "acme"
    summary = "Acme video operations"

    def cli(self) -> click.Command:
        return click.Group(name="acme")


# --------------------------------------------------------------------------
# discovery
# --------------------------------------------------------------------------


def test_search_is_registered_through_the_public_contract() -> None:
    """The first-party group uses the same entry point a third party would."""
    names = {ref.name for ref in plugins.discover()}
    assert "search" in names


def test_summary_is_read_without_importing_the_group(monkeypatch: pytest.MonkeyPatch) -> None:
    """Summaries are raw entry-point values, so prose round-trips intact."""
    boom = _FakeEntryPoint("acme", "acme:GROUP", dist="acme-vss", payload=AssertionError("imported!"))
    summary = _FakeEntryPoint("acme", "Tide tables, with commas & spaces", dist="acme-vss")
    _patch_entry_points(monkeypatch, [boom], [summary])

    (ref,) = plugins.discover()
    assert ref.summary == "Tide tables, with commas & spaces"


def test_group_without_declared_summary_falls_back_to_distribution(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_entry_points(monkeypatch, [_FakeEntryPoint("acme", "acme:GROUP", dist="acme-vss")], [])

    (ref,) = plugins.discover()
    assert ref.summary == "(provided by acme-vss)"


def test_disable_env_hides_a_group(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_entry_points(
        monkeypatch,
        [_FakeEntryPoint("acme", "acme:GROUP", dist="acme-vss"), _FakeEntryPoint("keep", "k:G", dist="k")],
        [],
    )
    monkeypatch.setenv(plugins.DISABLE_ENV, "acme")

    assert {ref.name for ref in plugins.discover()} == {"keep"}


# --------------------------------------------------------------------------
# loading and validation
# --------------------------------------------------------------------------


def test_load_returns_the_declared_group(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_entry_points(
        monkeypatch,
        [_FakeEntryPoint("acme", "acme:GROUP", dist="acme-vss", payload=_GoodGroup())],
        [],
    )

    assert plugins.load("acme").name == "acme"


def test_load_rejects_a_mismatched_api_version(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Old:
        api_version = plugins.API_VERSION + 1
        name = "acme"
        summary = "stale"

        def cli(self) -> click.Command:
            return click.Group(name="acme")

    _patch_entry_points(monkeypatch, [_FakeEntryPoint("acme", "a:G", dist="acme-vss", payload=_Old())], [])

    with pytest.raises(plugins.PluginLoadError) as excinfo:
        plugins.load("acme")
    message = str(excinfo.value)
    assert "acme-vss" in message
    assert str(plugins.API_VERSION) in message


def test_load_wraps_an_import_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    broken = _FakeEntryPoint("acme", "a:G", dist="acme-vss", payload=ImportError("no module named acme"))
    _patch_entry_points(monkeypatch, [broken], [])

    with pytest.raises(plugins.PluginLoadError) as excinfo:
        plugins.load("acme")
    assert "acme-vss" in str(excinfo.value)


def test_load_rejects_an_object_without_cli(monkeypatch: pytest.MonkeyPatch) -> None:
    class _NoCli:
        api_version = plugins.API_VERSION
        name = "acme"
        summary = "x"

    _patch_entry_points(monkeypatch, [_FakeEntryPoint("acme", "a:G", dist="d", payload=_NoCli())], [])

    with pytest.raises(plugins.PluginLoadError):
        plugins.load("acme")


# --------------------------------------------------------------------------
# failure isolation
# --------------------------------------------------------------------------


def test_a_broken_group_does_not_break_the_root(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    broken = _FakeEntryPoint("acme", "a:G", dist="acme-vss", payload=ImportError("boom"))
    _patch_entry_points(monkeypatch, [broken], [])

    assert cli.main(["--help"]) == 0
    assert "acme" in capsys.readouterr().out


def test_invoking_a_broken_group_reports_the_distribution(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    broken = _FakeEntryPoint("acme", "a:G", dist="acme-vss", payload=ImportError("boom"))
    _patch_entry_points(monkeypatch, [broken], [])

    assert cli.main(["acme"]) == 1
    assert "acme-vss" in capsys.readouterr().err


def test_broken_group_reports_the_load_error_even_with_plugin_options(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The load failure must win over Click's own "No such option" parse error."""
    broken = _FakeEntryPoint("acme", "a:G", dist="acme-vss", payload=ImportError("boom"))
    _patch_entry_points(monkeypatch, [broken], [])

    assert cli.main(["acme", "tides", "--sensor", "cam-west-77"]) == 1
    stderr = capsys.readouterr().err
    assert "acme-vss" in stderr
    assert "No such option" not in stderr


def test_broken_command_is_mounted_for_a_failing_cli_factory(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Explodes:
        api_version = plugins.API_VERSION
        name = "acme"
        summary = "x"

        def cli(self) -> click.Command:
            raise RuntimeError("cli() blew up")

    _patch_entry_points(monkeypatch, [_FakeEntryPoint("acme", "a:G", dist="d", payload=_Explodes())], [])

    root = registry.build_root()
    ctx = click.Context(root)
    assert isinstance(root.get_command(ctx, "acme"), registry.BrokenCommand)


def test_entry_point_name_wins_over_the_callback_name(monkeypatch: pytest.MonkeyPatch) -> None:
    """Click 8.2 strips _command/_group suffixes when deriving names."""

    class _Suffixed:
        api_version = plugins.API_VERSION
        name = "acme"
        summary = "x"

        def cli(self) -> click.Command:
            @click.group()
            def acme_group() -> None: ...

            return acme_group

    _patch_entry_points(monkeypatch, [_FakeEntryPoint("acme", "a:G", dist="d", payload=_Suffixed())], [])

    root = registry.build_root()
    command = root.get_command(click.Context(root), "acme")
    assert command is not None
    assert command.name == "acme"


# --------------------------------------------------------------------------
# laziness
# --------------------------------------------------------------------------


def test_root_help_does_not_import_the_search_runtime() -> None:
    """Run in a subprocess: an earlier test in this process may have imported it."""
    code = "import sys; import vss_cli; vss_cli.main(['--help']); sys.exit(1 if 'vss_cli.search.group' in sys.modules else 0)"
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=False)
    assert result.returncode == 0, f"vss --help imported the search runtime\n{result.stdout}{result.stderr}"
