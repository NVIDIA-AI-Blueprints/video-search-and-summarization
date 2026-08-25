# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Contract tests for the Search orchestrator (execute_core_search + Search primitive)."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

from pydantic import ValidationError
import pytest

from vss_core.search_core.agent_chunks import AgentMessageChunk
from vss_core.search_core.agent_chunks import AgentMessageChunkType
from vss_core.search_core.errors import BackendUnreachableError
from vss_core.search_core.errors import IndexNotFoundError
from vss_core.search_core.errors import InvalidInputError
from vss_core.search_core.errors import NoFinalResultError
from vss_core.search_core.events import ErrorEvent
from vss_core.search_core.events import FinalResultEvent
from vss_core.search_core.events import StatusEvent
from vss_core.search_core.models.attribute_search import AttributeSearchMetadata
from vss_core.search_core.models.attribute_search import AttributeSearchOutput
from vss_core.search_core.models.attribute_search import AttributeSearchResult
from vss_core.search_core.models.embed_search import EmbedSearchOutput
from vss_core.search_core.models.embed_search import EmbedSearchResultItem
from vss_core.search_core.models.search import SearchInput
from vss_core.search_core.primitives._search_helpers import execute_core_search_wrapper
from vss_core.search_core.primitives.search import Search
from vss_core.search_core.primitives.search import _coerce_attribute_payload
from vss_core.search_core.primitives.search import _coerce_embed_payload
from vss_core.vios import VSTError

# --------------------------------------------------------------------- fakes


class _FakeEmbed:
    """Returns a pre-canned EmbedSearchOutput per call (last one repeats)."""

    def __init__(self, outputs: list[EmbedSearchOutput]) -> None:
        self._outputs = outputs
        self.calls: list[Any] = []

    async def ainvoke(self, payload: Any) -> EmbedSearchOutput:
        idx = min(len(self.calls), len(self._outputs) - 1)
        self.calls.append(payload)
        return self._outputs[idx]


class _FakeAttr:
    """Returns a bare list of AttributeSearchResult (the shape the orchestrator wants)."""

    def __init__(self, results: list[AttributeSearchResult] | None = None, error: Exception | None = None) -> None:
        self._results = results or []
        self._error = error
        self.calls: list[Any] = []

    async def ainvoke(self, payload: Any) -> list[AttributeSearchResult]:
        self.calls.append(payload)
        if self._error is not None:
            raise self._error
        return list(self._results)


class _FakeBehaviorEs:
    endpoint = "http://es"

    async def search(self, *, index: Any, body: Any = None, **_kwargs: Any) -> Any:
        if body and "knn" in body:
            return {"hits": {"hits": [_behavior_hit()]}}
        return {"hits": {"hits": [{"_source": {"embeddings": {"vector": [0.1, 0.2, 0.3]}}}]}}

    async def aclose(self) -> None:
        return None


def _behavior_hit(object_id: str = "42", sensor_id: str = "cam1", score: float = 0.9) -> dict:
    return {
        "_id": f"h{object_id}",
        "_score": score,
        "_source": {
            "object": {"id": object_id, "type": "Person", "bbox": {}},
            "sensor": {"id": sensor_id},
            "timestamp": "2025-01-01T00:00:00Z",
            "end": "2025-01-01T00:00:10Z",
        },
    }


def _embed_item(
    *,
    video_name: str = "v1",
    sensor_id: str = "camA",
    similarity: float = 0.8,
    start: str = "2025-01-01T00:00:00Z",
    end: str = "2025-01-01T00:00:05Z",
) -> EmbedSearchResultItem:
    return EmbedSearchResultItem(
        video_name=video_name,
        description="desc",
        start_time=start,
        end_time=end,
        sensor_id=sensor_id,
        screenshot_url="",
        similarity_score=similarity,
    )


def _embed_output(items: list[EmbedSearchResultItem]) -> EmbedSearchOutput:
    return EmbedSearchOutput(results=items)


