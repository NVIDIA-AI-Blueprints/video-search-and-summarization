# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Contract tests for the retrieval-only VSSSearch lifecycle."""

from __future__ import annotations

import asyncio

from vss_core.critic import CriticAgentOutput
from vss_core.critic import CriticAgentResult
from vss_core.critic import VideoResult
from vss_core.search_core import SearchRuntime
from vss_core.search_core import VSSSearch
from vss_core.search_core.events import FinalResultEvent
from vss_core.search_core.events import StatusEvent
from vss_core.search_core.models import SearchOutput
from vss_core.search_core.models import SearchResult


def _runtime() -> SearchRuntime:
    return SearchRuntime.from_kwargs(
        es_endpoint="http://es:9200",
        cosmos_embed_endpoint="http://embed:8017",
        rtvi_cv_endpoint="http://cv:9000",
        vst_internal_url="http://vst:30888",
        vst_external_url="http://vst:7777",
    )


class _RecordingPrimitive:
    def __init__(self) -> None:
        self.close_calls = 0

    async def aclose(self) -> None:
        self.close_calls += 1


def _result(*, sensor_id: str = "cam-1", start: str = "2025-01-01T00:00:10Z") -> SearchResult:
    return SearchResult(
        video_name="warehouse.mp4",
        description="candidate",
        start_time=start,
        end_time="2025-01-01T00:00:20Z",
        sensor_id=sensor_id,
        screenshot_url="https://vss.example/frame.jpg",
        similarity=0.9,
    )


class _SearchPrimitive(_RecordingPrimitive):
    def __init__(self, output: SearchOutput) -> None:
        super().__init__()
        self.output = output

    async def run(self, _inp) -> SearchOutput:
        return self.output

    async def stream(self, _inp):
        yield StatusEvent(stage="search", message="running")
        yield FinalResultEvent(output=self.output)


class _Critic:
    def __init__(self, result: CriticAgentResult | Exception) -> None:
        self.result = result
        self.queries: list[str] = []

    async def run(self, inp) -> CriticAgentOutput:
        self.queries.append(inp.query)
        if isinstance(self.result, Exception):
            raise self.result
        return CriticAgentOutput(
            video_results=[
                VideoResult(
                    video_info=video,
                    result=self.result,
                    criteria_met={"subject:forklift": self.result == CriticAgentResult.CONFIRMED},
                )
                for video in inp.videos
            ]
        )


def test_aclose_closes_primitives_and_is_idempotent() -> None:
    vss = VSSSearch.from_runtime(_runtime())
    primitive = _RecordingPrimitive()
    vss._search = primitive  # type: ignore[assignment]

    asyncio.run(vss.aclose())
    asyncio.run(vss.aclose())

    assert primitive.close_calls == 1
    assert vss._search is None


def test_search_without_critic_leaves_critic_result_null() -> None:
    vss = VSSSearch.from_runtime(_runtime())
    vss._search = _SearchPrimitive(SearchOutput(data=[_result()]))  # type: ignore[assignment]

    output = asyncio.run(vss.search(query="forklift"))

    assert output.data[0].critic_result is None


def test_search_uses_injected_critic_and_original_query() -> None:
    critic = _Critic(CriticAgentResult.CONFIRMED)
    vss = VSSSearch.from_runtime(_runtime(), critic=critic)  # type: ignore[arg-type]
    vss._search = _SearchPrimitive(SearchOutput(data=[_result()]))  # type: ignore[assignment]

    output = asyncio.run(vss.search(query="decomposed query", original_query="find a forklift"))

    assert critic.queries == ["find a forklift"]
    assert output.data[0].critic_result.result == "confirmed"
    assert output.data[0].critic_result.criteria_met == {"subject:forklift": True}


def test_search_preserves_fusion_attributes_in_critic_query() -> None:
    critic = _Critic(CriticAgentResult.CONFIRMED)
    vss = VSSSearch.from_runtime(_runtime(), critic=critic)  # type: ignore[arg-type]
    vss._search = _SearchPrimitive(SearchOutput(data=[_result()]))  # type: ignore[assignment]

    asyncio.run(
        vss.search(
            query="person climbing a ladder",
            search_mode="fusion",
            attributes=["white jacket"],
        )
    )

    assert critic.queries == ["person climbing a ladder; required visual attributes: white jacket"]


