# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Extensible root entry point for ``vss``."""

from __future__ import annotations

import sys

from .registry import Command
from .registry import CommandRegistry
from .search_operations import SEARCH_OPERATIONS


def _search_handler(argv: list[str]) -> int:
    from .search import run as run_search

    if not argv or argv[0] in {"-h", "--help"}:
        operations = ",".join(SEARCH_OPERATIONS)
        sys.stdout.write(
            f"usage: vss search {{{operations}}} [options]\n\n"
            "Normal archive search: vss search run --help\n"
            "Lower-level primitives: vss search <embed|attribute> --help\n"
            "From the checkout: set VSS_REPO_ROOT, then run\n"
            '  uv run --project "$VSS_REPO_ROOT/services/agent" --no-dev vss search run ...\n'
        )
        return 0
    operation = argv[0]
    if operation not in SEARCH_OPERATIONS:
        sys.stderr.write(f"vss search: unknown operation {argv[0]!r}\n")
        return 2
    try:
        return run_search(operation, argv[1:])
    except SystemExit as error:
        # argparse owns help/usage rendering.  Convert its normal process exit
        # into the integer return contract of this root dispatcher.
        return error.code if isinstance(error.code, int) else 1


def _registry() -> CommandRegistry:
    registry = CommandRegistry()
    registry.register(Command(name="search", summary="Search indexed video", handler=_search_handler))
    return registry


def _write_help(registry: CommandRegistry) -> None:
    sys.stdout.write("usage: vss <command> [options]\n\nCommands:\n")
    for command in registry.commands():
        sys.stdout.write(f"  {command.name:<12} {command.summary}\n")


def main(argv: list[str] | None = None) -> int:
    """Run the root VSS CLI dispatcher."""
    args = list(sys.argv[1:] if argv is None else argv)
    registry = _registry()
    if not args or args[0] in {"-h", "--help"}:
        _write_help(registry)
        return 0
    command = registry.get(args[0])
    if command is None:
        sys.stderr.write(f"vss: unknown command {args[0]!r}\n")
        return 2
    return command.handler(args[1:])


__all__ = ["main"]
