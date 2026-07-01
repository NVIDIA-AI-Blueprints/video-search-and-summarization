# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Unit tests for the pure attribute-search helpers (no async, no backends)."""

from __future__ import annotations

from datetime import UTC
from datetime import datetime

import pytest

from lib.search_core.models.attribute_search import AttributeSearchMetadata
from lib.search_core.models.attribute_search import AttributeSearchResult
from lib.search_core.primitives import _attribute_helpers as ah

# ---------------------------------------------------------------- index select


def test_resolve_index_video_file():
    assert ah.resolve_index_by_source_type("bi", "video_file", "w-*") == "bi"


def test_resolve_index_rtsp():
    assert ah.resolve_index_by_source_type("bi", "rtsp", "w-*") == ["w-*", "-bi"]


def test_resolve_index_bad_source_type():
    with pytest.raises(ValueError, match="Unsupported source_type"):
        ah.resolve_index_by_source_type("bi", "bogus", "w-*")  # type: ignore[arg-type]


# ---------------------------------------------------------------- fetch_k


@pytest.mark.parametrize(("top_k", "expected"), [(1, 10), (2, 200), (5, 200), (25, 250), (100, 1000)])
def test_compute_fetch_k(top_k, expected):
    assert ah.compute_fetch_k(top_k) == expected


# ---------------------------------------------------------------- overlap filter


def test_overlap_filter_none():
    assert ah.build_behavior_overlap_filter(None, None) is None


def test_overlap_filter_start_only():
    clause = ah.build_behavior_overlap_filter(datetime(2025, 1, 1, tzinfo=UTC), None)
    assert clause == {"bool": {"must": [{"range": {"end": {"gte": "2025-01-01T00:00:00+00:00"}}}]}}


def test_overlap_filter_both():
    clause = ah.build_behavior_overlap_filter(datetime(2025, 1, 1, tzinfo=UTC), datetime(2025, 1, 2, tzinfo=UTC))
    assert len(clause["bool"]["must"]) == 2


# ---------------------------------------------------------------- knn body


def test_knn_body_no_filters():
    body = ah.build_behavior_knn_body([0.1, 0.2], top_k=1, min_similarity=0.3, filter_clauses=[])
    assert body["knn"]["field"] == "embeddings.vector"
    assert body["knn"]["k"] == 10
    assert body["knn"]["num_candidates"] == 100
    assert "filter" not in body["knn"]
    assert body["size"] == 10
    assert body["min_score"] == 0.3
    assert "object.id" in body["_source"]


def test_knn_body_single_filter_inlined():
    f = {"terms": {"sensor.id.keyword": ["x"]}}
    body = ah.build_behavior_knn_body([0.1], top_k=5, min_similarity=0.0, filter_clauses=[f])
    assert body["knn"]["filter"] == f
    assert body["knn"]["k"] == 200


def test_knn_body_multi_filter_wrapped_in_bool():
    f1 = {"terms": {"sensor.id.keyword": ["x"]}}
    f2 = {"bool": {"must": []}}
    body = ah.build_behavior_knn_body([0.1], top_k=5, min_similarity=0.0, filter_clauses=[f1, f2])
    assert body["knn"]["filter"] == {"bool": {"must": [f1, f2]}}


# ---------------------------------------------------------------- midpoint


def test_midpoint_iso_valid():
    assert ah.midpoint_iso("2025-01-01T00:00:00Z", "2025-01-01T00:00:10Z") == "2025-01-01T00:00:05Z"


@pytest.mark.parametrize("bad", [("bad", "2025-01-01T00:00:10Z"), ("2025-01-01T00:00:00Z", "nope")])
def test_midpoint_iso_malformed_returns_none(bad):
    assert ah.midpoint_iso(*bad) is None


# ---------------------------------------------------------------- hit_to_result


def _behavior_hit(score: float = 0.9) -> dict:
    return {
        "_id": "h1",
        "_score": score,
        "_source": {
            "object": {"id": 42, "type": "Person", "bbox": {"leftX": 1, "rightX": 2, "topY": 3, "bottomY": 4}},
            "sensor": {"id": "cam1"},
            "timestamp": "2025-01-01T00:00:00Z",
            "end": "2025-01-01T00:00:10Z",
        },
    }


def test_hit_to_result_basic():
    r = ah.hit_to_result(_behavior_hit(), frame_result=None)
    assert r.metadata.sensor_id == "cam1"
    assert r.metadata.object_id == "42"  # coerced from int
    assert r.metadata.object_type == "Person"
    assert r.metadata.behavior_score == 0.9
    assert r.metadata.bbox == {"leftX": 1, "rightX": 2, "topY": 3, "bottomY": 4}
    assert r.metadata.frame_timestamp == "2025-01-01T00:00:05Z"  # midpoint
    assert r.metadata.start_time == "2025-01-01T00:00:00Z"
    assert r.metadata.end_time == "2025-01-01T00:00:10Z"


