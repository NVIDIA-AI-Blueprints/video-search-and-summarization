# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Ordered serialization and deterministic backend constraints."""

from __future__ import annotations

import base64
from dataclasses import asdict
import json
import logging
import secrets
from typing import TYPE_CHECKING
from typing import Any

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator
    from collections.abc import Callable
    from pathlib import Path

from .chat import ChatError
from .chat import ChatRequest
from .chat import ImageBytes
from .chat import ImagePart
from .chat import TextPart
from .chat import VideoFile
from .chat import VideoOptions
from .chat import VideoPart

_LOG = logging.getLogger(__name__)


def validate_backend(request: ChatRequest, backend: str) -> None:
    """Keep server-specific RT-VLM restrictions out of direct chat backends.

    Cosmos Reason NIM uses ordered OpenAI-style messages and top-level
    generation/continuation fields. Its deployed model and image determine
    modality, history and continuation support; HTTP rejections stay typed.
    Legacy video-option translations remain unchanged for existing consumers.
    """
    media = [
        p
        for m in request.messages
        if not isinstance(m.content, str)
        for p in m.content
        if isinstance(p, (ImagePart, VideoPart))
    ]
    generation = request.generation
    if backend == "rt_vlm":
        if request.continuation or generation.repetition_penalty is not None:
            raise ChatError("RT-VLM does not support continuation or repetition_penalty")
        if generation.top_k is not None and not 1 <= generation.top_k <= 1000:
            raise ChatError("RT-VLM top_k must be in 1..1000")
        systems = [i for i, m in enumerate(request.messages) if m.role == "system"]
        if systems and (
            systems != [0] or not isinstance(request.messages[0].content, str) or not request.messages[0].content
        ):
            raise ChatError("RT-VLM accepts at most one nonempty leading string system message")
        if not media:
            if any(not isinstance(m.content, str) for m in request.messages):
                raise ChatError("RT-VLM text history requires string content")
        else:
            turns = request.messages[1:] if systems else request.messages
            if (
                len(media) != 1
                or len(turns) != 1
                or turns[0].role != "user"
                or isinstance(turns[0].content, str)
                or len(turns[0].content) != 2
                or sum(isinstance(p, TextPart) for p in turns[0].content) != 1
            ):
                raise ChatError("RT-VLM media requires one user turn with one media part and one text part")


def _backend_options(request: ChatRequest, backend: str) -> dict[str, Any]:
    payload = {k: v for k, v in asdict(request.generation).items() if v is not None}
    if request.continuation:
        payload.update(add_generation_prompt=False, continue_final_message=True)
    options = request.video_options or VideoOptions()
    if backend in ("vllm", "openai") and options.chunk_duration not in (None, 0):
        raise ChatError(f"positive --chunk-duration is not supported by the {backend} backend")
    if backend == "openai":
        ignored = [n for n in ("fps", "max_frames", "total_pixels") if getattr(options, n) is not None]
        if request.enable_reasoning is not None:
            ignored.append("enable_reasoning")
        if ignored:
            _LOG.warning(
                "%s not sent: the openai backend sends a plain chat completion, so the endpoint's defaults apply",
                ", ".join(ignored),
            )
        return payload
    if backend == "cosmos_reason_nim":
        _LOG.warning(
            "Cosmos Reason NIM backend support is alpha; request construction currently uses the RT-VLM request schema and is pending refinement."
        )
    if backend == "vllm" and request.enable_reasoning is not None:
        payload["chat_template_kwargs"] = {"enable_thinking": request.enable_reasoning}
    video = {}
    if options.fps is not None:
        video["fps"] = options.fps
        if backend == "vllm" and options.max_frames is None:
            video["num_frames"] = -1
    if options.max_frames is not None:
        if backend == "vllm":
            video.update(num_frames=options.max_frames, max_frames=options.max_frames)
        elif options.fps is None:
            video["num_frames"] = options.max_frames
        else:
            _LOG.warning(
                "max_frames %s not sent: this backend takes fps or a frame count, not both; its deployment frame cap applies",
                options.max_frames,
            )
    processor: dict[str, Any] = {}
    if video:
        payload["media_io_kwargs"] = {"video": video}
        if backend == "vllm":
            processor["do_sample_frames"] = False
    # Preserve the field order of existing video requests, including streamed
    # JSON bodies: RT-VLM/NIM put video sampling before reasoning/chunk options.
    if backend != "vllm":
        if request.enable_reasoning is not None:
            payload["enable_reasoning"] = request.enable_reasoning
        if options.chunk_duration is not None:
            payload["chunk_duration"] = options.chunk_duration
    if options.total_pixels is not None:
        processor["size"] = {
            "shortest_edge": min(128 * 32 * 32, options.total_pixels),
            "longest_edge": options.total_pixels,
        }
    if processor:
        payload["mm_processor_kwargs"] = processor
    return payload


def serialize_request(
    request: ChatRequest, backend: str
) -> tuple[dict[str, Any] | None, Callable[[], AsyncGenerator[bytes]] | None]:
    """Return JSON or a fresh async body factory; never mutate the request."""
    validate_backend(request, backend)
    payload: dict[str, Any] = {"model": request.model, "messages": [], **_backend_options(request, backend)}
    files: dict[str, Path] = {}
    for message in request.messages:
        content: Any = message.content
        if not isinstance(message.content, str):
            parts: list[dict[str, Any]] = []
            for part in message.content:
                if isinstance(part, TextPart):
                    parts.append({"type": "text", "text": part.text})
                elif isinstance(part, ImagePart):
                    source = part.source
                    url = (
                        (f"data:{source.mime};base64," + base64.b64encode(source.data).decode())
                        if isinstance(source, ImageBytes)
                        else source
                    )
                    ref = {"url": url}
                    if part.detail is not None:
                        ref["detail"] = part.detail
                    parts.append({"type": "image_url", "image_url": ref})
                elif isinstance(part.source, VideoFile):
                    try:
                        with part.source.path.open("rb"):
                            pass
                    except OSError:
                        raise ChatError("cannot read local video source") from None
                    sentinel = "__video_" + secrets.token_hex(24) + "__"
                    files[sentinel] = part.source.path
                    parts.append({"type": "video_url", "video_url": {"url": sentinel}})
                else:
                    parts.append({"type": "video_url", "video_url": {"url": part.source}})
            content = parts
        payload["messages"].append({"role": message.role, "content": content})
    if not files:
        return payload, None
    raw = json.dumps(payload)
    # Unique random markers occur only in serialized file references.
    segments = []
    remaining = raw
    while files:
        candidates = [(remaining.index(json.dumps(marker)), marker) for marker in files]
        offset, marker = min(candidates)
        segments.append((remaining[:offset], files.pop(marker)))
        remaining = remaining[offset + len(json.dumps(marker)) :]

    async def body() -> AsyncGenerator[bytes]:
        for prefix, path in segments:
            yield (prefix + '"data:video/mp4;base64,').encode()
            with path.open("rb") as stream:
                while chunk := stream.read(3 * 65536):
                    yield base64.b64encode(chunk)
            yield b'"'
        yield remaining.encode()

    return None, body
