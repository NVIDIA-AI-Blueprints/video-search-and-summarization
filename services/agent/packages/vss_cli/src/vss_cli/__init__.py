# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Extensible root entry point for ``vss``.

Command groups are discovered from the ``vss.commands`` entry-point group (see
:mod:`vss_cli.plugins`); none are imported until one is invoked. The
first-party ``search`` group is registered the same way as any third-party
group, so the published contract is the one this package itself uses.
"""

from __future__ import annotations

import sys

import click

from .config import ConfigError
from .exits import Exit
from .registry import build_root


def main(argv: list[str] | None = None) -> int:
    """Run the root VSS CLI dispatcher.

    Returns an exit code rather than exiting, so the process boundary stays in
    the console script and the dispatcher remains callable from tests.
    """
    args = list(sys.argv[1:] if argv is None else argv)
    root = build_root()
    try:
        # standalone_mode=False keeps Click from calling sys.exit() itself,
        # which is what preserves the ``main() -> int`` contract. In this mode
        # Click *returns* the code from ctx.exit() instead of raising Exit, so
        # the return value is the exit code and must not be discarded.
        result = root.main(args=args, prog_name="vss", standalone_mode=False)
    except click.exceptions.Exit as exit_signal:  # pragma: no cover - defensive
        return int(exit_signal.exit_code)
    except ConfigError as error:
        # A missing or stale deployment is exit 4 for every group, with the
        # remedy in the message -- not a traceback. Handled here rather than
        # per-group so no group can forget.
        sys.stderr.write(f"vss: {error}\n")
        return int(Exit.CONFIGURATION)
    except click.Abort:
        sys.stderr.write("vss: aborted\n")
        return 130
    except click.ClickException as error:
        # Catch the base class, not specific subclasses: Click 8.4 introduced
        # NoSuchCommand for unknown commands, changing the exception type that
        # a bare `vss bogus` produces.
        error.show()
        return int(error.exit_code)
    return result if isinstance(result, int) else 0


__all__ = ["main"]
