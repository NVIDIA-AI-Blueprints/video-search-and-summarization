# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import json
from pathlib import Path

from vss_unified_memory.adapters.cli.input_models import PersistSummaryInput
from vss_unified_memory.adapters.cli.mapper import map_input_to_summary
from vss_unified_memory.adapters.persistence.elasticsearch.mapper import source_to_entity, summary_to_documents
from vss_unified_memory.application.models import (
    EmbeddedRecordPassages,
    EmbeddedTextPassage,
    MemoryEmbeddings,
    TextPassage,
)
from vss_unified_memory.domain.models import Summary

FIXTURES = Path(__file__).parents[1] / "fixtures"


def test_summary_maps_to_parent_and_event_documents() -> None:
    summary = map_input_to_summary(
        PersistSummaryInput.model_validate_json((FIXTURES / "vss_summary_input.json").read_text())
    )
    summary_document, event_documents = summary_to_documents(summary, None)
    assert summary_document.to_source() == json.loads((FIXTURES / "summary_document.json").read_text())
    assert [document.to_source() for document in event_documents] == json.loads(
        (FIXTURES / "event_documents.json").read_text()
    )


def test_parent_and_event_documents_reconstruct_summary() -> None:
    parent = json.loads((FIXTURES / "summary_document.json").read_text())
    events = json.loads((FIXTURES / "event_documents.json").read_text())
    summary = source_to_entity(parent, events)
    assert isinstance(summary, Summary)
    assert summary.event_count == 2
    assert summary.events[1].ordinal == 2


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
    source = summary_document.to_source()

    assert "embedding" not in source
    assert source["description"] == summary.description
    assert source["summary_chunks"][0]["text"] == summary.description
    assert source["summary_chunks"][0]["embedding"] == [0.1, 0.2]
    assert all(document.embedding_model == "cosmos-embed1-448p" for document in event_documents)
    assert all(document.event_chunks[0].embedding == (0.3, 0.4) for document in event_documents)
