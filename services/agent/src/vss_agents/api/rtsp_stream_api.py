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
RTSP Stream API Ingestion
"""

from enum import StrEnum
import logging
import os
from typing import Any

from fastapi import APIRouter
from fastapi import FastAPI
import httpx
from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field

from vss_agents.tools.vst.utils import add_sensor as vst_add_sensor
from vss_agents.tools.vst.utils import delete_sensor as vst_delete_sensor
from vss_agents.tools.vst.utils import delete_storage as vst_delete_storage
from vss_agents.tools.vst.utils import get_rtsp_url as vst_get_rtsp_url
from vss_agents.tools.vst.utils import get_stream_info_by_name as vst_get_stream_info_by_name
from vss_agents.utils.retry import create_retry_strategy
from vss_agents.utils.time_measure import TimeMeasure


class StreamMode(StrEnum):
    """Mode for stream processing."""

    SEARCH = "search"  # search profile: VST + RTVI-CV + RTVI-embed + embedding generation
    OTHER = "other"  # rest other profiles: VST only


logger = logging.getLogger(__name__)

# ============================================================================
# Configuration
# ============================================================================


class ServiceConfig:
    """Service URLs and settings - initialized once per router."""

    def __init__(
        self,
        vst_internal_url: str,
        rtvi_cv_base_url: str = "",
        rtvi_embed_base_url: str = "",
        rtvi_embed_model: str = "cosmos-embed1-448p",
        rtvi_embed_chunk_duration: int = 5,
        default_stream_mode: str = "search",
    ):
        self.vst_url = vst_internal_url.rstrip("/")
        self.rtvi_cv_url = rtvi_cv_base_url.rstrip("/") if rtvi_cv_base_url else ""
        self.rtvi_embed_url = rtvi_embed_base_url.rstrip("/") if rtvi_embed_base_url else ""
        self.rtvi_embed_model = rtvi_embed_model
        self.rtvi_embed_chunk_duration = rtvi_embed_chunk_duration
        self.default_stream_mode = StreamMode(default_stream_mode) if default_stream_mode else StreamMode.SEARCH


# ============================================================================
# Request/Response Models
# ============================================================================


class AddStreamRequest(BaseModel):
    """Request model for adding an RTSP stream (matches VST API)."""

    model_config = ConfigDict(populate_by_name=True)

    sensor_url: str = Field(..., alias="sensorUrl", description="RTSP URL of the stream")
    name: str = Field(..., description="Name for the sensor/stream")
    username: str = Field(default="", description="RTSP authentication username")
    password: str = Field(default="", description="RTSP authentication password")
    location: str = Field(default="", description="Location information")
    tags: str = Field(default="", description="Tags for the sensor")


class AddStreamResponse(BaseModel):
    """Response model for add stream operation."""

    status: str = Field(..., description="'success' or 'failure'")
    message: str = Field(..., description="Human-readable status message")
    error: str | None = Field(None, description="Error details if failed")


class DeleteStreamResponse(BaseModel):
    """Response model for delete stream operation."""

    status: str = Field(..., description="'success', 'partial', or 'failure'")
    message: str = Field(..., description="Human-readable status message")
    name: str = Field(..., description="The sensor name that was deleted")


# ============================================================================
# VST API Wrappers
# ============================================================================


async def add_to_vst(config: ServiceConfig, request: AddStreamRequest) -> tuple[bool, str, str | None, str | None]:
    """
    Add stream to VST and fetch the RTSP URL from streams API.
    Returns: (success, message, sensor_id, rtsp_url)
    """
    # Add sensor using shared util
    success, msg, sensor_id = await vst_add_sensor(
        sensor_url=request.sensor_url,
        name=request.name,
        username=request.username,
        password=request.password,
        location=request.location,
        tags=request.tags,
        vst_internal_url=config.vst_url,
    )
    if not success:
        return False, msg, None, None

    # After successful add, sensor_id is guaranteed to be set
    assert sensor_id is not None, "sensor_id should be set after successful VST add"

    # Fetch RTSP URL using shared util
    success, msg, rtsp_url = await vst_get_rtsp_url(sensor_id, config.vst_url)
    if not success:
        return False, msg, sensor_id, None

    return True, "OK", sensor_id, rtsp_url


async def cleanup_vst_sensor(config: ServiceConfig, sensor_id: str | None) -> tuple[bool, str]:
    """Delete sensor from VST using shared util."""
    return await vst_delete_sensor(sensor_id, config.vst_url)


async def cleanup_vst_storage(config: ServiceConfig, sensor_id: str | None) -> tuple[bool, str]:
    """Delete storage files from VST using shared util."""
    return await vst_delete_storage(sensor_id, config.vst_url)


async def get_stream_info_by_name(config: ServiceConfig, name: str) -> tuple[bool, str, str | None, str | None]:
    """
    Find stream_id and RTSP URL from VST by camera/sensor name using shared util.
    Returns: (success, message, stream_id, rtsp_url)
    """
    stream_id, rtsp_url = await vst_get_stream_info_by_name(name, config.vst_url)
    if stream_id is None:
        return False, f"Stream with name '{name}' not found in VST", None, None
    return True, "OK", stream_id, rtsp_url


# ============================================================================
# RTVI API Functions
# ============================================================================


async def add_to_rtvi_cv(
    client: httpx.AsyncClient, config: ServiceConfig, sensor_id: str, name: str, sensor_url: str
) -> tuple[bool, str]:
    """
    Add stream to RTVI-CV.
    Returns: (success, message)
    """
    if not config.rtvi_cv_url:
        logger.info("RTVI-CV not configured, skipping")
        return True, "Skipped (not configured)"

    url = f"{config.rtvi_cv_url}/api/v1/stream/add"
    payload = {
        "key": "sensor",
        "value": {
            "camera_id": sensor_id,
            "camera_name": name,
            "camera_url": sensor_url,
            "change": "camera_add",
            "metadata": {"resolution": "1920x1080", "codec": "h264", "framerate": 30},
        },
        "headers": {"source": "vst"},
    }

    logger.info(f"Adding stream to RTVI-CV: POST {url}")
    logger.debug(f"Payload: {payload}")

    try:
        response = await client.post(url, json=payload)
        if response.status_code not in (200, 201):
            error = f"RTVI-CV returned {response.status_code}: {response.text}"
            logger.error(error)
            return False, error

        logger.info(f"RTVI-CV stream registered: {sensor_id}")
        return True, "OK"

    except Exception as e:
        error = f"RTVI-CV request failed: {e!s}"
        logger.error(error, exc_info=True)
        return False, error


async def add_to_rtvi_embed(
    client: httpx.AsyncClient, config: ServiceConfig, sensor_id: str, name: str, sensor_url: str
) -> tuple[bool, str, str | None]:
    """
    Add stream to RTVI-embed with retries.

    During new stream ingestion the RTSP URL may exist but not yet be ready for
    consumption.  This function retries the POST to RTVI-embed so transient
    "stream not ready" failures are tolerated.

    Returns: (success, message, rtvi_stream_id)
    """
    if not config.rtvi_embed_url:
        logger.info("RTVI-embed not configured, skipping")
        return True, "Skipped (not configured)", sensor_id

    url = f"{config.rtvi_embed_url}/v1/streams/add"
    payload = {
        "streams": [
            {"liveStreamUrl": sensor_url, "description": "VST live stream", "sensor_name": name, "id": sensor_id}
        ]
    }

    logger.info(f"Adding stream to RTVI-embed: POST {url}")
    logger.debug(f"Payload: {payload}")

    try:
        async for retry in create_retry_strategy(delay=2, retries=6, exceptions=(Exception,)):
            with retry:
                response = await client.post(url, json=payload)
                if response.status_code not in (200, 201):
                    error = f"RTVI-embed returned {response.status_code}: {response.text}"
                    logger.warning(f"RTVI-embed add failed: {error}, retrying...")
                    raise RuntimeError(error)

                result = response.json()

                streams = result.get("streams", [])
                rtvi_stream_id = (streams[0].get("id") if streams else None) or sensor_id

                logger.info(f"RTVI-embed stream registered: {rtvi_stream_id}")
                return True, "Success", rtvi_stream_id
    except Exception as e:
        error = f"RTVI-embed request failed: {e!s}"
        logger.error(error, exc_info=True)
        return False, error, None

    return False, "RTVI-embed add failed after retries", None  # pragma: no cover


async def start_embedding_generation(
    client: httpx.AsyncClient, config: ServiceConfig, stream_id: str
) -> tuple[bool, str]:
    """
    Start embedding generation (fire-and-verify: confirm HTTP 200, then close).
    Returns: (success, message)
    """
    if not config.rtvi_embed_url:
        logger.info("RTVI-embed not configured, skipping embedding generation")
        return True, "Skipped (not configured)"

    url = f"{config.rtvi_embed_url}/v1/generate_video_embeddings"
    payload = {
        "id": stream_id,
        "model": config.rtvi_embed_model,
        "stream": True,
        "chunk_duration": config.rtvi_embed_chunk_duration,
    }

    logger.info(f"Starting embedding generation: POST {url}")
    logger.debug(f"Payload: {payload}")

    try:
        # Fire-and-verify: Open SSE connection, verify HTTP 200, then close
        async with client.stream(
            "POST",
            url,
            json=payload,
            headers={"Content-Type": "application/json", "Accept": "text/event-stream"},
        ) as response:
            if response.status_code != 200:
                error_body = await response.aread()
                error = f"RTVI-embed returned {response.status_code}: {error_body.decode()}"
                logger.error(error)
                return False, error

            # HTTP 200 received - embedding generation has started
            # RTVI-embed continues processing internally after we close
            logger.info(f"Embedding generation started for stream {stream_id}")
            return True, "OK"

    except Exception as e:
        error = f"Embedding generation request failed: {e!s}"
        logger.error(error, exc_info=True)
        return False, error


# ============================================================================
# RTVI Cleanup Functions
# ============================================================================


async def cleanup_rtvi_cv(
    client: httpx.AsyncClient, config: ServiceConfig, sensor_id: str, name: str = "", sensor_url: str = ""
) -> tuple[bool, str]:
    """Remove stream from RTVI-CV."""
    if not config.rtvi_cv_url:
        return True, "Skipped (not configured)"

    url = f"{config.rtvi_cv_url}/api/v1/stream/remove"
    payload = {
        "key": "sensor",
        "value": {
            "camera_id": sensor_id,
            "camera_name": name,
            "camera_url": sensor_url,
            "change": "camera_remove",
            "metadata": {"resolution": "1920x1080", "codec": "h264", "framerate": 30},
        },
        "headers": {"source": "vst"},
    }

    logger.info(f"Removing from RTVI-CV: POST {url}")

    try:
        response = await client.post(url, json=payload)
        if response.status_code in (200, 201, 204):
            logger.info(f"RTVI-CV stream removed: {sensor_id}")
            return True, "OK"
        return False, f"RTVI-CV returned {response.status_code}: {response.text}"
    except Exception as e:
        return False, str(e)


async def cleanup_rtvi_embed_stream(
    client: httpx.AsyncClient, config: ServiceConfig, stream_id: str | None
) -> tuple[bool, str]:
    """Remove stream from RTVI-embed."""
    if not config.rtvi_embed_url:
        return True, "Skipped (not configured)"

    url = f"{config.rtvi_embed_url}/v1/streams/delete/{stream_id}"
    logger.info(f"Removing from RTVI-embed: DELETE {url}")

    try:
        response = await client.delete(url)
        if response.status_code in (200, 204):
            logger.info(f"RTVI-embed stream removed: {stream_id}")
            return True, "OK"
        return False, f"RTVI-embed returned {response.status_code}: {response.text}"
    except Exception as e:
        return False, str(e)


async def cleanup_rtvi_embed_generation(
    client: httpx.AsyncClient, config: ServiceConfig, stream_id: str | None
) -> tuple[bool, str]:
    """Stop embedding generation in RTVI-embed."""
    if not config.rtvi_embed_url:
        return True, "Skipped (not configured)"

    url = f"{config.rtvi_embed_url}/v1/generate_video_embeddings/{stream_id}"
    logger.info(f"Stopping embedding generation: DELETE {url}")

    try:
        response = await client.delete(url)
        if response.status_code in (200, 204):
            logger.info(f"Embedding generation stopped: {stream_id}")
            return True, "OK"
        return False, f"RTVI-embed returned {response.status_code}: {response.text}"
    except Exception as e:
        return False, str(e)


# ============================================================================
# Router Factory
# ============================================================================


def create_rtsp_stream_api_router(
    vst_internal_url: str,
    rtvi_cv_base_url: str = "",
    rtvi_embed_base_url: str = "",
    rtvi_embed_model: str = "cosmos-embed1-448p",
    rtvi_embed_chunk_duration: int = 5,
    default_stream_mode: str = "search",
) -> APIRouter:
    """Create the RTSP stream API router with fire-and-forget implementation."""

    router = APIRouter()
    config = ServiceConfig(
        vst_internal_url=vst_internal_url,
        rtvi_cv_base_url=rtvi_cv_base_url,
        rtvi_embed_base_url=rtvi_embed_base_url,
        rtvi_embed_model=rtvi_embed_model,
        rtvi_embed_chunk_duration=rtvi_embed_chunk_duration,
        default_stream_mode=default_stream_mode,
    )

    @router.post(
        "/api/v1/rtsp-streams/add",
        response_model=AddStreamResponse,
        response_model_exclude_none=True,
        summary="Add an RTSP stream",
        description="Adds stream to VST. If mode='search', also adds to RTVI-CV, RTVI-embed and starts embedding generation.",
        tags=["RTSP Streams"],
    )
    async def add_stream(request: AddStreamRequest) -> AddStreamResponse:
        """
        Add an RTSP stream.

        Mode 'search' (default):
        1. Add to VST → get sensor_id
        2. Add to RTVI-CV
        3. Add to RTVI-embed
        4. Start embedding generation
        On failure at any step, previous steps are rolled back.

        Mode 'other':
        1. Add to VST only
        """
        sensor_id = None
        rtvi_embed_stream_id = None
        rtvi_cv_added = False
        rtvi_embed_added = False

        is_search_mode = config.default_stream_mode == StreamMode.SEARCH
        logger.info(f"Adding stream '{request.name}' in mode: {config.default_stream_mode.value}")

        # Step 1: Add to VST and get RTSP URL (uses shared utils)
        with TimeMeasure("rtsp_stream: add to VST"):
            success, msg, sensor_id, rtsp_url = await add_to_vst(config, request)

        if not success:
            return AddStreamResponse(
                status="failure",
                message=f"Failed at VST: {msg}",
                error=msg,
            )
        logger.info(f"Added RTSP to VST: {sensor_id} {rtsp_url} successfully")
        # After successful VST add, sensor_id and rtsp_url are guaranteed to be set
        assert sensor_id is not None, "sensor_id should be set after successful VST add"
        assert rtsp_url is not None, "rtsp_url should be set after successful VST add"

        # For 'other' mode, stop here - VST only
        if not is_search_mode:
            return AddStreamResponse(
                status="success",
                message=f"Stream '{request.name}' added successfully",
                error=None,
            )

        # For search mode, use httpx client for RTVI calls
        async with httpx.AsyncClient(timeout=60.0) as client:
            # Step 2: Add to RTVI-CV using RTSP URL from VST streams API
            with TimeMeasure("rtsp_stream: add to RTVI-CV"):
                success, msg = await add_to_rtvi_cv(client, config, sensor_id, request.name, rtsp_url)
            if not success:
                # Rollback: cleanup VST sensor and storage
                await cleanup_vst_sensor(config, sensor_id)
                await cleanup_vst_storage(config, sensor_id)
                return AddStreamResponse(
                    status="failure",
                    message=f"Failed at RTVI-CV: {msg}",
                    error=msg,
                )
            rtvi_cv_added = config.rtvi_cv_url != ""

            # Step 3: Add to RTVI-embed using RTSP URL from VST streams API
            with TimeMeasure("rtsp_stream: add to RTVI-embed"):
                success, msg, rtvi_embed_stream_id = await add_to_rtvi_embed(
                    client, config, sensor_id, request.name, rtsp_url
                )
            if not success:
                # Rollback: cleanup RTVI-CV and VST (sensor + storage)
                if rtvi_cv_added:
                    await cleanup_rtvi_cv(client, config, sensor_id, request.name, rtsp_url)
                await cleanup_vst_sensor(config, sensor_id)
                await cleanup_vst_storage(config, sensor_id)
                return AddStreamResponse(
                    status="failure",
                    message=f"Failed at RTVI-embed: {msg}",
                    error=msg,
                )
            rtvi_embed_added = config.rtvi_embed_url != ""

            # Step 4: Start embedding generation
            if rtvi_embed_stream_id is None:
                rtvi_embed_stream_id = sensor_id
            with TimeMeasure("rtsp_stream: start embedding generation"):
                success, msg = await start_embedding_generation(client, config, rtvi_embed_stream_id)
            if not success:
                # Rollback: cleanup RTVI-embed, RTVI-CV, and VST (sensor + storage)
                if rtvi_embed_added:
                    await cleanup_rtvi_embed_stream(client, config, rtvi_embed_stream_id)
                if rtvi_cv_added:
                    await cleanup_rtvi_cv(client, config, sensor_id, request.name, rtsp_url)
                await cleanup_vst_sensor(config, sensor_id)
                await cleanup_vst_storage(config, sensor_id)
                return AddStreamResponse(
                    status="failure",
                    message=f"Failed at embedding generation: {msg}",
                    error=msg,
                )

        # Success
        return AddStreamResponse(
            status="success",
            message=f"Stream '{request.name}' added successfully",
            error=None,
        )

    @router.delete(
        "/api/v1/rtsp-streams/delete/{name}",
        response_model=DeleteStreamResponse,
        response_model_exclude_none=True,
        summary="Delete an RTSP stream by name",
        description="Removes stream from services based on configured mode. 'search' mode deletes from VST, RTVI-CV, RTVI-embed. 'other' mode deletes from VST only.",
        tags=["RTSP Streams"],
    )
    async def delete_stream(name: str) -> DeleteStreamResponse:
        """
        Delete an RTSP stream from services by camera/sensor name.

        Mode 'search' (best-effort, continues even if individual steps fail):
        1. Find stream_id and RTSP URL from VST by name
        2. Stop embedding generation
        3. Delete from RTVI-embed
        4. Delete from RTVI-CV
        5. Delete sensor from VST
        (VST storage is not deleted in search mode.)

        Mode 'other':
        1. Find stream_id from VST by name
        2. Delete sensor from VST
        3. Delete storage from VST
        """
        results = []  # Track success/failure for overall status

        is_search_mode = config.default_stream_mode == StreamMode.SEARCH

        logger.info(f"Deleting stream by name '{name}' in mode: {config.default_stream_mode.value}")

        # First, find stream_id and RTSP URL from VST by name (uses shared utils)
        success, msg, stream_id, rtsp_url = await get_stream_info_by_name(config, name)
        if not success:
            logger.error(f"Failed to find stream '{name}': {msg}")
            return DeleteStreamResponse(
                status="failure",
                message=f"Failed to find stream with name '{name}': {msg}",
                name=name,
            )

        logger.info(f"Found stream_id '{stream_id}' for name '{name}'")
        if stream_id is None:
            return DeleteStreamResponse(
                status="failure",
                message=f"Found stream '{name}' but stream ID is missing",
                name=name,
            )

        # --- Search mode only: cleanup RTVI services ---
        if is_search_mode:
            async with httpx.AsyncClient(timeout=60.0) as client:
                # Step 1: Stop embedding generation
                success, msg = await cleanup_rtvi_embed_generation(client, config, stream_id)
                results.append(success)
                logger.info(f"Stop embedding generation: {'OK' if success else msg}")

                # Step 2: Delete from RTVI-embed
                success, msg = await cleanup_rtvi_embed_stream(client, config, stream_id)
                results.append(success)
                logger.info(f"Delete from RTVI-embed: {'OK' if success else msg}")

                # Step 3: Delete from RTVI-CV
                success, msg = await cleanup_rtvi_cv(client, config, stream_id, name=name, sensor_url=rtsp_url or "")
                results.append(success)
                logger.info(f"Delete from RTVI-CV: {'OK' if success else msg}")

        # Delete sensor from VST (uses shared utils)
        success, msg = await cleanup_vst_sensor(config, stream_id)
        results.append(success)
        logger.info(f"Delete VST sensor: {'OK' if success else msg}")

        # Delete storage from VST for other profiles only (uses shared utils)
        if not is_search_mode:
            success, msg = await cleanup_vst_storage(config, stream_id)
            results.append(success)
            logger.info(f"Delete VST storage: {'OK' if success else msg}")

        # Determine overall status
        all_success = all(results)
        any_success = any(results)

        if all_success:
            status = "success"
            message = f"Stream '{name}' deleted successfully"
        elif any_success:
            status = "partial"
            message = f"Stream '{name}' partially deleted - some services failed"
        else:
            status = "failure"
            message = f"Failed to delete stream '{name}'"

        logger.info(f"Delete stream '{name}' completed with status: {status}")

        return DeleteStreamResponse(
            status=status,
            message=message,
            name=name,
        )

    return router


# ============================================================================
# Registration Function
# ============================================================================


def register_rtsp_stream_api_routes(app: FastAPI, config: Any) -> None:
    """
    Register RTSP stream API routes to the FastAPI app.

    Args:
        app: FastAPI application instance
        config: NAT Config object containing application configuration
    """
    try:
        # Look for streaming_ingest config under general.front_end
        streaming_config = getattr(config.general.front_end, "streaming_ingest", None)

        if streaming_config:
            vst_internal_url = getattr(streaming_config, "vst_internal_url", None) or os.getenv("VST_INTERNAL_URL")
            rtvi_cv_base_url = getattr(streaming_config, "rtvi_cv_base_url", None) or ""
            rtvi_embed_base_url = getattr(streaming_config, "rtvi_embed_base_url", None) or ""
            rtvi_embed_model = getattr(streaming_config, "rtvi_embed_model", "cosmos-embed1-448p")
            rtvi_embed_chunk_duration = getattr(streaming_config, "rtvi_embed_chunk_duration", 5)
            default_stream_mode = str(
                getattr(streaming_config, "stream_mode", None) or os.getenv("STREAM_MODE", "search")
            )
            logger.info("Using streaming_ingest config from YAML for RTSP stream routes")
        else:
            # Fallback to environment variables
            host_ip = os.getenv("HOST_IP")
            vst_internal_url = os.getenv("VST_INTERNAL_URL")
            rtvi_cv_port = os.getenv("RTVI_CV_PORT", "9000")
            rtvi_embed_port = os.getenv("RTVI_EMBED_PORT", "8017")
            rtvi_cv_base_url = f"http://{host_ip}:{rtvi_cv_port}" if host_ip else ""
            rtvi_embed_base_url = f"http://{host_ip}:{rtvi_embed_port}" if host_ip else ""
            rtvi_embed_model = "cosmos-embed1-448p"
            rtvi_embed_chunk_duration = 5
            default_stream_mode = os.getenv("STREAM_MODE", "search")
            logger.info("Using environment variables for configuration")

        # Validate required fields
        if not vst_internal_url:
            raise ValueError("VST_INTERNAL_URL must be set")

        if not rtvi_embed_base_url:
            raise ValueError("RTVI-embed URL must be configured (HOST_IP + RTVI_EMBED_PORT or rtvi_embed_base_url)")

        # Create and register router
        router = create_rtsp_stream_api_router(
            vst_internal_url=vst_internal_url,
            rtvi_cv_base_url=rtvi_cv_base_url,
            rtvi_embed_base_url=rtvi_embed_base_url,
            rtvi_embed_model=rtvi_embed_model,
            rtvi_embed_chunk_duration=rtvi_embed_chunk_duration,
            default_stream_mode=default_stream_mode,
        )
        app.include_router(router)
        logger.info(f"RTSP stream API routes registered successfully (default mode: {default_stream_mode})")

    except Exception as e:
        logger.error(f"Failed to register RTSP stream API routes: {e}", exc_info=True)
        raise
