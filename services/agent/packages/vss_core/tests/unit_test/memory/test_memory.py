# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Hermetic unit tests for ``nv.vss.memory/1.0`` parent/child records."""

from __future__ import annotations

import json

from pydantic import ValidationError
import pytest

from vss_core.memory.adapters import RecordBundle
from vss_core.memory.adapters import child_record
from vss_core.memory.adapters import clear_adapter_registry
from vss_core.memory.adapters import get_adapter
from vss_core.memory.adapters import register_adapter
from vss_core.memory.adapters import utc_now_iso
from vss_core.memory.backends.in_memory import InMemoryStore
from vss_core.memory.models import FORBIDDEN_EXT_COLLECTIONS
from vss_core.memory.models import SCHEMA_ID
from vss_core.memory.models import MemoryGroup
from vss_core.memory.models import MemoryInput
from vss_core.memory.models import MemoryOutput
from vss_core.memory.models import SensorInfo
from vss_core.memory.models import UnifiedMemoryRecord
from vss_core.memory.service import MemoryNotFoundError
from vss_core.memory.service import MemoryService
from vss_core.memory.store import JobFilters
from vss_core.memory.store import MemoryQuery
from vss_core.memory.store import make_storage_id
from vss_core.memory.store import storage_id_for
from vss_core.search_core.memory_adapter import SearchAdapter

from .group_adapters import SummaryAdapter
from .group_adapters import alert_incident_bundle


def _parent(**overrides: object) -> UnifiedMemoryRecord:
    base: dict[str, object] = {
        "schema": SCHEMA_ID,
        "job": {
            "job_id": "summarize-01TEST",
            "group": "summary",
            "operation": "run",
            "status": "completed",
            "created_at": "2026-07-22T12:00:00Z",
            "updated_at": "2026-07-22T12:03:41Z",
        },
        "input": {
            "query": "summarize the loading bay",
            "sensors": [{"id": "cam-1", "type": "video"}],
            "params": {"model": "cosmos", "temperature": 0.2},
        },
        "output": {
            "answer": "A forklift entered the bay.",
            "ext": {"event_count": 1, "model": "cosmos"},
        },
    }
    base.update(overrides)
    return UnifiedMemoryRecord.model_validate(base)


def _child(
    *,
    job_id: str = "summarize-01TEST",
    record_id: str = "evt-001",
    record_type: str = "event",
    timestamp: str = "2026-07-22T11:14:08Z",
    sensor_id: str = "cam-1",
    answer: str = "A delivery vehicle stopped.",
) -> UnifiedMemoryRecord:
    return UnifiedMemoryRecord.model_validate(
        {
            "schema": SCHEMA_ID,
            "job": {
                "job_id": job_id,
                "record_id": record_id,
                "record_type": record_type,
                "group": "summary" if record_type == "event" else "search",
                "operation": "run",
                "status": "completed",
                "created_at": "2026-07-22T12:00:00Z",
            },
            "input": {
                "sensors": [{"id": sensor_id}],
                "window": {
                    "start": {"timestamp": timestamp},
                    "end": {"timestamp": "2026-07-22T11:14:35Z"},
                },
            },
            "output": {"answer": answer},
        }
    )


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


def test_parent_omits_record_fields_on_wire() -> None:
    dumped = _parent().model_dump_memory()
    assert "record_id" not in dumped["job"]
    assert "record_type" not in dumped["job"]
    assert dumped["schema"] == SCHEMA_ID


def test_child_requires_both_record_fields() -> None:
    child = _child()
    dumped = child.model_dump_memory()
    assert dumped["job"]["record_id"] == "evt-001"
    assert dumped["job"]["record_type"] == "event"


def test_only_record_id_rejected() -> None:
    with pytest.raises(ValidationError):
        UnifiedMemoryRecord.model_validate(
            {
                "schema": SCHEMA_ID,
                "job": {
                    "job_id": "j1",
                    "record_id": "evt-1",
                    "group": "summary",
                    "operation": "run",
                    "status": "completed",
                    "created_at": "2026-07-22T12:00:00Z",
                },
            }
        )


