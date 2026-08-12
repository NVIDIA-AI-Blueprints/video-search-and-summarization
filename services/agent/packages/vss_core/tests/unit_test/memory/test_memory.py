# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Hermetic unit tests for ``nv.vss.memory/1.0`` models, store, and adapters."""

from __future__ import annotations

import json
import subprocess
import sys

from pydantic import ValidationError
import pytest

from vss_core.memory.adapters import SearchAdapter
from vss_core.memory.adapters import SummaryAdapter
from vss_core.memory.adapters import clear_adapter_registry
from vss_core.memory.adapters import get_adapter
from vss_core.memory.adapters import register_adapter
from vss_core.memory.adapters import utc_now_iso
from vss_core.memory.backends.in_memory import InMemoryStore
from vss_core.memory.models import SCHEMA_ID
from vss_core.memory.models import JobInfo
from vss_core.memory.models import MemoryGroup
from vss_core.memory.models import MemoryInput
from vss_core.memory.models import MemoryOutput
from vss_core.memory.models import TimestampPoint
from vss_core.memory.models import UnifiedMemoryRecord
from vss_core.memory.service import MemoryNotFoundError
from vss_core.memory.service import MemoryService
from vss_core.memory.store import JobFilters
from vss_core.memory.store import MemoryQuery
from vss_core._foundation.time import datetime_to_iso8601
from vss_core._foundation.time import iso8601_to_datetime


def _sample_record(**overrides: object) -> UnifiedMemoryRecord:
    base = {
        "schema": SCHEMA_ID,
        "job": {
            "job_id": "summary-01TESTJOBID000000000000",
            "group": "summary",
            "operation": "run",
            "status": "completed",
            "created_at": "2026-07-22T12:00:00Z",
            "updated_at": "2026-07-22T12:03:41Z",
            "backend_ref": None,
        },
        "input": {
            "query": "summarize the loading bay",
            "sensors": [{"id": "cam-1", "type": "video", "info": {"source": "vst"}}],
            "window": None,
            "params": {"model": "cosmos", "temperature": 0.2},
        },
        "output": {
            "answer": "A forklift entered the bay.",
            "Embedding": [],
            "handles": {
                "media_urls": [],
                "related_job_ids": [],
            },
            "ext": {
                "event_ids": ["evt-1"],
                "events": [{"id": "evt-1", "description": "forklift", "timestamp": "2026-07-22T12:01:00Z"}],
            },
        },
        "error": None,
    }
    base.update(overrides)
    return UnifiedMemoryRecord.model_validate(base)


def test_schema_round_trip_preserves_embedding_alias() -> None:
    record = _sample_record()
    dumped = record.model_dump_memory()
    assert dumped["schema"] == SCHEMA_ID
    assert "Embedding" in dumped["output"]
    assert "embedding" not in dumped["output"]
    restored = UnifiedMemoryRecord.model_validate(dumped)
    assert restored.output.Embedding == []
    assert restored.output.answer == "A forklift entered the bay."
    wire = json.loads(json.dumps(dumped))
    assert wire["output"]["Embedding"] == []


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
                    "updated_at": "2026-07-22T12:00:00Z",
                },
                "input": {},
                "output": {},
                "error": None,
            }
        )


def test_media_group_rejected_per_vios8() -> None:
    """VIOS does not write memory — ``media`` is not a valid job.group."""
    with pytest.raises(ValidationError):
        UnifiedMemoryRecord.model_validate(
            {
                "schema": SCHEMA_ID,
                "job": {
                    "job_id": "media-01TESTJOBID000000000000",
                    "group": "media",
                    "operation": "run",
                    "status": "completed",
                    "created_at": "2026-07-22T12:00:00Z",
                    "updated_at": "2026-07-22T12:00:00Z",
                },
                "input": {},
                "output": {},
                "error": None,
            }
        )


