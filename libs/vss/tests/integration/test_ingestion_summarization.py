# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Network-mocked integration through the existing production pipeline.

Only HTTP responses and backend construction are injected. Public ingest(), its
real queue, RTVI transport/SSE parsing, response mapping, aggregation, summary
service, provider adapter and provider SDK all execute. No helper constructs
caption response objects or supplies missing processing-adapter behavior.

This establishes the whole-video pipeline baseline. It does not establish local
frame/clip processing integration or live inference accuracy.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import UUID

import httpx
import pytest
from openai import AsyncOpenAI

import vss.ingestion.backend as backend_registry
from vss.ingestion import (
    IngestionJob,
    IngestionJobState,
    IngestionQueue,
    RequestSource,
    RTVIBackend,
    VlmChunkResponse,
    ingest,
    register_backend,
)
from vss.ingestion.exceptions import RTVIBackendError
from vss.ingestion.rtvi import _RTVITransport
from vss.summarization import (
    CompletionResponse,
    InMemoryChunkStore,
    OpenAISummaryGenerator,
    SummaryGenerationError,
    summarize_from_store,
    summarize_video,
)

ORIGINAL_ID = "11111111-1111-4111-8111-111111111111"
ASSET_ID = "22222222-2222-4222-8222-222222222222"


def caption_payload(
    index: int, start: int, end: int, event: dict[str, Any]
) -> dict[str, Any]:
    """Server-side wire fixture, not a constructed library response."""
    return {
        "chunk_id": index,
        "start_time": str(start),
        "end_time": str(end),
        "content": json.dumps({"events": [event]}),
        "frame_count": 8,
        "input_tokens": 12,
        "output_tokens": 4,
    }


@dataclass
class Pipeline:
    source: RequestSource
    queue: IngestionQueue
    generator: OpenAISummaryGenerator
    trace: list[str] = field(default_factory=list)
    caption_requests: list[dict[str, Any]] = field(default_factory=list)
    summary_requests: list[dict[str, Any]] = field(default_factory=list)
    jobs: list[IngestionJob[Any]] = field(default_factory=list)
    mode: str = "normal"
    summary_fail: bool = False
    delete_conflicts: int = 0
    caption_started: asyncio.Event = field(default_factory=asyncio.Event)
    deleted: asyncio.Event = field(default_factory=asyncio.Event)
    first_event_consumed: asyncio.Event = field(default_factory=asyncio.Event)
    stream_closed: asyncio.Event = field(default_factory=asyncio.Event)

    def submit(
        self, source: RequestSource | None = None, *, model: str | None = "fixture-vlm"
    ) -> Any:
        job = ingest(
            source or self.source,
            backends=["network_e2e"],
            model=model,
            prompt="Return source-relative events.",
            chunk_duration=10,
            chunk_overlap_duration=2,
            queue=self.queue,
        )
        self.jobs.append(job)
        return job