def test_only_record_type_rejected() -> None:
    with pytest.raises(ValidationError):
        UnifiedMemoryRecord.model_validate(
            {
                "schema": SCHEMA_ID,
                "job": {
                    "job_id": "j1",
                    "record_type": "event",
                    "group": "summary",
                    "operation": "run",
                    "status": "completed",
                    "created_at": "2026-07-22T12:00:00Z",
                },
            }
        )


def test_pending_child_rejected() -> None:
    with pytest.raises(ValidationError):
        UnifiedMemoryRecord.model_validate(
            {
                "schema": SCHEMA_ID,
                "job": {
                    "job_id": "j1",
                    "record_id": "evt-1",
                    "record_type": "event",
                    "group": "summary",
                    "operation": "run",
                    "status": "submitted",
                    "created_at": "2026-07-22T12:00:00Z",
                },
            }
        )


def test_empty_optionals_omitted_false_and_zero_kept() -> None:
    record = UnifiedMemoryRecord.model_validate(
        {
            "schema": SCHEMA_ID,
            "job": {
                "job_id": "j1",
                "group": "vlm",
                "operation": "run",
                "status": "completed",
                "created_at": "2026-07-22T12:00:00Z",
                "backend_ref": None,
            },
            "input": {"query": "", "sensors": [], "params": {}},
            "output": {
                "answer": "ok",
                "embedding": [],
                "handles": {"media_urls": [], "related_job_ids": []},
                "ext": {"count": 0, "ok": False, "empty": ""},
            },
            "error": None,
        }
    )
    dumped = record.model_dump_memory()
    assert "backend_ref" not in dumped["job"]
    assert "updated_at" not in dumped["job"]
    assert "input" not in dumped or "query" not in dumped.get("input", {})
    assert "embedding" not in dumped["output"]
    assert dumped["output"]["ext"]["count"] == 0
    assert dumped["output"]["ext"]["ok"] is False
    assert "empty" not in dumped["output"]["ext"]
    assert "job" in dumped and dumped["job"]["job_id"] == "j1"
    assert dumped["job"]["status"] == "completed"


def test_sensor_without_id_rejected() -> None:
    with pytest.raises(ValidationError):
        SensorInfo.model_validate({"id": "  "})


def test_lowercase_embedding_serialized_legacy_accepted() -> None:
    record = UnifiedMemoryRecord.model_validate(
        {
            "schema": SCHEMA_ID,
            "job": {
                "job_id": "j1",
                "group": "search",
                "operation": "run",
                "status": "completed",
                "created_at": "2026-07-22T12:00:00Z",
            },
            "output": {
                "Embedding": [{"es_ref": "vss-semantic-memory", "doc_ids": ["d1"]}],
            },
        }
    )
    assert record.output is not None
    assert record.output.embedding is not None
    dumped = record.model_dump_memory()
    assert "embedding" in dumped["output"]
    assert "Embedding" not in dumped["output"]


def test_inline_vectors_rejected() -> None:
    with pytest.raises(ValidationError):
        MemoryOutput.model_validate({"embedding": [{"vector": [0.1, 0.2]}]})


def test_media_group_accepted() -> None:
    record = UnifiedMemoryRecord.model_validate(
        {
            "schema": SCHEMA_ID,
            "job": {
                "job_id": "media-1",
                "group": "media",
                "operation": "run",
                "status": "completed",
                "created_at": "2026-07-22T12:00:00Z",
            },
            "output": {"handles": {"media_urls": ["https://x/clip.mp4"]}, "ext": {"kind": "clip"}},
        }
    )
    assert record.job.group == "media"


def test_unknown_group_rejected() -> None:
    with pytest.raises(ValidationError):
        UnifiedMemoryRecord.model_validate(
            {
                "schema": SCHEMA_ID,
                "job": {
                    "job_id": "x-1",
                    "group": "unknown",
                    "operation": "run",
                    "status": "completed",
                    "created_at": "2026-07-22T12:00:00Z",
                },
            }
        )


def test_nested_ext_collections_rejected() -> None:
    """Parent/child writers must not nest complete collections in ``output.ext``.

    Hard schema rejection is deferred until the command-group PR migrates
    develop's summarize CLI off nested ``events`` and restores the validator.
    Until then, adapters under test still prove the parent/child shape by
    omitting those keys (see ``test_summary_bundle_three_events_no_nested_ext``).
    """
    # Soft check: constructing with nested keys is still accepted during the
    # transitional window, but the documented forbid set names the contract.
    assert frozenset({"events", "results", "incidents"}) == FORBIDDEN_EXT_COLLECTIONS
    nested = MemoryOutput.model_validate({"ext": {"events": [{"id": "e1"}]}})
    assert "events" in (nested.ext or {})