def test_hit_to_result_coerces_missing_fields():
    hit = {"_id": "h", "_score": 0.5, "_source": {"object": {}, "sensor": {}}}
    r = ah.hit_to_result(hit, frame_result=None)
    assert r.metadata.sensor_id == "unknown"
    assert r.metadata.object_id == "unknown"
    assert r.metadata.object_type == "unknown"
    assert r.metadata.bbox is None
    assert r.metadata.frame_timestamp is None


def test_hit_to_result_malformed_timestamps_do_not_crash():
    hit = {
        "_id": "h",
        "_score": 0.5,
        "_source": {"object": {"id": 1}, "sensor": {"id": "s"}, "timestamp": "bad", "end": "worse"},
    }
    r = ah.hit_to_result(hit, frame_result=None)
    # midpoint fails -> falls back to behavior_end string; no exception.
    assert r.metadata.frame_timestamp == "worse"
    assert r.metadata.start_time == "bad"


def test_hit_to_result_uses_frame_result():
    frame = (999, {"leftX": 9, "rightX": 9, "topY": 9, "bottomY": 9}, 0.77, "2025-01-01T00:00:03Z")
    r = ah.hit_to_result(_behavior_hit(), frame_result=frame)
    assert r.metadata.frame_timestamp == "2025-01-01T00:00:03Z"
    assert r.metadata.frame_score == 0.77
    assert r.metadata.bbox == {"leftX": 9, "rightX": 9, "topY": 9, "bottomY": 9}


def test_hit_to_result_input_timestamp_override():
    r = ah.hit_to_result(
        _behavior_hit(),
        frame_result=None,
        input_timestamp_start=datetime(2024, 6, 1, tzinfo=UTC),
        input_timestamp_end=datetime(2024, 6, 2, tzinfo=UTC),
    )
    assert r.metadata.start_time == "2024-06-01T00:00:00Z"
    assert r.metadata.end_time == "2024-06-02T00:00:00Z"


def test_hit_to_result_missing_score_raises():
    with pytest.raises(KeyError):
        ah.hit_to_result({"_source": {}}, frame_result=None)


def test_hit_to_result_preserves_zero_object_id():
    # 0 is a valid id and must not collapse to "unknown".
    hit = {"_id": "h", "_score": 0.5, "_source": {"object": {"id": 0, "type": "Person"}, "sensor": {"id": "cam1"}}}
    r = ah.hit_to_result(hit, frame_result=None)
    assert r.metadata.object_id == "0"


@pytest.mark.parametrize(
    ("value", "expected"),
    [(None, "d"), ("", "d"), (0, "0"), (42, "42"), ("x", "x")],
)
def test_coerce_str(value, expected):
    assert ah._coerce_str(value, "d") == expected


# ---------------------------------------------------------------- dedup


def _result(sensor: str, obj: str, start: str, end: str) -> AttributeSearchResult:
    return AttributeSearchResult(
        metadata=AttributeSearchMetadata(
            sensor_id=sensor, object_id=obj, object_type="p", start_time=start, end_time=end, behavior_score=0.9
        )
    )


def test_deduplicate_merges_time_range():
    r1 = _result("s", "1", "2025-01-01T00:00:05Z", "2025-01-01T00:00:06Z")
    r2 = _result("s", "1", "2025-01-01T00:00:00Z", "2025-01-01T00:00:10Z")
    candidates = [
        {"_source": {"timestamp": "2025-01-01T00:00:05Z", "end": "2025-01-01T00:00:06Z"}},
        {"_source": {"timestamp": "2025-01-01T00:00:00Z", "end": "2025-01-01T00:00:10Z"}},
    ]
    merged = ah.deduplicate_by_object([r1, r2], candidates)
    assert len(merged) == 1
    assert merged[0].metadata.start_time == "2025-01-01T00:00:00Z"
    assert merged[0].metadata.end_time == "2025-01-01T00:00:10Z"


def test_deduplicate_keeps_distinct_objects():
    r1 = _result("s", "1", "2025-01-01T00:00:00Z", "2025-01-01T00:00:01Z")
    r2 = _result("s", "2", "2025-01-01T00:00:00Z", "2025-01-01T00:00:01Z")
    assert len(ah.deduplicate_by_object([r1, r2])) == 2