def test_input_intent_accepted_and_optional() -> None:
    """SDD v0.9 ``intent`` must not hard-fail under ``extra=\"forbid\"``."""
    without = _sample_record()
    assert without.input.intent is None

    with_intent = _sample_record(
        input={
            "query": "summarize the loading bay",
            "intent": "video-qa",
            "sensors": [{"id": "cam-1", "type": "video", "info": {"source": "vst"}}],
            "window": None,
            "params": {"model": "cosmos"},
        }
    )
    assert with_intent.input.intent == "video-qa"
    dumped = with_intent.model_dump_memory()
    assert dumped["input"]["intent"] == "video-qa"
    restored = UnifiedMemoryRecord.model_validate(dumped)
    assert restored.input.intent == "video-qa"


def test_open_params_info_and_ext() -> None:
    record = _sample_record()
    assert record.input.params["temperature"] == 0.2
    assert record.input.sensors[0].info["source"] == "vst"
    assert "events" in record.output.ext


def test_no_legacy_fields_in_serialization() -> None:
    dumped = _sample_record().model_dump_memory()
    blob = json.dumps(dumped)
    for forbidden in ("memory_id", "domain", "record_type", "origin", '"body"'):
        assert forbidden not in blob or (forbidden == '"body"' and "body" not in dumped)


def test_idempotent_upsert_preserves_created_at() -> None:
    store = InMemoryStore()
    first = _sample_record()
    store.upsert(first)
    second = first.model_copy(
        deep=True,
        update={
            "job": JobInfo.model_validate(
                {
                    **first.job.model_dump(mode="json"),
                    "status": "running",
                    "updated_at": "2026-07-22T12:01:00Z",
                    "created_at": "2099-01-01T00:00:00Z",
                }
            )
        },
    )
    stored = store.upsert(second)
    assert stored.job.created_at == iso8601_to_datetime("2026-07-22T12:00:00Z")
    assert stored.job.updated_at == iso8601_to_datetime("2026-07-22T12:01:00Z")
    assert store.upsert_ids == [first.job.job_id, first.job.job_id]


def test_lifecycle_transitions_via_service() -> None:
    store = InMemoryStore()
    service = MemoryService(store)
    adapter = SummaryAdapter()
    created = "2026-07-22T12:00:00Z"
    input_data = SummaryAdapter.build_input(
        prompt="p",
        video_id="cam-1",
        media_ref={"source": "vst"},
        params={"model": "m"},
    )
    submitted = adapter.submitted_record(job_id="summary-1", created_at=created, input_data=input_data)
    service.upsert(submitted)
    running = adapter.running_record(job_id="summary-1", created_at=created, input_data=input_data)
    service.upsert(running)
    terminal = adapter.terminal_record(
        job_id="summary-1",
        created_at=created,
        status="completed",
        input_data=input_data,
        output=SummaryAdapter.build_output(
            answer="done",
            events=[{"id": "e1", "timestamp": "2026-07-22T12:01:00Z"}],
        ),
    )
    service.upsert(terminal)
    got = service.get("summary-1", reconcile=False)
    assert got.job.status == "completed"
    assert got.job.created_at == iso8601_to_datetime(created)
    assert got.job.updated_at != iso8601_to_datetime(created) or got.output.answer == "done"
    assert store.upsert_ids == ["summary-1", "summary-1", "summary-1"]


def test_status_get_list_memory_first_no_reconciler_calls() -> None:
    store = InMemoryStore()
    service = MemoryService(store)
    record = _sample_record()
    service.upsert(record)
    assert service.status(record.job.job_id).job.status == "completed"
    assert service.get(record.job.job_id).output.answer == record.output.answer
    listed = service.list_jobs(JobFilters(group="summary"))
    assert len(listed) == 1


def test_pending_without_backend_ref_does_not_reconcile() -> None:
    calls: list[str] = []

    class _Reconciler:
        def reconcile(self, record: UnifiedMemoryRecord) -> UnifiedMemoryRecord | None:
            calls.append(record.job.job_id)
            return None

    store = InMemoryStore()
    service = MemoryService(store, reconciler=_Reconciler())
    adapter = SummaryAdapter()
    created = utc_now_iso()
    pending = adapter.running_record(
        job_id="summary-pending",
        created_at=created,
        input_data=MemoryInput(query="q"),
        backend_ref=None,
    )
    service.upsert(pending)
    got = service.get("summary-pending", reconcile=True)
    assert got.job.status == "running"
    assert calls == []