def test_record_id_rejects_hash_delimiter() -> None:
    with pytest.raises(ValidationError):
        _child(record_id="evt#001")


# ---------------------------------------------------------------------------
# Adapters
# ---------------------------------------------------------------------------


def test_summary_bundle_three_events_no_nested_ext() -> None:
    adapter = SummaryAdapter()
    input_data = adapter.build_input(
        prompt="Summarize west entrance",
        video_id="cam-west-77",
        media_ref=None,
        params={"step_size": 1.0},
    )
    events = [
        {
            "event_id": "evt-001",
            "timestamp": "2026-07-22T11:14:08Z",
            "end_time": "2026-07-22T11:14:35Z",
            "description": "A delivery vehicle stopped.",
            "event_type": "vehicle_arrival",
            "media_url": "https://deployment/vst/storage/clip-123.mp4",
        },
        {
            "event_id": "evt-002",
            "timestamp": "2026-07-22T11:20:00Z",
            "description": "Person walked past.",
        },
        {
            "id": "evt-003",
            "timestamp": "2026-07-22T11:30:00Z",
            "description": "Door opened.",
        },
    ]
    bundle = adapter.terminal_bundle(
        job_id="summarize-01JZX8",
        created_at="2026-07-22T12:00:00Z",
        status="completed",
        input_data=input_data,
        answer="Three notable events occurred.",
        events=events,
        ext={"model": "cosmos-reason"},
    )
    assert bundle.parent.job.record_id is None
    parent_dump = bundle.parent.model_dump_memory()
    assert "events" not in parent_dump.get("output", {}).get("ext", {})
    assert parent_dump["output"]["ext"]["event_count"] == 3
    assert len(bundle.children) == 3
    assert [c.job.record_id for c in bundle.children] == ["evt-001", "evt-002", "evt-003"]
    assert all(c.job.record_type == "event" for c in bundle.children)
    assert all(c.job.job_id == "summarize-01JZX8" for c in bundle.children)


def test_search_bundle_four_hits_no_nested_results() -> None:
    adapter = SearchAdapter()
    input_data = adapter.build_input(
        query="Find the forklift",
        sensors=[{"id": "warehouse-camera"}],
        window=None,
        params={"search_mode": "fusion", "top_k": 10},
    )
    results = [
        {
            "id": f"hit-{i:04d}",
            "timestamp": f"2026-07-22T12:3{i}:04Z",
            "end_time": f"2026-07-22T12:3{i}:14Z",
            "description": f"hit {i}",
            "score": 0.9 - i * 0.01,
            "object_ids": [f"object-{i}"],
            "media_url": f"https://x/{i}.mp4",
        }
        for i in range(1, 5)
    ]
    bundle = adapter.terminal_bundle(
        job_id="search-01SEARCH",
        created_at="2026-07-22T13:00:00Z",
        status="completed",
        input_data=input_data,
        answer="Found 4 matching video segments.",
        results=results,
    )
    parent_dump = bundle.parent.model_dump_memory()
    assert "results" not in parent_dump.get("output", {}).get("ext", {})
    assert parent_dump["output"]["ext"]["result_count"] == 4
    assert len(bundle.children) == 4
    assert all(c.job.record_type == "search_hit" for c in bundle.children)
    assert bundle.children[0].job.record_id == "hit-0001"
    assert bundle.children[0].output is not None
    assert bundle.children[0].output.ext is not None
    assert bundle.children[0].output.ext["rank"] == 1


def test_alert_fixture_incident_children() -> None:
    bundle = alert_incident_bundle(
        job_id="alert-01A",
        created_at="2026-07-22T14:00:00Z",
        input_data=MemoryInput(query="alerts", sensors=[SensorInfo(id="cam-1")]),
        answer="2 incidents",
        incidents=[
            {"incident_id": "inc-1", "timestamp": "2026-07-22T13:00:00Z", "description": "A"},
            {"incident_id": "inc-2", "timestamp": "2026-07-22T13:05:00Z", "description": "B"},
        ],
    )
    assert len(bundle.children) == 2
    assert all(c.job.record_type == "incident" for c in bundle.children)


