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
import re

from fastapi import FastAPI
from fastapi.routing import APIRoute
from nat.builder.workflow_builder import WorkflowBuilder
from nat.data_models.api_server import ChatResponseChunk
from nat.data_models.config import Config
from nat.front_ends.fastapi.fastapi_front_end_plugin_worker import FastApiFrontEndPluginWorker
from starlette.types import ASGIApp
from starlette.types import Message
from starlette.types import Receive
from starlette.types import Scope
from starlette.types import Send

from vss_agents.api.rtsp_delete import register_rtsp_delete_routes
from vss_agents.api.rtsp_ingest import register_rtsp_ingest_routes
from vss_agents.api.video_delete import register_video_delete_routes
from vss_agents.api.video_ingest import register_video_upload
from vss_agents.api.video_ingest import register_video_upload_complete
from vss_agents.api.video_search_ingest import register_video_search_ingest_routes

logger = logging.getLogger(__name__)

_DONE_EVENT = re.compile(rb"(?:^|\r?\n)data:[\t ]*\[DONE\]\r?\n\r?\n")
_ERROR_EVENT = re.compile(rb"(?:^|\r?\n)event:[\t ]*error[\t ]*\r?\n")
_WORKFLOW_ERROR = re.compile(rb'"code"\s*:\s*"workflow_error"')
_STREAM_EVENT_TAIL_BYTES = 256
_LEGACY_CHAT_STREAM_PATHS = frozenset({"/chat/stream", "/v1/chat/stream"})


class LegacyChatTerminalMiddleware:
    """Complete successful NAT interactive chat streams using the OpenAI SSE contract.

    NAT 1.8 routes interactive ``*/chat/stream`` requests through its interactive
    runner, which omits the final ``finish_reason=stop`` chunk and ``[DONE]``
    sentinel emitted by its non-interactive response helper. Repair cleanly
    ended streams, but preserve incomplete termination after either NAT error
    format so clients do not mistake a partial answer for success.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        response_successful = False
        stream_failed = False
        done_sent = False
        stream_event_tail = b""

        async def send_with_terminal(message: Message) -> None:
            nonlocal response_successful, stream_failed, done_sent, stream_event_tail

            if message["type"] == "http.response.start":
                response_successful = 200 <= message["status"] < 300
                await send(message)
                return

            if message["type"] != "http.response.body":
                await send(message)
                return

            body = message.get("body", b"")
            stream_event_window = stream_event_tail + body
            stream_failed = stream_failed or bool(
                _ERROR_EVENT.search(stream_event_window) or _WORKFLOW_ERROR.search(stream_event_window)
            )
            done_sent = done_sent or bool(_DONE_EVENT.search(stream_event_window))
            stream_event_tail = stream_event_window[-_STREAM_EVENT_TAIL_BYTES:]

            if not message.get("more_body", False) and response_successful and not stream_failed and not done_sent:
                if body:
                    await send({**message, "more_body": True})
                terminal = (
                    ChatResponseChunk.create_streaming_chunk("", finish_reason="stop").get_stream_data()
                    + "data: [DONE]\n\n"
                ).encode()
                await send({"type": "http.response.body", "body": terminal, "more_body": False})
                return

            await send(message)

        await self.app(scope, receive, send_with_terminal)


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

        self._register_legacy_chat_terminal_wrappers(app)

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

    @staticmethod
    def _register_legacy_chat_terminal_wrappers(app: FastAPI) -> None:
        """Wrap NAT's legacy chat routes without mutating FastAPI middleware state.

        NAT calls ``add_routes`` from its lifespan startup, after FastAPI has
        frozen the application middleware stack. Route-level ASGI wrappers are
        safe to install at that point and keep the repair scoped to the two
        affected POST endpoints.
        """
        for route in app.routes:
            if (
                isinstance(route, APIRoute)
                and route.path in _LEGACY_CHAT_STREAM_PATHS
                and route.methods is not None
                and "POST" in route.methods
            ):
                route.app = LegacyChatTerminalMiddleware(route.app)

    def _register_streaming_routes(self, app: FastAPI) -> None:
        """Register the custom video / RTSP / delete routes.

        Every route is registered unconditionally on every profile — each
        handler self-skips downstream calls (RTVI, storage delete, etc.)
        when its backing service isn't configured, so the same shape works
        on search/lvs/alerts/base:

        - ``POST /api/v1/videos`` — returns the VST upload URL for a new
          chat-tab video upload (UI handshake step 1).
        - ``POST /api/v1/videos/{sensor_id}/complete`` — universal upload
          completion hook (self-skips RTVI-CV / embedding when unset).
        - ``PUT /api/v1/videos-for-search/{filename}`` — *deprecated* compat
          shim for the ``metromind/ci-vss-oss`` search-profile test fixture.
          Registered with ``deprecated=True`` in OpenAPI; will be dropped
          once the fixture migrates to the new three-step flow.
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

        register_video_upload(app, self.config)
        register_video_upload_complete(app, self.config)
        register_video_search_ingest_routes(app, self.config)
        register_rtsp_ingest_routes(app, self.config)
        register_rtsp_delete_routes(app, self.config)
        register_video_delete_routes(app, self.config)
