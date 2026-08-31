# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Cross-group ``vss memory`` command tests."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING
from typing import Any

from click.testing import CliRunner
import pytest

from vss_cli import config as config_mod
from vss_cli.exits import Exit
from vss_cli.memory import Memory
from vss_cli.memory_cmd import memory
from vss_cli.memory_cmd import set_test_introspect
from vss_cli.memory_cmd import set_test_memory
from vss_core.introspection import IntrospectionResult
from vss_core.memory import MemoryService
from vss_core.memory import UnifiedMemoryRecord
from vss_core.memory.backends.in_memory import InMemoryStore

if TYPE_CHECKING:
    from collections.abc import Generator
    from pathlib import Path

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
    set_test_introspect(None)
    set_test_memory(None)


def _invoke(*args: str, input: str | None = None) -> Any:
    return CliRunner().invoke(memory, list(args), input=input)


def test_memory_exposes_store_verbs_not_job_grammar() -> None:
    result = _invoke("--help")
    assert result.exit_code == 0
    assert all(verb in result.output for verb in ("upsert", "get", "query", "events", "introspect"))
    assert all(verb not in result.output for verb in ("run", "status", "list"))


def test_memory_verbs_do_not_expose_static_index_selection() -> None:
    for verb in ("upsert", "get", "query", "events", "introspect"):
        result = _invoke(verb, "--help")
        assert result.exit_code == 0, result.output
        assert "--memory-index" not in result.output


def test_events_does_not_advertise_undefined_duration_window() -> None:
    result = _invoke("events", "--help")
    assert result.exit_code == 0, result.output
    assert "--window" not in result.output


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


def test_unknown_handles_exit_five() -> None:
    assert _invoke("get", "--job-id", "missing").exit_code == int(Exit.NOT_FOUND)
    assert _invoke("events", "--asset-id", "missing").exit_code == int(Exit.NOT_FOUND)


def _introspection_result(status: str = "completed") -> IntrospectionResult:
    return IntrospectionResult(
        status=status,
        sufficient_from_memory=status == "completed",
        answer="A forklift crossed the aisle." if status != "no_memory" else None,
    )


def test_introspect_help_exposes_exact_options() -> None:
    result = _invoke("introspect", "--help")
    assert result.exit_code == 0
    for option in (
        "--query",
        "--sensor",
        "--start-time",
        "--end-time",
        "--job-id",
        "--record-id",
        "--record-type",
        "--group",
        "--pretty",
    ):
        assert option in result.output
    assert "--no-persist" not in result.output


@pytest.mark.parametrize(
    "selector",
    (
        ("--sensor", "warehouse"),
        ("--job-id", "summary-01"),
        ("--record-id", "event-1"),
        ("--record-type", "event", "--sensor", "warehouse"),
        ("--group", "summary", "--sensor", "warehouse"),
        ("--start-time", "2026-08-19T20:00:00Z", "--end-time", "2026-08-19T21:00:00+00:00"),
    ),
)
def test_introspect_accepts_each_selector(selector: tuple[str, ...]) -> None:
    async def fake(_request: Any) -> IntrospectionResult:
        return _introspection_result()

    set_test_introspect(fake)
    result = _invoke("introspect", "--query", "What happened?", *selector)
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["status"] == "completed"


@pytest.mark.parametrize(
    "args",
    (
        ("--query", "What happened?"),
        ("--query", "What happened?", "--group", "summary"),
        ("--query", "What happened?", "--record-type", "event"),
        ("--query", "What happened?", "--start-time", "2026-08-19T20:00:00Z"),
        ("--query", "What happened?", "--end-time", "2026-08-19T21:00:00Z"),
        (
            "--query",
            "What happened?",
            "--sensor",
            "warehouse",
            "--start-time",
            "not-iso",
            "--end-time",
            "2026-08-19T21:00:00Z",
        ),
        ("--query", " ", "--sensor", "warehouse"),
    ),
)
def test_introspect_rejects_invalid_scope_and_values(args: tuple[str, ...]) -> None:
    assert _invoke("introspect", *args).exit_code == int(Exit.INVALID_INPUT)


def test_introspect_compact_and_pretty_json() -> None:
    async def fake(_request: Any) -> IntrospectionResult:
        return _introspection_result()

    set_test_introspect(fake)
    compact = _invoke("introspect", "--query", "What?", "--sensor", "warehouse")
    pretty = _invoke("introspect", "--query", "What?", "--sensor", "warehouse", "--pretty")
    assert "\n" not in compact.output.rstrip("\n")
    assert ": " not in compact.output
    assert '\n  "status"' in pretty.output
    assert json.loads(compact.output) == json.loads(pretty.output)