def test_events_from_persisted_ext_only() -> None:
    store = InMemoryStore()
    service = MemoryService(store)
    service.upsert(_sample_record())
    events = service.events(asset_id="cam-1", match="forklift")
    assert len(events) == 1
    assert events[0]["id"] == "evt-1"
    with pytest.raises(MemoryNotFoundError):
        service.events(asset_id="missing")


def test_events_match_scans_beyond_former_record_cap() -> None:
    """Filters run after a wide record scan so older matching events are not dropped.

    The previous ``limit * 4`` job cap (200 when ``limit=50``) truncated before
    ``match``, so a hit only present on an older record was omitted.
    """
    store = InMemoryStore()
    service = MemoryService(store)
    asset = "cam-scan"
    # Newest-first store ordering: create many recent non-matching jobs, then
    # one older job that holds the only matching event.
    for index in range(250):
        service.upsert(
            _sample_record(
                job={
                    "job_id": f"summary-recent-{index:03d}",
                    "group": "summary",
                    "operation": "run",
                    "status": "completed",
                    "created_at": f"2026-07-22T13:{index // 60:02d}:{index % 60:02d}Z",
                    "updated_at": f"2026-07-22T14:{index // 60:02d}:{index % 60:02d}Z",
                    "backend_ref": None,
                },
                input={
                    "query": "noise",
                    "sensors": [{"id": asset, "type": "video", "info": {"source": "vst"}}],
                    "window": None,
                    "params": {},
                },
                output={
                    "answer": "noise",
                    "Embedding": [],
                    "handles": {"media_urls": [], "related_job_ids": []},
                    "ext": {
                        "events": [
                            {
                                "id": f"n-{index}",
                                "description": "unrelated activity",
                                "timestamp": "2026-07-22T14:00:00Z",
                            }
                        ]
                    },
                },
            )
        )
    service.upsert(
        _sample_record(
            job={
                "job_id": "summary-old-match",
                "group": "summary",
                "operation": "run",
                "status": "completed",
                "created_at": "2026-07-22T10:00:00Z",
                "updated_at": "2026-07-22T10:00:00Z",
                "backend_ref": None,
            },
            input={
                "query": "find forklift",
                "sensors": [{"id": asset, "type": "video", "info": {"source": "vst"}}],
                "window": None,
                "params": {},
            },
            output={
                "answer": "forklift crossed bay",
                "Embedding": [],
                "handles": {"media_urls": [], "related_job_ids": []},
                "ext": {
                    "events": [
                        {
                            "id": "evt-old",
                            "description": "yellow forklift entered loading bay",
                            "timestamp": "2026-07-22T10:00:30Z",
                        }
                    ]
                },
            },
        )
    )

    events = service.events(asset_id=asset, match="forklift", limit=50)
    assert len(events) == 1
    assert events[0]["id"] == "evt-old"
    assert events[0]["_source_job_id"] == "summary-old-match"


def test_search_and_summary_adapters_map_results() -> None:
    summary_out = SummaryAdapter.build_output(
        answer="text",
        events=[{"event_id": "e1", "description": "x", "timestamp": "2026-07-22T12:01:00Z"}],
        ext={"model": "m"},
    )
    assert summary_out.ext["event_ids"] == ["e1"]
    assert summary_out.ext["events"][0]["event_id"] == "e1"
    assert summary_out.handles.media_urls == []

    search_out = SearchAdapter.build_output(
        answer="hit",
        results=[{"object_ids": ["o1"], "screenshot_url": "http://x", "similarity": 0.9}],
    )
    assert search_out.ext["object_ids"] == ["o1"]
    assert search_out.handles.media_urls == ["http://x"]
    assert search_out.ext["result_count"] == 1
    assert "event_ids" not in search_out.handles.model_dump()
    assert "object_ids" not in search_out.handles.model_dump()
    assert "frame_ids" not in search_out.handles.model_dump()


def test_summary_events_require_timestamp() -> None:
    with pytest.raises(ValueError, match="require a timestamp"):
        SummaryAdapter.build_output(answer="text", events=[{"id": "e1", "description": "x"}])


