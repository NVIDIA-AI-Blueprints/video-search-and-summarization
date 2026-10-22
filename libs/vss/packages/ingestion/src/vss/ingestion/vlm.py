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

"""Frame-level VLM processing over caller-selected CPU or GPU decoders."""

from __future__ import annotations

import asyncio
import math
import os
import time
from abc import ABC, abstractmethod
from collections.abc import Mapping
from contextlib import AsyncExitStack, aclosing
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

import httpx

from vss.video_processing.contracts import (
    DecodeOptions,
    DecodePlan,
    SamplingPolicy,
    SourceInput,
)
from vss.video_processing.execution import decode_frames, probe_video, subsample_frames
from vss.video_processing.frames import Frame

from .exceptions import VLMBackendError
from .vlm_contracts import (
    ChunkInfo,
    VlmCaptionsCompletionResponse,
    VlmChunkResponse,
)

if TYPE_CHECKING:
    from vss.video_processing.shared_decode import FrameBuffer

DEFAULT_VLM_BASE_URL = "https://inference-api.nvidia.com/v1"
DEFAULT_VLM_PROMPT = (
    "Describe the important visible events with their start time, end time, "
    "type, and description."
)
DEFAULT_SYSTEM_PROMPT = (
    "You analyze sampled video frames. Return only valid JSON with this shape: "
    '{"events":[{"start_time":number,"end_time":number,"type":string,'
    '"description":string}]}. Use only the supplied frame timestamps and keep '
    "each event inside the chunk boundaries."
)
DEFAULT_FRAMES_PER_CHUNK = 8
DEFAULT_PROCESSING_MAX_CONCURRENCY = 4
_PROCESSING_LIMITER_ATTRIBUTE = "_vss_vlm_processing_limiter"


class VLMSource(Protocol):
    """Source identity exposed to custom frame-level VLM backends."""

    @property
    def source_id(self) -> str: ...

    @property
    def url(self) -> str | None: ...


class _VLMCheckpoint(Protocol):
    """Private callbacks supplied by a higher-level durable orchestration layer.

    Times use source-relative nanoseconds. A confirmation must finish durably
    before inference for the next window; callback failures abort processing.
    The processor never owns or serializes the checkpoint's storage adapter.
    """

    @property
    def duration_s(self) -> float: ...

    @property
    def sample_timestamps_ns(self) -> tuple[int, ...]: ...

    def is_confirmed(self, index: int) -> bool: ...

    async def advance(self, index: int, /) -> None: ...

    async def confirm(self, index: int, response: VlmChunkResponse, /) -> None: ...

    async def get_responses(self) -> VlmCaptionsCompletionResponse: ...


@dataclass
class _BoundedFrameSampler:
    """Keep a bounded, temporally distributed set including both endpoints."""

    frames: list[Frame] = field(default_factory=list)

    def add(self, frame: Frame) -> None:
        self.frames.append(frame)
        if len(self.frames) <= DEFAULT_FRAMES_PER_CHUNK:
            return
        remove_at = min(
            range(1, len(self.frames) - 1),
            key=lambda index: (
                self.frames[index + 1].timestamp_ns
                - self.frames[index - 1].timestamp_ns,
                index,
            ),
        )
        del self.frames[remove_at]

    def selected(self) -> list[Frame]:
        return list(self.frames)

    @property
    def last(self) -> Frame | None:
        return self.frames[-1] if self.frames else None


@dataclass(frozen=True, slots=True)
class _ResolvedVLMSource:
    source_id: str
    url: str | None


