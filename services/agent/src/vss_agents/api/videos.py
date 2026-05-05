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
Profile-agnostic video upload completion.

The UI uploads chunks directly to VST's nvstreamer endpoint (the same path
Video Management already uses), then calls this module's universal
``POST /api/v1/videos/{filename}/complete`` for post-processing: timeline
lookup, storage URL resolution, optional RTVI-CV register, and optional
embedding generation. Each post-processing step skips gracefully if its
backing service isn't configured, so this single endpoint works on every
profile — search profiles get ingestion (RTVI-CV + embeddings) for free,
base/lvs/alerts profiles complete the upload without it.

The legacy ``/api/v1/videos-for-search/*`` routes in ``video_search_ingest``
remain registered (deprecated) so existing UI clients keep working until
they migrate to this single ``/complete`` endpoint.
"""

import json
import logging
import os
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


class VideoIngestResponse(BaseModel):
    """Response for video ingest endpoint."""

    message: str = Field(..., description="Status message indicating completion")
    video_id: str = Field(..., description="The video ID used for storage")
    filename: str = Field(..., description="The filename returned by VST after upload")
    chunks_processed: int = Field(default=0, description="Number of chunks processed")


class VideoUploadCompleteInput(BaseModel):
    """Input for the upload-complete endpoint (chunked upload post-processing).

    The UI forwards the full video-storage upload response as the request body
    so it stays decoupled from the storage API shape. We extract the single
    field the post-processing pipeline needs (sensorId) and ignore the rest.
    Accepts both the camelCase ``sensorId`` (as VST returns it) and the
    snake_case ``sensor_id`` for backward compatibility.
    """

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    sensor_id: str = Field(
        ...,
        alias="sensorId",
        min_length=1,
        description="Video stream identifier from the upload response",
    )


class _VideoUploadConfig(BaseModel):
    """Resolved settings for the video upload + upload-complete routes.

    Built once at registration time from ``streaming_ingest`` (preferred) or
    environment variables (fallback for profiles where NAT strips the
    section). ``vst_internal_url`` is the only required field — the RTVI URLs
    are optional and downstream calls self-skip when their URL is empty.
    """

    vst_internal_url: str
    rtvi_embed_base_url: str = ""
    rtvi_cv_base_url: str = ""
    rtvi_embed_model: str = "cosmos-embed1-448p"
    rtvi_embed_chunk_duration: int = 5


def _resolve_video_upload_config(config: "Any") -> _VideoUploadConfig | None:
    """Read upload settings from YAML ``streaming_ingest`` with env-var fallback.

    Returns None when ``VST_INTERNAL_URL`` can't be resolved — the caller logs
    and skips registration so the agent boots without these routes.
    """
    streaming_config = getattr(getattr(config.general, "front_end", None), "streaming_ingest", None)

    if streaming_config:
        vst_internal_url = getattr(streaming_config, "vst_internal_url", None) or os.getenv("VST_INTERNAL_URL", "")
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
        host_ip = os.getenv("HOST_IP", "")
        rtvi_embed_port = os.getenv("RTVI_EMBED_PORT", "")
        rtvi_cv_port = os.getenv("RTVI_CV_PORT", "")
        rtvi_embed_base_url = f"http://{host_ip}:{rtvi_embed_port}" if host_ip and rtvi_embed_port else ""
        rtvi_cv_base_url = f"http://{host_ip}:{rtvi_cv_port}" if host_ip and rtvi_cv_port else ""
        rtvi_embed_model = os.getenv("RTVI_EMBED_MODEL", "cosmos-embed1-448p")
        rtvi_embed_chunk_duration = 5

    if not vst_internal_url:
        return None

    return _VideoUploadConfig(
        vst_internal_url=vst_internal_url,
        rtvi_embed_base_url=rtvi_embed_base_url,
        rtvi_cv_base_url=rtvi_cv_base_url,
        rtvi_embed_model=rtvi_embed_model,
        rtvi_embed_chunk_duration=rtvi_embed_chunk_duration,
    )


async def _run_post_upload_processing(
    camera_name: str,
    sensor_id: str,
    filename: str,
    vst_url: str,
    rtvi_embed_base_url: str,
    rtvi_cv_base_url: str = "",
    rtvi_embed_model: str = "cosmos-embed1-448p",
    rtvi_embed_chunk_duration: int = 5,
) -> VideoIngestResponse:
    """
    Run post-upload processing: get timeline, get video URL, add to RTVI-CV, generate embeddings.

    Shared between the deprecated streaming PUT endpoint
    (``/api/v1/videos-for-search/{filename}``) and the universal
    ``/api/v1/videos/{filename}/upload-complete`` endpoint.

    Args:
        camera_name: Identifier sent as RTVI-CV ``camera_name``. Callers should pass
            the filename without extension so the value is stable regardless of
            which upload path was used. Note this is distinct from ``sensor_id``;
            the returned ``VideoIngestResponse.video_id`` is set to ``sensor_id``,
            not to ``camera_name``.
        sensor_id: Stream id returned by VST after upload. Used for timeline
            lookup, storage URL resolution, and as the ``video_id`` in the
            response.
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

    async with httpx.AsyncClient(timeout=60.0) as client:
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

    # Add to RTVI-CV (if configured). The URL parser rejects empty, scheme-only,
    # and "http://host:" (no port body) forms — anything that wouldn't connect.
    parsed_cv = _parse_optional_http_url(rtvi_cv_base_url)
    if parsed_cv is not None:
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
            async with httpx.AsyncClient(timeout=60.0) as rtvi_cv_client:
                with TimeMeasure("video_ingest: register with RTVI-CV"):
                    rtvi_cv_response = await rtvi_cv_client.post(rtvi_cv_add_url, json=rtvi_cv_payload)

                if rtvi_cv_response.status_code not in (200, 201):
                    error_msg = f"RTVI-CV returned {rtvi_cv_response.status_code}: {rtvi_cv_response.text}"
                    logger.error(error_msg)
                    raise HTTPException(status_code=502, detail=f"RTVI-CV add failed: {error_msg}")

                logger.info(f"RTVI-CV video added: {sensor_id}")
        except httpx.ConnectError:
            logger.warning("RTVI-CV not reachable at %s, skipping (service may not be deployed)", rtvi_cv_add_url)
        except httpx.TimeoutException:
            logger.warning("RTVI-CV timed out at %s, skipping", rtvi_cv_add_url)
    else:
        logger.info("RTVI-CV not configured, skipping")

    # Trigger embedding generation (skip if the embed service isn't configured).
    # Uses the same parser as RTVI-CV for consistency — hostname-only URLs
    # relying on the scheme's default port are accepted.
    parsed_embed = _parse_optional_http_url(rtvi_embed_base_url)
    chunks_processed = 0

    if parsed_embed is None:
        logger.info("RTVI Embed not configured, skipping embedding generation")
    else:
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

        async with httpx.AsyncClient(timeout=600.0) as client:
            with TimeMeasure("video_ingest: generate embeddings (RTVI)"):
                embed_response = await client.post(
                    embedding_url,
                    json=embed_request,
                    headers={"accept": "application/json", "Content-Type": "application/json"},
                )

            if embed_response.status_code != 200:
                error_msg = (
                    f"Embedding generation failed with status {embed_response.status_code}: {embed_response.text}"
                )
                logger.error(error_msg)
                raise HTTPException(status_code=502, detail=f"Embedding generation failed: {error_msg}")

            embed_result = embed_response.json()
            logger.info("RTVI Embedding generation successful")
            chunks_processed = embed_result.get("usage", {}).get("total_chunks_processed", 0)

    message = (
        f"Video {filename} successfully uploaded to VST and embeddings generated"
        if parsed_embed is not None
        else f"Video {filename} successfully uploaded to VST"
    )
    return VideoIngestResponse(
        message=message,
        video_id=sensor_id,
        filename=filename,
        chunks_processed=chunks_processed,
    )


def create_video_upload_complete_router(
    vst_internal_url: str,
    rtvi_embed_base_url: str = "",
    rtvi_cv_base_url: str = "",
    rtvi_embed_model: str = "cosmos-embed1-448p",
    rtvi_embed_chunk_duration: int = 5,
) -> APIRouter:
    """Build the universal ``POST /api/v1/videos/{filename}/complete`` router."""
    router = APIRouter()

    @router.post(
        "/api/v1/videos/{filename}/complete",
        response_model=VideoIngestResponse,
        summary="Complete a chunked video upload",
        description=(
            "Universal completion endpoint. Called by the UI after the last chunk "
            "lands. Runs timeline lookup → storage URL resolution → optional "
            "RTVI-CV register → optional embedding generation. Each step skips "
            "gracefully if its backing service isn't configured, so this works "
            "across profiles; for search profiles the RTVI-CV/embedding hooks "
            "drive ingestion."
        ),
        tags=["Video Ingest"],
    )
    async def upload_complete(filename: str, body: VideoUploadCompleteInput) -> VideoIngestResponse:
        # Strip the extension so RTVI-CV's camera_name matches what the
        # search-profile streaming PUT produces for the same filename.
        camera_name = filename.rsplit(".", 1)[0] if "." in filename else filename

        try:
            return await _run_post_upload_processing(
                camera_name=camera_name,
                sensor_id=body.sensor_id,
                filename=filename,
                vst_url=vst_internal_url,
                rtvi_embed_base_url=rtvi_embed_base_url,
                rtvi_cv_base_url=rtvi_cv_base_url,
                rtvi_embed_model=rtvi_embed_model,
                rtvi_embed_chunk_duration=rtvi_embed_chunk_duration,
            )
        except HTTPException:
            raise
        except Exception as exc:
            logger.error("/complete failed for %s: %s", filename, exc, exc_info=True)
            raise HTTPException(status_code=500, detail=f"Post-processing failed: {exc}") from exc

    return router


def register_video_upload_complete(app: "FastAPI", config: "Any") -> None:
    """Register ``POST /api/v1/videos/{filename}/complete``.

    Embedding and RTVI-CV URLs are passed through when configured and the
    handler self-skips downstream calls when they aren't — so base/alerts/lvs
    profiles get a working completion path that just doesn't register
    embeddings.
    """
    try:
        cfg = _resolve_video_upload_config(config)
        if cfg is None:
            logger.warning("VST_INTERNAL_URL not set — skipping POST /api/v1/videos/{filename}/complete")
            return

        app.include_router(
            create_video_upload_complete_router(
                vst_internal_url=cfg.vst_internal_url,
                rtvi_embed_base_url=cfg.rtvi_embed_base_url,
                rtvi_cv_base_url=cfg.rtvi_cv_base_url,
                rtvi_embed_model=cfg.rtvi_embed_model,
                rtvi_embed_chunk_duration=cfg.rtvi_embed_chunk_duration,
            )
        )
        logger.info("Registered POST /api/v1/videos/{filename}/complete")
    except Exception as exc:
        logger.error("Failed to register video upload-complete route: %s", exc, exc_info=True)
        raise
