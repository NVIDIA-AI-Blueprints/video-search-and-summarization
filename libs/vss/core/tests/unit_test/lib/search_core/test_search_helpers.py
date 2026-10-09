# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Unit tests for the pure/async helpers in lib.search_core.primitives._search_helpers."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from vss_core.search_core.errors import BackendUnreachableError
from vss_core.search_core.errors import IndexNotFoundError
from vss_core.search_core.models.attribute_search import AttributeSearchMetadata
from vss_core.search_core.models.attribute_search import AttributeSearchResult
from vss_core.search_core.models.search import SearchInput
from vss_core.search_core.models.search import SearchResult
from vss_core.search_core.primitives import _search_helpers as sh

# --------------------------------------------------- _resolve_video_sources_for_search


def test_resolve_sources_empty_returns_input():
    assert sh._resolve_video_sources_for_search([], {"name": "uuid"}, "video_file") == []
    assert sh._resolve_video_sources_for_search(["a"], {}, "video_file") == ["a"]


def test_resolve_sources_video_file_maps_names_to_uuids():
    name_to_uuid = {"warehouse": "uuid-1"}
    resolved = sh._resolve_video_sources_for_search(["warehouse", "unknown"], name_to_uuid, "video_file")
    assert resolved == ["uuid-1", "unknown"]


def test_resolve_sources_rtsp_maps_names_to_uuids_like_video_file():
    name_to_uuid = {"warehouse": "uuid-1"}
    # RTSP documents are keyed by the VST sensor UUID under ``sensor.id``, so a
    # known name resolves to its uuid, an already-resolved uuid is left alone,
    # and an unknown source falls through to wildcard matching.
    resolved = sh._resolve_video_sources_for_search(["warehouse", "uuid-1", "other"], name_to_uuid, "rtsp")
    assert resolved == ["uuid-1", "uuid-1", "other"]


# ----------------------------------------------- attribute_result_to_search_result


def _attr_result(
    *,
    object_id: str | None = "7",
    frame_score: float | None = None,
    behavior_score: float = 0.0,
    start_time: str | None = None,
    end_time: str | None = None,
    frame_timestamp: str | None = None,
    screenshot_url: str | None = None,
    sensor_id: str = "cam1",
) -> AttributeSearchResult:
    meta = AttributeSearchMetadata(
        sensor_id=sensor_id,
        object_id=object_id,
        object_type="person",
        frame_score=frame_score,
        behavior_score=behavior_score,
        start_time=start_time,
        end_time=end_time,
        frame_timestamp=frame_timestamp,
    )
    return AttributeSearchResult(screenshot_url=screenshot_url, metadata=meta)


def test_attribute_result_preserves_object_id_zero():
    result = sh.attribute_result_to_search_result(_attr_result(object_id="0", behavior_score=0.5))
    assert result.object_ids == ["0"]


def test_attribute_result_omits_blank_object_id():
    result = sh.attribute_result_to_search_result(_attr_result(object_id="", behavior_score=0.5))
    assert result.object_ids == []


def test_attribute_result_omits_missing_object_id():
    result = sh.attribute_result_to_search_result(_attr_result(object_id=None, behavior_score=0.5))
    assert result.object_ids == []


def test_attribute_result_empty_timestamp_becomes_blank_bucket():
    result = sh.attribute_result_to_search_result(_attr_result(behavior_score=0.5))
    assert result.start_time == ""
    assert result.end_time == ""


def test_attribute_result_frame_score_preferred_when_positive():
    result = sh.attribute_result_to_search_result(_attr_result(frame_score=0.8, behavior_score=0.2))
    assert result.similarity == pytest.approx(0.8)


def test_attribute_result_falls_back_to_behavior_score():
    result = sh.attribute_result_to_search_result(_attr_result(frame_score=None, behavior_score=0.3))
    assert result.similarity == pytest.approx(0.3)


def test_attribute_result_null_screenshot_becomes_empty_string():
    result = sh.attribute_result_to_search_result(_attr_result(screenshot_url=None, behavior_score=0.5))
    assert result.screenshot_url == ""


