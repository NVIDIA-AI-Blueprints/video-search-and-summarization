# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Unit tests for harness-native Markdown memory note sinks."""

from __future__ import annotations

from datetime import UTC
from datetime import datetime
import threading
from typing import TYPE_CHECKING

import pytest

from vss_core.memory.models import SCHEMA_ID
from vss_core.memory.models import UnifiedMemoryRecord
from vss_core.memory.notes import MemoryNoteStatus
from vss_core.memory.notes import OpenClawMarkdownSink
from vss_core.memory.notes import render_memory_note
from vss_core.memory.notes import resolve_note_path

if TYPE_CHECKING:
    from pathlib import Path


def _record(**overrides: object) -> UnifiedMemoryRecord:
    base: dict[str, object] = {
        "schema": SCHEMA_ID,
        "job": {
            "job_id": "summary-01KABC",
            "group": "summary",
            "operation": "run",
            "status": "completed",
            "created_at": "2026-08-07T10:00:00Z",
            "updated_at": "2026-08-07T10:05:00Z",
            "backend_ref": None,
        },
        "input": {
            "query": "Summarize activity at the west entrance from 10:00 to 11:00.",
            "sensors": [{"id": "cam-west-77", "type": "video", "info": {}}],
            "window": {
                "start": {"timestamp": "2026-08-07T10:00:00-07:00"},
                "end": {"timestamp": "2026-08-07T11:00:00-07:00"},
            },
            "params": {},
        },
        "output": {
            "answer": "Three delivery vehicles arrived.",
            "Embedding": [{"es_ref": "emb-1", "doc_ids": ["d1"], "kind": "summary"}],
            "handles": {
                "media_urls": ["https://example.com/clip.mp4?X-Amz-Signature=secret"],
                "related_job_ids": [],
            },
            "ext": {
                "event_ids": ["evt-1", "evt-2"],
                "events": [{"id": "evt-1", "description": "arrive"}] * 20,
            },
        },
        "error": None,
    }
    base.update(overrides)
    return UnifiedMemoryRecord.model_validate(base)


def test_render_includes_answer_and_pointer_excludes_embeddings_and_ext() -> None:
    text = render_memory_note(_record(), persisted=True)
    assert "Three delivery vehicles arrived." in text
    assert "vss memory get summary-01KABC" in text
    assert "cam-west-77" in text
    assert "Embedding" not in text
    assert "emb-1" not in text
    assert "X-Amz-Signature" not in text
    assert '"events"' not in text
    assert "evt-1" in text


def test_render_all_five_groups() -> None:
    groups = {
        "summary": _record(),
        "search": _record(
            job={
                "job_id": "search-1",
                "group": "search",
                "operation": "run",
                "status": "completed",
                "created_at": "2026-08-07T10:00:00Z",
                "updated_at": "2026-08-07T10:00:01Z",
            },
            input={"query": "white van", "sensors": [], "window": None, "params": {}},
            output={
                "answer": "Found 2 windows.",
                "Embedding": [],
                "handles": {"media_urls": [], "related_job_ids": []},
                "ext": {"result_count": 2, "results": [{"id": "r1"}, {"id": "r2"}]},
            },
        ),
        "alert": _record(
            job={
                "job_id": "alert-1",
                "group": "alert",
                "operation": "run",
                "status": "completed",
                "created_at": "2026-08-07T10:00:00Z",
                "updated_at": "2026-08-07T10:00:01Z",
            },
            input={
                "query": "intrusion",
                "sensors": [],
                "window": None,
                "params": {"severity": "high", "category": "security"},
            },
            output={
                "answer": "Intrusion detected.",
                "Embedding": [],
                "handles": {"media_urls": [], "related_job_ids": []},
                "ext": {"incident_ids": ["inc-1"]},
            },
        ),
        "media": _record(
            job={
                "job_id": "media-1",
                "group": "media",
                "operation": "run",
                "status": "completed",
                "created_at": "2026-08-07T10:00:00Z",
                "updated_at": "2026-08-07T10:00:01Z",
            },
            input={"query": None, "sensors": [{"id": "cam-1", "type": "video", "info": {}}], "window": None, "params": {}},
            output={
                "answer": "Clip ready.",
                "Embedding": [],
                "handles": {"media_urls": [], "related_job_ids": []},
                "ext": {"description": "west entrance clip", "media_ids": ["clip-9"]},
            },
        ),
        "vlm": _record(
            job={
                "job_id": "vlm-1",
                "group": "vlm",
                "operation": "run",
                "status": "completed",
                "created_at": "2026-08-07T10:00:00Z",
                "updated_at": "2026-08-07T10:00:01Z",
            },
            input={"query": "What color is the jacket?", "sensors": [], "window": None, "params": {}},
            output={
                "answer": "Red.",
                "Embedding": [],
                "handles": {"media_urls": [], "related_job_ids": ["summary-1"]},
                "ext": {"media_ids": ["frame-3"]},
            },
        ),
    }
    for group, record in groups.items():
        text = render_memory_note(record, persisted=True)
        assert record.job.job_id in text
        assert group == "summary" or record.output.answer in text
        assert "vss memory get" in text
        assert "Embedding" not in text


