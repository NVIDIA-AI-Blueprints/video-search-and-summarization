# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

"""Real per-request batching and failure evidence, with no external services."""

from __future__ import annotations

import asyncio
import copy
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock

import httpx
import pytest
from openai import AsyncOpenAI

import vss.summarization.service as service
from vss.ingestion import ChunkInfo, VlmChunkResponse
from vss.observability import collect_vss_metrics
from vss.storage import FileStorageAdapter, StorageConfig
from vss.summarization import (
    AdapterChunkStore,
    FileChunkStore,
    GeneratedSummary,
    InMemoryChunkStore,
    OpenAISummaryGenerator,
    SummaryConfigurationError,
    SummaryGenerationError,
    SummaryGenerator,
    aggregate_events,
    summarize_from_store,
    summarize_video,
    summarize_video_sync,
)


def chunks(count: int = 5) -> list[VlmChunkResponse]:
    return [
        VlmChunkResponse(
            chunk=ChunkInfo(
                sourceId="147c40d6-98ff-4f7f-8845-9fa62a3a9e8c",
                chunkIdx=i,
                start_pts=i * 10_000_000_000,
                end_pts=(i + 1) * 10_000_000_000,
            ),
            vlm_response=f"Distinct event {i}",
        )
        for i in range(count)
    ]


class Generator(SummaryGenerator):
    def __init__(self, results: list[GeneratedSummary] | None = None):
        self.calls: list[list[dict[str, Any]]] = []
        self.options: list[dict[str, Any]] = []
        self.results = results
        self.closed = 0
        self.active = False

    async def generate(self, events, **options):
        assert not self.active
        self.active = True
        self.calls.append(copy.deepcopy(events))
        self.options.append(options)
        await asyncio.sleep(0)
        self.active = False
        if self.results is not None:
            return self.results[len(self.calls) - 1]
        return GeneratedSummary(
            text=f"Batch {len(self.calls)}",
            model="actual-model",
            prompt_tokens=11,
            completion_tokens=7,
        )

    async def aclose(self):
        self.closed += 1


@pytest.mark.parametrize(
    "size,counts",
    [(None, [5]), (1, [1] * 5), (2, [2, 2, 1]), (5, [5]), (1_000_000, [5])],
)
async def test_ordered_partitions_actual_calls_and_metrics(size, counts):
    source = list(reversed(chunks()))
    generator = Generator()
    with collect_vss_metrics() as metrics:
        result = await summarize_video(
            source, generator=generator, batch_size=size, prompt="Focus", model="alias"
        )
    body = json.loads(result.choices[0].message.content)
    assert [len(call) for call in generator.calls] == counts
    assert [event for call in generator.calls for event in call] == aggregate_events(
        source
    )
    assert body["events"] == aggregate_events(source)
    assert body["total_events"] == 5
    assert body["uuids"] == [source[0].chunk.sourceId]
    assert body["video_summary"] == "\n\n".join(
        f"Batch {i + 1}" for i in range(len(counts))
    )
    assert generator.options == [{"prompt": "Focus", "model": "alias"}] * len(counts)
    assert generator.closed == 0
    assert result.model == "actual-model"
    assert result.usage.summary_requests == len(counts)
    assert result.usage.aggregation_tokens == 18 * len(counts)
    assert result.usage.summary_tokens == 0
    assert metrics.complete
    llm = [e for e in metrics.observations if e.name == "summarization.llm"]
    assert len(llm) == len(counts) and len({e.span_id for e in llm}) == len(counts)
    for name in ("batches", "requests"):
        counters = [
            e for e in metrics.observations if e.name == f"summarization.{name}"
        ]
        assert sum(e.value for e in counters) == len(counts)
        if size is not None:
            assert {e.parent_id for e in counters} == {e.span_id for e in llm}
    for name in ("input_tokens", "output_tokens"):
        assert len(
            [e for e in metrics.observations if e.name == f"summarization.{name}"]
        ) == len(counts)