@dataclass(frozen=True, slots=True)
class VLMFrameChunk:
    """Owned decoded frames for one half-open presentation-time window."""

    source: VLMSource
    chunk_idx: int
    start_s: float
    end_s: float
    frames: tuple[Frame, ...]

    def __post_init__(self) -> None:
        if self.chunk_idx < 0:
            raise ValueError("chunk_idx must be nonnegative")
        if (
            not math.isfinite(self.start_s)
            or not math.isfinite(self.end_s)
            or self.start_s < 0
            or self.end_s <= self.start_s
        ):
            raise ValueError("chunk bounds must be finite and satisfy 0 <= start < end")
        frames = tuple(self.frames)
        start_ns = round(self.start_s * 1_000_000_000)
        end_ns = round(self.end_s * 1_000_000_000)
        if any(not isinstance(frame, Frame) for frame in frames):
            raise TypeError("frames must contain Frame instances")
        if any(
            frame.timestamp_ns < start_ns or frame.timestamp_ns >= end_ns
            for frame in frames
        ):
            raise ValueError("frame timestamps must be inside the chunk window")
        object.__setattr__(self, "frames", frames)

    @property
    def duration_s(self) -> float:
        return self.end_s - self.start_s


class VLMBackend(ABC):
    """Interface for VLM inference over already-decoded image chunks."""

    async def __aenter__(self) -> VLMBackend:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        """Release resources owned by the backend."""
        return None

    @abstractmethod
    async def infer(
        self,
        chunk: VLMFrameChunk,
        *,
        prompt: str,
        model: str | None = None,
        generation_config: Mapping[str, Any] | None = None,
    ) -> VlmChunkResponse:
        """Run inference for one timestamped image chunk."""

    @abstractmethod
    async def cancel(self, source_id: str) -> bool:
        """Cancel active inference requests for a source."""

    @abstractmethod
    async def check_health(self) -> bool:
        """Return whether the provider endpoint is reachable."""

    @abstractmethod
    def capability_map(self) -> Mapping[str, Any]:
        """Describe backend capabilities."""


_VLM_BACKEND_REGISTRY: dict[str, type[VLMBackend]] = {}


def register_vlm_backend(name: str, backend_type: type[VLMBackend]) -> None:
    """Register a frame-level VLM processing backend."""
    if not name:
        raise ValueError("backend name must not be empty")
    if not issubclass(backend_type, VLMBackend):
        raise TypeError("backend_type must implement VLMBackend")
    _VLM_BACKEND_REGISTRY[name] = backend_type


def get_vlm_backend(name: str, **kwargs: Any) -> VLMBackend:
    """Construct a registered frame-level VLM processing backend."""
    try:
        backend_type = _VLM_BACKEND_REGISTRY[name]
    except KeyError as error:
        available = ", ".join(sorted(_VLM_BACKEND_REGISTRY)) or "none"
        raise KeyError(
            f"unknown VLM backend {name!r}; available: {available}"
        ) from error
    return backend_type(**kwargs)


def available_vlm_backends() -> tuple[str, ...]:
    """Return registered frame-level VLM backend names."""
    return tuple(sorted(_VLM_BACKEND_REGISTRY))


