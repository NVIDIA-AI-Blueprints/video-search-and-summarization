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
from vss_unified_memory.application.models import MemoryQuery, WriteStatus
from vss_unified_memory.domain.models import Event, MediaRef, RecordType, Summary, TimeRange

FIXTURES = Path(__file__).parents[1] / "fixtures"


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
    parent = json.loads((FIXTURES / "summary_document.json").read_text())
    events = json.loads((FIXTURES / "event_documents.json").read_text())
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