def test_path_uses_injected_clock_and_rejects_traversal(tmp_path: Path) -> None:
    clock = lambda: datetime(2026, 8, 7, 15, 30, tzinfo=UTC)  # noqa: E731
    path = resolve_note_path(tmp_path, clock=clock)
    assert path == (tmp_path / "memory" / "2026-08-07-vss.md").resolve()
    with pytest.raises(ValueError, match=r"\.\."):
        resolve_note_path(tmp_path, template="../escape.md")
    with pytest.raises(ValueError, match="relative"):
        resolve_note_path(tmp_path, template="/tmp/escape.md")


def test_openclaw_sink_append_noop_replace_and_preserve(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "MEMORY.md").write_text("# long term\n", encoding="utf-8")
    sink = OpenClawMarkdownSink(
        workspace=workspace,
        clock=lambda: datetime(2026, 8, 7, tzinfo=UTC),
    )
    note = workspace / "memory" / "2026-08-07-vss.md"
    note.parent.mkdir(parents=True)
    note.write_text("<!-- user note -->\nkeep me\n", encoding="utf-8")

    first = sink.write(_record())
    assert first.status == MemoryNoteStatus.WRITTEN
    text = note.read_text(encoding="utf-8")
    assert "keep me" in text
    assert text.count("<!-- vss-job:summary-01KABC -->") == 1

    second = sink.write(_record())
    assert second.status == MemoryNoteStatus.UNCHANGED
    assert note.read_text(encoding="utf-8").count("<!-- vss-job:summary-01KABC -->") == 1

    updated = _record(
        output={
            "answer": "Updated answer.",
            "Embedding": [],
            "handles": {"media_urls": [], "related_job_ids": []},
            "ext": {"event_ids": ["evt-9"]},
        }
    )
    third = sink.write(updated)
    assert third.status == MemoryNoteStatus.REPLACED
    text = note.read_text(encoding="utf-8")
    assert "Updated answer." in text
    assert "Three delivery vehicles" not in text
    assert "keep me" in text
    assert (workspace / "MEMORY.md").read_text(encoding="utf-8") == "# long term\n"

    other = _record(
        job={
            "job_id": "summary-OTHER",
            "group": "summary",
            "operation": "run",
            "status": "completed",
            "created_at": "2026-08-07T10:00:00Z",
            "updated_at": "2026-08-07T10:00:01Z",
        }
    )
    fourth = sink.write(other)
    assert fourth.status == MemoryNoteStatus.WRITTEN
    text = note.read_text(encoding="utf-8")
    assert "summary-01KABC" in text and "summary-OTHER" in text


def test_symlink_escape_rejected(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    outside = tmp_path / "outside"
    workspace.mkdir()
    outside.mkdir()
    target = outside / "leak.md"
    target.write_text("secret\n", encoding="utf-8")
    memory_dir = workspace / "memory"
    memory_dir.mkdir()
    link = memory_dir / "2026-08-07-vss.md"
    link.symlink_to(target)

    sink = OpenClawMarkdownSink(
        workspace=workspace,
        clock=lambda: datetime(2026, 8, 7, tzinfo=UTC),
    )
    result = sink.write(_record())
    assert result.status == MemoryNoteStatus.FAILED
    assert target.read_text(encoding="utf-8") == "secret\n"


def test_concurrent_writers_preserve_both_blocks(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    sink = OpenClawMarkdownSink(
        workspace=workspace,
        clock=lambda: datetime(2026, 8, 7, tzinfo=UTC),
    )
    errors: list[BaseException] = []

    def write_one(job_id: str) -> None:
        try:
            sink.write(
                _record(
                    job={
                        "job_id": job_id,
                        "group": "summary",
                        "operation": "run",
                        "status": "completed",
                        "created_at": "2026-08-07T10:00:00Z",
                        "updated_at": "2026-08-07T10:00:01Z",
                    }
                )
            )
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=write_one, args=(f"summary-{i}",)) for i in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    text = (workspace / "memory" / "2026-08-07-vss.md").read_text(encoding="utf-8")
    for i in range(8):
        assert f"summary-{i}" in text


def test_unsupported_harness_plugin_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="unsupported"):
        OpenClawMarkdownSink(workspace=tmp_path, harness="openclaw", plugin="memory-wiki")
