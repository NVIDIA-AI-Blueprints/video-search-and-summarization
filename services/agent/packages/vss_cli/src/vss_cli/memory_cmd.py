# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""``vss memory`` -- cross-group store surface (SDD §2).

Not a command group: upsert/get/query/events have no job lifecycle, so
``run``/``status``/``get``/``list`` would be meaningless. Job reads stay on
``vss <group> status|get|list``; this command is the admin/introspection
surface over the same ``nv.vss.memory/1.0`` index.
"""

from __future__ import annotations

import json
import sys
from typing import Any

import click
from pydantic import ValidationError

from vss_core._foundation.errors import BackendUnreachableError
from vss_core._foundation.errors import ConfigurationError
from vss_core.memory.models import KNOWN_GROUPS
from vss_core.memory.models import UnifiedMemoryRecord
from vss_core.memory.service import MemoryNotFoundError
from vss_core.memory.service import MemoryService
from vss_core.memory.service import build_memory_service
from vss_core.memory.store import InMemoryStore
from vss_core.memory.store import MemoryQuery

from . import config as config_mod
from .exits import Exit
from .memory_access import DEFAULT_MEMORY_INDEX

#: Injectable store for hermetic unit tests.
_TEST_STORE: InMemoryStore | None = None


def set_test_store(store: InMemoryStore | None) -> None:
    """Install (or clear) a process-local in-memory store for tests."""
    global _TEST_STORE
    _TEST_STORE = store


def _emit(obj: Any, *, pretty: bool) -> None:
    click.echo(json.dumps(obj, indent=2 if pretty else None, default=str))


def _resolve_service(*, es_endpoint: str | None, memory_index: str | None) -> MemoryService:
    if _TEST_STORE is not None:
        return MemoryService(_TEST_STORE)
    endpoint = es_endpoint
    if not endpoint:
        deployment = config_mod.load()
        endpoint = deployment.endpoint("elasticsearch")
    return build_memory_service(
        es_endpoint=endpoint,
        memory_index=memory_index or DEFAULT_MEMORY_INDEX,
    )


def _store_options(fn: Any) -> Any:
    fn = click.option(
        "--es-endpoint",
        default=None,
        help="Elasticsearch endpoint for the vss-memory index (defaults to configured deployment).",
    )(fn)
    fn = click.option(
        "--memory-index",
        default=None,
        help=f"Elasticsearch index name (default: {DEFAULT_MEMORY_INDEX}).",
    )(fn)
    fn = click.option("--pretty", is_flag=True, help="Indent JSON output.")(fn)
    return fn


@click.group(name="memory")
def memory() -> None:
    """Unified memory store: upsert, get, query, and events introspection."""


@memory.command("upsert")
@click.option("--json", "json_payload", default=None, help="Inline JSON record. Reads stdin when omitted.")
@_store_options
def upsert(json_payload: str | None, es_endpoint: str | None, memory_index: str | None, pretty: bool) -> None:
    """Upsert a unified memory record from JSON."""
    raw = json_payload if json_payload is not None else sys.stdin.read()
    try:
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise ValueError("memory upsert payload must be a JSON object")
        record = UnifiedMemoryRecord.model_validate(payload)
    except (ValueError, ValidationError, json.JSONDecodeError) as error:
        click.echo(f"vss memory: invalid input: {error}", err=True)
        raise SystemExit(int(Exit.INVALID_INPUT)) from error
    try:
        stored = _resolve_service(es_endpoint=es_endpoint, memory_index=memory_index).upsert(record)
    except ConfigurationError as error:
        click.echo(f"vss memory: configuration error: {error}", err=True)
        raise SystemExit(int(Exit.CONFIGURATION)) from error
    except config_mod.ConfigError as error:
        click.echo(f"vss memory: configuration error: {error}", err=True)
        raise SystemExit(int(Exit.CONFIGURATION)) from error
    except BackendUnreachableError as error:
        click.echo(f"vss memory: backend unreachable: {error}", err=True)
        raise SystemExit(int(Exit.BACKEND_UNREACHABLE)) from error
    _emit(stored.model_dump_memory(), pretty=pretty)


@memory.command("get")
@click.option("--job-id", required=True)
@_store_options
def get_record(job_id: str, es_endpoint: str | None, memory_index: str | None, pretty: bool) -> None:
    """Fetch one memory record by job_id."""
    try:
        record = _resolve_service(es_endpoint=es_endpoint, memory_index=memory_index).get(job_id)
    except MemoryNotFoundError as error:
        click.echo(f"vss memory: {error}", err=True)
        raise SystemExit(int(Exit.NOT_FOUND)) from error
    except ConfigurationError as error:
        click.echo(f"vss memory: configuration error: {error}", err=True)
        raise SystemExit(int(Exit.CONFIGURATION)) from error
    except BackendUnreachableError as error:
        click.echo(f"vss memory: backend unreachable: {error}", err=True)
        raise SystemExit(int(Exit.BACKEND_UNREACHABLE)) from error
    except config_mod.ConfigError as error:
        click.echo(f"vss memory: configuration error: {error}", err=True)
        raise SystemExit(int(Exit.CONFIGURATION)) from error
    _emit(record.model_dump_memory(), pretty=pretty)


@memory.command("query")
@click.option("--query", "text", default=None, help="Free-text match over query/answer/ext collections.")
@click.option("--job-id", default=None)
@click.option("--group", type=click.Choice(sorted(KNOWN_GROUPS)), default=None)
@click.option(
    "--status",
    type=click.Choice(("submitted", "running", "completed", "failed", "partial", "timeout")),
    default=None,
)
@click.option("--sensor-id", default=None)
@click.option("--since", default=None, help="Lower bound on job.created_at (UTC ISO-8601).")
@click.option("--until", default=None, help="Upper bound on job.created_at (UTC ISO-8601).")
@click.option("--limit", type=click.IntRange(1), default=20)
@_store_options
def query_records(
    text: str | None,
    job_id: str | None,
    group: str | None,
    status: str | None,
    sensor_id: str | None,
    since: str | None,
    until: str | None,
    limit: int,
    es_endpoint: str | None,
    memory_index: str | None,
    pretty: bool,
) -> None:
    """Query unified memory records."""
    query = MemoryQuery(
        text=text,
        group=group,  # type: ignore[arg-type]
        status=status,  # type: ignore[arg-type]
        sensor_id=sensor_id,
        job_id=job_id,
        since=since,
        until=until,
        limit=limit,
    )
    try:
        records = _resolve_service(es_endpoint=es_endpoint, memory_index=memory_index).query(query)
    except ConfigurationError as error:
        click.echo(f"vss memory: configuration error: {error}", err=True)
        raise SystemExit(int(Exit.CONFIGURATION)) from error
    except BackendUnreachableError as error:
        click.echo(f"vss memory: backend unreachable: {error}", err=True)
        raise SystemExit(int(Exit.BACKEND_UNREACHABLE)) from error
    except config_mod.ConfigError as error:
        click.echo(f"vss memory: configuration error: {error}", err=True)
        raise SystemExit(int(Exit.CONFIGURATION)) from error
    _emit({"records": [record.model_dump_memory() for record in records]}, pretty=pretty)


@memory.command("events")
@click.option("--asset-id", required=True)
@click.option("--start-time", default=None)
@click.option("--end-time", default=None)
@click.option("--anchor-event-id", default=None)
@click.option("--direction", type=click.Choice(("before", "after", "around")), default="around")
@click.option("--window", default=None, help="Reserved for duration windows (passed through).")
@click.option("--match", default=None)
@click.option("--limit", type=click.IntRange(1), default=50)
@_store_options
def events(
    asset_id: str,
    start_time: str | None,
    end_time: str | None,
    anchor_event_id: str | None,
    direction: str,
    window: str | None,
    match: str | None,
    limit: int,
    es_endpoint: str | None,
    memory_index: str | None,
    pretty: bool,
) -> None:
    """Extract event collections from persisted memory records for an asset."""
    try:
        rows = _resolve_service(es_endpoint=es_endpoint, memory_index=memory_index).events(
            asset_id=asset_id,
            start_time=start_time,
            end_time=end_time,
            anchor_event_id=anchor_event_id,
            direction=direction,
            window=window,
            match=match,
            limit=limit,
        )
    except MemoryNotFoundError as error:
        click.echo(f"vss memory: {error}", err=True)
        click.echo(
            "hint: run a covering job, e.g. vss summarize run --start-time … --end-time …",
            err=True,
        )
        raise SystemExit(int(Exit.NOT_FOUND)) from error
    except ConfigurationError as error:
        click.echo(f"vss memory: configuration error: {error}", err=True)
        raise SystemExit(int(Exit.CONFIGURATION)) from error
    except BackendUnreachableError as error:
        click.echo(f"vss memory: backend unreachable: {error}", err=True)
        raise SystemExit(int(Exit.BACKEND_UNREACHABLE)) from error
    except config_mod.ConfigError as error:
        click.echo(f"vss memory: configuration error: {error}", err=True)
        raise SystemExit(int(Exit.CONFIGURATION)) from error
    _emit({"asset_id": asset_id, "events": rows}, pretty=pretty)


class _MemoryGroup:
    """Plugin spec so ``memory`` mounts through the published contract."""

    api_version = 1
    name = "memory"
    summary = "Unified memory store surface"

    def cli(self) -> Any:
        return memory


MEMORY = _MemoryGroup()

__all__ = ["MEMORY", "memory", "set_test_store"]
