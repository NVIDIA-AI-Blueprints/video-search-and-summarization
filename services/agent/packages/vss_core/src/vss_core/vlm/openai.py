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

import base64
from datetime import datetime
import logging
from typing import TYPE_CHECKING
from typing import Any
from typing import Literal

import httpx

from vss_core._foundation.errors import BackendUnreachableError
from vss_core._foundation.errors import ConfigurationError
from vss_core._foundation.retry import create_retry_strategy
from vss_core._foundation.sanitize import scrub_log

if TYPE_CHECKING:
    from vss_core.vios.protocols import VSTSnapshot

logger = logging.getLogger(__name__)

# ``frame_base64`` remains accepted as a compatibility alias, but now sends the
# native MP4 just like ``video_base64`` instead of extracting JPEG frames.
MediaMode = Literal["video_url", "video_base64", "frame_base64"]
VideoURLScope = Literal["internal", "external"]
_VLM_RETRYABLE_ERRORS = (httpx.TimeoutException, httpx.TransportError)


class _RetryableVLMStatusError(Exception):
    """Raised for HTTP 5xx responses so tenacity retries the request."""


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
        rt_vlm_frame_budget: int | None = None,
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
        if rt_vlm_frame_budget is not None and rt_vlm_frame_budget < 1:
            raise ConfigurationError("VLM rt_vlm_frame_budget must be >= 1")
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
        self._rt_vlm_frame_budget = rt_vlm_frame_budget
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
            clip_url = await self._vst.get_video_clip_url(
                sensor_id=sensor_id,
                start_timestamp=start_timestamp,
                end_timestamp=end_timestamp,
                time_format=time_format,
                internal=self._video_url_scope == "internal",
                disable_audio=self._disable_audio,
            )
            duration_seconds = _duration_seconds(start_timestamp, end_timestamp, time_format)
            content = await self._build_content(
                prompt=prompt,
                clip_url=clip_url,
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
        if self._cosmos_nim_runtime_options and "cosmos" in model:
            payload["media_io_kwargs"] = {
                "video": {
                    "num_frames": _dynamic_num_frames(duration_seconds, self._max_frames, self._max_fps),
                }
            }
        if self._rt_vlm_frame_budget is not None:
            # RT-VLM does its own preprocessing and ignores media_io_kwargs, but
            # it defaults the budget to 0 (opening frame only) when absent, so a
            # window-scoped question would be answered from a single frame.
            payload["num_frames_per_second_or_fixed_frames_chunk"] = self._rt_vlm_frame_budget
        if not self._disable_audio and "omni" in model:
            payload["mm_processor_kwargs"] = {"use_audio_in_video": True}

    async def _build_content(
        self,
        *,
        prompt: str,
        clip_url: str,
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

        video_b64 = base64.b64encode(video_bytes).decode("ascii")
        return [
            {"type": "text", "text": prompt},
            {"type": "video_url", "video_url": {"url": f"data:video/mp4;base64,{video_b64}"}},
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