@pytest.fixture(params=["responses", "chat_completions"])
async def pipeline(
    request: pytest.FixtureRequest,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[Pipeline]:
    monkeypatch.delenv("VLM_MODEL", raising=False)
    monkeypatch.delenv("RTVI_MODEL", raising=False)
    source_path = tmp_path / "fixture.mp4"
    source_path.write_bytes(b"video bytes uploaded by the actual transport")
    state: Pipeline
    backends: list[RTVIBackend] = []

    class BlockingCaptionStream(httpx.AsyncByteStream):
        async def __aiter__(self) -> AsyncIterator[bytes]:
            chunk = caption_payload(
                0,
                0,
                10,
                {
                    "start_time": 1,
                    "end_time": 2,
                    "type": "entry",
                    "description": "A person enters.",
                },
            )
            yield (
                "data: " + json.dumps({"chunk_responses": [chunk]}) + "\n\n"
            ).encode()
            # Execution resumes here only when the production SSE consumer asks
            # for another event after processing the first response.
            state.first_event_consumed.set()
            await asyncio.Event().wait()

        async def aclose(self) -> None:
            state.stream_closed.set()

    async def vlm_handler(http_request: httpx.Request) -> httpx.Response:
        state.trace.append(f"VLM {http_request.method} {http_request.url.path}")
        if http_request.url.path == "/v1/files" and http_request.method == "POST":
            assert source_path.name.encode() in http_request.content
            assert source_path.read_bytes() in http_request.content
            return httpx.Response(200, json={"id": ASSET_ID})
        if http_request.method == "DELETE":
            assert http_request.url.path == f"/v1/files/{ASSET_ID}"
            if state.delete_conflicts:
                state.delete_conflicts -= 1
                return httpx.Response(409, json={"detail": "asset still in use"})
            state.deleted.set()
            return httpx.Response(204)
        if http_request.url.path == "/v1/models":
            return httpx.Response(503, json={"detail": "model discovery unavailable"})
        assert http_request.url.path == "/v1/generate_captions"
        state.caption_requests.append(json.loads(http_request.content))
        state.caption_started.set()
        if state.mode == "blocked":
            await asyncio.Event().wait()
        if state.mode == "blocked_stream":
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                stream=BlockingCaptionStream(),
            )
        if state.mode == "http_error":
            return httpx.Response(503, json={"detail": "inference unavailable"})
        if state.mode == "malformed":
            text = "data: {broken-json}\n\n"
        else:
            first = caption_payload(
                0,
                0,
                10,
                {
                    "start_time": 8,
                    "end_time": 10,
                    "type": "entry",
                    "description": "A person enters.",
                },
            )
            overlapping = caption_payload(
                1,
                8,
                18,
                {
                    "start_time": 9,
                    "end_time": 11,
                    "type": "entry",
                    "description": "A person enters.",
                },
            )
            last = caption_payload(
                2,
                20,
                30,
                {
                    "start_time": 21,
                    "end_time": 23,
                    "type": "exit",
                    "description": "A person exits.",
                },
            )
            stale = {**first, "content": "stale caption replaced by a later update"}
            wire_chunks = (
                [] if state.mode == "empty" else [last, stale, overlapping, first]
            )
            text = (
                "".join(
                    "data: " + json.dumps({"chunk_responses": [chunk]}) + "\n\n"
                    for chunk in wire_chunks
                )
                + "data: [DONE]\n\n"
            )
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            text=text,
        )

    async def summary_handler(http_request: httpx.Request) -> httpx.Response:
        state.trace.append(f"LLM {http_request.method} {http_request.url.path}")
        state.summary_requests.append(json.loads(http_request.content))
        if state.summary_fail:
            return httpx.Response(503, json={"error": {"message": "LLM unavailable"}})
        if request.param == "responses":
            assert http_request.url.path == "/v1/responses"
            body = {
                "id": "resp_fixture",
                "object": "response",
                "created_at": 1,
                "status": "completed",
                "model": "served-summary-model",
                "output": [
                    {
                        "id": "msg_fixture",
                        "type": "message",
                        "role": "assistant",
                        "status": "completed",
                        "content": [
                            {
                                "type": "output_text",
                                "text": "A person enters, then exits.",
                                "annotations": [],
                            }
                        ],
                    }
                ],
                "usage": {"input_tokens": 9, "output_tokens": 5, "total_tokens": 14},
            }
        else:
            assert http_request.url.path == "/v1/chat/completions"
            body = {
                "id": "chatcmpl_fixture",
                "object": "chat.completion",
                "created": 1,
                "model": "served-summary-model",
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {
                            "role": "assistant",
                            "content": "A person enters, then exits.",
                        },
                    }
                ],
                "usage": {
                    "prompt_tokens": 9,
                    "completion_tokens": 5,
                    "total_tokens": 14,
                },
            }
        return httpx.Response(200, json=body)

    vlm_http = httpx.AsyncClient(
        base_url="https://vlm.test",
        transport=httpx.MockTransport(vlm_handler),
    )
    summary_http = httpx.AsyncClient(transport=httpx.MockTransport(summary_handler))
    sdk = AsyncOpenAI(
        base_url="https://summary.test/v1",
        api_key="fixture-summary-secret",
        http_client=summary_http,
        max_retries=0,
    )
    generator = OpenAISummaryGenerator(
        client=sdk,
        model="configured-summary-model",
        api_type=request.param,
    )
    state = Pipeline(
        source=RequestSource.for_file(str(source_path)),
        queue=IngestionQueue(max_concurrency=1, max_pending=2),
        generator=generator,
    )
    state.source.source_id = ORIGINAL_ID

    class ConfiguredBackend(RTVIBackend):
        def __init__(self) -> None:
            super().__init__(
                _RTVITransport(
                    client=vlm_http,
                    api_key="fixture-vlm-secret",
                )
            )
            backends.append(self)

    monkeypatch.setattr(
        backend_registry, "_BACKEND_REGISTRY", dict(backend_registry._BACKEND_REGISTRY)
    )
    register_backend("network_e2e", ConfiguredBackend)
    try:
        yield state
    finally:
        try:
            # Private test seam: public job cancellation completes its future
            # before the queue worker finishes upload cleanup. Backend tracking
            # also ends before deletion retries, so snapshot queue workers.
            workers = tuple(state.queue._running.values())
            # Reverse submission order removes pending jobs before active ones.
            # Already-cancelled workers must finish without a second cancel.
            for job in reversed(state.jobs):
                if not job.done() and not job.cancelled():
                    job.cancel()
            await asyncio.wait_for(
                asyncio.gather(*workers, return_exceptions=True), timeout=2
            )
            await asyncio.wait_for(
                asyncio.gather(*(backend.aclose() for backend in backends)),
                timeout=2,
            )
        finally:
            await sdk.close()
            await vlm_http.aclose()


