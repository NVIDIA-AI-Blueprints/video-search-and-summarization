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

"""Ingestion backend interface, registry, and RTVI GPU implementation."""

from __future__ import annotations

import asyncio
import os
import time
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Mapping
from typing import Any, Literal, Protocol
from uuid import uuid4

from vss.observability import measure
from vss.observability.context import correlate_source, resolve_current
from vss.observability.instrumentation import async_stage
from vss.video_processing.cuda_ipc import CudaIpcFrameSource
from vss.video_processing.frames import Frame
from vss.video_processing.shared_decode import (
    FrameBuffer,
    SharedFrameCapability,
)

from ._metrics import ChunkMetrics
from ._plan import IngestionPlan
from .contracts import (
    ChunkInfo,
    CvChunkResponse,
    CvCompletionResponse,
    CvDetection,
    EmbeddingChunkResponse,
    EmbeddingCompletionResponse,
    IngestionResponse,
    RequestSource,
    VlmCaptionsCompletionResponse,
    VlmChunkResponse,
    VlmModelInfo,
)
from .exceptions import (
    RTEmbedBackendError,
    RTVIBackendError,
    RTVICvBackendError,
    VLMBackendError,
)
from .rtvi import _RTVIEmbedTransport, _RTVITransport, _RTVITransportError
from .rtvi_cv import (
    _CvMessageConsumer,
    _parse_cv_detections,
    _payload_sensor_id,
    _payload_timestamp_ns,
    _RTVICvTransport,
    _RTVICvTransportError,
)


class IngestionBackend(ABC):
    """Interface implemented by VSS ingestion backends."""

    async def __aenter__(self) -> IngestionBackend:
        return self

    async def __aexit__(self, exc_type: object, *_: object) -> None:
        try:
            with measure("ingestion.cleanup", library="ingestion"):
                await self.aclose()
        except BaseException:
            if exc_type is None:
                raise

    async def aclose(self) -> None:
        """Release resources owned by the backend."""

        return None

    @abstractmethod
    async def ingest(
        self,
        source: RequestSource,
        *,
        prompt: str,
        model: str | None = None,
        chunk_duration: int = 0,
        chunk_overlap_duration: int = 0,
        generation_config: dict[str, Any] | None = None,
    ) -> IngestionResponse:
        """Ingest a source and return the backend-specific chunk responses."""

    @abstractmethod
    async def cancel(self, source_id: str) -> bool:
        """Cancel active ingestion for a source."""

    @abstractmethod
    async def check_health(self) -> bool:
        """Return whether the backend is ready."""

    @abstractmethod
    def capability_map(self) -> Mapping[str, Any]:
        """Describe backend capabilities."""

    def shared_frame_capability(self) -> SharedFrameCapability | None:
        """Return the accepted shared-frame contract, if one is implemented."""

        return None

    def shared_frame_capability_for_transport(
        self,
        transport: Literal["owned_cpu", "cuda_ipc"],
    ) -> SharedFrameCapability | None:
        """Return the shared-frame contract for an explicitly selected transport."""

        capability = self.shared_frame_capability()
        if capability is None or capability.transport != transport:
            return None
        return capability

    async def ingest_shared(
        self,
        source: RequestSource,
        frame_buffer: FrameBuffer,
        *,
        prompt: str,
        model: str | None = None,
        chunk_duration: int = 0,
        chunk_overlap_duration: int = 0,
        generation_config: dict[str, Any] | None = None,
    ) -> IngestionResponse:
        """Consume a coordinator buffer without opening another decoder."""

        del (
            source,
            frame_buffer,
            prompt,
            model,
            chunk_duration,
            chunk_overlap_duration,
            generation_config,
        )
        raise NotImplementedError(
            f"{type(self).__name__} does not support shared-frame ingestion"
        )

    async def ingest_cuda_ipc(
        self,
        source: RequestSource,
        frame_source: CudaIpcFrameSource,
        ready: asyncio.Event,
        *,
        prompt: str,
        model: str | None = None,
        chunk_duration: int = 0,
        chunk_overlap_duration: int = 0,
        generation_config: dict[str, Any] | None = None,
    ) -> IngestionResponse:
        """Attach a remote CUDA IPC consumer and process until broker EOS."""

        del (
            source,
            frame_source,
            ready,
            prompt,
            model,
            chunk_duration,
            chunk_overlap_duration,
            generation_config,
        )
        raise NotImplementedError(
            f"{type(self).__name__} does not support CUDA IPC ingestion"
        )


_BACKEND_REGISTRY: dict[str, type[IngestionBackend]] = {}


def register_backend(name: str, backend_type: type[IngestionBackend]) -> None:
    """Register an ingestion backend implementation."""

    if not name:
        raise ValueError("backend name must not be empty")
    if not issubclass(backend_type, IngestionBackend):
        raise TypeError("backend_type must implement IngestionBackend")
    _BACKEND_REGISTRY[name] = backend_type


def get_backend(name: str, **kwargs: Any) -> IngestionBackend:
    """Construct a registered backend by name."""

    try:
        backend_type = _BACKEND_REGISTRY[name]
    except KeyError as error:
        from .vlm import available_vlm_backends

        if name in available_vlm_backends():
            return VLMIngestionBackend(vlm_backend=name, **kwargs)
        available = ", ".join(available_backends()) or "none"
        raise KeyError(
            f"unknown ingestion backend {name!r}; available: {available}"
        ) from error
    return backend_type(**kwargs)


def available_backends() -> tuple[str, ...]:
    """Return registered backend names."""

    from .vlm import available_vlm_backends

    return tuple(sorted(set(_BACKEND_REGISTRY) | set(available_vlm_backends())))


def is_vlm_backend(name: str) -> bool:
    """Return whether a backend name selects a frame-level VLM provider."""

    registered = _BACKEND_REGISTRY.get(name)
    if registered is not None:
        return issubclass(registered, VLMIngestionBackend)
    from .vlm import available_vlm_backends

    return name in available_vlm_backends()


class _CaptionTransport(Protocol):
    """Private transport contract used by caption backends."""

    async def aclose(self) -> None: ...

    async def prepare_source(self, source: RequestSource) -> RequestSource: ...

    async def release_source(self, source: RequestSource) -> None: ...

    async def check_health(self) -> bool: ...

    async def get_model_info(self) -> VlmModelInfo: ...

    def generate_captions_stream(
        self,
        *,
        source_id: str,
        prompt: str,
        model: str,
        url: str | None = None,
        chunk_duration: int = 0,
        chunk_overlap_duration: int = 0,
        generation_config: dict[str, Any] | None = None,
    ) -> AsyncIterator[dict[str, Any]]: ...

    async def generate_frame_captions(
        self,
        *,
        frames: list[Frame],
        chunk_start_s: float,
        chunk_end_s: float,
        prompt: str,
        model: str,
        generation_config: dict[str, Any] | None = None,
    ) -> dict[str, Any]: ...

    async def add_shared_stream(
        self,
        *,
        source_id: str,
        camera_id: str,
        frame_source: dict[str, Any],
        metadata: dict[str, Any] | None = None,
    ) -> str: ...

    async def remove_shared_stream(self, *, camera_id: str) -> None: ...


