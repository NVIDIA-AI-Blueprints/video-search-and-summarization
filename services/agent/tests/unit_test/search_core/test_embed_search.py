# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Contract tests for lib.search_core.primitives.EmbedSearch.

Locks in the behaviors that /api/v1/embed_search depends on:
  - index selection by source_type (video_file → configured index;
    rtsp → wildcard list excluding configured index)
  - precomputed_embedding bypasses the embed client
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

from lib.search_core import EmbedSearch
from lib.search_core.models.embed_search import EmbedSearchInput

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

    def _make(*, hits: list[dict] | None = None, index_wildcard: str = "mdx-embed-filtered-*"):
        es = _MockEs(hits=hits)
        embed = _MockEmbed()
        vst = _MockVst()
        e = EmbedSearch(
            es=es,
            embed=embed,
            vst=vst,
            video_embed_index="video_embeddings",
            video_embed_index_wildcard=index_wildcard,
            default_max_results=10,
        )
        return e, es, embed, vst

    return _make


# ---------------------------------------------------------------------- tests


class TestEmbedSearchContract:
    @pytest.mark.asyncio
    async def test_video_file_uses_configured_index(self, make_search):
        e, es, _embed, _vst = make_search()
        out = await e.run(EmbedSearchInput(query="red car", source_type="video_file"))
        assert es.last_index == "video_embeddings"
        assert len(out.results) == 1  # h2 (no llm) skipped

    @pytest.mark.asyncio
    async def test_rtsp_uses_wildcard_with_exclusion(self, make_search):
        e, es, _embed, _vst = make_search()
        await e.run(EmbedSearchInput(query="person", source_type="rtsp"))
        assert es.last_index == ["mdx-embed-filtered-*", "-video_embeddings"]

    @pytest.mark.asyncio
    async def test_precomputed_embedding_bypasses_embed_client(self, make_search):
        e, _es, embed, _vst = make_search()
        await e.run(
            EmbedSearchInput(
                query="ignored",
                source_type="video_file",
                precomputed_embedding=[1.0, 2.0, 3.0],
            )
        )
        assert embed.text_calls == 0

    @pytest.mark.asyncio
    async def test_image_url_routes_to_image_embed(self, make_search):
        e, _es, embed, _vst = make_search()
        await e.run(EmbedSearchInput(query="", source_type="video_file", image_url="data:image/jpeg;base64,Zm9v"))
        assert embed.image_calls == 1
        assert embed.text_calls == 0

    @pytest.mark.asyncio
    async def test_video_url_routes_to_video_embed(self, make_search):
        e, _es, embed, _vst = make_search()
        await e.run(EmbedSearchInput(query="", source_type="video_file", video_url="https://example.com/x.mp4"))
        assert embed.video_calls == 1

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
        with pytest.raises(ValueError, match="at least one"):
            await e.run(EmbedSearchInput(source_type="video_file"))


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