def sent_events(pipeline: Pipeline, index: int = -1) -> list[dict[str, Any]]:
    body = pipeline.summary_requests[index]
    text = (
        body["input"][0]["content"]
        if "input" in body
        else body["messages"][1]["content"]
    )
    return json.loads(text)["events"]


async def test_public_ingestion_to_real_summary_adapter(pipeline: Pipeline) -> None:
    original_source = pipeline.source.model_dump()
    job = pipeline.submit()
    assert isinstance(job, IngestionJob)
    chunks = (await job)["network_e2e"]
    assert job.state is IngestionJobState.SUCCEEDED
    assert [response.chunk.chunkIdx for response in chunks] == [0, 1, 2]
    assert chunks[0].chunk.is_first
    assert chunks[-1].chunk.is_last
    assert all(response.rtvi_frame_count == 8 for response in chunks)
    assert all(
        response.vlm_stats == {"input_tokens": 12, "output_tokens": 4}
        for response in chunks
    )
    assert pipeline.source.model_dump() == original_source
    assert pipeline.deleted.is_set()
    assert len(pipeline.caption_requests) == 1
    assert pipeline.caption_requests[0]["id"] == ASSET_ID
    assert pipeline.caption_requests[0]["chunk_overlap_duration"] == 2

    # Characterize the existing whole-video contract honestly: it currently
    # exposes uploaded asset identity. Future processing adapters need their own
    # original-source-identity acceptance tests, not normalization in this helper.
    assert all(response.chunk.sourceId == ASSET_ID for response in chunks)
    assert all(
        VlmChunkResponse.model_validate_json(response.model_dump_json()) == response
        for response in chunks
    )
    completion = await summarize_video(chunks, generator=pipeline.generator)
    payload = json.loads(completion.choices[0].message.content)
    assert payload["video_summary"] == "A person enters, then exits."
    assert payload["uuids"] == [ASSET_ID]
    assert payload["total_events"] == 2
    assert [
        (event["start_time"], event["end_time"]) for event in payload["events"]
    ] == [(8, 11), (21, 23)]
    assert [item["chunk_idx"] for item in payload["events"][0]["evidence"]] == [0, 1]
    assert sent_events(pipeline) == [
        {
            "start_time": 8,
            "end_time": 11,
            "type": "entry",
            "description": "A person enters.",
        },
        {
            "start_time": 21,
            "end_time": 23,
            "type": "exit",
            "description": "A person exits.",
        },
    ]
    assert completion.video_id == UUID(ASSET_ID)
    assert completion.model == "served-summary-model"
    assert completion.media_info.start_offset == 0
    assert completion.media_info.end_offset == 30
    assert completion.usage.total_chunks_processed == 3
    assert completion.usage.aggregation_tokens == 14
    assert (
        CompletionResponse.model_validate_json(completion.model_dump_json())
        == completion
    )
    assert pipeline.trace.index(f"VLM DELETE /v1/files/{ASSET_ID}") < next(
        index for index, entry in enumerate(pipeline.trace) if entry.startswith("LLM")
    )
    diagnostic = completion.model_dump_json() + str(pipeline.trace)
    assert "fixture-vlm-secret" not in diagnostic
    assert "fixture-summary-secret" not in diagnostic


async def test_deferred_summary_uses_real_ingestion_outputs_without_reingestion(
    pipeline: Pipeline,
) -> None:
    job = pipeline.submit()
    chunks = (await job)["network_e2e"]
    store = InMemoryChunkStore()
    await store.store_chunks(str(job.id), chunks)
    vlm_calls = [entry for entry in pipeline.trace if entry.startswith("VLM")]
    completion = await summarize_from_store(
        str(job.id), store=store, generator=pipeline.generator
    )
    assert json.loads(completion.choices[0].message.content)["total_events"] == 2
    assert [entry for entry in pipeline.trace if entry.startswith("VLM")] == vlm_calls
    assert pipeline.deleted.is_set()
    assert len(pipeline.summary_requests) == 1


@pytest.mark.parametrize("mode", ["http_error", "malformed"])
async def test_ingestion_failure_releases_upload_and_never_calls_summary(
    pipeline: Pipeline, mode: str
) -> None:
    pipeline.mode = mode
    job = pipeline.submit()
    with pytest.raises(RTVIBackendError):
        chunks = (await job)["network_e2e"]
        await summarize_video(chunks, generator=pipeline.generator)
    assert job.state is IngestionJobState.FAILED
    assert pipeline.deleted.is_set()
    assert pipeline.summary_requests == []