class _EmbeddingTransport(Protocol):
    """Private transport contract used by the RTVI-Embed backend."""

    async def aclose(self) -> None: ...

    async def prepare_source(self, source: RequestSource) -> RequestSource: ...

    async def release_source(self, source: RequestSource) -> None: ...

    async def check_health(self) -> bool: ...

    async def get_model_info(self) -> VlmModelInfo: ...

    def generate_video_embeddings_stream(
        self,
        *,
        source_id: str,
        model: str,
        url: str | None = None,
        chunk_duration: int = 0,
        chunk_overlap_duration: int = 0,
    ) -> AsyncIterator[dict[str, Any]]: ...

    async def generate_frame_embeddings(
        self,
        *,
        frames: list[Frame],
        chunk_start_s: float,
        chunk_end_s: float,
        model: str,
    ) -> dict[str, Any]: ...

    async def add_shared_stream(
        self,
        *,
        source_id: str,
        camera_id: str,
        frame_source: dict[str, Any],
        metadata: dict[str, Any] | None = None,
    ) -> str: ...

    async def remove_shared_stream(self, *, camera_id: str) -> None: ...


class VLMIngestionBackend(IngestionBackend):
    """Caption ingestion through a selectable decoder and frame-level VLM."""

    name = "vlm"

    def __init__(
        self,
        *,
        vlm_backend: str = "vlm",
        decoder: str | None = None,
    ) -> None:
        from vss.video_processing import DecodeOptions

        self.vlm_backend = vlm_backend
        self.decoder = (
            decoder or os.getenv("VLM_DECODER", "auto").strip().lower() or "auto"
        )
        DecodeOptions(decoder=self.decoder)
        self._active_sources: dict[
            str, asyncio.Task[VlmCaptionsCompletionResponse]
        ] = {}

    async def aclose(self) -> None:
        for task in tuple(self._active_sources.values()):
            task.cancel()
        if self._active_sources:
            await asyncio.gather(*self._active_sources.values(), return_exceptions=True)

    async def ingest(
        self,
        source: RequestSource,
        *,
        prompt: str,
        model: str | None = None,
        chunk_duration: int = 60,
        chunk_overlap_duration: int = 0,
        generation_config: dict[str, Any] | None = None,
    ) -> VlmCaptionsCompletionResponse:
        if chunk_overlap_duration:
            raise ValueError(f"{self.name} does not support chunk overlap")
        current_task = asyncio.current_task()
        if current_task is None:
            raise VLMBackendError("ingest must run inside an asyncio task")
        if source.source_id in self._active_sources:
            raise VLMBackendError(
                f"source {source.source_id!r} is already being ingested"
            )
        self._active_sources[source.source_id] = current_task
        try:
            from .vlm import _process_video, _processing_limiter

            limiter = _processing_limiter()
            with measure("ingestion.vlm.capacity_wait", library="ingestion"):
                await limiter.acquire()
            try:
                return await _process_video(
                    source,
                    source_id=source.source_id,
                    prompt=prompt,
                    model=model,
                    decoder=self.decoder,
                    chunk_duration=chunk_duration,
                    generation_config=generation_config,
                    backend=self.vlm_backend,
                )
            finally:
                limiter.release()
        finally:
            self._active_sources.pop(source.source_id, None)

    async def ingest_shared(
        self,
        source: RequestSource,
        frame_buffer: FrameBuffer,
        *,
        prompt: str,
        model: str | None = None,
        chunk_duration: int = 60,
        chunk_overlap_duration: int = 0,
        generation_config: dict[str, Any] | None = None,
    ) -> VlmCaptionsCompletionResponse:
        if chunk_overlap_duration:
            raise ValueError(f"{self.name} does not support chunk overlap")
        current_task = asyncio.current_task()
        if current_task is None:
            raise VLMBackendError("ingest must run inside an asyncio task")
        if source.source_id in self._active_sources:
            raise VLMBackendError(
                f"source {source.source_id!r} is already being ingested"
            )
        self._active_sources[source.source_id] = current_task
        try:
            from .vlm import _process_shared_frames

            return await _process_shared_frames(
                source,
                frame_buffer,
                source_id=source.source_id,
                prompt=prompt,
                model=model,
                chunk_duration=chunk_duration,
                generation_config=generation_config,
                backend=self.vlm_backend,
            )
        finally:
            self._active_sources.pop(source.source_id, None)

    async def cancel(self, source_id: str) -> bool:
        task = self._active_sources.get(source_id)
        if task is None or task.done():
            return False
        task.cancel()
        return True

    async def check_health(self) -> bool:
        from .vlm import get_vlm_backend

        async with get_vlm_backend(self.vlm_backend) as backend:
            return await backend.check_health()

    def capability_map(self) -> Mapping[str, Any]:
        shared = self.shared_frame_capability()
        assert shared is not None
        return {
            "backend": self.name,
            "pipeline": "vlm",
            "vlm_backend": self.vlm_backend,
            "modalities": ("video",),
            "outputs": ("caption",),
            "streaming_response": False,
            "decoder": self.decoder,
            "shared_frames": {
                "supported": True,
                "protocol_version": shared.protocol_version,
                "transport": shared.transport,
                "pixel_formats": (shared.pixel_format,),
                "device_ids": () if shared.device_id is None else (shared.device_id,),
                "max_buffer_depth": shared.max_buffer_depth,
                "live_streams": shared.live_streams,
            },
        }

    def shared_frame_capability(self) -> SharedFrameCapability:
        return SharedFrameCapability(decoder=self.decoder)


class CPUVLMBackend(VLMIngestionBackend):
    """Frame-level VLM ingestion with an OpenCV decoder."""

    name = "cpu_vlm"

    def __init__(
        self,
        *,
        vlm_backend: str = "vlm",
        decoder: str | None = None,
    ) -> None:
        del decoder
        super().__init__(vlm_backend=vlm_backend, decoder="opencv")


class GPUVLMBackend(VLMIngestionBackend):
    """Frame-level VLM ingestion with a PyNvVideoCodec decoder."""

    name = "gpu_vlm"

    def __init__(
        self,
        *,
        vlm_backend: str = "vlm",
        decoder: str | None = None,
    ) -> None:
        del decoder
        super().__init__(vlm_backend=vlm_backend, decoder="pynv")


