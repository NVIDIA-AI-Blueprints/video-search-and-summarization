# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from elasticsearch import Elasticsearch

from vss_unified_memory.adapters.persistence.elasticsearch.repository import ElasticsearchMemoryRepository
from vss_unified_memory.application.models import (
    EmbeddedRecordPassages,
    EmbeddedTextPassage,
    MemoryEmbeddings,
    MemoryQuery,
    TextPassage,
    WriteStatus,
)
from vss_unified_memory.domain.models import Event, MediaRef, RecordType, Summary, TimeRange

pytestmark = pytest.mark.integration


@pytest.fixture
def repository() -> ElasticsearchMemoryRepository:
    endpoint = os.getenv("VSS_MEMORY_TEST_ELASTICSEARCH_ENDPOINT")
    if not endpoint:
        pytest.skip("VSS_MEMORY_TEST_ELASTICSEARCH_ENDPOINT is not set")
    index = f"vss-unified-memory-test-{uuid4().hex}"
    client = Elasticsearch(endpoint)
    template_path = (
        Path(__file__).parents[2] / "src/vss_unified_memory/adapters/persistence/elasticsearch/index-template-v1.json"
    )
    template = json.loads(template_path.read_text())
    client.indices.create(
        index=index, settings=template["template"]["settings"], mappings=template["template"]["mappings"]
    )
    value = ElasticsearchMemoryRepository(endpoint, index, client=client)
    try:
        yield value
    finally:
        client.indices.delete(index=index)


def test_save_and_reconstruct_summary(repository: ElasticsearchMemoryRepository) -> None:
    summary = Summary(
        id=f"summary:{uuid4()}",
        description="A forklift crossed an aisle.",
        media_ref=MediaRef("vst", str(uuid4()), "camera-1", "clip.mp4"),
        created_at=datetime.now(timezone.utc),
        events=(Event(f"event:{uuid4()}:0001", 1, TimeRange(1, 3), "Forklift crossed.", "activity"),),
    )
    embeddings = MemoryEmbeddings(
        model="cosmos-embed1-448p",
        chunking_version="test-wordpiece-v1-128t-16o",
        summary=EmbeddedRecordPassages(
            summary.id,
            (
                EmbeddedTextPassage(
                    TextPassage.create(
                        record_id=summary.id,
                        ordinal=0,
                        start_char=0,
                        end_char=len(summary.description),
                        token_count=9,
                        text=summary.description,
                    ),
                    (1.0, *((0.0,) * 767)),
                ),
            ),
        ),
        events=(
            EmbeddedRecordPassages(
                summary.events[0].id,
                (
                    EmbeddedTextPassage(
                        TextPassage.create(
                            record_id=summary.events[0].id,
                            ordinal=0,
                            start_char=0,
                            end_char=len(summary.events[0].description),
                            token_count=5,
                            text=summary.events[0].description,
                        ),
                        (0.2,) * 768,
                    ),
                ),
            ),
        ),
    )
    result = repository.save(summary, embeddings)
    recalled = repository.get(summary.id, RecordType.VIDEO_SUMMARY, include_related=True)
    assert result.status == WriteStatus.COMPLETE
    assert recalled == summary

    search_results = repository.search(
        MemoryQuery(query_text="forklift aisle", semantic=True, query_vector=(1.0, *((0.0,) * 767)))
    )
    assert len(search_results) == 1
    assert search_results[0].memory == summary