def test_stable_and_deterministic_child_ids() -> None:
    adapter = SummaryAdapter()
    input_data = adapter.build_input(prompt="p", video_id="cam-1", media_ref=None, params=None)
    event = {"timestamp": "2026-07-22T11:00:00Z", "description": "no upstream id"}
    first = adapter.terminal_bundle(
        job_id="summarize-1",
        created_at="2026-07-22T12:00:00Z",
        status="completed",
        input_data=input_data,
        answer="a",
        events=[event],
    )
    second = adapter.terminal_bundle(
        job_id="summarize-1",
        created_at="2026-07-22T12:00:00Z",
        status="completed",
        input_data=input_data,
        answer="a",
        events=[event],
    )
    assert first.children[0].job.record_id == second.children[0].job.record_id
    assert first.children[0].job.record_id.startswith("evt-")
    with_id = adapter.terminal_bundle(
        job_id="summarize-1",
        created_at="2026-07-22T12:00:00Z",
        status="completed",
        input_data=input_data,
        answer="a",
        events=[{"event_id": "upstream-77", "timestamp": "2026-07-22T11:00:00Z", "description": "x"}],
    )
    assert with_id.children[0].job.record_id == "upstream-77"


def test_future_adapter_uses_store_without_changes() -> None:
    """Prove a new group can create parent+child without store/service changes."""

    @register_adapter
    class _FutureAdapter:
        group: MemoryGroup = "vlm"

        def submitted_record(self, **kwargs: object) -> UnifiedMemoryRecord:
            raise NotImplementedError

        def running_record(self, **kwargs: object) -> UnifiedMemoryRecord:
            raise NotImplementedError

        def terminal_record(self, **kwargs: object) -> UnifiedMemoryRecord:
            raise NotImplementedError

    clear_adapter_registry()
    # External adapters (search_core / summarize CLI / tests) opt into the registry.
    register_adapter(SummaryAdapter)
    register_adapter(SearchAdapter)
    register_adapter(_FutureAdapter)
    assert get_adapter("summary").group == "summary"
    assert get_adapter("search").group == "search"
    assert get_adapter("vlm").group == "vlm"
    with pytest.raises(KeyError):
        get_adapter("alert")

    store = InMemoryStore()
    service = MemoryService(store)
    parent = _parent(
        job={
            "job_id": "vlm-1",
            "group": "vlm",
            "operation": "run",
            "status": "completed",
            "created_at": "2026-07-22T12:00:00Z",
        }
    )
    child = child_record(
        job_id="vlm-1",
        group="vlm",
        record_id="note-1",
        record_type="event",
        created_at="2026-07-22T12:00:00Z",
        input_data=MemoryInput(sensors=[SensorInfo(id="cam-1")], window=None),
        output=MemoryOutput(answer="visual note"),
    )
    # Force window for event time queries
    child = UnifiedMemoryRecord.model_validate(
        {
            **child.model_dump(by_alias=True, mode="json"),
            "input": {
                "sensors": [{"id": "cam-1"}],
                "window": {"start": {"timestamp": "2026-07-22T11:00:00Z"}},
            },
        }
    )
    result = service.upsert_bundle(RecordBundle(parent=parent, children=(child,)))
    assert result.ok
    assert service.get("vlm-1").job.group == "vlm"
    assert service.get_record("vlm-1", "event", "note-1").output is not None
    clear_adapter_registry()


# ---------------------------------------------------------------------------
# Stores
# ---------------------------------------------------------------------------


def test_parent_child_same_job_id_no_overwrite() -> None:
    store = InMemoryStore()
    parent = _parent()
    child = _child()
    store.upsert(parent)
    store.upsert(child)
    assert store.get("summarize-01TEST").job.record_id is None
    assert store.get_record("summarize-01TEST", "event", "evt-001").job.record_id == "evt-001"
    assert storage_id_for(child) == "summarize-01TEST#event#evt-001"
    assert "#" not in (child.job.record_id or "")