class RTVIBackend(IngestionBackend):
    """Remote caption ingestion backend implemented through RTVI."""

    name = "rtvi"

    def __init__(self, transport: _CaptionTransport | None = None) -> None:
        self._transport = transport if transport is not None else _RTVITransport()
        self._owns_transport = transport is None
        self._active_sources: dict[
            str, asyncio.Task[VlmCaptionsCompletionResponse]
        ] = {}

    async def __aenter__(self) -> RTVIBackend:
        return self

    async def __aexit__(self, exc_type: object, *_: object) -> None:
        try:
            with measure("ingestion.cleanup", library="ingestion"):
                await self.aclose()
        except BaseException:
            if exc_type is None:
                raise

    async def aclose(self) -> None:
        for task in tuple(self._active_sources.values()):
            task.cancel()
        if self._active_sources:
            await asyncio.gather(*self._active_sources.values(), return_exceptions=True)
        if self._owns_transport:
            await self._transport.aclose()

    @async_stage("ingestion.backend", library="ingestion")
    async def ingest(
        self,
        source: RequestSource,
        *,
        prompt: str,
        model: str | None = None,
        chunk_duration: int = 0,
        chunk_overlap_duration: int = 0,
        generation_config: dict[str, Any] | None = None,
    ) -> VlmCaptionsCompletionResponse:
        current_task = asyncio.current_task()
        if current_task is None:
            raise RTVIBackendError("ingest must run inside an asyncio task")
        if source.source_id in self._active_sources:
            raise RTVIBackendError(
                f"source {source.source_id!r} is already being ingested"
            )

        self._active_sources[source.source_id] = current_task
        responses: dict[int, VlmChunkResponse] = {}
        prepared_source: RequestSource | None = None
        primary_failed = False
        complete = False
        metrics = ChunkMetrics(embedding=self.name == "rtvi_embed")
        selected_metric_model = model
        try:
            with measure("ingestion.prepare", library="ingestion"):
                prepared_source = await self._transport.prepare_source(source)
            correlate_source(source.source_id, prepared_source.source_id)
            with measure("ingestion.model_resolution", library="ingestion"):
                if model is None:
                    model = (await self._transport.get_model_info()).id
            selected_metric_model = model
            resolve_current(model=model, backend=self.name)
            plan = IngestionPlan(
                source=prepared_source,
                prompt=prompt,
                model=model,
                chunk_duration=chunk_duration,
                chunk_overlap_duration=chunk_overlap_duration,
            )
            async for payload in self._transport.generate_captions_stream(
                source_id=plan.source.source_id,
                url=plan.source.url,
                prompt=plan.prompt,
                model=plan.model,
                chunk_duration=plan.chunk_duration,
                chunk_overlap_duration=plan.chunk_overlap_duration,
                generation_config=generation_config,
            ):
                if not isinstance(payload, dict):
                    raise RTVIBackendError("RTVI returned an invalid caption response")
                for chunk_payload in payload.get("chunk_responses", ()):
                    if not isinstance(chunk_payload, dict):
                        raise RTVIBackendError("RTVI returned an invalid caption chunk")
                    response = _to_chunk_response(plan.source, chunk_payload)
                    assert response.chunk is not None
                    responses[response.chunk.chunkIdx] = response
                    metrics.observe(
                        str(response.chunk.chunkIdx),
                        response.chunk.chunkIdx,
                        chunk_payload,
                    )
            complete = True
        except _RTVITransportError:
            primary_failed = True
            raise RTVIBackendError("RTVI request failed; check the provider") from None
        except (ValueError, TypeError, KeyError):
            primary_failed = True
            raise RTVIBackendError(
                "RTVI returned an invalid caption response"
            ) from None
        except BaseException:
            primary_failed = True
            raise
        finally:
            self._active_sources.pop(source.source_id, None)
            if prepared_source is not None:
                try:
                    with measure("ingestion.release", library="ingestion"):
                        await self._transport.release_source(prepared_source)
                except _RTVITransportError:
                    if not primary_failed:
                        metrics.finish(complete=False, model=selected_metric_model)
                        raise RTVIBackendError("RTVI upload cleanup failed") from None
                except BaseException:
                    if not primary_failed:
                        metrics.finish(complete=False, model=selected_metric_model)
                        raise
            if not complete:
                metrics.finish(complete=False, model=selected_metric_model)

        with measure("ingestion.assemble", library="ingestion"):
            ordered = [responses[index] for index in sorted(responses)]
        metrics.finish(complete=True, model=selected_metric_model)
        if ordered:
            assert ordered[-1].chunk is not None
            ordered[-1].chunk.is_last = True
        return ordered

    async def ingest_shared(
        self,
        source: RequestSource,
        frame_buffer: FrameBuffer,
        *,
        prompt: str,
        model: str | None = None,
        chunk_duration: int = 60,
        chunk_overlap_duration: int = 0,
        generation_config: dict[str, Any] | None = None,
    ) -> VlmCaptionsCompletionResponse:
        """Caption bounded samples from the library-owned decoder."""

        if chunk_overlap_duration:
            raise ValueError("rtvi does not support shared-frame chunk overlap")
        if chunk_duration < 1 or chunk_duration > 3600:
            raise ValueError("chunk_duration must be between 1 and 3600 seconds")

        current_task = asyncio.current_task()
        if current_task is None:
            raise RTVIBackendError("ingest_shared must run inside an asyncio task")
        if source.source_id in self._active_sources:
            raise RTVIBackendError(
                f"source {source.source_id!r} is already being ingested"
            )

        self._active_sources[source.source_id] = current_task
        selected_model = model
        responses: VlmCaptionsCompletionResponse = []
        sampler = _BoundedSharedFrameSampler()
        chunk_idx: int | None = None
        last_timestamp_ns: int | None = None

        async def dispatch(idx: int, frames: list[Frame], duration_s: float) -> None:
            if not frames:
                return
            start_s = idx * chunk_duration
            end_s = min(start_s + chunk_duration, duration_s)
            started = time.time()
            payload = await self._transport.generate_frame_captions(
                frames=frames,
                chunk_start_s=start_s,
                chunk_end_s=max(start_s, end_s),
                prompt=prompt,
                model=selected_model or "",
                generation_config=generation_config,
            )
            completed = time.time()
            response = VlmChunkResponse(
                chunk=ChunkInfo(
                    sourceId=source.source_id,
                    chunkIdx=idx,
                    file=source.url or "",
                    start_pts=round(start_s * 1_000_000_000),
                    end_pts=round(max(start_s, end_s) * 1_000_000_000),
                    is_first=idx == 0,
                ),
                vlm_response=str(payload.get("content") or ""),
                vlm_start_time=started,
                vlm_end_time=completed,
                processing_latency=completed - started,
                rtvi_frame_count=len(frames),
                vlm_stats={
                    "input_tokens": int(payload.get("input_tokens") or 0),
                    "output_tokens": int(payload.get("output_tokens") or 0),
                },
            )
            if payload.get("error"):
                response.error = "RTVI reported a chunk error"
            responses.append(response)

        try:
            if selected_model is None:
                selected_model = (await self._transport.get_model_info()).id
            if not selected_model:
                raise RTVIBackendError("RTVI returned no model information")

            async for lease in frame_buffer:
                try:
                    frame = lease.frame
                    current_idx = frame.timestamp_ns // (chunk_duration * 1_000_000_000)
                    if chunk_idx is not None and current_idx != chunk_idx:
                        await dispatch(chunk_idx, sampler.selected(), float("inf"))
                        sampler = _BoundedSharedFrameSampler()
                    chunk_idx = current_idx
                    sampler.add(frame)
                    last_timestamp_ns = frame.timestamp_ns
                finally:
                    await lease.release()
            if chunk_idx is not None and last_timestamp_ns is not None:
                duration_s = frame_buffer.duration_s
                if duration_s is None:
                    duration_s = (last_timestamp_ns + 1) / 1_000_000_000
                await dispatch(chunk_idx, sampler.selected(), duration_s)
        except _RTVITransportError:
            raise RTVIBackendError(
                "RTVI shared-frame request failed; check the provider"
            ) from None
        finally:
            self._active_sources.pop(source.source_id, None)
            await frame_buffer.aclose()

        if not responses:
            raise RTVIBackendError(
                "shared video processing produced no frames for RTVI"
            )
        if responses[-1].chunk is not None:
            responses[-1].chunk.is_last = True
        return responses

    async def ingest_cuda_ipc(
        self,
        source: RequestSource,
        frame_source: CudaIpcFrameSource,
        ready: asyncio.Event,
        *,
        prompt: str,
        model: str | None = None,
        chunk_duration: int = 60,
        chunk_overlap_duration: int = 0,
        generation_config: dict[str, Any] | None = None,
    ) -> VlmCaptionsCompletionResponse:
        """Run brokered NV12 frames through RTVI's normal live VLM pipeline."""

        current_task = asyncio.current_task()
        if current_task is None:
            raise RTVIBackendError("ingest_cuda_ipc must run inside an asyncio task")
        if source.source_id in self._active_sources:
            raise RTVIBackendError(
                f"source {source.source_id!r} is already being ingested"
            )
        self._active_sources[source.source_id] = current_task
        camera_id = _request_camera_id(source.source_id)
        registration_attempted = False
        primary_error: BaseException | None = None
        responses: dict[int, VlmChunkResponse] = {}
        try:
            selected_model = model or (await self._transport.get_model_info()).id
            registration_attempted = True
            asset_id = await self._transport.add_shared_stream(
                source_id=source.source_id,
                camera_id=camera_id,
                frame_source=frame_source.as_dict(),
                metadata={
                    "prompt": prompt,
                    "model": selected_model,
                    "chunk_duration": chunk_duration,
                    "chunk_overlap_duration": chunk_overlap_duration,
                },
            )
            ready.set()
            async for payload in self._transport.generate_captions_stream(
                source_id=asset_id,
                prompt=prompt,
                model=selected_model,
                chunk_duration=chunk_duration,
                chunk_overlap_duration=chunk_overlap_duration,
                generation_config=generation_config,
            ):
                for chunk_payload in payload.get("chunk_responses", ()):
                    if not isinstance(chunk_payload, dict):
                        raise RTVIBackendError(
                            "RTVI returned an invalid CUDA IPC caption chunk"
                        )
                    response = _to_chunk_response(source, chunk_payload)
                    assert response.chunk is not None
                    responses[response.chunk.chunkIdx] = response
        except BaseException as error:
            primary_error = error
            if isinstance(error, _RTVITransportError):
                raise RTVIBackendError(
                    "RTVI CUDA IPC request failed; check the provider"
                ) from None
            raise
        finally:
            self._active_sources.pop(source.source_id, None)
            if registration_attempted:
                try:
                    await self._transport.remove_shared_stream(camera_id=camera_id)
                except _RTVITransportError:
                    if primary_error is None:
                        raise RTVIBackendError(
                            "RTVI CUDA IPC stream cleanup failed"
                        ) from None
        ordered = [responses[index] for index in sorted(responses)]
        if not ordered:
            raise RTVIBackendError("RTVI CUDA IPC stream produced no captions")
        assert ordered[-1].chunk is not None
        ordered[-1].chunk.is_last = True
        return ordered

    async def cancel(self, source_id: str) -> bool:
        task = self._active_sources.get(source_id)
        if task is None or task.done():
            return False
        task.cancel()
        return True

    async def check_health(self) -> bool:
        return await self._transport.check_health()

    def capability_map(self) -> Mapping[str, Any]:
        shared = self.shared_frame_capability()
        return {
            "backend": self.name,
            "pipeline": "rtvi",
            "modalities": ("video",),
            "outputs": ("caption",),
            "streaming_response": True,
            "requires_gpu": True,
            "shared_frames": {
                "supported": True,
                "protocol_version": shared.protocol_version,
                "transport": shared.transport,
                "pixel_formats": (shared.pixel_format,),
                "device_ids": () if shared.device_id is None else (shared.device_id,),
                "max_buffer_depth": shared.max_buffer_depth,
                "live_streams": shared.live_streams,
            },
        }

    def shared_frame_capability(self) -> SharedFrameCapability:
        """Accept the configured PyNv shared-frame transport."""

        return _rtvi_shared_capability()

    def shared_frame_capability_for_transport(
        self,
        transport: Literal["owned_cpu", "cuda_ipc"],
    ) -> SharedFrameCapability:
        return _rtvi_shared_capability(transport)