def _attr_result(
    *,
    object_id: str = "7",
    behavior_score: float = 0.7,
    sensor_id: str = "camX",
    start_time: str = "2025-01-01T00:00:00Z",
    end_time: str = "2025-01-01T00:00:05Z",
) -> AttributeSearchResult:
    return AttributeSearchResult(
        screenshot_url=None,
        metadata=AttributeSearchMetadata(
            sensor_id=sensor_id,
            object_id=object_id,
            object_type="person",
            behavior_score=behavior_score,
            start_time=start_time,
            end_time=end_time,
        ),
    )


def _config(**overrides: Any) -> SimpleNamespace:
    base: dict[str, Any] = {
        "attribute_search_tool": "attribute_search",
        "embed_confidence_threshold": 0.1,
        "default_max_results": 5,
        "fusion_method": "rrf",
        "w_attribute": 0.55,
        "w_embed": 0.35,
        "rrf_k": 60,
        "rrf_w": 0.5,
        "top_percent_filter": None,
        "vst_internal_url": "",
        "vst_external_url": "",
        "behavior_es_endpoint": "http://es",
        "behavior_index": "behavior_index",
        "behavior_index_wildcard": "mdx-behavior-*",
    }
    base.update(overrides)
    return SimpleNamespace(**base)


async def _run(inp: SearchInput, **kwargs: Any) -> Any:
    return await execute_core_search_wrapper(search_input=inp, **kwargs)


# --------------------------------------------------------------------- tests


