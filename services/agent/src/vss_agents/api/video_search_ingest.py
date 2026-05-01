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
Custom video ingest endpoint for VSS Search.
This bypasses NAT's standard endpoint pattern to support direct file upload to VST.
"""

import json
import logging
from typing import Any
import urllib.parse

from fastapi import APIRouter
from fastapi import FastAPI
from fastapi import HTTPException
from fastapi import Request
import httpx
from pydantic import BaseModel
from pydantic import Field

from vss_agents.tools.vst.timeline import get_timeline
from vss_agents.tools.vst.utils import VSTError
from vss_agents.utils.time_measure import TimeMeasure
from vss_agents.utils.url_translation import rewrite_url_host

logger = logging.getLogger(__name__)

# Allowed video MIME types - Only MP4 and MKV as supported
ALLOWED_VIDEO_TYPES = {
    "video/mp4",  # .mp4
    "video/x-matroska",  # .mkv
}


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

    model_config = {"populate_by_name": True, "extra": "ignore"}

    sensor_id: str = Field(
        ...,
        alias="sensorId",
        min_length=1,
        description="Video stream identifier from the upload response",
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

    Shared between the streaming PUT endpoint (small files, streamed through agent) and
    the chunked-upload /complete endpoint (large files, uploaded directly to VST in chunks).

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


def create_video_search_ingest_router(
    vst_internal_url: str,
    rtvi_embed_base_url: str,
    rtvi_cv_base_url: str = "",
    rtvi_embed_model: str = "cosmos-embed1-448p",
    rtvi_embed_chunk_duration: int = 5,
) -> APIRouter:
    """
    Create a FastAPI router for video search ingest.

    This router handles raw binary data uploads and streams them directly
    to VST without buffering the entire file in memory/disk.

    Args:
        vst_internal_url: Internal VST URL for API calls (required)
        rtvi_embed_base_url: Base URL for RTVI Embed service (required)
        rtvi_cv_base_url: Base URL for RTVI-CV service (optional, skipped if empty)
        rtvi_embed_model: Model name for RTVI embedding generation (default: cosmos-embed1-448p)
        rtvi_embed_chunk_duration: Chunk duration in seconds for embedding generation (default: 5)

    Returns:
        APIRouter with the video search ingest route
    """
    router = APIRouter()

    @router.put(
        "/api/v1/videos-for-search/{filename}",
        response_model=VideoIngestResponse,
        summary="Upload video to VST",
        description="Upload video file directly from client to VST without ANY intermediate storage",
        tags=["Video Ingest"],
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

        Args:
            filename: Name of the video file (from URL path parameter)
            request: FastAPI Request object for accessing raw stream

        Returns:
            VideoIngestResponse with upload status

        Raises:
            HTTPException: If upload fails
        """
        # Fixed timestamp as per requirements
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
            # Stream directly from request to VST
            # No intermediate storage, only 8KB in memory at a time
            async with httpx.AsyncClient(timeout=300.0) as client:
                logger.info(f"Streaming directly from client to VST at {vst_upload_url}")

                with TimeMeasure("video_ingest: stream upload to VST"):
                    vst_response = await client.put(
                        vst_upload_url,
                        content=request.stream(),
                        headers={"Content-Type": content_type, "Content-Length": content_length},
                    )

                # Check VST response
                logger.info(f"VST upload response status: {vst_response.status_code}")
                if vst_response.status_code not in (200, 201):
                    error_msg = f"VST upload failed with status {vst_response.status_code}: {vst_response.text}"
                    logger.error(error_msg)
                    raise HTTPException(status_code=502, detail=f"VST upload failed: {error_msg}")

                # Parse VST response
                vst_result = vst_response.json()
                logger.info(f"VST upload successful - Streamed {content_length_int} bytes")
                logger.debug(f"VST response body: {vst_result}")

                # Extract streamId and sensorId from VST response
                vst_sensor_id = vst_result.get("sensorId")
                if not vst_sensor_id:
                    error_msg = f"VST response missing 'sensorId' field: {vst_result}"
                    logger.error(error_msg)
                    raise HTTPException(status_code=502, detail=f"VST response invalid: {error_msg}")

                logger.info(f"VST sensor ID: {vst_sensor_id}")

                # Extract filename from VST response
                vst_filename = vst_result.get("filename", filename)
                logger.info(f"VST filename: {vst_filename}")

                # Run post-upload processing (timeline, storage URL, RTVI-CV, embeddings)
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
        summary="Complete a chunked video upload and trigger post-processing",
        description=(
            "Called after a chunked upload directly to VST is finished. "
            "Triggers timeline lookup, RTVI-CV registration, and embedding generation. "
            "This bypasses the PUT upload endpoint to avoid Cloudflare's 100s timeout "
            "for large files (the UI uploads chunks directly to VST, then calls this)."
        ),
        tags=["Video Ingest"],
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
        # the PUT upload path produces for the same filename (line ~322).
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


# This function will be called by custom FastAPI worker to register the router
def register_video_search_ingest_routes(app: "FastAPI", config: "Any") -> None:
    """
    Register videos-for-search routes to the FastAPI app.

    Reads configuration from ``general.front_end.streaming_ingest`` in the YAML.
    The caller (``CustomFastApiFrontEndWorker``) gates this on the
    ``enable_videos_for_search`` capability flag, so reaching this function
    with a missing ``streaming_ingest`` is a programming error.

    These routes are search-only: both ``vst_internal_url`` and
    ``rtvi_embed_base_url`` are required.

    Args:
        app: FastAPI application instance
        config: NAT Config object containing application configuration
    """
    try:
        streaming_config = getattr(config.general.front_end, "streaming_ingest", None)
        if streaming_config is None:
            raise ValueError(
                "streaming_ingest must be configured under general.front_end to register videos-for-search routes"
            )

        vst_internal_url = getattr(streaming_config, "vst_internal_url", "") or ""
        rtvi_embed_base_url = getattr(streaming_config, "rtvi_embed_base_url", "") or ""
        rtvi_cv_base_url = getattr(streaming_config, "rtvi_cv_base_url", "") or ""
        rtvi_embed_model = getattr(streaming_config, "rtvi_embed_model", "cosmos-embed1-448p")
        rtvi_embed_chunk_duration = getattr(streaming_config, "rtvi_embed_chunk_duration", 5)

        if not vst_internal_url:
            raise ValueError("streaming_ingest.vst_internal_url must be set for videos-for-search routes")

        if not rtvi_embed_base_url:
            raise ValueError(
                "streaming_ingest.rtvi_embed_base_url must be set for videos-for-search routes "
                "(this endpoint is search-only and requires the embedding service)"
            )

        router = create_video_search_ingest_router(
            vst_internal_url=vst_internal_url,
            rtvi_embed_base_url=rtvi_embed_base_url,
            rtvi_cv_base_url=rtvi_cv_base_url,
            rtvi_embed_model=rtvi_embed_model,
            rtvi_embed_chunk_duration=rtvi_embed_chunk_duration,
        )
        app.include_router(router)
        logger.info("Registered videos-for-search routes")
    except Exception as e:
        logger.error(f"Failed to register videos-for-search routes: {e}", exc_info=True)
        raise
