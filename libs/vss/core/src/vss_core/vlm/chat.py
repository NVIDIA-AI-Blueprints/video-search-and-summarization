# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Frozen multimodal chat contracts and a reusable, event-loop-bound client."""

from __future__ import annotations

import asyncio
import base64
import binascii
from dataclasses import dataclass
from dataclasses import field
import math
from pathlib import Path
import time
from typing import TYPE_CHECKING
from typing import Any
from typing import Literal

if TYPE_CHECKING:
    from collections.abc import Callable
    from collections.abc import Collection

    import httpx

    from .completions import ChatCompletion
from urllib.parse import urlsplit

from vss_core.openai import OpenAIChatRejectedError
from vss_core.openai import OpenAIChatRequestError
from vss_core.openai import OpenAIChatTransport

IMAGE_LIMIT = 20 * 1024 * 1024
IMAGE_TOTAL_LIMIT = 64 * 1024 * 1024
TEXT_LIMIT = 512_000


class ChatError(Exception):
    """Safe failure metadata; diagnostics never contain caller content."""

    def __init__(
        self,
        detail: str,
        *,
        kind: str = "validation",
        status_code: int | None = None,
        attempts: int = 0,
        latency_s: float = 0.0,
    ) -> None:
        super().__init__(detail)
        self.kind = kind
        self.status_code = status_code
        self.attempts = attempts
        self.latency_s = latency_s

    def metadata(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "http_status": self.status_code,
            "attempts": self.attempts,
            "latency_s": self.latency_s,
        }


def _check(condition: bool, detail: str) -> None:
    if not condition:
        raise ChatError(detail)


def _integer(value: object, name: str, low: int, high: int) -> None:
    _check(type(value) is int and low <= value <= high, f"invalid {name}")


def _number(value: object, name: str, low: float, high: float, *, open_low: bool = False) -> None:
    try:
        valid = (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isfinite(value)
            and (low < value if open_low else low <= value)
            and value <= high
        )
    except OverflowError:
        valid = False
    _check(valid, f"invalid {name}")


def _image_bytes(data: bytes, mime: str) -> None:
    _check(type(data) is bytes and 0 < len(data) <= IMAGE_LIMIT, "invalid embedded image size")
    _check(type(mime) is str, "invalid image MIME")
    signatures = {
        "image/png": data.startswith(b"\x89PNG\r\n\x1a\n"),
        "image/jpeg": data.startswith(b"\xff\xd8\xff"),
        "image/webp": len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP",
    }
    _check(signatures.get(mime, False), "unsupported or mismatched image MIME/signature")


