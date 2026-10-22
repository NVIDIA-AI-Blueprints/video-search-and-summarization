# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""``vision-ai configure <tool> show|set|unset|check`` -- edit ``~/.vision-ai/<tool>/config.json``."""

from __future__ import annotations

import asyncio
import sys
from typing import TYPE_CHECKING
from typing import Any

import click
from pydantic import ValidationError

from nvidia_vision_ai.tool import Tool
from nvidia_vision_ai.tool import public_settings
from nvidia_vision_ai.tool import secret_fields

from . import plugins
from .exits import Exit
from .jobs import exit_for
from .output import broken_command
from .output import emit
from .output import fail
from .runtime import ConfigError
from .runtime import InputError
from .runtime import Runtime
from .runtime import env_name
from .runtime import storable
from .runtime import validation_message

if TYPE_CHECKING:
    from pathlib import Path


def _masked(tool: Tool, values: dict[str, Any]) -> dict[str, Any]:
    secrets = secret_fields(tool.Settings)
    return {k: ("***" if k in secrets and v is not None else v) for k, v in values.items()}


def _key(tool: Tool, raw: str) -> str:
    key = raw.strip().replace("-", "_")
    if key not in tool.Settings.model_fields:
        known = ", ".join(tool.Settings.model_fields)
        fail(Exit.INVALID_INPUT, f"{tool.name} has no setting {raw!r} (known: {known})")
    return key


def _parse_pairs(tool: Tool, pairs: tuple[str, ...]) -> dict[str, str]:
    """``KEY=VALUE`` pairs; a secret must be ``KEY=-`` and is read from stdin, never argv."""
    secrets = secret_fields(tool.Settings)
    updates: dict[str, str] = {}
    for pair in pairs:
        raw, sep, value = pair.partition("=")
        if not sep:
            fail(Exit.INVALID_INPUT, f"expected KEY=VALUE, got {pair!r}")
        key = _key(tool, raw)
        if key in secrets:
            if value != "-":
                fail(Exit.INVALID_INPUT, f"{key} is a secret: pass `{key}=-` and pipe the value on stdin")
            value = sys.stdin.read().strip()
        updates[key] = value
    return updates


def _read_or_fail(runtime: Runtime, tool: Tool) -> dict[str, Any]:
    try:
        return runtime.read_config(tool.name)
    except ConfigError as exc:
        fail(Exit.CONFIGURATION, str(exc))


def _write_or_fail(runtime: Runtime, tool: Tool, values: dict[str, Any]) -> Path:
    try:
        return runtime.write_config(tool.name, values)
    except ConfigError as exc:
        fail(Exit.CONFIGURATION, str(exc))


def _settings_or_fail(runtime: Runtime, tool: Tool) -> Any:
    try:
        return runtime.settings(tool)
    except (ConfigError, InputError) as exc:
        code, message = exit_for(exc)
        fail(code, message)


def tool_group(tool: Tool) -> click.Group:
    group = click.Group(tool.name, help=f"Configure `vision-ai {tool.name}`.")

    @group.command("show")
    def show() -> None:
        """Print the config file and the effective settings (file + env), secrets masked."""
        runtime = Runtime.from_env()
        settings = _settings_or_fail(runtime, tool)
        secrets_set = sorted(name for name in secret_fields(tool.Settings) if getattr(settings, name) is not None)
        emit(
            {
                "tool": tool.name,
                "path": str(runtime.config_path(tool.name)),
                "file": _masked(tool, runtime.read_config(tool.name)),
                "env": sorted(env_name(tool.name, k) for k in runtime.env_values(tool)),
                "effective": {**public_settings(settings), **dict.fromkeys(secrets_set, "***")},
            }
        )

    @group.command("set")
    @click.argument("pairs", nargs=-1, required=True, metavar="KEY=VALUE...")
    def set_(pairs: tuple[str, ...]) -> None:
        """Set one or more settings, validated before the file is written.

        A secret (e.g. api_key) is never taken from the command line: pass
        `api_key=-` and pipe the value on stdin.
        """
        runtime = Runtime.from_env()
        merged = {**_read_or_fail(runtime, tool), **_parse_pairs(tool, pairs)}
        try:
            validated = tool.Settings.model_validate(merged)
        except ValidationError as exc:
            fail(Exit.INVALID_INPUT, validation_message(exc))
        path = _write_or_fail(runtime, tool, storable(validated, set(merged)))
        emit({"tool": tool.name, "path": str(path), "file": _masked(tool, runtime.read_config(tool.name))})

    @group.command("unset")
    @click.argument("keys", nargs=-1, required=True)
    def unset(keys: tuple[str, ...]) -> None:
        """Remove settings from the file so defaults (or env) apply."""
        runtime = Runtime.from_env()
        current = _read_or_fail(runtime, tool)
        for raw in keys:
            current.pop(_key(tool, raw), None)
        path = _write_or_fail(runtime, tool, current)
        emit({"tool": tool.name, "path": str(path), "file": _masked(tool, current)})

    @group.command("check")
    def check() -> None:
        """Probe the configured backend."""
        settings = _settings_or_fail(Runtime.from_env(), tool)
        try:
            report = asyncio.run(tool.check(settings))
        except Exception as exc:
            code, message = exit_for(exc)
            fail(code, message)
        emit({"tool": tool.name, "ok": True, **report})

    return group


class ConfigureGroup(click.Group):
    """One subgroup per installed tool, resolved lazily like the tools themselves."""

    def list_commands(self, ctx: click.Context) -> list[str]:  # noqa: ARG002
        return plugins.discover()

    def get_command(self, ctx: click.Context, cmd_name: str) -> click.Command | None:  # noqa: ARG002
        if cmd_name not in plugins.discover():
            return None
        try:
            return tool_group(plugins.load(cmd_name))
        except Exception as exc:  # one broken plugin must not take down `vision-ai configure --help`
            return broken_command(cmd_name, str(exc))


configure = ConfigureGroup(
    "configure", help="Show, set and check per-tool settings in ~/.vision-ai/<tool>/config.json."
)
