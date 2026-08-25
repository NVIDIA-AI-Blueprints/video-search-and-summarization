# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Cross-group ``vss memory`` command tests."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING
from typing import Any

from click.testing import CliRunner
import pytest

from vss_cli.exits import Exit
from vss_cli.memory import Memory
from vss_cli.memory_cmd import memory
from vss_cli.memory_cmd import set_test_memory
from vss_core.memory import MemoryService
from vss_core.memory import UnifiedMemoryRecord
from vss_core.memory.backends.in_memory import InMemoryStore

if TYPE_CHECKING:
    from collections.abc import Generator

_CREATED = "2026-08-19T20:00:00Z"


def _parent(job_id: str = "summary-01", *, group: str = "summary", asset_id: str = "camera-1") -> dict[str, Any]:
    return {
        "schema": "nv.vss.memory/1.0",
        "job": {
            "job_id": job_id,
            "group": group,
            "operation": "run",
            "status": "completed",
            "created_at": _CREATED,
        },
        "input": {"sensors": [{"id": asset_id}]},
        "output": {"answer": "summary", "ext": {"event_count": 1}},
    }


def _event(job_id: str = "summary-01", *, record_id: str = "event-1", asset_id: str = "camera-1") -> dict[str, Any]:
    return {
        "schema": "nv.vss.memory/1.0",
        "job": {
            "job_id": job_id,
            "record_type": "event",
            "record_id": record_id,
            "group": "summary",
            "operation": "run",
            "status": "completed",
            "created_at": _CREATED,
        },
        "input": {
            "sensors": [{"id": asset_id}],
            "window": {
                "start": {"timestamp": "2026-08-19T20:01:00Z"},
                "end": {"timestamp": "2026-08-19T20:02:00Z"},
            },
        },
        "output": {"answer": "forklift entered aisle"},
    }


@pytest.fixture(autouse=True)
def injected_memory() -> Generator[Memory]:
    facade = Memory(MemoryService(InMemoryStore()), index="vss-memory")
    set_test_memory(facade)
    yield facade
    set_test_memory(None)


def _invoke(*args: str, input: str | None = None) -> Any:
    return CliRunner().invoke(memory, list(args), input=input)


def test_memory_exposes_store_verbs_not_job_grammar() -> None:
    result = _invoke("--help")
    assert result.exit_code == 0
    assert all(verb in result.output for verb in ("upsert", "get", "query", "events"))
    assert all(verb not in result.output for verb in ("run", "status", "list"))


def test_upsert_get_parent_and_child_round_trip() -> None:
    parent = _parent()
    child = _event()
    assert _invoke("upsert", "--json", json.dumps(parent)).exit_code == 0
    assert _invoke("upsert", input=json.dumps(child)).exit_code == 0

    got_parent = _invoke("get", "--job-id", "summary-01")
    assert got_parent.exit_code == 0
    assert json.loads(got_parent.output)["job"]["job_id"] == "summary-01"

    got_child = _invoke(
        "get",
        "--job-id",
        "summary-01",
        "--record-type",
        "event",
        "--record-id",
        "event-1",
    )
    assert got_child.exit_code == 0
    assert json.loads(got_child.output)["job"]["record_id"] == "event-1"


def test_query_and_events_return_child_records(injected_memory: Memory) -> None:
    injected_memory.service.upsert(UnifiedMemoryRecord.model_validate(_parent()))
    injected_memory.service.upsert(UnifiedMemoryRecord.model_validate(_event()))

    queried = _invoke("query", "--job-id", "summary-01", "--record-type", "event")
    assert queried.exit_code == 0
    records = json.loads(queried.output)["records"]
    assert [row["job"]["record_id"] for row in records] == ["event-1"]

    recalled = _invoke("events", "--asset-id", "camera-1")
    assert recalled.exit_code == 0
    events = json.loads(recalled.output)["events"]
    assert events[0]["record_id"] == "event-1"
    assert events[0]["description"] == "forklift entered aisle"


def test_events_empty_filters_succeed_for_known_asset(injected_memory: Memory) -> None:
    injected_memory.service.upsert(UnifiedMemoryRecord.model_validate(_parent()))
    result = _invoke("events", "--asset-id", "camera-1", "--match", "not present")
    assert result.exit_code == 0
    assert json.loads(result.output)["events"] == []


def test_invalid_inputs_exit_two() -> None:
    assert _invoke("upsert", "--json", "{").exit_code == int(Exit.INVALID_INPUT)
    assert _invoke("query", "--group", "media").exit_code == int(Exit.INVALID_INPUT)
    mismatch = _invoke("get", "--job-id", "summary-01", "--record-type", "event")
    assert mismatch.exit_code == int(Exit.INVALID_INPUT)
    unsupported = _invoke("events", "--asset-id", "camera-1", "--window", "1h")
    assert unsupported.exit_code == int(Exit.INVALID_INPUT)


def test_unknown_handles_exit_five() -> None:
    assert _invoke("get", "--job-id", "missing").exit_code == int(Exit.NOT_FOUND)
    assert _invoke("events", "--asset-id", "missing").exit_code == int(Exit.NOT_FOUND)
