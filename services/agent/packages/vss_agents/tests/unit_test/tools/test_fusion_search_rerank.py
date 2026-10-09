# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Unit tests for the agent fusion_search_rerank embed-once seam (NVBug 6781021)."""

import asyncio
from typing import Any
from unittest.mock import AsyncMock
from unittest.mock import MagicMock

from nat.builder.function import LambdaFunction
import pytest

from vss_agents.tools.attribute_search import AttributeSearchConfig
from vss_agents.tools.attribute_search import AttributeSearchInput
from vss_agents.tools.attribute_search import AttributeSearchResult
from vss_agents.tools.attribute_search import build_attribute_search
from vss_agents.tools.attribute_search import search_attributes
from vss_agents.tools.search import SearchConfig
from vss_agents.tools.search import SearchInput
from vss_agents.tools.search import SearchOutput
from vss_agents.tools.search import SearchResult
from vss_agents.tools.search import execute_core_search
from vss_agents.tools.search import fusion_search_rerank


def _embed_result(*, video_name: str, sensor_id: str, similarity: float = 0.9) -> SearchResult:
    return SearchResult(
        video_name=video_name,
        description="d",
        start_time="2025-01-01T00:00:00Z",
        end_time="2025-01-01T00:00:05Z",
        sensor_id=sensor_id,
        screenshot_url="",
        similarity=similarity,
        object_ids=[],
    )


class _RecordingAttr:
    """Attribute adapter that records every ainvoke payload and returns empty."""

    def __init__(self) -> None:
        self.calls: list[Any] = []

    async def ainvoke(self, payload: Any) -> Any:
        self.calls.append(payload)
        return []


