# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Contract tests for the Search orchestrator (execute_core_search + Search primitive)."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

from pydantic import ValidationError
import pytest

from lib.search_core.agent_chunks import AgentMessageChunk
from lib.search_core.agent_chunks import AgentMessageChunkType
from lib.search_core.errors import BackendUnreachableError
from lib.search_core.errors import ConfigurationError
from lib.search_core.errors import IndexNotFoundError
from lib.search_core.errors import InvalidInputError
from lib.search_core.events import ErrorEvent
from lib.search_core.events import FinalResultEvent
from lib.search_core.events import StatusEvent
from lib.search_core.models.attribute_search import AttributeSearchMetadata
from lib.search_core.models.attribute_search import AttributeSearchOutput
from lib.search_core.models.attribute_search import AttributeSearchResult
from lib.search_core.models.critic import CriticAgentOutput
from lib.search_core.models.critic import CriticAgentResult
from lib.search_core.models.critic import VideoResult
from lib.search_core.models.embed_search import EmbedSearchOutput
from lib.search_core.models.embed_search import EmbedSearchResultItem
from lib.search_core.models.search import SearchInput
from lib.search_core.primitives._search_helpers import execute_core_search_wrapper
from lib.search_core.primitives.search import Search
from lib.search_core.primitives.search import _coerce_attribute_payload
from lib.search_core.primitives.search import _coerce_embed_payload

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


class _FakeCritic:
    """Applies one verdict per call to every video handed in."""

    def __init__(self, verdicts: list[CriticAgentResult], error: Exception | None = None) -> None:
        self._verdicts = verdicts
        self._error = error
        self.calls = 0

    async def ainvoke(self, payload: Any) -> CriticAgentOutput:
        if self._error is not None:
            raise self._error
        verdict = self._verdicts[min(self.calls, len(self._verdicts) - 1)]
        self.calls += 1
        videos = payload["videos"]
        return CriticAgentOutput(
            video_results=[VideoResult(video_info=v, result=verdict, criteria_met={"ok": True}) for v in videos]
        )


class _PerVideoCritic:
    """Applies a per-sensor verdict; records the sensor set seen on each call.

    Lets a test assert both which videos were (re-)sent to the critic and the
    verdict each received — needed for the reject-replacement and merge-stability
    scenarios where a single blanket verdict is not expressive enough.
    """

    def __init__(self, verdict_by_sensor: dict[str, CriticAgentResult]) -> None:
        self._verdicts = verdict_by_sensor
        self.calls = 0
        self.seen_sensors: list[set[str]] = []

    async def ainvoke(self, payload: Any) -> CriticAgentOutput:
        self.calls += 1
        videos = payload["videos"]
        self.seen_sensors.append({v.sensor_id for v in videos})
        return CriticAgentOutput(
            video_results=[
                VideoResult(
                    video_info=v,
                    result=self._verdicts.get(v.sensor_id, CriticAgentResult.UNVERIFIED),
                    criteria_met={"ok": True},
                )
                for v in videos
            ]
        )


class _FakeBehaviorEs:
    """Minimal behavior-index ES for the object_id path (embedding fetch + kNN)."""

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
            "object": {"id": object_id, "type": "Person", "bbox": {"leftX": 1, "rightX": 2, "topY": 3, "bottomY": 4}},
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
    meta = AttributeSearchMetadata(
        sensor_id=sensor_id,
        object_id=object_id,
        object_type="person",
        behavior_score=behavior_score,
        start_time=start_time,
        end_time=end_time,
    )
    return AttributeSearchResult(screenshot_url=None, metadata=meta)


