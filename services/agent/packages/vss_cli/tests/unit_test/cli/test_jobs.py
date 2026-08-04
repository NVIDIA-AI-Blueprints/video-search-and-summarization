# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Contract tests for shared job lifecycle helpers."""

from __future__ import annotations

import json

import pytest

from vss_cli._jobs import MARKER_COMPLETED
from vss_cli._jobs import MARKER_FAILED
from vss_cli._jobs import MARKER_TIMEOUT
from vss_cli._jobs import JobLifecycle
from vss_cli._jobs import PersistError
from vss_cli._jobs import completion_marker
from vss_cli._jobs import mint_job_id
from vss_cli._jobs import safe_persist
from vss_core.memory.adapters import SummaryAdapter
from vss_core.memory.models import MemoryInput
from vss_core.memory.service import MemoryService
from vss_core.memory.store import InMemoryStore


def test_mint_job_id_uses_group_prefix() -> None:
    assert mint_job_id("summary").startswith("summary-")
    assert mint_job_id("search").startswith("search-")
    assert len(mint_job_id("summary").split("-", 1)[1]) == 26


def test_completion_marker_contract() -> None:
    line = completion_marker(
        MARKER_COMPLETED,
        group="summary",
        job_id="summary-01ABCDEFGHJKMNPQRSTVWXYZ",
        status="completed",
        persisted=True,
        exit_hint=0,
        asset_id="cam-1",
    )
    marker = json.loads(line)
    assert marker == {
        "event": "vss_job_completed",
        "group": "summary",
        "job_id": "summary-01ABCDEFGHJKMNPQRSTVWXYZ",
        "asset_id": "cam-1",
        "status": "completed",
        "persisted": True,
        "exit_hint": 0,
    }
    assert "memory_id" not in marker
    assert "domain" not in marker
    assert len(line.encode("utf-8")) <= 1024


@pytest.mark.parametrize("event", [MARKER_COMPLETED, MARKER_FAILED, MARKER_TIMEOUT])
def test_marker_event_vocabulary(event: str) -> None:
    line = completion_marker(event, group="search", job_id="search-1", status="failed", persisted=False, exit_hint=1)
    assert json.loads(line)["event"] == event


def test_lifecycle_write_ahead_and_immutable_created_at() -> None:
    store = InMemoryStore()
    service = MemoryService(store)
    adapter = SummaryAdapter()
    lifecycle = JobLifecycle.start(
        group="summary",
        adapter=adapter,
        input_data=MemoryInput(query="q"),
        persist=True,
        service=service,
        job_id="summary-lifecycle",
    )
    created = lifecycle.created_at
    lifecycle.write_running()
    lifecycle.write_terminal(status="completed", output=SummaryAdapter.build_output(answer="a"))
    record = service.get("summary-lifecycle")
    assert record.job.created_at == created
    assert record.job.status == "completed"
    assert store.upsert_ids == ["summary-lifecycle", "summary-lifecycle", "summary-lifecycle"]


def test_safe_persist_wraps_failures() -> None:
    with pytest.raises(PersistError):
        safe_persist(lambda: (_ for _ in ()).throw(RuntimeError("boom")))