class OpenAICompatibleVLMBackend(VLMBackend):
    """OpenAI-compatible VLM backend for frames decoded on CPU or GPU."""

    name = "vlm"

    def __init__(
        self,
        base_url: str | None = None,
        *,
        api_key: str | None = None,
        model: str | None = None,
        system_prompt: str = DEFAULT_SYSTEM_PROMPT,
        timeout_s: float = 600,
        encoding_concurrency: int = 4,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if (
            isinstance(encoding_concurrency, bool)
            or not isinstance(encoding_concurrency, int)
            or encoding_concurrency < 1
        ):
            raise ValueError("encoding_concurrency must be a positive integer")
        resolved_base_url = (
            base_url or os.getenv("VLM_BASE_URL") or DEFAULT_VLM_BASE_URL
        ).rstrip("/")
        self._chat_completions_url = f"{resolved_base_url}/chat/completions"
        self._models_url = f"{resolved_base_url}/models"
        self._api_key = api_key or os.getenv("VLM_API_KEY")
        self._model = model or os.getenv("VLM_MODEL")
        self._system_prompt = system_prompt
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(timeout=httpx.Timeout(timeout_s))
        self._encoding_limiter = asyncio.Semaphore(encoding_concurrency)
        self._active: dict[str, set[asyncio.Task[VlmChunkResponse]]] = {}

    async def aclose(self) -> None:
        tasks = {
            task
            for source_tasks in self._active.values()
            for task in source_tasks
            if not task.done()
        }
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        if self._owns_client:
            await self._client.aclose()

    async def infer(
        self,
        chunk: VLMFrameChunk,
        *,
        prompt: str,
        model: str | None = None,
        generation_config: Mapping[str, Any] | None = None,
    ) -> VlmChunkResponse:
        if not prompt.strip():
            raise ValueError("prompt must not be empty")
        selected_model = model or self._model
        if not selected_model:
            raise VLMBackendError(
                "a VLM model is required; pass model or set VLM_MODEL"
            )
        current_task = asyncio.current_task()
        if current_task is None:
            raise VLMBackendError("VLM inference must run inside an asyncio task")
        if generation_config:
            reserved = {"messages", "model", "stream"} & generation_config.keys()
            if reserved:
                names = ", ".join(sorted(reserved))
                raise ValueError(
                    f"generation_config cannot override reserved fields: {names}"
                )

        self._active.setdefault(chunk.source.source_id, set()).add(current_task)
        started = time.time()
        try:
            payload: dict[str, Any] = {
                "model": selected_model,
                "messages": await self._messages(chunk, prompt),
                "max_tokens": 1024,
            }
            if generation_config:
                payload.update(generation_config)
            response = await self._client.post(
                self._chat_completions_url,
                json=payload,
                headers=self._headers,
            )
            response.raise_for_status()
            body = response.json()
            content = _completion_text(body)
        except httpx.HTTPStatusError as error:
            detail = _error_detail(error.response)
            raise VLMBackendError(
                f"VLM returned HTTP {error.response.status_code}: {detail}"
            ) from error
        except httpx.HTTPError as error:
            raise VLMBackendError(f"VLM request failed: {error}") from error
        except (KeyError, TypeError, ValueError) as error:
            raise VLMBackendError("VLM returned an invalid completion") from error
        finally:
            source_tasks = self._active.get(chunk.source.source_id)
            if source_tasks is not None:
                source_tasks.discard(current_task)
                if not source_tasks:
                    self._active.pop(chunk.source.source_id, None)

        completed = time.time()
        usage = body.get("usage") if isinstance(body, dict) else None
        usage = usage if isinstance(usage, dict) else {}
        return VlmChunkResponse(
            chunk=ChunkInfo(
                sourceId=chunk.source.source_id,
                chunkIdx=chunk.chunk_idx,
                file=chunk.source.url or "",
                start_pts=round(chunk.start_s * 1_000_000_000),
                end_pts=round(chunk.end_s * 1_000_000_000),
                is_first=chunk.chunk_idx == 0,
            ),
            vlm_response=content,
            vlm_start_time=started,
            vlm_end_time=completed,
            processing_latency=completed - started,
            rtvi_frame_count=len(chunk.frames),
            vlm_stats={
                "input_tokens": int(usage.get("prompt_tokens") or 0),
                "output_tokens": int(usage.get("completion_tokens") or 0),
            },
        )

    async def cancel(self, source_id: str) -> bool:
        active = [
            task for task in self._active.get(source_id, set()) if not task.done()
        ]
        for task in active:
            task.cancel()
        return bool(active)

    async def check_health(self) -> bool:
        try:
            response = await self._client.get(
                self._models_url,
                headers=self._headers,
            )
            return 200 <= response.status_code < 300
        except httpx.HTTPError:
            return False

    def capability_map(self) -> Mapping[str, Any]:
        return {
            "backend": self.name,
            "api": "chat_completions",
            "modalities": ("image", "text"),
            "outputs": ("caption",),
            "requires_decoded_frames": True,
            "decoder": "caller-selected",
        }

    @property
    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._api_key}"} if self._api_key else {}

    async def _messages(
        self,
        chunk: VLMFrameChunk,
        prompt: str,
    ) -> list[dict[str, Any]]:
        encoded_frames = await asyncio.gather(
            *(self._encode_frame(frame) for frame in chunk.frames)
        )
        content: list[dict[str, Any]] = [
            {
                "type": "text",
                "text": (
                    f"{prompt}\n\n"
                    f"Chunk {chunk.chunk_idx} spans {chunk.start_s:.3f} to "
                    f"{chunk.end_s:.3f} seconds ({chunk.duration_s:.3f} seconds) "
                    f"and contains {len(chunk.frames)} sampled frames."
                ),
            }
        ]
        for frame, encoded_frame in zip(chunk.frames, encoded_frames, strict=True):
            content.extend(
                (
                    {
                        "type": "text",
                        "text": (
                            "Frame timestamp: "
                            f"{frame.timestamp_ns / 1_000_000_000:.3f} seconds"
                        ),
                    },
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{encoded_frame}"},
                    },
                )
            )
        return [
            {"role": "system", "content": self._system_prompt},
            {"role": "user", "content": content},
        ]

    async def _encode_frame(self, frame: Frame) -> str:
        async with self._encoding_limiter:
            return await asyncio.to_thread(frame.to_base64, format="jpeg")


