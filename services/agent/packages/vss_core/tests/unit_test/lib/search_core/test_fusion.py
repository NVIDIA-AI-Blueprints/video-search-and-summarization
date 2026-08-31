# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Unit tests for the pure fusion helpers (no async, no backends)."""

from __future__ import annotations

import pytest

from vss_core.search_core.errors import InvalidInputError
from vss_core.search_core.models.attribute_search import AttributeSearchMetadata
from vss_core.search_core.models.attribute_search import AttributeSearchResult
from vss_core.search_core.models.embed_search import EmbedSearchOutput
from vss_core.search_core.models.embed_search import EmbedSearchResultItem
from vss_core.search_core.models.search import SearchResult
from vss_core.search_core.primitives import _fusion

# ------------------------------------------------------------------- builders


def _embed_result(
    *,
    video_name: str = "v1",
    sensor_id: str = "cam1",
    similarity: float = 0.5,
    start: str = "2025-01-01T00:00:00Z",
    end: str = "2025-01-01T00:00:05Z",
    screenshot_url: str = "embed_shot",
    object_ids: list[str] | None = None,
) -> SearchResult:
    return SearchResult(
        video_name=video_name,
        description="desc",
        start_time=start,
        end_time=end,
        sensor_id=sensor_id,
        screenshot_url=screenshot_url,
        similarity=similarity,
        object_ids=object_ids or [],
    )


def _attr_result(
    *,
    object_id: str = "7",
    frame_score: float | None = None,
    behavior_score: float = 0.0,
    screenshot_url: str | None = None,
    sensor_id: str = "cam1",
) -> AttributeSearchResult:
    meta = AttributeSearchMetadata(
        sensor_id=sensor_id,
        object_id=object_id,
        object_type="person",
        frame_score=frame_score,
        behavior_score=behavior_score,
    )
    return AttributeSearchResult(screenshot_url=screenshot_url, metadata=meta)


# ------------------------------------------------------- build_fusion_candidates


def test_build_candidates_normalised_score_divides_by_attribute_count():
    pairs = [(_embed_result(), [_attr_result(behavior_score=0.4), _attr_result(object_id="8", behavior_score=0.6)])]
    candidates = _fusion.build_fusion_candidates(pairs, attribute_count=2)
    assert len(candidates) == 1
    assert candidates[0].normalised_attribute_score == pytest.approx(0.5)


def test_build_candidates_attribute_count_zero_is_guarded():
    pairs = [(_embed_result(), [_attr_result(behavior_score=0.9)])]
    candidates = _fusion.build_fusion_candidates(pairs, attribute_count=0)
    assert candidates[0].normalised_attribute_score == 0.0


def test_build_candidates_dedups_object_ids_and_preserves_zero():
    attrs = [
        _attr_result(object_id="0", behavior_score=0.1),
        _attr_result(object_id="0", behavior_score=0.2),
        _attr_result(object_id="9", behavior_score=0.3),
    ]
    candidates = _fusion.build_fusion_candidates([(_embed_result(), attrs)], attribute_count=3)
    assert candidates[0].object_ids == ["0", "9"]


def test_build_candidates_frame_score_preferred_when_positive():
    # frame_score positive wins; a None/zero frame_score falls back to behavior.
    attrs = [_attr_result(frame_score=0.8, behavior_score=0.2), _attr_result(frame_score=None, behavior_score=0.5)]
    candidates = _fusion.build_fusion_candidates([(_embed_result(), attrs)], attribute_count=2)
    assert candidates[0].normalised_attribute_score == pytest.approx((0.8 + 0.5) / 2)


def test_build_candidates_screenshot_fallback_to_embed():
    pairs = [(_embed_result(screenshot_url="embed_shot"), [_attr_result(behavior_score=0.5)])]
    candidates = _fusion.build_fusion_candidates(pairs, attribute_count=1)
    assert candidates[0].screenshot_url == "embed_shot"


def test_build_candidates_screenshot_prefers_attribute():
    pairs = [
        (_embed_result(screenshot_url="embed_shot"), [_attr_result(behavior_score=0.5, screenshot_url="attr_shot")])
    ]
    candidates = _fusion.build_fusion_candidates(pairs, attribute_count=1)
    assert candidates[0].screenshot_url == "attr_shot"


def test_build_candidates_empty_attribute_payload():
    candidates = _fusion.build_fusion_candidates([(_embed_result(similarity=0.7), None)], attribute_count=2)
    assert candidates[0].normalised_attribute_score == 0.0
    assert candidates[0].object_ids == []
    assert candidates[0].embed_score == pytest.approx(0.7)