class TestExecutionPaths:
    @pytest.mark.asyncio
    async def test_embed_only_path(self):
        embed = _FakeEmbed([_embed_output([_embed_item(video_name="v1", similarity=0.8)])])
        out = await _run(
            SearchInput(query="red forklift", source_type="video_file"),
            embed_search=embed,
            config=_config(),
        )
        assert len(out.data) == 1
        assert out.data[0].video_name == "v1"
        assert out.data[0].similarity == pytest.approx(0.8)
        assert len(embed.calls) == 1

    @pytest.mark.asyncio
    async def test_attribute_only_path(self):
        embed = _FakeEmbed([_embed_output([])])
        attr = _FakeAttr([_attr_result(object_id="42", sensor_id="camX")])
        out = await _run(
            SearchInput(
                query="person in white jacket",
                source_type="video_file",
                attributes=["white jacket"],
                search_mode="attribute",
            ),
            embed_search=embed,
            config=_config(),
            attribute_search_fn=attr,
        )
        assert len(out.data) == 1
        assert out.data[0].object_ids == ["42"]
        # embed search is not run on the attribute-only path.
        assert embed.calls == []
        assert len(attr.calls) == 1

    @pytest.mark.asyncio
    async def test_fusion_path_calls_attribute_per_video(self):
        embed = _FakeEmbed([_embed_output([_embed_item(video_name="v1", sensor_id="camA", similarity=0.8)])])
        attr = _FakeAttr([_attr_result(object_id="42", sensor_id="camA")])
        out = await _run(
            SearchInput(
                query="person climbing ladder",
                source_type="video_file",
                attributes=["white jacket"],
                search_mode="fusion",
            ),
            embed_search=embed,
            config=_config(),
            attribute_search_fn=attr,
        )
        assert len(embed.calls) == 1
        # fusion runs an attribute lookup per embed result.
        assert len(attr.calls) == 1
        assert len(out.data) == 1

    @pytest.mark.asyncio
    async def test_fusion_mode_is_authoritative(self):
        embed = _FakeEmbed([_embed_output([_embed_item(video_name="v1", sensor_id="camA", similarity=0.8)])])
        attr = _FakeAttr([_attr_result(object_id="42", sensor_id="camA")])
        out = await _run(
            SearchInput(
                query="person climbing ladder",
                source_type="video_file",
                attributes=["white jacket"],
                search_mode="fusion",
            ),
            embed_search=embed,
            config=_config(),
            attribute_search_fn=attr,
        )
        assert len(attr.calls) == 1
        assert len(out.data) == 1

    @pytest.mark.asyncio
    async def test_explicit_fusion_preserves_route_below_confidence_threshold(self):
        embed = _FakeEmbed([_embed_output([_embed_item(video_name="v1", sensor_id="camA", similarity=0.05)])])
        attr = _FakeAttr([_attr_result(object_id="42", sensor_id="camA")])
        out = await _run(
            SearchInput(
                query="q",
                source_type="video_file",
                attributes=["white jacket"],
                search_mode="fusion",
            ),
            embed_search=embed,
            config=_config(embed_confidence_threshold=0.1),
            attribute_search_fn=attr,
        )
        assert len(attr.calls) == 1
        assert out.data[0].object_ids == ["42"]

    @pytest.mark.asyncio
    async def test_fusion_without_embed_candidates_does_not_drop_action_query(self):
        embed = _FakeEmbed([_embed_output([])])
        attr = _FakeAttr([_attr_result(object_id="42", sensor_id="camX")])
        out = await _run(
            SearchInput(
                query="person climbing ladder",
                source_type="video_file",
                attributes=["white jacket"],
                search_mode="fusion",
            ),
            embed_search=embed,
            config=_config(),
            attribute_search_fn=attr,
        )
        assert len(embed.calls) == 1
        assert attr.calls == []
        assert out.data == []
        assert out.search_messages == [
            "Fusion search found no semantic candidates; attribute-only fallback was not used."
        ]

    @pytest.mark.asyncio
    async def test_object_id_path(self):
        embed = _FakeEmbed([_embed_output([])])
        out = await _run(
            SearchInput(query="similar to 42", source_type="video_file", search_mode="object", object_ids=[42]),
            embed_search=embed,
            config=_config(),
            behavior_es=_FakeBehaviorEs(),
        )
        assert len(out.data) == 1
        assert out.data[0].object_ids == ["42"]
        # embed search is skipped entirely on the object_id path.
        assert embed.calls == []

    @pytest.mark.asyncio
    async def test_object_id_path_propagates_systemic_search_error(self):
        # A systemic library error on the behavior kNN must propagate (not be
        # swallowed into an empty result), matching the attribute/fusion paths.
        class _RaisingBehaviorEs:
            endpoint = "http://es"

            async def search(self, *, index: Any, body: Any = None, **_kwargs: Any) -> Any:
                raise InvalidInputError("bad object query")

            async def aclose(self) -> None:
                return None

        embed = _FakeEmbed([_embed_output([])])
        with pytest.raises(InvalidInputError):
            await _run(
                SearchInput(query="similar to 42", source_type="video_file", search_mode="object", object_ids=[42]),
                embed_search=embed,
                config=_config(),
                behavior_es=_RaisingBehaviorEs(),
            )

    @pytest.mark.asyncio
    async def test_object_id_path_keeps_unknown_ids_distinct(self):
        class _UnknownBehaviorEs(_FakeBehaviorEs):
            async def search(self, *, index: Any, body: Any = None, **_kwargs: Any) -> Any:
                if body and "knn" in body:
                    return {
                        "hits": {
                            "hits": [
                                _behavior_hit("unknown", "cam1", 0.9),
                                _behavior_hit("unknown", "cam2", 0.8),
                            ]
                        }
                    }
                return await super().search(index=index, body=body, **_kwargs)

        out = await _run(
            SearchInput(query="similar to 42", source_type="video_file", search_mode="object", object_ids=[42]),
            embed_search=_FakeEmbed([_embed_output([])]),
            config=_config(),
            behavior_es=_UnknownBehaviorEs(),
        )
        assert len(out.data) == 2


