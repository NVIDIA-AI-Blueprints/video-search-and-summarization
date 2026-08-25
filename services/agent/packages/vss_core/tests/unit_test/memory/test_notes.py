# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Compact Markdown memory-note renderer tests."""

from __future__ import annotations

from vss_core.memory.models import SCHEMA_ID
from vss_core.memory.models import UnifiedMemoryRecord
from vss_core.memory.notes import MAX_ANSWER_CHARS
from vss_core.memory.notes import MAX_MEMORY_NOTE_CHARS
from vss_core.memory.notes import render_memory_note


def _record(
    *,
    query: str | None = "Summarize the west entrance.",
    answer: str | None = "Three delivery vehicles arrived.",
    ext: dict[str, object] | None = None,
) -> UnifiedMemoryRecord:
    return UnifiedMemoryRecord.model_validate(
        {
            "schema": SCHEMA_ID,
            "job": {
                "job_id": "summarize-01KABC",
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
