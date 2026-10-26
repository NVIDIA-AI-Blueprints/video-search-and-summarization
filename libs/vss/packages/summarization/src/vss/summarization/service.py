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

"""Public M2 video summarization services."""

from __future__ import annotations

import asyncio
import copy
import json
import time
import uuid
from typing import Any
from uuid import UUID

from vss.ingestion import ChunkInfo, VlmChunkResponse
from vss.ingestion.persistence import environment_storage_config
from vss.observability import measure, metric_scope, record
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
from .exceptions import SummaryConfigurationError, SummaryGenerationError
from .generators import (
    GeneratedSummary,
    OpenAISummaryGenerator,
    SummaryGenerator,
    get_generator,
)
from .storage import AdapterChunkStore, ChunkStore, InMemoryChunkStore

DEFAULT_CHUNK_STORE = InMemoryChunkStore()


async def summarize_video(
    chunks: list[VlmChunkResponse],
    *,
    generator: SummaryGenerator | None = None,
    prompt: str | None = None,
    model: str | None = None,
    batch_size: int | None = None,
) -> CompletionResponse:
    """Summarize ordered events, optionally in sequential event-count batches.

    ``batch_size=None`` keeps the single-call behavior. With a positive size,
    batch summaries are joined in order with blank lines, without a further
    model call. The size does not limit tokens, context length or input memory.
    """

    _validate_batch_size(batch_size)

    with metric_scope(
        source_ids=(
            response.chunk.sourceId
            for response in chunks
            if response.chunk is not None and response.chunk.sourceId
        ),
        pipeline="summarization",
        configured_model=model,
    ):
        with measure("summarization.total", library="summarization"):
            return await _summarize_video(
                chunks,
                generator=generator,
                prompt=prompt,
                model=model,
                batch_size=batch_size,
            )