class RTEmbedBackend(IngestionBackend):
    """Video embedding ingestion backend implemented through RTVI-Embed."""

    name = "rtvi_embed"

    def __init__(self, transport: _EmbeddingTransport | None = None) -> None:
        self._transport = transport if transport is not None else _RTVIEmbedTransport()
        self._owns_transport = transport is None
        self._active_sources: dict[str, asyncio.Task[EmbeddingCompletionResponse]] = {}

    async def __aenter__(self) -> RTEmbedBackend:
        return self

    async def __aexit__(self, exc_type: object, *_: object) -> None:
        try:
            with measure("ingestion.cleanup", library="ingestion"):
                await self.aclose()
        except BaseException:
            if exc_type is None:
                raise

    async def aclose(self) -> None:
        for task in tuple(self._active_sources.values()):
            task.cancel()
        if self._active_sources:
            await asyncio.gather(*self._active_sources.values(), return_exceptions=True)
        if self._owns_transport:
            await self._transport.aclose()

    @async_stage("ingestion.backend", library="ingestion")
    async def ingest(
        self,
        source: RequestSource,
        *,
        prompt: str,
        model: str | None = None,
        chunk_duration: int = 0,
        chunk_overlap_duration: int = 0,
        generation_config: dict[str, Any] | None = None,
    ) -> EmbeddingCompletionResponse:
        del prompt
        if generation_config:
            raise ValueError(
                "generation_config is not supported by the RTVI-Embed backend"
            )
        current_task = asyncio.current_task()
        if current_task is None:
            raise RTEmbedBackendError("ingest must run inside an asyncio task")
        if source.source_id in self._active_sources:
            raise RTEmbedBackendError(
                f"source {source.source_id!r} is already being ingested"
            )

        self._active_sources[source.source_id] = current_task
        chunks: dict[tuple[str, str], dict[str, Any]] = {}
        prepared_source: RequestSource | None = None
        primary_failed = False
        complete = False
        metrics = ChunkMetrics(embedding=self.name == "rtvi_embed")
        selected_metric_model = model
        try:
            with measure("ingestion.prepare", library="ingestion"):
                prepared_source = await self._transport.prepare_source(source)
            correlate_source(source.source_id, prepared_source.source_id)
            with measure("ingestion.model_resolution", library="ingestion"):
                selected_model = model or (await self._transport.get_model_info()).id
            selected_metric_model = selected_model
            resolve_current(model=selected_model, backend=self.name)
            if not selected_model:
                raise RTEmbedBackendError("RTVI-Embed returned no model information")
            async for payload in self._transport.generate_video_embeddings_stream(
                source_id=prepared_source.source_id,
                url=prepared_source.url,
                model=selected_model,
                chunk_duration=chunk_duration,
                chunk_overlap_duration=chunk_overlap_duration,
            ):
                for chunk_payload in payload.get("chunk_responses", ()):
                    if not isinstance(chunk_payload, dict):
                        raise RTEmbedBackendError(
                            "RTVI-Embed returned an invalid chunk response"
                        )
                    key = (
                        str(chunk_payload.get("start_time", "")),
                        str(chunk_payload.get("end_time", "")),
                    )
                    chunks[key] = chunk_payload
                    metrics.observe("\x00".join(key), len(chunks) - 1, chunk_payload)
            complete = True
        except _RTVITransportError:
            primary_failed = True
            raise RTEmbedBackendError(
                "RTVI-Embed request failed; check the provider"
            ) from None
        except BaseException:
            primary_failed = True
            raise
        finally:
            self._active_sources.pop(source.source_id, None)
            if prepared_source is not None:
                try:
                    with measure("ingestion.release", library="ingestion"):
                        await self._transport.release_source(prepared_source)
                except _RTVITransportError:
                    if not primary_failed:
                        metrics.finish(complete=False, model=selected_metric_model)
                        raise RTEmbedBackendError("RTVI-Embed cleanup failed") from None
                except BaseException:
                    if not primary_failed:
                        metrics.finish(complete=False, model=selected_metric_model)
                        raise
            if not complete:
                metrics.finish(complete=False, model=selected_metric_model)

        try:
            with measure("ingestion.assemble", library="ingestion"):
                ordered_payloads = sorted(
                    chunks.values(), key=_embedding_chunk_sort_key
                )
                if not ordered_payloads:
                    raise RTEmbedBackendError(
                        "RTVI-Embed completed without producing an embedding chunk"
                    )
                responses = [
                    _to_embedding_response(source, payload, index)
                    for index, payload in enumerate(ordered_payloads)
                ]
        except BaseException:
            metrics.finish(complete=False, model=selected_metric_model)
            raise
        for index, payload in enumerate(ordered_payloads):
            key = (
                str(payload.get("start_time", ""))
                + "\x00"
                + str(payload.get("end_time", ""))
            )
            metrics.reindex(key, index)
        metrics.finish(complete=True, model=selected_metric_model)
        if responses:
            responses[-1].chunk.is_last = True
        return responses

    async def ingest_shared(
        self,
        source: RequestSource,
        frame_buffer: FrameBuffer,
        *,
        prompt: str,
        model: str | None = None,
        chunk_duration: int = 60,
        chunk_overlap_duration: int = 0,
        generation_config: dict[str, Any] | None = None,
    ) -> EmbeddingCompletionResponse:
        """Embed bounded frame samples from the library-owned decoder."""

        del prompt
        if generation_config:
            raise ValueError(
                "generation_config is not supported by the RTVI-Embed backend"
            )
        if chunk_overlap_duration:
            raise ValueError("rtvi_embed does not support shared-frame chunk overlap")
        if chunk_duration < 1 or chunk_duration > 3600:
            raise ValueError("chunk_duration must be between 1 and 3600 seconds")

        current_task = asyncio.current_task()
        if current_task is None:
            raise RTEmbedBackendError("ingest_shared must run inside an asyncio task")
        if source.source_id in self._active_sources:
            raise RTEmbedBackendError(
                f"source {source.source_id!r} is already being ingested"
            )

        self._active_sources[source.source_id] = current_task
        selected_model = model
        responses: EmbeddingCompletionResponse = []
        sampler = _BoundedSharedFrameSampler()
        chunk_idx: int | None = None
        last_timestamp_ns: int | None = None

        async def dispatch(idx: int, frames: list[Frame], duration_s: float) -> None:
            if not frames:
                return
            start_s = idx * chunk_duration
            end_s = min(start_s + chunk_duration, duration_s)
            started = time.time()
            payload = await self._transport.generate_frame_embeddings(
                frames=frames,
                chunk_start_s=start_s,
                chunk_end_s=max(start_s, end_s),
                model=selected_model or "",
            )
            completed = time.time()
            normalized = {
                **payload,
                "start_time": str(start_s),
                "end_time": str(max(start_s, end_s)),
                "frame_count": len(frames),
                "inference_latency_ms": (completed - started) * 1000,
            }
            responses.append(_to_embedding_response(source, normalized, idx))

        try:
            if selected_model is None:
                selected_model = (await self._transport.get_model_info()).id
            if not selected_model:
                raise RTEmbedBackendError("RTVI-Embed returned no model information")

            async for lease in frame_buffer:
                try:
                    frame = lease.frame
                    current_idx = frame.timestamp_ns // (chunk_duration * 1_000_000_000)
                    if chunk_idx is not None and current_idx != chunk_idx:
                        await dispatch(chunk_idx, sampler.selected(), float("inf"))
                        sampler = _BoundedSharedFrameSampler()
                    chunk_idx = current_idx
                    sampler.add(frame)
                    last_timestamp_ns = frame.timestamp_ns
                finally:
                    await lease.release()
            if chunk_idx is not None and last_timestamp_ns is not None:
                duration_s = frame_buffer.duration_s
                if duration_s is None:
                    duration_s = (last_timestamp_ns + 1) / 1_000_000_000
                await dispatch(chunk_idx, sampler.selected(), duration_s)
        except _RTVITransportError:
            raise RTEmbedBackendError(
                "RTVI-Embed shared-frame request failed; check the provider"
            ) from None
        finally:
            self._active_sources.pop(source.source_id, None)
            await frame_buffer.aclose()

        if not responses:
            raise RTEmbedBackendError(
                "shared video processing produced no frames for RTVI-Embed"
            )
        responses[-1].chunk.is_last = True
        return responses

    async def ingest_cuda_ipc(
        self,
        source: RequestSource,
        frame_source: CudaIpcFrameSource,
        ready: asyncio.Event,
        *,
        prompt: str,
        model: str | None = None,
        chunk_duration: int = 60,
        chunk_overlap_duration: int = 0,
        generation_config: dict[str, Any] | None = None,
    ) -> EmbeddingCompletionResponse:
        """Run brokered NV12 frames through the normal RTVI-Embed pipeline."""

        del prompt
        if generation_config:
            raise ValueError(
                "generation_config is not supported by the RTVI-Embed backend"
            )
        current_task = asyncio.current_task()
        if current_task is None:
            raise RTEmbedBackendError("ingest_cuda_ipc must run inside an asyncio task")
        if source.source_id in self._active_sources:
            raise RTEmbedBackendError(
                f"source {source.source_id!r} is already being ingested"
            )
        self._active_sources[source.source_id] = current_task
        camera_id = _request_camera_id(source.source_id)
        registration_attempted = False
        primary_error: BaseException | None = None
        chunks: dict[tuple[str, str], dict[str, Any]] = {}
        try:
            selected_model = model or (await self._transport.get_model_info()).id
            registration_attempted = True
            asset_id = await self._transport.add_shared_stream(
                source_id=source.source_id,
                camera_id=camera_id,
                frame_source=frame_source.as_dict(),
                metadata={
                    "model": selected_model,
                    "chunk_duration": chunk_duration,
                    "chunk_overlap_duration": chunk_overlap_duration,
                },
            )
            ready.set()
            async for payload in self._transport.generate_video_embeddings_stream(
                source_id=asset_id,
                model=selected_model,
                chunk_duration=chunk_duration,
                chunk_overlap_duration=chunk_overlap_duration,
            ):
                for chunk_payload in payload.get("chunk_responses", ()):
                    if not isinstance(chunk_payload, dict):
                        raise RTEmbedBackendError(
                            "RTVI-Embed returned an invalid CUDA IPC chunk"
                        )
                    key = (
                        str(chunk_payload.get("start_time", "")),
                        str(chunk_payload.get("end_time", "")),
                    )
                    chunks[key] = chunk_payload
        except BaseException as error:
            primary_error = error
            if isinstance(error, _RTVITransportError):
                raise RTEmbedBackendError(
                    "RTVI-Embed CUDA IPC request failed; check the provider"
                ) from None
            raise
        finally:
            self._active_sources.pop(source.source_id, None)
            if registration_attempted:
                try:
                    await self._transport.remove_shared_stream(camera_id=camera_id)
                except _RTVITransportError:
                    if primary_error is None:
                        raise RTEmbedBackendError(
                            "RTVI-Embed CUDA IPC stream cleanup failed"
                        ) from None
        ordered_payloads = sorted(chunks.values(), key=_embedding_chunk_sort_key)
        if not ordered_payloads:
            raise RTEmbedBackendError("RTVI-Embed CUDA IPC stream produced no chunks")
        responses = [
            _to_embedding_response(source, payload, index)
            for index, payload in enumerate(ordered_payloads)
        ]
        responses[-1].chunk.is_last = True
        return responses

    async def cancel(self, source_id: str) -> bool:
        task = self._active_sources.get(source_id)
        if task is None or task.done():
            return False
        task.cancel()
        return True

    async def check_health(self) -> bool:
        return await self._transport.check_health()

    def capability_map(self) -> Mapping[str, Any]:
        shared = self.shared_frame_capability()
        return {
            "backend": self.name,
            "pipeline": "rt_embed",
            "modalities": ("video",),
            "outputs": ("embedding",),
            "streaming_response": True,
            "requires_gpu": True,
            "shared_frames": {
                "supported": True,
                "protocol_version": shared.protocol_version,
                "transport": shared.transport,
                "pixel_formats": (shared.pixel_format,),
                "device_ids": () if shared.device_id is None else (shared.device_id,),
                "max_buffer_depth": shared.max_buffer_depth,
                "live_streams": shared.live_streams,
            },
        }

    def shared_frame_capability(self) -> SharedFrameCapability:
        """Accept the configured PyNv shared-frame transport."""

        return _rtvi_shared_capability()

    def shared_frame_capability_for_transport(
        self,
        transport: Literal["owned_cpu", "cuda_ipc"],
    ) -> SharedFrameCapability:
        return _rtvi_shared_capability(transport)


