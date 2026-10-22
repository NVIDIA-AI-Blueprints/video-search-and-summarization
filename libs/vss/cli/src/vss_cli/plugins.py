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
from typing import TYPE_CHECKING
from typing import Protocol
from typing import cast
from typing import runtime_checkable

if TYPE_CHECKING:
    from collections.abc import Sequence

    import click

#: Contract version. A group declaring a different major version is rejected at
#: load time with a diagnostic rather than being imported and half-mounted.
API_VERSION = 1

#: Entry-point group naming the importable command-group object.
COMMANDS_GROUP = "vss.commands"

#: Entry-point group carrying one-line summaries as raw strings (never loaded).
SUMMARIES_GROUP = "vss.command_summaries"

#: Comma-separated group names to skip, e.g. ``VSS_DISABLE_PLUGINS=acme,other``.
#: Mirrors pytest's ``-p no:name``: an operator needs a way to boot the CLI when
#: an installed plugin is actively breaking it.
DISABLE_ENV = "VSS_DISABLE_PLUGINS"


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


class PluginLoadError(Exception):
    """A discovered group could not be turned into a usable command."""


def _disabled() -> frozenset[str]:
    raw = os.environ.get(DISABLE_ENV, "")
    return frozenset(part.strip() for part in raw.split(",") if part.strip())


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
    """List installed command groups. Imports nothing."""
    disabled = _disabled()
    summaries = _summaries()
    refs: list[GroupRef] = []
    for ep in entry_points(group=COMMANDS_GROUP):
        if ep.name in disabled:
            continue
        dist = ep.dist.name if ep.dist is not None else None
        summary = summaries.get(ep.name) or (f"(provided by {dist})" if dist else "")
        refs.append(GroupRef(name=ep.name, summary=summary, value=ep.value, dist=dist))
    return sorted(refs, key=lambda r: r.name)


def load(name: str) -> CommandGroupSpec:
    """Import and validate one command group.

    Raises :class:`PluginLoadError` with a diagnostic naming the distribution;
    callers turn that into a broken-command placeholder so one bad plugin
    cannot stop the whole CLI from starting.
    """
    matches = [ep for ep in entry_points(group=COMMANDS_GROUP) if ep.name == name]
    if not matches:
        raise PluginLoadError(f"no command group named {name!r}")
    ep = matches[0]
    dist = ep.dist.name if ep.dist is not None else "unknown distribution"

    try:
        obj = ep.load()
    except Exception as exc:
        raise PluginLoadError(f"{name!r} (from {dist}) failed to import: {exc!r}") from exc

    declared = getattr(obj, "api_version", None)
    if declared != API_VERSION:
        raise PluginLoadError(
            f"{name!r} (from {dist}) declares api_version={declared!r}, but this vss requires {API_VERSION}"
        )
    if not callable(getattr(obj, "cli", None)):
        raise PluginLoadError(f"{name!r} (from {dist}) has no callable cli()")
    return cast("CommandGroupSpec", obj)


def summaries_for(refs: Sequence[GroupRef]) -> dict[str, str]:
    """Name -> summary map for help rendering."""
    return {ref.name: ref.summary for ref in refs}
