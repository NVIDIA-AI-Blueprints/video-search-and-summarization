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
Delete video API endpoint.

Provides a DELETE endpoint for removing uploaded videos from the system.
Supports two modes:
  - "other" (non-search): Deletes from VST only (sensor + storage).
  - "search": Deletes from Elasticsearch indexes (embed, behavior, raw),
    RTVI-CV, and VST (reverse of add flow).
"""

import logging
import os
from typing import Any

from vss_agents.tools.vst.utils import VSTError
from vss_agents.tools.vst.utils import delete_vst_sensor
from vss_agents.tools.vst.utils import delete_vst_storage
from vss_agents.tools.vst.utils import get_sensor_id_from_stream_id

from elasticsearch import AsyncElasticsearch
from fastapi import APIRouter
from fastapi import FastAPI
import httpx
from pydantic import BaseModel
from pydantic import Field

logger = logging.getLogger(__name__)


# ============================================================================
# Response Models
# ============================================================================


class DeleteVideoResponse(BaseModel):
    """Response model for delete video operation."""

    status: str = Field(..., description="'success', 'partial', or 'failure'")
    message: str = Field(..., description="Human-readable status message")
    video_id: str = Field(..., description="The video/sensor ID that was deleted")


# ============================================================================
# RTVI-CV Cleanup Helper
# ============================================================================


async def _remove_from_rtvi_cv(
    client: httpx.AsyncClient, rtvi_cv_url: str, sensor_id: str, sensor_name: str
) -> tuple[bool, str]:
    """
    Remove a video stream from RTVI-CV.

    Args:
        client: HTTP client
        rtvi_cv_url: Base RTVI-CV URL (e.g., http://localhost:9000)
        sensor_id: The sensor UUID
        sensor_name: The sensor/video name

    Returns:
        (success, message) tuple
    """
    if not rtvi_cv_url:
        logger.info("RTVI-CV not configured, skipping")
        return True, "Skipped (not configured)"

    url = f"{rtvi_cv_url}/api/v1/stream/remove"
    payload = {
        "key": "sensor",
        "value": {
            "camera_id": sensor_id,
            "camera_name": sensor_name,
            "camera_url": "",
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
        logger.error(f"RTVI-CV remove failed: {e}", exc_info=True)
        return False, str(e)


# ============================================================================
# Elasticsearch Cleanup Helper
# ============================================================================


async def _delete_es_documents(es_endpoint: str, index_pattern: str, id_value: str, id_field: str) -> tuple[bool, str]:
    """
    Delete all Elasticsearch documents matching a field value.

    Uses the delete_by_query API to remove all documents where the specified
    field matches the given value.

    The field name and ID value vary by index (use .keyword for exact match):
      - mdx-embed-filtered:    field="sensor.id.keyword",  value=streamId (UUID)
      - mdx-behavior: field="sensor.id.keyword",  value=sensorName
      - mdx-raw:      field="sensorId.keyword",   value=sensorName

    Args:
        es_endpoint: Elasticsearch URL (e.g., http://localhost:9200)
        index_pattern: ES index name (e.g., "mdx-embed-filtered-2025-01-01")
        id_value: The value to match (either UUID or sensorName)
        id_field: The ES document field to match against (use .keyword for exact match)

    Returns:
        (success, message) tuple
    """
    es_client = AsyncElasticsearch(es_endpoint)
    try:
        result = await es_client.delete_by_query(
            index=index_pattern,
            body={
                "query": {
                    "term": {
                        id_field: id_value,
                    }
                }
            },
            refresh=True,
            conflicts="proceed",  # Don't fail on version conflicts
        )
        deleted = result.get("deleted", 0)
        logger.info(f"Deleted {deleted} docs from ES index '{index_pattern}' (field={id_field}, value={id_value})")
        return True, f"Deleted {deleted} documents"
    except Exception as e:
        logger.error(f"ES delete_by_query failed for index '{index_pattern}': {e}", exc_info=True)
        return False, str(e)
    finally:
        await es_client.close()


# ============================================================================
# Router Factory
# ============================================================================


def create_video_delete_router(
    vst_internal_url: str,
    elasticsearch_url: str = "",
    rtvi_cv_base_url: str = "",
    es_embed_index: str = "mdx-embed-filtered-2025-01-01",
    es_behavior_index: str = "mdx-behavior-2025-01-01",
    es_raw_index: str = "mdx-raw-2025-01-01",
    stream_mode: str = "search",
) -> APIRouter:
    """
    Create a FastAPI router for video deletion.

    Args:
        vst_internal_url: Internal VST URL for API calls
        elasticsearch_url: Elasticsearch endpoint URL (required for search mode)
        rtvi_cv_base_url: RTVI-CV service URL (for removing video from RTVI-CV in search mode)
        es_embed_index: ES index for video embeddings
        es_behavior_index: ES index for object behavior data
        es_raw_index: ES index for raw detection data
        stream_mode: "search" deletes from ES + RTVI-CV + VST; "other" deletes from VST only

    Returns:
        APIRouter with the delete video route
    """
    router = APIRouter()
    vst_url = vst_internal_url.rstrip("/")
    rtvi_cv_url = rtvi_cv_base_url.rstrip("/") if rtvi_cv_base_url else ""

    @router.delete(
        "/api/v1/videos/{video_id}",
        response_model=DeleteVideoResponse,
        response_model_exclude_none=True,
        summary="Delete an uploaded video",
        description=(
            "Deletes a video by its sensor/video ID (UUID). "
            "In 'search' mode, also removes from ES and RTVI-CV. "
            "In 'other' mode, only removes from VST."
        ),
        tags=["Video Management"],
    )
    async def delete_video(video_id: str) -> DeleteVideoResponse:
        """
        Delete a video from the system by sensor/video ID.

        This endpoint uses a best-effort approach: it continues even if
        individual steps fail, and reports the overall result as
        'success', 'partial', or 'failure'.

        Non-search mode ('other'):
          1. Delete sensor from VST
          2. Delete storage from VST

        Search mode (reverse of add flow):
          0. Look up sensorName from VST (before any deletions)
          1. Delete from ES embed index   (by sensor.id = video_id/UUID)
          2. Delete from ES behavior index (by sensor.id = sensorName)
          3. Delete from ES raw index      (by sensorId = sensorName)
          4. Remove from RTVI-CV
          5. Delete sensor from VST
          6. Delete storage from VST

        Args:
            video_id: The sensor/video UUID (e.g., from the upload response)

        Returns:
            DeleteVideoResponse with overall status
        """
        results: list[bool] = []
        is_search = stream_mode == "search"
        sensor_name = ""

        logger.info(f"Deleting video '{video_id}' (mode: {stream_mode})")

        async with httpx.AsyncClient(timeout=60.0) as client:
            # --- Step 0: Look up sensorName from VST (search mode only) ---
            # Must happen BEFORE any deletions, since we need sensorName for ES queries.
            if is_search:
                try:
                    sensor_name = await get_sensor_id_from_stream_id(video_id, vst_url)
                except VSTError as e:
                    logger.warning(
                        "Could not look up sensorName for '%s': %s. ES cleanup for behavior/raw may not work.",
                        video_id,
                        e,
                    )
                    sensor_name = ""

            # --- ES cleanup (search mode only, done first to avoid 'not found' issues) ---
            # Each index uses .keyword for exact match (avoids accidental match on similar names):
            #   - mdx-embed-filtered:    sensor.id.keyword  = video_id (UUID/streamId)
            #   - mdx-behavior: sensor.id.keyword  = sensorName
            #   - mdx-raw:      sensorId.keyword   = sensorName
            if is_search and elasticsearch_url:
                es_index_configs = [
                    (es_embed_index, "sensor.id.keyword", video_id),
                    (es_behavior_index, "sensor.id.keyword", sensor_name),
                    (es_raw_index, "sensorId.keyword", sensor_name),
                ]
                for index_name, field_name, id_value in es_index_configs:
                    if not id_value:
                        logger.warning(f"Skipping ES delete for '{index_name}': no identifier available")
                        continue
                    success, msg = await _delete_es_documents(elasticsearch_url, index_name, id_value, field_name)
                    results.append(success)
                    logger.info(f"Delete from ES '{index_name}': {'OK' if success else msg}")

            # --- Remove from RTVI-CV (search mode only) ---
            if is_search:
                success, msg = await _remove_from_rtvi_cv(client, rtvi_cv_url, video_id, sensor_name)
                results.append(success)
                logger.info(f"Remove from RTVI-CV: {'OK' if success else msg}")

            # --- Delete VST sensor (using shared vst utils) ---
            success, msg = await delete_vst_sensor(vst_url, video_id)
            results.append(success)
            logger.info("Delete VST sensor: %s", "OK" if success else msg)

            # --- Delete VST storage (using shared vst utils) ---
            success, msg = await delete_vst_storage(vst_url, video_id)
            results.append(success)
            logger.info("Delete VST storage: %s", "OK" if success else msg)

        # --- Determine overall status ---
        all_success = bool(results) and all(results)
        any_success = any(results)

        if all_success:
            status = "success"
            message = f"Video '{video_id}' deleted successfully"
        elif any_success:
            status = "partial"
            message = f"Video '{video_id}' partially deleted - some steps failed"
        else:
            status = "failure"
            message = f"Failed to delete video '{video_id}'"

        logger.info(f"Delete video '{video_id}' completed with status: {status}")

        return DeleteVideoResponse(
            status=status,
            message=message,
            video_id=video_id,
        )

    return router


# ============================================================================
# Registration Function
# ============================================================================


def register_video_delete_routes(app: "FastAPI", config: "Any") -> None:
    """
    Register video delete routes to the FastAPI app.

    Reads configuration from the YAML config (streaming_ingest section)
    with fallback to environment variables.

    Args:
        app: FastAPI application instance
        config: NAT Config object containing application configuration
    """
    try:
        # Look for streaming_ingest config under general.front_end
        streaming_config = getattr(config.general.front_end, "streaming_ingest", None)

        if streaming_config:
            # streaming_ingest found in config (NAT supports extra fields)
            vst_internal_url = getattr(streaming_config, "vst_internal_url", None) or os.getenv("VST_INTERNAL_URL")
            raw_elasticsearch_url = getattr(streaming_config, "elasticsearch_url", None)
            elasticsearch_url = (
                raw_elasticsearch_url
                if isinstance(raw_elasticsearch_url, str)
                else os.getenv("ELASTIC_SEARCH_ENDPOINT", "")
            )
            rtvi_cv_base_url = getattr(streaming_config, "rtvi_cv_base_url", None) or ""
            stream_mode = getattr(streaming_config, "stream_mode", None) or os.getenv("STREAM_MODE", "search")
            logger.info("Using streaming_ingest config from YAML for video delete routes")
        else:
            # Fallback to environment variables
            vst_internal_url = os.getenv("VST_INTERNAL_URL")
            elasticsearch_url = os.getenv("ELASTIC_SEARCH_ENDPOINT", "")
            host_ip = os.getenv("HOST_IP")
            rtvi_cv_port = os.getenv("RTVI_CV_PORT", "9000")
            rtvi_cv_base_url = f"http://{host_ip}:{rtvi_cv_port}" if host_ip else ""
            stream_mode = os.getenv("STREAM_MODE", "search")
            logger.info("Using environment variables for video delete routes")

        # Validate required fields
        if not vst_internal_url:
            raise ValueError("VST_INTERNAL_URL must be set for video delete routes")

        # Uploaded videos use a fixed timestamp (2025-01-01) so they always land
        # in these specific indexes.
        router = create_video_delete_router(
            vst_internal_url=vst_internal_url,
            elasticsearch_url=elasticsearch_url,
            rtvi_cv_base_url=rtvi_cv_base_url,
            es_embed_index="mdx-embed-filtered-2025-01-01",
            es_behavior_index="mdx-behavior-2025-01-01",
            es_raw_index="mdx-raw-2025-01-01",
            stream_mode=stream_mode or "search",
        )
        app.include_router(router)
        logger.info(f"Video delete routes registered successfully (mode: {stream_mode})")

    except Exception as e:
        logger.error(f"Failed to register video delete routes: {e}", exc_info=True)
        raise
