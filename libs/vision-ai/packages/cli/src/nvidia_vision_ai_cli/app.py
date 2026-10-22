# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""The ``vision-ai`` root command: built-ins plus one command per installed tool."""

from __future__ import annotations

import logging
import sys
from typing import Any

import click

from . import plugins
from .configure import configure
from .exits import Exit
from .jobs import tool_command
from .memory_cmd import memory
from .output import broken_command
from .output import fail

_BUILTINS: dict[str, click.Command] = {"configure": configure, "memory": memory}


class VisionAiGroup(click.Group):
    def list_commands(self, ctx: click.Context) -> list[str]:  # noqa: ARG002
        return [*plugins.discover(), *sorted(_BUILTINS)]

    def get_command(self, ctx: click.Context, cmd_name: str) -> click.Command | None:  # noqa: ARG002
        if cmd_name in _BUILTINS:
            return _BUILTINS[cmd_name]
        if cmd_name not in plugins.discover():
            return None
        try:
            return tool_command(plugins.load(cmd_name))
        except Exception as exc:  # one broken plugin must not take down `vision-ai --help`
            return broken_command(cmd_name, str(exc))

    def main(self, *args: Any, standalone_mode: bool = True, **kwargs: Any) -> Any:
        """Run without click's standalone handling, so usage errors also print one JSON object."""
        try:
            rv = super().main(*args, standalone_mode=False, **kwargs)
        except click.UsageError as exc:
            fail(Exit.INVALID_INPUT, exc.format_message())
        except click.ClickException as exc:
            fail(Exit.ERROR, exc.format_message())
        except click.Abort:
            fail(Exit.ERROR, "aborted")
        if standalone_mode:
            raise SystemExit(rv if isinstance(rv, int) else 0)
        return rv


@click.group(cls=VisionAiGroup)
@click.option("-v", "--verbose", is_flag=True, help="Log to stderr at INFO.")
def cli(verbose: bool) -> None:
    """Vision AI harness tools. Every tool call is recorded in ~/.vision-ai/memory/main.db.

    \b
    Exit codes: 0 ok, 1 error, 2 invalid input, 3 configuration,
                4 backend unreachable, 5 not found, 6 backend error.
    """
    logging.basicConfig(level=logging.INFO if verbose else logging.WARNING, stream=sys.stderr)


def main() -> None:
    cli()
