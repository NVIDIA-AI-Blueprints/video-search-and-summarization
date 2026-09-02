# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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
"""OpenAI-compatible reusable VLM analyzer."""

from __future__ import annotations

import asyncio
import base64
from datetime import datetime
import logging
import math
from pathlib import Path
import tempfile
from typing import TYPE_CHECKING
from typing import Any
from typing import Literal

import cv2
import httpx

from vss_core._foundation.errors import BackendUnreachableError
from vss_core._foundation.errors import ConfigurationError
from vss_core._foundation.retry import create_retry_strategy
from vss_core._foundation.sanitize import scrub_log
from vss_core._foundation.time_measure import TimeMeasure

if TYPE_CHECKING:
    from vss_core.vios.protocols import VSTSnapshot

logger = logging.getLogger(__name__)

MediaMode = Literal["video_url", "video_base64", "frame_base64"]
VideoURLScope = Literal["internal", "external"]
_VLM_RETRYABLE_ERRORS = (httpx.TimeoutException, httpx.TransportError)


class _RetryableVLMStatusError(Exception):
    """Raised for HTTP 5xx responses so tenacity retries the request."""


class _FrameExtractionError(ValueError):
    """Raised when decoding/selecting frames from a VST clip fails.

    A ``ValueError`` subclass so it is distinguishable from response-parse
    ValueErrors, letting ``analyze`` report clip/frame problems separately from
    "invalid VLM response format".
    """


