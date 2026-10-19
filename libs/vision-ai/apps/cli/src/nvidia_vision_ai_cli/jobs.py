# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""``vision-ai <tool>``: one synchronous call, recorded as a job.

Order of a call:

1. Validate flags, resolve settings (Runtime) and ``tool.prepare``. Failures
   here exit 2 or 3 with an ``{"error": ...}`` object and no job record --
   nothing ran.
2. Write the job ``running`` (write-ahead: the record exists before the backend is called).
3. ``asyncio.run(tool.execute(...))``.
4. Write the job ``completed`` with the result, or ``failed`` with an error
   code -- including on Ctrl-C or SIGTERM -- and print the record as the one
   JSON object on stdout.
"""

from __future__ import annotations

import asyncio
import contextlib
import signal
import sqlite3
from typing import TYPE_CHECKING
from typing import Any
from typing import NoReturn

import click
from pydantic import ValidationError

from nvidia_vision_ai import BackendResponseError
from nvidia_vision_ai import BackendUnreachableError
from nvidia_vision_ai import ConfigurationError
from nvidia_vision_ai import InvalidRequestError
from nvidia_vision_ai.memory import JobError
from nvidia_vision_ai.memory import JobRecord
from nvidia_vision_ai.memory import new_job_id
from nvidia_vision_ai.memory import utc_now

from .exits import Exit
from .options import given
from .options import options_for
from .output import emit
from .output import fail
from .runtime import ConfigError
from .runtime import InputError
from .runtime import Runtime
from .runtime import validation_message

if TYPE_CHECKING:
    from collections.abc import Iterator
    from types import FrameType

    from nvidia_vision_ai.memory import JobStore

    from .tool import Tool


class Terminated(BaseException):
    """Raised by the SIGTERM handler so a killed call still records its job as failed."""


def exit_for(exc: BaseException) -> tuple[Exit, str]:
    """Map a tool failure onto an exit code and a one-line message."""
    if isinstance(exc, ValidationError):
        return Exit.INVALID_INPUT, validation_message(exc)
    if isinstance(exc, ConfigurationError | ConfigError):
        return Exit.CONFIGURATION, str(exc)
    if isinstance(exc, InputError | InvalidRequestError | ValueError):
        return Exit.INVALID_INPUT, str(exc)
    if isinstance(exc, BackendUnreachableError):
        return Exit.BACKEND_UNREACHABLE, str(exc)
    if isinstance(exc, BackendResponseError):
        return Exit.BACKEND_ERROR, str(exc)
    return Exit.ERROR, f"{type(exc).__name__}: {exc}"


@contextlib.contextmanager
def _sigterm_raises() -> Iterator[None]:
    def handler(signum: int, frame: FrameType | None) -> None:  # noqa: ARG001
        raise Terminated

    try:
        previous = signal.signal(signal.SIGTERM, handler)
    except ValueError:  # not the main thread: leave signals alone
        yield
        return
    try:
        yield
    finally:
        signal.signal(signal.SIGTERM, previous)


def run_tool(tool: Tool, values: dict[str, Any], runtime: Runtime) -> None:
    try:
        inputs = tool.Input.model_validate(given(values, tool.Input))
        flags = given(values, tool.Settings)
        settings = tool.prepare(inputs, runtime.settings(tool, flags), frozenset(flags))
        store = runtime.store()
    except (ValidationError, ConfigError, InputError, ConfigurationError, ValueError) as exc:
        code, message = exit_for(exc)
        fail(code, message)

    running = JobRecord(
        job_id=new_job_id(tool.name),
        type=tool.name,
        status="running",
        created_at=utc_now(),
        input=tool.record_input(inputs, settings),
    )
    try:
        store.put(running)
    except sqlite3.Error as exc:
        fail(Exit.CONFIGURATION, f"cannot write the job store at {runtime.memory_path()}: {exc}")
    try:
        with _sigterm_raises():
            result = asyncio.run(tool.execute(inputs, settings))
    except (KeyboardInterrupt, Terminated, asyncio.CancelledError) as exc:
        reason = "terminated (SIGTERM)" if isinstance(exc, Terminated) else "interrupted"
        click.echo(f"vision-ai: {reason} (job {running.job_id})", err=True)
        _finish(store, _failed(running, Exit.ERROR, reason), Exit.ERROR)
    except Exception as exc:
        code, message = exit_for(exc)
        click.echo(f"vision-ai: {message} (job {running.job_id})", err=True)
        _finish(store, _failed(running, code, message), code)

    record = running.model_copy(
        update={"status": "completed", "finished_at": utc_now(), "result": result.model_dump(mode="json")}
    )
    _finish(store, record, Exit.SUCCESS)


def _failed(running: JobRecord, code: Exit, message: str) -> JobRecord:
    return running.model_copy(
        update={"status": "failed", "finished_at": utc_now(), "error": JobError(code=code.token, message=message)}
    )


def _finish(store: JobStore, record: JobRecord, code: Exit) -> NoReturn:
    """Write the terminal record, print it, and exit. A store failure here is ERROR:
    the call already ran, so the record on stdout is the only copy."""
    try:
        store.put(record)
    except sqlite3.Error as exc:
        click.echo(f"vision-ai: job {record.job_id} ran but could not be recorded: {exc}", err=True)
        code = Exit.ERROR
    emit(record.model_dump(mode="json"))
    raise SystemExit(int(code))


def tool_command(tool: Tool) -> click.Command:
    """The ``vision-ai <tool>`` command: Input flags (call) + Settings flags (overrides)."""
    overlap = set(tool.Input.model_fields) & set(tool.Settings.model_fields)
    if overlap:
        raise TypeError(f"tool {tool.name!r}: Input and Settings share fields {sorted(overlap)}")

    def callback(**values: Any) -> None:
        run_tool(tool, values, Runtime.from_env())

    return click.Command(
        tool.name,
        params=[*options_for(tool.Input), *options_for(tool.Settings, all_optional=True)],
        callback=callback,
        help=f"{tool.summary}.\n\nSettings flags override `vision-ai configure {tool.name}` for this call only.",
        short_help=tool.summary,
    )