class RTVICvBackend(IngestionBackend):
    """RTVI-CV decoded-frame backend with Kafka detection results."""

    name = "rtvi_cv"

    def __init__(self, transport: _RTVICvTransport | None = None) -> None:
        self._transport = transport if transport is not None else _RTVICvTransport()
        self._owns_transport = transport is None
        self._active_sources: dict[str, asyncio.Task[CvCompletionResponse]] = {}

    async def aclose(self) -> None:
        for task in tuple(self._active_sources.values()):
            task.cancel()
        if self._active_sources:
            await asyncio.gather(*self._active_sources.values(), return_exceptions=True)
        if self._owns_transport:
            await self._transport.aclose()

    async def ingest(
        self,
        source: RequestSource,
        *,
        prompt: str,
        model: str | None = None,
        chunk_duration: int = 60,
        chunk_overlap_duration: int = 0,
        generation_config: dict[str, Any] | None = None,
    ) -> CvCompletionResponse:
        del source, prompt, model, chunk_duration, chunk_overlap_duration
        del generation_config
        raise RTVICvBackendError(
            "rtvi_cv requires single_decode=True with cuda_ipc transport"
        )

    async def ingest_cuda_ipc(
        self,
        source: RequestSource,
        frame_source: CudaIpcFrameSource,
        ready: asyncio.Event,
        *,
        prompt: str,
        model: str | None = None,
        chunk_duration: int = 60,
        chunk_overlap_duration: int = 0,
        generation_config: dict[str, Any] | None = None,
    ) -> CvCompletionResponse:
        del prompt, model
        if generation_config:
            raise ValueError("generation_config is not supported by rtvi_cv")
        if chunk_overlap_duration:
            raise ValueError("rtvi_cv does not support chunk overlap")
        if chunk_duration < 1 or chunk_duration > 3600:
            raise ValueError("chunk_duration must be between 1 and 3600 seconds")
        current_task = asyncio.current_task()
        if current_task is None:
            raise RTVICvBackendError("ingest_cuda_ipc must run inside an asyncio task")
        if source.source_id in self._active_sources:
            raise RTVICvBackendError(
                f"source {source.source_id!r} is already being ingested"
            )
        self._active_sources[source.source_id] = current_task
        camera_id = _request_camera_id(source.source_id)
        consumer: _CvMessageConsumer | None = None
        registration_attempted = False
        detections: list[CvDetection] = []
        stream_end_ns = frame_source.duration_ns
        final_results_boundary_ns: int | None = None
        timestamp_origin_ns = frame_source.timestamp_origin_ns
        final_result_pts_ns: int | None = None
        result_message_count = 0
        eos_deadline: float | None = None
        started = time.time()
        primary_error: BaseException | None = None
        try:
            consumer = self._transport.create_consumer(camera_id)
            await consumer.start()
            registration_attempted = True
            await self._transport.add_shared_stream(
                source_id=source.source_id,
                camera_id=camera_id,
                frame_source=frame_source.as_dict(),
            )
            ready.set()
            while True:
                payload = await consumer.receive(0.2)
                if payload is not None:
                    sensor_id = _payload_sensor_id(payload)
                    if sensor_id in {camera_id, source.source_id}:
                        result_message_count += 1
                        payload_timestamp_ns = _payload_timestamp_ns(payload)
                        if payload_timestamp_ns is not None:
                            final_result_pts_ns = payload_timestamp_ns
                        parsed = _parse_cv_detections(
                            payload,
                            fallback_timestamp_ns=(
                                stream_end_ns if stream_end_ns is not None else 0
                            ),
                        )
                        detections.extend(parsed)
                status = await self._transport.stream_status(camera_id)
                state = str(status.get("status", "")).lower()
                status_origin = status.get("timestamp_origin_ns")
                if (
                    isinstance(status_origin, int)
                    and not isinstance(status_origin, bool)
                    and status_origin >= 0
                ):
                    timestamp_origin_ns = status_origin
                status_pts = status.get("last_pts_ns")
                if (
                    isinstance(status_pts, int)
                    and not isinstance(status_pts, bool)
                    and status_pts >= 0
                ):
                    status_pts = _relative_cv_timestamp(
                        status_pts,
                        timestamp_origin_ns=timestamp_origin_ns,
                    )
                else:
                    status_pts = None
                if status_pts is not None:
                    final_results_boundary_ns = (
                        status_pts
                        if final_results_boundary_ns is None
                        else max(final_results_boundary_ns, status_pts)
                    )
                    stream_end_ns = (
                        status_pts
                        if stream_end_ns is None
                        else max(stream_end_ns, status_pts)
                    )
                if state in {"error", "failed"}:
                    raise RTVICvBackendError("RTVI-CV shared stream failed")
                if state in {"eos", "completed", "finished"}:
                    expected_count = status.get("final_results_count")
                    final_count = (
                        expected_count
                        if isinstance(expected_count, int) and expected_count >= 0
                        else None
                    )
                    if final_count is not None:
                        if result_message_count >= final_count:
                            break
                    else:
                        final_results_pts = status.get("final_results_pts_ns")
                        if (
                            not isinstance(final_results_pts, int)
                            or isinstance(final_results_pts, bool)
                            or final_results_pts < 0
                        ):
                            final_results_pts = (
                                final_results_boundary_ns
                                if final_results_boundary_ns is not None
                                else stream_end_ns
                            )
                        else:
                            final_results_pts = _relative_cv_timestamp(
                                final_results_pts,
                                timestamp_origin_ns=timestamp_origin_ns,
                            )
                        consumed_pts = _relative_cv_timestamp(
                            final_result_pts_ns,
                            timestamp_origin_ns=timestamp_origin_ns,
                        )
                        if final_results_pts is not None and consumed_pts is not None:
                            if consumed_pts >= final_results_pts:
                                break
                    if eos_deadline is None:
                        eos_deadline = time.monotonic() + 30
                    elif time.monotonic() >= eos_deadline:
                        raise RTVICvBackendError(
                            "RTVI-CV ended before final stream results were confirmed"
                        )
        except BaseException as error:
            primary_error = error
            if isinstance(error, _RTVICvTransportError):
                raise RTVICvBackendError(
                    "RTVI-CV shared-frame request failed"
                ) from None
            raise
        finally:
            self._active_sources.pop(source.source_id, None)
            cleanup_error: BaseException | None = None
            try:
                if registration_attempted:
                    await self._transport.remove_shared_stream(camera_id)
            except _RTVICvTransportError as error:
                cleanup_error = error
            finally:
                if consumer is not None:
                    try:
                        await consumer.aclose()
                    except BaseException as error:
                        cleanup_error = cleanup_error or error
            if cleanup_error is not None and primary_error is None:
                if isinstance(cleanup_error, _RTVICvTransportError):
                    raise RTVICvBackendError("RTVI-CV stream cleanup failed") from None
                raise cleanup_error
        return _cv_chunk_responses(
            source,
            detections,
            chunk_duration=chunk_duration,
            last_pts_ns=stream_end_ns,
            timestamp_origin_ns=timestamp_origin_ns,
            processing_latency=time.time() - started,
        )

    async def cancel(self, source_id: str) -> bool:
        task = self._active_sources.get(source_id)
        if task is None or task.done():
            return False
        task.cancel()
        return True

    async def check_health(self) -> bool:
        return await self._transport.check_health()

    def capability_map(self) -> Mapping[str, Any]:
        shared = self.shared_frame_capability()
        return {
            "backend": self.name,
            "pipeline": "rtvi_cv",
            "modalities": ("video",),
            "outputs": ("detections", "tracks"),
            "streaming_response": False,
            "requires_gpu": True,
            "shared_frames": {
                "supported": True,
                "protocol_version": shared.protocol_version,
                "transport": shared.transport,
                "pixel_formats": (shared.pixel_format,),
                "device_ids": (shared.device_id,),
                "max_buffer_depth": shared.max_buffer_depth,
                "live_streams": shared.live_streams,
            },
        }

    def shared_frame_capability(self) -> SharedFrameCapability:
        return _rtvi_shared_capability("cuda_ipc")

    def shared_frame_capability_for_transport(
        self,
        transport: Literal["owned_cpu", "cuda_ipc"],
    ) -> SharedFrameCapability | None:
        if transport != "cuda_ipc":
            return None
        return _rtvi_shared_capability(transport)


