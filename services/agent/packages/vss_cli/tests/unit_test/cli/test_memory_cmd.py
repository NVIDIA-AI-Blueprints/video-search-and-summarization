# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Contract tests for the non-job ``vss memory`` store surface."""

from __future__ import annotations

import json

from click.testing import CliRunner
import pytest

from vss_cli.exits import Exit
from vss_cli.memory_cmd import MEMORY
from vss_cli.memory_cmd import set_test_store
from vss_core.memory.adapters import SummaryAdapter
from vss_core.memory.adapters import utc_now_iso
from vss_core.memory.store import InMemoryStore


@pytest.fixture
def store() -> InMemoryStore:
    mem = InMemoryStore()
    set_test_store(mem)
    yield mem
    set_test_store(None)


def _run(*argv: str) -> object:
    return CliRunner().invoke(MEMORY.cli(), list(argv))


def test_memory_exposes_store_verbs_not_job_grammar() -> None:
    commands = set(MEMORY.cli().commands)
    assert {"upsert", "get", "query", "events"} <= commands
    assert "run" not in commands
    assert "status" not in commands
    assert "list" not in commands


def test_upsert_get_and_events_round_trip(store: InMemoryStore) -> None:
    created = utc_now_iso()
    adapter = SummaryAdapter()
    record = adapter.terminal_record(
        job_id="summary-test1",
        created_at=created,
        status="completed",
        input_data=SummaryAdapter.build_input(
            prompt="what happened?",
            video_id="cam-1",
            media_ref={"source": "vst"},
            params={},
        ),
        output=SummaryAdapter.build_output(
            answer="a forklift crossed",
            events=[{"id": "e1", "description": "forklift near bay"}],
        ),
    )
    result = _run("upsert", "--json", json.dumps(record.model_dump_memory()))
    assert result.exit_code == 0, result.output

    got = _run("get", "--job-id", "summary-test1")
    assert got.exit_code == 0, got.output
    body = json.loads(got.output)
    assert body["job"]["job_id"] == "summary-test1"
    assert body["output"]["ext"]["event_ids"] == ["e1"]

    events = _run("events", "--asset-id", "cam-1")
    assert events.exit_code == 0, events.output
    payload = json.loads(events.output)
    assert payload["asset_id"] == "cam-1"
    assert payload["events"][0]["description"] == "forklift near bay"


def test_events_missing_asset_is_not_found(store: InMemoryStore) -> None:
    result = _run("events", "--asset-id", "missing")
    assert result.exit_code == int(Exit.NOT_FOUND)