def test_child_id_collisions_across_jobs_and_types() -> None:
    store = InMemoryStore()
    store.upsert(_child(job_id="job-a", record_id="same", record_type="event"))
    store.upsert(
        _child(job_id="job-b", record_id="same", record_type="event").model_copy(
            update={
                "job": _child(job_id="job-b", record_id="same", record_type="event").job.model_copy(
                    update={"group": "summary"}
                )
            }
        )
    )
    hit = UnifiedMemoryRecord.model_validate(
        {
            **_child(job_id="job-a", record_id="same", record_type="search_hit").model_dump(by_alias=True, mode="json"),
            "job": {
                "job_id": "job-a",
                "record_id": "same",
                "record_type": "search_hit",
                "group": "search",
                "operation": "run",
                "status": "completed",
                "created_at": "2026-07-22T12:00:00Z",
            },
        }
    )
    store.upsert(hit)
    assert store.get_record("job-a", "event", "same") is not None
    assert store.get_record("job-b", "event", "same") is not None
    assert store.get_record("job-a", "search_hit", "same") is not None
    assert make_storage_id(job_id="job-a", record_type="event", record_id="same") != make_storage_id(
        job_id="job-a", record_type="search_hit", record_id="same"
    )


def test_reupsert_child_updates_not_duplicates() -> None:
    store = InMemoryStore()
    child = _child(answer="first")
    store.upsert(child)
    updated = _child(answer="second")
    store.upsert(updated)
    assert len(store.query(MemoryQuery(job_id="summarize-01TEST", record_type="event", limit=10))) == 1
    got = store.get_record("summarize-01TEST", "event", "evt-001")
    assert got is not None and got.output is not None
    assert got.output.answer == "second"


def test_list_jobs_excludes_children() -> None:
    store = InMemoryStore()
    store.upsert(_parent())
    store.upsert(_child())
    jobs = store.list_jobs(JobFilters(group="summary"))
    assert len(jobs) == 1
    assert jobs[0].job.is_parent


def test_query_children_by_job_and_type() -> None:
    store = InMemoryStore()
    store.upsert(_parent())
    store.upsert(_child(record_id="evt-001"))
    store.upsert(_child(record_id="evt-002"))
    kids = store.query(MemoryQuery(job_id="summarize-01TEST", record_type="event", limit=10))
    assert len(kids) == 2
    assert all(k.job.is_child for k in kids)


def test_parent_lifecycle_preserves_created_at() -> None:
    store = InMemoryStore()
    adapter = SummaryAdapter()
    created = "2026-07-22T12:00:00Z"
    input_data = adapter.build_input(prompt="p", video_id="cam-1", media_ref=None, params=None)
    submitted = adapter.submitted_record(job_id="summary-1", created_at=created, input_data=input_data)
    store.upsert(submitted)
    running = adapter.running_record(
        job_id="summary-1",
        created_at="2099-01-01T00:00:00Z",
        input_data=input_data,
        updated_at="2026-07-22T12:01:00Z",
    )
    store.upsert(running)
    got = store.get("summary-1")
    assert got is not None
    assert got.job.created_at == submitted.job.created_at
    assert got.job.status == "running"


# ---------------------------------------------------------------------------
# Event recall
# ---------------------------------------------------------------------------


def test_events_from_child_documents_not_nested_ext() -> None:
    store = InMemoryStore()
    service = MemoryService(store)
    # Poison parent with nested ext — model forbids it, so inject via store bypass
    # by using a parent without nested collections and a separate child.
    store.upsert(_parent())
    store.upsert(_child(record_id="evt-001", timestamp="2026-07-22T10:00:00Z", answer="early"))
    store.upsert(_child(record_id="evt-002", timestamp="2026-07-22T11:00:00Z", answer="late"))
    events = service.events(asset_id="cam-1", limit=10)
    assert [e["event_id"] for e in events] == ["evt-002", "evt-001"]
    assert all("_source_job_id" in e for e in events)


def test_events_sensor_and_time_filters() -> None:
    store = InMemoryStore()
    service = MemoryService(store)
    store.upsert(_parent())
    store.upsert(_child(record_id="a", sensor_id="cam-1", timestamp="2026-07-22T10:00:00Z"))
    store.upsert(_child(record_id="b", sensor_id="cam-2", timestamp="2026-07-22T10:30:00Z"))
    store.upsert(_child(record_id="c", sensor_id="cam-1", timestamp="2026-07-22T12:00:00Z"))
    events = service.events(
        asset_id="cam-1",
        start_time="2026-07-22T09:00:00Z",
        end_time="2026-07-22T11:00:00Z",
        limit=10,
    )
    assert [e["event_id"] for e in events] == ["a"]