async def _summarize_video(
    chunks: list[VlmChunkResponse],
    *,
    generator: SummaryGenerator | None,
    prompt: str | None,
    model: str | None,
    batch_size: int | None,
) -> CompletionResponse:

    started = time.monotonic()
    with measure("summarization.aggregate", library="summarization"):
        events = aggregate_events(chunks)
    aggregation_latency = time.monotonic() - started
    record("summarization.chunks", len(chunks), library="summarization")
    record("summarization.events", len(events), library="summarization")
    if batch_size is None or not events:
        record("summarization.batches", 1, library="summarization")
    summary_started = time.monotonic()
    if events:
        message_size = (
            _batch_preflight(chunks, events, batch_size)
            if batch_size is not None
            else 0
        )
        if batch_size is not None:
            _check_batch_elapsed(started)
        selected_generator = generator or get_generator("openai")
        primary_failed = False
        try:
            if batch_size is None:
                with metric_scope(backend="summary_generator", model=model):
                    with measure("summarization.llm", library="summarization"):
                        generated = await selected_generator.generate(
                            events, prompt=prompt, model=model
                        )
                summary_requests = 1
            else:
                generated, summary_requests = await _generate_batches(
                    events,
                    selected_generator,
                    message_size=message_size,
                    batch_size=batch_size,
                    prompt=prompt,
                    model=model,
                    started=started,
                )
        except BaseException:
            primary_failed = True
            raise
        finally:
            if generator is None:
                try:
                    with measure("summarization.cleanup", library="summarization"):
                        await selected_generator.aclose()
                except BaseException:
                    if not primary_failed:
                        raise
        if batch_size is None:
            _record_generated_usage(selected_generator, generated)
    else:
        generated = GeneratedSummary(text="", model=model or "none")
        summary_requests = 0
        for metric in ("llm", "input_tokens", "output_tokens"):
            record(
                "summarization." + metric,
                None,
                library="summarization",
                availability="not_applicable",
            )
    if batch_size is None or not events:
        record("summarization.requests", summary_requests, library="summarization")
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
    batch_size: int | None = None,
) -> CompletionResponse:
    """Synchronous wrapper for :func:`summarize_video`."""

    _validate_batch_size(batch_size)
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(
            summarize_video(
                chunks,
                generator=generator,
                prompt=prompt,
                model=model,
                batch_size=batch_size,
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
    batch_size: int | None = None,
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
    _validate_batch_size(batch_size)
    with measure("summarization.store", library="summarization"):
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
        chunks, generator=generator, prompt=prompt, model=model, batch_size=batch_size
    )


def _validate_batch_size(batch_size: int | None) -> None:
    if batch_size is not None and (
        type(batch_size) is not int or not 1 <= batch_size <= 1_000_000
    ):
        raise SummaryConfigurationError(
            "batch_size must be None or an integer from 1 to 1000000"
        )


def _record_generated_usage(
    generator: SummaryGenerator,
    generated: GeneratedSummary,
) -> None:
    if isinstance(generator, OpenAISummaryGenerator):
        return  # The adapter records raw usage, including unavailable fields.
    with metric_scope(model=generated.model, backend="summary_generator"):
        for metric, field in (
            ("input_tokens", "prompt_tokens"),
            ("output_tokens", "completion_tokens"),
        ):
            present = field in generated.model_fields_set
            record(
                "summarization." + metric,
                getattr(generated, field) if present else None,
                library="summarization",
                origin="provider",
                availability="measured" if present else "not_reported",
            )


def _batch_preflight(
    chunks: list[VlmChunkResponse], events: list[dict[str, Any]], batch_size: int
) -> int:
    message_size = len(
        json.dumps(
            {
                "events": events,
                "total_events": len(events),
                "video_summary": "",
                "uuids": _source_ids(chunks),
            },
            separators=(",", ":"),
            ensure_ascii=False,
        )
    )
    requests = (len(events) + batch_size - 1) // batch_size
    if message_size > 1_000_000 or len(chunks) > 1_000_000 or requests > 1_000_000:
        raise SummaryGenerationError(
            "batched summary exceeds completion response limits"
        )
    return message_size


def _check_batch_elapsed(started: float) -> None:
    if (time.monotonic() - started) * 1000 > 1_000_000:
        raise SummaryGenerationError(
            "batched summary exceeds completion response limits"
        )


async def _generate_batches(
    events: list[dict[str, Any]],
    generator: SummaryGenerator,
    *,
    message_size: int,
    batch_size: int,
    prompt: str | None,
    model: str | None,
    started: float,
) -> tuple[GeneratedSummary, int]:
    """One sequential attempt per event partition, with no hidden reduction."""
    requests = (len(events) + batch_size - 1) // batch_size
    parts: list[str] = []
    prompt_tokens = completion_tokens = 0
    selected_model: str | None = None
    finish_reason = "stop"
    priorities = {"stop": 0, "length": 1, "content_filter": 2}
    for start in range(0, len(events), batch_size):
        await asyncio.sleep(0)  # Deliver cancellation for non-yielding plugins too.
        _check_batch_elapsed(started)
        with metric_scope(backend="summary_generator", model=model):
            with measure("summarization.llm", library="summarization"):
                record("summarization.batches", 1, library="summarization")
                record("summarization.requests", 1, library="summarization")
                generated = await generator.generate(
                    copy.deepcopy(events[start : start + batch_size]),
                    prompt=prompt,
                    model=model,
                )
                if not isinstance(generated, GeneratedSummary):
                    raise SummaryGenerationError(
                        "summary generator returned an invalid batch result"
                    )
                _record_generated_usage(generator, generated)
                if selected_model is not None and generated.model != selected_model:
                    raise SummaryGenerationError(
                        "summary model changed between batches"
                    )
                selected_model = generated.model
                if generated.finish_reason not in priorities:
                    raise SummaryGenerationError(
                        "unsupported summary batch finish reason"
                    )
                if priorities[generated.finish_reason] > priorities[finish_reason]:
                    finish_reason = generated.finish_reason
                prompt_tokens += generated.prompt_tokens
                completion_tokens += generated.completion_tokens
                message_size += len(json.dumps(generated.text, ensure_ascii=False)) - 2
                if parts:
                    message_size += 4  # JSON-escaped blank-line separator.
                _check_batch_elapsed(started)
                if (
                    prompt_tokens + completion_tokens > 1_000_000
                    or message_size > 1_000_000
                    or len(generated.model) > 1024
                ):
                    raise SummaryGenerationError(
                        "batched summary exceeds completion response limits"
                    )
                parts.append(generated.text)
    await asyncio.sleep(0)
    assert selected_model is not None
    return GeneratedSummary(
        text="\n\n".join(parts),
        model=selected_model,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        finish_reason=finish_reason,
    ), requests


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