class _BoundedSharedFrameSampler:
    """Keep eight temporally distributed owned CPU frames per chunk."""

    def __init__(self) -> None:
        self._frames: list[Frame] = []

    def add(self, frame: Frame) -> None:
        self._frames.append(frame)
        if len(self._frames) <= 8:
            return
        remove_at = min(
            range(1, len(self._frames) - 1),
            key=lambda index: (
                self._frames[index + 1].timestamp_ns
                - self._frames[index - 1].timestamp_ns,
                index,
            ),
        )
        del self._frames[remove_at]

    def selected(self) -> list[Frame]:
        return list(self._frames)


def _rtvi_shared_capability(
    transport: Literal["owned_cpu", "cuda_ipc"] | None = None,
) -> SharedFrameCapability:
    if transport is None:
        configured = (
            os.getenv("VSS_SHARED_FRAME_TRANSPORT", "owned_cpu").strip().lower()
            or "owned_cpu"
        )
        if configured not in {"owned_cpu", "cuda_ipc"}:
            raise ValueError("VSS_SHARED_FRAME_TRANSPORT must be owned_cpu or cuda_ipc")
        transport = "owned_cpu" if configured == "owned_cpu" else "cuda_ipc"
    if transport == "owned_cpu":
        return SharedFrameCapability(decoder="pynv", decoder_agnostic=True)
    raw_device = os.getenv("VSS_SHARED_DECODE_DEVICE_ID", "0")
    try:
        device_id = int(raw_device)
    except ValueError as error:
        raise ValueError("VSS_SHARED_DECODE_DEVICE_ID must be an integer") from error
    if device_id < 0:
        raise ValueError("VSS_SHARED_DECODE_DEVICE_ID must be nonnegative")
    return SharedFrameCapability(
        decoder="pynv",
        transport="cuda_ipc",
        pixel_format="nv12",
        device_id=device_id,
        live_streams=True,
    )