def test_attribute_result_uses_frame_timestamp_when_no_range():
    result = sh.attribute_result_to_search_result(
        _attr_result(frame_timestamp="2025-01-01T00:00:03Z", behavior_score=0.5)
    )
    assert result.start_time == "2025-01-01T00:00:03Z"
    assert result.end_time == "2025-01-01T00:00:03Z"


# --------------------------------------------------------------- fusion_search_rerank


def _embed_result(*, video_name: str, sensor_id: str, similarity: float = 0.5, sensor_id_raw: str = "") -> SearchResult:
    return SearchResult(
        video_name=video_name,
        description="d",
        start_time="2025-01-01T00:00:00Z",
        end_time="2025-01-01T00:00:05Z",
        sensor_id=sensor_id,
        screenshot_url="",
        similarity=similarity,
        object_ids=[],
        sensor_id_raw=sensor_id_raw,
    )


class _SelectiveAttr:
    """Raises for one video's attribute lookup, returns empty for the rest."""

    def __init__(self, bad_source: str, error: Exception | None = None) -> None:
        self._bad = bad_source
        self._error = error or ValueError("attribute lookup boom")
        self.calls: list[Any] = []

    async def ainvoke(self, payload: Any) -> Any:
        self.calls.append(payload)
        sources = payload.get("video_sources") or []
        if self._bad in sources:
            raise self._error
        return []


class _AlwaysRaisesAttr:
    def __init__(self, error: Exception) -> None:
        self._error = error

    async def ainvoke(self, payload: Any) -> Any:
        raise self._error