class OpenAIVLMAnalyzer:
    """VLMAnalyzer implementation backed by OpenAI-style chat completions."""

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        vst: VSTSnapshot,
        api_key: str | None = None,
        timeout_seconds: int = 30,
        media_mode: MediaMode = "video_url",
        video_url_scope: VideoURLScope = "internal",
        disable_audio: bool = True,
        max_frames: int = 60,
        max_fps: int = 2,
        cosmos_nim_runtime_options: bool = True,
    ) -> None:
        if not base_url.strip():
            raise ConfigurationError("VLM base_url must be non-empty")
        if not model.strip():
            raise ConfigurationError("VLM model must be non-empty")
        if timeout_seconds < 1:
            raise ConfigurationError("VLM timeout_seconds must be >= 1")
        if media_mode not in {"video_url", "video_base64", "frame_base64"}:
            raise ConfigurationError(f"unsupported VLM media_mode: {media_mode!r}")
        if video_url_scope not in {"internal", "external"}:
            raise ConfigurationError(f"unsupported VLM video_url_scope: {video_url_scope!r}")
        self._base_url = _normalize_base_url(base_url)
        self._model = model
        self._api_key = api_key
        self._vst = vst
        self._timeout = timeout_seconds
        self._media_mode = media_mode
        self._video_url_scope = video_url_scope
        self._disable_audio = disable_audio
        self._max_frames = max(1, max_frames)
        self._max_fps = max(1, max_fps)
        self._cosmos_nim_runtime_options = cosmos_nim_runtime_options
        self._client: httpx.AsyncClient | None = None

    @property
    def _chat_completions_url(self) -> str:
        if self._base_url.endswith("/chat/completions"):
            return self._base_url
        return f"{self._base_url}/chat/completions"

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            timeout = httpx.Timeout(
                connect=float(self._timeout),
                read=float(max(self._timeout, 120)),
                write=float(max(self._timeout, 120)),
                pool=float(self._timeout),
            )
            self._client = httpx.AsyncClient(timeout=timeout)
        return self._client

    async def analyze(
        self,
        *,
        sensor_id: str,
        start_timestamp: str,
        end_timestamp: str,
        prompt: str,
        time_format: Literal["iso", "offset"] = "iso",
    ) -> str:
        """Analyze a VST clip and return the VLM's text response."""
        try:
            with TimeMeasure("vlm: resolve VST clip url"):
                clip_url = await self._vst.get_video_clip_url(
                    sensor_id=sensor_id,
                    start_timestamp=start_timestamp,
                    end_timestamp=end_timestamp,
                    time_format=time_format,
                    internal=self._video_url_scope == "internal",
                    disable_audio=self._disable_audio,
                )
            duration_seconds = _duration_seconds(start_timestamp, end_timestamp, time_format)
            # Cost here depends entirely on media_mode: a link is free, while
            # video_base64 downloads the clip and encodes it, and frame_base64
            # additionally decodes and samples frames.
            with TimeMeasure("vlm: prepare media"):
                content = await self._build_content(
                    prompt=prompt,
                    clip_url=clip_url,
                    duration_seconds=duration_seconds,
                )
            payload = {
                "model": self._model,
                "temperature": 0,
                "messages": [
                    {
                        "role": "user",
                        "content": content,
                    }
                ],
            }
            self._add_model_runtime_options(payload, duration_seconds)
            headers = {"Content-Type": "application/json"}
            if self._api_key:
                headers["Authorization"] = f"Bearer {self._api_key}"

            with TimeMeasure("vlm: inference"):
                response = await self._request_with_retries(
                    "POST", self._chat_completions_url, headers=headers, json=payload
                )
            answer = _extract_chat_content(response.json())
            if not answer.strip():
                raise ValueError("VLM response contained no text content")
            return answer
        except BackendUnreachableError:
            # Library errors from the injected VST dependency (backend="vst") or
            # elsewhere already carry backend context — let them propagate as-is
            # rather than masking them as a VLM error.
            raise
        except _FrameExtractionError as e:
            logger.error("VLM frame extraction failed: %s", scrub_log(e))
            raise BackendUnreachableError("vlm", f"Frame extraction from VST clip failed: {e}", e) from e
        except (httpx.HTTPError, _RetryableVLMStatusError) as e:
            logger.error("VLM request failed: %s", scrub_log(e))
            raise BackendUnreachableError("vlm", str(e), e) from e
        except (KeyError, IndexError, TypeError, ValueError) as e:
            logger.error("Invalid VLM response: %s", scrub_log(e))
            raise BackendUnreachableError("vlm", f"Invalid VLM response format: {e}", e) from e

    async def _request_with_retries(
        self,
        method: str,
        url: str,
        *,
        configuration_4xx: bool = True,
        **kwargs: Any,
    ) -> httpx.Response:
        async for retry in create_retry_strategy(
            retries=3,
            exceptions=(*_VLM_RETRYABLE_ERRORS, _RetryableVLMStatusError),
        ):
            with retry:
                response = await self._get_client().request(method, url, **kwargs)
                if response.status_code == 429 or response.status_code >= 500:
                    raise _RetryableVLMStatusError(f"HTTP {response.status_code}: {scrub_log(response.text[:200])}")
                if configuration_4xx and 400 <= response.status_code < 500:
                    detail = scrub_log(response.text[:200])
                    suffix = f": {detail}" if detail else ""
                    raise ConfigurationError(
                        f"VLM request was rejected with HTTP {response.status_code}{suffix}. "
                        "Check the configured endpoint, model, credentials, and request media mode."
                    )
                response.raise_for_status()
                return response
        # ``create_retry_strategy(..., reraise=True)`` always re-raises the
        # final retryable error, so execution cannot reach this line.
        raise AssertionError("unreachable: retry strategy reraises exhausted request errors")

    def _add_model_runtime_options(self, payload: dict[str, Any], duration_seconds: float) -> None:
        model = self._model.lower()
        if self._cosmos_nim_runtime_options and "cosmos" in model and self._media_mode != "frame_base64":
            payload["media_io_kwargs"] = {
                "video": {
                    "num_frames": _dynamic_num_frames(duration_seconds, self._max_frames, self._max_fps),
                }
            }
        if not self._disable_audio and "omni" in model:
            payload["mm_processor_kwargs"] = {"use_audio_in_video": True}

    async def _build_content(
        self,
        *,
        prompt: str,
        clip_url: str,
        duration_seconds: float,
    ) -> list[dict[str, Any]]:
        if self._media_mode == "video_url":
            return [
                {"type": "text", "text": prompt},
                {"type": "video_url", "video_url": {"url": clip_url}},
            ]

        try:
            response = await self._request_with_retries("GET", clip_url, configuration_4xx=False)
        except (httpx.HTTPError, _RetryableVLMStatusError) as e:
            if isinstance(e, httpx.HTTPStatusError):
                detail = f"VST clip download returned HTTP {e.response.status_code}"
            else:
                detail = f"VST clip download failed ({type(e).__name__})"
            # HTTPX exception text and chained tracebacks include the request
            # URL. VST clip URLs may carry short-lived query credentials, so do
            # not copy or chain the raw exception into a user/log-facing error.
            raise BackendUnreachableError("vst", detail) from None
        video_bytes = response.content

        if self._media_mode == "video_base64":
            video_b64 = base64.b64encode(video_bytes).decode("ascii")
            return [
                {"type": "text", "text": prompt},
                {"type": "video_url", "video_url": {"url": f"data:video/mp4;base64,{video_b64}"}},
            ]

        frame_b64s = await asyncio.to_thread(
            _select_base64_frames,
            video_bytes,
            duration_seconds,
            self._max_frames,
            self._max_fps,
        )
        if not frame_b64s:
            raise _FrameExtractionError("No frames selected from VST clip")
        return [
            {
                "type": "text",
                "text": (
                    "The following images are a sequence of frames from a video. "
                    f"Answer the user's question based on the video: {prompt}"
                ),
            },
            *[
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{frame_b64}"}}
                for frame_b64 in frame_b64s
            ],
        ]

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None


