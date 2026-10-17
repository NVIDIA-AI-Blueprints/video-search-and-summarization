# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Entry-point discovery for ``vss`` command groups.

A command group is a top-level domain of the CLI (``search``, ``alerts``, ...).
First-party groups ship in this distribution; third parties add their own by
declaring an entry point, with no change to this package:

.. code-block:: toml

    [project.entry-points."vss.commands"]
    acme = "acme_vss.entrypoint:GROUP"

    [project.entry-points."vss.command_summaries"]
    acme = "Acme video operations"

Two entry-point groups, deliberately. ``vss.commands`` names the object to
import; ``vss.command_summaries`` carries the one-line help as *data*. Entry
point values are opaque strings until ``.load()`` is called, so the summaries
group is readable without importing anything -- which is what lets ``vss
--help`` list every group, first- and third-party, while still importing only
the group actually being invoked.

Summaries are declared per command, not per distribution. Reading
``dist.metadata["Summary"]`` would be simpler but gives every group from one
wheel the same blurb, which is wrong as soon as a distribution ships two.
"""

from __future__ import annotations

from dataclasses import dataclass
from importlib.metadata import entry_points
import os
from pathlib import Path
import sys
import tomllib
from typing import TYPE_CHECKING
from typing import Protocol
from typing import cast
from typing import runtime_checkable

if TYPE_CHECKING:
    from collections.abc import Sequence

    import click

#: Contract version. A group declaring a different major version is rejected at
#: load time with a diagnostic rather than being imported and half-mounted.
#: Kept in step with :data:`vss_cli.group.API_VERSION`, which is what a group
#: declares; this module is where that declaration is checked.
API_VERSION = 2

#: Entry-point group naming the importable command-group object.
COMMANDS_GROUP = "vss.commands"

#: Entry-point group carrying one-line summaries as raw strings (never loaded).
SUMMARIES_GROUP = "vss.command_summaries"

#: Comma-separated group names to skip, e.g. ``VSS_DISABLE_PLUGINS=acme,other``.
#: Mirrors pytest's ``-p no:name``: an operator needs a way to boot the CLI when
#: an installed plugin is actively breaking it.
DISABLE_ENV = "VSS_DISABLE_PLUGINS"

#: Directory scanned for on-disk groups, and the variable that replaces it.
#: Single directory rather than a path list, mirroring how ``config.py``
#: resolves ``~/.vss``: one plugin root per process needs no precedence rules
#: and has no same-name-in-two-roots case to answer.
PLUGIN_PATH_ENV = "VSS_PLUGIN_PATH"
PLUGIN_DIR_NAME = "plugins"

#: Manifest filename inside each ``<root>/<name>/`` directory.
MANIFEST_NAME = "plugin.toml"


@runtime_checkable
class CommandGroupSpec(Protocol):
    """What a ``vss.commands`` entry point must resolve to.

    Intentionally minimal: a name, a summary, and a factory returning the Click
    command to mount. The factory is called only when the group is invoked, so
    a group is free to do its expensive imports inside it.
    """

    #: Must equal :data:`API_VERSION`; anything else is refused.
    api_version: int

    #: Group name as it appears in ``vss <name> ...``.
    name: str

    #: One-line help. Authoritative once loaded; the ``vss.command_summaries``
    #: entry point is the lazy stand-in used before that.
    summary: str

    def cli(self) -> click.Command:
        """Build and return the Click command for this group."""
        ...


@dataclass(frozen=True)
class GroupRef:
    """A discovered group that has not been imported yet."""

    name: str
    summary: str
    value: str
    dist: str | None
    #: Directory the manifest was read from, for an on-disk group. None for an
    #: installed one. Carried so ``load`` can import from it, and so a clash
    #: with an installed name can say where the other side came from.
    source: Path | None = None
    #: Why this group cannot be loaded, when discovery already knows: a
    #: manifest that will not parse, or one declaring no importable.
    #: ``load`` raises it rather than guessing.
    unavailable: str | None = None


class PluginLoadError(Exception):
    """A discovered group could not be turned into a usable command."""


def _disabled() -> frozenset[str]:
    raw = os.environ.get(DISABLE_ENV, "")
    return frozenset(part.strip() for part in raw.split(",") if part.strip())


def plugin_root() -> Path:
    """Where on-disk groups live: ``$VSS_PLUGIN_PATH`` or ``~/.vss/plugins``."""
    override = os.environ.get(PLUGIN_PATH_ENV)
    if override:
        return Path(override)
    from . import config as config_mod

    return config_mod.config_home() / PLUGIN_DIR_NAME


def _manifests() -> list[GroupRef]:
    """Read every ``<root>/<name>/plugin.toml``. Imports nothing.

    The directory name *is* the group name, so the manifest carries only what
    the filesystem cannot say: a one-line ``summary`` and the ``module:attr``
    to import. ``summary`` is data for the same reason the
    ``vss.command_summaries`` entry point is: ``vss --help`` lists every group
    without importing any, and an on-disk group must not be the one exception
    that makes help pay for an import -- or lets a broken third-party module
    break help for everything else.
    """
    root = plugin_root()
    try:
        entries = sorted(root.iterdir())
    except (OSError, ValueError):
        return []
    refs: list[GroupRef] = []
    for directory in entries:
        manifest = directory / MANIFEST_NAME
        if not manifest.is_file():
            continue
        try:
            declared = tomllib.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
            # A malformed manifest becomes a broken command, not a dead CLI:
            # the reason shows up when that group is invoked.
            refs.append(
                GroupRef(
                    name=directory.name,
                    summary="",
                    value="",
                    dist=None,
                    source=directory,
                    unavailable=f"its {MANIFEST_NAME} will not parse: {error}",
                )
            )
            continue
        # The directory *is* the name. A `name` key would let two directories
        # claim one mount point, which is the collision this cannot now have.
        name = directory.name
        value = declared.get("group")
        refs.append(
            GroupRef(
                name=name,
                summary=str(declared.get("summary") or ""),
                value=str(value) if value else "",
                dist=None,
                source=directory,
                unavailable=None if value else f'its {MANIFEST_NAME} declares no `group = "module:attr"`',
            )
        )

    return refs


def _summaries() -> dict[str, str]:
    """Read declared summaries without importing a single plugin module."""
    out: dict[str, str] = {}
    for ep in entry_points(group=SUMMARIES_GROUP):
        # ``.value`` is the raw right-hand side of the entry point. It is only
        # parsed as ``module:attr`` by ``.load()``, which is never called here,
        # so arbitrary prose (spaces, commas, ampersands) round-trips intact.
        out[ep.name] = ep.value.strip()
    return out


def discover() -> list[GroupRef]:
    """List command groups, installed and on disk. Imports nothing.

    An on-disk group whose name is already installed is a hard error rather
    than a silent override in either direction: the whole point of the plugin
    directory is that something else writes into it, and a dropped file that
    quietly replaced ``search`` is the worst outcome to debug. The collision is
    reported as a broken command naming both sides, so the rest of the CLI
    still runs.
    """
    disabled = _disabled()
    summaries = _summaries()
    refs: list[GroupRef] = []
    installed: dict[str, GroupRef] = {}
    for ep in entry_points(group=COMMANDS_GROUP):
        dist = ep.dist.name if ep.dist is not None else None
        summary = summaries.get(ep.name) or (f"(provided by {dist})" if dist else "")
        installed[ep.name] = GroupRef(name=ep.name, summary=summary, value=ep.value, dist=dist)
    refs.extend(installed.values())

    for ref in _manifests():
        clash = installed.get(ref.name)
        if clash is not None:
            refs = [existing for existing in refs if existing.name != ref.name]
            refs.append(
                GroupRef(
                    name=ref.name,
                    summary=clash.summary,
                    value=(
                        f"<name collision: installed by {clash.dist or 'an unknown distribution'} "
                        f"and present at {ref.source}>"
                    ),
                    dist=clash.dist,
                    source=ref.source,
                )
            )
            continue
        refs.append(ref)
    return sorted((ref for ref in refs if ref.name not in disabled), key=lambda r: r.name)


def load(name: str) -> CommandGroupSpec:
    """Import and validate one command group.

    Raises :class:`PluginLoadError` with a diagnostic naming the distribution
    or directory; callers turn that into a broken-command placeholder so one
    bad plugin cannot stop the whole CLI from starting.
    """
    ref = next((candidate for candidate in discover() if candidate.name == name), None)
    if ref is None:
        raise PluginLoadError(f"no command group named {name!r}")
    if ref.unavailable is not None:
        raise PluginLoadError(f"{name!r} cannot be loaded: {ref.unavailable}")
    if ref.source is not None and ref.dist is not None:
        raise PluginLoadError(
            f"{name!r} is both installed (by {ref.dist}) and present at {ref.source / MANIFEST_NAME}; "
            f"remove one, or set {DISABLE_ENV}={name} to hide it"
        )

    if ref.source is not None:
        origin = f"{ref.source / MANIFEST_NAME}"
        module_attr = ref.value
        if ":" not in module_attr:
            raise PluginLoadError(f'{name!r} (from {origin}) declares no importable `group = "module:attr"`')
        module_name, _, attribute = module_attr.partition(":")
        # The plugin directory itself goes on the path, so a dropped group is
        # importable without being installed. Appended, not prepended: an
        # on-disk group must not shadow an installed module of the same name.
        try:
            import importlib.util

            module_path = ref.source / f"{module_name.replace('.', '/')}.py"
            if not module_path.is_file():
                raise PluginLoadError(
                    f"{name!r} (from {origin}) declares module {module_name!r}, which is not in {ref.source}"
                )
            # Private, per-plugin module name, loaded straight from the file.
            # Going through sys.path would let an installed module of the same
            # basename win, and would cache this one under a name the next
            # plugin could collide with -- serving plugin A's GROUP for
            # plugin B in the same process.
            qualified = f"_vss_plugin_{name}_{module_name}"
            spec = importlib.util.spec_from_file_location(qualified, module_path)
            if spec is None or spec.loader is None:
                raise PluginLoadError(f"{name!r} (from {origin}) could not be loaded from {module_path}")
            module = importlib.util.module_from_spec(spec)
            # Registered before exec so the module can import itself by name;
            # keyed on the private name, so nothing else can be shadowed.
            sys.modules[qualified] = module
            spec.loader.exec_module(module)
            obj = getattr(module, attribute)
        except PluginLoadError:
            raise
        except Exception as exc:
            raise PluginLoadError(f"{name!r} (from {origin}) failed to import: {exc!r}") from exc
    else:
        matches = [ep for ep in entry_points(group=COMMANDS_GROUP) if ep.name == name]
        if not matches:  # pragma: no cover - discover() already resolved it
            raise PluginLoadError(f"no command group named {name!r}")
        ep = matches[0]
        origin = ep.dist.name if ep.dist is not None else "unknown distribution"
        try:
            obj = ep.load()
        except Exception as exc:
            raise PluginLoadError(f"{name!r} (from {origin}) failed to import: {exc!r}") from exc

    declared = getattr(obj, "api_version", None)
    if declared != API_VERSION:
        raise PluginLoadError(
            f"{name!r} (from {origin}) declares api_version={declared!r}, but this vss requires {API_VERSION}"
        )
    if not callable(getattr(obj, "cli", None)):
        raise PluginLoadError(f"{name!r} (from {origin}) has no callable cli()")
    return cast("CommandGroupSpec", obj)


def summaries_for(refs: Sequence[GroupRef]) -> dict[str, str]:
    """Name -> summary map for help rendering."""
    return {ref.name: ref.summary for ref in refs}