def test_search_build_input_rejects_half_open_window() -> None:
    with pytest.raises(ValueError, match="both start and end"):
        SearchAdapter.build_input(
            query="q",
            sensors=None,
            window={"start": {"timestamp": "2026-07-22T10:00:00Z"}},
            params=None,
        )


def test_events_time_filter_excludes_untimestamped() -> None:
    store = InMemoryStore()
    service = MemoryService(store)
    service.upsert(
        _sample_record(
            output={
                "answer": "a",
                "Embedding": [],
                "handles": {"media_urls": [], "related_job_ids": []},
                "ext": {
                    "events": [
                        {"id": "with-ts", "description": "forklift", "timestamp": "2026-07-22T10:30:00Z"},
                        {"id": "no-ts", "description": "forklift"},
                    ]
                },
            }
        )
    )
    events = service.events(
        asset_id="cam-1",
        start_time="2026-07-22T10:00:00Z",
        end_time="2026-07-22T11:00:00Z",
    )
    assert [event["id"] for event in events] == ["with-ts"]


def test_register_fake_future_adapter_without_common_code_changes() -> None:
    clear_adapter_registry()

    @register_adapter
    class AlertAdapter:
        group: MemoryGroup = "alert"

        def submitted_record(self, **kwargs: object) -> UnifiedMemoryRecord:
            return SummaryAdapter().submitted_record(  # type: ignore[arg-type]
                job_id=str(kwargs["job_id"]),
                created_at=str(kwargs["created_at"]),
                input_data=kwargs["input_data"],  # type: ignore[arg-type]
            )

        def running_record(self, **kwargs: object) -> UnifiedMemoryRecord:
            return self.submitted_record(**kwargs)

        def terminal_record(self, **kwargs: object) -> UnifiedMemoryRecord:
            return SummaryAdapter().terminal_record(  # type: ignore[arg-type]
                job_id=str(kwargs["job_id"]),
                created_at=str(kwargs["created_at"]),
                status=kwargs["status"],  # type: ignore[arg-type]
                input_data=kwargs["input_data"],  # type: ignore[arg-type]
            )

    adapter = get_adapter("alert")
    assert adapter.group == "alert"
    clear_adapter_registry()


def test_query_filters() -> None:
    store = InMemoryStore()
    service = MemoryService(store)
    service.upsert(_sample_record())
    other = _sample_record()
    other = other.model_copy(
        deep=True,
        update={
            "job": other.job.model_copy(update={"job_id": "search-1", "group": "search"}),
            "input": MemoryInput(query="forklift", sensors=[], params={"search_mode": "fusion"}),
            "output": MemoryOutput(answer=None, ext={"results": []}),
        },
    )
    service.upsert(other)
    assert len(service.query(MemoryQuery(group="search"))) == 1
    assert len(service.query(MemoryQuery(text="loading bay"))) == 1


def test_bare_memory_import_is_nat_free() -> None:
    code = (
        "import sys\n"
        "import vss_core.memory as m\n"
        "assert 'nat' not in sys.modules\n"
        "assert 'torch' not in sys.modules\n"
        "assert 'elasticsearch' not in sys.modules\n"
        "assert m.SCHEMA_ID == 'nv.vss.memory/1.0'\n"
    )
    completed = subprocess.run([sys.executable, "-c", code], check=False, capture_output=True, text=True)
    assert completed.returncode == 0, completed.stderr


def test_iso_instants_reject_garbage_and_normalize_wire() -> None:
    with pytest.raises(ValidationError):
        JobInfo.model_validate(
            {
                "job_id": "summary-1",
                "group": "summary",
                "operation": "run",
                "status": "completed",
                "created_at": "banana",
                "updated_at": "not-a-timestamp-at-all",
            }
        )
    with pytest.raises(ValidationError):
        TimestampPoint.model_validate({"timestamp": ""})

    record = _sample_record(
        job={
            "job_id": "summary-1",
            "group": "summary",
            "operation": "run",
            "status": "completed",
            "created_at": "2026-07-22T12:00:00+00:00",
            "updated_at": "2026-07-22T12:00:00.500000Z",
            "backend_ref": None,
        }
    )
    dumped = record.model_dump_memory()
    assert dumped["job"]["created_at"] == "2026-07-22T12:00:00Z"
    assert dumped["job"]["updated_at"] == "2026-07-22T12:00:00.500000Z"


