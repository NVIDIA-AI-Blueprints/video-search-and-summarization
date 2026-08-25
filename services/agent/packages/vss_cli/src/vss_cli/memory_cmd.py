# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""``vss memory`` -- cross-group unified-memory access (SDD §2).

This is an administrative domain, not a job-capable command group. Job-scoped
reads remain available as ``vss <group> status|get|list``; these commands expose
the underlying parent/child store across groups.
"""

from __future__ import annotations

import json
import sys
from typing import TYPE_CHECKING
from typing import Any

import click
from pydantic import ValidationError

from . import config as config_mod
from . import memory as memory_mod
from .exits import Exit

if TYPE_CHECKING:
    from vss_cli.memory import Memory

_TEST_MEMORY: Memory | None = None


def set_test_memory(memory: Memory | None) -> None:
    """Inject an in-process memory facade for hermetic command tests."""
    global _TEST_MEMORY
    _TEST_MEMORY = memory


def _memory() -> Memory:
    if _TEST_MEMORY is not None:
        return _TEST_MEMORY
    return memory_mod.build(config_mod.load())


def _emit(value: Any, *, pretty: bool) -> None:
    click.echo(json.dumps(value, indent=2 if pretty else None, default=str))


def _fail(prefix: str, error: BaseException, exit_code: Exit) -> None:
    click.echo(f"vss memory: {prefix}: {error}", err=True)
    raise SystemExit(int(exit_code)) from error


def _read_failure(error: BaseException) -> None:
    if type(error).__name__ == "MemoryNotFoundError":
        _fail("not found", error, Exit.NOT_FOUND)
    if type(error).__name__ == "MemoryDecodeError":
        _fail("unreadable stored record", error, Exit.ERROR)
    if type(error).__name__ == "NestedCollectionError":
        _fail("invalid input", error, Exit.INVALID_INPUT)
    if isinstance(error, (config_mod.ConfigError, memory_mod.MemoryUnavailable)) or type(error).__name__ == (
        "ConfigurationError"
    ):
        _fail("configuration error", error, Exit.CONFIGURATION)
    if isinstance(error, memory_mod.write_failures()):
        _fail("backend unreachable", error, Exit.BACKEND_UNREACHABLE)
    raise error


def _output_options(function: Any) -> Any:
    function = click.option("--pretty", is_flag=True, help="Indent JSON output.")(function)
    return function


@click.group(name="memory")
def memory() -> None:
    """Inspect and update the cross-group unified-memory store."""


@memory.command("upsert")
@click.option("--json", "json_payload", default=None, help="Inline JSON record. Reads stdin when omitted.")
@_output_options
def upsert(json_payload: str | None, pretty: bool) -> None:
    """Upsert one parent or child unified-memory record."""
    raw = json_payload if json_payload is not None else sys.stdin.read()
    try:
        from vss_core.memory import UnifiedMemoryRecord

        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise ValueError("memory upsert payload must be a JSON object")
        record = UnifiedMemoryRecord.model_validate(payload)
    except (json.JSONDecodeError, ValidationError, ValueError) as error:
        _fail("invalid input", error, Exit.INVALID_INPUT)

    try:
        stored = _memory().service.upsert(record)
    except Exception as error:
        _read_failure(error)
        raise AssertionError("unreachable") from error
    _emit(stored.model_dump_memory(), pretty=pretty)


@memory.command("get")
@click.option("--job-id", required=True)
@click.option("--record-type", type=click.Choice(("event", "search_hit", "incident")))
@click.option("--record-id")
@_output_options
def get_record(
    job_id: str,
    record_type: str | None,
    record_id: str | None,
    pretty: bool,
) -> None:
    """Fetch a parent by job id or a child by its public identity."""
    if (record_type is None) != (record_id is None):
        raise click.UsageError("--record-type and --record-id must be supplied together")
    try:
        service = _memory().service
        record = (
            service.get_record(job_id, record_type, record_id)
            if record_type is not None and record_id is not None
            else service.get(job_id, reconcile=False)
        )
    except Exception as error:
        _read_failure(error)
        raise AssertionError("unreachable") from error
    _emit(record.model_dump_memory(), pretty=pretty)


@memory.command("query")
@click.option("--query", "text", default=None, help="Free-text match over memory content.")
@click.option("--job-id")
@click.option("--group", type=click.Choice(("summary", "search", "alert", "vlm")))
@click.option("--status", type=click.Choice(("submitted", "running", "completed", "failed", "partial", "timeout")))
@click.option("--sensor-id")
@click.option("--record-type", type=click.Choice(("event", "search_hit", "incident")))
@click.option("--record-id")
@click.option("--parents-only", is_flag=True, help="Return parent job records only.")
@click.option("--since", help="Lower ISO-8601 time bound.")
@click.option("--until", help="Upper ISO-8601 time bound.")
@click.option("--time-field", type=click.Choice(("created_at", "window")), default="created_at", show_default=True)
@click.option("--limit", type=click.IntRange(1), default=20, show_default=True)
@_output_options
def query_records(
    text: str | None,
    job_id: str | None,
    group: str | None,
    status: str | None,
    sensor_id: str | None,
    record_type: str | None,
    record_id: str | None,
    parents_only: bool,
    since: str | None,
    until: str | None,
    time_field: str,
    limit: int,
    pretty: bool,
) -> None:
    """Query parent and child records across command groups."""
    try:
        from vss_core.memory import MemoryQuery

        query = MemoryQuery(
            text=text,
            group=group,  # type: ignore[arg-type]
            status=status,  # type: ignore[arg-type]
            sensor_id=sensor_id,
            job_id=job_id,
            record_type=record_type,  # type: ignore[arg-type]
            record_id=record_id,
            parents_only=parents_only,
            since=since,
            until=until,
            time_field=time_field,
            limit=limit,
        )
        records = _memory().service.query(query)
    except ValueError as error:
        _fail("invalid input", error, Exit.INVALID_INPUT)
    except Exception as error:
        _read_failure(error)
        raise AssertionError("unreachable") from error
    _emit({"records": [record.model_dump_memory() for record in records]}, pretty=pretty)


@memory.command("events")
@click.option("--asset-id", required=True)
@click.option("--start-time")
@click.option("--end-time")
@click.option("--anchor-event-id")
@click.option("--direction", type=click.Choice(("before", "after", "around")), default="around", show_default=True)
@click.option("--window", help="Reserved duration window; currently unsupported.")
@click.option("--match")
@click.option("--limit", type=click.IntRange(1), default=50, show_default=True)
@_output_options
def events(
    asset_id: str,
    start_time: str | None,
    end_time: str | None,
    anchor_event_id: str | None,
    direction: str,
    window: str | None,
    match: str | None,
    limit: int,
    pretty: bool,
) -> None:
    """Recall persisted event, incident, and search-hit child records."""
    try:
        rows = _memory().service.events(
            asset_id=asset_id,
            start_time=start_time,
            end_time=end_time,
            anchor_event_id=anchor_event_id,
            direction=direction,
            window=window,
            match=match,
            limit=limit,
        )
    except ValueError as error:
        _fail("invalid input", error, Exit.INVALID_INPUT)
    except Exception as error:
        _read_failure(error)
        raise AssertionError("unreachable") from error
    _emit({"asset_id": asset_id, "events": rows}, pretty=pretty)


class _MemoryGroup:
    api_version = 1
    name = "memory"
    summary = "Unified memory store surface"

    def cli(self) -> Any:
        return memory


MEMORY = _MemoryGroup()

__all__ = ["MEMORY", "memory", "set_test_memory"]
