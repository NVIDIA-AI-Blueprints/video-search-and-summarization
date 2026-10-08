# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Native lifecycle management for DL Algo Streaming VLM sessions.

This module deliberately has no HTTP or image-codec dependency. The production
factory creates the source-patched vLLM ``StreamingSession`` objects, whose
``push_frame`` method accepts an already-decoded frame.
"""

from __future__ import annotations

import asyncio
import inspect
from dataclasses import dataclass
from typing import Any, Callable, Protocol

import numpy
import torch
from PIL import Image

TEMPORAL_CHUNK_FRAMES = 2


class StreamingSessionCapacityError(RuntimeError):
    """Raised when admitting another stream would exceed the configured cap."""


class StreamingSessionNotFoundError(KeyError):
    """Raised when a frame targets a stream without an active session."""


def temporal_video_segment_count(frame_count: int) -> int:
    """Return the number of two-frame video items needed for ``frame_count``."""
    if frame_count < 1:
        raise ValueError("frame_count must be at least 1")
    return (frame_count + TEMPORAL_CHUNK_FRAMES - 1) // TEMPORAL_CHUNK_FRAMES


@dataclass(frozen=True)
class DlalgoSessionConfig:
    """Inputs fixed for the lifetime of one native streaming session."""

    system_prompt: str = ""
    question: str = ""
    fps: float = 1.0
    max_tokens: int = 24
    min_tokens: int | None = None
    ignore_eos: bool = False
    temperature: float = 0.3
    top_p: float = 0.9
    top_k: int = 20
    repetition_penalty: float = 1.0
    seed: int = 1
    max_video_segments: int = 30
    max_text_tokens: int | None = None
    max_session_tokens: int = 7000
    reprefill_threshold: float = 0.7
    exact_video_compaction: bool = False
    text_round: int = 0
    reprefill_relocation_interval: int = 0
    text_sink_tokens: int = 512
    text_sliding_window_tokens: int = 512
    previous_text: str = ""
    time_offset_s: float = 0.0
    mm_processor_kwargs: dict[str, Any] | None = None
    structured_outputs: Any | None = None
    decode_interval_steps: int = 1
    question_on_decode: bool = False
    absolute_segment_timestamps: bool = False
    retain_generated_text: bool = True


class NativeStreamingSession(Protocol):
    """Subset of the DL Algo session contract used by RTVI."""

    def start(self) -> None: ...

    async def push_frame(self, frame: Any, *, generate: bool = True) -> Any: ...

    async def push_frames(self, frames: list[Any], *, generate: bool = True) -> Any: ...

    async def close(self) -> None: ...


SessionFactory = Callable[[str, DlalgoSessionConfig], NativeStreamingSession]


def apply_native_streaming_engine_args(
    engine_args: dict[str, Any],
    supported_params: set[str],
    *,
    async_scheduling: bool = False,
) -> None:
    """Apply engine invariants required by the DL Algo retention scheduler."""
    if "async_scheduling" not in supported_params:
        raise RuntimeError(
            "installed vLLM cannot configure async scheduling required by "
            "DL Algo Streaming VLM"
        )
    engine_args["async_scheduling"] = async_scheduling
    if "enable_prefix_caching" in supported_params:
        # Streaming sessions relocate prompt tokens and KV slots in place.
        # Publishing those mutable blocks through prefix caching can expose
        # stale hashes or shared blocks to another request.
        engine_args["enable_prefix_caching"] = False


def to_pil_rgb_frame(frame: Any) -> Image.Image:
    """Convert one decoded RTVI frame to the renderer's in-memory RGB form."""
    if isinstance(frame, (bytes, bytearray, memoryview)):
        raise TypeError(
            "native Streaming VLM requires a decoded frame, not encoded bytes"
        )
    if isinstance(frame, Image.Image):
        return frame if frame.mode == "RGB" else frame.convert("RGB")

    if isinstance(frame, torch.Tensor):
        array = frame.detach().cpu()
        if array.ndim == 4:
            if array.shape[0] != 1:
                raise ValueError("native Streaming VLM requires a single decoded frame")
            array = array[0]
        if array.ndim == 3 and array.shape[0] in (1, 3, 4):
            array = array.permute(1, 2, 0)
        array = array.numpy()
    elif isinstance(frame, numpy.ndarray):
        array = frame
        if array.ndim == 4:
            if array.shape[0] != 1:
                raise ValueError("native Streaming VLM requires a single decoded frame")
            array = array[0]
    else:
        raise TypeError(
            f"native Streaming VLM requires a decoded frame, got {type(frame)!r}"
        )

    if array.ndim != 3 or array.shape[-1] not in (1, 3, 4):
        raise ValueError(
            "decoded frame must have HWC, CHW, 1HWC, or 1CHW shape with 1, 3, or 4 channels"
        )
    if array.dtype != numpy.uint8:
        raise ValueError(f"decoded frame dtype must be uint8, got {array.dtype}")

    if array.shape[-1] == 1:
        array = numpy.repeat(array, 3, axis=-1)
    elif array.shape[-1] == 4:
        array = array[..., :3]
    return Image.fromarray(numpy.ascontiguousarray(array), mode="RGB")


