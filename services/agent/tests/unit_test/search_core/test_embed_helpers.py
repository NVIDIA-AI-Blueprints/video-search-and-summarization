# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Unit tests for the pure embed-search helpers.

These exercise the dependency-free building blocks in
``lib.search_core.primitives._embed_helpers`` directly with plain dicts — no
async, no mocks, no backends.
"""

from __future__ import annotations

import pytest

from lib.search_core.models.embed_search import EmbedSearchInput
from lib.search_core.primitives import _embed_helpers as h

_UUID = "8fce43a6-1c35-4d6a-b6e3-391c42090a87"


# ---------------------------------------------------------------- index select


def test_select_index_video_file():
    assert h.select_search_index("video_file", video_embed_index="vi", video_embed_index_wildcard="w-*") == "vi"


def test_select_index_rtsp():
    assert h.select_search_index("rtsp", video_embed_index="vi", video_embed_index_wildcard="w-*") == ["w-*", "-vi"]


# (escaping + video_sources filter now live in _internal/es_filters.py and are
#  covered by test_es_filters.py)


# ---------------------------------------------------------------- filters


def test_description_filter_none():
    assert h.build_description_filter(None) is None
    assert h.build_description_filter("") is None


def test_description_filter_shape():
    clause = h.build_description_filter("warehouse")
    assert clause["bool"]["minimum_should_match"] == 1
    assert {"match": {"sensor.description": "warehouse"}} in clause["bool"]["should"]


def test_timestamp_filter_none():
    assert h.build_timestamp_filter(None, None) is None


def test_timestamp_filter_start_only():
    from datetime import UTC
    from datetime import datetime

    clause = h.build_timestamp_filter(datetime(2025, 1, 1, tzinfo=UTC), None)
    assert clause == {"range": {"timestamp": {"gte": "2025-01-01T00:00:00+00:00"}}}


def test_timestamp_filter_both():
    from datetime import UTC
    from datetime import datetime

    clause = h.build_timestamp_filter(datetime(2025, 1, 1, tzinfo=UTC), datetime(2025, 1, 2, tzinfo=UTC))
    assert "must" in clause["bool"]
    assert len(clause["bool"]["must"]) == 2


# ---------------------------------------------------------------- k value


@pytest.mark.parametrize(
    ("top_k", "min_cos", "has_filters", "expected"),
    [
        (None, 0.0, False, 100),  # default
        (5, 0.0, False, 5),  # plain
        (5, 0.5, False, 25),  # threshold -> overfetch
        (5, 0.0, True, 25),  # filters -> overfetch
    ],
)
def test_compute_k_value(top_k, min_cos, has_filters, expected):
    assert (
        h.compute_k_value(top_k, default_max_results=100, min_cosine_similarity=min_cos, has_filters=has_filters)
        == expected
    )


def test_build_es_query_unfiltered():
    inp = EmbedSearchInput(query="q", source_type="video_file", top_k=4)
    body = h.build_es_query(inp, [0.1, 0.2], default_max_results=100)
    assert body["size"] == 4
    assert body["query"]["nested"]["query"]["knn"]["k"] == 4
    assert body["query"]["nested"]["query"]["knn"]["query_vector"] == [0.1, 0.2]


def test_build_es_query_filtered():
    inp = EmbedSearchInput(query="q", source_type="video_file", top_k=2, description="warehouse")
    body = h.build_es_query(inp, [0.1], default_max_results=100)
    # filters -> overfetch (2 * 5)
    assert body["size"] == 10
    assert "filter" in body["query"]["bool"]


# ---------------------------------------------------------------- scoring


@pytest.mark.parametrize(("score", "expected"), [(0.85, 0.7), (0.5, 0.0), (1.0, 1.0), (0.6, 0.2)])
def test_score_to_cosine(score, expected):
    assert h.score_to_cosine(score) == expected


# ---------------------------------------------------------------- stream id


def test_extract_stream_id_from_stream_id_field():
    assert h.extract_stream_id({"stream_id": _UUID, "id": "cam1"}, "") == _UUID


def test_extract_stream_id_from_path():
    assert h.extract_stream_id({"id": "cam1"}, f"rtsp://host/live/{_UUID}") == _UUID


def test_extract_stream_id_from_sensor_id():
    assert h.extract_stream_id({"id": _UUID}, "/tmp/no-uuid/file.mp4") == _UUID


def test_extract_stream_id_fallback_to_name():
    assert h.extract_stream_id({"id": "cam1"}, "/tmp/file.mp4") == "cam1"


def test_extract_stream_id_empty():
    assert h.extract_stream_id({}, "") is None


# ---------------------------------------------------------------- response data


def test_extract_response_data_valid():
    queries = [{"response": '{"video_name": "v.mp4"}'}]
    assert h.extract_response_data(queries) == {"video_name": "v.mp4"}


@pytest.mark.parametrize("queries", [[], "notalist", [{"response": "not-json"}], [{"response": "[1,2]"}], [{}]])
def test_extract_response_data_bad(queries):
    assert h.extract_response_data(queries) == {}


# ---------------------------------------------------------------- video name


def test_extract_video_name_from_response():
    assert h.extract_video_name({"video_name": "v.mp4"}, _UUID, "/tmp/x.mp4") == "v.mp4"


def test_extract_video_name_video_file_uses_basename():
    assert h.extract_video_name({}, _UUID, "/tmp/a/b/clip.mp4") == "clip.mp4"


def test_extract_video_name_rtsp_uses_sensor_name():
    assert h.extract_video_name({}, "cam1", "rtsp://host/live") == "cam1"


# ---------------------------------------------------------------- timestamps


def test_extract_timestamps_from_response_data():
    start, end = h.extract_timestamps({}, {"start_time": "2025-05-01T00:00:00Z", "end_time": "2025-05-01T00:01:00Z"})
    assert start == "2025-05-01T00:00:00Z"
    assert end == "2025-05-01T00:01:00Z"


def test_extract_timestamps_from_source():
    start, end = h.extract_timestamps({"timestamp": "2025-05-01T00:00:00Z", "end": "2025-05-01T00:01:00Z"}, {})
    assert start.startswith("2025-05-01T00:00:00")
    assert end.startswith("2025-05-01T00:01:00")


def test_extract_timestamps_fallback():
    start, end = h.extract_timestamps({}, {})
    assert start.startswith("2025-01-01")
    assert end.startswith("2025-01-01")


# ---------------------------------------------------------------- exclusion


def test_is_excluded_by_raw_sensor_id():
    assert h.is_excluded(
        sensor_id_raw="cam1",
        stream_id=_UUID,
        start_time="s",
        end_time="e",
        exclude_videos=[{"sensor_id": "cam1", "start_timestamp": "s", "end_timestamp": "e"}],
    )


def test_is_excluded_by_resolved_uuid():
    # DIVERGENCE: exclude entry uses the resolved UUID, raw id is a name.
    assert h.is_excluded(
        sensor_id_raw="cam1",
        stream_id=_UUID,
        start_time="s",
        end_time="e",
        exclude_videos=[{"sensor_id": _UUID, "start_timestamp": "s", "end_timestamp": "e"}],
    )


def test_is_excluded_no_match():
    assert not h.is_excluded(
        sensor_id_raw="cam1",
        stream_id=_UUID,
        start_time="s",
        end_time="e",
        exclude_videos=[{"sensor_id": "other", "start_timestamp": "s", "end_timestamp": "e"}],
    )


# ---------------------------------------------------------------- parse_hit


def _hit(score: float = 0.85) -> dict:
    return {
        "_id": "h1",
        "_score": score,
        "_source": {
            "llm": {"queries": []},
            "sensor": {"id": _UUID, "description": "warehouse cam", "info": {"path": f"/tmp/{_UUID}/v.mp4"}},
            "timestamp": "2025-01-01T00:00:00",
            "end": "2025-01-01T00:00:05",
        },
    }


def test_parse_hit_valid():
    parsed = h.parse_hit(_hit(), min_cosine_similarity=0.0, exclude_videos=[])
    assert parsed is not None
    assert parsed.sensor_id == _UUID
    assert parsed.video_name == "v.mp4"
    assert parsed.similarity_score == 0.7


def test_parse_hit_below_threshold():
    assert h.parse_hit(_hit(score=0.6), min_cosine_similarity=0.9, exclude_videos=[]) is None


def test_parse_hit_missing_llm():
    hit = _hit()
    del hit["_source"]["llm"]
    assert h.parse_hit(hit, min_cosine_similarity=0.0, exclude_videos=[]) is None


def test_parse_hit_excluded():
    parsed = h.parse_hit(_hit(), min_cosine_similarity=0.0, exclude_videos=[])
    assert parsed is not None
    again = h.parse_hit(
        _hit(),
        min_cosine_similarity=0.0,
        exclude_videos=[{"sensor_id": _UUID, "start_timestamp": parsed.start_time, "end_timestamp": parsed.end_time}],
    )
    assert again is None


def test_parse_hit_missing_score_raises_keyerror():
    # The primitive catches this per-hit; the helper itself surfaces the error.
    with pytest.raises(KeyError):
        h.parse_hit({"_source": {"llm": {}}}, min_cosine_similarity=0.0, exclude_videos=[])


def test_parse_hit_null_description_coerced_to_empty():
    hit = _hit()
    hit["_source"]["sensor"]["description"] = None
    parsed = h.parse_hit(hit, min_cosine_similarity=0.0, exclude_videos=[])
    assert parsed is not None
    assert parsed.description == ""


def test_parse_hit_null_sensor_id_coerced():
    hit = _hit()
    hit["_source"]["sensor"]["id"] = None
    hit["_source"]["sensor"]["info"]["path"] = "/tmp/no-uuid/v.mp4"
    parsed = h.parse_hit(hit, min_cosine_similarity=0.0, exclude_videos=[])
    assert parsed is not None
    assert parsed.sensor_id == ""
    assert isinstance(parsed.description, str)


def test_extract_timestamps_coerces_non_string_values():
    # A non-string start_time in stored response data must not leak a non-str out.
    start, end = h.extract_timestamps({}, {"start_time": 123, "end_time": 456})
    assert (start, end) == ("123", "456")
    assert isinstance(start, str) and isinstance(end, str)