class TestFusionErrorSemantics:
    @pytest.mark.asyncio
    async def test_fusion_soft_degrades_one_video(self):
        embed = _FakeEmbed(
            [
                _embed_output(
                    [
                        _embed_item(video_name="vA", sensor_id="camA", similarity=0.9),
                        _embed_item(video_name="vB", sensor_id="camB", similarity=0.8),
                    ]
                )
            ]
        )

        class _SelectiveAttr:
            def __init__(self) -> None:
                self.calls: list[Any] = []

            async def ainvoke(self, payload: Any) -> Any:
                self.calls.append(payload)
                if "vA" in (payload.get("video_sources") or []):
                    raise ValueError("attribute lookup boom")
                return []

        attr = _SelectiveAttr()
        out = await _run(
            SearchInput(
                query="q",
                source_type="video_file",
                attributes=["white jacket"],
                search_mode="fusion",
                top_k=5,
            ),
            embed_search=embed,
            config=_config(),
            attribute_search_fn=attr,
        )
        # The degraded video still appears (with its embed-only score).
        assert {r.video_name for r in out.data} == {"vA", "vB"}

    @pytest.mark.asyncio
    async def test_fusion_propagates_index_not_found(self):
        embed = _FakeEmbed([_embed_output([_embed_item(video_name="vA", sensor_id="camA", similarity=0.9)])])
        attr = _FakeAttr(error=IndexNotFoundError("behavior_index"))
        with pytest.raises(IndexNotFoundError):
            await _run(
                SearchInput(query="q", source_type="video_file", attributes=["white jacket"], search_mode="fusion"),
                embed_search=embed,
                config=_config(),
                attribute_search_fn=attr,
            )


class TestFinalCapping:
    @pytest.mark.asyncio
    async def test_final_top_k_caps_results(self):
        items = [_embed_item(video_name=f"v{i}", sensor_id=f"cam{i}", similarity=0.9 - i * 0.1) for i in range(3)]
        embed = _FakeEmbed([_embed_output(items)])
        out = await _run(
            SearchInput(query="q", source_type="video_file", top_k=1),
            embed_search=embed,
            config=_config(),
        )
        assert len(out.data) == 1


class TestInputValidation:
    def test_blank_query_rejected_semantically(self):
        with pytest.raises(InvalidInputError, match="non-empty"):
            SearchInput(query="   ").validate_semantics()

    def test_validate_semantics_timestamp_order(self):
        inp = SearchInput(
            query="q",
            source_type="video_file",
            timestamp_start="2025-01-02T00:00:00Z",
            timestamp_end="2025-01-01T00:00:00Z",
        )
        with pytest.raises(InvalidInputError, match="must not be after"):
            inp.validate_semantics()

    def test_top_k_below_one_rejected_at_construction(self):
        # top_k now carries Field(ge=1, le=1000), so a sub-1 value is rejected at
        # model construction (Pydantic) rather than reaching validate_semantics().
        with pytest.raises(ValidationError):
            SearchInput(query="q", source_type="video_file", top_k=0)

    def test_top_k_above_max_rejected_at_construction(self):
        with pytest.raises(ValidationError):
            SearchInput(query="q", source_type="video_file", top_k=1001)

    @pytest.mark.asyncio
    async def test_search_primitive_run_rejects_invalid_timestamp_order(self):
        # The Search primitive calls validate_semantics() before touching adapters.
        class _PrimEmbed:
            async def run(self, inp: Any) -> EmbedSearchOutput:
                raise AssertionError("must not be reached")

            async def aclose(self) -> None:
                return None

        class _PrimAttr:
            async def run(self, inp: Any) -> AttributeSearchOutput:
                raise AssertionError("must not be reached")

            async def aclose(self) -> None:
                return None

        class _PrimBehaviorEs:
            endpoint = "http://es"

            async def aclose(self) -> None:
                return None

        search = Search(
            embed=_PrimEmbed(),  # type: ignore[arg-type]
            attribute=_PrimAttr(),  # type: ignore[arg-type]
            behavior_es=_PrimBehaviorEs(),  # type: ignore[arg-type]
            behavior_index="behavior_index",
        )
        inp = SearchInput(
            query="q",
            source_type="video_file",
            timestamp_start="2025-01-02T00:00:00Z",
            timestamp_end="2025-01-01T00:00:00Z",
        )
        with pytest.raises(InvalidInputError):
            await search.run(inp)


# --------------------------------------------------------------- reject semantics