def _config(**overrides: Any) -> SimpleNamespace:
    base: dict[str, Any] = {
        "attribute_search_tool": "attribute_search",
        "critic_agent": None,
        "enable_critic": False,
        "use_attribute_search": False,
        "embed_confidence_threshold": 0.1,
        "search_max_iterations": 1,
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
            SearchInput(query="red forklift", source_type="video_file", agent_mode=False),
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
                has_action=False,
                agent_mode=False,
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
                has_action=True,
                agent_mode=False,
            ),
            embed_search=embed,
            config=_config(use_attribute_search=True),
            attribute_search_fn=attr,
        )
        assert len(embed.calls) == 1
        # fusion runs an attribute lookup per embed result.
        assert len(attr.calls) == 1
        assert len(out.data) == 1

    @pytest.mark.asyncio
    async def test_low_confidence_falls_back_to_attribute_only(self):
        # Embed score below threshold -> attribute-only fallback, not fusion.
        embed = _FakeEmbed([_embed_output([_embed_item(video_name="v1", similarity=0.05)])])
        attr = _FakeAttr([_attr_result(object_id="42", sensor_id="camX")])
        out = await _run(
            SearchInput(
                query="q",
                source_type="video_file",
                attributes=["white jacket"],
                has_action=True,
                agent_mode=False,
            ),
            embed_search=embed,
            config=_config(embed_confidence_threshold=0.1, use_attribute_search=True),
            attribute_search_fn=attr,
        )
        assert out.data[0].object_ids == ["42"]

    @pytest.mark.asyncio
    async def test_object_id_path(self):
        embed = _FakeEmbed([_embed_output([])])
        out = await _run(
            SearchInput(query="similar to 42", source_type="video_file", object_ids=[42], agent_mode=False),
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
                SearchInput(query="similar to 42", source_type="video_file", object_ids=[42], agent_mode=False),
                embed_search=embed,
                config=_config(),
                behavior_es=_RaisingBehaviorEs(),
            )


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
                has_action=True,
                agent_mode=False,
                top_k=5,
            ),
            embed_search=embed,
            config=_config(use_attribute_search=True),
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
                SearchInput(
                    query="q", source_type="video_file", attributes=["white jacket"], has_action=True, agent_mode=False
                ),
                embed_search=embed,
                config=_config(use_attribute_search=True),
                attribute_search_fn=attr,
            )


class TestCriticLoop:
    @pytest.mark.asyncio
    async def test_critic_confirms(self):
        embed = _FakeEmbed([_embed_output([_embed_item(video_name="v1", sensor_id="camA", similarity=0.8)])])
        critic = _FakeCritic([CriticAgentResult.CONFIRMED])
        out = await _run(
            SearchInput(query="q", source_type="video_file", agent_mode=False),
            embed_search=embed,
            config=_config(enable_critic=True, critic_agent="critic", search_max_iterations=2),
            critic_agent=critic,
        )
        assert critic.calls == 1
        assert embed.calls and len(embed.calls) == 1  # confirmed -> no re-search
        assert out.data[0].critic_result is not None
        assert out.data[0].critic_result.result == "confirmed"

    @pytest.mark.asyncio
    async def test_critic_reject_triggers_research(self):
        # Iteration 1 returns v1 (rejected); iteration 2 returns v2 (confirmed).
        embed = _FakeEmbed(
            [
                _embed_output([_embed_item(video_name="v1", sensor_id="camA", similarity=0.8)]),
                _embed_output([_embed_item(video_name="v2", sensor_id="camB", similarity=0.8)]),
            ]
        )
        critic = _FakeCritic([CriticAgentResult.REJECTED, CriticAgentResult.CONFIRMED])
        out = await _run(
            SearchInput(query="q", source_type="video_file", agent_mode=False),
            embed_search=embed,
            config=_config(enable_critic=True, critic_agent="critic", search_max_iterations=2),
            critic_agent=critic,
        )
        assert len(embed.calls) == 2  # rejection forced a re-search
        assert critic.calls == 2
        assert out.data[0].video_name == "v2"
        assert out.data[0].critic_result.result == "confirmed"

    @pytest.mark.asyncio
    async def test_critic_soft_fails_on_backend_error(self):
        embed = _FakeEmbed([_embed_output([_embed_item(video_name="v1", sensor_id="camA", similarity=0.8)])])
        critic = _FakeCritic([], error=RuntimeError("vlm down"))
        out = await _run(
            SearchInput(query="q", source_type="video_file", agent_mode=False),
            embed_search=embed,
            config=_config(enable_critic=True, critic_agent="critic", search_max_iterations=1),
            critic_agent=critic,
        )
        # Critic failure is best-effort: results still returned, with a note.
        assert len(out.data) == 1
        assert any("Critic verification unavailable" in m for m in out.search_messages)


class TestFinalCapping:
    @pytest.mark.asyncio
    async def test_final_top_k_caps_results(self):
        items = [_embed_item(video_name=f"v{i}", sensor_id=f"cam{i}", similarity=0.9 - i * 0.1) for i in range(3)]
        embed = _FakeEmbed([_embed_output(items)])
        out = await _run(
            SearchInput(query="q", source_type="video_file", agent_mode=False, top_k=1),
            embed_search=embed,
            config=_config(),
        )
        assert len(out.data) == 1


