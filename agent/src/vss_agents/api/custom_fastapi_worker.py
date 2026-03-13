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

        # Add custom streaming routes if configured
        self._maybe_register_streaming_routes(app)

    def _maybe_register_streaming_routes(self, app: FastAPI) -> None:
        """Register streaming ingest routes (video upload and RTSP streams) only when configured."""
        front_end_cfg = getattr(getattr(self.config, "general", None), "front_end", None)
        streaming_config = getattr(front_end_cfg, "streaming_ingest", None) if front_end_cfg else None

        # Register video upload streaming routes
        try:
            from vss_agents.api.video_search_ingest import register_streaming_routes

            logger.info("Adding video upload streaming routes...")
            register_streaming_routes(app, self.config)
            logger.info("Successfully registered video upload streaming routes")
        except ImportError as exc:
            logger.debug("Video streaming routes module not available: %s", exc)
        except ValueError as exc:
            if streaming_config is not None:
                logger.error("Streaming ingest configured but invalid: %s", exc)
                raise
            logger.info("Skipping video streaming routes (not configured): %s", exc)
        except Exception as exc:
            logger.error("Failed to register video streaming routes: %s", exc, exc_info=True)
            raise

        # Register RTSP stream management routes
        try:
            from vss_agents.api.rtsp_stream_api import register_rtsp_stream_api_routes

            logger.info("Adding RTSP stream management routes...")
            register_rtsp_stream_api_routes(app, self.config)
            logger.info("Successfully registered RTSP stream management routes")
        except ImportError as exc:
            logger.debug("RTSP stream routes module not available: %s", exc)
        except ValueError as exc:
            if streaming_config is not None:
                logger.error("RTSP stream routes configured but invalid: %s", exc)
                raise
            logger.info("Skipping RTSP stream routes (not configured): %s", exc)
        except Exception as exc:
            logger.error("Failed to register RTSP stream routes: %s", exc, exc_info=True)
            raise

        # Register video delete routes
        try:
            from vss_agents.api.video_delete import register_video_delete_routes

            logger.info("Adding video delete routes...")
            register_video_delete_routes(app, self.config)
            logger.info("Successfully registered video delete routes")
        except ImportError as exc:
            logger.debug("Video delete routes module not available: %s", exc)
        except ValueError as exc:
            if streaming_config is not None:
                logger.error("Video delete routes configured but invalid: %s", exc)
                raise
            logger.info("Skipping video delete routes (not configured): %s", exc)
        except Exception as exc:
            logger.error("Failed to register video delete routes: %s", exc, exc_info=True)
            raise