class _CountingEmbed:
    """Embed client that records each text and returns a deterministic vector."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def get_text_embedding(self, text: str) -> list[float]:
        self.calls.append(text)
        return [float(sum(ord(c) for c in text)), 0.0, 0.0]


@pytest.mark.asyncio
async def test_attribute_embed_client_embeds_each_attribute_once():
    # NVBug 6781021: with attribute_embed_client supplied, each attribute is
    # embedded ONCE (not once per candidate video) and the precomputed vectors
    # are threaded into every per-hit attribute lookup.
    embed_results = [
        _embed_result(video_name="vA", sensor_id="camA"),
        _embed_result(video_name="vB", sensor_id="camB"),
        _embed_result(video_name="vC", sensor_id="camC"),
    ]
    embed = _CountingEmbed()
    attr = _RecordingAttr()
    await fusion_search_rerank(
        embed_results=embed_results,
        attributes=["red hat", "blue car"],
        attribute_search_fn=attr,
        vst_internal_url="",
        attribute_embed_client=embed,
    )
    # (a) embed-once: exactly one get_text_embedding per attribute, NOT per candidate.
    assert len(embed.calls) == 2
    assert set(embed.calls) == {"red hat", "blue car"}
    # (b) the precomputed vectors are propagated into every per-hit ainvoke payload,
    # in attribute order, identical across the three candidate videos.
    expected_vectors = [
        [float(sum(ord(c) for c in "red hat")), 0.0, 0.0],
        [float(sum(ord(c) for c in "blue car")), 0.0, 0.0],
    ]
    assert len(attr.calls) == 3
    for payload in attr.calls:
        assert payload["query_embedding"] == expected_vectors


@pytest.mark.asyncio
async def test_no_attribute_embed_client_skips_precompute():
    # Legacy/back-compat path: no attribute_embed_client -> no precompute, and the
    # precomputed-vectors key is not added to the payload.
    embed_results = [_embed_result(video_name="vA", sensor_id="camA")]
    attr = _RecordingAttr()
    await fusion_search_rerank(
        embed_results=embed_results,
        attributes=["red hat"],
        attribute_search_fn=attr,
        vst_internal_url="",
    )
    assert len(attr.calls) == 1
    assert "query_embedding" not in attr.calls[0]


@pytest.mark.asyncio
async def test_blank_attributes_dropped_and_stripped_before_embed():
    # Greptile/zac-wang-nv: blank entries are dropped and survivors are stripped,
    # so a padded attribute is embedded as its stripped form.
    embed_results = [_embed_result(video_name="vA", sensor_id="camA")]
    embed = _CountingEmbed()
    attr = _RecordingAttr()
    await fusion_search_rerank(
        embed_results=embed_results,
        attributes=[" red hat ", "   ", "blue car"],
        attribute_search_fn=attr,
        vst_internal_url="",
        attribute_embed_client=embed,
    )
    assert len(embed.calls) == 2
    assert set(embed.calls) == {"red hat", "blue car"}
    assert attr.calls[0]["query"] == ["red hat", "blue car"]


@pytest.mark.asyncio
async def test_empty_embed_results_skips_precompute():
    # Greptile P1: with no candidate videos to rerank, precomputation is skipped.
    embed = _CountingEmbed()
    attr = _RecordingAttr()
    out = await fusion_search_rerank(
        embed_results=[],
        attributes=["red hat", "blue car"],
        attribute_search_fn=attr,
        vst_internal_url="",
        attribute_embed_client=embed,
    )
    assert embed.calls == []
    assert attr.calls == []
    assert out == []


@pytest.mark.asyncio
@pytest.mark.parametrize("fuse", [True, False])
@pytest.mark.parametrize("vectors", [[[1.0]], [[1.0], [2.0], [3.0]], [1.0]])
async def test_mismatched_vectors_rejected_before_search(fuse: bool, vectors: list[Any]) -> None:
    embed = AsyncMock()
    es = AsyncMock()
    with pytest.raises(ValueError, match=r"query_embedding.*query"):
        await search_attributes(
            AttributeSearchInput(query=["red hat", "blue car"], query_embedding=vectors, fuse_multi_attribute=fuse),
            embed,
            "behavior",
            "",
            es,
        )
    embed.get_text_embedding.assert_not_awaited()
    es.search.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("fuse", [True, False])
@pytest.mark.parametrize("vectors", [None, [], [[11.0], [22.0]]])
async def test_attribute_vectors_reach_es(fuse: bool, vectors: list[list[float]] | None) -> None:
    embed = AsyncMock()
    embed.get_text_embedding.side_effect = [[11.0], [22.0]]
    es = AsyncMock()
    es.search.return_value = {"hits": {"total": {"value": 0}, "hits": []}}
    await search_attributes(
        AttributeSearchInput(query=["red hat", "blue car"], query_embedding=vectors, fuse_multi_attribute=fuse),
        embed,
        "behavior",
        "",
        es,
    )
    assert [call.kwargs["body"]["knn"]["query_vector"] for call in es.search.await_args_list] == [[11.0], [22.0]]
    assert embed.get_text_embedding.await_count == (0 if vectors else 2)


@pytest.mark.asyncio
@pytest.mark.parametrize("embedding_failure", [None, "transient", "persistent"])
async def test_execute_core_search_uses_built_nat_attribute_embedder(
    monkeypatch: pytest.MonkeyPatch,
    embedding_failure: str | None,
) -> None:
    """Exercise production decomposition, NAT wrapping, fusion and ES vector dispatch."""
    embed = AsyncMock()
    attempts = 0

    async def get_embedding(text: str) -> list[float]:
        nonlocal attempts
        attempts += 1
        if embedding_failure == "persistent" or (embedding_failure == "transient" and attempts == 1):
            raise RuntimeError("embedding service unavailable")
        return {"red hat": [11.0], "blue car": [22.0]}[text]

    embed.get_text_embedding.side_effect = get_embedding
    es = AsyncMock()
    es.search.return_value = {"hits": {"total": {"value": 0}, "hits": []}}
    monkeypatch.setattr("vss_agents.tools.attribute_search.RTVICVEmbedClient", lambda _: embed)
    monkeypatch.setattr("vss_agents.tools.attribute_search.VSSESClient.get_es_client", AsyncMock(return_value=es))
    monkeypatch.setattr("vss_agents.tools.attribute_search.VSSESClient.close_all", AsyncMock())
    attr_config = AttributeSearchConfig(
        rtvi_cv_endpoint="http://embed.test", es_endpoint="http://es.test", vst_external_url=""
    )
    builder = MagicMock()
    async with build_attribute_search(attr_config, builder) as info:
        attribute_fn = LambdaFunction.from_info(config=attr_config, info=info, instance_name="attribute_search")
        builder.get_function = AsyncMock(return_value=attribute_fn)
        llm = MagicMock()
        llm.model_name = "test-model"
        llm.ainvoke = AsyncMock(
            return_value=MagicMock(
                content='{"query":"person walking", "attributes":["red hat","blue car"], "has_action":true}'
            )
        )
        embed_search = AsyncMock()
        embed_search.ainvoke.return_value = {
            "results": [
                {**_embed_result(video_name=f"v{i}", sensor_id=f"cam{i}").model_dump(), "similarity_score": 0.9}
                for i in range(3)
            ]
        }
        config = SearchConfig(
            embed_search_tool="embed_search",
            attribute_search_tool="attribute_search",
            agent_mode_llm="llm",
            vst_internal_url="",
            use_attribute_search=True,
        )
        updates = [
            update
            async for update in execute_core_search(
                search_input=SearchInput(
                    query="person walking with red hat and blue car",
                    source_type="video_file",
                    agent_mode=True,
                    use_critic=False,
                ),
                embed_search=embed_search,
                agent_llm=llm,
                config=config,
                builder=builder,
            )
        ]
        assert isinstance(updates[-1], SearchOutput)
        assert len(updates[-1].data) == 3
        builder.get_function.assert_awaited_once_with(config.attribute_search_tool)
        expected_calls = ["red hat", "blue car"] * (4 if embedding_failure else 1)
        assert [call.args[0] for call in embed.get_text_embedding.await_args_list] == expected_calls
        # A failed precompute retains normal per-candidate handling. A transient
        # error recovers and reaches ES; a persistent error is handled for each
        # candidate without aborting the whole fusion pass.
        assert any(getattr(update, "content", "") == "Fusion reranking complete" for update in updates)
        assert len(es.search.await_args_list) == (0 if embedding_failure == "persistent" else 6)
        assert [call.kwargs["body"]["knn"]["query_vector"] for call in es.search.await_args_list] == [
            [11.0],
            [22.0],
        ] * (0 if embedding_failure == "persistent" else 3)


@pytest.mark.asyncio
async def test_single_query_accepts_flat_vector() -> None:
    embed = AsyncMock()
    es = AsyncMock()
    es.search.return_value = {"hits": {"total": {"value": 0}, "hits": []}}
    await search_attributes(
        AttributeSearchInput(query="red hat", query_embedding=[11.0]),
        embed,
        "behavior",
        "",
        es,
    )
    embed.get_text_embedding.assert_not_awaited()
    assert es.search.await_args.kwargs["body"]["knn"]["query_vector"] == [11.0]


@pytest.mark.asyncio
async def test_precompute_cancellation_propagates() -> None:
    embed = AsyncMock()
    embed.get_text_embedding.side_effect = asyncio.CancelledError()
    attr = _RecordingAttr()
    with pytest.raises(asyncio.CancelledError):
        await fusion_search_rerank(
            embed_results=[_embed_result(video_name="vA", sensor_id="camA")],
            attributes=["red hat"],
            attribute_search_fn=attr,
            vst_internal_url="",
            attribute_embed_client=embed,
        )
    assert attr.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel_request", [False, True])
async def test_precompute_drains_delayed_sibling_before_fallback_or_cancellation(cancel_request: bool) -> None:
    started = asyncio.Event()
    release = asyncio.Event()
    finished = asyncio.Event()
    embedding_tasks: list[asyncio.Task[Any]] = []

    async def get_embedding(text: str) -> list[float]:
        task = asyncio.current_task()
        assert task is not None
        embedding_tasks.append(task)
        if text == "red hat":
            await started.wait()
            if not cancel_request:
                raise RuntimeError("first embedding failed")
            await release.wait()
            return [11.0]
        started.set()
        try:
            await release.wait()
            return [22.0]
        finally:
            # Cleanup itself yields; cancelling without awaiting is insufficient.
            await asyncio.sleep(0)
            finished.set()

    embed = AsyncMock()
    embed.get_text_embedding.side_effect = get_embedding
    fallback_states: list[bool] = []
    attr = AsyncMock()

    async def search(payload: dict[str, Any]) -> list[Any]:
        fallback_states.append(finished.is_set())
        assert "query_embedding" not in payload
        return []

    attr.ainvoke.side_effect = search
    rerank = asyncio.create_task(
        fusion_search_rerank(
            embed_results=[_embed_result(video_name="vA", sensor_id="camA")],
            attributes=["red hat", "blue car"],
            attribute_search_fn=attr,
            vst_internal_url="",
            attribute_embed_client=embed,
        )
    )
    try:
        await started.wait()
        if cancel_request:
            rerank.cancel()
            with pytest.raises(asyncio.CancelledError):
                await rerank
            assert fallback_states == []
        else:
            results = await rerank
            assert len(results) == 1
            assert fallback_states == [True]
        assert finished.is_set()
        assert all(task.done() for task in embedding_tasks)
    finally:
        release.set()
        await asyncio.gather(*embedding_tasks, return_exceptions=True)


@pytest.mark.asyncio
async def test_fusion_preserves_partial_match_scores_and_overlay_metadata() -> None:
    attr = AsyncMock()
    matches = [
        {
            "screenshot_url": "http://vst.test/overlay",
            "metadata": {
                "sensor_id": "camA",
                "object_id": object_id,
                "object_type": "person",
                "frame_timestamp": "2025-01-01T00:00:01Z",
                "behavior_score": behavior,
                "frame_score": frame,
            },
        }
        for object_id, behavior, frame in [("1", 0.2, 0.8), ("1", 0.4, 0.0), ("2", 0.6, None)]
    ]
    # Real adapters can return models or dictionaries; both retain the same metadata.
    attr.ainvoke.return_value = [matches[0], AttributeSearchResult.model_validate(matches[1]), matches[2]]
    result = await fusion_search_rerank(
        embed_results=[_embed_result(video_name="vA", sensor_id="camA")],
        attributes=["red hat", "blue coat", "white shoes", "black bag"],
        attribute_search_fn=attr,
        fusion_method="weighted_linear",
        w_embed=0.0,
        w_attribute=1.0,
    )
    assert result[0].similarity == pytest.approx(0.45)
    assert result[0].object_ids == ["1", "2"]
    assert result[0].screenshot_url == "http://vst.test/overlay"