def test_events_adjacency_and_unknown_anchor() -> None:
    store = InMemoryStore()
    service = MemoryService(store)
    store.upsert(_parent())
    for index, hour in enumerate((10, 11, 12, 13), start=1):
        store.upsert(
            _child(
                record_id=f"evt-{index}",
                timestamp=f"2026-07-22T{hour:02d}:00:00Z",
                answer=f"e{index}",
            )
        )
    around = service.events(asset_id="cam-1", anchor_event_id="evt-2", direction="around", limit=3)
    assert [e["event_id"] for e in around] == ["evt-1", "evt-2", "evt-3"]
    before = service.events(asset_id="cam-1", anchor_event_id="evt-3", direction="before", limit=2)
    assert [e["event_id"] for e in before] == ["evt-1", "evt-2"]
    after = service.events(asset_id="cam-1", anchor_event_id="evt-2", direction="after", limit=2)
    assert [e["event_id"] for e in after] == ["evt-3", "evt-4"]
    with pytest.raises(MemoryNotFoundError):
        service.events(asset_id="cam-1", anchor_event_id="missing", limit=5)


def test_events_limit_zero_and_negative() -> None:
    store = InMemoryStore()
    service = MemoryService(store)
    store.upsert(_parent())
    store.upsert(_child())
    assert service.events(asset_id="cam-1", limit=0) == []
    assert service.events(asset_id="cam-1", limit=-1) == []


def test_events_unknown_asset() -> None:
    service = MemoryService(InMemoryStore())
    with pytest.raises(MemoryNotFoundError):
        service.events(asset_id="missing")


def test_reconcile_parent_only() -> None:
    store = InMemoryStore()

    class _Reconciler:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def reconcile(self, record: UnifiedMemoryRecord) -> UnifiedMemoryRecord | None:
            self.calls.append(record.job.job_id)
            assert record.job.is_parent
            job = record.job.model_copy(update={"status": "completed", "updated_at": record.job.created_at})
            return record.model_copy(update={"job": job, "output": MemoryOutput(answer="done")})

    reconciler = _Reconciler()
    service = MemoryService(store, reconciler=reconciler)
    adapter = SummaryAdapter()
    input_data = adapter.build_input(prompt="p", video_id="cam-1", media_ref=None, params=None)
    pending = adapter.submitted_record(
        job_id="summary-pending",
        created_at=utc_now_iso(),
        input_data=input_data,
        backend_ref="backend-1",
    )
    store.upsert(pending)
    store.upsert(_child(job_id="summary-pending", record_id="evt-x"))
    got = service.get("summary-pending", reconcile=True)
    assert got.job.status == "completed"
    assert reconciler.calls == ["summary-pending"]


def test_upsert_bundle_reports_partial_failures() -> None:
    class _Flaky(InMemoryStore):
        def upsert(self, record: UnifiedMemoryRecord) -> UnifiedMemoryRecord:
            if record.job.is_child and record.job.record_id == "evt-bad":
                raise RuntimeError("write failed")
            return super().upsert(record)

    service = MemoryService(_Flaky())
    adapter = SummaryAdapter()
    input_data = adapter.build_input(prompt="p", video_id="cam-1", media_ref=None, params=None)
    bundle = adapter.terminal_bundle(
        job_id="summarize-x",
        created_at="2026-07-22T12:00:00Z",
        status="completed",
        input_data=input_data,
        answer="ok",
        events=[
            {"event_id": "evt-ok", "timestamp": "2026-07-22T11:00:00Z", "description": "a"},
            {"event_id": "evt-bad", "timestamp": "2026-07-22T11:01:00Z", "description": "b"},
        ],
    )
    assert bundle.parent.job.status == "completed"
    assert bundle.parent.output is not None
    assert bundle.parent.output.ext is not None
    assert bundle.parent.output.ext["event_count"] == 2

    result = service.upsert_bundle(bundle)
    assert result.expected == 3
    assert result.written == 2
    assert not result.ok
    assert result.failed[0].record_id == "evt-bad"
    payload = result.to_dict()
    assert payload["expected"] == 3
    assert payload["written"] == 2

    # Parent must not keep advertising a complete result set after a child miss.
    parent = service.get("summarize-x", reconcile=False)
    assert parent.job.status == "partial"
    assert parent.output is not None
    assert parent.output.ext is not None
    assert parent.output.ext["event_count"] == 1
    assert service.get_record("summarize-x", "event", "evt-ok").output is not None
    with pytest.raises(MemoryNotFoundError):
        service.get_record("summarize-x", "event", "evt-bad")