def _cv_chunk_responses(
    source: RequestSource,
    detections: list[CvDetection],
    *,
    chunk_duration: int,
    last_pts_ns: int | None,
    timestamp_origin_ns: int | None,
    processing_latency: float,
) -> CvCompletionResponse:
    for detection in detections:
        relative_timestamp = _relative_cv_timestamp(
            detection.timestamp_ns,
            timestamp_origin_ns=timestamp_origin_ns,
        )
        if relative_timestamp is None:
            raise RTVICvBackendError(
                "RTVI-CV absolute timestamps require the source timestamp origin"
            )
        detection.timestamp_ns = relative_timestamp
    chunk_ns = chunk_duration * 1_000_000_000
    grouped: dict[int, list[CvDetection]] = {}
    for detection in detections:
        grouped.setdefault(detection.timestamp_ns // chunk_ns, []).append(detection)
    final_index = 0 if last_pts_ns is None else last_pts_ns // chunk_ns
    if last_pts_ns is not None and last_pts_ns > 0 and last_pts_ns % chunk_ns == 0:
        final_index = max(0, final_index - 1)
    if not grouped:
        grouped[0] = []
    for index in range(max(final_index, max(grouped)) + 1):
        grouped.setdefault(index, [])
    responses = [
        CvChunkResponse(
            chunk=ChunkInfo(
                sourceId=source.source_id,
                chunkIdx=index,
                file=source.url or "",
                start_pts=index * chunk_ns,
                end_pts=min((index + 1) * chunk_ns, last_pts_ns)
                if last_pts_ns is not None
                else (index + 1) * chunk_ns,
                is_first=index == 0,
            ),
            detections=grouped[index],
            processing_latency=processing_latency,
        )
        for index in sorted(grouped)
    ]
    responses[-1].chunk.is_last = True
    return responses


def _relative_cv_timestamp(
    timestamp_ns: int | None,
    *,
    timestamp_origin_ns: int | None,
) -> int | None:
    if timestamp_ns is None:
        return None
    if timestamp_ns <= 10**15:
        return timestamp_ns
    if timestamp_origin_ns is None or timestamp_origin_ns <= 10**15:
        return None
    return max(0, timestamp_ns - timestamp_origin_ns)


def _request_camera_id(_source_id: str) -> str:
    """Return an external stream identity unique to this ingestion request."""

    return f"vss-{uuid4().hex}"


def _to_chunk_response(
    source: RequestSource, payload: dict[str, Any]
) -> VlmChunkResponse:
    chunk_id = int(payload.get("chunk_id", 0))
    start = str(payload.get("start_time", "0"))
    end = str(payload.get("end_time", "0"))
    start_seconds = _numeric_timestamp(start)
    end_seconds = _numeric_timestamp(end)

    chunk = ChunkInfo(
        sourceId=source.source_id,
        chunkIdx=chunk_id,
        file=source.url or "",
        start_pts=int(start_seconds * 1_000_000_000)
        if start_seconds is not None
        else 0,
        end_pts=int(end_seconds * 1_000_000_000) if end_seconds is not None else -1,
        start_ntp=start if start_seconds is None else "",
        end_ntp=end if end_seconds is None else "",
        is_first=chunk_id == 0,
    )

    response = VlmChunkResponse()
    response.chunk = chunk
    response.vlm_response = payload.get("content")
    if payload.get("error"):
        # Keep explicit failure status so managed persistence cannot publish this
        # as a successful caption. Raw provider error text is diagnostic data.
        response.error = "RTVI reported a chunk error"
    response.audio_transcript = payload.get("audio_transcript")
    response.reasoning_description = payload.get("reasoning_description")
    response.queue_time = float(payload.get("queue_time_s") or 0)
    response.processing_latency = float(payload.get("processing_latency_s") or 0)
    response.rtvi_chunk_latency_s = float(payload.get("chunk_latency_ms") or 0) / 1000
    response.rtvi_frame_count = int(payload.get("frame_count") or 0)
    response.vlm_stats = {
        "input_tokens": int(payload.get("input_tokens") or 0),
        "output_tokens": int(payload.get("output_tokens") or 0),
    }

    now = time.time()
    vlm_duration = float(payload.get("vlm_latency_ms") or 0) / 1000
    decode_duration = float(payload.get("decode_latency_ms") or 0) / 1000
    response.vlm_end_time = now
    response.vlm_start_time = now - vlm_duration
    response.decode_end_time = response.vlm_start_time
    response.decode_start_time = response.decode_end_time - decode_duration
    return response


def _to_embedding_response(
    source: RequestSource,
    payload: dict[str, Any],
    chunk_idx: int,
) -> EmbeddingChunkResponse:
    start = str(payload.get("start_time", "0"))
    end = str(payload.get("end_time", "0"))
    start_seconds = _numeric_timestamp(start)
    end_seconds = _numeric_timestamp(end)
    chunk = ChunkInfo(
        sourceId=source.source_id,
        chunkIdx=chunk_idx,
        file=source.url or "",
        start_pts=int(start_seconds * 1_000_000_000)
        if start_seconds is not None
        else 0,
        end_pts=int(end_seconds * 1_000_000_000) if end_seconds is not None else -1,
        start_ntp=start if start_seconds is None else "",
        end_ntp=end if end_seconds is None else "",
        is_first=chunk_idx == 0,
    )
    now = time.time()
    inference_duration = float(payload.get("inference_latency_ms") or 0) / 1000
    decode_duration = float(payload.get("decode_latency_ms") or 0) / 1000
    embedding_start = now - inference_duration
    decode_end = embedding_start
    error = payload.get("error")
    return EmbeddingChunkResponse(
        chunk=chunk,
        embeddings=payload.get("embeddings") or [],
        error=str(error) if error is not None else None,
        decode_start_time=decode_end - decode_duration,
        decode_end_time=decode_end,
        embedding_start_time=embedding_start,
        embedding_end_time=now,
        queue_time=float(payload.get("queue_time_s") or 0),
        processing_latency=float(payload.get("processing_latency_s") or 0),
        rtvi_chunk_latency_s=float(payload.get("chunk_latency_ms") or 0) / 1000,
        rtvi_frame_count=int(payload.get("frame_count") or 0),
    )


def _embedding_chunk_sort_key(payload: dict[str, Any]) -> tuple[int, float | str]:
    start = str(payload.get("start_time", ""))
    numeric_start = _numeric_timestamp(start)
    return (0, numeric_start) if numeric_start is not None else (1, start)


def _numeric_timestamp(value: str) -> float | None:
    try:
        return float(value)
    except ValueError:
        return None


register_backend(CPUVLMBackend.name, CPUVLMBackend)
register_backend(GPUVLMBackend.name, GPUVLMBackend)
register_backend(VLMIngestionBackend.name, VLMIngestionBackend)
register_backend(RTVIBackend.name, RTVIBackend)
register_backend(RTEmbedBackend.name, RTEmbedBackend)
register_backend(RTVICvBackend.name, RTVICvBackend)
