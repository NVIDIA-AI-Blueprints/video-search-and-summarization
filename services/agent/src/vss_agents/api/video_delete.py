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
Per-step behavior is driven by what's configured:
  - VST sensor + storage cleanup: always runs.
  - RTVI-CV cleanup: runs when ``rtvi_cv_base_url`` is set.
  - Elasticsearch cleanup (embed, behavior, raw indexes): runs when an
    ``EsCleanupConfig`` is provided.
"""

import logging
from typing import Any

from elasticsearch import AsyncElasticsearch
from fastapi import APIRouter
from fastapi import FastAPI
import httpx
from pydantic import BaseModel
from pydantic import Field

from vss_agents.tools.vst.utils import VSTError
from vss_agents.tools.vst.utils import delete_vst_sensor
from vss_agents.tools.vst.utils import delete_vst_storage
from vss_agents.tools.vst.utils import get_sensor_id_from_stream_id
from vss_agents.utils.time_measure import TimeMeasure

logger = logging.getLogger(__name__)


# ============================================================================
# Response Models
# ============================================================================


class DeleteVideoResponse(BaseModel):
    """Response model for delete video operation."""

    status: str = Field(..., description="'success', 'partial', or 'failure'")
    message: str = Field(..., description="Human-readable status message")
    video_id: str = Field(..., description="The video/sensor ID that was deleted")


class EsCleanupConfig(BaseModel):
    """Elasticsearch configuration for video-delete cleanup.

    Bundles the ES URL with its index names so callers can't pass index
    names without a URL (or vice versa). Pass ``None`` to ``create_video_delete_router``
    when ES cleanup should be skipped entirely.
    """

    url: str = Field(..., description="Elasticsearch endpoint URL")
    embed_index: str = Field(
        default="mdx-embed-filtered-2025-01-01",
        description="ES index for video embeddings",
    )
    behavior_index: str = Field(
        default="mdx-behavior-2025-01-01",
        description="ES index for object behavior data",
    )
    raw_index: str = Field(
        default="mdx-raw-2025-01-01",
        description="ES index for raw detection data",
    )


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
    rtvi_cv_base_url: str = "",
    es_config: EsCleanupConfig | None = None,
) -> APIRouter:
    """
    Create a FastAPI router for video deletion.

    Per-step behavior is driven by what's configured: ES cleanup runs only
    when ``es_config`` is provided, RTVI-CV cleanup runs only when
    ``rtvi_cv_base_url`` is set. VST sensor + storage cleanup always runs.

    Args:
        vst_internal_url: Internal VST URL for API calls
        rtvi_cv_base_url: RTVI-CV service URL. Empty = skip RTVI-CV cleanup.
        es_config: Bundled ES URL + index names. ``None`` = skip ES cleanup.
            The URL and the index names live together so callers can't supply
            indexes without a URL.

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
            "ES cleanup runs when es_config is provided; "
            "RTVI-CV cleanup runs when rtvi_cv_base_url is configured. "
            "VST sensor + storage are always removed."
        ),
        tags=["Video Management"],
    )
    async def delete_video(video_id: str) -> DeleteVideoResponse:
        """
        Delete a video from the system by sensor/video ID.

        Best-effort: continues even if individual steps fail and reports the
        overall result as 'success', 'partial', or 'failure'.

        Steps:
          0. Look up sensorName from VST (only when ES cleanup will run; needed
             for behavior/raw index queries).
          1. ES embed index delete by sensor.id = video_id        (skipped if no es_config)
          2. ES behavior index delete by sensor.id = sensorName   (skipped if no es_config)
          3. ES raw index delete by sensorId = sensorName         (skipped if no es_config)
          4. RTVI-CV remove                                       (skipped if no RTVI-CV URL)
          5. VST sensor delete
          6. VST storage delete

        Args:
            video_id: The sensor/video UUID (e.g., from the upload response)

        Returns:
            DeleteVideoResponse with overall status
        """
        results: list[bool] = []
        sensor_name = ""

        logger.info(f"Deleting video '{video_id}'")

        async with httpx.AsyncClient(timeout=60.0) as client:
            # --- Step 0: Look up sensorName from VST (only when ES cleanup will run) ---
            # Must happen BEFORE any deletions, since we need sensorName for ES queries.
            if es_config is not None:
                try:
                    with TimeMeasure("video_delete: lookup sensor name from VST"):
                        sensor_name = await get_sensor_id_from_stream_id(video_id, vst_url)
                except VSTError as e:
                    logger.warning(
                        "Could not look up sensorName for '%s': %s. ES cleanup for behavior/raw may not work.",
                        video_id,
                        e,
                    )
                    sensor_name = ""

            # --- ES cleanup (done first to avoid 'not found' issues) ---
            # Each index uses .keyword for exact match (avoids accidental match on similar names):
            #   - mdx-embed-filtered:    sensor.id.keyword  = video_id (UUID/streamId)
            #   - mdx-behavior: sensor.id.keyword  = sensorName
            #   - mdx-raw:      sensorId.keyword   = sensorName
            if es_config is not None:
                es_index_configs = [
                    (es_config.embed_index, "sensor.id.keyword", video_id),
                    (es_config.behavior_index, "sensor.id.keyword", sensor_name),
                    (es_config.raw_index, "sensorId.keyword", sensor_name),
                ]
                for index_name, field_name, id_value in es_index_configs:
                    if not id_value:
                        logger.warning(f"Skipping ES delete for '{index_name}': no identifier available")
                        continue
                    with TimeMeasure(f"video_delete: ES delete from {index_name}"):
                        success, msg = await _delete_es_documents(es_config.url, index_name, id_value, field_name)
                    results.append(success)
                    logger.info(f"Delete from ES '{index_name}': {'OK' if success else msg}")

            # --- Remove from RTVI-CV ---
            if rtvi_cv_url:
                with TimeMeasure("video_delete: remove from RTVI-CV"):
                    success, msg = await _remove_from_rtvi_cv(client, rtvi_cv_url, video_id, sensor_name)
                results.append(success)
                logger.info(f"Remove from RTVI-CV: {'OK' if success else msg}")

            # --- Delete VST sensor (using shared vst utils) ---
            with TimeMeasure("video_delete: delete VST sensor"):
                success, msg = await delete_vst_sensor(vst_url, video_id)
            results.append(success)
            logger.info("Delete VST sensor: %s", "OK" if success else msg)

            # --- Delete VST storage (using shared vst utils) ---
            with TimeMeasure("video_delete: delete VST storage"):
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

    Reads configuration from ``general.front_end.streaming_ingest`` in the YAML.
    The caller (``CustomFastApiFrontEndWorker``) gates this on the
    ``enable_video_delete`` capability flag, so reaching this function with a
    missing ``streaming_ingest`` is a programming error.

    Args:
        app: FastAPI application instance
        config: NAT Config object containing application configuration

    Raises:
        ValueError: when ``streaming_ingest`` is missing or ``vst_internal_url``
            is empty. ``elasticsearch_url`` and ``rtvi_cv_base_url`` are
            optional — empty values cause the corresponding cleanup steps to
            self-skip at request time.
    """
    try:
        streaming_config = getattr(config.general.front_end, "streaming_ingest", None)
        if streaming_config is None:
            raise ValueError(
                "streaming_ingest must be configured under general.front_end to register video delete routes"
            )

        vst_internal_url = getattr(streaming_config, "vst_internal_url", "") or ""
        elasticsearch_url = getattr(streaming_config, "elasticsearch_url", "") or ""
        rtvi_cv_base_url = getattr(streaming_config, "rtvi_cv_base_url", "") or ""

        if not vst_internal_url:
            raise ValueError("streaming_ingest.vst_internal_url must be set for video delete routes")

        # Uploaded videos use a fixed timestamp (2025-01-01) so they always land
        # in these specific indexes. Only build the ES config when a URL is set;
        # otherwise pass None and ES cleanup self-skips at request time.
        es_config = EsCleanupConfig(url=elasticsearch_url) if elasticsearch_url else None

        router = create_video_delete_router(
            vst_internal_url=vst_internal_url,
            rtvi_cv_base_url=rtvi_cv_base_url,
            es_config=es_config,
        )
        app.include_router(router)
        logger.info(
            "Video delete routes registered "
            f"(es={'on' if es_config else 'off'}, "
            f"rtvi_cv={'on' if rtvi_cv_base_url else 'off'})"
        )

    except Exception as e:
        logger.error(f"Failed to register video delete routes: {e}", exc_info=True)
        raise