async def test_merge_and_filter_happen_before_partition():
    source = chunks(5)
    source[0].vlm_response = source[1].vlm_response = "Door opens"
    source[3].error = "failed"
    source[4].vlm_response = None
    generator = Generator()
    result = await summarize_video(source, generator=generator, batch_size=1)
    events = json.loads(result.choices[0].message.content)["events"]
    assert len(generator.calls) == 2
    assert [e["id"] for e in events] == [1, 2]
    assert events[0]["start_time"] == 0 and events[0]["end_time"] == 20
    assert [e["chunk_idx"] for e in events[0]["evidence"]] == [0, 1]


@pytest.mark.parametrize("filtered", [False, True])
async def test_empty_retains_one_aggregation_batch_no_provider(monkeypatch, filtered):
    factory = Mock(side_effect=AssertionError("must not construct provider"))
    monkeypatch.setattr(service, "get_generator", factory)
    source = chunks(1) if filtered else []
    if filtered:
        source[0].error = "failed"
    with collect_vss_metrics() as metrics:
        result = await summarize_video(source, batch_size=1)
    assert result.usage.summary_requests == 0
    assert json.loads(result.choices[0].message.content)["video_summary"] == ""
    assert [
        (e.value, e.availability)
        for e in metrics.observations
        if e.name == "summarization.llm"
    ] == [(None, "not_applicable")]
    assert [
        e.value for e in metrics.observations if e.name == "summarization.batches"
    ] == [1]
    factory.assert_not_called()


@pytest.mark.parametrize("size", [True, False, 0, -1, 1.5, "2", 1_000_001])
async def test_invalid_size_fails_before_work(monkeypatch, size):
    aggregate = Mock(side_effect=AssertionError("must not aggregate"))
    factory = Mock(side_effect=AssertionError("must not construct provider"))
    store = Mock()
    monkeypatch.setattr(service, "aggregate_events", aggregate)
    monkeypatch.setattr(service, "get_generator", factory)
    with pytest.raises(SummaryConfigurationError):
        await summarize_video(chunks(), batch_size=size)
    with pytest.raises(SummaryConfigurationError):
        summarize_video_sync(chunks(), batch_size=size)
    with pytest.raises(SummaryConfigurationError):
        await summarize_from_store("job", store=store, batch_size=size)
    aggregate.assert_not_called()
    factory.assert_not_called()
    store.get_chunks.assert_not_called()


def test_sync_forwards_batch_size():
    generator = Generator()
    result = summarize_video_sync(chunks(), generator=generator, batch_size=2)
    assert result.usage.summary_requests == 3
    assert len(generator.calls) == 3


@pytest.mark.parametrize("selection", ["memory", "file", "adapter", "config"])
async def test_stored_captions_forward_batching(tmp_path: Path, selection):
    generator = Generator()
    job_id = "147c40d6-98ff-4f7f-8845-9fa62a3a9e8c"
    if selection == "memory":
        store = InMemoryChunkStore()
        await store.store_chunks(job_id, chunks())
        result = await summarize_from_store(
            job_id, store=store, generator=generator, batch_size=2
        )
    elif selection == "file":
        async with FileChunkStore(root=tmp_path) as store:
            await store.store_chunks(job_id, chunks())
            result = await summarize_from_store(
                job_id, store=store, generator=generator, batch_size=2
            )
    else:
        async with FileStorageAdapter(root=tmp_path) as adapter:
            await AdapterChunkStore(adapter).store_chunks(job_id, chunks())
            if selection == "adapter":
                result = await summarize_from_store(
                    job_id, storage=adapter, generator=generator, batch_size=2
                )
            else:
                result = await summarize_from_store(
                    job_id,
                    storage_config=StorageConfig(backend="file", root=tmp_path),
                    generator=generator,
                    batch_size=2,
                )
    assert result.usage.summary_requests == 3
    assert [len(call) for call in generator.calls] == [2, 2, 1]