def _reference(url: str, *, image: bool) -> int:
    _check(type(url) is str and bool(url), "invalid media reference")
    if url.startswith("data:"):
        separator = url.find(",")
        head = url[:separator] if separator >= 0 else url
        allowed = ("image/png", "image/jpeg", "image/webp") if image else ("video/mp4",)
        mime = head[5:].removesuffix(";base64")
        _check(separator >= 0 and head.endswith(";base64") and mime in allowed, "invalid media data URI")
        start = separator + 1
        if image:
            _check(len(url) - start <= 4 * ((IMAGE_LIMIT + 2) // 3), "embedded image exceeds size limit")
        try:
            if not image:
                # Validate videos without allocating the full decoded payload or
                # copying the encoded URI. Padding belongs only to the final block.
                size = 0
                block_size = 4 * 65536
                for offset in range(start, len(url), block_size):
                    block = url[offset : offset + block_size]
                    if offset + block_size < len(url) and "=" in block:
                        raise ValueError("nonfinal base64 padding")
                    size += len(base64.b64decode(block, validate=True))
                _check(size > 0, "empty embedded media")
                return size
            decoded = base64.b64decode(url[start:], validate=True)
        except (ValueError, binascii.Error):
            raise ChatError("invalid media base64") from None
        _check(bool(decoded), "empty embedded media")
        if image:
            _image_bytes(decoded, mime)
        return len(decoded)
    try:
        parsed = urlsplit(url)
        valid = parsed.scheme in ("http", "https") and bool(parsed.hostname) and parsed.username is None
        _ = parsed.port
    except ValueError:
        valid = False
    _check(valid, "media reference must be HTTP(S) without userinfo or a supported data URI")
    return 0


@dataclass(frozen=True)
class ImageBytes:
    data: bytes = field(repr=False)
    mime: str

    def __post_init__(self) -> None:
        _image_bytes(self.data, self.mime)


@dataclass(frozen=True)
class VideoFile:
    path: Path = field(repr=False)

    def __post_init__(self) -> None:
        _check(isinstance(self.path, (str, Path)), "invalid video file source")
        object.__setattr__(self, "path", Path(self.path))


@dataclass(frozen=True)
class TextPart:
    text: str = field(repr=False)

    def __post_init__(self) -> None:
        _check(type(self.text) is str and len(self.text) <= TEXT_LIMIT, "invalid text part")


@dataclass(frozen=True)
class ImagePart:
    source: str | ImageBytes = field(repr=False)
    detail: Literal["auto", "low", "high"] | None = None

    def __post_init__(self) -> None:
        _check(self.detail in (None, "auto", "low", "high"), "invalid image detail")
        if isinstance(self.source, str):
            _reference(self.source, image=True)
        else:
            _check(isinstance(self.source, ImageBytes), "invalid image source")


@dataclass(frozen=True)
class VideoPart:
    source: str | VideoFile = field(repr=False)

    def __post_init__(self) -> None:
        if isinstance(self.source, str):
            _reference(self.source, image=False)
        else:
            _check(isinstance(self.source, VideoFile), "invalid video source")


@dataclass(frozen=True)
class ChatMessage:
    role: Literal["system", "user", "assistant"]
    content: str | tuple[TextPart | ImagePart | VideoPart, ...] = field(repr=False)

    def __post_init__(self) -> None:
        _check(self.role in ("system", "user", "assistant"), "invalid message role")
        if isinstance(self.content, str):
            _check(len(self.content) <= TEXT_LIMIT, "message text exceeds limit")
        else:
            _check(type(self.content) is tuple and bool(self.content), "content must be text or a nonempty tuple")
            _check(all(isinstance(p, (TextPart, ImagePart, VideoPart)) for p in self.content), "invalid content part")
            _check(
                self.role == "user" or all(isinstance(p, TextPart) for p in self.content), "media requires user role"
            )


@dataclass(frozen=True)
class GenerationOptions:
    temperature: float | None = None
    max_tokens: int | None = None
    seed: int | None = None
    top_p: float | None = None
    top_k: int | None = None
    repetition_penalty: float | None = None

    def __post_init__(self) -> None:
        if self.temperature is not None:
            _number(self.temperature, "temperature", 0, 2)
        if self.max_tokens is not None:
            _integer(self.max_tokens, "max_tokens", 1, 1_000_000)
        if self.seed is not None:
            _integer(self.seed, "seed", 1, 2**32 - 1)
        if self.top_p is not None:
            _number(self.top_p, "top_p", 0, 1)
        if self.top_k is not None:
            _check(type(self.top_k) is int and (self.top_k == -1 or self.top_k >= 1), "invalid top_k")
        if self.repetition_penalty is not None:
            _number(self.repetition_penalty, "repetition_penalty", 0, math.inf, open_low=True)


@dataclass(frozen=True)
class VideoOptions:
    fps: float | None = None
    max_frames: int | None = None
    total_pixels: int | None = None
    chunk_duration: int | None = None

    def __post_init__(self) -> None:
        if self.fps is not None:
            _number(self.fps, "fps", 0, 256, open_low=True)
        for name, low, high in (
            ("max_frames", 1, 2**31 - 1),
            ("total_pixels", 1, 2**31 - 1),
            ("chunk_duration", 0, 3600),
        ):
            value = getattr(self, name)
            if value is not None:
                _integer(value, name, low, high)


@dataclass(frozen=True)
class ChatRequest:
    messages: tuple[ChatMessage, ...] = field(repr=False)
    model: str
    generation: GenerationOptions = field(default_factory=GenerationOptions)
    continuation: bool = False
    enable_reasoning: bool | None = None
    video_options: VideoOptions | None = None

    def __post_init__(self) -> None:
        _check(
            type(self.messages) is tuple
            and 0 < len(self.messages) <= 256
            and all(isinstance(m, ChatMessage) for m in self.messages),
            "invalid messages or message count",
        )
        _check(any(m.role == "user" for m in self.messages), "at least one user message required")
        _check(type(self.model) is str and bool(self.model.strip()) and len(self.model) <= 1024, "invalid model")
        _check(isinstance(self.generation, GenerationOptions), "invalid generation options")
        _check(type(self.continuation) is bool, "invalid continuation")
        _check(self.enable_reasoning is None or type(self.enable_reasoning) is bool, "invalid enable_reasoning")
        _check(self.video_options is None or isinstance(self.video_options, VideoOptions), "invalid video options")
        text_count = part_count = image_count = image_bytes = video_count = 0
        for message in self.messages:
            if isinstance(message.content, str):
                text_count += len(message.content)
                continue
            part_count += len(message.content)
            for part in message.content:
                if isinstance(part, TextPart):
                    text_count += len(part.text)
                elif isinstance(part, ImagePart):
                    image_count += 1
                    image_bytes += (
                        len(part.source.data)
                        if isinstance(part.source, ImageBytes)
                        else _reference(part.source, image=True)
                    )
                else:
                    video_count += 1
        _check(
            part_count <= 1024 and image_count <= 32 and image_bytes <= IMAGE_TOTAL_LIMIT and text_count <= TEXT_LIMIT,
            "request exceeds content limits",
        )
        _check(self.video_options is None or video_count > 0, "video options require video")
        if self.continuation:
            last = self.messages[-1]
            _check(last.role == "assistant", "continuation requires a final assistant message")
            text = (
                last.content
                if isinstance(last.content, str)
                else "".join(p.text for p in last.content if isinstance(p, TextPart))
            )
            _check(last.role == "assistant" and bool(text), "continuation requires a nonempty final assistant text")


class VLMChatClient:
    """Reuse one lazily initialized transport on one event loop; close explicitly."""

    def __init__(
        self,
        base_url: str,
        backend: str,
        api_key: str | None = None,
        timeout_seconds: float = 30,
        attempts: int = 1,
        retry_statuses: Collection[int] | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        client: httpx.AsyncClient | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        _check(backend in ("vllm", "openai", "rt_vlm", "cosmos_reason_nim"), "invalid backend")
        _number(timeout_seconds, "timeout_seconds", 0, math.inf, open_low=True)
        _integer(attempts, "attempts", 1, 100)
        _check(client is None or transport is None, "provide client or transport, not both")
        _check(type(base_url) is str and bool(base_url.strip()), "invalid base URL")
        try:
            parsed = urlsplit(base_url.strip())
            valid_url = (
                parsed.scheme in ("http", "https")
                and bool(parsed.hostname)
                and parsed.username is None
                and not parsed.query
                and not parsed.fragment
            )
            _ = parsed.port
        except ValueError:
            valid_url = False
        _check(valid_url, "base URL must be HTTP(S) without credentials, query or fragment")
        self._backend = backend
        self._transport_args: dict[str, Any] = dict(  # noqa: C408
            base_url=base_url,
            api_key=api_key,
            timeout_seconds=timeout_seconds,
            attempts=attempts,
            retry_statuses=frozenset(retry_statuses) if retry_statuses is not None else None,
            transport=transport,
            client=client,
        )
        self._clock = clock
        self._transport: OpenAIChatTransport | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._closed = False
        self._active = 0

    def _bind(self) -> None:
        loop = asyncio.get_running_loop()
        if self._closed or (self._loop is not None and self._loop is not loop):
            raise ChatError("chat client is closed or belongs to another event loop", kind="lifecycle")
        self._loop = loop
        if self._transport is None:
            self._transport = OpenAIChatTransport(**self._transport_args)

    async def __aenter__(self) -> VLMChatClient:
        self._bind()
        return self

    async def __aexit__(self, *_args: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._closed:
            return
        if self._active or (self._loop is not None and self._loop is not asyncio.get_running_loop()):
            raise ChatError("finish outstanding calls on the owning loop before shutdown", kind="lifecycle")
        self._closed = True
        if self._transport is not None:
            await self._transport.aclose()

    async def complete(self, request: ChatRequest, *, allow_text_parts: bool = True) -> ChatCompletion:
        from .completions import extract_completion
        from .requests import serialize_request

        started = self._clock()
        attempts = 0
        self._bind()
        self._active += 1
        try:
            _check(isinstance(request, ChatRequest), "expected ChatRequest")
            _check(type(allow_text_parts) is bool, "invalid response content policy")
            payload, body_factory = serialize_request(request, self._backend)
            try:
                assert self._transport is not None
                response, attempts = await self._transport.post_with_attempts(payload, body_factory=body_factory)
            except (OpenAIChatRejectedError, OpenAIChatRequestError) as error:
                status = error.status_code
                kind = (
                    "validation"
                    if getattr(error, "kind", "") == "source"
                    else "rate_limited"
                    if status == 429
                    else "rejected"
                    if status is not None and status < 500
                    else "timeout"
                    if "Timeout" in getattr(error, "kind", "")
                    else "transport"
                )
                raise ChatError(
                    f"completion request failed ({kind})",
                    kind=kind,
                    status_code=status,
                    attempts=error.attempts,
                    latency_s=self._clock() - started,
                ) from None
            try:
                decoded = response.json()
                return extract_completion(
                    decoded, request.model, attempts, self._clock() - started, allow_text_parts=allow_text_parts
                )
            except (ValueError, TypeError, KeyError, IndexError):
                raise ChatError(
                    "malformed completion response",
                    kind="response_format",
                    attempts=attempts,
                    latency_s=self._clock() - started,
                ) from None
        except ChatError as error:
            error.latency_s = self._clock() - started
            raise
        except OSError:
            raise ChatError(
                "cannot read local video source", attempts=attempts, latency_s=self._clock() - started
            ) from None
        finally:
            self._active -= 1
