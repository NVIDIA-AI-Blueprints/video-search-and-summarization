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
Custom FastAPI front-end worker that extends NAT's default worker
to support additional streaming endpoints and a lightweight health check.
"""

import logging

from fastapi import FastAPI
from nat.builder.workflow_builder import WorkflowBuilder
from nat.data_models.config import Config
from nat.front_ends.fastapi.fastapi_front_end_plugin_worker import FastApiFrontEndPluginWorker

from vss_agents.api.rtsp_delete import register_rtsp_delete_routes
from vss_agents.api.rtsp_ingest import register_rtsp_ingest_routes
from vss_agents.api.video_delete import register_video_delete_routes
from vss_agents.api.video_ingest import register_video_upload_complete

logger = logging.getLogger(__name__)


class CustomFastApiFrontEndWorker(FastApiFrontEndPluginWorker):
    """
    Custom FastAPI front-end worker that extends NAT's default worker.
    """

    def __init__(self, config: Config):
        super().__init__(config)
        logger.info("Initialized CustomFastApiFrontEndWorker")

    async def add_routes(self, app: FastAPI, builder: WorkflowBuilder) -> None:
        """
        Override add_routes to add custom endpoints.

        Args:
            app: FastAPI application instance
            builder: WorkflowBuilder instance
        """
        # Add standard NAT routes
        await super().add_routes(app, builder)

        # Remove NAT's default health endpoint and add our custom one
        # We need to override it to return the expected format for integration tests
        app.routes[:] = [route for route in app.routes if getattr(route, "path", None) != "/health"]

        # Add lightweight health endpoint (no telemetry)
        @app.get("/health", include_in_schema=False)
        async def health_check() -> dict:
            return {"value": {"isAlive": True}}

        logger.info("Registered custom /health endpoint (replaced NAT default)")

        # Register custom streaming routes per capability flags in streaming_ingest
        self._register_streaming_routes(app)

    def _register_streaming_routes(self, app: FastAPI) -> None:
        """Register the custom video / RTSP / delete routes.

        Every route is registered unconditionally on every profile — each
        handler self-skips downstream calls (RTVI, storage delete, etc.)
        when its backing service isn't configured, so the same shape works
        on search/lvs/alerts/base:

        - ``POST /api/v1/videos/{filename}/complete`` — universal upload
          completion hook (self-skips RTVI-CV / embedding when unset).
        - ``POST /api/v1/rtsp-streams/add`` and ``DELETE /.../delete/{name}``.
        - ``DELETE /api/v1/videos/{video_id}``.

        Raises:
            ValueError: when ``streaming_ingest`` is missing from the config.
                Every profile is expected to declare it explicitly.
        """
        front_end_cfg = getattr(getattr(self.config, "general", None), "front_end", None)
        streaming_config = getattr(front_end_cfg, "streaming_ingest", None) if front_end_cfg else None

        if streaming_config is None:
            raise ValueError(
                "general.front_end.streaming_ingest must be set in the profile YAML "
                "to register custom video / RTSP routes"
            )

        # `stream_mode` and the old `enable_*` capability flags are no longer
        # supported. Routes register unconditionally now.
        legacy_extra = getattr(streaming_config, "model_extra", None)
        if isinstance(legacy_extra, dict) and "stream_mode" in legacy_extra:
            raise ValueError(
                "general.front_end.streaming_ingest.stream_mode is no longer supported. "
                "Drop it from the YAML; the upload-complete + RTSP + delete routes "
                "register unconditionally on every profile."
            )

        logger.info("Registering streaming_ingest routes")

        register_video_upload_complete(app, self.config)
        register_rtsp_ingest_routes(app, self.config)
        register_rtsp_delete_routes(app, self.config)
        register_video_delete_routes(app, self.config)