async def test_missing_and_measured_zero_usage_remain_distinct():
    generator = Generator(
        [
            GeneratedSummary(text="one", model="model", prompt_tokens=0),
            GeneratedSummary(text="two", model="model", completion_tokens=0),
            GeneratedSummary(
                text="three", model="model", prompt_tokens=9, completion_tokens=4
            ),
        ]
    )
    with collect_vss_metrics() as metrics:
        result = await summarize_video(chunks(3), generator=generator, batch_size=1)
    assert result.usage.aggregation_tokens == 13
    for name, expected in (
        ("input_tokens", [(0, "measured"), (None, "not_reported"), (9, "measured")]),
        ("output_tokens", [(None, "not_reported"), (0, "measured"), (4, "measured")]),
    ):
        assert [
            (e.value, e.availability)
            for e in metrics.observations
            if e.name == f"summarization.{name}"
        ] == expected


@pytest.mark.parametrize(
    "failure", [ValueError("provider failure"), asyncio.CancelledError()]
)
@pytest.mark.parametrize("owned", [False, True])
async def test_middle_batch_failure_stops_dispatch_and_preserves_primary(
    monkeypatch, failure, owned
):
    class Failing(Generator):
        async def generate(self, events, **options):
            value = await super().generate(events, **options)
            if len(self.calls) == 2:
                raise failure
            return value

        async def aclose(self):
            await super().aclose()
            raise RuntimeError("secondary close error")

    generator = Failing()
    factory = Mock(return_value=generator)
    monkeypatch.setattr(service, "get_generator", factory)
    with collect_vss_metrics() as metrics:
        with pytest.raises(type(failure)) as caught:
            await summarize_video(
                chunks(), generator=None if owned else generator, batch_size=1
            )
    assert caught.value is failure
    assert len(generator.calls) == 2 and generator.closed == int(owned)
    assert factory.call_count == int(owned)
    llm = [e for e in metrics.observations if e.name == "summarization.llm"]
    assert [e.status for e in llm] == [
        "ok",
        "cancelled" if isinstance(failure, asyncio.CancelledError) else "error",
    ]
    assert (
        len([e for e in metrics.observations if e.name == "summarization.requests"])
        == 2
    )


@pytest.mark.parametrize("cancel_on", [1, 3])
async def test_non_yielding_plugin_cancellation_is_observed(cancel_on):
    class Cancelling(Generator):
        async def generate(self, events, **options):
            self.calls.append(events)
            if len(self.calls) == cancel_on:
                asyncio.current_task().cancel()
            return GeneratedSummary(text="text", model="model")

    generator = Cancelling()
    task = asyncio.create_task(
        summarize_video(chunks(3), generator=generator, batch_size=1)
    )
    with pytest.raises(asyncio.CancelledError):
        await task
    assert len(generator.calls) == cancel_on


async def test_owned_generator_reused_and_closed_once(monkeypatch):
    generator = Generator()
    factory = Mock(return_value=generator)
    monkeypatch.setattr(service, "get_generator", factory)
    await summarize_video(chunks(), batch_size=2)
    assert factory.call_count == 1
    assert len(generator.calls) == 3 and generator.closed == 1


@pytest.mark.parametrize(
    "reasons,expected",
    [
        (["stop"] * 3, "stop"),
        (["stop", "length", "stop"], "length"),
        (["content_filter", "length", "stop"], "content_filter"),
    ],
)
async def test_finish_reason_precedence(reasons, expected):
    generator = Generator(
        [
            GeneratedSummary(text="part", model="model", finish_reason=reason)
            for reason in reasons
        ]
    )
    result = await summarize_video(chunks(3), generator=generator, batch_size=1)
    assert result.choices[0].finish_reason == expected


@pytest.mark.parametrize(
    "second",
    [
        GeneratedSummary(text="part", model="different"),
        GeneratedSummary(text="part", model="model", finish_reason="unknown"),
        "invalid",
    ],
)
async def test_invalid_batch_results_fail_closed_with_span_error(second):
    generator = Generator([GeneratedSummary(text="part", model="model"), second])
    with collect_vss_metrics() as metrics:
        with pytest.raises(SummaryGenerationError):
            await summarize_video(chunks(), generator=generator, batch_size=1)
    assert len(generator.calls) == 2
    assert [
        e.status for e in metrics.observations if e.name == "summarization.llm"
    ] == ["ok", "error"]


