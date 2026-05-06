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
RTSP stream deletion: ``DELETE /api/v1/rtsp-streams/delete/{name}``.

Best-effort teardown — each step logs success or failure but doesn't abort,
and the response status (``success`` / ``partial`` / ``failure``) reflects the
aggregated outcome. Reuses ``ServiceConfig``, ``get_stream_info_by_name``, and
the ``cleanup_*`` helpers from :mod:`vss_agents.api.rtsp_ingest` so both ends
of the lifecycle share the same VST / RTVI logic.
"""

import logging
from typing import Any

from fastapi import APIRouter
from fastapi import FastAPI
import httpx
from pydantic import BaseModel
from pydantic import Field

from vss_agents.api.rtsp_ingest import ServiceConfig
from vss_agents.api.rtsp_ingest import _resolve_service_config
from vss_agents.api.rtsp_ingest import cleanup_rtvi_cv
from vss_agents.api.rtsp_ingest import cleanup_rtvi_embed_generation
from vss_agents.api.rtsp_ingest import cleanup_rtvi_embed_stream
from vss_agents.api.rtsp_ingest import cleanup_vst_sensor
from vss_agents.api.rtsp_ingest import cleanup_vst_storage
from vss_agents.api.rtsp_ingest import get_stream_info_by_name

logger = logging.getLogger(__name__)


class DeleteStreamResponse(BaseModel):
    """Response model for delete stream operation."""

    status: str = Field(..., description="'success', 'partial', or 'failure'")
    message: str = Field(..., description="Human-readable status message")
    name: str = Field(..., description="The sensor name that was deleted")


def create_rtsp_delete_router(config: ServiceConfig) -> APIRouter:
    """Create the router that handles ``DELETE /api/v1/rtsp-streams/delete/{name}``."""

    router = APIRouter()

    @router.delete(
        "/api/v1/rtsp-streams/delete/{name}",
        response_model=DeleteStreamResponse,
        response_model_exclude_none=True,
        summary="Delete an RTSP stream by name",
        description=(
            "Removes the stream from VST. RTVI cleanup steps run when their URLs are "
            "configured. VST storage is also removed when "
            "``delete_vst_storage_on_stream_remove`` is True (default)."
        ),
        tags=["RTSP Streams"],
    )
    async def delete_stream(name: str) -> DeleteStreamResponse:
        """
        Delete an RTSP stream from services by camera/sensor name.

        Best-effort: continues even if individual steps fail.

        1. Find stream_id and RTSP URL from VST by name
        2. Stop embedding generation (skipped when ``rtvi_embed_base_url`` empty)
        3. Delete from RTVI-embed (skipped when ``rtvi_embed_base_url`` empty)
        4. Delete from RTVI-CV (skipped when ``rtvi_cv_base_url`` empty)
        5. Delete sensor from VST
        6. Delete storage from VST (only when ``delete_vst_storage_on_stream_remove`` True)
        """
        results: list[bool] = []

        logger.info(f"Deleting stream by name '{name}'")

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

        # RTVI cleanup runs only when at least one RTVI URL is configured.
        # The individual cleanup helpers self-skip when their URL is empty,
        # but we avoid opening an httpx client when nothing's configured.
        if config.rtvi_embed_url or config.rtvi_cv_url:
            async with httpx.AsyncClient(timeout=60.0) as client:
                success, msg = await cleanup_rtvi_embed_generation(client, config, stream_id)
                results.append(success)
                logger.info(f"Stop embedding generation: {'OK' if success else msg}")

                success, msg = await cleanup_rtvi_embed_stream(client, config, stream_id)
                results.append(success)
                logger.info(f"Delete from RTVI-embed: {'OK' if success else msg}")

                success, msg = await cleanup_rtvi_cv(client, config, stream_id, name=name, sensor_url=rtsp_url or "")
                results.append(success)
                logger.info(f"Delete from RTVI-CV: {'OK' if success else msg}")

        success, msg = await cleanup_vst_sensor(config, stream_id)
        results.append(success)
        logger.info(f"Delete VST sensor: {'OK' if success else msg}")

        if config.delete_vst_storage_on_stream_remove:
            success, msg = await cleanup_vst_storage(config, stream_id)
            results.append(success)
            logger.info(f"Delete VST storage: {'OK' if success else msg}")

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


def register_rtsp_delete_routes(app: FastAPI, config: Any) -> None:
    """Register ``DELETE /api/v1/rtsp-streams/delete/{name}``.

    Reads the same ``streaming_ingest`` config as ``register_rtsp_ingest_routes``;
    only ``vst_internal_url`` is required.
    """
    try:
        service_config = _resolve_service_config(config)
        app.include_router(create_rtsp_delete_router(service_config))
        logger.info(
            "RTSP delete route registered "
            f"(rtvi_embed={'on' if service_config.rtvi_embed_url else 'off'}, "
            f"rtvi_cv={'on' if service_config.rtvi_cv_url else 'off'}, "
            f"delete_vst_storage_on_stream_remove={service_config.delete_vst_storage_on_stream_remove})"
        )
    except Exception as e:
        logger.error(f"Failed to register RTSP delete route: {e}", exc_info=True)
        raise