async def _process_video(
    source: SourceInput,
    *,
    source_id: str | None = None,
    prompt: str = DEFAULT_VLM_PROMPT,
    model: str | None = None,
    decoder: str | None = None,
    chunk_duration: int = 60,
    generation_config: dict[str, Any] | None = None,
    backend: str = "vlm",
    backend_instance: VLMBackend | None = None,
    checkpoint: _VLMCheckpoint | None = None,
) -> VlmCaptionsCompletionResponse:
    """Execute decode and inference under a permit acquired by the caller."""
    async with AsyncExitStack() as cleanup:
        vlm_backend = (
            await cleanup.enter_async_context(backend_instance)
            if backend_instance is not None
            else None
        )
        if chunk_duration < 1 or chunk_duration > 3600:
            raise ValueError("chunk_duration must be between 1 and 3600 seconds")
        selected_decoder = (
            decoder or os.getenv("VLM_DECODER", "auto").strip().lower() or "auto"
        )
        if checkpoint is None:
            facts = await probe_video(
                source,
                source_id=source_id,
                options=DecodeOptions(decoder=selected_decoder),
            )
            if facts.duration_s is not None and facts.duration_s <= 0:
                raise VLMBackendError(
                    "VLM processing requires a video with a positive duration"
                )
            resolved_id = facts.source_id
            duration_s = facts.duration_s if facts.duration_s is not None else math.inf
            targets = (
                _chunk_sample_timestamps_ns(duration_s, chunk_duration)
                if facts.duration_s is not None
                else ()
            )
        else:
            duration_s = checkpoint.duration_s
            assert source_id is not None
            resolved_id = source_id
            targets = checkpoint.sample_timestamps_ns
        resolved_source = _ResolvedVLMSource(
            source_id=resolved_id, url=_source_url(source)
        )
        if vlm_backend is None:
            vlm_backend = await cleanup.enter_async_context(get_vlm_backend(backend))
        responses: VlmCaptionsCompletionResponse = []
        frames: list[Frame] = []
        sampler = _BoundedFrameSampler()
        chunk_idx: int | None = None
        unknown_duration = checkpoint is None and not targets
        policy = (
            SamplingPolicy(mode="all")
            if unknown_duration
            else SamplingPolicy(
                mode="timestamps",
                timestamps_ns=targets,
                allow_missing_timestamps=True,
            )
        )
        stream = subsample_frames(
            source,
            policy,
            source_id=resolved_id,
            options=DecodeOptions(decoder=selected_decoder),
        )

        async def dispatch(idx: int, values: list[Frame]) -> None:
            if not values or (checkpoint is not None and checkpoint.is_confirmed(idx)):
                return
            response = await vlm_backend.infer(
                _frame_chunk(resolved_source, idx, chunk_duration, duration_s, values),
                prompt=prompt,
                model=model,
                generation_config=generation_config,
            )
            if response.error and (
                checkpoint is not None or backend_instance is not None
            ):
                raise VLMBackendError("VLM provider reported an error")
            if checkpoint is not None:
                await checkpoint.confirm(idx, response)
            responses.append(response)

        async with aclosing(stream):
            async for frame in stream:
                current_idx = frame.timestamp_ns // (chunk_duration * 1_000_000_000)
                if chunk_idx is not None and current_idx != chunk_idx:
                    await dispatch(
                        chunk_idx,
                        sampler.selected() if unknown_duration else frames,
                    )
                    frames = []
                    sampler = _BoundedFrameSampler()
                if checkpoint is not None and current_idx != chunk_idx:
                    await checkpoint.advance(current_idx)
                chunk_idx = current_idx
                if checkpoint is None or not checkpoint.is_confirmed(current_idx):
                    if unknown_duration:
                        sampler.add(frame)
                    else:
                        frames.append(frame)
        if chunk_idx is not None:
            if unknown_duration and sampler.last is not None:
                duration_s = (sampler.last.timestamp_ns + 1) / 1_000_000_000
            await dispatch(
                chunk_idx,
                sampler.selected() if unknown_duration else frames,
            )
        if checkpoint is not None:
            # Empty future windows are committed by the owner only after clean EOF.
            return await checkpoint.get_responses()
        if not responses:
            raise VLMBackendError("video processing produced no frames for VLM")
        if responses[-1].chunk is not None:
            responses[-1].chunk.is_last = True
        return responses