class TestInputValidation:
    def test_validate_semantics_timestamp_order(self):
        inp = SearchInput(
            query="q",
            source_type="video_file",
            agent_mode=False,
            timestamp_start="2025-01-02T00:00:00Z",
            timestamp_end="2025-01-01T00:00:00Z",
        )
        with pytest.raises(InvalidInputError, match="must not be after"):
            inp.validate_semantics()

    def test_top_k_below_one_rejected_at_construction(self):
        # top_k now carries Field(ge=1, le=1000), so a sub-1 value is rejected at
        # model construction (Pydantic) rather than reaching validate_semantics().
        with pytest.raises(ValidationError):
            SearchInput(query="q", source_type="video_file", agent_mode=False, top_k=0)

    def test_top_k_above_max_rejected_at_construction(self):
        with pytest.raises(ValidationError):
            SearchInput(query="q", source_type="video_file", agent_mode=False, top_k=1001)

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
            critic=None,
            behavior_es=_PrimBehaviorEs(),  # type: ignore[arg-type]
            behavior_index="behavior_index",
        )
        inp = SearchInput(
            query="q",
            source_type="video_file",
            agent_mode=False,
            timestamp_start="2025-01-02T00:00:00Z",
            timestamp_end="2025-01-01T00:00:00Z",
        )
        with pytest.raises(InvalidInputError):
            await search.run(inp)


# --------------------------------------------------------------- reject semantics


class TestCriticRejectReplacement:
    """TRUE reject-replacement semantics (#1): a REJECTED result is dropped from
    the output, its slot goes to a lower-ranked replacement, and rejected clips
    are excluded on re-search."""

    @pytest.mark.asyncio
    async def test_rejected_removed_and_replacement_promoted_into_cap(self):
        # STABLE embed fake: same three-result set every call. top_k=2 means the
        # final cap keeps 2. If the rejected top hit were kept it would occupy a
        # slot and push v3 out; TRUE replacement drops it so v3 surfaces.
        embed = _FakeEmbed(
            [
                _embed_output(
                    [
                        _embed_item(video_name="v1", sensor_id="camA", similarity=0.9),
                        _embed_item(video_name="v2", sensor_id="camB", similarity=0.8),
                        _embed_item(video_name="v3", sensor_id="camC", similarity=0.7),
                    ]
                )
            ]
        )
        critic = _PerVideoCritic(
            {
                "camA": CriticAgentResult.REJECTED,
                "camB": CriticAgentResult.CONFIRMED,
                "camC": CriticAgentResult.CONFIRMED,
            }
        )
        out = await _run(
            SearchInput(query="q", source_type="video_file", agent_mode=False, top_k=2),
            embed_search=embed,
            config=_config(enable_critic=True, critic_agent="critic", search_max_iterations=2),
            critic_agent=critic,
        )
        names = {r.video_name for r in out.data}
        assert "v1" not in names  # rejected result removed
        assert names == {"v2", "v3"}  # replacement (v3) promoted into the top-2
        assert len(embed.calls) == 2  # rejection forced a bounded re-search

        # The rejected clip is threaded into the re-search as an exclusion.
        second_payload = json.loads(embed.calls[1])
        excluded_sensors = {ev["sensor_id"] for ev in second_payload["exclude_videos"]}
        assert "camA" in excluded_sensors

    @pytest.mark.asyncio
    async def test_default_single_iteration_still_removes_rejected(self):
        # With the default search_max_iterations=1 there is no re-search, but the
        # rejected result must STILL be dropped from the output.
        embed = _FakeEmbed(
            [
                _embed_output(
                    [
                        _embed_item(video_name="v1", sensor_id="camA", similarity=0.9),
                        _embed_item(video_name="v2", sensor_id="camB", similarity=0.8),
                    ]
                )
            ]
        )
        critic = _PerVideoCritic({"camA": CriticAgentResult.REJECTED, "camB": CriticAgentResult.CONFIRMED})
        out = await _run(
            SearchInput(query="q", source_type="video_file", agent_mode=False),
            embed_search=embed,
            config=_config(enable_critic=True, critic_agent="critic", search_max_iterations=1),
            critic_agent=critic,
        )
        assert {r.video_name for r in out.data} == {"v2"}
        assert len(embed.calls) == 1  # no re-search at the default iteration cap