class _CountingEmbed:
    """Embed client that records each text and returns a deterministic vector."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def get_text_embedding(self, text: str) -> list[float]:
        self.calls.append(text)
        # Deterministic per-text vector so asyncio.gather completion order is irrelevant.
        return [float(sum(ord(c) for c in text)), 0.0, 0.0]

    async def aclose(self) -> None:
        return None


class _RecordingAttr:
    """Attribute adapter that records every ainvoke payload and returns empty."""

    def __init__(self) -> None:
        self.calls: list[Any] = []

    async def ainvoke(self, payload: Any) -> Any:
        self.calls.append(payload)
        return []


@pytest.mark.asyncio
async def test_fusion_rerank_soft_degrades_single_video():
    embed_results = [
        _embed_result(video_name="vA", sensor_id="camA", similarity=0.9),
        _embed_result(video_name="vB", sensor_id="camB", similarity=0.8),
    ]
    attr = _SelectiveAttr(bad_source="vA")
    out = await sh.fusion_search_rerank(
        embed_results=embed_results,
        attributes=["red hat"],
        attribute_search_fn=attr,
        vst_internal_url="",
    )
    # The failing video degrades to embed-only; both videos still come back.
    assert len(out) == 2
    assert {r.video_name for r in out} == {"vA", "vB"}


@pytest.mark.asyncio
async def test_fusion_rerank_propagates_systemic_search_error():
    embed_results = [_embed_result(video_name="vA", sensor_id="camA")]
    attr = _AlwaysRaisesAttr(IndexNotFoundError("behavior_index"))
    with pytest.raises(IndexNotFoundError):
        await sh.fusion_search_rerank(
            embed_results=embed_results,
            attributes=["red hat"],
            attribute_search_fn=attr,
            vst_internal_url="",
        )


@pytest.mark.asyncio
async def test_attribute_only_soft_degrade_appends_search_message():
    # An unexpected (non-SearchError) failure degrades to [] but must leave a
    # note so an empty result is distinguishable from a genuine no-matches case.
    attr = _AlwaysRaisesAttr(ValueError("boom"))
    messages: list[str] = []
    out = await sh._run_attribute_only_search(
        attribute_list=["red hat"],
        search_input=SearchInput(query="q", source_type="video_file", search_mode="attribute", attributes=["red hat"]),
        attribute_search_fn=attr,
        top_k=5,
        min_similarity=0.0,
        search_messages=messages,
    )
    assert out == []
    assert any("degraded" in m for m in messages)


@pytest.mark.asyncio
async def test_attribute_only_propagates_systemic_search_error():
    # A SearchError on the primary attribute-only path is NOT soft-degraded.
    attr = _AlwaysRaisesAttr(IndexNotFoundError("behavior_index"))
    with pytest.raises(IndexNotFoundError):
        await sh._run_attribute_only_search(
            attribute_list=["red hat"],
            search_input=SearchInput(
                query="q", source_type="video_file", search_mode="attribute", attributes=["red hat"]
            ),
            attribute_search_fn=attr,
            top_k=5,
            min_similarity=0.0,
            search_messages=[],
        )


@pytest.mark.asyncio
async def test_fusion_rerank_skips_unparseable_timestamp_video():
    bad = SearchResult(
        video_name="vBad",
        description="d",
        start_time="not-a-date",
        end_time="not-a-date",
        sensor_id="camBad",
        screenshot_url="",
        similarity=0.7,
        object_ids=[],
    )
    attr = _SelectiveAttr(bad_source="never")
    out = await sh.fusion_search_rerank(
        embed_results=[bad],
        attributes=["red hat"],
        attribute_search_fn=attr,
        vst_internal_url="",
    )
    # No attribute lookup is attempted for the unparseable clip.
    assert attr.calls == []
    assert len(out) == 1


@pytest.mark.asyncio
async def test_fusion_rerank_uses_indexed_sensor_id_when_vst_absent():
    # Regression (PR #2263 review): with VST absent, fusion_search_rerank must
    # scope each embed hit's attribute lookup by the indexed sensor identity
    # (sensor.id), not the display filename (video_name). A behavior document
    # keyed by sensor.id="warehouse_clip" with no path/url is otherwise missed
    # when the embed hit's video_name is the display filename
    # "warehouse_clip.mp4" and its sensor_id is the stream UUID.
    stream_id = "11111111-2222-3333-4444-555555555555"
    embed_results = [
        _embed_result(
            video_name="warehouse_clip.mp4",
            sensor_id=stream_id,
            similarity=0.9,
            sensor_id_raw="warehouse_clip",
        ),
    ]

    class _AttrByIndexedSensorId:
        def __init__(self) -> None:
            self.calls: list[Any] = []

        async def ainvoke(self, payload: Any) -> Any:
            self.calls.append(payload)
            sources = payload.get("video_sources") or []
            # Only the indexed sensor.id matches the behavior document; the
            # display filename and the stream UUID must not.
            if "warehouse_clip" in sources:
                return [_attr_result(object_id="42", sensor_id="warehouse_clip")]
            return []

    attr = _AttrByIndexedSensorId()
    out = await sh.fusion_search_rerank(
        embed_results=embed_results,
        attributes=["white jacket"],
        attribute_search_fn=attr,
        vst_internal_url="",  # no VST: forces the indexed-identity path
    )
    # The per-hit lookup was scoped to the indexed sensor identity, not the
    # display filename ("warehouse_clip.mp4") the old fallback used.
    assert attr.calls
    assert attr.calls[0]["video_sources"] == ["warehouse_clip"]
    # The attribute hit (object 42) survives rrf fusion instead of vanishing.
    assert out
    assert any("42" in r.object_ids for r in out)


@pytest.mark.asyncio
async def test_fusion_rerank_embeds_each_attribute_once_and_threads_vectors():
    # NVBug 6781021: with an embed client, attributes are embedded ONCE up front and
    # the precomputed vectors are threaded into every per-hit attribute lookup,
    # instead of re-embedding the same attributes for each candidate video.
    embed_results = [
        _embed_result(video_name="vA", sensor_id="camA"),
        _embed_result(video_name="vB", sensor_id="camB"),
        _embed_result(video_name="vC", sensor_id="camC"),
    ]
    embed = _CountingEmbed()
    attr = _RecordingAttr()
    await sh.fusion_search_rerank(
        embed_results=embed_results,
        attributes=["red hat", "blue car"],
        attribute_search_fn=attr,
        vst_internal_url="",
        attribute_embed_client=embed,
    )
    # Two attributes embedded exactly once each (2 calls) -- NOT once per video (6).
    assert len(embed.calls) == 2
    assert set(embed.calls) == {"red hat", "blue car"}
    # One attribute lookup per candidate video, each carrying the precomputed vectors
    # in attributes order (gather preserves input order regardless of completion).
    assert len(attr.calls) == 3
    expected_vectors = [
        [float(sum(ord(c) for c in "red hat")), 0.0, 0.0],
        [float(sum(ord(c) for c in "blue car")), 0.0, 0.0],
    ]
    for payload in attr.calls:
        assert payload["query_embedding"] == expected_vectors


@pytest.mark.asyncio
async def test_fusion_rerank_without_embed_client_omits_query_embedding():
    # Legacy/back-compat path: no embed client -> per-hit re-embed via the adapter,
    # and the precomputed-vectors key is not added to the payload.
    embed_results = [_embed_result(video_name="vA", sensor_id="camA")]
    attr = _RecordingAttr()
    await sh.fusion_search_rerank(
        embed_results=embed_results,
        attributes=["red hat"],
        attribute_search_fn=attr,
        vst_internal_url="",
    )
    assert len(attr.calls) == 1
    assert "query_embedding" not in attr.calls[0]


@pytest.mark.asyncio
async def test_fusion_rerank_blanks_dropped_before_embed_and_aligned():
    # Greptile P1: a blank attribute entry must not be embedded and must not desync the
    # precomputed vectors from the (blank-stripping) normalized_queries() downstream.
    embed_results = [_embed_result(video_name="vA", sensor_id="camA")]
    embed = _CountingEmbed()
    attr = _RecordingAttr()
    out = await sh.fusion_search_rerank(
        embed_results=embed_results,
        attributes=[" red hat ", "   ", "blue car"],
        attribute_search_fn=attr,
        vst_internal_url="",
        attribute_embed_client=embed,
    )
    # Only the two non-blank attributes were embedded (stripped); the blank was dropped first.
    assert len(embed.calls) == 2
    assert set(embed.calls) == {"red hat", "blue car"}
    # The per-hit lookup carries exactly two vectors in attribute order (stripped),
    # with no slot for the dropped blank, so downstream normalized_queries() stays aligned.
    assert len(attr.calls) == 1
    assert attr.calls[0]["query"] == ["red hat", "blue car"]
    expected_vectors = [
        [float(sum(ord(c) for c in "red hat")), 0.0, 0.0],
        [float(sum(ord(c) for c in "blue car")), 0.0, 0.0],
    ]
    assert attr.calls[0]["query_embedding"] == expected_vectors
    # The embed hit survives fusion (attribute-only matches empty); no InvalidInputError.
    assert len(out) == 1


@pytest.mark.asyncio
async def test_fusion_rerank_empty_embed_results_skips_precompute():
    # Greptile P1: with no candidate videos to rerank, precomputation is skipped so an
    # embedder failure cannot turn a no-hits search into an error, and no latency is paid.
    embed = _CountingEmbed()
    attr = _RecordingAttr()
    out = await sh.fusion_search_rerank(
        embed_results=[],
        attributes=["red hat", "blue car"],
        attribute_search_fn=attr,
        vst_internal_url="",
        attribute_embed_client=embed,
    )
    assert embed.calls == []  # nothing embedded when there is nothing to rerank
    assert attr.calls == []  # no per-hit attribute lookups either
    assert out == []  # nothing to fuse


@pytest.mark.asyncio
@pytest.mark.parametrize("persistent", [False, True])
async def test_fusion_precompute_failure_preserves_per_candidate_lookup(persistent: bool) -> None:
    class _FailingEmbed(_CountingEmbed):
        async def get_text_embedding(self, text: str) -> list[float]:
            if persistent or not self.calls:
                self.calls.append(text)
                raise RuntimeError("embedding failed")
            return await super().get_text_embedding(text)

    embed = _FailingEmbed()

    class _EmbeddingAttr(_RecordingAttr):
        async def ainvoke(self, payload: Any) -> Any:
            self.calls.append(payload)
            assert "query_embedding" not in payload
            await embed.get_text_embedding(payload["query"][0])
            return [_attr_result(object_id="42", behavior_score=0.8)]

    attr = _EmbeddingAttr()
    out = await sh.fusion_search_rerank(
        [_embed_result(video_name=f"v{i}", sensor_id=f"cam{i}") for i in range(2)],
        ["red hat"],
        attr,
        attribute_embed_client=embed,
    )
    assert len(attr.calls) == 2
    assert len(embed.calls) == 3  # failed precompute, then the original per-candidate lookups
    assert len(out) == 2
    assert all(("42" in result.object_ids) is (not persistent) for result in out)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure",
    [BackendUnreachableError("rtvi_cv", "unavailable"), IndexNotFoundError("behavior"), asyncio.CancelledError()],
)
async def test_fusion_precompute_systemic_errors_and_cancellation_propagate(failure: BaseException) -> None:
    class _FailingEmbed:
        async def get_text_embedding(self, _text: str) -> list[float]:
            raise failure

    attr = _RecordingAttr()
    with pytest.raises(type(failure)) as caught:
        await sh.fusion_search_rerank(
            [_embed_result(video_name="v", sensor_id="cam")],
            ["red hat"],
            attr,
            attribute_embed_client=_FailingEmbed(),
        )
    assert caught.value is failure
    assert attr.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure", [RuntimeError("transient"), BackendUnreachableError("rtvi_cv", "unavailable"), asyncio.CancelledError()]
)
async def test_failed_precompute_drains_delayed_sibling_before_fallback_or_return(failure: BaseException) -> None:
    sibling_started = asyncio.Event()
    sibling_drained = asyncio.Event()
    sibling_tasks: list[asyncio.Task[Any]] = []

    class _DelayedEmbed:
        async def get_text_embedding(self, text: str) -> list[float]:
            if text == "red hat":
                await sibling_started.wait()
                raise failure
            task = asyncio.current_task()
            assert task is not None
            sibling_tasks.append(task)
            sibling_started.set()
            try:
                await asyncio.Event().wait()
            finally:
                # Cancellation starts asynchronous cleanup; merely canceling is insufficient.
                await asyncio.sleep(0)
                sibling_drained.set()
            return [1.0]

    class _AfterCleanupAttr(_RecordingAttr):
        async def ainvoke(self, payload: Any) -> Any:
            assert sibling_drained.is_set()
            assert all(task.done() for task in sibling_tasks)
            assert "query_embedding" not in payload
            return await super().ainvoke(payload)

    attr = _AfterCleanupAttr()
    args = ([_embed_result(video_name="v", sensor_id="cam")], ["red hat", "blue car"], attr)
    if isinstance(failure, RuntimeError):
        out = await sh.fusion_search_rerank(*args, attribute_embed_client=_DelayedEmbed())
        assert len(out) == 1
        assert len(attr.calls) == 1
    else:
        with pytest.raises(type(failure)) as caught:
            await sh.fusion_search_rerank(*args, attribute_embed_client=_DelayedEmbed())
        assert caught.value is failure
        assert attr.calls == []
    assert sibling_drained.is_set()
    assert sibling_tasks[0].cancelled()


@pytest.mark.asyncio
async def test_cancelled_rerank_drains_all_precompute_tasks() -> None:
    started = asyncio.Event()
    tasks: list[asyncio.Task[Any]] = []
    drained: list[str] = []

    class _DelayedEmbed:
        async def get_text_embedding(self, text: str) -> list[float]:
            task = asyncio.current_task()
            assert task is not None
            tasks.append(task)
            if len(tasks) == 2:
                started.set()
            try:
                await asyncio.Event().wait()
            finally:
                await asyncio.sleep(0)
                drained.append(text)
            return [1.0]

    attr = _RecordingAttr()
    rerank = asyncio.create_task(
        sh.fusion_search_rerank(
            [_embed_result(video_name="v", sensor_id="cam")],
            ["red hat", "blue car"],
            attr,
            attribute_embed_client=_DelayedEmbed(),
        )
    )
    await started.wait()
    rerank.cancel()
    with pytest.raises(asyncio.CancelledError):
        await rerank
    assert set(drained) == {"red hat", "blue car"}
    assert all(task.done() for task in tasks)
    assert attr.calls == []