class TestTopKOverflow:
    @pytest.mark.asyncio
    async def test_embed_path_high_top_k_does_not_error_and_clamps_overfetch(self):
        # Merging is on, so the fetch is doubled for headroom and then clamped
        # to the downstream bound -- 750 -> 1500 -> 1000 -- which is what stops
        # the doubling from tripping the `le=1000` field constraint.
        embed = _FakeEmbed([_embed_output([_embed_item(video_name="v1", similarity=0.8)])])
        out = await _run(
            SearchInput(query="q", source_type="video_file", top_k=750),
            embed_search=embed,
            config=_config(),
        )
        assert len(out.data) == 1
        sent = json.loads(embed.calls[0])
        assert sent["params"]["top_k"] == "1000"

    @pytest.mark.asyncio
    async def test_merging_adjacent_windows_still_returns_top_k(self):
        """Asking for N results returns N even when every window is adjacent.

        Merging runs after retrieval, so fetching exactly ``top_k`` guaranteed
        a short result set: ten contiguous 5s windows collapse to five. Against
        a live deployment this made ``--top-k 10`` return 7.
        """
        # Five pairs, each pair adjacent and separated from the next by a gap,
        # so merging halves ten hits into exactly five results.
        items = []
        for pair in range(5):
            base = pair * 20
            for offset in (0, 5):
                start = base + offset
                items.append(
                    _embed_item(
                        video_name="v1",
                        similarity=0.9 - len(items) * 0.01,
                        start=f"2025-01-01T00:00:{start:02d}Z",
                        end=f"2025-01-01T00:00:{start + 5:02d}Z",
                    )
                )
        embed = _FakeEmbed([_embed_output(items)])
        out = await _run(
            SearchInput(query="q", source_type="video_file", top_k=5),
            embed_search=embed,
            config=_config(),
        )

        # Fetching exactly 5 would leave 3 results after merging; fetching 10
        # leaves 5. The doubled request is what makes the count survive.
        assert json.loads(embed.calls[0])["params"]["top_k"] == "10"
        assert len(out.data) == 5

    def test_coerce_embed_payload_maps_validation_error(self):
        with pytest.raises(InvalidInputError):
            _coerce_embed_payload({"query": "x", "source_type": "video_file", "top_k": 5000})

    def test_coerce_attribute_payload_maps_validation_error(self):
        with pytest.raises(InvalidInputError):
            _coerce_attribute_payload({"query": "x", "top_k": 5000})


class TestSingleWordAttributes:
    @pytest.mark.asyncio
    async def test_valid_single_word_attributes_are_not_pruned(self):
        embed = _FakeEmbed([_embed_output([_embed_item(video_name="v1", similarity=0.8)])])
        attr = _FakeAttr([_attr_result(object_id="42", sensor_id="camX")])
        out = await _run(
            SearchInput(
                query="q",
                source_type="video_file",
                attributes=["person", "red"],
                search_mode="fusion",
            ),
            embed_search=embed,
            config=_config(),
            attribute_search_fn=attr,
        )
        assert not any("single-word" in m for m in out.search_messages)
        assert attr.calls


# ------------------------------------------------------------- stream() contract


def _build_stream_search(embed_run: Any, **config_overrides: Any) -> Search:
    """Build a Search whose embed primitive delegates to ``embed_run(inp)``."""

    class _PrimEmbed:
        async def run(self, inp: Any) -> EmbedSearchOutput:
            return await embed_run(inp)

        async def aclose(self) -> None:
            return None

    class _PrimAttr:
        async def run(self, inp: Any) -> AttributeSearchOutput:
            raise AssertionError("attribute search must not be reached")

        async def aclose(self) -> None:
            return None

    class _PrimBehaviorEs:
        endpoint = "http://es"

        async def aclose(self) -> None:
            return None

    return Search(
        embed=_PrimEmbed(),  # type: ignore[arg-type]
        attribute=_PrimAttr(),  # type: ignore[arg-type]
        behavior_es=_PrimBehaviorEs(),  # type: ignore[arg-type]
        behavior_index="behavior_index",
        **config_overrides,
    )