@pytest.mark.parametrize("over", [False, True])
async def test_escaped_message_limit_includes_envelope_and_separators(over):
    source = chunks(2)
    events = aggregate_events(source)
    base = service._batch_preflight(source, events, 1)
    # Quote, newline, control character, backslash and Unicode exercise JSON size.
    text = '"\n\x01\\é'
    escaped = len(json.dumps(text, ensure_ascii=False)) - 2
    remaining = 1_000_000 - base - escaped - 4 + int(over)
    generator = Generator(
        [
            GeneratedSummary(text=text, model="model"),
            GeneratedSummary(text="x" * remaining, model="model"),
        ]
    )
    if over:
        with pytest.raises(SummaryGenerationError):
            await summarize_video(source, generator=generator, batch_size=1)
    else:
        result = await summarize_video(source, generator=generator, batch_size=1)
        assert len(result.choices[0].message.content) == 1_000_000
        assert (
            json.loads(result.choices[0].message.content)["video_summary"]
            == text + "\n\n" + "x" * remaining
        )


async def test_oversized_envelope_fails_before_provider_creation(monkeypatch):
    source = chunks(1)
    source[0].vlm_response = "x" * 1_000_000
    factory = Mock(side_effect=AssertionError("must not construct provider"))
    monkeypatch.setattr(service, "get_generator", factory)
    with pytest.raises(SummaryGenerationError):
        await summarize_video(source, batch_size=1)
    factory.assert_not_called()


@pytest.mark.parametrize("over", [False, True])
async def test_combined_token_limit_stops_before_next_call(over):
    generator = Generator(
        [
            GeneratedSummary(
                text="part",
                model="model",
                prompt_tokens=500_000,
                completion_tokens=500_000 + int(over),
            ),
            GeneratedSummary(text="part", model="model"),
        ]
    )
    if over:
        with pytest.raises(SummaryGenerationError):
            await summarize_video(chunks(2), generator=generator, batch_size=1)
        assert len(generator.calls) == 1
    else:
        result = await summarize_video(chunks(2), generator=generator, batch_size=1)
        assert result.usage.aggregation_tokens == 1_000_000


async def test_plugin_mutation_cannot_change_response_events():
    class Mutating(Generator):
        async def generate(self, events, **options):
            result = await super().generate(events, **options)
            events[0]["description"] = "tampered"
            events[0]["evidence"].clear()
            events.clear()
            return result

    source = chunks(3)
    result = await summarize_video(source, generator=Mutating(), batch_size=1)
    assert json.loads(result.choices[0].message.content)["events"] == aggregate_events(
        source
    )


@pytest.mark.parametrize("limit", ["message", "model", "time"])
async def test_result_limits_stop_additional_calls(monkeypatch, limit):
    generator = Generator(
        [
            GeneratedSummary(
                text="x" * 1_000_000 if limit == "message" else "part",
                model="x" * 1025 if limit == "model" else "model",
            )
        ]
    )
    if limit == "time":
        clock = iter([0, 0, 0, 0, 0, 1001])
        monkeypatch.setattr(
            service, "time", SimpleNamespace(monotonic=lambda: next(clock))
        )
    with collect_vss_metrics() as metrics:
        with pytest.raises(SummaryGenerationError):
            await summarize_video(chunks(3), generator=generator, batch_size=1)
    assert len(generator.calls) == 1
    assert [
        e.status for e in metrics.observations if e.name == "summarization.llm"
    ] == ["error"]
    # Usage already consumed by the rejected result remains visible.
    assert (
        len([e for e in metrics.observations if e.name == "summarization.input_tokens"])
        == 1
    )


async def test_cleanup_failure_after_success_prevents_successful_response(monkeypatch):
    class CloseFailure(Generator):
        async def aclose(self):
            await super().aclose()
            raise RuntimeError("close failed")

    generator = CloseFailure()
    monkeypatch.setattr(service, "get_generator", lambda _: generator)
    with pytest.raises(RuntimeError, match="close failed"):
        await summarize_video(chunks(), batch_size=2)
    assert len(generator.calls) == 3 and generator.closed == 1


