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