class TestStreamContract:
    @pytest.mark.asyncio
    async def test_stream_success_yields_single_final_event(self):
        async def embed_run(_inp: Any) -> EmbedSearchOutput:
            return _embed_output([_embed_item(video_name="v1", similarity=0.8)])

        search = _build_stream_search(embed_run)
        events = [e async for e in search.stream(SearchInput(query="q", source_type="video_file"))]

        terminals = [e for e in events if isinstance(e, (FinalResultEvent, ErrorEvent))]
        assert len(terminals) == 1  # exactly one terminator
        assert isinstance(terminals[0], FinalResultEvent)
        assert terminals[0] is events[-1]  # terminator is last
        # Non-terminal chunks translate to StatusEvent(stage=chunk.type.value).
        status_events = [e for e in events if isinstance(e, StatusEvent)]
        assert status_events
        assert all(isinstance(e.stage, str) and e.stage for e in status_events)

    @pytest.mark.asyncio
    async def test_stream_search_error_yields_single_error_event(self):
        async def embed_run(_inp: Any) -> EmbedSearchOutput:
            raise IndexNotFoundError("behavior_index")

        search = _build_stream_search(embed_run)
        events = [e async for e in search.stream(SearchInput(query="q", source_type="video_file"))]

        terminals = [e for e in events if isinstance(e, (FinalResultEvent, ErrorEvent))]
        assert len(terminals) == 1
        assert isinstance(terminals[0], ErrorEvent)
        assert terminals[0].error_code == "IndexNotFoundError"  # precise code preserved

    @pytest.mark.asyncio
    async def test_stream_vst_error_uses_backend_error_code(self, monkeypatch):
        from vss_core.search_core.primitives import _search_helpers as sh

        async def vst_failure(**_kwargs: Any):
            if False:
                yield None
            raise VSTError("connection refused")

        async def embed_run(_inp: Any) -> EmbedSearchOutput:
            return _embed_output([])

        search = _build_stream_search(embed_run)
        monkeypatch.setattr(sh, "execute_core_search", vst_failure)

        events = [e async for e in search.stream(SearchInput(query="q", source_type="video_file"))]

        assert len(events) == 1
        assert isinstance(events[0], ErrorEvent)
        assert events[0].error_code == "VSTError"

    @pytest.mark.asyncio
    async def test_stream_unexpected_error_maps_to_unexpected_error_code(self):
        async def embed_run(_inp: Any) -> EmbedSearchOutput:
            raise RuntimeError("boom")

        search = _build_stream_search(embed_run)
        events = [e async for e in search.stream(SearchInput(query="q", source_type="video_file"))]

        terminals = [e for e in events if isinstance(e, (FinalResultEvent, ErrorEvent))]
        assert len(terminals) == 1
        assert isinstance(terminals[0], ErrorEvent)
        # A RuntimeError in embed is wrapped as BackendUnreachableError upstream.
        assert terminals[0].error_code == BackendUnreachableError.__name__

    @pytest.mark.asyncio
    async def test_stream_no_final_result_fallback(self, monkeypatch):
        # If the core generator ever exits without a SearchOutput, stream() must
        # still emit exactly one terminal event: a NoFinalResult ErrorEvent.
        from vss_core.search_core.primitives import _search_helpers as sh

        async def _only_status(**_kwargs: Any):
            yield AgentMessageChunk(type=AgentMessageChunkType.THOUGHT, content="partial only")

        async def embed_run(_inp: Any) -> EmbedSearchOutput:
            return _embed_output([])

        search = _build_stream_search(embed_run)
        monkeypatch.setattr(sh, "execute_core_search", _only_status)
        events = [e async for e in search.stream(SearchInput(query="q", source_type="video_file"))]

        terminals = [e for e in events if isinstance(e, (FinalResultEvent, ErrorEvent))]
        assert len(terminals) == 1
        assert isinstance(terminals[0], ErrorEvent)
        assert terminals[0].error_code == "NoFinalResult"

    @pytest.mark.asyncio
    async def test_non_streaming_no_final_result_raises(self, monkeypatch):
        from vss_core.search_core.primitives import _search_helpers as sh

        async def _only_status(**_kwargs: Any):
            yield AgentMessageChunk(type=AgentMessageChunkType.THOUGHT, content="partial only")

        monkeypatch.setattr(sh, "execute_core_search", _only_status)
        with pytest.raises(NoFinalResultError, match="without yielding SearchOutput"):
            await _run(
                SearchInput(query="q", source_type="video_file"),
                embed_search=_FakeEmbed([_embed_output([])]),
                config=_config(),
            )