@pytest.mark.parametrize("expiry", ["aggregate", "between", "boundary"])
async def test_elapsed_guard_before_dispatch_and_exact_boundary(monkeypatch, expiry):
    now = [0.0]
    monkeypatch.setattr(
        service, "time", SimpleNamespace(monotonic=lambda: now[0], time=lambda: 1)
    )
    if expiry == "aggregate":
        original = aggregate_events

        def slow_aggregate(source):
            result = original(source)
            now[0] = 1000.001
            return result

        monkeypatch.setattr(service, "aggregate_events", slow_aggregate)

    class Timed(Generator):
        async def generate(self, events, **options):
            result = await super().generate(events, **options)
            now[0] = 1000.0
            if expiry == "between":
                asyncio.get_running_loop().call_soon(now.__setitem__, 0, 1000.001)
            return result

    generator = Timed()
    factory = Mock(return_value=generator)
    monkeypatch.setattr(service, "get_generator", factory)
    if expiry == "boundary":
        result = await summarize_video(chunks(2), batch_size=1)
        assert result.usage.query_processing_time == 1_000_000
        assert len(generator.calls) == 2
    else:
        with pytest.raises(SummaryGenerationError):
            await summarize_video(chunks(2), batch_size=1)
        assert len(generator.calls) == (0 if expiry == "aggregate" else 1)
        assert factory.call_count == (0 if expiry == "aggregate" else 1)


@pytest.mark.parametrize("api_type", ["chat_completions", "responses"])
@pytest.mark.parametrize("provider_reason", ["stop", "length", "content_filter"])
async def test_actual_sdk_sends_each_batch_and_records_usage_once(
    api_type, provider_reason
):
    captured = []

    async def respond(request):
        captured.append(json.loads(request.content))
        if api_type == "chat_completions":
            body = {
                "id": "chatcmpl-test",
                "created": 1,
                "object": "chat.completion",
                "model": "model",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "part"},
                        "finish_reason": provider_reason,
                    }
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 7},
            }
        else:
            body = {
                "id": "resp-test",
                "created_at": 1,
                "object": "response",
                "model": "model",
                "status": "completed" if provider_reason == "stop" else "incomplete",
                "incomplete_details": (
                    None
                    if provider_reason == "stop"
                    else {
                        "reason": "max_output_tokens"
                        if provider_reason == "length"
                        else provider_reason
                    }
                ),
                "output": [
                    {
                        "id": "msg-test",
                        "type": "message",
                        "role": "assistant",
                        "status": "completed",
                        "content": [
                            {"type": "output_text", "text": "part", "annotations": []}
                        ],
                    }
                ],
                "usage": {"input_tokens": 10, "output_tokens": 7},
            }
        return httpx.Response(200, json=body)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(respond), trust_env=False
    ) as transport:
        async with AsyncOpenAI(
            api_key="fixture",
            base_url="https://fixture.invalid/v1",
            http_client=transport,
            max_retries=0,
        ) as sdk:
            generator = OpenAISummaryGenerator(
                client=sdk, model="model", api_type=api_type, max_output_tokens=17
            )
            with collect_vss_metrics() as metrics:
                result = await summarize_video(
                    chunks(), generator=generator, batch_size=2
                )
            assert result.usage.summary_requests == 3
            assert result.usage.aggregation_tokens == 51
            # Existing Responses adapter normalizes native content_filter to stop.
            # Batching preserves the generator-exposed reason; it cannot recover
            # native evidence which an adapter did not expose.
            expected_reason = (
                "stop"
                if api_type == "responses" and provider_reason == "content_filter"
                else provider_reason
            )
            assert result.choices[0].finish_reason == expected_reason
            assert len(captured) == 3
            field = (
                "max_tokens" if api_type == "chat_completions" else "max_output_tokens"
            )
            assert [body[field] for body in captured] == [17] * 3
            for name, value in (("input_tokens", 10), ("output_tokens", 7)):
                assert [
                    e.value
                    for e in metrics.observations
                    if e.name == f"summarization.{name}"
                ] == [value] * 3
            assert not transport.is_closed