class VllmDlalgoSessionFactory:
    """Create source-patched vLLM sessions on RTVI's in-process engine."""

    def __init__(self, engine: Any) -> None:
        try:
            from vllm.entrypoints.openai.streaming.session import StreamingSession
            from vllm.sampling_params import RequestOutputKind, SamplingParams
            from vllm.v1.streaming.retention import StreamingRetentionParams
        except ImportError as exc:
            raise RuntimeError(
                "installed vLLM does not contain the DL Algo Streaming VLM patch"
            ) from exc

        renderer = getattr(engine, "renderer", None)
        if renderer is None:
            raise RuntimeError(
                "vLLM engine does not expose the renderer required for streaming"
            )

        self._engine = engine
        self._renderer = renderer
        self._streaming_session_cls = StreamingSession
        self._sampling_params_cls = SamplingParams
        self._retention_params_cls = StreamingRetentionParams
        self._delta_output_kind = RequestOutputKind.DELTA

    def __call__(
        self,
        stream_id: str,
        config: DlalgoSessionConfig,
    ) -> NativeStreamingSession:
        retention_kwargs = dict(
            max_video_segments=config.max_video_segments,
            max_text_tokens=config.max_text_tokens,
            max_session_tokens=config.max_session_tokens,
            reprefill_threshold=config.reprefill_threshold,
            text_round=config.text_round,
            text_sink_tokens=config.text_sink_tokens,
            text_sliding_window_tokens=config.text_sliding_window_tokens,
        )
        retention_params = inspect.signature(self._retention_params_cls).parameters
        supports_retained_text_control = (
            "retain_generated_text" in retention_params
            or any(
                parameter.kind is inspect.Parameter.VAR_KEYWORD
                for parameter in retention_params.values()
            )
        )
        if supports_retained_text_control:
            retention_kwargs["retain_generated_text"] = config.retain_generated_text
        elif not config.retain_generated_text:
            raise RuntimeError(
                "installed vLLM does not support stateless generated text"
            )
        supports_exact_video_compaction = (
            "exact_video_compaction" in retention_params
            or any(
                parameter.kind is inspect.Parameter.VAR_KEYWORD
                for parameter in retention_params.values()
            )
        )
        if supports_exact_video_compaction:
            retention_kwargs["exact_video_compaction"] = config.exact_video_compaction
        elif config.exact_video_compaction:
            raise RuntimeError("installed vLLM does not support exact video compaction")
        supports_relocation_interval = (
            "reprefill_relocation_interval" in retention_params
            or any(
                parameter.kind is inspect.Parameter.VAR_KEYWORD
                for parameter in retention_params.values()
            )
        )
        if supports_relocation_interval:
            retention_kwargs["reprefill_relocation_interval"] = (
                config.reprefill_relocation_interval
            )
        elif config.reprefill_relocation_interval:
            raise RuntimeError(
                "installed vLLM does not support per-session periodic re-prefill"
            )
        retention = self._retention_params_cls(**retention_kwargs)
        sampling_kwargs = dict(
            max_tokens=config.max_tokens,
            ignore_eos=config.ignore_eos,
            temperature=config.temperature,
            top_p=config.top_p,
            top_k=config.top_k,
            repetition_penalty=config.repetition_penalty,
            seed=config.seed,
            output_kind=self._delta_output_kind,
            extra_args={"streaming_retention": retention},
        )
        if config.min_tokens is not None:
            sampling_kwargs["min_tokens"] = config.min_tokens
        if config.structured_outputs is not None:
            sampling_kwargs["structured_outputs"] = config.structured_outputs
        sampling_params = self._sampling_params_cls(**sampling_kwargs)
        return self._streaming_session_cls(
            stream_id,
            self._engine,
            sampling_params,
            self._renderer,
            system_prompt=config.system_prompt,
            question=config.question,
            fps=config.fps,
            previous_text=config.previous_text,
            time_offset_s=config.time_offset_s,
            mm_processor_kwargs=config.mm_processor_kwargs,
            question_on_decode=config.question_on_decode,
            absolute_segment_timestamps=config.absolute_segment_timestamps,
        )


