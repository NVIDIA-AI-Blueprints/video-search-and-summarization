# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""VSS Hermes plugin: a single tool, ``vss_cli``.

Mirrors the OpenClaw plugin's ``vss_cli`` tool (``../../.openclaw/plugin/src/index.ts``):
same name, same {args, cwd, timeoutSec} parameter schema, the pinned ``vss``
CLI run as a no-shell subprocess (``subprocess.run([bin, *args], ...)``, never
``shell=True``), the same 200,000-char per-stream output cap, and the same
{command, exitCode, signal, timedOut, stdout, stderr, truncated} result shape.
A skill that says "call the vss_cli tool" works unchanged on either harness.

No platform adapter, so registration goes through ``register(ctx)`` directly
rather than the deferred ``tools.py``/``provides_tools`` path plugins with a
heavy adapter SDK use (see hermes_cli/plugins.py) — importing this module has
no cost worth deferring.
"""

from __future__ import annotations

import os
import subprocess
from typing import Any, Optional

__all__ = ["register"]

_MAX_CAPTURE = 200_000
_DEFAULT_TIMEOUT_SEC = 600

_SCHEMA = {
    "type": "function",
    "function": {
        "name": "vss_cli",
        "description": (
            "Run the NVIDIA VSS command-line client (`vss`) against the configured VSS "
            "deployment. Pass the subcommand and flags as an argument array; the VSS "
            "skills describe which subcommands to use. Returns exit code, stdout and stderr."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "args": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        'Arguments after `vss`, one per element, for example '
                        '["summarize", "--help"] or ["ask", "--file", "clip.mp4", "what happens?"].'
                    ),
                },
                "cwd": {
                    "type": "string",
                    "description": "Working directory for the call. Defaults to the process cwd.",
                },
                "timeoutSec": {
                    "type": "integer",
                    "minimum": 1,
                    "description": "Seconds before the call is killed.",
                },
            },
            "required": ["args"],
            "additionalProperties": False,
        },
    },
}


def _clip(text: str) -> tuple[str, bool]:
    if len(text) <= _MAX_CAPTURE:
        return text, False
    return text[:_MAX_CAPTURE] + "\n…[truncated]", True


def vss_cli_tool(args: dict, **_kw: Any) -> dict:
    argv = args.get("args") or []
    cwd: Optional[str] = args.get("cwd")
    timeout_sec = args.get("timeoutSec") or int(
        os.environ.get("VSS_CLI_DEFAULT_TIMEOUT_SEC", _DEFAULT_TIMEOUT_SEC)
    )
    bin_path = os.environ.get("VSS_BIN", "/usr/local/bin/vss")
    command = " ".join([bin_path, *argv])

    try:
        proc = subprocess.run(
            [bin_path, *argv],
            cwd=cwd,
            timeout=timeout_sec,
            capture_output=True,
            text=True,
            shell=False,
        )
        out, out_truncated = _clip(proc.stdout or "")
        err, err_truncated = _clip(proc.stderr or "")
        return {
            "command": command,
            "exitCode": proc.returncode,
            "signal": None,
            "timedOut": False,
            "stdout": out,
            "stderr": err,
            "truncated": out_truncated or err_truncated,
        }
    except subprocess.TimeoutExpired as e:
        out, out_truncated = _clip((e.stdout or "") if isinstance(e.stdout, str) else "")
        err, err_truncated = _clip((e.stderr or "") if isinstance(e.stderr, str) else "")
        return {
            "command": command,
            "exitCode": None,
            "signal": "SIGTERM",
            "timedOut": True,
            "stdout": out,
            "stderr": err,
            "truncated": out_truncated or err_truncated,
        }
    except OSError as e:
        return {
            "command": command,
            "exitCode": None,
            "signal": None,
            "timedOut": False,
            "stdout": "",
            "stderr": f"{type(e).__name__}: {e}",
            "truncated": False,
        }


def register(ctx) -> None:
    """Plugin entry point — called by the Hermes plugin system."""
    ctx.register_tool(
        name="vss_cli",
        toolset="vss",
        schema=_SCHEMA,
        handler=vss_cli_tool,
        description=_SCHEMA["function"]["description"],
        emoji="\U0001f4f9",  # video camera
    )
