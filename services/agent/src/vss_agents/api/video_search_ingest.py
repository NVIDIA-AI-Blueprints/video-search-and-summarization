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
Deprecated: search-specific video ingest endpoint.

This module exists for backward compatibility with callers that still
use ``PUT /api/v1/videos-for-search/{filename}`` — most notably the
``metromind/ci-vss-oss`` CI test fixture for the search profile. New
callers should use the universal three-step flow in
``vss_agents.api.video_ingest`` instead:

    POST /api/v1/videos                              # get upload URL
    POST {url}/v1/storage/file                       # chunked upload
    POST /api/v1/videos/{sensor_id}/complete         # post-processing

The route below is registered with ``deprecated=True`` in the OpenAPI
schema. It will be removed once the CI test fixture migrates to the
new flow (tracked separately).
"""

import logging
from typing import Any

from fastapi import APIRouter
from fastapi import FastAPI
from fastapi import HTTPException
from fastapi import Request
import httpx

from vss_agents.api.video_ingest import DEFAULT_RTVI_CV_TIMEOUT_SECONDS
from vss_agents.api.video_ingest import DEFAULT_RTVI_EMBED_TIMEOUT_SECONDS
from vss_agents.api.video_ingest import DEFAULT_VST_STORAGE_TIMEOUT_SECONDS
from vss_agents.api.video_ingest import DEFAULT_VST_UPLOAD_TIMEOUT_SECONDS
from vss_agents.api.video_ingest import VideoIngestResponse
from vss_agents.api.video_ingest import _resolve_video_upload_config
from vss_agents.api.video_ingest import _run_post_upload_processing
from vss_agents.utils.time_measure import TimeMeasure

logger = logging.getLogger(__name__)

# MIME types the legacy PUT path accepts; matches what the CI test fixture sends.
_ALLOWED_VIDEO_TYPES = {
    "video/mp4",
    "video/x-matroska",
}


def create_video_search_ingest_router(
    vst_internal_url: str,
    rtvi_embed_base_url: str,
    rtvi_cv_base_url: str = "",
    rtvi_embed_model: str = "cosmos-embed1-448p",
    rtvi_embed_chunk_duration: int = 5,
    vst_upload_timeout_seconds: float = DEFAULT_VST_UPLOAD_TIMEOUT_SECONDS,
    rtvi_cv_timeout_seconds: float = DEFAULT_RTVI_CV_TIMEOUT_SECONDS,
    rtvi_embed_timeout_seconds: float = DEFAULT_RTVI_EMBED_TIMEOUT_SECONDS,
    vst_storage_timeout_seconds: float = DEFAULT_VST_STORAGE_TIMEOUT_SECONDS,
) -> APIRouter:
    """Build the deprecated ``PUT /api/v1/videos-for-search/{filename}`` router.

    The route is flagged ``deprecated=True`` in the OpenAPI schema. New
    callers should use the universal three-step flow in
    ``vss_agents.api.video_ingest`` instead.
    """
    router = APIRouter()

    @router.put(
        "/api/v1/videos-for-search/{filename}",
        response_model=VideoIngestResponse,
        summary="Upload video to VST (deprecated)",
        description=(
            "Deprecated: streamed PUT upload to VST in a single request. "
            "Prefer the universal three-step flow: "
            "POST /api/v1/videos → chunked POST to VST → "
            "POST /api/v1/videos/{sensor_id}/complete."
        ),
        tags=["Video Ingest (deprecated)"],
        deprecated=True,
    )
    async def upload_video_to_vst(filename: str, request: Request) -> VideoIngestResponse:
        """Stream the request body straight to VST's storage PUT endpoint,
        then run the same post-processing pipeline ``/complete`` would run.
        """
        start_timestamp = "2025-01-01T00:00:00.000Z"

        # Use the filename (sans extension) as the camera_name / pre-upload id.
        # VST will return its own sensorId on success; we use that for
        # post-processing so RTVI-CV / embed work the same as the chunked path.
        camera_name = filename.rsplit(".", 1)[0] if "." in filename else filename

        vst_url = vst_internal_url.rstrip("/")
        vst_upload_url = f"{vst_url}/vst/api/v1/storage/file/{camera_name}/{start_timestamp}"

        content_type = request.headers.get("content-type")
        content_length = request.headers.get("content-length")

        if not content_type:
            raise HTTPException(
                status_code=400,
                detail="Content-Type header is required (video/mp4 or video/x-matroska).",
            )
        if content_type not in _ALLOWED_VIDEO_TYPES:
            raise HTTPException(
                status_code=415,
                detail=(
                    f"Unsupported video format: {content_type}. Supported: {', '.join(sorted(_ALLOWED_VIDEO_TYPES))}."
                ),
            )
        if not content_length:
            raise HTTPException(status_code=400, detail="Content-Length header is required.")
        try:
            content_length_int = int(content_length)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Invalid Content-Length header.") from exc
        if content_length_int == 0:
            raise HTTPException(status_code=400, detail="File is empty.")

        try:
            async with httpx.AsyncClient(timeout=vst_upload_timeout_seconds) as client:
                logger.info("Streaming PUT upload to VST at %s", vst_upload_url)
                with TimeMeasure("video_search_ingest: stream upload to VST"):
                    vst_response = await client.put(
                        vst_upload_url,
                        content=request.stream(),
                        headers={"Content-Type": content_type, "Content-Length": content_length},
                    )

                if vst_response.status_code not in (200, 201):
                    error_msg = f"VST upload failed ({vst_response.status_code}): {vst_response.text}"
                    logger.error(error_msg)
                    raise HTTPException(status_code=502, detail=error_msg)

                vst_result = vst_response.json()
                vst_sensor_id = vst_result.get("sensorId")
                if not vst_sensor_id:
                    raise HTTPException(
                        status_code=502,
                        detail=f"VST upload response missing 'sensorId': {vst_result}",
                    )

                vst_filename = vst_result.get("filename", filename)
                logger.info(
                    "VST upload complete — sensorId=%s, filename=%s, bytes=%d",
                    vst_sensor_id,
                    vst_filename,
                    content_length_int,
                )

                return await _run_post_upload_processing(
                    camera_name=camera_name,
                    sensor_id=vst_sensor_id,
                    filename=vst_filename,
                    vst_url=vst_url,
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
            logger.error("Error in legacy PUT upload for %s: %s", filename, exc, exc_info=True)
            raise HTTPException(status_code=500, detail=f"Internal server error: {exc!s}") from exc

    return router


def register_video_search_ingest_routes(app: "FastAPI", config: "Any") -> None:
    """Register the deprecated ``PUT /api/v1/videos-for-search/{filename}`` route.

    Skipped (with a warning) when VST isn't configured. Otherwise the route
    is registered with ``deprecated=True`` so the OpenAPI schema surfaces
    its compat-only status. Will be removed once the CI test fixture in
    ``metromind/ci-vss-oss`` migrates to the new three-step upload flow.
    """
    try:
        cfg = _resolve_video_upload_config(config)
        if cfg is None:
            logger.warning("VST_INTERNAL_URL not set — skipping deprecated PUT /api/v1/videos-for-search/{filename}")
            return

        app.include_router(
            create_video_search_ingest_router(
                vst_internal_url=cfg.vst_internal_url,
                rtvi_embed_base_url=cfg.rtvi_embed_base_url,
                rtvi_cv_base_url=cfg.rtvi_cv_base_url,
                rtvi_embed_model=cfg.rtvi_embed_model,
                rtvi_embed_chunk_duration=cfg.rtvi_embed_chunk_duration,
                vst_upload_timeout_seconds=cfg.vst_upload_timeout_seconds,
                rtvi_cv_timeout_seconds=cfg.rtvi_cv_timeout_seconds,
                rtvi_embed_timeout_seconds=cfg.rtvi_embed_timeout_seconds,
                vst_storage_timeout_seconds=cfg.vst_storage_timeout_seconds,
            )
        )
        logger.info("Registered deprecated PUT /api/v1/videos-for-search/{filename}")
    except Exception as exc:
        logger.error("Failed to register deprecated videos-for-search route: %s", exc, exc_info=True)
        raise
