# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Contract tests for lib.search_core.primitives.AttributeSearch (mocked backends)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from elasticsearch import NotFoundError as ESNotFoundError
import pytest

from vss_core.search_core.errors import IndexNotFoundError
from vss_core.search_core.errors import InvalidInputError
from vss_core.search_core.models.attribute_search import AttributeSearchInput
from vss_core.search_core.primitives.attribute_search import AttributeSearch
from vss_core.search_core.runtime import BEHAVIOR_INDEX_ANCHOR

# --------------------------------------------------------------------- mocks


def _behavior_hit(object_id: int = 42, sensor_id: str = "cam1", score: float = 0.9) -> dict:
    return {
        "_id": f"h{object_id}",
        "_score": score,
        "_source": {
            "object": {"id": object_id, "type": "Person", "bbox": {"leftX": 1, "rightX": 2, "topY": 3, "bottomY": 4}},
            "sensor": {"id": sensor_id},
            "timestamp": "2025-01-01T00:00:00Z",
            "end": "2025-01-01T00:00:10Z",
        },
    }


class _MockEs:
    def __init__(self, behavior_hits: list[dict] | None = None, *, raise_not_found: bool = False) -> None:
        self._behavior_hits = behavior_hits if behavior_hits is not None else [_behavior_hit()]
        self._raise_not_found = raise_not_found
        self.calls: list[dict[str, Any]] = []

    async def search(self, *, index: Any, body: Any = None, **_kwargs: Any) -> Any:
        self.calls.append({"index": index, "body": body})
        if self._raise_not_found:
            raise ESNotFoundError("index_not_found_exception", SimpleNamespace(status=404), {})
        if body and "knn" in body:
            return {"hits": {"hits": self._behavior_hits}}
        return {"hits": {"hits": []}}

    async def aclose(self) -> None:
        return None

    @property
    def endpoint(self) -> str:
        return "http://mock-es"


class _MockEmbed:
    def __init__(self) -> None:
        self.calls = 0

    async def get_text_embedding(self, text: str) -> list[float]:
        self.calls += 1
        return [0.1, 0.2, 0.3]

    async def aclose(self) -> None:
        return None


@pytest.fixture
def make_attr():
    def _make(
        *,
        behavior_hits: list[dict] | None = None,
        raise_not_found: bool = False,
        behavior_index: str = "behavior_index",
    ):
        es = _MockEs(behavior_hits, raise_not_found=raise_not_found)
        embed = _MockEmbed()
        attr = AttributeSearch(
            es=es,
            embed=embed,
            behavior_index=behavior_index,
            behavior_index_wildcard="mdx-behavior-*",
            frames_index=None,
            frames_index_wildcard="mdx-raw-*",
            enable_frame_lookup=False,  # keep tests to the behavior path
            default_max_results=10,
            vst_external_url="",  # skip VST screenshot resolution (no HTTP in tests)
            vst_internal_url=None,
        )
        return attr, es, embed

    return _make


def _behavior_body(es: _MockEs) -> dict:
    return next(call["body"] for call in es.calls if call["body"] and "knn" in call["body"])


# --------------------------------------------------------------------- tests