def test_build_candidates_skips_unprocessable_item():
    # A malformed attribute item is skipped; the good one still contributes.
    candidates = _fusion.build_fusion_candidates(
        [(_embed_result(), [{"not": "a valid attribute result"}, _attr_result(behavior_score=0.4)])],
        attribute_count=1,
    )
    assert candidates[0].normalised_attribute_score == pytest.approx(0.4)


def test_weighted_rrf_union_keeps_tag_only_candidates() -> None:
    shared_embed = _embed_result(video_name="shared", sensor_id="cam1")
    shared_tag = _embed_result(
        video_name="shared",
        sensor_id="cam1",
        start="2025-01-01T00:00:02Z",
        end="2025-01-01T00:00:06Z",
    )
    tag_only = _embed_result(video_name="tag-only", sensor_id="cam2")
    fused = _fusion.weighted_rrf_union(
        {"embed": [shared_embed], "tag": [shared_tag, tag_only]},
        weights={"embed": 0.35, "tag": 0.45},
        rrf_k=60,
    )
    assert [result.video_name for result in fused] == ["shared", "tag-only"]
    assert fused[0].similarity == pytest.approx((0.35 + 0.45) / 61)


def test_weighted_rrf_union_deduplicates_object_ids() -> None:
    left = _embed_result(object_ids=["1"])
    right = _embed_result(object_ids=["1", "2", "2"])
    fused = _fusion.weighted_rrf_union(
        {"embed": [left], "attribute": [right]},
        weights={"embed": 1.0, "attribute": 1.0},
        rrf_k=60,
    )
    assert fused[0].object_ids == ["1", "2"]


def test_weighted_rrf_union_does_not_collapse_same_provider_chunks() -> None:
    first = _embed_result(
        video_name="first",
        sensor_id="cam1",
        start="2025-01-01T00:00:00Z",
        end="2025-01-01T00:00:05Z",
    )
    second = _embed_result(
        video_name="second",
        sensor_id="cam1",
        start="2025-01-01T00:00:05Z",
        end="2025-01-01T00:00:10Z",
    )

    fused = _fusion.weighted_rrf_union(
        {"tag": [first, second]},
        weights={"tag": 1.0},
        rrf_k=60,
    )

    assert [(result.start_time, result.end_time) for result in fused] == [
        ("2025-01-01T00:00:00Z", "2025-01-01T00:00:05Z"),
        ("2025-01-01T00:00:05Z", "2025-01-01T00:00:10Z"),
    ]
    assert fused[0].similarity == pytest.approx(1.0 / 61)
    assert fused[1].similarity == pytest.approx(1.0 / 62)


def test_fuse_ranked_union_dispatches_weighted_and_equal_rrf() -> None:
    shared_embed = _embed_result(video_name="shared", sensor_id="cam1")
    shared_tag = _embed_result(video_name="shared", sensor_id="cam1")
    providers = {"embed": [shared_embed], "tag": [shared_tag]}

    weighted = _fusion.fuse_ranked_union(
        providers,
        method="weighted_rrf",
        weights={"embed": 0.25, "tag": 0.75},
        rrf_k=60,
    )
    equal = _fusion.fuse_ranked_union(
        providers,
        method="rrf",
        weights={"embed": 0.25, "tag": 0.75},
        rrf_k=60,
    )

    assert weighted[0].similarity == pytest.approx(1.0 / 61)
    assert equal[0].similarity == pytest.approx(2.0 / 61)


@pytest.mark.parametrize("method", ["bogus", "weighted_linear"])
def test_fuse_ranked_union_rejects_unknown_method(method: str) -> None:
    with pytest.raises(InvalidInputError, match="Unknown union fusion_method"):
        _fusion.fuse_ranked_union({}, method=method, weights={}, rrf_k=60)


# ------------------------------------------------------------------- fusion math


def test_weighted_linear_ranking_order():
    a = _fusion.FusionCandidate(
        _embed_result(video_name="a"), embed_score=0.9, normalised_attribute_score=0.0, screenshot_url="", object_ids=[]
    )
    b = _fusion.FusionCandidate(
        _embed_result(video_name="b"), embed_score=0.1, normalised_attribute_score=1.0, screenshot_url="", object_ids=[]
    )
    ranked = _fusion.weighted_linear_fusion([a, b], w_embed=0.35, w_attribute=0.55)
    # b: 0.35*0.1 + 0.55*1.0 = 0.585 beats a: 0.35*0.9 = 0.315
    assert [r.video_name for r in ranked] == ["b", "a"]


