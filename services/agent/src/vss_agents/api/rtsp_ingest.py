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
RTSP stream ingestion: ``POST /api/v1/rtsp-streams/add``.

Adds a stream to VST and (when configured) registers it with RTVI-CV /
RTVI-Embed (search path) or RTVI-VLM (LVS path). On any failure, the
previously completed steps are rolled back in reverse.

Also defines the shared ``ServiceConfig`` and the VST / RTVI helper
functions used by both this module and ``rtsp_delete``. Keeping the helpers
here lets the ingest path use them directly for rollback while the delete
module just imports what it needs.
"""

import logging
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

logger = logging.getLogger(__name__)

# ============================================================================
# Configuration
# ============================================================================


class ServiceConfig:
    """Service URLs and settings - initialized once per router.

    Per-step runtime behavior is driven by URL presence: each integration
    self-skips when its URL is empty (see ``add_to_rtvi_cv``,
    ``add_to_rtvi_embed``, etc.). The only behavior that isn't reducible to
    URL presence is whether to also delete VST storage when an RTSP stream is
    removed, which is governed by ``delete_vst_storage_on_stream_remove``.
    """

    def __init__(
        self,
        vst_internal_url: str,
        rtvi_cv_base_url: str = "",
        rtvi_embed_base_url: str = "",
        rtvi_vlm_base_url: str = "",
        rtvi_embed_model: str = "cosmos-embed1-448p",
        rtvi_embed_chunk_duration: int = 5,
        delete_vst_storage_on_stream_remove: bool = True,
    ):
        self.vst_url = vst_internal_url.rstrip("/")
        self.rtvi_cv_url = rtvi_cv_base_url.rstrip("/") if rtvi_cv_base_url else ""
        self.rtvi_embed_url = rtvi_embed_base_url.rstrip("/") if rtvi_embed_base_url else ""
        self.rtvi_vlm_url = rtvi_vlm_base_url.rstrip("/") if rtvi_vlm_base_url else ""
        self.rtvi_embed_model = rtvi_embed_model
        self.rtvi_embed_chunk_duration = rtvi_embed_chunk_duration
        self.delete_vst_storage_on_stream_remove = delete_vst_storage_on_stream_remove


def _resolve_service_config(config: Any) -> ServiceConfig:
    """Build a ``ServiceConfig`` from ``general.front_end.streaming_ingest``.

    Shared between ``register_rtsp_ingest_routes`` and
    ``register_rtsp_delete_routes`` so both paths read the same YAML keys.
    """
    streaming_config = getattr(config.general.front_end, "streaming_ingest", None)
    if streaming_config is None:
        raise ValueError("streaming_ingest must be configured under general.front_end to register RTSP routes")

    vst_internal_url = getattr(streaming_config, "vst_internal_url", "") or ""
    if not vst_internal_url:
        raise ValueError("streaming_ingest.vst_internal_url must be set for RTSP routes")

    return ServiceConfig(
        vst_internal_url=vst_internal_url,
        rtvi_cv_base_url=getattr(streaming_config, "rtvi_cv_base_url", "") or "",
        rtvi_embed_base_url=getattr(streaming_config, "rtvi_embed_base_url", "") or "",
        rtvi_vlm_base_url=getattr(streaming_config, "rtvi_vlm_base_url", "") or "",
        rtvi_embed_model=getattr(streaming_config, "rtvi_embed_model", "cosmos-embed1-448p"),
        rtvi_embed_chunk_duration=getattr(streaming_config, "rtvi_embed_chunk_duration", 5),
        delete_vst_storage_on_stream_remove=bool(
            getattr(streaming_config, "delete_vst_storage_on_stream_remove", True)
        ),
    )


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


# ============================================================================
# VST API Wrappers
# ============================================================================


async def add_to_vst(config: ServiceConfig, request: AddStreamRequest) -> tuple[bool, str, str | None, str | None]:
    """
    Add stream to VST and fetch the RTSP URL from streams API.
    Returns: (success, message, sensor_id, rtsp_url)
    """
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

    assert sensor_id is not None, "sensor_id should be set after successful VST add"

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
# RTVI API Functions (add)
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
        async for retry in create_retry_strategy(delay=2, retries=6, exceptions=(httpx.TransportError, RuntimeError)):
            with retry:
                response = await client.post(url, json=payload)
                if response.status_code not in (200, 201):
                    error = f"RTVI-embed returned {response.status_code}: {response.text}"
                    if response.status_code in (408, 429) or response.status_code >= 500:
                        raise RuntimeError(error)
                    logger.error(f"RTVI-embed add failed (non-retryable): {error}")
                    return False, error, None

                result = response.json()

                streams = result.get("streams", [])
                rtvi_stream_id = (streams[0].get("id") if streams else None) or sensor_id

                logger.info(f"RTVI-embed stream registered: {rtvi_stream_id}")
                return True, "Success", rtvi_stream_id
    except Exception as e:
        error = f"RTVI-embed request failed: {e!s}"
        logger.error(error, exc_info=True)
        return False, error, None

    raise AssertionError("RTVI-embed: tenacity produced no retry attempt")


async def add_to_rtvi_vlm(
    client: httpx.AsyncClient, config: ServiceConfig, sensor_id: str, name: str, sensor_url: str
) -> tuple[bool, str, str | None]:
    """
    Add stream to RTVI-VLM so LVS can use the VST sensor ID as a known resource.

    Returns: (success, message, rtvi_vlm_stream_id)
    """
    if not config.rtvi_vlm_url:
        logger.info("RTVI-VLM not configured, skipping")
        return True, "Skipped (not configured)", sensor_id

    url = f"{config.rtvi_vlm_url}/v1/streams/add"
    payload = {
        "streams": [
            {
                "liveStreamUrl": sensor_url,
                "description": name,
                # Pass sensor_id (not friendly name) so the protobuf streamId
                # stamped on raw_events == VST sensor_id. Logstash indexes
                # raw_events under `default_<streamId>`, the same key LVS
                # queries during summarization aggregation.
                "sensor_name": sensor_id,
                "id": sensor_id,
            }
        ],
    }

    logger.info(f"Adding stream to RTVI-VLM (name={name!r}, sensor_id={sensor_id}): POST {url}")
    logger.debug(f"Payload: {payload}")

    try:
        async for retry in create_retry_strategy(delay=2, retries=6, exceptions=(httpx.TransportError, RuntimeError)):
            with retry:
                response = await client.post(url, json=payload)
                if response.status_code not in (200, 201):
                    error = f"RTVI-VLM returned {response.status_code}: {response.text}"
                    if response.status_code in (408, 429) or response.status_code >= 500:
                        raise RuntimeError(error)
                    logger.error(f"RTVI-VLM add failed (non-retryable): {error}")
                    return False, error, None

                result = response.json() if response.content else {}
                errors = result.get("errors") if isinstance(result, dict) else None
                results = result.get("results", []) if isinstance(result, dict) else []
                if errors and not results:
                    return False, f"RTVI-VLM returned errors: {errors}", None

                rtvi_stream_id = (results[0].get("id") if results else None) or sensor_id
                logger.info(
                    "RTVI-VLM stream registered: rtvi_stream_id=%s (vst_sensor_id=%s)",
                    rtvi_stream_id,
                    sensor_id,
                )
                if rtvi_stream_id != sensor_id:
                    logger.warning(
                        "RTVI-VLM returned a different id than VST sensor_id; "
                        "downstream LVS lookups will use sensor_id=%s and may fail. "
                        "rtvi_stream_id=%s",
                        sensor_id,
                        rtvi_stream_id,
                    )
                return True, "OK", rtvi_stream_id
    except Exception as e:
        error = f"RTVI-VLM request failed: {e!s}"
        logger.error(error, exc_info=True)
        return False, error, None

    raise AssertionError("RTVI-VLM: tenacity produced no retry attempt")


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

            logger.info(f"Embedding generation started for stream {stream_id}")
            return True, "OK"

    except Exception as e:
        error = f"Embedding generation request failed: {e!s}"
        logger.error(error, exc_info=True)
        return False, error


# ============================================================================
# RTVI Cleanup Functions (used by ingest rollback + delete path)
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


async def cleanup_rtvi_vlm_stream(
    client: httpx.AsyncClient, config: ServiceConfig, stream_id: str | None
) -> tuple[bool, str]:
    """Remove stream from RTVI-VLM."""
    if not config.rtvi_vlm_url:
        return True, "Skipped (not configured)"

    url = f"{config.rtvi_vlm_url}/v1/streams/delete/{stream_id}"
    logger.info(f"Removing from RTVI-VLM: DELETE {url}")

    try:
        response = await client.delete(url)
        if response.status_code in (200, 204):
            logger.info(f"RTVI-VLM stream removed: {stream_id}")
            return True, "OK"
        return False, f"RTVI-VLM returned {response.status_code}: {response.text}"
    except Exception as e:
        return False, str(e)


# ============================================================================
# Router Factory
# ============================================================================


def create_rtsp_ingest_router(config: ServiceConfig) -> APIRouter:
    """Create the router that handles ``POST /api/v1/rtsp-streams/add``."""

    router = APIRouter()

    @router.post(
        "/api/v1/rtsp-streams/add",
        response_model=AddStreamResponse,
        response_model_exclude_none=True,
        summary="Add an RTSP stream",
        description=(
            "Adds stream to VST. RTVI-CV, RTVI-embed, and embedding generation steps "
            "self-skip when their respective URLs are not configured."
        ),
        tags=["RTSP Streams"],
    )
    async def add_stream(request: AddStreamRequest) -> AddStreamResponse:
        """
        Add an RTSP stream.

        1. Add to VST → get sensor_id
        2. Add to RTVI-CV (skipped when ``rtvi_cv_base_url`` is empty)
        3. Add to RTVI-embed (skipped when ``rtvi_embed_base_url`` is empty)
        4. Start embedding generation (skipped when ``rtvi_embed_base_url`` is empty)

        On failure at any step, previous steps are rolled back. Steps that
        self-skip never fail and never need rollback.
        """
        sensor_id = None
        rtvi_embed_stream_id = None
        rtvi_cv_added = False
        rtvi_embed_added = False

        logger.info(f"Adding stream '{request.name}'")

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

        # LVS mode is determined by whether an RTVI-VLM URL is configured: when
        # set, the stream is registered with RTVI-VLM only (LVS path); otherwise
        # the search path (RTVI-CV + RTVI-embed) is used.
        is_lvs_mode = bool(config.rtvi_vlm_url)

        async with httpx.AsyncClient(timeout=60.0) as client:
            if is_lvs_mode:
                with TimeMeasure("rtsp_stream: add to RTVI-VLM"):
                    success, msg, _rtvi_vlm_stream_id = await add_to_rtvi_vlm(
                        client, config, sensor_id, request.name, rtsp_url
                    )
                if not success:
                    await cleanup_vst_sensor(config, sensor_id)
                    await cleanup_vst_storage(config, sensor_id)
                    return AddStreamResponse(
                        status="failure",
                        message=f"Failed at RTVI-VLM: {msg}",
                        error=msg,
                    )

                return AddStreamResponse(
                    status="success",
                    message=f"Stream '{request.name}' added successfully",
                    error=None,
                )

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

    return router


# ============================================================================
# Registration Function
# ============================================================================


def register_rtsp_ingest_routes(app: FastAPI, config: Any) -> None:
    """Register ``POST /api/v1/rtsp-streams/add``.

    Reads configuration from ``general.front_end.streaming_ingest``. Only
    ``vst_internal_url`` is required; ``rtvi_*_base_url`` values are optional
    and unset URLs cause the corresponding RTVI step to self-skip at request
    time (each profile gets the same shape).
    """
    try:
        service_config = _resolve_service_config(config)
        app.include_router(create_rtsp_ingest_router(service_config))
        logger.info(
            "RTSP ingest route registered "
            f"(rtvi_embed={'on' if service_config.rtvi_embed_url else 'off'}, "
            f"rtvi_cv={'on' if service_config.rtvi_cv_url else 'off'}, "
            f"rtvi_vlm={'on' if service_config.rtvi_vlm_url else 'off'})"
        )
    except Exception as e:
        logger.error(f"Failed to register RTSP ingest route: {e}", exc_info=True)
        raise