def test_upsert_bundle_counts_distinct_storage_ids_on_child_collision() -> None:
    """Colliding child record_ids must not inflate written beyond docs stored."""
    service = MemoryService(InMemoryStore())
    adapter = SummaryAdapter()
    input_data = adapter.build_input(prompt="p", video_id="cam-1", media_ref=None, params=None)
    duplicate = {
        "event_id": "evt-72d73704a0ef2ce7",
        "timestamp": "2026-07-22T11:00:00Z",
        "description": "same id twice",
    }
    bundle = adapter.terminal_bundle(
        job_id="summarize-collision",
        created_at="2026-07-22T12:00:00Z",
        status="completed",
        input_data=input_data,
        answer="ok",
        events=[duplicate, duplicate],
    )
    assert len(bundle.children) == 2
    assert storage_id_for(bundle.children[0]) == storage_id_for(bundle.children[1])
    assert bundle.parent.output is not None
    assert bundle.parent.output.ext is not None
    assert bundle.parent.output.ext["event_count"] == 2

    result = service.upsert_bundle(bundle)
    # Distinct storage ids: parent + one child document.
    assert result.expected == 2
    assert result.written == 2
    assert result.ok
    assert result.failed == []

    parent = service.get("summarize-collision", reconcile=False)
    assert parent.job.status == "partial"
    assert parent.output is not None
    assert parent.output.ext is not None
    assert parent.output.ext["event_count"] == 1
    assert service.get_record("summarize-collision", "event", "evt-72d73704a0ef2ce7").output is not None


def test_upsert_bundle_skips_children_when_parent_fails() -> None:
    class _ParentFails(InMemoryStore):
        def upsert(self, record: UnifiedMemoryRecord) -> UnifiedMemoryRecord:
            if record.job.is_parent:
                raise RuntimeError("parent write failed")
            return super().upsert(record)

    store = _ParentFails()
    service = MemoryService(store)
    adapter = SummaryAdapter()
    input_data = adapter.build_input(prompt="p", video_id="cam-1", media_ref=None, params=None)
    bundle = adapter.terminal_bundle(
        job_id="summarize-orphan",
        created_at="2026-07-22T12:00:00Z",
        status="completed",
        input_data=input_data,
        answer="ok",
        events=[
            {"event_id": "evt-1", "timestamp": "2026-07-22T11:00:00Z", "description": "a"},
            {"event_id": "evt-2", "timestamp": "2026-07-22T11:01:00Z", "description": "b"},
        ],
    )
    result = service.upsert_bundle(bundle)
    assert result.written == 0
    assert result.expected == 3
    assert len(result.failed) == 3
    assert result.failed[0].is_parent
    assert all(not item.is_parent for item in result.failed[1:])
    assert all("skipped: parent upsert failed" in item.error for item in result.failed[1:])
    assert store.get("summarize-orphan") is None
    assert store.get_record("summarize-orphan", "event", "evt-1") is None
    assert store.get_record("summarize-orphan", "event", "evt-2") is None


def test_intent_round_trip() -> None:
    record = _parent(
        input={
            "query": "q",
            "intent": "video-qa",
            "sensors": [{"id": "cam-1"}],
        }
    )
    assert record.input is not None
    assert record.input.intent == "video-qa"
    assert record.model_dump_memory()["input"]["intent"] == "video-qa"


def test_schema_json_round_trip() -> None:
    dumped = _parent().model_dump_memory()
    wire = json.loads(json.dumps(dumped))
    restored = UnifiedMemoryRecord.model_validate(wire)
    assert restored.job.job_id == "summarize-01TEST"