def test_rrf_ranking_boosted_by_attribute_score():
    a = _fusion.FusionCandidate(
        _embed_result(video_name="a"), embed_score=0.9, normalised_attribute_score=0.0, screenshot_url="", object_ids=[]
    )
    b = _fusion.FusionCandidate(
        _embed_result(video_name="b"), embed_score=0.8, normalised_attribute_score=5.0, screenshot_url="", object_ids=[]
    )
    ranked = _fusion.rrf_fusion([a, b], rrf_k=60, rrf_w=0.5)
    # b's large attribute boost overtakes a's slightly better embed rank.
    assert [r.video_name for r in ranked] == ["b", "a"]


def test_rrf_with_attribute_rank_returns_all_and_no_index_collision():
    # Two candidates with identical scores must not collide (index-keyed ranks).
    cands = [
        _fusion.FusionCandidate(_embed_result(video_name="a"), 0.5, 0.5, "", []),
        _fusion.FusionCandidate(_embed_result(video_name="b"), 0.5, 0.5, "", []),
    ]
    ranked = _fusion.rrf_fusion_with_attribute_rank(cands, rrf_k=60, rrf_w=0.5)
    assert sorted(r.video_name for r in ranked) == ["a", "b"]


def test_apply_fusion_dispatch():
    cands = [_fusion.FusionCandidate(_embed_result(), 0.5, 0.5, "", [])]
    for method in ("weighted_linear", "rrf", "rrf_with_attribute_rank"):
        out = _fusion.apply_fusion(cands, method, rrf_k=60, rrf_w=0.5, w_embed=0.35, w_attribute=0.55)
        assert len(out) == 1


def test_apply_fusion_unknown_method_raises_invalid_input():
    cands = [_fusion.FusionCandidate(_embed_result(), 0.5, 0.5, "", [])]
    with pytest.raises(InvalidInputError, match="Unknown fusion_method"):
        _fusion.apply_fusion(cands, "bogus", rrf_k=60, rrf_w=0.5, w_embed=0.35, w_attribute=0.55)


def test_weighted_linear_stable_order_on_equal_scores():
    # Equal fused scores must preserve input order deterministically (stable sort).
    cands = [
        _fusion.FusionCandidate(
            _embed_result(video_name=name), embed_score=0.5, normalised_attribute_score=0.5, screenshot_url=""
        )
        for name in ("a", "b", "c", "d")
    ]
    ranked = _fusion.weighted_linear_fusion(cands, w_embed=0.35, w_attribute=0.55)
    assert [r.video_name for r in ranked] == ["a", "b", "c", "d"]


def test_rrf_stable_order_on_equal_embed_scores():
    # Identical embed scores -> identical rrf scores -> stable input order.
    cands = [
        _fusion.FusionCandidate(
            _embed_result(video_name=name), embed_score=0.5, normalised_attribute_score=0.0, screenshot_url=""
        )
        for name in ("a", "b", "c")
    ]
    ranked = _fusion.rrf_fusion(cands, rrf_k=60, rrf_w=0.5)
    assert [r.video_name for r in ranked] == ["a", "b", "c"]


# ------------------------------------------------------- merge_consecutive_results


def test_merge_consecutive_overlapping_same_sensor():
    r1 = _embed_result(
        sensor_id="cam1", start="2025-01-01T00:00:00Z", end="2025-01-01T00:00:05Z", similarity=0.9, object_ids=["a"]
    )
    r2 = _embed_result(
        sensor_id="cam1",
        start="2025-01-01T00:00:04Z",
        end="2025-01-01T00:00:10Z",
        similarity=0.88,
        object_ids=["b", "a"],
    )
    merged = _fusion.merge_consecutive_results([r1, r2])
    assert len(merged) == 1
    assert merged[0].object_ids == ["a", "b"]
    assert merged[0].end_time == "2025-01-01T00:00:10Z"


def test_merge_splits_on_dissimilar_similarity():
    r1 = _embed_result(sensor_id="cam1", start="2025-01-01T00:00:00Z", end="2025-01-01T00:00:05Z", similarity=0.9)
    r2 = _embed_result(sensor_id="cam1", start="2025-01-01T00:00:04Z", end="2025-01-01T00:00:10Z", similarity=0.1)
    merged = _fusion.merge_consecutive_results([r1, r2])
    assert len(merged) == 2


def test_merge_routes_malformed_timestamps_to_no_timestamp_bucket():
    good = _embed_result(sensor_id="cam1", similarity=0.5)
    bad = _embed_result(sensor_id="cam2", start="not-a-date", end="not-a-date", similarity=0.9)
    merged = _fusion.merge_consecutive_results([good, bad])
    # both survive; the malformed one is kept un-merged and sorts by similarity.
    assert len(merged) == 2
    assert merged[0].similarity == 0.9


