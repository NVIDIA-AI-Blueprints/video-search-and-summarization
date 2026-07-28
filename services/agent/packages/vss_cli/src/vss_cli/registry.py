# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Command registration for the extensible ``vss`` root."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

CommandHandler = Callable[[list[str]], int]


@dataclass(frozen=True)
class Command:
    """A top-level CLI domain."""

    name: str
    summary: str
    handler: CommandHandler


class CommandRegistry:
    """Registry for top-level VSS CLI domains."""

    def __init__(self) -> None:
        self._commands: dict[str, Command] = {}

    def register(self, command: Command) -> None:
        if command.name in self._commands:
            raise ValueError(f"duplicate vss command: {command.name}")
        self._commands[command.name] = command

    def get(self, name: str) -> Command | None:
        return self._commands.get(name)

    def commands(self) -> tuple[Command, ...]:
        return tuple(sorted(self._commands.values(), key=lambda command: command.name))
