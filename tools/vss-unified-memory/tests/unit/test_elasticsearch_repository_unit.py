# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from elasticsearch import Elasticsearch

from vss_unified_memory.adapters.persistence.elasticsearch import repository as repository_module
from vss_unified_memory.adapters.persistence.elasticsearch.repository import ElasticsearchMemoryRepository
from vss_unified_memory.application.errors import RepositoryError
from vss_unified_memory.application.models import MemoryQuery, WriteStatus
from vss_unified_memory.domain.models import Event, MediaRef, RecordType, Summary, TimeRange

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


def test_bulk_partial_failure_is_degraded(monkeypatch: pytest.MonkeyPatch) -> None:
    client = MagicMock(spec=Elasticsearch)
    monkeypatch.setattr(repository_module, "bulk", lambda *args, **kwargs: (1, [{"index": {"status": 400}}]))
    summary = Summary(
        "summary:1",
        "Summary",
        MediaRef("vst", "video-1"),
        datetime.now(timezone.utc),
        (Event("event:1", 1, TimeRange(1, 2), "Event", "activity"),),
    )
    repository = ElasticsearchMemoryRepository("http://localhost:9200", "memory", client=client)
    result = repository.save(summary)
    assert result.status == WriteStatus.DEGRADED
    assert result.attempted_records == 2
    assert result.successful_records == 1
    client.index.assert_not_called()


def test_summary_search_reconstructs_events_with_one_batched_lookup() -> None:
    parent = _summary_read_source()
    events = _event_read_sources()
    client = MagicMock(spec=Elasticsearch)
    client.search.side_effect = [
        {"hits": {"hits": [{"_source": parent, "_score": 1.0}]}},
        {"hits": {"hits": [{"_source": event} for event in events], "total": {"value": len(events)}}},
    ]
    repository = ElasticsearchMemoryRepository("http://localhost:9200", "memory", client=client)
    results = repository.search(MemoryQuery(query_text="forklift", record_type=RecordType.VIDEO_SUMMARY))
    assert len(results) == 1
    assert isinstance(results[0].memory, Summary)
    assert results[0].memory.event_count == 2
    assert client.search.call_count == 2
    assert client.search.call_args_list[0].kwargs["source_excludes"] == ["summary_chunks", "event_chunks"]


def test_get_rejects_malformed_elasticsearch_source() -> None:
    malformed = _summary_read_source()
    malformed.pop("description")
    client = MagicMock(spec=Elasticsearch)
    client.get.return_value = {"_source": malformed}
    repository = ElasticsearchMemoryRepository("http://localhost:9200", "memory", client=client)

    with pytest.raises(RepositoryError, match="invalid memory document") as error:
        repository.get("summary:1")

    assert error.value.retryable is False


def test_related_event_lookup_rejects_non_event_document() -> None:
    parent = _summary_read_source()
    client = MagicMock(spec=Elasticsearch)
    client.search.side_effect = [
        {"hits": {"hits": [{"_source": parent, "_score": 1.0}]}},
        {"hits": {"hits": [{"_source": parent}], "total": {"value": 1}}},
    ]
    repository = ElasticsearchMemoryRepository("http://localhost:9200", "memory", client=client)

    with pytest.raises(RepositoryError, match="non-event document"):
        repository.search(MemoryQuery(query_text="forklift", record_type=RecordType.VIDEO_SUMMARY))


def test_semantic_event_hit_hydrates_typed_parent_and_events() -> None:
    parent = _summary_read_source()
    events = _event_read_sources()
    client = MagicMock(spec=Elasticsearch)
    client.search.side_effect = [
        {"hits": {"hits": []}},
        {"hits": {"hits": [{"_source": events[0], "_score": 0.91}]}},
        {"hits": {"hits": [{"_source": event} for event in events], "total": {"value": len(events)}}},
    ]
    client.mget.return_value = {"docs": [{"found": True, "_source": parent}]}
    repository = ElasticsearchMemoryRepository("http://localhost:9200", "memory", client=client)

    results = repository.search(MemoryQuery(query_text="near miss", semantic=True, query_vector=(0.1, 0.2)))

    assert len(results) == 1
    assert isinstance(results[0].memory, Summary)
    assert results[0].memory.event_count == 2
    assert results[0].score == 0.91
    client.mget.assert_called_once()
