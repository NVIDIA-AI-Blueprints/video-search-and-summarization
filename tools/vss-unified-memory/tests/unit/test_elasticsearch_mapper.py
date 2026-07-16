# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from vss_unified_memory.adapters.cli.input_models import PersistSummaryInput
from vss_unified_memory.adapters.cli.mapper import map_input_to_summary
from vss_unified_memory.adapters.persistence.elasticsearch.mapper import read_document_to_domain, summary_to_documents
from vss_unified_memory.adapters.persistence.elasticsearch.models import (
    PassageDocument,
    SummaryReadDocument,
    elasticsearch_read_document_parser,
)
from vss_unified_memory.application.errors import RepositoryError
from vss_unified_memory.application.models import (
    EmbeddedRecordPassages,
    EmbeddedTextPassage,
    MemoryEmbeddings,
    TextPassage,
)
from vss_unified_memory.domain.models import Summary

FIXTURES = Path(__file__).parents[1] / "fixtures"


def _summary_read_source() -> dict[str, object]:
    source: dict[str, object] = json.loads((FIXTURES / "summary_document.json").read_text())
    source.pop("summary_chunks")
    return source


def _event_read_sources() -> list[dict[str, object]]:
    sources: list[dict[str, object]] = json.loads((FIXTURES / "event_documents.json").read_text())
    for source in sources:
        source.pop("event_chunks")
    return sources


def test_summary_maps_to_parent_and_event_documents() -> None:
    summary = map_input_to_summary(
        PersistSummaryInput.model_validate_json((FIXTURES / "vss_summary_input.json").read_text())
    )
    summary_document, event_documents = summary_to_documents(summary, None)
    assert summary_document.model_dump(mode="json") == json.loads((FIXTURES / "summary_document.json").read_text())
    assert [document.model_dump(mode="json") for document in event_documents] == json.loads(
        (FIXTURES / "event_documents.json").read_text()
    )


def test_read_documents_reconstruct_summary() -> None:
    parent = elasticsearch_read_document_parser.validate_python(_summary_read_source())
    events = tuple(elasticsearch_read_document_parser.validate_python(source) for source in _event_read_sources())
    assert isinstance(parent, SummaryReadDocument)
    assert all(not isinstance(event, SummaryReadDocument) for event in events)
    summary = read_document_to_domain(parent, events)  # type: ignore[arg-type]
    assert isinstance(summary, Summary)
    assert summary.event_count == 2
    assert summary.events[1].ordinal == 2


def test_read_document_rejects_unknown_fields_and_record_types() -> None:
    unknown_field = _summary_read_source()
    unknown_field["unexpected"] = True
    with pytest.raises(ValidationError):
        elasticsearch_read_document_parser.validate_python(unknown_field)

    unsupported = _summary_read_source()
    unsupported["record_type"] = "alert_record"
    with pytest.raises(ValidationError):
        elasticsearch_read_document_parser.validate_python(unsupported)


def test_write_passage_requires_embedding() -> None:
    with pytest.raises(ValidationError):
        PassageDocument.model_validate(
            {
                "chunk_id": "summary:1:chunk:0000",
                "ordinal": 0,
                "start_char": 0,
                "end_char": 7,
                "token_count": 3,
                "text_hash": "239f59ed55e737c77147cf55ad0c1b030b6d7ee748a7426952f9b852d5a935e5",
                "text": "payload",
            }
        )


def test_related_events_must_match_parent_summary() -> None:
    parent = elasticsearch_read_document_parser.validate_python(_summary_read_source())
    event_sources = _event_read_sources()
    event_sources[0]["summary_id"] = "summary:different"
    events = tuple(elasticsearch_read_document_parser.validate_python(source) for source in event_sources)
    assert isinstance(parent, SummaryReadDocument)
    with pytest.raises(RepositoryError, match="different summary"):
        read_document_to_domain(parent, events)  # type: ignore[arg-type]


def test_summary_embedding_is_replaced_by_nested_chunks() -> None:
    summary = map_input_to_summary(
        PersistSummaryInput.model_validate_json((FIXTURES / "vss_summary_input.json").read_text())
    )
    chunk = TextPassage.create(
        record_id=summary.id,
        ordinal=0,
        start_char=0,
        end_char=len(summary.description),
        token_count=13,
        text=summary.description,
    )
    embeddings = MemoryEmbeddings(
        model="cosmos-embed1-448p",
        chunking_version="chunking-v1",
        summary=EmbeddedRecordPassages(summary.id, (EmbeddedTextPassage(chunk, (0.1, 0.2)),)),
        events=tuple(
            EmbeddedRecordPassages(
                event.id,
                (
                    EmbeddedTextPassage(
                        TextPassage.create(
                            record_id=event.id,
                            ordinal=0,
                            start_char=0,
                            end_char=len(event.description),
                            token_count=10,
                            text=event.description,
                        ),
                        (0.3, 0.4),
                    ),
                ),
            )
            for event in summary.events
        ),
    )

    summary_document, event_documents = summary_to_documents(summary, embeddings)
    source = summary_document.model_dump(mode="json")

    assert "embedding" not in source
    assert source["description"] == summary.description
    assert source["summary_chunks"][0]["text"] == summary.description
    assert source["summary_chunks"][0]["embedding"] == [0.1, 0.2]
    assert all(document.embedding_model == "cosmos-embed1-448p" for document in event_documents)
    assert all(document.event_chunks[0].embedding == (0.3, 0.4) for document in event_documents)
