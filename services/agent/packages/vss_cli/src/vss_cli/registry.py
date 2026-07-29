# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""The lazy root group for ``vss``.

``click.Group`` normally holds a dict of already-constructed subcommands, which
would mean importing every installed plugin on every invocation -- including
``vss --help`` and ``vss --version``. Overriding :meth:`list_commands` and
:meth:`get_command` moves that to a per-invocation cost: names come from entry
point metadata (no imports), and exactly one group is imported, only when it is
the one being run.

A group that fails to load becomes a :class:`BrokenCommand` rather than
propagating. ``vss --help`` still works, the other groups still run, and the
failure surfaces with the offending distribution named when that one command is
invoked. This is the ``click-plugins`` behaviour, reimplemented here: that
project is end-of-life (``Development Status :: 7 - Inactive``, last
substantive release 2019) and its published build still imports
``pkg_resources``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import click

from . import plugins

if TYPE_CHECKING:
    from collections.abc import Sequence


class BrokenCommand(click.Command):
    """Placeholder mounted when a command group cannot be loaded."""

    def __init__(self, name: str, message: str) -> None:
        super().__init__(
            name,
            short_help=f"[unavailable] {message}",
            help=(
                f"This command group could not be loaded.\n\n{message}\n\n"
                f"Reinstall or remove the offending package, or set "
                f"{plugins.DISABLE_ENV}={name} to hide it."
            ),
            # Swallow whatever the user typed. Without this, Click's parser
            # rejects the plugin's own options first ("No such option
            # '--sensor'"), hiding the actual reason the group is unavailable.
            params=[click.Argument(["ignored"], nargs=-1, type=click.UNPROCESSED)],
            add_help_option=False,
            context_settings={"ignore_unknown_options": True, "help_option_names": []},
        )
        self._message = message

    def invoke(self, ctx: click.Context) -> None:  # noqa: ARG002 - Click override signature
        raise click.ClickException(self._message)


class VssGroup(click.Group):
    """Root group that resolves subcommands from entry points on demand."""

    def list_commands(self, ctx: click.Context) -> list[str]:
        names = set(super().list_commands(ctx))
        names.update(ref.name for ref in plugins.discover())
        return sorted(names)

    def get_command(self, ctx: click.Context, cmd_name: str) -> click.Command | None:
        eager = super().get_command(ctx, cmd_name)
        if eager is not None:
            return eager
        if cmd_name not in {ref.name for ref in plugins.discover()}:
            return None
        try:
            spec = plugins.load(cmd_name)
            command = spec.cli()
        except plugins.PluginLoadError as exc:
            return BrokenCommand(cmd_name, str(exc))
        except Exception as exc:
            return BrokenCommand(cmd_name, f"{cmd_name!r} failed to build its CLI: {exc!r}")
        # Always name the command from the entry point, never from the callback
        # function name. Click 8.2 strips ``_command``/``_cmd``/``_group``/``_grp``
        # suffixes when deriving names, so a plugin's ``def deploy_command()``
        # would otherwise register as ``deploy``.
        command.name = cmd_name
        return command

    def format_commands(self, ctx: click.Context, formatter: click.HelpFormatter) -> None:
        """Render summaries from metadata so help stays import-free."""
        declared = plugins.summaries_for(plugins.discover())
        rows: list[tuple[str, str]] = []
        for name in self.list_commands(ctx):
            eager = click.Group.get_command(self, ctx, name)
            if eager is not None:
                rows.append((name, eager.get_short_help_str(limit=68)))
            else:
                rows.append((name, declared.get(name, "")))
        if rows:
            with formatter.section("Commands"):
                formatter.write_dl(rows)


def build_root(commands: Sequence[click.Command] = ()) -> VssGroup:
    """Build the root group, mounting any eagerly-provided commands."""

    @click.group(
        cls=VssGroup,
        context_settings={"help_option_names": ["-h", "--help"]},
    )
    @click.version_option(package_name="nvidia-vss-cli", prog_name="vss")
    def root() -> None:
        """NVIDIA VSS command-line interface."""

    for command in commands:
        root.add_command(command)
    return root
