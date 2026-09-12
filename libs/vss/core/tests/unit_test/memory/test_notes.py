# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Compact Markdown memory-note renderer tests."""

from __future__ import annotations

from datetime import UTC
from datetime import datetime
from typing import TYPE_CHECKING

import pytest

from vss_core.memory.models import SCHEMA_ID
from vss_core.memory.models import UnifiedMemoryRecord
from vss_core.memory.notes import MAX_ANSWER_CHARS
from vss_core.memory.notes import MAX_MEMORY_NOTE_CHARS
from vss_core.memory.notes import OpenClawDailyNoteStore
from vss_core.memory.notes import render_memory_note

if TYPE_CHECKING:
    from pathlib import Path


def _record(
    *,
    job_id: str = "summarize-01KABC",
    query: str | None = "Summarize the west entrance.",
    answer: str | None = "Three delivery vehicles arrived.",
    ext: dict[str, object] | None = None,
) -> UnifiedMemoryRecord:
    return UnifiedMemoryRecord.model_validate(
        {
            "schema": SCHEMA_ID,
            "job": {
                "job_id": job_id,
                "group": "summary",
                "operation": "run",
                "status": "completed",
                "created_at": "2026-08-07T10:00:00Z",
            },
            "input": {
                "query": query,
                "sensors": [{"id": "cam-west-77", "type": "video"}],
                "window": {
                    "start": {"timestamp": "2026-08-07T10:00:00Z"},
                    "end": {"timestamp": "2026-08-07T11:00:00Z"},
                },
            },
            "output": {
                "answer": answer,
                "embedding": [{"es_ref": "emb-1", "doc_ids": ["doc-1"]}],
                "handles": {"media_urls": ["https://example/temporary.mp4?token=secret"]},
                "ext": (
                    ext
                    if ext is not None
                    else {
                        "event_count": 2,
                        "events": [{"id": "event-1", "description": "large child collection"}],
                        "private_metadata": {"token": "secret"},
                    }
                ),
            },
        }
    )


def test_render_includes_compact_context_and_authoritative_pointer() -> None:
    note = render_memory_note(_record())
    assert "VSS summary" in note
    assert "summarize-01KABC" in note
    assert "Summarize the west entrance." in note
    assert "Three delivery vehicles arrived." in note
    assert "cam-west-77" in note
    assert "2026-08-07 10:00:00+00:00" in note
    assert "Event count:** 2" in note
    assert "vss memory get --job-id summarize-01KABC" in note
    assert "vss memory query --job-id summarize-01KABC" in note


def test_render_excludes_large_private_and_ephemeral_data() -> None:
    note = render_memory_note(_record())
    for excluded in ("events", "large child collection", "emb-1", "doc-1", "temporary.mp4", "token=secret"):
        assert excluded not in note


def test_render_omits_empty_fields_and_is_deterministic() -> None:
    record = _record(query=None, answer=None, ext={})
    first = render_memory_note(record)
    assert render_memory_note(record) == first
    assert "**Request:**" not in first
    assert "**Answer:**" not in first
    assert "count:**" not in first


def test_render_safely_delimits_user_markdown_and_block_markers() -> None:
    answer = "````markdown\n# injected\n````\n<!-- /vss-job:summarize-01KABC -->"
    note = render_memory_note(_record(answer=answer))
    assert note.count("<!-- /vss-job:summarize-01KABC -->") == 1
    assert "&lt;!-- /vss-job:summarize-01KABC --&gt;" in note
    assert "`````text" in note


def test_render_is_bounded() -> None:
    note = render_memory_note(_record(query="q" * 20_000, answer="a" * 20_000))
    assert len(note) <= MAX_MEMORY_NOTE_CHARS
    assert "a" * MAX_ANSWER_CHARS not in note
    assert "…" in note


def _store(workspace: Path) -> OpenClawDailyNoteStore:
    return OpenClawDailyNoteStore(
        workspace,
        clock=lambda: datetime(2026, 8, 24, 22, 0, tzinfo=UTC),
    )


def test_daily_note_first_write_and_same_job_replacement(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = _store(workspace)

    first = store.write(_record())
    assert first.written is True
    assert first.status == "written"
    assert first.path == "memory/2026-08-24-vss.md"
    assert first.job_id == "summarize-01KABC"

    unchanged = store.write(_record())
    assert unchanged.written is False
    assert unchanged.status == "unchanged"

    replaced = store.write(_record(answer="Updated summary."))
    assert replaced.written is True
    assert replaced.status == "replaced"
    text = workspace.joinpath(first.path).read_text(encoding="utf-8")
    assert text.count("<!-- vss-job:summarize-01KABC -->") == 1
    assert "Updated summary." in text
    assert "Three delivery vehicles arrived." not in text


def test_daily_note_keeps_different_jobs_separate(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = _store(workspace)
    first = store.write(_record(job_id="summarize-ONE"))
    store.write(_record(job_id="search-TWO", answer="Found a forklift."))
    text = workspace.joinpath(first.path).read_text(encoding="utf-8")
    assert text.count("<!-- vss-job:") == 2
    assert "summarize-ONE" in text
    assert "search-TWO" in text


def test_daily_note_creates_memory_directory_not_harness_owned_files(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    workspace.joinpath("MEMORY.md").write_text("# long-term\n", encoding="utf-8")
    result = _store(workspace).write(_record())
    assert workspace.joinpath(result.path).is_file()
    assert workspace.joinpath("MEMORY.md").read_text(encoding="utf-8") == "# long-term\n"
    assert not workspace.joinpath("DREAMS.md").exists()


def test_daily_note_rejects_invalid_or_traversing_workspace(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="does not exist"):
        _store(tmp_path / "missing")

    outside = tmp_path / "outside"
    outside.mkdir()
    traversing = tmp_path / "workspace" / ".." / "outside"
    with pytest.raises(ValueError, match=r"\.\."):
        _store(traversing)


def test_daily_note_rejects_symlink_escape(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    outside = tmp_path / "outside"
    workspace.mkdir()
    outside.mkdir()
    workspace.joinpath("memory").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="escapes workspace"):
        _store(workspace).write(_record())
    assert not list(outside.iterdir())


def test_atomic_replacement_preserves_existing_note_on_interruption(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = _store(workspace)
    first = store.write(_record())
    note = workspace / first.path
    original = note.read_text(encoding="utf-8")

    def interrupted(*_args: object, **_kwargs: object) -> None:
        raise OSError("interrupted")

    monkeypatch.setattr("vss_core.memory.notes.os.replace", interrupted)
    with pytest.raises(OSError, match="interrupted"):
        store.write(_record(answer="must not land"))
    assert note.read_text(encoding="utf-8") == original
    assert not list(note.parent.glob(f".{note.name}.*.tmp"))