async def test_model_discovery_failure_releases_upload(pipeline: Pipeline) -> None:
    job = pipeline.submit(model=None)
    with pytest.raises(RTVIBackendError):
        await job
    assert pipeline.deleted.is_set()
    assert pipeline.caption_requests == []


async def test_summary_failure_occurs_after_media_release(pipeline: Pipeline) -> None:
    chunks = (await pipeline.submit())["network_e2e"]
    pipeline.summary_fail = True
    with pytest.raises(SummaryGenerationError):
        await summarize_video(chunks, generator=pipeline.generator)
    assert pipeline.deleted.is_set()
    assert len(pipeline.summary_requests) == 1


async def test_empty_ingestion_avoids_summary_endpoint(pipeline: Pipeline) -> None:
    pipeline.mode = "empty"
    chunks = (await pipeline.submit())["network_e2e"]
    completion = await summarize_video(chunks, generator=pipeline.generator)
    payload = json.loads(completion.choices[0].message.content)
    assert chunks == []
    assert payload["events"] == []
    assert payload["video_summary"] == ""
    assert completion.usage.summary_requests == 0
    assert pipeline.summary_requests == []
    assert pipeline.deleted.is_set()


async def test_caller_owned_asset_is_not_deleted(pipeline: Pipeline) -> None:
    chunks = (await pipeline.submit(RequestSource(source_id=ASSET_ID)))["network_e2e"]
    await summarize_video(chunks, generator=pipeline.generator)
    assert pipeline.trace[0] == "VLM POST /v1/generate_captions"
    assert not any("/v1/files" in entry for entry in pipeline.trace)


async def test_upload_delete_conflict_retries_before_summary(
    pipeline: Pipeline,
) -> None:
    pipeline.delete_conflicts = 1
    chunks = (await pipeline.submit())["network_e2e"]
    await summarize_video(chunks, generator=pipeline.generator)
    assert pipeline.trace.count(f"VLM DELETE /v1/files/{ASSET_ID}") == 2
    assert pipeline.deleted.is_set()


async def test_request_cancellation_joins_observable_cleanup_and_allows_next_job(
    pipeline: Pipeline,
) -> None:
    pipeline.mode = "blocked"
    active = pipeline.submit()
    await asyncio.wait_for(pipeline.caption_started.wait(), timeout=2)
    assert active.cancel()
    with pytest.raises(asyncio.CancelledError):
        await active
    # The public future cancels before worker cleanup: this event, not awaiting
    # the cancelled job alone, establishes completed remote asset deletion.
    await asyncio.wait_for(pipeline.deleted.wait(), timeout=2)
    assert active.state is IngestionJobState.CANCELLED
    pipeline.mode = "normal"
    chunks = (await asyncio.wait_for(pipeline.submit(), timeout=2))["network_e2e"]
    completion = await summarize_video(chunks, generator=pipeline.generator)
    assert completion.usage.total_chunks_processed == 3


async def test_mid_sse_cancellation_closes_stream_releases_upload_and_reuses_queue(
    pipeline: Pipeline,
) -> None:
    pipeline.mode = "blocked_stream"
    active = pipeline.submit()
    await asyncio.wait_for(pipeline.first_event_consumed.wait(), timeout=2)
    assert active.cancel()
    with pytest.raises(asyncio.CancelledError):
        await active
    await asyncio.wait_for(pipeline.stream_closed.wait(), timeout=2)
    await asyncio.wait_for(pipeline.deleted.wait(), timeout=2)
    assert pipeline.summary_requests == []
    pipeline.mode = "normal"
    chunks = (await asyncio.wait_for(pipeline.submit(), timeout=2))["network_e2e"]
    completion = await summarize_video(chunks, generator=pipeline.generator)
    assert completion.usage.total_chunks_processed == 3


async def test_pending_cancel_never_uploads_its_source(pipeline: Pipeline) -> None:
    pipeline.mode = "blocked"
    active = pipeline.submit()
    await asyncio.wait_for(pipeline.caption_started.wait(), timeout=2)
    pending = pipeline.submit()
    assert pending.state is IngestionJobState.PENDING
    assert pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending
    assert pipeline.trace.count("VLM POST /v1/files") == 1
    active.cancel()
    with pytest.raises(asyncio.CancelledError):
        await active
    await asyncio.wait_for(pipeline.deleted.wait(), timeout=2)
    # A completed subsequent job is an observable FIFO barrier. If cancelled
    # pending work mistakenly ran when the active slot drained, it would upload
    # ahead of this job and make the count three rather than two.
    pipeline.mode = "normal"
    await asyncio.wait_for(pipeline.submit(), timeout=2)
    assert pipeline.trace.count("VLM POST /v1/files") == 2
