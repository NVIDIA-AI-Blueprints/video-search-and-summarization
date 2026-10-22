# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Public M2 video summarization services."""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from uuid import UUID

from vss.ingestion import ChunkInfo, VlmChunkResponse
from vss.ingestion.persistence import environment_storage_config
from vss.storage import (
    StorageAdapter,
    StorageConfig,
    StorageConfigurationError,
    create_storage,
)

from .aggregation import aggregate_events
from .contracts import (
    ChatCompletionResponseMessage,
    CompletionFinishReason,
    CompletionObject,
    CompletionResponse,
    CompletionResponseChoice,
    CompletionUsage,
    MediaInfoOffset,
    MediaInfoTimeStamp,
)
from .generators import GeneratedSummary, SummaryGenerator, get_generator
from .storage import AdapterChunkStore, ChunkStore, InMemoryChunkStore

DEFAULT_CHUNK_STORE = InMemoryChunkStore()


async def summarize_video(
    chunks: list[VlmChunkResponse],
    *,
    generator: SummaryGenerator | None = None,
    prompt: str | None = None,
    model: str | None = None,
) -> CompletionResponse:
    """Aggregate VLM chunks and generate an existing completion response."""

    started = time.monotonic()
    events = aggregate_events(chunks)
    aggregation_latency = time.monotonic() - started
    summary_started = time.monotonic()
    if events:
        selected_generator = generator or get_generator("openai")
        try:
            generated = await selected_generator.generate(
                events, prompt=prompt, model=model
            )
        finally:
            if generator is None:
                await selected_generator.aclose()
        summary_requests = 1
    else:
        generated = GeneratedSummary(text="", model=model or "none")
        summary_requests = 0
    summary_latency = time.monotonic() - summary_started
    source_ids = _source_ids(chunks)
    content = json.dumps(
        {
            "events": events,
            "total_events": len(events),
            "video_summary": generated.text,
            "uuids": source_ids,
        },
        separators=(",", ":"),
        ensure_ascii=False,
    )

    return CompletionResponse(
        id=uuid.uuid4(),
        choices=[
            CompletionResponseChoice(
                finish_reason=_finish_reason(generated.finish_reason),
                index=0,
                message=ChatCompletionResponseMessage(
                    content=content,
                    role="assistant",
                ),
            )
        ],
        created=int(time.time()),
        model=generated.model,
        object=CompletionObject.SUMMARIZATION_COMPLETION,
        usage=CompletionUsage(
            query_processing_time=int((time.monotonic() - started) * 1_000),
            total_chunks_processed=len(chunks),
            summary_tokens=0,
            aggregation_tokens=(generated.prompt_tokens + generated.completion_tokens),
            summary_requests=summary_requests,
            summary_latency=summary_latency,
            aggregation_latency=aggregation_latency,
        ),
        video_id=_video_id(chunks),
        media_info=_media_info(chunks),
    )


def summarize_video_sync(
    chunks: list[VlmChunkResponse],
    *,
    generator: SummaryGenerator | None = None,
    prompt: str | None = None,
    model: str | None = None,
) -> CompletionResponse:
    """Synchronous wrapper for :func:`summarize_video`."""

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(
            summarize_video(
                chunks,
                generator=generator,
                prompt=prompt,
                model=model,
            )
        )
    raise RuntimeError(
        "summarize_video_sync cannot run inside an active event loop; "
        "await summarize_video instead"
    )


async def summarize_from_store(
    job_id: str,
    *,
    store: ChunkStore | None = None,
    storage: StorageAdapter | None = None,
    storage_config: StorageConfig | None = None,
    generator: SummaryGenerator | None = None,
    prompt: str | None = None,
    model: str | None = None,
) -> CompletionResponse:
    """Read complete captions and summarize without video decoding or inference.

    ``job_id`` is the canonical string of an ingestion UUID or an opaque key
    saved via ChunkStore. ``store``, ``storage`` and ``storage_config`` are mutually
    exclusive. Explicit selection wins over VSS_STORAGE_BACKEND/ROOT. With no
    selection/configuration, DEFAULT_CHUNK_STORE retains process-local behavior.
    Config-created adapters are closed after reading; injected adapters/stores
    remain caller-owned. Missing raises KeyError; corrupt/incomplete data fails
    before calling the LLM. Prompt/model affect this summary, not saved captions.
    """
    if sum(value is not None for value in (store, storage, storage_config)) > 1:
        raise StorageConfigurationError(
            "Supply only one of store, storage or storage_config"
        )
    if store is not None:
        chunks = await store.get_chunks(job_id)
    elif storage is not None:
        chunks = await AdapterChunkStore(storage).get_chunks(job_id)
    else:
        config = (
            storage_config
            if storage_config is not None
            else environment_storage_config()
        )
        if config is None:
            chunks = await DEFAULT_CHUNK_STORE.get_chunks(job_id)
        else:
            async with create_storage(config) as selected:
                chunks = await AdapterChunkStore(selected).get_chunks(job_id)
    return await summarize_video(
        chunks, generator=generator, prompt=prompt, model=model
    )


def _finish_reason(value: str) -> CompletionFinishReason:
    try:
        return CompletionFinishReason(value)
    except ValueError:
        return CompletionFinishReason.STOP


def _video_id(chunks: list[VlmChunkResponse]) -> UUID:
    for response in chunks:
        if response.chunk is not None and response.chunk.sourceId:
            try:
                return UUID(response.chunk.sourceId)
            except ValueError:
                return uuid.uuid5(uuid.NAMESPACE_URL, response.chunk.sourceId)
    return uuid.uuid5(uuid.NAMESPACE_URL, "vss:unknown-video")


def _source_ids(chunks: list[VlmChunkResponse]) -> list[str]:
    return list(
        dict.fromkeys(
            response.chunk.sourceId
            for response in chunks
            if response.chunk is not None and response.chunk.sourceId
        )
    )


def _media_info(
    chunks: list[VlmChunkResponse],
) -> MediaInfoOffset | MediaInfoTimeStamp:
    infos = [response.chunk for response in chunks if response.chunk is not None]
    if any(info.start_ntp or info.end_ntp for info in infos):
        return MediaInfoTimeStamp(
            type="timestamp",
            start_timestamp=_first_ntp(infos),
            end_timestamp=_last_ntp(infos),
        )

    starts = [info.start_pts / 1_000_000_000 for info in infos]
    ends = [info.end_pts / 1_000_000_000 for info in infos]
    start = min(starts, default=0.0)
    end = max(ends, default=start)
    return MediaInfoOffset.for_response(start, end)


def _first_ntp(infos: list[ChunkInfo]) -> str | None:
    values = [info.start_ntp for info in infos if info.start_ntp]
    return min(values) if values else None


def _last_ntp(infos: list[ChunkInfo]) -> str | None:
    values = [info.end_ntp for info in infos if info.end_ntp]
    return max(values) if values else None
