# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""VSS Hermes plugin exposing the ``vss`` CLI as the ``vss_cli`` agent tool."""

from __future__ import annotations

import json
import os
import subprocess
from typing import Any

__all__ = ["register"]

_MAX_CAPTURE = 200_000
_DEFAULT_TIMEOUT_SEC = 600

_FUNCTION_SCHEMA = {
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
}


def _clip(text: str) -> tuple[str, bool]:
    if len(text) <= _MAX_CAPTURE:
        return text, False
    return text[:_MAX_CAPTURE] + "\n…[truncated]", True


def _as_text(value: object) -> str:
    if isinstance(value, bytes):
        return value.decode(errors="replace")
    return value if isinstance(value, str) else ""


def _reply(
    command: str,
    *,
    exit_code: int | None = None,
    signal: str | None = None,
    timed_out: bool = False,
    stdout: str = "",
    stderr: str = "",
    truncated: bool = False,
) -> str:
    # Hermes tool handlers must return str (or multimodal envelope), not dict.
    return json.dumps({
        "command": command,
        "exitCode": exit_code,
        "signal": signal,
        "timedOut": timed_out,
        "stdout": stdout,
        "stderr": stderr,
        "truncated": truncated,
    })


def vss_cli_tool(args: dict, **_kw: Any) -> str:
    argv = args.get("args") or []
    cwd: str | None = args.get("cwd")
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
        out, out_trunc = _clip(proc.stdout or "")
        err, err_trunc = _clip(proc.stderr or "")
        return _reply(
            command,
            exit_code=proc.returncode,
            stdout=out,
            stderr=err,
            truncated=out_trunc or err_trunc,
        )
    except subprocess.TimeoutExpired as e:
        out, out_trunc = _clip(_as_text(e.stdout))
        err, err_trunc = _clip(_as_text(e.stderr))
        return _reply(
            command,
            signal="SIGTERM",
            timed_out=True,
            stdout=out,
            stderr=err,
            truncated=out_trunc or err_trunc,
        )
    except OSError as e:
        return _reply(command, stderr=f"{type(e).__name__}: {e}")


def register(ctx) -> None:
    # Hermes wraps `schema` in {"type": "function", "function": ...} itself.
    ctx.register_tool(
        name="vss_cli",
        toolset="vss",
        schema=_FUNCTION_SCHEMA,
        handler=vss_cli_tool,
        description=_FUNCTION_SCHEMA["description"],
        emoji="\U0001f4f9",
    )
