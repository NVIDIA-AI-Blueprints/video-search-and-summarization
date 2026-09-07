# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Contract tests for lib.search_core.primitives.EmbedSearch.

Locks in the behaviors that /api/v1/embed_search depends on:
  - video_file queries the pinned uploads anchor; rtsp queries the wildcard
    minus that anchor (index-name subtraction, mirroring behavior/raw)
  - text queries are the only supported embedding source
  - hits without an `llm` field are skipped
  - min_cosine_similarity threshold (cosine = 2*_score - 1, rounded to 2dp)
  - exclude_videos filter
  - screenshot URL construction via VSTSnapshot
  - empty input raises ValueError

These are unit tests with mocked backends; the contract they assert is the
shape of inputs/outputs and the filter semantics, NOT the network behavior
of Elastic or the embed service.
"""

from __future__ import annotations

from typing import Any

import pytest

from vss_core.search_core import EmbedSearch
from vss_core.search_core.errors import BackendUnreachableError
from vss_core.search_core.errors import IndexNotFoundError
from vss_core.search_core.errors import InvalidInputError
from vss_core.search_core.models.embed_search import EmbedSearchInput

# ---------------------------------------------------------------------- mocks


class _MockEmbed:
    """Implements the CosmosEmbedder protocol surface used by EmbedSearch."""

    def __init__(self) -> None:
        self.text_calls = 0
        self.image_calls = 0
        self.video_calls = 0

    async def get_text_embedding(self, text: str) -> list[float]:
        self.text_calls += 1
        return [0.1, 0.2, 0.3]

    async def get_image_embedding(self, image_url: str) -> list[float]:
        self.image_calls += 1
        return [0.4, 0.5, 0.6]

    async def get_video_embedding(self, video_url: str) -> list[float]:
        self.video_calls += 1
        return [0.7, 0.8, 0.9]

    async def aclose(self) -> None:
        return None


class _MockEs:
    """Implements the ElasticIndex protocol surface used by EmbedSearch."""

    def __init__(self, hits: list[dict] | None = None) -> None:
        self.last_index: str | list[str] | None = None
        self.last_body: dict | None = None
        self._hits = hits if hits is not None else _default_hits()

    async def search(self, *, index: Any, body: Any = None, **kwargs: Any) -> Any:
        self.last_index = index
        self.last_body = body
        return {"hits": {"hits": self._hits}}

    async def aclose(self) -> None:
        return None


class _MockVst:
    """Implements the VSTSnapshot protocol surface used by EmbedSearch."""

    def build_screenshot_url(self, *, sensor_id: str, timestamp: str, internal: bool = False) -> str:
        return f"http://vst:7777/vst/api/v1/replay/stream/{sensor_id}/picture?startTime={timestamp}"

    async def resolve_stream_id(self, sensor_id: str) -> str:
        return sensor_id

    async def get_timeline(self, sensor_id: str) -> tuple[str, str]:
        raise NotImplementedError

    async def aclose(self) -> None:
        return None


def _default_hits() -> list[dict]:
    return [
        {
            "_id": "h1",
            "_score": 0.85,  # cosine = 2*0.85 - 1 = 0.7
            "_source": {
                "llm": {"queries": []},
                "sensor": {
                    "id": "8fce43a6-1c35-4d6a-b6e3-391c42090a87",
                    "description": "warehouse cam",
                    "info": {"path": "/tmp/8fce43a6-1c35-4d6a-b6e3-391c42090a87/video.mp4"},
                },
                "timestamp": "2025-01-01T00:00:00",
                "end": "2025-01-01T00:00:05",
            },
        },
        # No "llm" key → must be skipped (matches tools/embed_search.py:421).
        {"_id": "h2", "_score": 0.50, "_source": {"sensor": {}}},
    ]


@pytest.fixture
def make_search():
    """Factory that returns (EmbedSearch, es_mock, embed_mock, vst_mock)."""

    def _make(
        *,
        hits: list[dict] | None = None,
        index_base: str = "mdx-embed-filtered-2025-01-01",
        index_wildcard: str = "mdx-embed-filtered-*",
    ):
        es = _MockEs(hits=hits)
        embed = _MockEmbed()
        vst = _MockVst()
        e = EmbedSearch(
            es=es,
            embed=embed,
            vst=vst,
            video_embed_index=index_base,
            video_embed_index_wildcard=index_wildcard,
            default_max_results=10,
        )
        return e, es, embed, vst

    return _make


# ---------------------------------------------------------------------- tests


class TestEmbedSearchContract:
    @pytest.mark.asyncio
    async def test_video_file_uses_pinned_base(self, make_search):
        e, es, _embed, _vst = make_search()
        out = await e.run(EmbedSearchInput(query="red car", source_type="video_file"))
        assert es.last_index == "mdx-embed-filtered-2025-01-01"
        assert len(out.results) == 1  # h2 (no llm) skipped

    @pytest.mark.asyncio
    async def test_rtsp_subtracts_base_from_wildcard(self, make_search):
        e, es, _embed, _vst = make_search()
        await e.run(EmbedSearchInput(query="person", source_type="rtsp"))
        assert es.last_index == ["mdx-embed-filtered-*", "-mdx-embed-filtered-2025-01-01"]

    @pytest.mark.asyncio
    async def test_missing_llm_key_is_skipped(self, make_search):
        e, _es, _embed, _vst = make_search()
        out = await e.run(EmbedSearchInput(query="q", source_type="video_file"))
        # h2 has no "llm" key in _source → skipped.
        assert len(out.results) == 1
        assert out.results[0].sensor_id == "8fce43a6-1c35-4d6a-b6e3-391c42090a87"

    @pytest.mark.asyncio
    async def test_min_cosine_similarity_threshold(self, make_search):
        e, _es, _embed, _vst = make_search()
        # h1 cosine = 0.7; threshold of 0.9 must filter it out.
        out = await e.run(EmbedSearchInput(query="q", source_type="video_file", min_cosine_similarity=0.9))
        assert len(out.results) == 0

    @pytest.mark.asyncio
    async def test_exclude_videos_filter(self, make_search):
        e, _es, _embed, _vst = make_search()
        # First produce a result to find its timestamps, then exclude it.
        first = await e.run(EmbedSearchInput(query="q", source_type="video_file"))
        assert len(first.results) == 1
        r = first.results[0]

        e2, _, _, _ = make_search()
        out = await e2.run(
            EmbedSearchInput(
                query="q",
                source_type="video_file",
                exclude_videos=[
                    {
                        "sensor_id": "8fce43a6-1c35-4d6a-b6e3-391c42090a87",
                        "start_timestamp": r.start_time,
                        "end_timestamp": r.end_time,
                    }
                ],
            )
        )
        assert len(out.results) == 0

    @pytest.mark.asyncio
    async def test_screenshot_url_construction(self, make_search):
        e, _es, _embed, _vst = make_search()
        out = await e.run(EmbedSearchInput(query="q", source_type="video_file"))
        assert (
            out.results[0].screenshot_url
            == f"http://vst:7777/vst/api/v1/replay/stream/{out.results[0].sensor_id}/picture?startTime={out.results[0].start_time}"
        )

    @pytest.mark.asyncio
    async def test_empty_input_raises(self, make_search):
        e, _es, _embed, _vst = make_search()
        with pytest.raises(InvalidInputError, match="query must be non-empty"):
            await e.run(EmbedSearchInput(source_type="video_file"))

    @pytest.mark.asyncio
    async def test_whitespace_only_query_raises(self, make_search):
        e, _es, embed, _vst = make_search()
        with pytest.raises(InvalidInputError, match="query must be non-empty"):
            await e.run(EmbedSearchInput(query="   ", source_type="video_file"))
        assert embed.text_calls == 0  # never hit the embed service

    @pytest.mark.asyncio
    async def test_timestamp_start_after_end_raises(self, make_search):
        e, _es, _embed, _vst = make_search()
        with pytest.raises(InvalidInputError, match="must not be after"):
            await e.run(
                EmbedSearchInput(
                    query="q",
                    source_type="video_file",
                    timestamp_start="2025-01-02T00:00:00Z",
                    timestamp_end="2025-01-01T00:00:00Z",
                )
            )

    @pytest.mark.asyncio
    async def test_missing_uploads_anchor_returns_empty(self, make_search):
        # video_file against the pinned anchor with no ingested files: the
        # absent anchor is an empty uploads partition, not a fault.
        e, es, _embed, _vst = make_search()

        async def _raise(**_kwargs: Any) -> Any:
            raise IndexNotFoundError("mdx-embed-filtered-2025-01-01")

        es.search = _raise  # type: ignore[method-assign]
        out = await e.run(EmbedSearchInput(query="q", source_type="video_file"))
        assert out.results == []

    @pytest.mark.asyncio
    async def test_missing_nonanchor_index_raises(self, make_search):
        # A missing index that is NOT the pinned anchor is a genuine fault and
        # still raises (the graceful-empty catch is gated on the anchor).
        e, es, _embed, _vst = make_search(index_base="mdx-embed-filtered-2099-01-01")

        async def _raise(**_kwargs: Any) -> Any:
            raise IndexNotFoundError("mdx-embed-filtered-2099-01-01")

        es.search = _raise  # type: ignore[method-assign]
        with pytest.raises(IndexNotFoundError) as exc_info:
            await e.run(EmbedSearchInput(query="q", source_type="video_file"))
        assert exc_info.value.index == "mdx-embed-filtered-2099-01-01"
        assert exc_info.value.backend == "elasticsearch"

    @pytest.mark.asyncio
    async def test_malformed_hit_is_skipped(self, make_search):
        # A hit missing "_score" is unprocessable; the primitive skips it per-hit
        # (best-effort mapping) rather than failing the whole search.
        bad_hits = [{"_id": "bad", "_source": {"llm": {}}}]
        e, _es, _embed, _vst = make_search(hits=bad_hits)
        out = await e.run(EmbedSearchInput(query="q", source_type="video_file"))
        assert out.results == []

    @pytest.mark.asyncio
    async def test_malformed_search_envelope_is_backend_error(self, make_search):
        e, es, _embed, _vst = make_search()

        async def _malformed(**_kwargs: Any) -> Any:
            return {"hits": {}}

        es.search = _malformed  # type: ignore[method-assign]
        with pytest.raises(BackendUnreachableError, match=r"hits\.hits"):
            await e.run(EmbedSearchInput(query="q", source_type="video_file"))

    @pytest.mark.asyncio
    async def test_one_corrupt_hit_does_not_fail_whole_search(self, make_search):
        # A non-dict "sensor" makes one hit unprocessable; the good hit still returns.
        good = _default_hits()[0]
        corrupt = {"_id": "corrupt", "_score": 0.85, "_source": {"llm": {}, "sensor": "not-a-dict"}}
        e, _es, _embed, _vst = make_search(hits=[corrupt, good])
        out = await e.run(EmbedSearchInput(query="q", source_type="video_file"))
        assert len(out.results) == 1
        assert out.results[0].sensor_id == "8fce43a6-1c35-4d6a-b6e3-391c42090a87"

    @pytest.mark.asyncio
    async def test_null_description_does_not_crash(self, make_search):
        hit = {
            "_id": "h1",
            "_score": 0.85,
            "_source": {
                "llm": {"queries": []},
                "sensor": {
                    "id": "8fce43a6-1c35-4d6a-b6e3-391c42090a87",
                    "description": None,
                    "info": {"path": "/tmp/8fce43a6-1c35-4d6a-b6e3-391c42090a87/v.mp4"},
                },
                "timestamp": "2025-01-01T00:00:00",
                "end": "2025-01-01T00:00:05",
            },
        }
        e, _es, _embed, _vst = make_search(hits=[hit])
        out = await e.run(EmbedSearchInput(query="q", source_type="video_file"))
        assert len(out.results) == 1
        assert out.results[0].description == ""

    @pytest.mark.asyncio
    async def test_null_sensor_id_does_not_crash(self, make_search):
        hit = {
            "_id": "h1",
            "_score": 0.85,
            "_source": {
                "llm": {"queries": []},
                "sensor": {"id": None, "info": {"path": "/tmp/no-uuid/v.mp4"}},
                "timestamp": "2025-01-01T00:00:00",
                "end": "2025-01-01T00:00:05",
            },
        }
        e, _es, _embed, _vst = make_search(hits=[hit])
        out = await e.run(EmbedSearchInput(query="q", source_type="video_file"))
        assert len(out.results) == 1
        assert out.results[0].sensor_id == ""

    @pytest.mark.asyncio
    async def test_rtsp_missing_index_message_is_readable(self, make_search):
        e, es, _embed, _vst = make_search()

        async def _raise(**_kwargs: Any) -> Any:
            raise IndexNotFoundError(["mdx-embed-filtered-*", "-video_embeddings"])

        es.search = _raise  # type: ignore[method-assign]
        with pytest.raises(IndexNotFoundError) as exc_info:
            await e.run(EmbedSearchInput(query="q", source_type="rtsp"))
        # index attribute preserves the raw list; the message is comma-joined.
        assert exc_info.value.index == ["mdx-embed-filtered-*", "-video_embeddings"]
        assert "mdx-embed-filtered-*, -video_embeddings" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_exclude_videos_by_resolved_uuid(self, make_search):
        # RTSP-style: exclude entry references the resolved UUID, not sensor name.
        e, _es, _embed, _vst = make_search()
        first = await e.run(EmbedSearchInput(query="q", source_type="video_file"))
        r = first.results[0]
        assert r.sensor_id == "8fce43a6-1c35-4d6a-b6e3-391c42090a87"

        e2, _, _, _ = make_search()
        out = await e2.run(
            EmbedSearchInput(
                query="q",
                source_type="video_file",
                exclude_videos=[
                    {
                        "sensor_id": r.sensor_id,  # the UUID, as returned to callers
                        "start_timestamp": r.start_time,
                        "end_timestamp": r.end_time,
                    }
                ],
            )
        )
        assert len(out.results) == 0


def _knn(body: dict) -> dict:
    """Pull the knn clause out of either the filtered or unfiltered query shape."""
    query = body["query"]
    if "bool" in query:
        return query["bool"]["must"][0]["nested"]["query"]["knn"]
    return query["nested"]["query"]["knn"]


class TestEmbedSearchQueryShape:
    """ES query construction details the embed path depends on."""

    @pytest.mark.asyncio
    async def test_default_k_uses_configured_default(self, make_search):
        e, es, _embed, _vst = make_search()
        await e.run(EmbedSearchInput(query="q", source_type="video_file"))
        # top_k unset -> k == default_max_results (10 from the fixture)
        assert es.last_body["size"] == 10
        assert _knn(es.last_body)["k"] == 10

    @pytest.mark.asyncio
    async def test_unfiltered_uses_top_k(self, make_search):
        # No filter and no similarity threshold -> k == top_k (no overfetch).
        e, es, _embed, _vst = make_search()
        await e.run(EmbedSearchInput(query="q", source_type="video_file", top_k=3))
        assert _knn(es.last_body)["k"] == 3
        assert "bool" not in es.last_body["query"]

    @pytest.mark.asyncio
    async def test_top_k_overfetches_with_threshold(self, make_search):
        e, es, _embed, _vst = make_search()
        await e.run(EmbedSearchInput(query="q", source_type="video_file", top_k=3, min_cosine_similarity=0.5))
        assert _knn(es.last_body)["k"] == 15  # 3 * 5

    @pytest.mark.asyncio
    async def test_top_k_overfetches_with_filters(self, make_search):
        e, es, _embed, _vst = make_search()
        await e.run(EmbedSearchInput(query="q", source_type="video_file", top_k=2, description="warehouse"))
        assert _knn(es.last_body)["k"] == 10  # 2 * 5

    @pytest.mark.asyncio
    async def test_description_filter_present(self, make_search):
        e, es, _embed, _vst = make_search()
        await e.run(EmbedSearchInput(query="q", source_type="video_file", description="warehouse"))
        assert "filter" in es.last_body["query"]["bool"]

    @pytest.mark.asyncio
    async def test_timestamp_filter_uses_overlap_semantics(self, make_search):
        # #11: the embed time filter must use OVERLAP (end >= start AND
        # timestamp <= end), not containment, so a straddling segment matches.
        e, es, _embed, _vst = make_search()
        await e.run(
            EmbedSearchInput(
                query="q",
                source_type="video_file",
                timestamp_start="2025-01-01T00:00:00Z",
                timestamp_end="2025-01-02T00:00:00Z",
            )
        )
        # timestamp is the only filter, so it is the sole filter clause.
        time_clause = es.last_body["query"]["bool"]["filter"][0]
        assert time_clause["bool"]["must"] == [
            {"range": {"end": {"gte": "2025-01-01T00:00:00+00:00"}}},
            {"range": {"timestamp": {"lte": "2025-01-02T00:00:00+00:00"}}},
        ]

    @pytest.mark.asyncio
    async def test_uuid_video_source_uses_terms_clause(self, make_search):
        e, es, _embed, _vst = make_search()
        uuid = "8fce43a6-1c35-4d6a-b6e3-391c42090a87"
        await e.run(EmbedSearchInput(query="q", source_type="video_file", video_sources=[uuid]))
        # video_sources is the only filter, so it is the sole filter clause.
        assert es.last_body["query"]["bool"]["filter"][0] == {"terms": {"sensor.id.keyword": [uuid]}}

    @pytest.mark.asyncio
    async def test_top_k_caps_results(self, make_search):
        many_hits = [
            {
                "_id": f"h{i}",
                "_score": 0.85,
                "_source": {
                    "llm": {"queries": []},
                    "sensor": {"id": f"8fce43a6-1c35-4d6a-b6e3-39000000000{i}", "info": {"path": "/tmp/v.mp4"}},
                    "timestamp": "2025-01-01T00:00:00",
                    "end": "2025-01-01T00:00:05",
                },
            }
            for i in range(5)
        ]
        e, _es, _embed, _vst = make_search(hits=many_hits)
        out = await e.run(EmbedSearchInput(query="q", source_type="video_file", top_k=2))
        assert len(out.results) == 2


class TestEmbedSearchOutputShape:
    """The contract `/api/v1/embed_search` callers depend on."""

    @pytest.mark.asyncio
    async def test_output_has_query_embedding_and_results(self, make_search):
        e, _, _, _ = make_search()
        out = await e.run(EmbedSearchInput(query="q", source_type="video_file"))
        assert out.query_embedding == [0.1, 0.2, 0.3]
        assert isinstance(out.results, list)

    @pytest.mark.asyncio
    async def test_result_item_field_names(self, make_search):
        e, _, _, _ = make_search()
        out = await e.run(EmbedSearchInput(query="q", source_type="video_file"))
        r = out.results[0]
        # Field NAMES are part of the contract — every caller pattern-matches
        # on these. If any rename happens, this test must be intentionally
        # updated and a CHANGELOG entry added (DESIGN.md §13).
        assert set(r.model_dump().keys()) == {
            "video_name",
            "description",
            "start_time",
            "end_time",
            "sensor_id",
            "screenshot_url",
            "similarity_score",
        }
