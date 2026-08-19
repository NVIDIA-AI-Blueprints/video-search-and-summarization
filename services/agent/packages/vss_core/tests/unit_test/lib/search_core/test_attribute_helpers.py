# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Unit tests for the attribute-search helpers.

Most helpers are pure/synchronous and are exercised with plain dicts; the small
set of async IO/enrichment helpers is exercised with lightweight mocks (no live
backends) at the bottom of the file.
"""

from __future__ import annotations

from datetime import UTC
from datetime import datetime
from types import SimpleNamespace
from typing import Any

from elasticsearch import NotFoundError as ESNotFoundError
import pytest

from vss_core._foundation.time import iso8601_instants_match
from vss_core.search_core.errors import IndexNotFoundError
from vss_core.search_core.models.attribute_search import AttributeSearchMetadata
from vss_core.search_core.models.attribute_search import AttributeSearchResult
from vss_core.search_core.primitives import _attribute_helpers as ah

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


def test_deduplicate_reads_aligned_candidate_source():
    # H2: candidates are aligned 1:1 with results, so the widen reads the RIGHT
    # candidate's _source even though a leading hit was skipped upstream.
    r1 = _result("s", "1", "2025-01-01T00:00:05Z", "2025-01-01T00:00:06Z")
    r2 = _result("s", "1", "2025-01-01T00:00:00Z", "2025-01-01T00:00:10Z")
    aligned_candidates = [
        {"_source": {"timestamp": "2025-01-01T00:00:05Z", "end": "2025-01-01T00:00:06Z"}},
        {"_source": {"timestamp": "2025-01-01T00:00:00Z", "end": "2025-01-01T00:00:10Z"}},
    ]
    merged = ah.deduplicate_by_object([r1, r2], aligned_candidates)
    assert len(merged) == 1
    assert merged[0].metadata.start_time == "2025-01-01T00:00:00Z"
    assert merged[0].metadata.end_time == "2025-01-01T00:00:10Z"


def test_deduplicate_does_not_merge_unknown_sensor():
    # H3: id-less rows carry the "unknown" sentinel and must stay distinct.
    r1 = _result("unknown", "unknown", "2025-01-01T00:00:00Z", "2025-01-01T00:00:01Z")
    r2 = _result("unknown", "unknown", "2025-01-01T00:00:02Z", "2025-01-01T00:00:03Z")
    assert len(ah.deduplicate_by_object([r1, r2])) == 2


def test_deduplicate_does_not_merge_unknown_object_id():
    r1 = _result("cam1", "unknown", "2025-01-01T00:00:00Z", "2025-01-01T00:00:01Z")
    r2 = _result("cam1", "unknown", "2025-01-01T00:00:02Z", "2025-01-01T00:00:03Z")
    assert len(ah.deduplicate_by_object([r1, r2])) == 2


def test_deduplicate_mixes_unknown_and_known():
    known1 = _result("cam1", "1", "2025-01-01T00:00:05Z", "2025-01-01T00:00:06Z")
    known2 = _result("cam1", "1", "2025-01-01T00:00:00Z", "2025-01-01T00:00:10Z")
    unknown1 = _result("unknown", "unknown", "2025-01-01T00:00:00Z", "2025-01-01T00:00:01Z")
    unknown2 = _result("unknown", "unknown", "2025-01-01T00:00:02Z", "2025-01-01T00:00:03Z")
    merged = ah.deduplicate_by_object([known1, known2, unknown1, unknown2])
    # known1/known2 collapse to one; the two unknowns stay distinct.
    assert len(merged) == 3


# ---------------------------------------------------------------- exclusion


def test_timestamps_match_exact():
    assert iso8601_instants_match("2025-01-01T00:00:00Z", "2025-01-01T00:00:00Z")


def test_timestamps_match_z_vs_offset():
    # "Z" and "+00:00" spell the same instant and must compare equal.
    assert iso8601_instants_match("2025-01-01T00:00:00Z", "2025-01-01T00:00:00+00:00")


def test_timestamps_match_different_instants():
    assert not iso8601_instants_match("2025-01-01T00:00:00Z", "2025-01-01T00:00:01Z")


def test_timestamps_match_unparseable_falls_back_to_equality():
    assert not iso8601_instants_match("bad", "worse")
    assert iso8601_instants_match("same", "same")


def test_is_attribute_excluded_by_raw_sensor_id():
    assert ah._is_attribute_excluded(
        sensor_id_raw="cam1",
        stream_id=None,
        start_time="2025-01-01T00:00:00Z",
        end_time="2025-01-01T00:00:10Z",
        exclude_videos=[
            {"sensor_id": "cam1", "start_timestamp": "2025-01-01T00:00:00Z", "end_timestamp": "2025-01-01T00:00:10Z"}
        ],
    )


def test_is_attribute_excluded_by_resolved_stream_id():
    # #9: exclude entry references the resolved stream id, raw id is a camera name.
    assert ah._is_attribute_excluded(
        sensor_id_raw="cam1",
        stream_id="8fce43a6-1c35-4d6a-b6e3-391c42090a87",
        start_time="2025-01-01T00:00:00Z",
        end_time="2025-01-01T00:00:10Z",
        exclude_videos=[
            {
                "sensor_id": "8fce43a6-1c35-4d6a-b6e3-391c42090a87",
                "start_timestamp": "2025-01-01T00:00:00Z",
                "end_timestamp": "2025-01-01T00:00:10Z",
            }
        ],
    )


def test_is_attribute_excluded_tolerates_timestamp_spelling():
    # #9: "Z" (result) vs "+00:00" (exclude entry) must still match.
    assert ah._is_attribute_excluded(
        sensor_id_raw="cam1",
        stream_id=None,
        start_time="2025-01-01T00:00:00Z",
        end_time="2025-01-01T00:00:10Z",
        exclude_videos=[
            {
                "sensor_id": "cam1",
                "start_timestamp": "2025-01-01T00:00:00+00:00",
                "end_timestamp": "2025-01-01T00:00:10+00:00",
            }
        ],
    )


def test_is_attribute_excluded_no_match_on_sensor():
    assert not ah._is_attribute_excluded(
        sensor_id_raw="cam1",
        stream_id="uuid",
        start_time="2025-01-01T00:00:00Z",
        end_time="2025-01-01T00:00:10Z",
        exclude_videos=[
            {"sensor_id": "other", "start_timestamp": "2025-01-01T00:00:00Z", "end_timestamp": "2025-01-01T00:00:10Z"}
        ],
    )


# ---------------------------------------------------------------- append ranking


def test_append_rank_key_orders_by_score_then_ids():
    hi = _result("camB", "2", "2025-01-01T00:00:00Z", "2025-01-01T00:00:01Z")
    hi.metadata.behavior_score = 0.9
    lo = _result("camA", "1", "2025-01-01T00:00:00Z", "2025-01-01T00:00:01Z")
    lo.metadata.behavior_score = 0.4
    ordered = sorted([lo, hi], key=ah._append_rank_key)
    assert [r.metadata.behavior_score for r in ordered] == [0.9, 0.4]


def test_append_rank_key_deterministic_tiebreak():
    a = _result("camA", "1", "2025-01-01T00:00:00Z", "2025-01-01T00:00:01Z")
    b = _result("camA", "2", "2025-01-01T00:00:00Z", "2025-01-01T00:00:01Z")
    a.metadata.behavior_score = 0.5
    b.metadata.behavior_score = 0.5
    ordered = sorted([b, a], key=ah._append_rank_key)
    assert [r.metadata.object_id for r in ordered] == ["1", "2"]


# ---------------------------------------------------------------- async: missing-anchor catch


class _RaisingEs:
    """ElasticIndex surface whose search always raises an ES 404."""

    async def search(self, *, index: Any, body: Any = None, **_kwargs: Any) -> Any:
        raise ESNotFoundError("index_not_found_exception", SimpleNamespace(status=404), {})

    async def aclose(self) -> None:
        return None


@pytest.mark.asyncio
async def test_search_behavior_missing_concrete_anchor_returns_empty():
    # video_file targets the concrete uploads anchor; its absence is an empty
    # uploads partition (graceful []), not a fault.
    hits = await ah._search_behavior(
        index="mdx-behavior-2025-01-01",
        query_embedding=[0.1, 0.2],
        top_k=1,
        min_similarity=0.0,
        es=_RaisingEs(),
        source_type="video_file",
    )
    assert hits == []


@pytest.mark.asyncio
async def test_search_behavior_missing_wildcard_raises():
    # An rtsp query targets a wildcard list; a genuine NotFound there is a fault
    # and must stay loud rather than silently returning empty.
    with pytest.raises(IndexNotFoundError):
        await ah._search_behavior(
            index=["mdx-behavior-*", "-mdx-behavior-2025-01-01"],
            query_embedding=[0.1, 0.2],
            top_k=1,
            min_similarity=0.0,
            es=_RaisingEs(),
            source_type="rtsp",
        )


@pytest.mark.asyncio
async def test_fetch_object_embedding_missing_concrete_anchor_returns_empty():
    # The object path seeds from a concrete anchor fetch; a missing video_file
    # anchor must degrade to an empty vector (no uploads), mirroring the
    # attribute path, so object search does not exit 5 in a live-only deployment.
    vector = await ah._fetch_object_embedding("42", "mdx-behavior-2025-01-01", _RaisingEs())
    assert vector == []


@pytest.mark.asyncio
async def test_fetch_object_embedding_missing_wildcard_raises():
    # A wildcard-list target (rtsp) that 404s is a genuine fault, not an empty
    # uploads partition, so it must still raise rather than seed an empty vector.
    with pytest.raises(IndexNotFoundError):
        await ah._fetch_object_embedding("42", ["mdx-behavior-*", "-mdx-behavior-2025-01-01"], _RaisingEs())


@pytest.mark.asyncio
async def test_search_by_object_embedding_missing_anchor_returns_empty():
    # End-to-end object leg: an absent uploads anchor yields no seed vector, so
    # the whole re-search returns [] instead of issuing an empty-vector kNN.
    results = await ah.search_by_object_embedding(
        object_id="42",
        behavior_index="mdx-behavior-2025-01-01",
        es=_RaisingEs(),
    )
    assert results == []


# ---------------------------------------------------------------- async: frame lookup


class _MockFramesEs:
    """Minimal ElasticIndex surface for frame-lookup tests."""

    def __init__(self, *, score: float = 0.8, hits: list[dict] | None = None, raise_exc: Exception | None = None):
        self._score = score
        self._hits = hits
        self._raise = raise_exc
        self.bodies: list[dict[str, Any]] = []

    async def search(self, *, index: Any, body: Any = None, **_kwargs: Any) -> Any:
        self.bodies.append(body)
        if self._raise is not None:
            raise self._raise
        if self._hits is not None:
            return {"hits": {"hits": self._hits}}
        return {
            "hits": {
                "hits": [
                    {
                        "_score": self._score,
                        "_source": {
                            "id": "frame1",
                            "timestamp": "2025-01-01T00:00:03Z",
                            "objects": [{"id": "42", "bbox": {"leftX": 1, "rightX": 2, "topY": 3, "bottomY": 4}}],
                        },
                    }
                ]
            }
        }

    async def aclose(self) -> None:
        return None


@pytest.mark.asyncio
async def test_get_frame_reports_score_verbatim_no_double_transform():
    # #6: the Painless script already returns (1 + cosine) / 2, so Python reports
    # _score verbatim rather than re-normalizing.
    es = _MockFramesEs(score=0.8)
    result = await ah._get_frame_from_behavior(
        frames_index="frames",
        sensor_id="cam1",
        object_id="42",
        start_time="2025-01-01T00:00:00Z",
        end_time="2025-01-01T00:00:10Z",
        query_embedding=[0.1, 0.2],
        es=es,
    )
    _frame_id, _bbox, frame_score, _ts = result
    assert frame_score == 0.8  # not (0.8 + 1) / 2


@pytest.mark.asyncio
async def test_get_frame_script_shifts_cosine_into_non_negative_range():
    # #7: negative cosine would make script_score throw; the script shifts cosine
    # into (1 + cosine) / 2 so the score is always non-negative.
    es = _MockFramesEs(score=0.5)
    await ah._get_frame_from_behavior(
        frames_index="frames",
        sensor_id="cam1",
        object_id="42",
        start_time="2025-01-01T00:00:00Z",
        end_time="2025-01-01T00:00:10Z",
        query_embedding=[0.1],
        es=es,
    )
    script = es.bodies[0]["query"]["function_score"]["script_score"]["script"]["source"]
    assert "(maxScore + 1.0) / 2.0" in script


@pytest.mark.asyncio
async def test_get_frame_no_hits_returns_empty_tuple():
    es = _MockFramesEs(hits=[])
    assert await ah._get_frame_from_behavior(
        frames_index="frames",
        sensor_id="cam1",
        object_id="42",
        start_time="s",
        end_time="e",
        query_embedding=[0.1],
        es=es,
    ) == (None, None, None, None)


@pytest.mark.asyncio
async def test_get_frame_swallows_errors():
    es = _MockFramesEs(raise_exc=RuntimeError("boom"))
    assert await ah._get_frame_from_behavior(
        frames_index="frames",
        sensor_id="cam1",
        object_id="42",
        start_time="s",
        end_time="e",
        query_embedding=[0.1],
        es=es,
    ) == (None, None, None, None)


def _candidate(object_id: Any, sensor_id: Any = "cam1") -> dict:
    return {"_source": {"object": {"id": object_id}, "sensor": {"id": sensor_id}}}


@pytest.mark.asyncio
async def test_perform_frame_lookups_skips_without_window():
    es = _MockFramesEs()
    out = await ah._perform_frame_lookups(
        candidates=[_candidate(1), _candidate(2)],
        query_embedding=[0.1],
        frames_index="frames",
        timestamp_start=None,
        timestamp_end=None,
        es=es,
    )
    assert out == [None, None]
    assert es.bodies == []  # no lookups issued


@pytest.mark.asyncio
async def test_perform_frame_lookups_aligns_results_to_candidates():
    # A candidate missing an id maps to None in place; ids are looked up around it,
    # keeping the returned list positionally aligned with candidates.
    es = _MockFramesEs(score=0.8)
    candidates = [_candidate(1), _candidate(None), _candidate(3)]
    out = await ah._perform_frame_lookups(
        candidates=candidates,
        query_embedding=[0.1],
        frames_index="frames",
        timestamp_start=datetime(2025, 1, 1, tzinfo=UTC),
        timestamp_end=datetime(2025, 1, 2, tzinfo=UTC),
        es=es,
    )
    assert len(out) == 3
    assert isinstance(out[0], tuple)
    assert out[1] is None  # id-less candidate skipped, position preserved
    assert isinstance(out[2], tuple)
    assert len(es.bodies) == 2  # only the two id-bearing candidates were looked up


# ---------------------------------------------------------------- async: screenshots (H1)


def _enrichable(sensor: str | None, frame_ts: str | None) -> AttributeSearchResult:
    return AttributeSearchResult(
        metadata=AttributeSearchMetadata(
            sensor_id=sensor or "",
            object_id="1",
            object_type="p",
            frame_timestamp=frame_ts,
            start_time="2025-01-01T00:00:00Z",
            end_time="2025-01-01T00:00:10Z",
            behavior_score=0.9,
        )
    )


@pytest.mark.asyncio
async def test_attach_screenshots_keeps_result_missing_timestamp(monkeypatch):
    # H1: a result without a frame_timestamp is kept (without a screenshot), never dropped.
    async def _stream_id(sensor_id: str, base_url: str) -> str:
        return "streamX"

    monkeypatch.setattr(ah, "get_stream_id", _stream_id)
    result = _enrichable("cam1", frame_ts=None)
    out = await ah._attach_screenshots([result], vst_internal_url=None, vst_external_url="http://vst", attr_query="q")
    assert len(out) == 1
    assert out[0].screenshot_url is None
    assert out[0].metadata.video_name == "cam1"  # raw sensor id captured as display name


@pytest.mark.asyncio
async def test_attach_screenshots_keeps_result_on_vst_failure(monkeypatch):
    # H1: a VST resolution failure keeps the result (without a screenshot).
    async def _boom(sensor_id: str, base_url: str) -> str:
        raise RuntimeError("vst down")

    monkeypatch.setattr(ah, "get_stream_id", _boom)
    result = _enrichable("cam1", frame_ts="2025-01-01T00:00:05Z")
    out = await ah._attach_screenshots([result], vst_internal_url=None, vst_external_url="http://vst", attr_query="q")
    assert len(out) == 1
    assert out[0].screenshot_url is None


@pytest.mark.asyncio
async def test_attach_screenshots_builds_url_on_success(monkeypatch):
    async def _stream_id(sensor_id: str, base_url: str) -> str:
        return "streamX"

    def _url(base: str, stream_id: str, ts: str) -> str:
        return f"{base}/{stream_id}/{ts}"

    monkeypatch.setattr(ah, "get_stream_id", _stream_id)
    monkeypatch.setattr(ah, "build_screenshot_url", _url)
    result = _enrichable("cam1", frame_ts="2025-01-01T00:00:05Z")
    out = await ah._attach_screenshots([result], vst_internal_url=None, vst_external_url="http://vst", attr_query="q")
    assert out[0].metadata.sensor_id == "streamX"
    assert out[0].screenshot_url == "http://vst/streamX/2025-01-01T00:00:05Z"
