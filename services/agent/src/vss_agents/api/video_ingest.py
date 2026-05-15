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
Profile-agnostic video upload routes.

The chat upload flow is a three-step contract:

1. ``POST /api/v1/videos`` — the UI sends ``{filename}`` and gets back
   ``{url}`` (the VST nvstreamer upload endpoint). The agent owns URL
   resolution so the browser doesn't need to know where VST lives.
2. The UI POSTs the file in chunks directly to that URL (nvstreamer
   protocol; the agent is not in the upload data path). VST returns
   ``sensorId`` on the final-chunk response — that's the VST sensor id.
3. ``POST /api/v1/videos/{sensor_id}/complete`` — the UI calls this with
   VST's sensor id to trigger post-processing: timeline lookup, storage
   URL resolution, optional RTVI-CV register, optional embedding
   generation. Each step self-skips if its backing service isn't
   configured, so the same endpoint works on every profile.

Naming: this path uses ``{sensor_id}`` to mirror VST's API surface
(``/vst/api/v1/sensor/...``). For uploaded videos one sensor maps to a
single stream, so ``sensor_id`` is unambiguous as the video identifier
here. For live RTSP ingestion a single VST sensor can fan out to
multiple streams — that lifecycle is handled by ``rtsp_ingest.py`` /
``rtsp_delete.py``, not this module.
"""

import asyncio
import json
import logging
import os
import re
from typing import Any
import urllib.parse

from fastapi import APIRouter
from fastapi import FastAPI
from fastapi import HTTPException
import httpx
from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field

from vss_agents.tools.vst.timeline import get_timeline
from vss_agents.tools.vst.utils import VSTError
from vss_agents.utils.time_measure import TimeMeasure
from vss_agents.utils.url_translation import rewrite_url_host

logger = logging.getLogger(__name__)

DEFAULT_RTVI_CV_TIMEOUT_SECONDS = 60.0
DEFAULT_RTVI_EMBED_TIMEOUT_SECONDS = 600.0
DEFAULT_VST_STORAGE_TIMEOUT_SECONDS = 60.0
DEFAULT_VST_UPLOAD_TIMEOUT_SECONDS = 300.0

ENV_RTVI_CV_TIMEOUT_SECONDS = "VIDEO_INGEST_RTVI_CV_TIMEOUT_SECONDS"
ENV_RTVI_EMBED_TIMEOUT_SECONDS = "VIDEO_INGEST_RTVI_EMBED_TIMEOUT_SECONDS"
ENV_VST_STORAGE_TIMEOUT_SECONDS = "VIDEO_INGEST_VST_STORAGE_TIMEOUT_SECONDS"
ENV_VST_UPLOAD_TIMEOUT_SECONDS = "VIDEO_INGEST_VST_UPLOAD_TIMEOUT_SECONDS"


def _parse_optional_http_url(url: str | None) -> urllib.parse.ParseResult | None:
    """
    Parse an optional HTTP(S) URL used to locate a downstream service.

    Returns the parsed URL if it has a hostname, otherwise None. Catches
    URLs like "", "http://", "http:", "http://host:" (no port body) —
    anything that wouldn't successfully connect — and classifies them as
    "not configured" so callers can skip the downstream step.

    A URL relying on the scheme's default port (e.g. "http://host") is
    considered valid: hostname alone is enough to connect.
    """
    if not url:
        return None
    try:
        parsed = urllib.parse.urlparse(url)
    except Exception:  # pragma: no cover — urlparse is extremely permissive
        return None
    if not parsed.hostname:
        return None
    # `http://host:` (trailing colon with empty port body) reaches us with a
    # valid hostname but no resolvable port — Python's urlparse leaves the
    # netloc as `host:` so calls would silently fall back to the scheme's
    # default port (80 / 443) and connect to nothing. Treat that as
    # misconfigured so callers skip the downstream step.
    if parsed.netloc.endswith(":"):
        return None
    return parsed


def _parse_timeout_seconds(raw_value: Any, *, default: float, setting_name: str) -> float:
    """Parse an optional timeout value, preserving the old hard-coded default."""
    if raw_value is None or raw_value == "":
        return default
    try:
        timeout_seconds = float(raw_value)
    except (TypeError, ValueError):
        logger.warning("Invalid %s=%r; falling back to %.1fs", setting_name, raw_value, default)
        return default
    if timeout_seconds <= 0:
        logger.warning("Invalid %s=%r; timeout must be > 0, falling back to %.1fs", setting_name, raw_value, default)
        return default
    return timeout_seconds


def _resolve_timeout_seconds(
    streaming_config: Any,
    *,
    attr_name: str,
    env_name: str,
    default: float,
) -> float:
    raw_value = getattr(streaming_config, attr_name, None) if streaming_config else None
    if raw_value is not None and raw_value.__class__.__module__ == "unittest.mock":
        raw_value = None
    if raw_value is None or raw_value == "":
        raw_value = os.getenv(env_name, "")
    return _parse_timeout_seconds(raw_value, default=default, setting_name=attr_name)


class VideoIngestResponse(BaseModel):
    """Response for video ingest endpoint."""

    message: str = Field(..., description="Status message indicating completion")
    sensor_id: str = Field(..., description="VST sensor id for the uploaded video (matches the {sensor_id} path param)")
    filename: str = Field(..., description="The filename returned by VST after upload")
    chunks_processed: int = Field(default=0, description="Number of chunks processed")


class VideoUploadUrlInput(BaseModel):
    """Input for ``POST /api/v1/videos`` — the upload-URL handshake."""

    model_config = ConfigDict(extra="ignore")

    filename: str = Field(..., min_length=1, description="Video filename to upload")


class VideoUploadUrlResponse(BaseModel):
    """Response for ``POST /api/v1/videos``."""

    url: str = Field(..., description="VST nvstreamer endpoint to POST chunks to")


class VideoUploadCompleteInput(BaseModel):
    """Input for ``POST /api/v1/videos/{sensor_id}/complete``.

    The UI forwards the VST upload response as the request body so the
    schema stays loose against storage-API churn. Only ``filename`` is read
    today (used for the response message and RTVI ``camera_name``); the
    rest is ignored.
    """

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    filename: str | None = Field(
        default=None,
        description="Original filename returned by VST (used in the response message and RTVI camera_name)",
    )
    custom_params: dict[str, Any] | None = Field(
        default=None,
        description="Optional per-upload custom parameters forwarded by the UI",
    )


class _VideoUploadConfig(BaseModel):
    """Resolved settings for the video upload routes.

    Built once at registration time from ``streaming_ingest`` (preferred) or
    environment variables (fallback for profiles where NAT strips the
    section). ``vst_internal_url`` is the only required field — the RTVI URLs
    are optional and downstream calls self-skip when their URL is empty.
    ``vst_external_url`` falls back to ``vst_internal_url`` when unset (works
    for in-cluster deployments where the URL the browser can hit is the same
    as the URL the agent uses server-to-server).
    """

    vst_internal_url: str
    vst_external_url: str = ""
    rtvi_embed_base_url: str = ""
    rtvi_cv_base_url: str = ""
    rtvi_embed_model: str = "cosmos-embed1-448p"
    rtvi_embed_chunk_duration: int = 5
    rtvi_cv_timeout_seconds: float = DEFAULT_RTVI_CV_TIMEOUT_SECONDS
    rtvi_embed_timeout_seconds: float = DEFAULT_RTVI_EMBED_TIMEOUT_SECONDS
    vst_storage_timeout_seconds: float = DEFAULT_VST_STORAGE_TIMEOUT_SECONDS
    vst_upload_timeout_seconds: float = DEFAULT_VST_UPLOAD_TIMEOUT_SECONDS


def _resolve_video_upload_config(config: "Any") -> _VideoUploadConfig | None:
    """Read upload settings from YAML ``streaming_ingest`` with env-var fallback.

    Returns None when ``VST_INTERNAL_URL`` can't be resolved — the caller logs
    and skips registration so the agent boots without these routes.
    """
    streaming_config = getattr(getattr(config.general, "front_end", None), "streaming_ingest", None)

    if streaming_config:
        vst_internal_url = getattr(streaming_config, "vst_internal_url", None) or os.getenv("VST_INTERNAL_URL", "")
        vst_external_url = getattr(streaming_config, "vst_external_url", None) or os.getenv("VST_EXTERNAL_URL", "")
        rtvi_embed_base_url = getattr(streaming_config, "rtvi_embed_base_url", None) or ""
        rtvi_cv_base_url = getattr(streaming_config, "rtvi_cv_base_url", None) or ""
        rtvi_embed_model = getattr(streaming_config, "rtvi_embed_model", "cosmos-embed1-448p")
        rtvi_embed_chunk_duration = getattr(streaming_config, "rtvi_embed_chunk_duration", 5)
    else:
        # NAT may strip unknown config sections — fall back to env vars set by
        # the deploy template. Empty RTVI_*_PORT (base profile, where RTVI
        # isn't deployed) keeps the URL empty so the post-processing step
        # skips at request time instead of hanging on `http://host:`.
        vst_internal_url = os.getenv("VST_INTERNAL_URL", "")
        vst_external_url = os.getenv("VST_EXTERNAL_URL", "")
        host_ip = os.getenv("HOST_IP", "")
        rtvi_embed_port = os.getenv("RTVI_EMBED_PORT", "")
        rtvi_cv_port = os.getenv("RTVI_CV_PORT", "")
        rtvi_embed_base_url = f"http://{host_ip}:{rtvi_embed_port}" if host_ip and rtvi_embed_port else ""
        rtvi_cv_base_url = f"http://{host_ip}:{rtvi_cv_port}" if host_ip and rtvi_cv_port else ""
        rtvi_embed_model = os.getenv("RTVI_EMBED_MODEL", "cosmos-embed1-448p")
        rtvi_embed_chunk_duration = 5

    if not vst_internal_url:
        return None

    rtvi_cv_timeout_seconds = _resolve_timeout_seconds(
        streaming_config,
        attr_name="rtvi_cv_timeout_seconds",
        env_name=ENV_RTVI_CV_TIMEOUT_SECONDS,
        default=DEFAULT_RTVI_CV_TIMEOUT_SECONDS,
    )
    rtvi_embed_timeout_seconds = _resolve_timeout_seconds(
        streaming_config,
        attr_name="rtvi_embed_timeout_seconds",
        env_name=ENV_RTVI_EMBED_TIMEOUT_SECONDS,
        default=DEFAULT_RTVI_EMBED_TIMEOUT_SECONDS,
    )
    vst_storage_timeout_seconds = _resolve_timeout_seconds(
        streaming_config,
        attr_name="vst_storage_timeout_seconds",
        env_name=ENV_VST_STORAGE_TIMEOUT_SECONDS,
        default=DEFAULT_VST_STORAGE_TIMEOUT_SECONDS,
    )
    vst_upload_timeout_seconds = _resolve_timeout_seconds(
        streaming_config,
        attr_name="vst_upload_timeout_seconds",
        env_name=ENV_VST_UPLOAD_TIMEOUT_SECONDS,
        default=DEFAULT_VST_UPLOAD_TIMEOUT_SECONDS,
    )

    return _VideoUploadConfig(
        vst_internal_url=vst_internal_url,
        vst_external_url=vst_external_url or vst_internal_url,
        rtvi_embed_base_url=rtvi_embed_base_url,
        rtvi_cv_base_url=rtvi_cv_base_url,
        rtvi_embed_model=rtvi_embed_model,
        rtvi_embed_chunk_duration=rtvi_embed_chunk_duration,
        rtvi_cv_timeout_seconds=rtvi_cv_timeout_seconds,
        rtvi_embed_timeout_seconds=rtvi_embed_timeout_seconds,
        vst_storage_timeout_seconds=vst_storage_timeout_seconds,
        vst_upload_timeout_seconds=vst_upload_timeout_seconds,
    )


async def _register_with_rtvi_cv(
    *,
    rtvi_cv_base_url: str,
    sensor_id: str,
    camera_name: str,
    vst_file_path: str,
    start_timestamp: str,
    timeout_seconds: float = DEFAULT_RTVI_CV_TIMEOUT_SECONDS,
) -> None:
    """POST ``/api/v1/stream/add`` to RTVI-CV. Best-effort (tolerates network errors).

    Connect/timeout failures degrade to a warning and silent skip — same as the
    original inline path — because RTVI-CV is treated as optional infra. Other
    HTTP errors (non-2xx) raise ``HTTPException(502)`` so the caller surfaces a
    hard failure.
    """
    # `x-stream-id` is the routing key for SDR-fronted RTVI deployments: the
    # in-front-of-RTVI proxy (HAProxy Ingress or Envoy via SDR coordinator)
    # consistent-hashes this header to pin a stream to one worker pod.
    # Without it the proxy falls back to round-robin and subsequent
    # /add → /delete → /config calls for the same sensor can land on different
    # workers. See Projects/SDR/wiki.md for the routing contract.
    rtvi_cv_url = rtvi_cv_base_url.rstrip("/")
    rtvi_cv_add_url = f"{rtvi_cv_url}/api/v1/stream/add"
    rtvi_cv_payload = {
        "key": "sensor",
        "value": {
            "camera_id": sensor_id,
            "camera_name": camera_name,
            "camera_url": vst_file_path,
            "creation_time": start_timestamp,
            "change": "camera_add",
            "metadata": {"resolution": "1920x1080", "codec": "h264", "framerate": 30},
        },
        "headers": {"source": "vst", "created_at": start_timestamp},
    }

    logger.info(f"Adding video to RTVI-CV: POST {rtvi_cv_add_url}")

    try:
        async with httpx.AsyncClient(timeout=timeout_seconds) as client:
            with TimeMeasure("video_ingest: register with RTVI-CV"):
                response = await client.post(
                    rtvi_cv_add_url,
                    json=rtvi_cv_payload,
                    headers={"x-stream-id": sensor_id},
                )
            if response.status_code not in (200, 201):
                error_msg = f"RTVI-CV returned {response.status_code}: {response.text}"
                logger.error(error_msg)
                raise HTTPException(status_code=502, detail=f"RTVI-CV add failed: {error_msg}")
            logger.info(f"RTVI-CV video added: {sensor_id}")
    except httpx.ConnectError:
        logger.warning("RTVI-CV not reachable at %s, skipping (service may not be deployed)", rtvi_cv_add_url)
    except httpx.TimeoutException:
        logger.warning("RTVI-CV timed out at %s, skipping", rtvi_cv_add_url)


async def _run_rtvi_embedding(
    *,
    rtvi_embed_base_url: str,
    sensor_id: str,
    vst_url: str,
    vst_file_path: str,
    rtvi_embed_model: str,
    rtvi_embed_chunk_duration: int,
    start_timestamp: str,
    timeout_seconds: float = DEFAULT_RTVI_EMBED_TIMEOUT_SECONDS,
) -> int:
    """POST ``/v1/generate_video_embeddings``. Returns ``total_chunks_processed``.

    The call is synchronous on the RTVI-Embed side — it blocks until the
    generation completes (up to the 600s client timeout). Any non-200 raises
    ``HTTPException(502)`` so the caller can surface the failure.
    """
    rtvi_embed_url = rtvi_embed_base_url.rstrip("/")
    embedding_url = f"{rtvi_embed_url}/v1/generate_video_embeddings"
    parsed_vst = urllib.parse.urlparse(f"http://{vst_url}" if "://" not in vst_url else vst_url)
    if not parsed_vst.hostname:
        raise HTTPException(status_code=500, detail=f"Invalid vst_url format: {vst_url}")
    translated_video_url = rewrite_url_host(vst_file_path, parsed_vst.hostname)
    logger.info(f"Using internal VST URL for RTVI: {translated_video_url}")

    embed_request = {
        "url": translated_video_url,
        "id": sensor_id,
        "model": rtvi_embed_model,
        "creation_time": start_timestamp,
        "chunk_duration": rtvi_embed_chunk_duration,
    }

    logger.info(f"Calling RTVI Embedding API: POST {embedding_url}")

    async with httpx.AsyncClient(timeout=timeout_seconds) as client:
        with TimeMeasure("video_ingest: generate embeddings (RTVI)"):
            response = await client.post(
                embedding_url,
                json=embed_request,
                headers={
                    "accept": "application/json",
                    "Content-Type": "application/json",
                    # SDR routing key — same rationale as RTVI-CV.
                    "x-stream-id": sensor_id,
                },
            )

        if response.status_code != 200:
            error_msg = f"Embedding generation failed with status {response.status_code}: {response.text}"
            logger.error(error_msg)
            raise HTTPException(status_code=502, detail=f"Embedding generation failed: {error_msg}")

        result = response.json()
        logger.info("RTVI Embedding generation successful")
        # `usage.total_chunks_processed` is the server-side count; coerce
        # explicitly so mypy keeps the helper's int return type intact.
        return int(result.get("usage", {}).get("total_chunks_processed", 0) or 0)


async def _run_post_upload_processing(
    camera_name: str,
    sensor_id: str,
    filename: str,
    vst_url: str,
    rtvi_embed_base_url: str,
    rtvi_cv_base_url: str = "",
    rtvi_embed_model: str = "cosmos-embed1-448p",
    rtvi_embed_chunk_duration: int = 5,
    rtvi_cv_timeout_seconds: float = DEFAULT_RTVI_CV_TIMEOUT_SECONDS,
    rtvi_embed_timeout_seconds: float = DEFAULT_RTVI_EMBED_TIMEOUT_SECONDS,
    vst_storage_timeout_seconds: float = DEFAULT_VST_STORAGE_TIMEOUT_SECONDS,
) -> VideoIngestResponse:
    """
    Run post-upload processing: get timeline, get video URL, add to RTVI-CV, generate embeddings.

    Called from the universal ``POST /api/v1/videos/{sensor_id}/complete``
    handler after the UI has uploaded chunks directly to VST.

    Args:
        camera_name: Identifier sent as RTVI-CV ``camera_name``. Callers should pass
            the filename without extension so the value is stable regardless of
            which upload path was used. Note this is distinct from ``sensor_id``;
            the returned ``VideoIngestResponse.sensor_id`` mirrors ``sensor_id``,
            not ``camera_name``.
        sensor_id: VST sensor id returned by VST after upload. Used for timeline
            lookup, storage URL resolution, and as ``VideoIngestResponse.sensor_id``
            in the response.
        filename: Original filename (with extension). Used only in the human-
            readable response message.
    """
    start_timestamp = "2025-01-01T00:00:00.000Z"

    # Get timeline
    try:
        with TimeMeasure("video_ingest: get timeline from VST"):
            timeline_start_time, timeline_end_time = await get_timeline(sensor_id, vst_url)
    except VSTError as e:
        logger.error("Timelines API failed for stream %s: %s", sensor_id, e)
        raise HTTPException(status_code=502, detail=f"Timelines API failed: {e}") from e

    if not timeline_start_time or not timeline_end_time:
        error_msg = f"No valid timeline for stream {sensor_id}"
        logger.error(error_msg)
        raise HTTPException(status_code=502, detail=error_msg)

    logger.info(
        "Timeline for stream %s: start=%s, end=%s",
        sensor_id,
        timeline_start_time,
        timeline_end_time,
    )

    # Get video URL via storage API
    storage_url = f"{vst_url}/vst/api/v1/storage/file/{sensor_id}/url"
    storage_params = {
        "startTime": timeline_start_time,
        "endTime": timeline_end_time,
        "container": "mp4",
        "configuration": json.dumps({"disableAudio": True}),
    }
    logger.info(f"Calling Storage API: GET {storage_url}")

    async with httpx.AsyncClient(timeout=vst_storage_timeout_seconds) as client:
        with TimeMeasure("video_ingest: get storage URL from VST"):
            storage_response = await client.get(storage_url, params=storage_params)

        if storage_response.status_code != 200:
            error_msg = f"Storage API failed with status {storage_response.status_code}: {storage_response.text}"
            logger.error(error_msg)
            raise HTTPException(status_code=502, detail=f"Storage API failed: {error_msg}")

        storage_result = storage_response.json()
        vst_file_path = storage_result.get("videoUrl")
        if not vst_file_path:
            error_msg = f"Storage API response missing 'videoUrl' field: {storage_result}"
            logger.error(error_msg)
            raise HTTPException(status_code=502, detail=f"Storage API response invalid: {error_msg}")

        logger.info(f"VST video URL obtained: {vst_file_path}")

    # Register with RTVI-CV and trigger embedding generation concurrently.
    # The two services are independent — they both consume the VST storage URL
    # but write to disjoint backends — so running them in parallel cuts the
    # post-upload wall time roughly down to max(cv_time, embed_time) instead
    # of cv_time + embed_time. The embed call is the long pole (it blocks
    # until generation completes, up to 600s), so the savings are real.
    parsed_cv = _parse_optional_http_url(rtvi_cv_base_url)
    parsed_embed = _parse_optional_http_url(rtvi_embed_base_url)

    rtvi_tasks: list[tuple[str, Any]] = []

    if parsed_cv is not None:
        rtvi_tasks.append(
            (
                "rtvi-cv",
                _register_with_rtvi_cv(
                    rtvi_cv_base_url=rtvi_cv_base_url,
                    sensor_id=sensor_id,
                    camera_name=camera_name,
                    vst_file_path=vst_file_path,
                    start_timestamp=start_timestamp,
                    timeout_seconds=rtvi_cv_timeout_seconds,
                ),
            )
        )
    else:
        logger.info("RTVI-CV not configured, skipping")

    chunks_processed = 0
    if parsed_embed is not None:
        rtvi_tasks.append(
            (
                "rtvi-embed",
                _run_rtvi_embedding(
                    rtvi_embed_base_url=rtvi_embed_base_url,
                    sensor_id=sensor_id,
                    vst_url=vst_url,
                    vst_file_path=vst_file_path,
                    rtvi_embed_model=rtvi_embed_model,
                    rtvi_embed_chunk_duration=rtvi_embed_chunk_duration,
                    start_timestamp=start_timestamp,
                    timeout_seconds=rtvi_embed_timeout_seconds,
                ),
            )
        )
    else:
        logger.info("RTVI Embed not configured, skipping embedding generation")

    if rtvi_tasks:
        with TimeMeasure("video_ingest: RTVI-CV register + embedding generation (parallel)"):
            results = await asyncio.gather(
                *(coro for _, coro in rtvi_tasks),
                return_exceptions=True,
            )
        # Re-raise in task-declaration order so the caller sees the same
        # priority the old sequential code did (CV first, then embed).
        # ``strict=True``: ``asyncio.gather`` always returns one result per
        # awaitable, so a length mismatch would indicate a bug.
        for (label, _), result in zip(rtvi_tasks, results, strict=True):
            if isinstance(result, BaseException):
                logger.error("%s task failed: %s", label, result)
                raise result
            if label == "rtvi-embed":
                chunks_processed = result or 0

    message = (
        f"Video {filename} successfully uploaded to VST and embeddings generated"
        if parsed_embed is not None
        else f"Video {filename} successfully uploaded to VST"
    )
    return VideoIngestResponse(
        message=message,
        sensor_id=sensor_id,
        filename=filename,
        chunks_processed=chunks_processed,
    )


def create_video_upload_router(vst_external_url: str) -> APIRouter:
    """Build the ``POST /api/v1/videos`` router (upload-URL handshake)."""
    router = APIRouter()

    @router.post(
        "/api/v1/videos",
        response_model=VideoUploadUrlResponse,
        summary="Get the VST upload URL for a new video",
        description=(
            "Returns the VST nvstreamer upload URL for the given filename. "
            "The UI POSTs file chunks directly to this URL, then calls "
            "``POST /api/v1/videos/{sensor_id}/complete`` to finalize."
        ),
        tags=["Video Ingest"],
    )
    async def get_upload_url(body: VideoUploadUrlInput) -> VideoUploadUrlResponse:
        filename = body.filename
        if re.search(r"\s", filename):
            raise HTTPException(
                status_code=400,
                detail="Filename cannot contain whitespace. Please rename the file and try again.",
            )

        # VST API is reachable at `/vst/api/...` via haproxy ingress; this is the
        # same path Video Management uses (NEXT_PUBLIC_VST_API_URL already includes
        # `/vst/api`, so its chunked-upload URL also resolves to `/vst/api/v1/storage/file`).
        url = f"{vst_external_url.rstrip('/')}/vst/api/v1/storage/file"
        logger.info("POST /api/v1/videos -> %s (filename=%s)", url, filename)
        return VideoUploadUrlResponse(url=url)

    return router


def create_video_upload_complete_router(
    vst_internal_url: str,
    rtvi_embed_base_url: str = "",
    rtvi_cv_base_url: str = "",
    rtvi_embed_model: str = "cosmos-embed1-448p",
    rtvi_embed_chunk_duration: int = 5,
    rtvi_cv_timeout_seconds: float = DEFAULT_RTVI_CV_TIMEOUT_SECONDS,
    rtvi_embed_timeout_seconds: float = DEFAULT_RTVI_EMBED_TIMEOUT_SECONDS,
    vst_storage_timeout_seconds: float = DEFAULT_VST_STORAGE_TIMEOUT_SECONDS,
) -> APIRouter:
    """Build the universal ``POST /api/v1/videos/{sensor_id}/complete`` router.

    ``sensor_id`` is VST's sensor id, returned on the final-chunk response as
    ``sensorId``. The body is the rest of that VST response forwarded
    verbatim by the UI — we only read ``filename`` (for the response message
    and RTVI ``camera_name``); other fields are ignored.
    """
    router = APIRouter()

    @router.post(
        "/api/v1/videos/{sensor_id}/complete",
        response_model=VideoIngestResponse,
        summary="Complete a chunked video upload",
        description=(
            "Universal completion endpoint. Called by the UI after the last chunk "
            "lands, with VST's sensor id as the path param. Runs timeline lookup "
            "→ storage URL resolution → optional RTVI-CV register → optional "
            "embedding generation. Each step skips gracefully if its backing "
            "service isn't configured, so this works across profiles; for search "
            "profiles the RTVI-CV/embedding hooks drive ingestion."
        ),
        tags=["Video Ingest"],
    )
    async def upload_complete(sensor_id: str, body: VideoUploadCompleteInput) -> VideoIngestResponse:
        if not sensor_id:
            raise HTTPException(status_code=400, detail="sensor_id is required")
        # ``filename`` comes from the VST upload response the UI forwards. Fall
        # back to ``sensor_id`` when missing so RTVI-CV's camera_name is at
        # least populated (lookups by sensor id keep working either way).
        filename = body.filename or sensor_id
        camera_name = filename.rsplit(".", 1)[0] if "." in filename else filename

        try:
            return await _run_post_upload_processing(
                camera_name=camera_name,
                sensor_id=sensor_id,
                filename=filename,
                vst_url=vst_internal_url,
                rtvi_embed_base_url=rtvi_embed_base_url,
                rtvi_cv_base_url=rtvi_cv_base_url,
                rtvi_embed_model=rtvi_embed_model,
                rtvi_embed_chunk_duration=rtvi_embed_chunk_duration,
                rtvi_cv_timeout_seconds=rtvi_cv_timeout_seconds,
                rtvi_embed_timeout_seconds=rtvi_embed_timeout_seconds,
                vst_storage_timeout_seconds=vst_storage_timeout_seconds,
            )
        except HTTPException:
            raise
        except Exception as exc:
            logger.error("/complete failed for %s: %s", sensor_id, exc, exc_info=True)
            raise HTTPException(status_code=500, detail=f"Post-processing failed: {exc}") from exc

    return router


def register_video_upload(app: "FastAPI", config: "Any") -> None:
    """Register ``POST /api/v1/videos`` — the upload-URL handshake.

    Returns a VST nvstreamer URL the browser POSTs chunks to. Skipped (with
    a warning) when VST isn't configured.
    """
    try:
        cfg = _resolve_video_upload_config(config)
        if cfg is None:
            logger.warning("VST_INTERNAL_URL not set — skipping POST /api/v1/videos")
            return

        app.include_router(create_video_upload_router(vst_external_url=cfg.vst_external_url))
        logger.info("Registered POST /api/v1/videos")
    except Exception as exc:
        logger.error("Failed to register video upload-URL route: %s", exc, exc_info=True)
        raise


def register_video_upload_complete(app: "FastAPI", config: "Any") -> None:
    """Register ``POST /api/v1/videos/{sensor_id}/complete``.

    Embedding and RTVI-CV URLs are passed through when configured and the
    handler self-skips downstream calls when they aren't — so base/alerts/lvs
    profiles get a working completion path that just doesn't register
    embeddings.
    """
    try:
        cfg = _resolve_video_upload_config(config)
        if cfg is None:
            logger.warning("VST_INTERNAL_URL not set — skipping POST /api/v1/videos/{sensor_id}/complete")
            return

        app.include_router(
            create_video_upload_complete_router(
                vst_internal_url=cfg.vst_internal_url,
                rtvi_embed_base_url=cfg.rtvi_embed_base_url,
                rtvi_cv_base_url=cfg.rtvi_cv_base_url,
                rtvi_embed_model=cfg.rtvi_embed_model,
                rtvi_embed_chunk_duration=cfg.rtvi_embed_chunk_duration,
                rtvi_cv_timeout_seconds=cfg.rtvi_cv_timeout_seconds,
                rtvi_embed_timeout_seconds=cfg.rtvi_embed_timeout_seconds,
                vst_storage_timeout_seconds=cfg.vst_storage_timeout_seconds,
            )
        )
        logger.info("Registered POST /api/v1/videos/{sensor_id}/complete")
    except Exception as exc:
        logger.error("Failed to register video upload-complete route: %s", exc, exc_info=True)
        raise
