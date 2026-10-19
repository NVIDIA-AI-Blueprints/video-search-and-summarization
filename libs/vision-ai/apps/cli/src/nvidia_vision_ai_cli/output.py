# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""stdout is exactly one JSON object per command; human messages go to stderr."""

from __future__ import annotations

import json
from typing import Any
from typing import NoReturn

import click

from .exits import Exit


def emit(payload: dict[str, Any]) -> None:
    click.echo(json.dumps(payload, ensure_ascii=False))


def fail(code: Exit, message: str) -> NoReturn:
    """Report an error that happened before (or outside) a job record and exit."""
    click.echo(f"vision-ai: {message}", err=True)
    emit({"error": {"code": code.token, "message": message}})
    raise SystemExit(int(code))


def broken_command(name: str, message: str) -> click.Command:
    """Stand-in for a tool that failed to load: listed in help, and running it reports why."""

    def callback() -> None:
        fail(Exit.ERROR, message)

    return click.Command(name, callback=callback, help=f"(unavailable) {message}", short_help="(unavailable)")