def test_inmemory_since_filter_uses_instant_not_lexicographic_iso() -> None:
    """Fractional-second created_at must not sort before a whole-second since bound."""
    store = InMemoryStore()
    store.upsert(
        _sample_record(
            job={
                "job_id": "summary-frac",
                "group": "summary",
                "operation": "run",
                "status": "completed",
                "created_at": "2026-07-22T12:00:00.500000Z",
                "updated_at": "2026-07-22T12:00:00.500000Z",
                "backend_ref": None,
            }
        )
    )
    assert len(store.query(MemoryQuery(since="2026-07-22T12:00:00Z"))) == 1
    assert len(store.query(MemoryQuery(until="2026-07-22T12:00:00Z"))) == 0
    assert datetime_to_iso8601(store.get("summary-frac").job.created_at) == "2026-07-22T12:00:00.500000Z"


def _event_job(job_id: str, event_id: str, event_ts: str, *, updated_at: str) -> UnifiedMemoryRecord:
    return _sample_record(
        job={
            "job_id": job_id,
            "group": "summary",
            "operation": "run",
            "status": "completed",
            "created_at": event_ts,
            "updated_at": updated_at,
            "backend_ref": None,
        },
        input={
            "query": "q",
            "sensors": [{"id": "cam-1", "type": "video", "info": {}}],
            "window": None,
            "params": {},
        },
        output={
            "answer": "a",
            "Embedding": [],
            "handles": {"media_urls": [], "related_job_ids": []},
            "ext": {"events": [{"id": event_id, "description": event_id, "timestamp": event_ts}]},
        },
    )


def test_events_adjacent_directions_are_chronological() -> None:
    """before/after follow event time, not newest-first job write order."""
    store = InMemoryStore()
    service = MemoryService(store)
    # Upsert newest job first so write order disagrees with event chronology.
    service.upsert(_event_job("summary-late", "evt-LATE", "2026-07-22T12:00:00Z", updated_at="2026-07-22T15:00:00Z"))
    service.upsert(_event_job("summary-anchor", "evt-ANCHOR", "2026-07-22T11:00:00Z", updated_at="2026-07-22T14:00:00Z"))
    service.upsert(_event_job("summary-early", "evt-EARLY", "2026-07-22T10:00:00Z", updated_at="2026-07-22T13:00:00Z"))

    before = service.events(asset_id="cam-1", anchor_event_id="evt-ANCHOR", direction="before")
    after = service.events(asset_id="cam-1", anchor_event_id="evt-ANCHOR", direction="after")
    assert [event["id"] for event in before] == ["evt-EARLY"]
    assert [event["id"] for event in after] == ["evt-LATE"]


def test_events_before_limit_keeps_closest_neighbours() -> None:
    store = InMemoryStore()
    service = MemoryService(store)
    for hour in range(10):
        stamp = f"2026-07-22T{10 + hour:02d}:00:00Z"
        service.upsert(
            _event_job(
                f"summary-{hour:02d}",
                f"e-{hour:02d}",
                stamp,
                updated_at=f"2026-07-22T{20 - hour:02d}:00:00Z",
            )
        )
    # Anchor on oldest; before is empty. Anchor on newest; before+limit=3 → e-07,e-08,e-09.
    before = service.events(asset_id="cam-1", anchor_event_id="e-09", direction="before", limit=3)
    assert [event["id"] for event in before] == ["e-06", "e-07", "e-08"]


def test_events_unknown_anchor_raises() -> None:
    store = InMemoryStore()
    service = MemoryService(store)
    service.upsert(_sample_record())
    with pytest.raises(MemoryNotFoundError, match="anchor_event_id not found"):
        service.events(asset_id="cam-1", anchor_event_id="missing-evt", direction="before")


def test_events_window_rejected_until_implemented() -> None:
    store = InMemoryStore()
    service = MemoryService(store)
    service.upsert(_sample_record())
    with pytest.raises(ValueError, match="window"):
        service.events(asset_id="cam-1", anchor_event_id="evt-1", direction="around", window="30s")