def test_search_critic_failure_is_fail_open() -> None:
    critic = _Critic(RuntimeError("critic failed"))
    vss = VSSSearch.from_runtime(_runtime(), critic=critic)  # type: ignore[arg-type]
    vss._search = _SearchPrimitive(SearchOutput(data=[_result()]))  # type: ignore[assignment]

    output = asyncio.run(vss.search(query="forklift"))

    assert output.data[0].critic_result is None
    assert output.search_messages == [
        "Visual verification failed; retrieval results carry no critic verdict (critic_result is null)."
    ]


def test_search_leaves_invalid_or_unidentifiable_hits_null() -> None:
    critic = _Critic(CriticAgentResult.CONFIRMED)
    vss = VSSSearch.from_runtime(_runtime(), critic=critic)  # type: ignore[arg-type]
    output = SearchOutput(
        data=[
            _result(sensor_id=""),
            _result(start="not-a-timestamp"),
        ]
    )
    vss._search = _SearchPrimitive(output)  # type: ignore[assignment]

    result = asyncio.run(vss.search(query="forklift"))

    assert critic.queries == []
    assert [item.critic_result for item in result.data] == [None, None]


def test_search_stream_verifies_only_terminal_output() -> None:
    critic = _Critic(CriticAgentResult.REJECTED)
    vss = VSSSearch.from_runtime(_runtime(), critic=critic)  # type: ignore[arg-type]
    vss._search = _SearchPrimitive(SearchOutput(data=[_result()]))  # type: ignore[assignment]

    async def collect():
        return [event async for event in vss.search_stream(query="forklift")]

    events = asyncio.run(collect())

    assert isinstance(events[0], StatusEvent)
    assert isinstance(events[1], FinalResultEvent)
    assert events[1].output.data[0].critic_result.result == "rejected"


def test_search_verifies_every_returned_hit() -> None:
    """The critic owns bounded concurrency, so the facade can preserve the
    all-results verification contract without launching every VLM call at once."""
    critic = _Critic(CriticAgentResult.CONFIRMED)
    vss = VSSSearch.from_runtime(_runtime(), critic=critic)  # type: ignore[arg-type]
    total = 13
    vss._search = _SearchPrimitive(  # type: ignore[assignment]
        SearchOutput(data=[_result(sensor_id=f"cam-{i}") for i in range(total)])
    )

    output = asyncio.run(vss.search(query="forklift"))

    assert [item.critic_result.result for item in output.data] == ["confirmed"] * total
    assert output.search_messages == []


def test_search_distinguishes_a_broken_verifier_from_no_verifier() -> None:
    """The critic degrades a failed candidate to `unverified` rather than
    raising, so without this message a deployment whose VLM answers /v1/models
    but fails every completion is indistinguishable from one with no VLM."""
    critic = _Critic(CriticAgentResult.UNVERIFIED)
    vss = VSSSearch.from_runtime(_runtime(), critic=critic)  # type: ignore[arg-type]
    vss._search = _SearchPrimitive(SearchOutput(data=[_result()]))  # type: ignore[assignment]

    output = asyncio.run(vss.search(query="forklift"))

    assert output.data[0].critic_result is not None
    assert output.data[0].critic_result.result == "unverified"
    assert any("produced no verdict for any hit" in message for message in output.search_messages)

    # No critic at all stays silent, so the two cases are tellable apart.
    quiet = VSSSearch.from_runtime(_runtime())
    quiet._search = _SearchPrimitive(SearchOutput(data=[_result()]))  # type: ignore[assignment]
    assert asyncio.run(quiet.search(query="forklift")).search_messages == []


def test_search_passes_source_type_so_only_file_bounds_are_rebased() -> None:
    """The critic may only re-anchor the synthetic file epoch; rebasing live
    bounds would verify a different clip."""
    captured: list[str | None] = []

    class _CapturingCritic(_Critic):
        async def run(self, inp):
            captured.extend(video.source_type for video in inp.videos)
            return await super().run(inp)

    critic = _CapturingCritic(CriticAgentResult.CONFIRMED)
    vss = VSSSearch.from_runtime(_runtime(), critic=critic)  # type: ignore[arg-type]
    vss._search = _SearchPrimitive(SearchOutput(data=[_result()]))  # type: ignore[assignment]

    asyncio.run(vss.search(query="forklift", source_type="rtsp"))

    assert captured == ["rtsp"]
