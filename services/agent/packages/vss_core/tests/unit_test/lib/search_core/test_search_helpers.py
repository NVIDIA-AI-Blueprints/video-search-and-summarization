# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Unit tests for the pure/async helpers in lib.search_core.primitives._search_helpers."""

from __future__ import annotations

from typing import Any

import pytest

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


def test_resolve_sources_rtsp_keeps_names_and_maps_uuid_back():
    name_to_uuid = {"warehouse": "uuid-1"}
    # A known name stays a name; a known uuid is converted back to its name.
    resolved = sh._resolve_video_sources_for_search(["warehouse", "uuid-1", "other"], name_to_uuid, "rtsp")
    assert resolved == ["warehouse", "warehouse", "other"]


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


def _embed_result(*, video_name: str, sensor_id: str, similarity: float = 0.5) -> SearchResult:
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