class TestAttributeSearchContract:
    @pytest.mark.asyncio
    async def test_video_file_uses_behavior_index(self, make_attr):
        attr, es, _embed = make_attr()
        await attr.run(AttributeSearchInput(query="red hat", source_type="video_file"))
        assert es.calls[0]["index"] == "behavior_index"

    @pytest.mark.asyncio
    async def test_rtsp_uses_wildcard_index(self, make_attr):
        attr, es, _embed = make_attr()
        await attr.run(AttributeSearchInput(query="red hat", source_type="rtsp"))
        # _search_behavior joins the index list into a comma string for the client.
        assert es.calls[0]["index"] == "mdx-behavior-*,-behavior_index"

    @pytest.mark.asyncio
    async def test_min_similarity_passed_as_min_score(self, make_attr):
        attr, es, _embed = make_attr()
        await attr.run(AttributeSearchInput(query="q", source_type="video_file", min_similarity=0.42))
        assert _behavior_body(es)["min_score"] == 0.42

    @pytest.mark.asyncio
    async def test_basic_result_shape(self, make_attr):
        attr, _es, _embed = make_attr()
        out = await attr.run(AttributeSearchInput(query="q", source_type="video_file"))
        assert len(out.results) == 1
        meta = out.results[0].metadata
        assert meta.sensor_id == "cam1"
        assert meta.object_id == "42"
        assert meta.object_type == "Person"

    @pytest.mark.asyncio
    async def test_top_k_caps_results_append_mode(self, make_attr):
        hits = [_behavior_hit(object_id=i) for i in (1, 2, 3)]
        attr, _es, _embed = make_attr(behavior_hits=hits)
        out = await attr.run(
            AttributeSearchInput(query="q", source_type="video_file", top_k=2, fuse_multi_attribute=False)
        )
        assert len(out.results) == 2

    @pytest.mark.asyncio
    async def test_exclude_videos_filter(self, make_attr):
        attr, _es, _embed = make_attr()
        out = await attr.run(
            AttributeSearchInput(
                query="q",
                source_type="video_file",
                exclude_videos=[
                    {
                        "sensor_id": "cam1",
                        "start_timestamp": "2025-01-01T00:00:00Z",
                        "end_timestamp": "2025-01-01T00:00:10Z",
                    }
                ],
            )
        )
        assert out.results == []

    @pytest.mark.asyncio
    async def test_fuse_mode_embeds_each_attribute(self, make_attr):
        attr, _es, embed = make_attr()
        await attr.run(
            AttributeSearchInput(query=["person", "red hat"], source_type="video_file", fuse_multi_attribute=True)
        )
        assert embed.calls == 2

    @pytest.mark.asyncio
    async def test_append_mode_embeds_each_attribute_and_dedups(self, make_attr):
        attr, _es, embed = make_attr()
        out = await attr.run(
            AttributeSearchInput(query=["person", "red hat"], source_type="video_file", fuse_multi_attribute=False)
        )
        assert embed.calls == 2
        # both attributes match the same (sensor, object), so dedup collapses to one.
        assert len(out.results) == 1

    @pytest.mark.asyncio
    async def test_append_mode_continues_on_single_attribute_error(self):
        # A non-systemic failure for one attribute must not sink the whole request.
        class _SelectiveEmbed:
            def __init__(self, bad_query: str) -> None:
                self.bad_query = bad_query
                self.calls = 0

            async def get_text_embedding(self, text: str) -> list[float]:
                self.calls += 1
                if text == self.bad_query:
                    raise ValueError("embed failed for this attribute")
                return [0.1, 0.2, 0.3]

            async def aclose(self) -> None:
                return None

        es = _MockEs([_behavior_hit()])
        embed = _SelectiveEmbed(bad_query="red hat")
        attr = AttributeSearch(
            es=es,
            embed=embed,  # type: ignore[arg-type]
            behavior_index="behavior_index",
            behavior_index_wildcard="mdx-behavior-*",
            frames_index=None,
            enable_frame_lookup=False,
            default_max_results=10,
            vst_external_url="",
            vst_internal_url=None,
        )
        out = await attr.run(
            AttributeSearchInput(query=["person", "red hat"], source_type="video_file", fuse_multi_attribute=False)
        )
        assert embed.calls == 2
        assert len(out.results) == 1  # "person" survived; "red hat" was skipped

    @pytest.mark.asyncio
    async def test_append_mode_propagates_systemic_error(self, make_attr):
        # A missing wildcard affects every attribute: fail fast, don't return
        # partial. (rtsp targets a wildcard list, so a NotFound there is a genuine
        # fault, unlike an absent video_file anchor.)
        attr, _es, _embed = make_attr(raise_not_found=True)
        with pytest.raises(IndexNotFoundError):
            await attr.run(
                AttributeSearchInput(query=["person", "red hat"], source_type="rtsp", fuse_multi_attribute=False)
            )

    @pytest.mark.asyncio
    async def test_missing_anchor_video_file_returns_empty(self, make_attr):
        # A live-only deployment has no uploads anchor index. The video_file leg
        # queries that concrete anchor, so its absence is an empty uploads
        # partition (graceful []), not a fault: no IndexNotFoundError, no exit 5.
        # Graceful-empty is gated on equality with the pinned anchor, so the base
        # must be that anchor (a customized base would raise instead).
        attr, _es, _embed = make_attr(raise_not_found=True, behavior_index=BEHAVIOR_INDEX_ANCHOR)
        out = await attr.run(AttributeSearchInput(query="q", source_type="video_file"))
        assert out.results == []

    @pytest.mark.asyncio
    async def test_missing_index_rtsp_message_lists_indices(self, make_attr):
        attr, _es, _embed = make_attr(raise_not_found=True)
        with pytest.raises(IndexNotFoundError) as exc_info:
            await attr.run(AttributeSearchInput(query="q", source_type="rtsp"))
        assert exc_info.value.index == ["mdx-behavior-*", "-behavior_index"]
        assert "mdx-behavior-*, -behavior_index" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_empty_query_raises_invalid_input(self, make_attr):
        attr, _es, _embed = make_attr()
        with pytest.raises(InvalidInputError, match="at least one non-empty attribute"):
            await attr.run(AttributeSearchInput(query="   ", source_type="video_file"))

    @pytest.mark.asyncio
    async def test_empty_attribute_list_raises_invalid_input(self, make_attr):
        attr, _es, _embed = make_attr()
        with pytest.raises(InvalidInputError):
            await attr.run(AttributeSearchInput(query=[], source_type="video_file"))

    @pytest.mark.asyncio
    async def test_timestamp_order_raises_invalid_input(self, make_attr):
        attr, _es, _embed = make_attr()
        with pytest.raises(InvalidInputError, match="must not be after"):
            await attr.run(
                AttributeSearchInput(
                    query="q",
                    source_type="video_file",
                    timestamp_start="2025-01-02T00:00:00Z",
                    timestamp_end="2025-01-01T00:00:00Z",
                )
            )

    @pytest.mark.asyncio
    async def test_malformed_hit_is_skipped(self, make_attr):
        bad = [{"_id": "bad", "_source": {"object": {}, "sensor": {}}}]  # missing _score
        attr, _es, _embed = make_attr(behavior_hits=bad)
        out = await attr.run(AttributeSearchInput(query="q", source_type="video_file"))
        assert out.results == []

    @pytest.mark.asyncio
    async def test_one_corrupt_hit_does_not_fail_whole_search(self, make_attr):
        bad = {"_id": "bad", "_source": {"object": {}, "sensor": {}}}  # missing _score
        attr, _es, _embed = make_attr(behavior_hits=[bad, _behavior_hit()])
        out = await attr.run(AttributeSearchInput(query="q", source_type="video_file"))
        assert len(out.results) == 1
        assert out.results[0].metadata.sensor_id == "cam1"

    @pytest.mark.asyncio
    async def test_dedup_after_skipped_hit_reads_aligned_source(self, make_attr):
        # H2: a corrupt hit (skipped) precedes two duplicate object hits with
        # different time spans. Because candidates stay aligned with results, the
        # widen reads the correct hit's _source and merges to the union span.
        corrupt = {"_id": "bad", "_source": {"object": {}, "sensor": {}}}  # missing _score
        narrow = {
            "_id": "h-narrow",
            "_score": 0.9,
            "_source": {
                "object": {"id": 42, "type": "Person"},
                "sensor": {"id": "cam1"},
                "timestamp": "2025-01-01T00:00:05Z",
                "end": "2025-01-01T00:00:06Z",
            },
        }
        wide = {
            "_id": "h-wide",
            "_score": 0.9,
            "_source": {
                "object": {"id": 42, "type": "Person"},
                "sensor": {"id": "cam1"},
                "timestamp": "2025-01-01T00:00:00Z",
                "end": "2025-01-01T00:00:10Z",
            },
        }
        attr, _es, _embed = make_attr(behavior_hits=[corrupt, narrow, wide])
        out = await attr.run(AttributeSearchInput(query="q", source_type="video_file", top_k=5))
        assert len(out.results) == 1
        assert out.results[0].metadata.start_time == "2025-01-01T00:00:00Z"
        assert out.results[0].metadata.end_time == "2025-01-01T00:00:10Z"

    @pytest.mark.asyncio
    async def test_idless_hits_are_not_merged(self, make_attr):
        # H3: two id-less hits both map to the ("unknown","unknown") key but must
        # stay distinct rather than collapse into one.
        idless1 = {"_id": "a", "_score": 0.9, "_source": {"object": {}, "sensor": {}, "timestamp": "t", "end": "u"}}
        idless2 = {"_id": "b", "_score": 0.8, "_source": {"object": {}, "sensor": {}, "timestamp": "t", "end": "u"}}
        attr, _es, _embed = make_attr(behavior_hits=[idless1, idless2])
        out = await attr.run(
            AttributeSearchInput(query="q", source_type="video_file", top_k=5, fuse_multi_attribute=False)
        )
        assert len(out.results) == 2

    @pytest.mark.asyncio
    async def test_append_global_ranking_before_top_k(self, make_attr):
        # #8: a lower-scoring first-attribute hit must not crowd out a
        # higher-scoring second-attribute hit when the top_k slice is applied.
        def _make_es_router():
            low = _behavior_hit(object_id=1, sensor_id="camLow", score=0.4)
            high = _behavior_hit(object_id=2, sensor_id="camHigh", score=0.95)
            queue = [[low], [high]]

            class _RouterEs:
                def __init__(self) -> None:
                    self.i = 0
                    self.calls: list[Any] = []

                async def search(self, *, index: Any, body: Any = None, **_kwargs: Any) -> Any:
                    self.calls.append(index)
                    if body and "knn" in body:
                        hits = queue[self.i] if self.i < len(queue) else []
                        self.i += 1
                        return {"hits": {"hits": hits}}
                    return {"hits": {"hits": []}}

                async def aclose(self) -> None:
                    return None

            return _RouterEs()

        es = _make_es_router()
        attr = AttributeSearch(
            es=es,
            embed=_MockEmbed(),
            behavior_index="behavior_index",
            behavior_index_wildcard="mdx-behavior-*",
            frames_index=None,
            enable_frame_lookup=False,
            default_max_results=10,
            vst_external_url="",
            vst_internal_url=None,
        )
        out = await attr.run(
            AttributeSearchInput(
                query=["dark", "bright"], source_type="video_file", top_k=1, fuse_multi_attribute=False
            )
        )
        assert len(out.results) == 1
        # The higher-scoring second-attribute hit wins the single slot.
        assert out.results[0].metadata.sensor_id == "camHigh"

    @pytest.mark.asyncio
    async def test_exclude_by_resolved_stream_id_and_timestamp_tolerance(self):
        # #9: exclude entry uses the resolved stream id spelling "+00:00" while the
        # result carries the raw sensor id and "Z"; timestamp tolerance + raw-id
        # matching must still exclude it.
        es = _MockEs([_behavior_hit(sensor_id="cam1")])
        attr = AttributeSearch(
            es=es,
            embed=_MockEmbed(),
            behavior_index="behavior_index",
            behavior_index_wildcard="mdx-behavior-*",
            frames_index=None,
            enable_frame_lookup=False,
            default_max_results=10,
            vst_external_url="",
            vst_internal_url=None,
        )
        out = await attr.run(
            AttributeSearchInput(
                query="q",
                source_type="video_file",
                exclude_videos=[
                    {
                        "sensor_id": "cam1",
                        "start_timestamp": "2025-01-01T00:00:00+00:00",
                        "end_timestamp": "2025-01-01T00:00:10+00:00",
                    }
                ],
            )
        )
        assert out.results == []