class TestCriticLoopBounds:
    @pytest.mark.asyncio
    async def test_loop_bounded_by_search_max_iterations(self):
        # A fresh candidate each call + always-reject would loop forever if not
        # bounded; embed must be called exactly search_max_iterations times.
        embed = _FakeEmbed(
            [
                _embed_output([_embed_item(video_name="v1", sensor_id="cam1", similarity=0.9)]),
                _embed_output([_embed_item(video_name="v2", sensor_id="cam2", similarity=0.9)]),
                _embed_output([_embed_item(video_name="v3", sensor_id="cam3", similarity=0.9)]),
                _embed_output([_embed_item(video_name="v4", sensor_id="cam4", similarity=0.9)]),
            ]
        )
        critic = _FakeCritic([CriticAgentResult.REJECTED])
        out = await _run(
            SearchInput(query="q", source_type="video_file", agent_mode=False),
            embed_search=embed,
            config=_config(enable_critic=True, critic_agent="critic", search_max_iterations=3),
            critic_agent=critic,
        )
        assert len(embed.calls) == 3  # bounded — never a 4th iteration
        assert out.data == []  # every candidate was rejected and removed

    @pytest.mark.asyncio
    async def test_all_unverified_stops_research(self):
        embed = _FakeEmbed([_embed_output([_embed_item(video_name="v1", sensor_id="camA", similarity=0.8)])])
        critic = _FakeCritic([CriticAgentResult.UNVERIFIED])
        out = await _run(
            SearchInput(query="q", source_type="video_file", agent_mode=False),
            embed_search=embed,
            config=_config(enable_critic=True, critic_agent="critic", search_max_iterations=3),
            critic_agent=critic,
        )
        # An all-UNVERIFIED verdict set halts re-search after the first pass.
        assert len(embed.calls) == 1
        assert {r.video_name for r in out.data} == {"v1"}
        assert any("VLM verification unavailable" in m for m in out.search_messages)

    @pytest.mark.asyncio
    async def test_verdict_key_stable_across_merge(self):
        # #6: a video CONFIRMED in iteration 1 (bounds A) must not be re-sent to
        # the critic in iteration 2 after merge_consecutive_results extends its
        # end_time (bounds B). Keying verdicts by (sensor_id, start_time) keeps it
        # recognised as already-confirmed.
        embed = _FakeEmbed(
            [
                _embed_output(
                    [
                        _embed_item(
                            video_name="vA",
                            sensor_id="camA",
                            similarity=0.9,
                            start="2025-01-01T00:00:00Z",
                            end="2025-01-01T00:00:05Z",
                        ),
                        _embed_item(video_name="vB", sensor_id="camB", similarity=0.8),
                    ]
                ),
                _embed_output(
                    [
                        _embed_item(
                            video_name="vA",
                            sensor_id="camA",
                            similarity=0.9,
                            start="2025-01-01T00:00:00Z",
                            end="2025-01-01T00:00:05Z",
                        ),
                        _embed_item(
                            video_name="vA",
                            sensor_id="camA",
                            similarity=0.88,
                            start="2025-01-01T00:00:04Z",
                            end="2025-01-01T00:00:10Z",
                        ),
                    ]
                ),
            ]
        )
        critic = _PerVideoCritic({"camA": CriticAgentResult.CONFIRMED, "camB": CriticAgentResult.REJECTED})
        out = await _run(
            SearchInput(query="q", source_type="video_file", agent_mode=False),
            embed_search=embed,
            config=_config(enable_critic=True, critic_agent="critic", search_max_iterations=2),
            critic_agent=critic,
        )
        # Iteration 2's embed set merges camA into 00:00->00:10 (end drifted), but
        # the critic is invoked only once — the merged result is recognised as the
        # already-confirmed camA and NOT re-verified.
        assert critic.calls == 1
        cam_a_results = [r for r in out.data if r.sensor_id == "camA"]
        assert len(cam_a_results) == 1
        assert cam_a_results[0].end_time == "2025-01-01T00:00:10Z"  # merged bounds
        assert cam_a_results[0].critic_result is not None
        assert cam_a_results[0].critic_result.result == "confirmed"


class TestTopKOverflow:
    @pytest.mark.asyncio
    async def test_embed_path_high_top_k_does_not_error_and_clamps_overfetch(self):
        # A user top_k in [501, 1000] doubles past the downstream le=1000 bound;
        # the overfetch must be clamped to 1000 so no ValidationError is raised.
        embed = _FakeEmbed([_embed_output([_embed_item(video_name="v1", similarity=0.8)])])
        out = await _run(
            SearchInput(query="q", source_type="video_file", agent_mode=False, top_k=750),
            embed_search=embed,
            config=_config(),
        )
        assert len(out.data) == 1
        sent = json.loads(embed.calls[0])
        assert sent["params"]["top_k"] == "1000"  # 750*2 clamped to 1000

    def test_coerce_embed_payload_maps_validation_error(self):
        with pytest.raises(InvalidInputError):
            _coerce_embed_payload({"query": "x", "source_type": "video_file", "top_k": 5000})

    def test_coerce_attribute_payload_maps_validation_error(self):
        with pytest.raises(InvalidInputError):
            _coerce_attribute_payload({"query": "x", "top_k": 5000})