def test_merge_no_timestamp_sorted_by_similarity():
    a = _embed_result(sensor_id="cam1", start="", end="", similarity=0.3)
    b = _embed_result(sensor_id="cam2", start="", end="", similarity=0.7)
    merged = _fusion.merge_consecutive_results([a, b])
    assert [r.similarity for r in merged] == [0.7, 0.3]


def test_merge_empty_returns_empty():
    assert _fusion.merge_consecutive_results([]) == []


def test_merge_touching_boundary_merges():
    # Adjacency (next.start == group.end) counts as overlapping and merges.
    r1 = _embed_result(sensor_id="cam1", start="2025-01-01T00:00:00Z", end="2025-01-01T00:00:05Z", similarity=0.9)
    r2 = _embed_result(sensor_id="cam1", start="2025-01-01T00:00:05Z", end="2025-01-01T00:00:10Z", similarity=0.9)
    merged = _fusion.merge_consecutive_results([r1, r2])
    assert len(merged) == 1
    assert merged[0].end_time == "2025-01-01T00:00:10Z"


def test_merge_gap_does_not_merge():
    # A clear time gap between same-sensor chunks keeps them separate.
    r1 = _embed_result(sensor_id="cam1", start="2025-01-01T00:00:00Z", end="2025-01-01T00:00:05Z", similarity=0.9)
    r2 = _embed_result(sensor_id="cam1", start="2025-01-01T00:00:30Z", end="2025-01-01T00:00:35Z", similarity=0.9)
    merged = _fusion.merge_consecutive_results([r1, r2])
    assert len(merged) == 2


def test_merge_negative_similarities_not_wrongly_merged():
    # A strong (-0.1) and a weak (-0.9) hit overlap in time but are far apart in
    # magnitude; the ratio guard must NOT collapse them just because both are
    # negative (a raw min/max ratio would be > 1 and wrongly merge).
    strong = _embed_result(sensor_id="cam1", start="2025-01-01T00:00:00Z", end="2025-01-01T00:00:05Z", similarity=-0.1)
    weak = _embed_result(sensor_id="cam1", start="2025-01-01T00:00:04Z", end="2025-01-01T00:00:10Z", similarity=-0.9)
    merged = _fusion.merge_consecutive_results([strong, weak])
    assert len(merged) == 2


def test_merge_close_negative_similarities_still_merge():
    # Two nearby negative scores of similar magnitude remain compatible and merge.
    a = _embed_result(sensor_id="cam1", start="2025-01-01T00:00:00Z", end="2025-01-01T00:00:05Z", similarity=-0.50)
    b = _embed_result(sensor_id="cam1", start="2025-01-01T00:00:04Z", end="2025-01-01T00:00:10Z", similarity=-0.48)
    merged = _fusion.merge_consecutive_results([a, b])
    assert len(merged) == 1


# ----------------------------------------------------------- top_percent_filter


def test_top_percent_filter_keeps_within_threshold():
    results = [_embed_result(similarity=1.0), _embed_result(similarity=0.6), _embed_result(similarity=0.4)]
    filtered = _fusion.apply_top_percent_filter(results, 0.5)
    assert [r.similarity for r in filtered] == [1.0, 0.6]


@pytest.mark.parametrize("top_pct", [None, 0.0, 1.0, 1.5, -0.1])
def test_top_percent_filter_noop(top_pct):
    results = [_embed_result(similarity=1.0), _embed_result(similarity=0.1)]
    assert len(_fusion.apply_top_percent_filter(results, top_pct)) == 2


def test_top_percent_filter_empty():
    assert _fusion.apply_top_percent_filter([], 0.5) == []


def test_top_percent_filter_negative_scores_keeps_all():
    # With a non-positive max, percent-of-max would put the threshold above the
    # top result and drop everything; the filter must no-op instead.
    results = [_embed_result(similarity=-0.2), _embed_result(similarity=-0.5)]
    filtered = _fusion.apply_top_percent_filter(results, 0.5)
    assert [r.similarity for r in filtered] == [-0.2, -0.5]


# --------------------------------------------------- embed_output_to_search_results


def test_embed_output_mapping_coerces_and_skips_empty_video_name():
    output = EmbedSearchOutput(
        results=[
            EmbedSearchResultItem(video_name="v1", similarity_score=0.8, sensor_id="cam1"),
            EmbedSearchResultItem(video_name="", similarity_score=0.9),  # skipped
        ]
    )
    results = _fusion.embed_output_to_search_results(output)
    assert len(results) == 1
    assert results[0].video_name == "v1"
    assert results[0].similarity == pytest.approx(0.8)
    assert results[0].object_ids == []