async def _process_shared_frames(
    source: SourceInput,
    frame_buffer: FrameBuffer,
    *,
    source_id: str,
    prompt: str = DEFAULT_VLM_PROMPT,
    model: str | None = None,
    chunk_duration: int = 60,
    generation_config: dict[str, Any] | None = None,
    backend: str = "vlm",
    backend_instance: VLMBackend | None = None,
) -> VlmCaptionsCompletionResponse:
    """Run VLM chunking over one independent shared-decode buffer."""

    if chunk_duration < 1 or chunk_duration > 3600:
        raise ValueError("chunk_duration must be between 1 and 3600 seconds")
    resolved_source = _ResolvedVLMSource(
        source_id=source_id,
        url=_source_url(source),
    )
    async with AsyncExitStack() as cleanup:
        vlm_backend = (
            await cleanup.enter_async_context(backend_instance)
            if backend_instance is not None
            else await cleanup.enter_async_context(get_vlm_backend(backend))
        )
        responses: VlmCaptionsCompletionResponse = []
        sampler = _BoundedFrameSampler()
        chunk_idx: int | None = None
        last_timestamp_ns: int | None = None

        async def dispatch(idx: int, values: list[Frame], duration_s: float) -> None:
            if not values:
                return
            response = await vlm_backend.infer(
                _frame_chunk(
                    resolved_source,
                    idx,
                    chunk_duration,
                    duration_s,
                    values,
                ),
                prompt=prompt,
                model=model,
                generation_config=generation_config,
            )
            if response.error and backend_instance is not None:
                raise VLMBackendError("VLM provider reported an error")
            responses.append(response)

        try:
            async for lease in frame_buffer:
                try:
                    frame = lease.frame
                    current_idx = frame.timestamp_ns // (chunk_duration * 1_000_000_000)
                    if chunk_idx is not None and current_idx != chunk_idx:
                        await dispatch(chunk_idx, sampler.selected(), math.inf)
                        sampler = _BoundedFrameSampler()
                    chunk_idx = current_idx
                    sampler.add(frame)
                    last_timestamp_ns = frame.timestamp_ns
                finally:
                    await lease.release()
        finally:
            await frame_buffer.aclose()

        if chunk_idx is not None and last_timestamp_ns is not None:
            duration_s = frame_buffer.duration_s
            if duration_s is None:
                duration_s = (last_timestamp_ns + 1) / 1_000_000_000
            await dispatch(chunk_idx, sampler.selected(), duration_s)
        if not responses:
            raise VLMBackendError("video processing produced no frames for VLM")
        if responses[-1].chunk is not None:
            responses[-1].chunk.is_last = True
        return responses


