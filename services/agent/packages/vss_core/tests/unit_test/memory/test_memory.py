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
from vss_core.memory.models import SCHEMA_ID
from vss_core.memory.models import MemoryGroup
from vss_core.memory.models import MemoryInput
from vss_core.memory.models import MemoryOutput
from vss_core.memory.models import UnifiedMemoryRecord
from vss_core.memory.service import MemoryNotFoundError
from vss_core.memory.service import MemoryService
from vss_core.memory.store import InMemoryStore
from vss_core.memory.store import JobFilters
from vss_core.memory.store import MemoryQuery


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
                "events": [{"id": "evt-1", "description": "forklift"}],
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
            "job": first.job.model_copy(
                update={"status": "running", "updated_at": "2026-07-22T12:01:00Z", "created_at": "2099-01-01T00:00:00Z"}
            )
        },
    )
    stored = store.upsert(second)
    assert stored.job.created_at == "2026-07-22T12:00:00Z"
    assert stored.job.updated_at == "2026-07-22T12:01:00Z"
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
        output=SummaryAdapter.build_output(answer="done", events=[{"id": "e1"}]),
    )
    service.upsert(terminal)
    got = service.get("summary-1", reconcile=False)
    assert got.job.status == "completed"
    assert got.job.created_at == created
    assert got.job.updated_at != created or got.output.answer == "done"
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
                    "ext": {"events": [{"id": f"n-{index}", "description": "unrelated activity"}]},
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
        events=[{"event_id": "e1", "description": "x"}],
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
