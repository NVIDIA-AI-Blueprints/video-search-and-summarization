# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from types import SimpleNamespace
from unittest.mock import AsyncMock

from fastapi import HTTPException
import pytest

from vss_agents.agents.critic_agent import CriticAgentResult
from vss_agents.agents.critic_agent import VideoInfo
from vss_agents.agents.critic_agent import VideoResult
from vss_agents.agents.search_agent import SearchAgentConfig
from vss_agents.tools.search import DecomposedQuery
from vss_agents.tools.search import SearchConfig
from vss_agents.tools.search import SearchInput
from vss_agents.tools.search_adapter import NATSearchAdapter
from vss_agents.tools.search_adapter import build_search_runtime
from vss_core.search_core import ErrorEvent
from vss_core.search_core import FinalResultEvent
from vss_core.search_core import SearchRuntime
from vss_core.search_core.models.search import SearchOutput as CoreSearchOutput
from vss_core.search_core.models.search import SearchResult as CoreSearchResult


def _runtime() -> SearchRuntime:
    return SearchRuntime.from_kwargs(
        es_endpoint="http://es",
        cosmos_embed_endpoint="http://embed",
        rtvi_cv_endpoint="http://cv",
        vst_internal_url="http://vst",
        vst_external_url="http://vst-public",
    )