@pytest.mark.parametrize(
    ("status", "failure_kind", "exit_code"),
    (
        ("completed", None, Exit.SUCCESS),
        ("partial", None, Exit.SUCCESS),
        ("no_memory", None, Exit.NOT_FOUND),
        ("partial", "timeout", Exit.TIMEOUT),
        ("partial", "backend_unreachable", Exit.BACKEND_UNREACHABLE),
    ),
)
def test_introspect_emits_result_before_status_exit(
    status: str,
    failure_kind: str | None,
    exit_code: Exit,
) -> None:
    async def fake(_request: Any) -> IntrospectionResult:
        return _introspection_result(status).model_copy(update={"failure_kind": failure_kind})

    set_test_introspect(fake)
    result = _invoke("introspect", "--query", "What?", "--sensor", "warehouse")
    assert result.exit_code == int(exit_code)
    payload = json.loads(result.output)
    assert payload["status"] == status
    assert "failure_kind" not in payload


def test_introspect_no_memory_emits_json_and_exit_five() -> None:
    async def fake(_request: Any) -> IntrospectionResult:
        return _introspection_result("no_memory")

    set_test_introspect(fake)
    result = _invoke("introspect", "--query", "Was the worker wearing PPE?", "--sensor", "warehouse")

    assert result.exit_code == 5 == int(Exit.NOT_FOUND)
    assert json.loads(result.output) == {
        "status": "no_memory",
        "sufficient_from_memory": False,
        "answer": None,
        "memory_evidence": [],
        "vlm_evidence": [],
        "unresolved_gaps": [],
    }


@pytest.mark.parametrize(
    ("persist_by_default", "persistence_errors", "expected_exit"),
    ((True, [], Exit.SUCCESS), (False, [], Exit.SUCCESS), (True, ["memory offline"], Exit.PARTIAL)),
)
def test_introspect_uses_normal_internal_vlm_policy_without_persisting_itself(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    injected_memory: Memory,
    persist_by_default: bool,
    persistence_errors: list[str],
    expected_exit: Exit,
) -> None:
    import vss_cli.vlm.runner as runner_mod
    import vss_core.introspection as introspection_mod

    deployment = config_mod.Deployment(
        base_url="http://vss.test",
        services={
            "rt_vlm": config_mod.Service(url="http://vss.test/rtvi-vlm", models=["first-model"]),
            "elasticsearch": config_mod.Service(url="http://vss.test/elasticsearch"),
        },
        memory=config_mod.MemoryConfig(enabled=True, persist_by_default=persist_by_default),
    )
    observed: dict[str, Any] = {}
    monkeypatch.setenv("HOME", str(tmp_path))

    class FakeClient:
        def __init__(self, **kwargs: Any) -> None:
            observed["client"] = kwargs

        async def aclose(self) -> None:
            observed["closed"] = True

    class FakeRunner:
        def __init__(self, _deployment: Any, **kwargs: Any) -> None:
            self.persistence_errors = persistence_errors
            self.backend_errors: list[str] = []
            self.timed_out = False
            observed["runner_memory"] = kwargs["memory"]

    async def fake_introspect(*_args: Any, **kwargs: Any) -> IntrospectionResult:
        observed["memory_service"] = kwargs["memory"]
        assert injected_memory.service.list_jobs() == []
        return _introspection_result()

    monkeypatch.setattr(config_mod, "load", lambda: deployment)
    monkeypatch.setattr(introspection_mod, "OpenAIIntrospectionClient", FakeClient)
    monkeypatch.setattr(introspection_mod, "introspect", fake_introspect)
    monkeypatch.setattr(runner_mod, "IntrospectionVLMJobRunner", FakeRunner)

    result = _invoke("introspect", "--query", "What?", "--sensor", "warehouse")

    assert result.exit_code == int(expected_exit), result.output
    assert json.loads(result.output)["answer"] == "A forklift crossed the aisle."
    assert observed["client"]["model"] == "first-model"
    assert observed["closed"] is True
    assert observed["memory_service"] is injected_memory.service
    assert (observed["runner_memory"] is injected_memory) is persist_by_default
    assert injected_memory.service.list_jobs() == []
    assert list(tmp_path.rglob("*.md")) == []