class DlalgoStreamingSessionManager:
    """Map RTVI stream IDs to isolated in-process DL Algo sessions."""

    def __init__(
        self,
        session_factory: SessionFactory,
        *,
        max_sessions: int,
        max_total_video_segments: int | None = None,
    ) -> None:
        if max_sessions < 1:
            raise ValueError("max_sessions must be at least 1")
        if max_total_video_segments is not None and max_total_video_segments < 1:
            raise ValueError("max_total_video_segments must be at least 1")
        self._session_factory = session_factory
        self._max_sessions = max_sessions
        self._max_total_video_segments = max_total_video_segments
        self._sessions: dict[str, NativeStreamingSession] = {}
        self._configs: dict[str, DlalgoSessionConfig] = {}
        self._push_locks: dict[str, asyncio.Lock] = {}
        self._completed_steps: dict[str, int] = {}
        self._lock = asyncio.Lock()

    @property
    def active_stream_ids(self) -> tuple[str, ...]:
        return tuple(self._sessions)

    @property
    def session_count(self) -> int:
        return len(self._sessions)

    async def ensure_session(
        self,
        stream_id: str,
        config: DlalgoSessionConfig,
    ) -> NativeStreamingSession:
        """Return the existing session or atomically create one for ``stream_id``."""
        if not stream_id:
            raise ValueError("stream_id cannot be empty")
        if config.decode_interval_steps < 1:
            raise ValueError("decode_interval_steps must be at least 1")

        async with self._lock:
            existing = self._sessions.get(stream_id)
            if existing is not None:
                if self._configs[stream_id] != config:
                    raise ValueError(
                        f"stream {stream_id!r} already has a session with different config"
                    )
                return existing

            if len(self._sessions) >= self._max_sessions:
                raise StreamingSessionCapacityError(
                    f"cannot admit {stream_id!r}: {len(self._sessions)} active session(s) "
                    f"already use the configured maximum of {self._max_sessions}"
                )

            if self._max_total_video_segments is not None:
                reserved_segments = sum(
                    session_config.max_video_segments
                    for session_config in self._configs.values()
                )
                requested_segments = config.max_video_segments
                if (
                    reserved_segments + requested_segments
                    > self._max_total_video_segments
                ):
                    raise StreamingSessionCapacityError(
                        f"cannot admit {stream_id!r}: video segment budget would be "
                        f"{reserved_segments + requested_segments}, exceeding the configured "
                        f"maximum of {self._max_total_video_segments}"
                    )

            session = self._session_factory(stream_id, config)
            session.start()
            self._sessions[stream_id] = session
            self._configs[stream_id] = config
            self._push_locks[stream_id] = asyncio.Lock()
            self._completed_steps[stream_id] = 0
            return session

    async def push_frame(
        self,
        stream_id: str,
        frame: Any,
        *,
        generate: bool = True,
    ) -> Any:
        """Append one decoded frame and optionally decode a response."""
        if isinstance(frame, (bytes, bytearray, memoryview)):
            raise TypeError(
                "native Streaming VLM requires a decoded frame, not encoded image bytes"
            )

        session = self._sessions.get(stream_id)
        if session is None:
            raise StreamingSessionNotFoundError(
                f"no active Streaming VLM session for {stream_id!r}"
            )
        push_lock = self._push_locks[stream_id]
        async with push_lock:
            return await session.push_frame(frame, generate=generate)

    async def push_frames(
        self,
        stream_id: str,
        frames: list[Any],
        *,
        generate: bool | None = True,
    ) -> Any:
        """Append one decoded frame batch and optionally decode a response."""
        if not frames:
            raise ValueError("native Streaming VLM frame batch cannot be empty")
        if any(isinstance(frame, (bytes, bytearray, memoryview)) for frame in frames):
            raise TypeError(
                "native Streaming VLM requires decoded frames, not encoded image bytes"
            )

        session = self._sessions.get(stream_id)
        if session is None:
            raise StreamingSessionNotFoundError(
                f"no active Streaming VLM session for {stream_id!r}"
            )
        push_lock = self._push_locks[stream_id]
        async with push_lock:
            should_generate = generate
            if should_generate is None:
                completed_steps = self._completed_steps[stream_id]
                decode_interval = self._configs[stream_id].decode_interval_steps
                should_generate = (completed_steps + 1) % decode_interval == 0
            response = await session.push_frames(frames, generate=should_generate)
            self._completed_steps[stream_id] += 1
            return response

    async def close_session(self, stream_id: str) -> bool:
        """Close only ``stream_id`` and leave every other stream running."""
        async with self._lock:
            session = self._sessions.pop(stream_id, None)
            self._configs.pop(stream_id, None)
            self._push_locks.pop(stream_id, None)
            self._completed_steps.pop(stream_id, None)
        if session is None:
            return False
        await session.close()
        return True

    async def close_all(self) -> None:
        """Detach and close every active session."""
        async with self._lock:
            sessions = tuple(self._sessions.values())
            self._sessions.clear()
            self._configs.clear()
            self._push_locks.clear()
            self._completed_steps.clear()

        if sessions:
            await asyncio.gather(*(session.close() for session in sessions))
