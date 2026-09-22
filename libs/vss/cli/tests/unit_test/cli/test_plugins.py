# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Contract tests for entry-point command-group discovery."""

from __future__ import annotations

from dataclasses import dataclass
import subprocess
import sys
from typing import TYPE_CHECKING
from typing import Any

import click
from click.testing import CliRunner
import pytest

import vss_cli as cli
from vss_cli import config as config_mod
from vss_cli import plugins
from vss_cli import registry

if TYPE_CHECKING:
    from pathlib import Path


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


def test_memory_is_registered_through_the_public_contract() -> None:
    """The cross-group memory domain mounts through the same lazy registry."""
    names = {ref.name for ref in plugins.discover()}
    assert "memory" in names


def test_analytics_is_registered_through_the_public_contract() -> None:
    names = {ref.name for ref in plugins.discover()}
    assert "analytics" in names


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


def test_root_help_does_not_import_the_analytics_group() -> None:
    code = "import sys; import vss_cli; vss_cli.main(['--help']); sys.exit(1 if 'vss_cli.analytics.group' in sys.modules else 0)"
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=False)
    assert result.returncode == 0, f"vss --help imported analytics\n{result.stdout}{result.stderr}"


# --------------------------------------------------------------------------
# on-disk groups (`~/.vss/plugins/<name>/plugin.toml`)
# --------------------------------------------------------------------------


def _drop_plugin(
    root: Path,
    name: str,
    *,
    manifest: str | None = None,
    module: str = "",
) -> Path:
    """Write one on-disk group, the way an optimisation agent would."""
    directory = root / name
    directory.mkdir(parents=True)
    if manifest is None:
        manifest = f'summary = "{name} operations"\ngroup = "{name}_plugin:GROUP"\n'
    (directory / plugins.MANIFEST_NAME).write_text(manifest, encoding="utf-8")
    if module:
        (directory / f"{name}_plugin.py").write_text(module, encoding="utf-8")
    return directory


_WORKING_PLUGIN = """
import click


class _Group:
    api_version = {version}
    name = "{name}"
    summary = "{name} operations"

    def cli(self):
        @click.command(name="{name}")
        def command():
            click.echo("{name} ran")

        return command


GROUP = _Group()
"""


@pytest.fixture
def plugin_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "plugins"
    root.mkdir()
    monkeypatch.setenv(plugins.PLUGIN_PATH_ENV, str(root))
    return root


def test_plugin_root_defaults_under_the_config_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """One user-level directory, resolved the way `config.json` already is."""
    monkeypatch.delenv(plugins.PLUGIN_PATH_ENV, raising=False)
    monkeypatch.setenv(config_mod.CONFIG_HOME_ENV, str(tmp_path / "cfg"))
    assert plugins.plugin_root() == tmp_path / "cfg" / "plugins"


def test_absent_plugin_root_is_not_an_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Nothing has ever been dropped, which is the ordinary case."""
    monkeypatch.setenv(plugins.PLUGIN_PATH_ENV, str(tmp_path / "never-created"))
    assert [ref.name for ref in plugins.discover() if ref.source is not None] == []


def test_a_dropped_group_is_discovered_without_importing_it(plugin_root: Path) -> None:
    """The summary is read as data, so `vss --help` costs no import.

    The module is deliberately one that raises: if discovery imported it, this
    would fail here instead of at invocation.
    """
    _drop_plugin(plugin_root, "acme", module="raise RuntimeError('imported too early')\n")
    ref = next(ref for ref in plugins.discover() if ref.name == "acme")
    assert ref.summary == "acme operations"
    assert ref.source == plugin_root / "acme"
    assert ref.dist is None


def test_a_dropped_group_loads_and_runs(plugin_root: Path) -> None:
    """The whole point: drop a directory, and the next process mounts it."""
    _drop_plugin(plugin_root, "acme", module=_WORKING_PLUGIN.format(name="acme", version=plugins.API_VERSION))
    spec = plugins.load("acme")
    assert spec.name == "acme"
    result = CliRunner().invoke(spec.cli(), [])
    assert result.exit_code == 0, result.output
    assert "acme ran" in result.output


def test_a_dropped_group_is_held_to_the_api_version(plugin_root: Path) -> None:
    """Same guard as a wheel: refused at load, not half-mounted."""
    _drop_plugin(plugin_root, "oldacme", module=_WORKING_PLUGIN.format(name="oldacme", version=plugins.API_VERSION + 1))
    with pytest.raises(plugins.PluginLoadError) as excinfo:
        plugins.load("oldacme")
    assert "api_version" in str(excinfo.value)
    assert str(plugin_root / "oldacme") in str(excinfo.value)


def test_a_malformed_manifest_does_not_break_discovery(plugin_root: Path) -> None:
    """One bad drop must not stop `vss --help` listing everything else."""
    _drop_plugin(plugin_root, "acme", manifest="this is not toml {{{\n")
    ref = next(ref for ref in plugins.discover() if ref.name == "acme")
    assert ref.unavailable is not None
    assert "will not parse" in ref.unavailable
    with pytest.raises(plugins.PluginLoadError):
        plugins.load("acme")


def test_a_manifest_without_a_group_key_says_so(plugin_root: Path) -> None:
    _drop_plugin(plugin_root, "acme", manifest='summary = "no importable"\n')
    with pytest.raises(plugins.PluginLoadError) as excinfo:
        plugins.load("acme")
    assert "group" in str(excinfo.value)


def test_a_dropped_group_may_not_shadow_an_installed_one(plugin_root: Path) -> None:
    """`search` is first-party; a dropped file of the same name is refused.

    Naming both sources is the point -- a silent win in either direction is the
    failure mode nobody would think to look for.
    """
    _drop_plugin(plugin_root, "search", module=_WORKING_PLUGIN.format(name="search", version=plugins.API_VERSION))
    with pytest.raises(plugins.PluginLoadError) as excinfo:
        plugins.load("search")
    message = str(excinfo.value)
    assert "nvidia-vss-cli" in message
    assert str(plugin_root / "search") in message


def test_disable_env_also_hides_a_dropped_group(plugin_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The operator escape hatch must not have a blind spot."""
    _drop_plugin(plugin_root, "acme", module=_WORKING_PLUGIN.format(name="acme", version=plugins.API_VERSION))
    monkeypatch.setenv(plugins.DISABLE_ENV, "acme")
    assert "acme" not in {ref.name for ref in plugins.discover()}