def _processing_limiter() -> asyncio.Semaphore:
    loop = asyncio.get_running_loop()
    configured: tuple[int, asyncio.Semaphore] | None = getattr(
        loop,
        _PROCESSING_LIMITER_ATTRIBUTE,
        None,
    )
    if configured is not None:
        return configured[1]

    limit_text = os.getenv(
        "VSS_PROCESSING_MAX_CONCURRENCY",
        str(DEFAULT_PROCESSING_MAX_CONCURRENCY),
    )
    try:
        limit = int(limit_text)
    except ValueError:
        raise ValueError(
            "VSS_PROCESSING_MAX_CONCURRENCY must be a positive integer"
        ) from None
    if limit < 1:
        raise ValueError("VSS_PROCESSING_MAX_CONCURRENCY must be a positive integer")

    configured = (limit, asyncio.Semaphore(limit))
    setattr(loop, _PROCESSING_LIMITER_ATTRIBUTE, configured)
    return configured[1]


def _source_url(source: SourceInput) -> str | None:
    if isinstance(source, (str, Path)):
        return str(source)
    return source.url


async def _duration_ns_from_decode(
    source: SourceInput,
    *,
    source_id: str,
    decoder: str,
) -> int:
    """Return an exclusive duration bound from observed presentation timestamps."""
    last_timestamp_ns: int | None = None
    stream = decode_frames(
        source,
        DecodePlan(),
        source_id=source_id,
        options=DecodeOptions(decoder=decoder),
    )
    async with aclosing(stream):
        async for frame in stream:
            if last_timestamp_ns is None or frame.timestamp_ns > last_timestamp_ns:
                last_timestamp_ns = frame.timestamp_ns
    if last_timestamp_ns is None:
        raise VLMBackendError("video processing produced no frames while planning")
    return last_timestamp_ns + 1


def _chunk_sample_timestamps_ns(
    duration_s: float,
    chunk_duration: int,
) -> tuple[int, ...]:
    duration_ns = round(duration_s * 1_000_000_000)
    chunk_duration_ns = chunk_duration * 1_000_000_000
    chunk_count = math.ceil(duration_ns / chunk_duration_ns)
    targets: list[int] = []
    for chunk_idx in range(chunk_count):
        start_ns = chunk_idx * chunk_duration_ns
        end_ns = min(start_ns + chunk_duration_ns, duration_ns)
        targets.extend(
            start_ns + (sample_idx * (end_ns - start_ns)) // DEFAULT_FRAMES_PER_CHUNK
            for sample_idx in range(DEFAULT_FRAMES_PER_CHUNK)
        )
    return tuple(targets)


def _frame_chunk(
    source: VLMSource,
    chunk_idx: int,
    chunk_duration: int,
    duration_s: float,
    frames: list[Frame],
) -> VLMFrameChunk:
    start_s = float(chunk_idx * chunk_duration)
    return VLMFrameChunk(
        source=source,
        chunk_idx=chunk_idx,
        start_s=start_s,
        end_s=min(start_s + chunk_duration, duration_s),
        frames=tuple(frames),
    )


def _completion_text(body: Any) -> str:
    choices = body["choices"]
    if not isinstance(choices, list) or not choices:
        raise ValueError("missing choices")
    content = choices[0]["message"]["content"]
    if isinstance(content, str) and content:
        return content
    if isinstance(content, list):
        text = "".join(
            str(part.get("text", ""))
            for part in content
            if isinstance(part, dict) and part.get("type") == "text"
        )
        if text:
            return text
    raise ValueError("missing message content")


def _error_detail(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return response.reason_phrase
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict):
            return str(error.get("message") or error)
        if error is not None:
            return str(error)
        for key in ("detail", "message"):
            if key in body:
                return str(body[key])
    return response.reason_phrase


register_vlm_backend(OpenAICompatibleVLMBackend.name, OpenAICompatibleVLMBackend)
