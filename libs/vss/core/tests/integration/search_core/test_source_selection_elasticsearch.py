# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Complete attribute selection and critic boundaries on an isolated real index."""

from __future__ import annotations

import json

import pytest
from test_source_snapshot_elasticsearch import snapshot_backend as snapshot_backend

from vss_core.search_core.clients.elastic import ElasticClient
from vss_core.search_core.critic import CriticAgentOutput
from vss_core.search_core.critic import CriticAgentResult
from vss_core.search_core.critic import VideoResult
from vss_core.search_core.host import VSSSearch
from vss_core.search_core.models.search import SearchInput
from vss_core.search_core.models.search import SearchOutput
from vss_core.search_core.primitives.attribute_search import AttributeSearch
from vss_core.search_core.primitives.search import Search
from vss_core.search_core.runtime import SearchRuntime

pytestmark = pytest.mark.integration


class TextEmbedding:
    async def get_text_embedding(self, query):
        return [1.0, 0.0]


class RecordingCritic:
    def __init__(self):
        self.calls = []

    async def run(self, inp):
        self.calls.append(inp)
        return CriticAgentOutput(
            video_results=[
                VideoResult(
                    video_info=video, result=CriticAgentResult.REJECTED if i == 0 else CriticAgentResult.CONFIRMED
                )
                for i, video in enumerate(inp.videos)
            ]
        )


def primitive(es, index, cap):
    return Search(
        embed=object(),
        attribute=AttributeSearch(
            es=es, embed=TextEmbedding(), behavior_index=index, behavior_index_wildcard="unused-*", frames_index=None
        ),
        behavior_es=es,
        behavior_index=index,
        max_hits_per_source=cap,
        merge_adjacent=False,
    )


@pytest.mark.asyncio
async def test_real_complete_pool_over_1615_and_selected_only_verification(snapshot_backend):
    origin, setup, index, _mapping = snapshot_backend
    lines = []
    for i in range(1617):
        sensor = "a-crowded" if i < 1500 else f"z-clip-{(i - 1500) // 10:02d}"
        lines.extend(
            [
                json.dumps({"index": {"_index": index, "_id": f"{i:04d}"}}),
                json.dumps(
                    {
                        "sensor": {"id": sensor},
                        "object": {"id": f"{i:04d}", "type": "Person"},
                        "timestamp": "2025-01-01T00:00:00Z",
                        "end": "2025-01-01T00:00:05Z",
                        "embeddings": [{"vector": [1.0, 0.0]}],
                    }
                ),
            ]
        )
    response = setup.post(
        "/_bulk?refresh=true", content="\n".join(lines) + "\n", headers={"Content-Type": "application/x-ndjson"}
    )
    response.raise_for_status()
    assert not response.json()["errors"]
    es = ElasticClient.from_endpoint(origin)
    parameters = {
        "search_mode": "attribute",
        "attributes": ["hat", "boots"],
        "candidate_limit": 2000,
        "attribute_min_similarity": 0.0,
        "original_query": "person wearing a hat and boots",
    }
    try:
        baseline = await primitive(es, index, None).run(SearchInput(**parameters, top_k=2000))
        critic = RecordingCritic()
        facade = VSSSearch.from_runtime(SearchRuntime(max_hits_per_source=1), critic=critic)
        facade._search = primitive(es, index, 1)
        output = await facade.search(**parameters, top_k=10)
        selection = output.source_selection
        assert len(selection.original_pool) == 1617
        assert selection.selected_indices == [0, *range(1500, 1590, 10)]
        assert len(output.data) == 10
        assert len({hit.source_id for hit in output.data}) == 10
        assert [hit.model_dump(exclude={"source_id"}) for hit in selection.original_pool] == [
            hit.model_dump() for hit in baseline.data
        ]
        assert len(selection.completeness["scans"]) == 2
        assert len({scan["snapshot_digest"] for scan in selection.completeness["scans"]}) == 1
        assert len(critic.calls) == 1
        assert len(critic.calls[0].videos) == 10
        assert critic.calls[0].query == parameters["original_query"]
        assert output.data[0].critic_result.result == "rejected"
        assert all(hit.critic_result is None for hit in selection.original_pool)
        # Rejecting the selected first subject cannot replace it with one of
        # the other 1,499 subjects from that source.
        assert selection.selected_indices[0] == 0
        assert output.data[0].object_ids == ["0000"]
        assert (
            SearchOutput.model_validate_json(output.model_dump_json()).source_selection.selected_indices
            == selection.selected_indices
        )
    finally:
        await es.aclose()