def test_a_dropped_group_reaches_the_root_dispatcher(plugin_root: Path) -> None:
    """End to end: `vss --help` lists it and `vss rootacme` runs it."""
    _drop_plugin(plugin_root, "rootacme", module=_WORKING_PLUGIN.format(name="rootacme", version=plugins.API_VERSION))
    root = registry.build_root()
    listed = CliRunner().invoke(root, ["--help"])
    assert "rootacme" in listed.output
    assert "rootacme operations" in listed.output
    ran = CliRunner().invoke(root, ["rootacme"])
    assert ran.exit_code == 0, ran.output
    assert "rootacme ran" in ran.output


def test_a_plugin_module_does_not_shadow_an_installed_one(plugin_root: Path) -> None:
    """Loading by path keeps a plugin out of the ordinary module namespace.

    Going through `sys.path` would let a plugin file named `json.py` win over
    the stdlib for the rest of the process, and would cache the plugin under a
    name the next plugin could collide with.
    """
    import json as stdlib_json

    _drop_plugin(
        plugin_root,
        "shadow",
        manifest='summary = "shadowy"\ngroup = "json:GROUP"\n',
        module=_WORKING_PLUGIN.format(name="shadow", version=plugins.API_VERSION),
    )
    (plugin_root / "shadow" / "json.py").write_text(
        _WORKING_PLUGIN.format(name="shadow", version=plugins.API_VERSION), encoding="utf-8"
    )
    spec = plugins.load("shadow")
    assert spec.name == "shadow"
    # The real json module is untouched, and nothing was added under its name.
    assert sys.modules["json"] is stdlib_json
    assert hasattr(stdlib_json, "loads")


def test_two_plugins_sharing_a_module_basename_stay_separate(plugin_root: Path) -> None:
    """Same file name in two directories must not serve the same GROUP twice.

    A fresh process per call hides this, but one pytest process -- or any
    embedding host -- would get plugin A's command under plugin B's name.
    """
    for name in ("alpha", "beta"):
        directory = plugin_root / name
        directory.mkdir(parents=True)
        (directory / plugins.MANIFEST_NAME).write_text(
            f'summary = "{name} ops"\ngroup = "shared:GROUP"\n', encoding="utf-8"
        )
        (directory / "shared.py").write_text(
            _WORKING_PLUGIN.format(name=name, version=plugins.API_VERSION), encoding="utf-8"
        )

    assert plugins.load("alpha").name == "alpha"
    assert plugins.load("beta").name == "beta", "the second plugin must not be served the first's GROUP"


def test_a_manifest_naming_a_missing_module_says_so(plugin_root: Path) -> None:
    _drop_plugin(plugin_root, "absent", manifest='summary = "x"\ngroup = "nope:GROUP"\n')
    with pytest.raises(plugins.PluginLoadError) as excinfo:
        plugins.load("absent")
    assert "not in" in str(excinfo.value)
