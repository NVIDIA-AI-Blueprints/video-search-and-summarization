#!/usr/bin/env python3
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

"""
Async HTTP client for the RTVI VLM microservice.

All methods are coroutines that use ``httpx.AsyncClient`` so calls never
block the asyncio event loop. A single client instance is reused across
calls (one TCP connection pool per :class:`RTVIVLMClient` instance).
"""

import copy
import logging
import re
from typing import Any, Dict, List, Optional

import httpx

from ..schemas import EXTENDED_OPTIONAL_FIELDS

logger = logging.getLogger(__name__)

_USERINFO_RE = re.compile(r"(://)[^@/]+@")
_SENSITIVE_KEYS = frozenset({"password", "username"})


def _redact_stream_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Return a shallow copy with credentials masked for safe logging."""
    safe = copy.deepcopy(payload)
    for stream in safe.get("streams") or []:
        url = stream.get("liveStreamUrl")
        if url:
            stream["liveStreamUrl"] = _USERINFO_RE.sub(r"\1***@", url)
        for key in _SENSITIVE_KEYS:
            if key in stream:
                stream[key] = "***"
    return safe


class RTVIVLMClient:
    """
    Async HTTP client that forwards start/stop requests to the RTVI VLM
    microservice.

    RTVI VLM API endpoints:
    - POST   /streams/add                      — Add live stream(s)
    - GET    /streams/get-stream-info           — List registered live streams
    - DELETE /streams/delete/{stream_id}        — Remove a live stream
    - POST   /generate_captions                 — Start caption generation
    - DELETE /generate_captions/{stream_id}     — Stop caption generation
    - GET    /ready                             — Health check
    """

    def __init__(self, base_url: str, timeout: float = 30):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        # AsyncClient is safe to instantiate outside an event loop;
        # connections are opened lazily on first request.
        self._client = httpx.AsyncClient(timeout=timeout)

    async def aclose(self) -> None:
        """Close the underlying connection pool. Call at app shutdown."""
        await self._client.aclose()

    async def start_stream(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """POST to RTVI VLM /streams/add to begin real-time analysis on an RTSP stream."""
        url = f"{self.base_url}/streams/add"
        # id (= sensor_id from VIOS) is optional: callers that don't have
        # a VIOS-assigned id can omit it (key missing) or set it to None,
        # in which case the field is forwarded as ``null`` and RTVI
        # generates its own stream identifier. ``.get`` rather than
        # ``[...]`` is the difference that lets replay survive against
        # legacy ES documents written before the field existed.
        stream_entry: Dict[str, Any] = {
            "id": payload.get("id"),
            "liveStreamUrl": payload.get("liveStreamUrl") or payload.get("rtsp_url"),
            "description": payload.get("description") or "",
        }

        # Remaining fields are optional — only forwarded when not None.
        optional_fields = (
            "sensor_name", "username", "password",
            "place_name", "place_type",
            "place_lat", "place_lon", "place_alt",
            "place_coordinate_x", "place_coordinate_y",
        )
        for field in optional_fields:
            value = payload.get(field)
            if value is not None:
                stream_entry[field] = value

        rtvi_payload: Dict[str, Any] = {"streams": [stream_entry]}

        logger.info("Calling RTVI VLM streams/add: %s  payload=%s", url, _redact_stream_payload(rtvi_payload))
        resp = await self._client.post(url, json=rtvi_payload)
        if not resp.is_success:
            logger.error(
                "RTVI streams/add returned %s: %s",
                resp.status_code,
                resp.text,
            )
        resp.raise_for_status()
        return resp.json()

    async def get_stream_info(self) -> List[Dict[str, Any]]:
        """GET /streams/get-stream-info — list every live stream RTVI has registered.

        Used by :class:`RealtimeAlertService` to (1) check whether a stream
        with the requested ``sensor_id`` is already registered and reuse
        its ``stream_id`` instead of issuing a duplicate ``/streams/add``,
        and (2) confirm that a freshly-added stream is visible to RTVI
        before declaring the rule active. The endpoint is documented in
        the VSS RT-Embed API reference under "Live Stream".

        Returns the list of stream entries verbatim from RTVI. Common
        envelope shapes:

        * ``{"results": [...]}`` — modern RTVI builds.
        * ``{"streams": [...]}`` — older builds.
        * ``[...]`` — bare list.

        The method normalises all three to a plain ``List[Dict]`` so
        callers don't have to hand-roll the envelope check at every
        call site. Network / HTTP errors propagate as ``httpx.HTTPError``
        so callers can decide whether to fall back (e.g. degrade to
        ``streams/add``) or fail the request.
        """
        url = f"{self.base_url}/streams/get-stream-info"
        logger.debug("Calling RTVI VLM streams/get-stream-info: %s", url)
        resp = await self._client.get(url)
        if not resp.is_success:
            logger.error(
                "RTVI streams/get-stream-info returned %s: %s",
                resp.status_code,
                resp.text,
            )
        resp.raise_for_status()
        body = resp.json()
        if isinstance(body, list):
            return body
        if isinstance(body, dict):
            for key in ("results", "streams", "items", "data"):
                value = body.get(key)
                if isinstance(value, list):
                    return value
        logger.warning(
            "RTVI streams/get-stream-info returned unexpected shape: %r",
            type(body).__name__,
        )
        return []

    async def stop_stream(self, rtvi_stream_id: str) -> Dict[str, Any]:
        """DELETE to RTVI VLM /streams/delete/{stream_id} to stop a running stream."""
        url = f"{self.base_url}/streams/delete/{rtvi_stream_id}"
        logger.info("Calling RTVI VLM streams/delete: %s", url)
        resp = await self._client.delete(url)
        resp.raise_for_status()
        if resp.text.strip():
            return resp.json()
        return {"status": "deleted", "stream_id": rtvi_stream_id}

    async def generate_captions(
        self,
        stream_id: str,
        prompt: str,
        model: str,
        system_prompt: str = "",
        chunk_duration: int = 30,
        chunk_overlap_duration: int = 5,
        alert_category: Optional[str] = None,
        *,
        num_frames_per_second_or_fixed_frames_chunk: int = 10,
        use_fps_for_chunking: bool = True,
        vlm_input_width: int = 256,
        vlm_input_height: int = 256,
        enable_reasoning: bool = True,
        # Extended RTVI VLM options — None means "omit from payload" so
        # RTVI applies its own server-side defaults for that field.
        api_type: Optional[str] = None,
        response_format: Optional[Dict[str, Any]] = None,
        stream_options: Optional[Dict[str, Any]] = None,
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
        top_p: Optional[float] = None,
        top_k: Optional[int] = None,
        ignore_eos: Optional[bool] = None,
        seed: Optional[int] = None,
        media_info: Optional[Dict[str, Any]] = None,
        enable_audio: Optional[bool] = None,
        mm_processor_kwargs: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """POST to /generate_captions with stream=true to trigger VLM analysis.

        Required fields are always sent. Optional fields (those that default
        to ``None``) are included in the payload only when explicitly set so
        RTVI can apply its own server-side defaults for omitted keys.
        """
        url = f"{self.base_url}/generate_captions"
        payload: Dict[str, Any] = {
            "id": stream_id,
            "prompt": prompt,
            "model": model,
            "system_prompt": system_prompt,
            "chunk_duration": chunk_duration,
            "chunk_overlap_duration": chunk_overlap_duration,
            "stream": True,
            "num_frames_per_second_or_fixed_frames_chunk": (
                num_frames_per_second_or_fixed_frames_chunk
            ),
            "use_fps_for_chunking": use_fps_for_chunking,
            "vlm_input_width": vlm_input_width,
            "vlm_input_height": vlm_input_height,
            "enable_reasoning": enable_reasoning,
        }
        if alert_category:
            payload["alert_category"] = alert_category

        # Include optional fields only when set (None → omit, let RTVI default).
        # Driven by EXTENDED_OPTIONAL_FIELDS so adding a new field to
        # AlertRuleConfig only requires updating that one constant.
        _local_kwargs = locals()
        for _field in EXTENDED_OPTIONAL_FIELDS:
            _val = _local_kwargs.get(_field)
            if _val is not None:
                payload[_field] = _val

        logger.info("Calling RTVI VLM generate_captions: %s (stream_id=%s)", url, stream_id)
        logger.debug("generate_captions payload: %s", payload)
        resp = await self._client.post(
            url, json=payload, timeout=max(self.timeout, 120),
        )
        if not resp.is_success:
            logger.error(
                "RTVI generate_captions returned %s: %s",
                resp.status_code,
                resp.text,
            )
        resp.raise_for_status()
        return {"status": "started", "stream_id": stream_id}

    async def stop_captions(self, stream_id: str) -> Dict[str, Any]:
        """DELETE /generate_captions/{stream_id} to stop caption generation."""
        url = f"{self.base_url}/generate_captions/{stream_id}"
        logger.info("Calling RTVI VLM stop generate_captions: %s", url)
        resp = await self._client.delete(url)
        resp.raise_for_status()
        if resp.text.strip():
            return resp.json()
        return {"status": "stopped", "stream_id": stream_id}

    async def health(self) -> bool:
        """Check RTVI VLM readiness via ready endpoint."""
        try:
            resp = await self._client.get(f"{self.base_url}/ready")
            return resp.is_success
        except httpx.HTTPError:
            return False
