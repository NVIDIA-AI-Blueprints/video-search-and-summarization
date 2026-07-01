# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Contract tests for lib.search_core.primitives.AttributeSearch (mocked backends)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from elasticsearch import NotFoundError as ESNotFoundError
import pytest

from lib.search_core.errors import IndexNotFoundError
from lib.search_core.errors import InvalidInputError
from lib.search_core.models.attribute_search import AttributeSearchInput
from lib.search_core.primitives.attribute_search import AttributeSearch

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
    def _make(*, behavior_hits: list[dict] | None = None, raise_not_found: bool = False):
        es = _MockEs(behavior_hits, raise_not_found=raise_not_found)
        embed = _MockEmbed()
        attr = AttributeSearch(
            es=es,
            embed=embed,
            behavior_index="behavior_index",
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
        # A missing index affects every attribute — fail fast, don't return partial.
        attr, _es, _embed = make_attr(raise_not_found=True)
        with pytest.raises(IndexNotFoundError):
            await attr.run(
                AttributeSearchInput(query=["person", "red hat"], source_type="video_file", fuse_multi_attribute=False)
            )

    @pytest.mark.asyncio
    async def test_missing_index_raises_index_not_found(self, make_attr):
        attr, _es, _embed = make_attr(raise_not_found=True)
        with pytest.raises(IndexNotFoundError) as exc_info:
            await attr.run(AttributeSearchInput(query="q", source_type="video_file"))
        assert exc_info.value.index == "behavior_index"
        assert exc_info.value.backend == "elasticsearch"

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
