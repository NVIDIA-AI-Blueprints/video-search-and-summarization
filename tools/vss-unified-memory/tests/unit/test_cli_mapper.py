# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from pathlib import Path

from vss_unified_memory.adapters.cli.input_models import PersistSummaryInput
from vss_unified_memory.adapters.cli.mapper import (
    map_input_to_summary,
    map_persist_result_to_output,
    map_recall_result_to_output,
)
from vss_unified_memory.application.models import (
    LatencyMs,
    MemorySearchResult,
    PersistObservability,
    PersistSummaryResult,
    RecallMemoryResult,
    RecallObservability,
    WriteStatus,
)

FIXTURE = Path(__file__).parents[1] / "fixtures" / "vss_summary_input.json"


def test_mapper_assigns_deterministic_ids_and_ordinals() -> None:
    input_model = PersistSummaryInput.model_validate_json(FIXTURE.read_text())
    first = map_input_to_summary(input_model)
    second = map_input_to_summary(input_model)
    assert first == second
    assert first.id == "summary:11111111-1111-4111-8111-111111111111"
    assert [event.ordinal for event in first.events] == [1, 2]
    assert first.events[0].id == "event:11111111-1111-4111-8111-111111111111:0001"


def test_mapper_maps_persist_observability() -> None:
    result = PersistSummaryResult(
        status=WriteStatus.COMPLETE,
        summary_id="summary:1",
        event_ids=("event:1",),
        attempted_records=2,
        successful_records=2,
        observability=PersistObservability(
            summary_id="summary:1",
            event_count=1,
            summary_chars=10,
            event_chars_total=5,
            summary_chunk_count=1,
            event_chunk_count=1,
            total_chunk_count=2,
            estimated_input_tokens=6,
            embedding_model="cosmos-embed1-448p",
            embedding_vector_count=2,
            es_attempted_records=2,
            es_successful_records=2,
            latency_ms=LatencyMs(chunking=1.0, embedding=2.0, es_bulk_index=3.0, total=6.0),
        ),
    )
    output = map_persist_result_to_output(result)
    assert output.observability is not None
    assert output.observability.total_chunk_count == 2
    assert output.observability.latency_ms.total == 6.0


def test_mapper_maps_recall_observability() -> None:
    from datetime import datetime, timezone

    from vss_unified_memory.domain.models import MediaRef, Summary

    summary = Summary("summary:1", "text", MediaRef("vst", "video-1"), datetime.now(timezone.utc), ())
    result = RecallMemoryResult(
        (MemorySearchResult(summary, 0.8),),
        observability=RecallObservability(
            operation="search",
            semantic=True,
            query_text_chars=12,
            query_text_hash="abc",
            returned_summary_count=1,
            returned_event_count=0,
            returned_chars=4,
            estimated_returned_tokens=1,
            latency_ms=LatencyMs(query_embedding=1.0, es_summary_knn=2.0, total=3.0),
        ),
    )
    output = map_recall_result_to_output(result)
    assert output.observability is not None
    assert output.observability.semantic is True
    assert output.observability.latency_ms.es_summary_knn == 2.0