def _extract_chat_content(payload: dict[str, Any]) -> str:
    message = payload["choices"][0]["message"]
    content = message.get("content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        pieces: list[str] = []
        for part in content:
            if isinstance(part, dict):
                text = part.get("text")
                if isinstance(text, str):
                    pieces.append(text)
        return "\n".join(pieces)
    return str(content)


def _normalize_base_url(base_url: str) -> str:
    normalized = base_url.rstrip("/")
    if normalized.endswith("/chat/completions") or normalized.endswith("/v1"):
        return normalized
    return f"{normalized}/v1"


def _duration_seconds(start_timestamp: str, end_timestamp: str, time_format: Literal["iso", "offset"]) -> float:
    if time_format == "offset":
        return max(float(end_timestamp) - float(start_timestamp), 1.0)
    start = _parse_iso(start_timestamp)
    end = _parse_iso(end_timestamp)
    return max((end - start).total_seconds(), 1.0)


def _dynamic_num_frames(duration_seconds: float, max_frames: int, max_fps: int) -> int:
    return max(min(int(duration_seconds) * max_fps, max_frames), 1)


def _parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _select_base64_frames(video_bytes: bytes, duration_seconds: float, max_frames: int, max_fps: int) -> list[str]:
    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=True) as tmp:
        tmp.write(video_bytes)
        tmp.flush()
        step_size = max(duration_seconds / max_frames, 1.0 / max_fps)
        return _frame_select(tmp.name, duration_seconds, step_size)


def _frame_select(video_path: str, duration_seconds: float, step_size: float) -> list[str]:
    cap = cv2.VideoCapture(str(Path(video_path)))
    if not cap.isOpened():
        raise _FrameExtractionError(f"Could not open video file: {video_path}")
    try:
        fps = cap.get(cv2.CAP_PROP_FPS)
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        if fps <= 0 or total_frames <= 0:
            raise _FrameExtractionError("Video has no readable frames")
        end_frame = min(total_frames - 1, math.ceil(duration_seconds * fps))
        step_size_frame = max(1, math.floor(step_size * fps))
        selected_frames = list(range(0, end_frame, step_size_frame))
        if not selected_frames:
            selected_frames = [0]

        base64_frames: list[str] = []
        for frame_idx in selected_frames:
            cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
            ok, frame = cap.read()
            if not ok:
                raise _FrameExtractionError(f"Could not read frame {frame_idx} from {video_path}")
            ok, buffer = cv2.imencode(".jpg", frame)
            if not ok:
                raise _FrameExtractionError(f"Could not encode frame {frame_idx} from {video_path}")
            base64_frames.append(base64.b64encode(buffer.tobytes()).decode("ascii"))
        return base64_frames
    finally:
        cap.release()
