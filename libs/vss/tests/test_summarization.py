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

from __future__ import annotations

import json
from uuid import UUID

import pytest
from pydantic import ValidationError

import vss.summarization.service as summarization_service
from vss.ingestion import ChunkInfo, VlmChunkResponse
from vss.summarization import (
    CompletionFinishReason,
    CompletionResponse,
    GeneratedSummary,
    InMemoryChunkStore,
    SummaryGenerator,
    aggregate_events,
    summarize_from_store,
    summarize_video,
    summarize_video_sync,
)


class DeterministicGenerator(SummaryGenerator):
    async def generate(
        self,
        events: list[dict[str, object]],
        *,
        prompt: str | None = None,
        model: str | None = None,
    ) -> GeneratedSummary:
        return GeneratedSummary(
            text=f"Summarized {len(events)} events.",
            model=model or "test-model",
            prompt_tokens=11,
            completion_tokens=7,
        )


def _response(
    index: int,
    caption: str | None,
    *,
    start: int | None = None,
    error: str | None = None,
) -> VlmChunkResponse:
    response = VlmChunkResponse()
    start_seconds = index * 10 if start is None else start
    response.chunk = ChunkInfo(
        sourceId="147c40d6-98ff-4f7f-8845-9fa62a3a9e8c",
        chunkIdx=index,
        start_pts=start_seconds * 1_000_000_000,
        end_pts=(start_seconds + 10) * 1_000_000_000,
    )
    response.vlm_response = caption
    response.error = error
    return response


def test_completion_contract_round_trip() -> None:
    result = summarize_video_sync(
        [_response(0, "A car enters.")],
        generator=DeterministicGenerator(),
    )

    restored = CompletionResponse.model_validate_json(result.model_dump_json())

    assert restored == result
    assert restored.choices[0].finish_reason is CompletionFinishReason.STOP
    assert restored.usage is not None
    assert restored.usage.summary_tokens == 0
    assert restored.usage.aggregation_tokens == 18
    assert restored.video_id == UUID("147c40d6-98ff-4f7f-8845-9fa62a3a9e8c")
    assert restored.media_info.type == "offset"
    assert restored.schema_version == "1.0"
    assert restored.choices[0].schema_version == "1.0"
    assert restored.choices[0].message.schema_version == "1.0"
    assert restored.usage.schema_version == "1.0"
    assert restored.media_info.schema_version == "1.0"


def test_generated_summary_is_versioned_and_validated() -> None:
    summary = GeneratedSummary(text="Summary.", model="test-model")

    assert summary.schema_version == "1.0"
    assert GeneratedSummary.model_validate_json(summary.model_dump_json()) == summary
    with pytest.raises(ValidationError):
        GeneratedSummary(text="Summary.", model="", completion_tokens=-1)


def test_aggregate_events_orders_filters_parses_and_merges() -> None:
    chunks = [
        _response(2, None),
        _response(1, '{"events":[{"description":"Door opens"}]}'),
        _response(3, "ignored", error="backend failed"),
        _response(0, '```json\n[{"caption":"Door opens"}]\n```'),
    ]

    events = aggregate_events(chunks)

    assert len(events) == 1
    assert events[0]["id"] == 1
    assert events[0]["description"] == "Door opens"
    assert events[0]["start_time"] == 0
    assert events[0]["end_time"] == 20
    assert events[0]["evidence"] == [
        {
            "chunk_idx": 0,
            "source_id": "147c40d6-98ff-4f7f-8845-9fa62a3a9e8c",
        },
        {
            "chunk_idx": 1,
            "source_id": "147c40d6-98ff-4f7f-8845-9fa62a3a9e8c",
        },
    ]


def test_aggregate_events_matches_prior_rtvi_event_semantics() -> None:
    chunks = [
        _response(
            0,
            (
                'RTVI result: {"events":[{"start_time":0,"end_time":0,'
                '"type":"motion","description":"A person enters"}]} done'
            ),
        ),
        _response(
            1,
            (
                '{"events":[{"start_time":11,"end_time":15,'
                '"type":"motion","description":"They walk inside"},'
                '{"start_time":12,"end_time":14,"type":"alarm",'
                '"description":"An alarm sounds"}]}'
            ),
        ),
    ]

    events = aggregate_events(chunks)

    assert events == [
        {
            "start_time": 0,
            "end_time": 15.0,
            "type": "motion",
            "description": "A person enters | They walk inside",
            "evidence": [
                {
                    "chunk_idx": 0,
                    "source_id": "147c40d6-98ff-4f7f-8845-9fa62a3a9e8c",
                },
                {
                    "chunk_idx": 1,
                    "source_id": "147c40d6-98ff-4f7f-8845-9fa62a3a9e8c",
                },
            ],
            "id": 1,
        },
        {
            "start_time": 12.0,
            "end_time": 14.0,
            "type": "alarm",
            "description": "An alarm sounds",
            "evidence": [
                {
                    "chunk_idx": 1,
                    "source_id": "147c40d6-98ff-4f7f-8845-9fa62a3a9e8c",
                }
            ],
            "id": 2,
        },
    ]


async def test_async_and_from_store_apis() -> None:
    chunks = [_response(0, "A person waves.")]
    generator = DeterministicGenerator()

    direct = await summarize_video(chunks, generator=generator)
    payload = json.loads(direct.choices[0].message.content or "")
    assert payload["video_summary"] == "Summarized 1 events."
    assert payload["total_events"] == 1
    assert payload["uuids"] == ["147c40d6-98ff-4f7f-8845-9fa62a3a9e8c"]
    assert payload["events"][0]["description"] == "A person waves."

    store = InMemoryChunkStore()
    await store.store_chunks("job-1", chunks)
    stored = await summarize_from_store("job-1", store=store, generator=generator)
    assert json.loads(stored.choices[0].message.content or "") == payload

    with pytest.raises(KeyError, match="unknown summarization job"):
        await summarize_from_store("missing", store=store, generator=generator)


async def test_empty_input_does_not_call_an_llm() -> None:
    result = await summarize_video([])
    payload = json.loads(result.choices[0].message.content or "")

    assert payload == {
        "events": [],
        "total_events": 0,
        "video_summary": "",
        "uuids": [],
    }
    assert result.usage is not None
    assert result.usage.summary_requests == 0


async def test_default_generator_is_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class ClosingGenerator(DeterministicGenerator):
        closed = False

        async def aclose(self) -> None:
            self.closed = True

    generator = ClosingGenerator()
    monkeypatch.setattr(
        summarization_service,
        "get_generator",
        lambda _: generator,
    )

    await summarize_video([_response(0, "A person waves.")])

    assert generator.closed


async def test_sync_api_rejects_running_event_loop() -> None:
    with pytest.raises(RuntimeError, match="await summarize_video"):
        summarize_video_sync(
            [_response(0, "A person waves.")],
            generator=DeterministicGenerator(),
        )
