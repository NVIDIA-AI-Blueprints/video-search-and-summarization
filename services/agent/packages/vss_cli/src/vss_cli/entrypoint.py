# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""First-party command groups, published through the plugin contract.

``search`` is mounted by the same ``vss.commands`` entry point a third party
would use. Dogfooding the contract keeps it honest: if the published extension
path regresses, the shipped CLI regresses with it.

Nothing here imports the search runtime. :func:`_operation` defers
``from .search import run`` into the command callback, so ``vss search --help``
resolves the group without pulling in Elasticsearch clients, embedders, or
their transitive imports.
"""

from __future__ import annotations

import click

from . import plugins
from .search_operations import SEARCH_OPERATIONS

_OPERATION_HELP = {
    "run": "Fusion archive search (the normal entry point)",
    "embed": "Embedding-similarity primitive",
    "attribute": "Attribute-filter primitive",
}


def _operation(name: str) -> click.Command:
    """Wrap one argparse-backed search operation as a Click subcommand.

    ``search.py`` owns 53 documented options and renders its own help. Rather
    than restate that surface in Click -- and risk drifting from the skill and
    eval contracts that quote it -- the operation keeps its parser and this
    command forwards argv verbatim.
    """

    @click.command(
        name=name,
        short_help=_OPERATION_HELP[name],
        add_help_option=False,
        context_settings={"ignore_unknown_options": True, "help_option_names": []},
    )
    @click.argument("argv", nargs=-1, type=click.UNPROCESSED)
    @click.pass_context
    def _command(ctx: click.Context, argv: tuple[str, ...]) -> None:
        from .search import run as run_search

        try:
            code = run_search(name, list(argv))
        except SystemExit as error:
            # argparse exits the process for --help and usage errors; convert
            # that back into an exit code Click can carry.
            code = error.code if isinstance(error.code, int) else 1
        ctx.exit(code)

    return _command


class _SearchGroup:
    """``vss search`` -- see :class:`vss_cli.plugins.CommandGroupSpec`."""

    api_version = plugins.API_VERSION
    name = "search"
    summary = "Search indexed video"

    def cli(self) -> click.Command:
        group = click.Group(
            name=self.name,
            help=(
                "Search indexed video.\n\n"
                "Normal archive search: vss search run --help\n"
                "Lower-level primitives: vss search <embed|attribute> --help"
            ),
            short_help=self.summary,
        )
        for operation in SEARCH_OPERATIONS:
            group.add_command(_operation(operation))
        return group


SEARCH = _SearchGroup()

__all__ = ["SEARCH"]
