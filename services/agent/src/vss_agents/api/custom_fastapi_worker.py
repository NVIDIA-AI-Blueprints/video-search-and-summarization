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

from vss_agents.api.rtsp_stream_api import register_rtsp_stream_api_routes
from vss_agents.api.video_delete import register_video_delete_routes
from vss_agents.api.video_search_ingest import register_streaming_routes

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
        """Register custom routes based on the capability flags in ``streaming_ingest``.

        Each profile YAML declares which custom routes it wants via the
        ``enable_videos_for_search``, ``enable_rtsp_streams``, and
        ``enable_video_delete`` boolean flags on ``streaming_ingest``. Adding a
        new profile is a YAML-only change.

        Raises:
            ValueError: when ``streaming_ingest`` is missing from the config.
                Every profile is expected to declare it explicitly.
        """
        front_end_cfg = getattr(getattr(self.config, "general", None), "front_end", None)
        streaming_config = getattr(front_end_cfg, "streaming_ingest", None) if front_end_cfg else None

        if streaming_config is None:
            raise ValueError(
                "general.front_end.streaming_ingest must be set in the profile YAML "
                "to register custom streaming/RTSP/video-delete routes"
            )

        enable_videos_for_search = bool(getattr(streaming_config, "enable_videos_for_search", False))
        enable_rtsp_streams = bool(getattr(streaming_config, "enable_rtsp_streams", False))
        enable_video_delete = bool(getattr(streaming_config, "enable_video_delete", False))

        logger.info(
            "Registering streaming_ingest routes "
            f"(videos_for_search={enable_videos_for_search}, "
            f"rtsp_streams={enable_rtsp_streams}, "
            f"video_delete={enable_video_delete})"
        )

        if enable_videos_for_search:
            register_streaming_routes(app, self.config)

        if enable_rtsp_streams:
            register_rtsp_stream_api_routes(app, self.config)

        if enable_video_delete:
            register_video_delete_routes(app, self.config)