def _config(**overrides):
    values = {
        "search_max_iterations": 3,
        "use_attribute_search": True,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _result(index: int) -> CoreSearchResult:
    return CoreSearchResult(
        video_name=f"video-{index}",
        description=f"result {index}",
        start_time=f"2025-01-01T00:00:0{index}Z",
        end_time=f"2025-01-01T00:00:1{index}Z",
        sensor_id=f"sensor-{index}",
        screenshot_url=f"http://example/{index}.jpg",
        similarity=1.0 - index / 10,
    )


class _FakeSearch:
    def __init__(self, outputs: list[list[CoreSearchResult]]) -> None:
        self._outputs = outputs
        self.calls: list[dict] = []

    async def search_stream(self, **kwargs):
        self.calls.append(kwargs)
        output = self._outputs[min(len(self.calls) - 1, len(self._outputs) - 1)]
        yield FinalResultEvent(output=CoreSearchOutput(data=output))

    async def aclose(self) -> None:
        return None


class _FakeErrorSearch:
    def __init__(self, event: ErrorEvent) -> None:
        self._event = event

    async def search_stream(self, **kwargs):
        yield self._event

    async def aclose(self) -> None:
        return None


def _critic_result(result: CoreSearchResult, verdict: CriticAgentResult) -> VideoResult:
    return VideoResult(
        video_info=VideoInfo(
            sensor_id=result.sensor_id,
            start_timestamp=result.start_time,
            end_timestamp=result.end_time,
        ),
        result=verdict,
        criteria_met={"subject": verdict == CriticAgentResult.CONFIRMED},
    )


def test_build_search_runtime_requires_explicit_library_endpoints() -> None:
    assert build_search_runtime(SimpleNamespace(vst_internal_url="http://vst")) is None

    runtime = build_search_runtime(
        SimpleNamespace(
            es_endpoint="http://es",
            cosmos_embed_endpoint="http://embed",
            rtvi_cv_endpoint="http://cv",
            vst_internal_url="http://vst",
            vst_external_url=None,
        )
    )

    assert runtime is not None
    assert runtime.vst_external_url == "http://vst"


def test_custom_behavior_index_reaches_runtime_from_nat_configs() -> None:
    runtime_options = {
        "embed_search_tool": "embed_search",
        "vst_internal_url": "http://vst",
        "es_endpoint": "http://es",
        "cosmos_embed_endpoint": "http://embed",
        "rtvi_cv_endpoint": "http://cv",
        "behavior_index": "custom-behavior-index",
    }
    configs = [
        SearchConfig(agent_mode_llm="llm", **runtime_options),
        SearchAgentConfig(**runtime_options),
    ]

    for config in configs:
        runtime = build_search_runtime(config)

        assert config.model_dump()["behavior_index"] == "custom-behavior-index"
        assert runtime is not None
        assert runtime.behavior_index == "custom-behavior-index"


@pytest.mark.asyncio
async def test_no_rejections_use_one_library_call() -> None:
    result = _result(1)
    critic = AsyncMock()
    critic.ainvoke.return_value = SimpleNamespace(video_results=[_critic_result(result, CriticAgentResult.CONFIRMED)])
    adapter = NATSearchAdapter(_runtime(), _config(), agent_llm=None, critic_agent=critic)
    fake_search = _FakeSearch([[result]])
    adapter._search = fake_search

    output = await adapter.run(SearchInput(query="person walking", source_type="video_file", agent_mode=True))

    assert len(fake_search.calls) == 1
    assert output.data[0].critic_result is not None
    assert output.data[0].critic_result.result == "confirmed"


@pytest.mark.asyncio
async def test_rejection_expands_top_k_and_retrieves_replacement() -> None:
    rejected_result = _result(1)
    replacement = _result(2)
    critic = AsyncMock()
    critic.ainvoke.side_effect = [
        SimpleNamespace(video_results=[_critic_result(rejected_result, CriticAgentResult.REJECTED)]),
        SimpleNamespace(video_results=[_critic_result(replacement, CriticAgentResult.CONFIRMED)]),
    ]
    adapter = NATSearchAdapter(_runtime(), _config(), agent_llm=None, critic_agent=critic)
    fake_search = _FakeSearch([[rejected_result], [rejected_result, replacement]])
    adapter._search = fake_search

    output = await adapter.run(SearchInput(query="person walking", source_type="video_file", top_k=1, agent_mode=True))

    assert [call["top_k"] for call in fake_search.calls] == [1, 2]
    assert critic.ainvoke.await_count == 2
    assert len(output.data) == 1
    assert output.data[0].video_name == replacement.video_name
    assert output.data[0].critic_result is not None
    assert output.data[0].critic_result.result == "confirmed"


@pytest.mark.asyncio
async def test_rejected_results_returned_when_no_replacement_exists() -> None:
    rejected_result = _result(1)
    critic = AsyncMock()
    critic.ainvoke.return_value = SimpleNamespace(
        video_results=[_critic_result(rejected_result, CriticAgentResult.REJECTED)]
    )
    adapter = NATSearchAdapter(_runtime(), _config(), agent_llm=None, critic_agent=critic)
    fake_search = _FakeSearch([[rejected_result], [rejected_result]])
    adapter._search = fake_search

    output = await adapter.run(SearchInput(query="person walking", source_type="video_file", top_k=1, agent_mode=True))

    assert len(output.data) == 1
    assert output.data[0].video_name == rejected_result.video_name
    assert output.data[0].critic_result is not None
    assert output.data[0].critic_result.result == "rejected"


@pytest.mark.asyncio
async def test_repeated_results_stop_when_retrieval_makes_no_progress() -> None:
    result = _result(1)
    critic = AsyncMock()
    critic.ainvoke.return_value = SimpleNamespace(video_results=[_critic_result(result, CriticAgentResult.REJECTED)])
    adapter = NATSearchAdapter(_runtime(), _config(search_max_iterations=5), agent_llm=None, critic_agent=critic)
    fake_search = _FakeSearch([[result]])
    adapter._search = fake_search

    await adapter.run(SearchInput(query="person walking", source_type="video_file", agent_mode=True))

    assert len(fake_search.calls) == 2
    assert critic.ainvoke.await_count == 1


@pytest.mark.asyncio
async def test_overlapping_results_across_iterations_keep_best_ranked_segment() -> None:
    narrow = _result(1).model_copy(
        update={
            "video_name": "narrow",
            "sensor_id": "sensor-1",
            "start_time": "2025-01-01T00:00:00Z",
            "end_time": "2025-01-01T00:00:05Z",
            "similarity": 0.9,
        }
    )
    rejected = _result(2)
    wide = narrow.model_copy(
        update={
            "video_name": "wide",
            "end_time": "2025-01-01T00:00:10Z",
            "similarity": 0.95,
        }
    )
    replacement = _result(3).model_copy(update={"similarity": 0.7})
    critic = AsyncMock()
    critic.ainvoke.side_effect = [
        SimpleNamespace(
            video_results=[
                _critic_result(narrow, CriticAgentResult.CONFIRMED),
                _critic_result(rejected, CriticAgentResult.REJECTED),
            ]
        ),
        SimpleNamespace(
            video_results=[
                _critic_result(wide, CriticAgentResult.CONFIRMED),
                _critic_result(replacement, CriticAgentResult.CONFIRMED),
            ]
        ),
    ]
    adapter = NATSearchAdapter(_runtime(), _config(), agent_llm=None, critic_agent=critic)
    fake_search = _FakeSearch([[narrow, rejected], [wide, rejected, replacement]])
    adapter._search = fake_search

    output = await adapter.run(SearchInput(query="person walking", source_type="video_file", top_k=2, agent_mode=True))

    assert [result.video_name for result in output.data] == ["wide", replacement.video_name]


@pytest.mark.asyncio
async def test_retrieval_stops_at_search_max_iterations() -> None:
    results = [_result(index) for index in range(1, 4)]
    critic = AsyncMock()
    critic.ainvoke.side_effect = [
        SimpleNamespace(video_results=[_critic_result(result, CriticAgentResult.REJECTED)]) for result in results
    ]
    adapter = NATSearchAdapter(_runtime(), _config(search_max_iterations=3), agent_llm=None, critic_agent=critic)
    fake_search = _FakeSearch([[results[0]], results[:2], results])
    adapter._search = fake_search

    output = await adapter.run(SearchInput(query="person walking", source_type="video_file", top_k=1, agent_mode=True))

    assert [call["top_k"] for call in fake_search.calls] == [1, 2, 3]
    assert critic.ainvoke.await_count == 3
    assert len(output.data) == 1


@pytest.mark.asyncio
async def test_all_unverified_stops_and_preserves_annotation() -> None:
    result = _result(1)
    critic = AsyncMock()
    critic.ainvoke.return_value = SimpleNamespace(video_results=[_critic_result(result, CriticAgentResult.UNVERIFIED)])
    adapter = NATSearchAdapter(_runtime(), _config(), agent_llm=None, critic_agent=critic)
    fake_search = _FakeSearch([[result]])
    adapter._search = fake_search

    output = await adapter.run(SearchInput(query="person walking", source_type="video_file", agent_mode=True))

    assert len(fake_search.calls) == 1
    assert output.data[0].critic_result is not None
    assert output.data[0].critic_result.result == "unverified"
    assert output.search_messages == [
        "VLM verification unavailable. Returning search results without critic verification."
    ]


@pytest.mark.asyncio
async def test_critic_failure_is_non_fatal() -> None:
    result = _result(1)
    critic = AsyncMock()
    critic.ainvoke.side_effect = RuntimeError("vlm unavailable")
    adapter = NATSearchAdapter(_runtime(), _config(), agent_llm=None, critic_agent=critic)
    fake_search = _FakeSearch([[result]])
    adapter._search = fake_search

    output = await adapter.run(SearchInput(query="person walking", source_type="video_file", agent_mode=True))

    assert len(output.data) == 1
    assert output.search_messages == [
        "VLM verification unavailable (VLM may be down). Returning results without verification."
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error_code", "expected_status"),
    [
        ("InvalidInputError", 400),
        ("ValidationError", 400),
        ("IndexNotFoundError", 404),
        ("BackendUnreachableError", 502),
        ("VSTError", 502),
        ("ConfigurationError", 500),
        ("UnexpectedError", 500),
    ],
)
async def test_library_error_events_map_to_http_exceptions(error_code: str, expected_status: int) -> None:
    adapter = NATSearchAdapter(_runtime(), _config(enable_critic=False), agent_llm=None, critic_agent=None)
    adapter._search = _FakeErrorSearch(ErrorEvent(error_code=error_code, message="library failure"))

    with pytest.raises(HTTPException) as exc:
        await adapter.run(SearchInput(query="person walking", source_type="video_file", agent_mode=False))

    assert exc.value.status_code == expected_status


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("decomposed", "use_attribute_search", "expected_mode"),
    [
        (DecomposedQuery(query="object 5", object_ids=[5]), True, "object"),
        (DecomposedQuery(query="red shirt", attributes=["red shirt"], has_action=False), True, "attribute"),
        (DecomposedQuery(query="red shirt running", attributes=["red shirt"], has_action=True), True, "fusion"),
        (DecomposedQuery(query="red shirt running", attributes=["red shirt"], has_action=True), False, "embed"),
        (DecomposedQuery(query="traffic"), True, "embed"),
    ],
)
async def test_decomposition_routes_to_explicit_library_search_mode(
    monkeypatch,
    decomposed: DecomposedQuery,
    use_attribute_search: bool,
    expected_mode: str,
) -> None:
    async def _decompose(*args, **kwargs):
        return decomposed

    async def _streams(*args, **kwargs):
        return {}

    monkeypatch.setattr("vss_agents.tools.search_adapter.decompose_query", _decompose)
    monkeypatch.setattr("vss_agents.tools.search_adapter.get_streams_info", _streams)
    adapter = NATSearchAdapter(
        _runtime(),
        _config(enable_critic=False),
        agent_llm=object(),
        critic_agent=None,
    )
    fake_search = _FakeSearch([[]])
    adapter._search = fake_search

    await adapter.run(
        SearchInput(query="original query", source_type="video_file", agent_mode=True),
        use_attribute_search=use_attribute_search,
    )

    assert fake_search.calls[0]["search_mode"] == expected_mode


@pytest.mark.asyncio
async def test_explicit_video_source_is_resolved_when_decomposition_has_no_sources(monkeypatch) -> None:
    async def _decompose(*args, **kwargs):
        return DecomposedQuery(query="person walking")

    async def _streams(*args, **kwargs):
        return {
            "stream-id": {
                "name": "clip.mp4",
                "url": "file:///videos/clip.mp4",
            }
        }

    monkeypatch.setattr("vss_agents.tools.search_adapter.decompose_query", _decompose)
    monkeypatch.setattr("vss_agents.tools.search_adapter.get_streams_info", _streams)
    adapter = NATSearchAdapter(_runtime(), _config(), agent_llm=object(), critic_agent=None)
    fake_search = _FakeSearch([[]])
    adapter._search = fake_search

    await adapter.run(
        SearchInput(
            query="person walking",
            source_type="video_file",
            video_sources=["clip.mp4"],
            agent_mode=True,
        )
    )

    assert fake_search.calls[0]["video_sources"] == ["stream-id"]
