# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""``vision-ai memory list|get`` -- query the job store at ``~/.vision-ai/memory/main.db``."""

from __future__ import annotations

import sqlite3
from typing import get_args

import click

from nvidia_vision_ai.memory import JobQuery
from nvidia_vision_ai.memory import JobStatus
from nvidia_vision_ai.memory import SqliteJobStore

from .exits import Exit
from .output import emit
from .output import fail
from .runtime import ConfigError
from .runtime import Runtime


def _store() -> SqliteJobStore:
    try:
        return Runtime.from_env().store()
    except ConfigError as exc:
        fail(Exit.CONFIGURATION, str(exc))


@click.group("memory")
def memory() -> None:
    """Query recorded jobs."""


@memory.command("list")
@click.option("--type", "job_type", help="Only jobs of this tool, e.g. vlm.")
@click.option("--status", type=click.Choice(get_args(JobStatus)), help="Only jobs in this status.")
@click.option("--limit", type=click.IntRange(1, 10_000), default=20, show_default=True)
def list_(job_type: str | None, status: str | None, limit: int) -> None:
    """List jobs, newest first."""
    try:
        records = _store().list(JobQuery(type=job_type, status=status, limit=limit))  # type: ignore[arg-type]
    except sqlite3.Error as exc:
        fail(Exit.CONFIGURATION, f"cannot read the job store: {exc}")
    emit({"jobs": [record.model_dump(mode="json") for record in records]})


@memory.command("get")
@click.argument("job_id")
def get(job_id: str) -> None:
    """Print one job record (exit 5 if unknown)."""
    try:
        record = _store().get(job_id)
    except sqlite3.Error as exc:
        fail(Exit.CONFIGURATION, f"cannot read the job store: {exc}")
    if record is None:
        fail(Exit.NOT_FOUND, f"no job {job_id!r}")
    emit(record.model_dump(mode="json"))