class TestFuseMode:
    @pytest.mark.asyncio
    async def test_fuse_continues_on_single_attribute_error(self):
        # M4: a non-systemic failure for one attribute must not sink the fuse request.
        class _SelectiveEmbed:
            def __init__(self, bad_query: str) -> None:
                self.bad_query = bad_query
                self.calls = 0

            async def get_text_embedding(self, text: str) -> list[float]:
                self.calls += 1
                if text == self.bad_query:
                    raise ValueError("embed failed for this attribute")
                return [0.1, 0.2, 0.3]

            async def aclose(self) -> None:
                return None

        es = _MockEs([_behavior_hit()])
        embed = _SelectiveEmbed(bad_query="red hat")
        attr = AttributeSearch(
            es=es,
            embed=embed,  # type: ignore[arg-type]
            behavior_index="behavior_index",
            behavior_index_wildcard="mdx-behavior-*",
            frames_index=None,
            enable_frame_lookup=False,
            default_max_results=10,
            vst_external_url="",
            vst_internal_url=None,
        )
        out = await attr.run(
            AttributeSearchInput(query=["person", "red hat"], source_type="video_file", fuse_multi_attribute=True)
        )
        assert embed.calls == 2
        assert len(out.results) == 1  # "person" survived; "red hat" was dropped

    @pytest.mark.asyncio
    async def test_fuse_propagates_systemic_error(self, make_attr):
        # M4: a missing wildcard affects every attribute: fail fast in fuse mode
        # too. (rtsp targets a wildcard list; an absent video_file anchor instead
        # yields a graceful empty, see test_missing_anchor_video_file_returns_empty.)
        attr, _es, _embed = make_attr(raise_not_found=True)
        with pytest.raises(IndexNotFoundError):
            await attr.run(
                AttributeSearchInput(query=["person", "red hat"], source_type="rtsp", fuse_multi_attribute=True)
            )

    @pytest.mark.asyncio
    async def test_fuse_resolves_screenshot_per_result(self, monkeypatch):
        # M5: results on different sensors each resolve their OWN stream id; fuse
        # must not relabel every result with the first result's stream id.
        from vss_core.search_core.primitives import _attribute_helpers as ah

        async def _stream_id(sensor_id: str, base_url: str) -> str:
            return f"{sensor_id}-stream"

        def _url(base: str, stream_id: str, ts: str) -> str:
            return f"{base}/{stream_id}/{ts}"

        monkeypatch.setattr(ah, "get_stream_id", _stream_id)
        monkeypatch.setattr(ah, "build_screenshot_url", _url)

        queue = [[_behavior_hit(object_id=1, sensor_id="cam1")], [_behavior_hit(object_id=2, sensor_id="cam2")]]

        class _RouterEs:
            def __init__(self) -> None:
                self.i = 0

            async def search(self, *, index: Any, body: Any = None, **_kwargs: Any) -> Any:
                if body and "knn" in body:
                    hits = queue[self.i] if self.i < len(queue) else []
                    self.i += 1
                    return {"hits": {"hits": hits}}
                return {"hits": {"hits": []}}

            async def aclose(self) -> None:
                return None

        attr = AttributeSearch(
            es=_RouterEs(),
            embed=_MockEmbed(),
            behavior_index="behavior_index",
            behavior_index_wildcard="mdx-behavior-*",
            frames_index=None,
            enable_frame_lookup=False,
            default_max_results=10,
            vst_external_url="http://vst",
            vst_internal_url=None,
        )
        out = await attr.run(
            AttributeSearchInput(query=["a", "b"], source_type="video_file", fuse_multi_attribute=True)
        )
        assert len(out.results) == 2
        stream_ids = {r.metadata.sensor_id for r in out.results}
        assert stream_ids == {"cam1-stream", "cam2-stream"}  # per-result, not relabeled to one
