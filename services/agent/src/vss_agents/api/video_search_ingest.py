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
Deprecated: search-specific video ingest endpoints.

These ``/api/v1/videos-for-search/*`` routes predate the universal video
upload flow in ``vss_agents.api.videos`` and are retained only for backward
compatibility with the Video Management UI tab, which still uploads chunks
directly to VST and notifies the agent here. New callers should use the
generic chunk-proxy + ``upload-complete`` pair from ``videos.py`` instead;
both register on every profile and the ``upload-complete`` hook handles
ingestion when RTVI is configured.

Routes (all marked ``deprecated=True`` in the OpenAPI schema):
    PUT  /api/v1/videos-for-search/{filename}            — streamed PUT upload
    POST /api/v1/videos-for-search/{filename}/complete   — post-processing hook
"""

import logging
import os
from typing import Any

from fastapi import APIRouter
from fastapi import FastAPI
from fastapi import HTTPException
from fastapi import Request
import httpx

from vss_agents.api.videos import VideoIngestResponse
from vss_agents.api.videos import VideoUploadCompleteInput
from vss_agents.api.videos import _run_post_upload_processing
from vss_agents.utils.time_measure import TimeMeasure

logger = logging.getLogger(__name__)

# Allowed video MIME types - Only MP4 and MKV as supported
ALLOWED_VIDEO_TYPES = {
    "video/mp4",  # .mp4
    "video/x-matroska",  # .mkv
}


def create_video_search_ingest_router(
    vst_internal_url: str,
    rtvi_embed_base_url: str,
    rtvi_cv_base_url: str = "",
    rtvi_embed_model: str = "cosmos-embed1-448p",
    rtvi_embed_chunk_duration: int = 5,
) -> APIRouter:
    """
    Build the deprecated /api/v1/videos-for-search/* router.

    Routes are flagged ``deprecated=True`` in the OpenAPI schema. New code
    should use the universal ``/api/v1/videos/chunked/upload`` +
    ``/api/v1/videos/{filename}/upload-complete`` pair from
    ``vss_agents.api.videos`` instead.
    """
    router = APIRouter()

    @router.put(
        "/api/v1/videos-for-search/{filename}",
        response_model=VideoIngestResponse,
        summary="Upload video to VST (deprecated)",
        description=(
            "Deprecated: streamed PUT upload to VST. Prefer the universal "
            "POST /api/v1/videos/chunked/upload + POST /api/v1/videos/"
            "{filename}/upload-complete pair."
        ),
        tags=["Video Ingest"],
        deprecated=True,
    )
    async def upload_video_to_vst(
        filename: str,
        request: Request,
    ) -> VideoIngestResponse:
        """
        This endpoint:
        1. Receives raw binary data from request body
        2. Streams directly to VST without ANY intermediate storage
        3. Call VST to get the timelines of uploaded video
        4. Call VST to get the video url
        5. Call RTVI Embed to generate embeddings for the video
        6. Return the video id and the number of chunks processed

        Client must send:
        - Content-Type: allowed video MIME types (mp4, mkv)
        - Content-Length: <file_size>
        - Body: Raw binary video data
        """
        start_timestamp = "2025-01-01T00:00:00.000Z"

        # Remove file extension if present to get video ID
        video_id = filename.rsplit(".", 1)[0] if "." in filename else filename

        # Construct VST upload URL
        vst_url = vst_internal_url.rstrip("/")
        vst_upload_url = f"{vst_url}/vst/api/v1/storage/file/{video_id}/{start_timestamp}"

        # Get headers from request
        content_type = request.headers.get("content-type")
        content_length = request.headers.get("content-length")

        # Validate Content-Type is present and valid
        if not content_type:
            logger.error("Content-Type header is missing")
            raise HTTPException(
                status_code=400,
                detail="Content-Type header is required. Must be a video format (e.g., video/mp4, video/x-matroska)",
            )

        if content_type not in ALLOWED_VIDEO_TYPES:
            logger.error(f"Unsupported video format: {content_type}")
            raise HTTPException(
                status_code=415,
                detail=f"Unsupported video format: {content_type}. Supported formats: {', '.join(sorted(ALLOWED_VIDEO_TYPES))}",
            )

        logger.debug(f"Content-Type validated: {content_type}")

        # Validate Content-Length is present
        if not content_length:
            logger.error("Content-Length header is required")
            raise HTTPException(status_code=400, detail="Content-Length header is required")

        try:
            content_length_int = int(content_length)
            if content_length_int == 0:
                logger.error("Content-Length is 0")
                raise HTTPException(status_code=400, detail="File is empty")
        except ValueError as e:
            logger.error(f"Invalid Content-Length: {content_length}")
            raise HTTPException(status_code=400, detail="Invalid Content-Length header") from e

        try:
            # Stream directly from request to VST (no intermediate storage).
            async with httpx.AsyncClient(timeout=300.0) as client:
                logger.info(f"Streaming directly from client to VST at {vst_upload_url}")

                with TimeMeasure("video_ingest: stream upload to VST"):
                    vst_response = await client.put(
                        vst_upload_url,
                        content=request.stream(),
                        headers={"Content-Type": content_type, "Content-Length": content_length},
                    )

                logger.info(f"VST upload response status: {vst_response.status_code}")
                if vst_response.status_code not in (200, 201):
                    error_msg = f"VST upload failed with status {vst_response.status_code}: {vst_response.text}"
                    logger.error(error_msg)
                    raise HTTPException(status_code=502, detail=f"VST upload failed: {error_msg}")

                vst_result = vst_response.json()
                logger.info(f"VST upload successful - Streamed {content_length_int} bytes")
                logger.debug(f"VST response body: {vst_result}")

                vst_sensor_id = vst_result.get("sensorId")
                if not vst_sensor_id:
                    error_msg = f"VST response missing 'sensorId' field: {vst_result}"
                    logger.error(error_msg)
                    raise HTTPException(status_code=502, detail=f"VST response invalid: {error_msg}")

                logger.info(f"VST sensor ID: {vst_sensor_id}")

                vst_filename = vst_result.get("filename", filename)
                logger.info(f"VST filename: {vst_filename}")

                return await _run_post_upload_processing(
                    camera_name=video_id,
                    sensor_id=vst_sensor_id,
                    filename=vst_filename,
                    vst_url=vst_url,
                    rtvi_embed_base_url=rtvi_embed_base_url,
                    rtvi_cv_base_url=rtvi_cv_base_url,
                    rtvi_embed_model=rtvi_embed_model,
                    rtvi_embed_chunk_duration=rtvi_embed_chunk_duration,
                )

        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error in streaming video ingest: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail=f"Internal server error: {e!s}") from e

    @router.post(
        "/api/v1/videos-for-search/{filename}/complete",
        response_model=VideoIngestResponse,
        summary="Complete a chunked video upload (deprecated)",
        description=(
            "Deprecated: search-profile completion endpoint. Use POST "
            "/api/v1/videos/{filename}/upload-complete instead — it works "
            "across profiles and runs the same post-processing."
        ),
        tags=["Video Ingest"],
        deprecated=True,
    )
    async def complete_video_upload(
        filename: str,
        body: VideoUploadCompleteInput,
    ) -> VideoIngestResponse:
        """
        Complete a chunked video upload by running post-upload processing.

        The client uploads the file directly to VST in chunks via the nvstreamer protocol,
        then calls this endpoint with the sensorId from the last chunk's response so the
        agent can trigger embedding generation and other post-processing.
        """
        vst_url = vst_internal_url.rstrip("/")
        # Strip the file extension so RTVI-CV's camera_name matches the value
        # the PUT upload path produces for the same filename.
        camera_name = filename.rsplit(".", 1)[0] if "." in filename else filename

        try:
            return await _run_post_upload_processing(
                camera_name=camera_name,
                sensor_id=body.sensor_id,
                filename=filename,
                vst_url=vst_url,
                rtvi_embed_base_url=rtvi_embed_base_url,
                rtvi_cv_base_url=rtvi_cv_base_url,
                rtvi_embed_model=rtvi_embed_model,
                rtvi_embed_chunk_duration=rtvi_embed_chunk_duration,
            )
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error in complete_video_upload: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail=f"Internal server error: {e!s}") from e

    return router


def register_video_search_ingest_routes(app: "FastAPI", config: "Any") -> None:
    """
    Register the deprecated /api/v1/videos-for-search/* routes.

    Reads configuration from ``general.front_end.streaming_ingest`` in the YAML.
    The caller (``CustomFastApiFrontEndWorker``) gates this on the
    ``enable_videos_for_search`` capability flag.

    These routes are kept for backward compatibility with the Video Management
    UI tab; new callers should use the generic
    ``vss_agents.api.videos.register_video_upload`` +
    ``register_video_upload_complete`` pair instead.
    """
    try:
        streaming_config = getattr(config.general.front_end, "streaming_ingest", None)
        if streaming_config is None:
            raise ValueError(
                "streaming_ingest must be configured under general.front_end to register videos-for-search routes"
            )

        if streaming_config:
            vst_internal_url = getattr(streaming_config, "vst_internal_url", None) or os.getenv("VST_INTERNAL_URL")
            rtvi_embed_base_url = getattr(streaming_config, "rtvi_embed_base_url", None)
            rtvi_cv_base_url = getattr(streaming_config, "rtvi_cv_base_url", None) or ""
            rtvi_embed_model = getattr(streaming_config, "rtvi_embed_model", "cosmos-embed1-448p")
            rtvi_embed_chunk_duration = getattr(streaming_config, "rtvi_embed_chunk_duration", 5)
            logger.info("Using streaming_ingest config from YAML for deprecated videos-for-search routes")
        else:
            # Fallback: streaming_ingest not in config (NAT may strip unknown
            # sections). Use environment variables. Require BOTH host AND port
            # to build a URL — empty `RTVI_EMBED_PORT` (e.g. base profile)
            # means RTVI is not configured and the URL stays empty so the
            # /complete handler will skip the embedding step at request time
            # rather than hang on `http://host:`.
            vst_internal_url = os.getenv("VST_INTERNAL_URL")
            host_ip = os.getenv("HOST_IP")
            rtvi_embed_port = os.getenv("RTVI_EMBED_PORT", "")
            rtvi_cv_port = os.getenv("RTVI_CV_PORT", "")
            rtvi_embed_base_url = f"http://{host_ip}:{rtvi_embed_port}" if host_ip and rtvi_embed_port else ""
            rtvi_cv_base_url = f"http://{host_ip}:{rtvi_cv_port}" if host_ip and rtvi_cv_port else ""
            rtvi_embed_model = os.getenv("RTVI_EMBED_MODEL", "cosmos-embed1-448p")
            rtvi_embed_chunk_duration = 5
            logger.info("streaming_ingest not in config, using environment variables")

        if not vst_internal_url:
            raise ValueError("streaming_ingest.vst_internal_url must be set for videos-for-search routes")

        if not rtvi_embed_base_url:
            # RTVI is optional on this deprecated path now. Profiles that don't
            # ingest to RTVI (base/lvs/alerts) just leave rtvi_embed_base_url
            # unset; the /complete handler skips the embedding step at request
            # time. Search profiles still set it. This matches the universal
            # /complete endpoint's behavior in vss_agents.api.videos.
            logger.warning(
                "rtvi_embed_base_url not set on streaming_ingest — "
                "/videos-for-search/*/complete will register but skip embedding generation"
            )

        router = create_video_search_ingest_router(
            vst_internal_url=vst_internal_url,
            rtvi_embed_base_url=rtvi_embed_base_url or "",
            rtvi_cv_base_url=rtvi_cv_base_url or "",
            rtvi_embed_model=rtvi_embed_model,
            rtvi_embed_chunk_duration=rtvi_embed_chunk_duration,
        )
        app.include_router(router)
        logger.info("Registered deprecated /api/v1/videos-for-search/* routes")
    except Exception as e:
        logger.error(f"Failed to register videos-for-search routes: {e}", exc_info=True)
        raise