class TestConfigurationErrors:
    @pytest.mark.asyncio
    async def test_missing_behavior_es_endpoint_raises_configuration_error(self):
        embed = _FakeEmbed([_embed_output([])])
        with pytest.raises(ConfigurationError):
            await _run(
                SearchInput(query="q", source_type="video_file", object_ids=[42], agent_mode=False),
                embed_search=embed,
                config=_config(behavior_es_endpoint=None),
                behavior_es=None,
            )

    @pytest.mark.asyncio
    async def test_missing_attribute_search_fn_raises_configuration_error(self):
        embed = _FakeEmbed([_embed_output([])])
        with pytest.raises(ConfigurationError):
            await _run(
                SearchInput(
                    query="q",
                    source_type="video_file",
                    attributes=["white jacket"],
                    has_action=False,
                    agent_mode=False,
                ),
                embed_search=embed,
                config=_config(),
                attribute_search_fn=None,
            )


class TestSingleWordPruning:
    @pytest.mark.asyncio
    async def test_pruning_all_attributes_surfaces_message(self):
        # Every attribute is single-word -> pruning empties the list and silently
        # flips routing to embed-only. A search_message must make that visible.
        embed = _FakeEmbed([_embed_output([_embed_item(video_name="v1", similarity=0.8)])])
        attr = _FakeAttr([_attr_result(object_id="42", sensor_id="camX")])
        out = await _run(
            SearchInput(
                query="q",
                source_type="video_file",
                attributes=["person", "red"],
                has_action=True,
                agent_mode=False,
            ),
            embed_search=embed,
            config=_config(use_attribute_search=True),
            attribute_search_fn=attr,
        )
        assert any("single-word" in m for m in out.search_messages)
        # Routing flipped to embed-only: no per-video attribute lookups happened.
        assert attr.calls == []
        assert {r.video_name for r in out.data} == {"v1"}


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
        critic=None,
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
        events = [e async for e in search.stream(SearchInput(query="q", source_type="video_file", agent_mode=False))]

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
        events = [e async for e in search.stream(SearchInput(query="q", source_type="video_file", agent_mode=False))]

        terminals = [e for e in events if isinstance(e, (FinalResultEvent, ErrorEvent))]
        assert len(terminals) == 1
        assert isinstance(terminals[0], ErrorEvent)
        assert terminals[0].error_code == "IndexNotFoundError"  # precise code preserved

    @pytest.mark.asyncio
    async def test_stream_unexpected_error_maps_to_unexpected_error_code(self):
        async def embed_run(_inp: Any) -> EmbedSearchOutput:
            raise RuntimeError("boom")

        search = _build_stream_search(embed_run)
        events = [e async for e in search.stream(SearchInput(query="q", source_type="video_file", agent_mode=False))]

        terminals = [e for e in events if isinstance(e, (FinalResultEvent, ErrorEvent))]
        assert len(terminals) == 1
        assert isinstance(terminals[0], ErrorEvent)
        # A RuntimeError in embed is wrapped as BackendUnreachableError upstream.
        assert terminals[0].error_code == BackendUnreachableError.__name__

    @pytest.mark.asyncio
    async def test_stream_no_final_result_fallback(self, monkeypatch):
        # If the core generator ever exits without a SearchOutput, stream() must
        # still emit exactly one terminal event: a NoFinalResult ErrorEvent.
        from lib.search_core.primitives import _search_helpers as sh

        async def _only_status(**_kwargs: Any):
            yield AgentMessageChunk(type=AgentMessageChunkType.THOUGHT, content="partial only")

        async def embed_run(_inp: Any) -> EmbedSearchOutput:
            return _embed_output([])

        search = _build_stream_search(embed_run)
        monkeypatch.setattr(sh, "execute_core_search", _only_status)
        events = [e async for e in search.stream(SearchInput(query="q", source_type="video_file", agent_mode=False))]

        terminals = [e for e in events if isinstance(e, (FinalResultEvent, ErrorEvent))]
        assert len(terminals) == 1
        assert isinstance(terminals[0], ErrorEvent)
        assert terminals[0].error_code == "NoFinalResult"
