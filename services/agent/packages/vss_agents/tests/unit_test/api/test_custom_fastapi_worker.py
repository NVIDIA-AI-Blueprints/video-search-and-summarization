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
"""Unit tests for the route dispatcher in CustomFastApiFrontEndWorker.

The dispatcher registers six sets of routes on every profile, with no
per-profile capability flags:

  * ``register_video_upload``               — POST /api/v1/videos
  * ``register_video_upload_complete``      — POST /api/v1/videos/{sensor_id}/complete
  * ``register_video_search_ingest_routes`` — PUT  /api/v1/videos-for-search/{filename} (deprecated)
  * ``register_rtsp_ingest_routes``         — POST /api/v1/rtsp-streams/add
  * ``register_rtsp_delete_routes``         — DELETE /api/v1/rtsp-streams/delete/{name}
  * ``register_video_delete_routes``        — DELETE /api/v1/videos/{video_id}
"""

from typing import cast
from unittest.mock import MagicMock
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.routing import APIRoute
import pytest
from starlette.types import Message
from starlette.types import Receive
from starlette.types import Scope
from starlette.types import Send

from vss_agents.api.custom_fastapi_worker import CustomFastApiFrontEndWorker
from vss_agents.api.custom_fastapi_worker import LegacyChatTerminalMiddleware
from vss_agents.api.front_end_config import StreamingIngestConfig

_MISSING = object()


async def _run_terminal_middleware(chunks: list[bytes], status: int = 200) -> list[Message]:
    async def app(_scope: Scope, _receive: Receive, send: Send) -> None:
        await send({"type": "http.response.start", "status": status, "headers": []})
        for chunk in chunks:
            await send({"type": "http.response.body", "body": chunk, "more_body": True})
        await send({"type": "http.response.body", "body": b"", "more_body": False})

    messages: list[Message] = []

    async def receive() -> Message:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: Message) -> None:
        messages.append(message)

    scope = cast("Scope", {"type": "http", "method": "POST", "path": "/v1/chat/stream"})
    await LegacyChatTerminalMiddleware(app)(scope, receive, send)
    return messages


class TestLegacyChatTerminalMiddleware:
    def test_registers_route_level_wrappers_only_on_legacy_chat_post_routes(self) -> None:
        app = FastAPI()

        @app.post("/v1/chat/stream")
        async def chat_stream() -> None:
            return None

        @app.get("/v1/chat/stream")
        async def chat_stream_get() -> None:
            return None

        @app.post("/health")
        async def health() -> None:
            return None

        CustomFastApiFrontEndWorker._register_legacy_chat_terminal_wrappers(app)

        routes = [route for route in app.routes if isinstance(route, APIRoute)]
        chat_post = next(route for route in routes if route.path == "/v1/chat/stream" and "POST" in route.methods)
        chat_get = next(route for route in routes if route.path == "/v1/chat/stream" and "GET" in route.methods)
        health_post = next(route for route in routes if route.path == "/health" and "POST" in route.methods)
        assert isinstance(chat_post.app, LegacyChatTerminalMiddleware)
        assert not isinstance(chat_get.app, LegacyChatTerminalMiddleware)
        assert not isinstance(health_post.app, LegacyChatTerminalMiddleware)

    @pytest.mark.asyncio
    async def test_appends_openai_terminal_frames_after_confirmed_workflow_completion(self) -> None:
        messages = await _run_terminal_middleware(
            [b'intermediate_data: {"id":"workflow","parent_id": "root","name": "Function Complete: <workflow>"}\n']
        )

        bodies = [message.get("body", b"") for message in messages if message["type"] == "http.response.body"]
        assert b'"finish_reason":"stop"' in bodies[-1]
        assert bodies[-1].endswith(b"data: [DONE]\n\n")
        assert messages[-1]["more_body"] is False

    @pytest.mark.asyncio
    async def test_appends_openai_terminal_frames_after_clean_eof(self) -> None:
        messages = await _run_terminal_middleware([b'data: {"value":"partial"}\n\n'])

        bodies = [message.get("body", b"") for message in messages if message["type"] == "http.response.body"]
        assert bodies[-1].endswith(b"data: [DONE]\n\n")

    @pytest.mark.asyncio
    async def test_does_not_mark_interactive_workflow_error_as_success(self) -> None:
        messages = await _run_terminal_middleware(
            [
                b'intermediate_data: {"parent_id":"root","name":"Function Complete: <workflow>"}\n',
                b'event: error\ndata: {"code":"work',
                b'flow_error","message":"boom","details":"RuntimeError"}\n\n',
            ]
        )

        bodies = [message.get("body", b"") for message in messages if message["type"] == "http.response.body"]
        assert all(b'"finish_reason":"stop"' not in body for body in bodies)
        assert all(b"[DONE]" not in body for body in bodies)

    @pytest.mark.asyncio
    async def test_does_not_mark_noninteractive_workflow_error_as_success(self) -> None:
        messages = await _run_terminal_middleware(
            [b'{"code": "workflow_error", "message":"boom","details":"RuntimeError"}']
        )

        bodies = [message.get("body", b"") for message in messages if message["type"] == "http.response.body"]
        assert all(b'"finish_reason":"stop"' not in body for body in bodies)
        assert all(b"[DONE]" not in body for body in bodies)

    @pytest.mark.asyncio
    async def test_does_not_mark_unsuccessful_http_response_as_success(self) -> None:
        messages = await _run_terminal_middleware([b'{"detail":"failed"}'], status=500)

        bodies = [message.get("body", b"") for message in messages if message["type"] == "http.response.body"]
        assert all(b'"finish_reason":"stop"' not in body for body in bodies)
        assert all(b"[DONE]" not in body for body in bodies)

    @pytest.mark.asyncio
    async def test_answer_text_containing_done_sentinel_does_not_suppress_terminal_event(self) -> None:
        messages = await _run_terminal_middleware(
            [
                b'data: {"choices":[{"delta":{"content":"The terminal marker is data: [DONE]"}}]}\n\n',
                b'intermediate_data: {"parent_id":"root","name":"Function Complete: <workflow>"}\n',
            ]
        )

        bodies = [message.get("body", b"") for message in messages if message["type"] == "http.response.body"]
        assert bodies[-1].endswith(b"data: [DONE]\n\n")

    @pytest.mark.asyncio
    async def test_does_not_duplicate_existing_split_done_event(self) -> None:
        messages = await _run_terminal_middleware(
            [
                b'intermediate_data: {"parent_id":"root","name":"Function Complete: <workflow>"}\n',
                b"data: [DO",
                b"NE]\n",
                b"\n",
            ]
        )

        bodies = [message.get("body", b"") for message in messages if message["type"] == "http.response.body"]
        assert b"".join(bodies).count(b"data: [DONE]") == 1


def _make_worker(streaming_ingest):
    """Construct a worker bypassing the parent ``__init__`` so we can drive
    ``_register_streaming_routes`` directly without standing up a full NAT
    Config object. The dispatcher only reads ``self.config`` so a duck-typed
    MagicMock is sufficient.
    """
    worker = CustomFastApiFrontEndWorker.__new__(CustomFastApiFrontEndWorker)
    config = MagicMock()
    if streaming_ingest is _MISSING:
        config.general.front_end = MagicMock(spec=[])  # no streaming_ingest attr at all
    else:
        config.general.front_end.streaming_ingest = streaming_ingest
    worker._config = config
    return worker


@pytest.fixture
def patched_register_fns():
    """Patch every register fn the dispatcher delegates to.

    Returns a 6-tuple in the order:
        (video_upload, video_upload_complete, video_search_ingest,
         rtsp_ingest, rtsp_delete, video_delete)
    """
    with (
        patch("vss_agents.api.custom_fastapi_worker.register_video_upload") as video_upload,
        patch("vss_agents.api.custom_fastapi_worker.register_video_upload_complete") as video_upload_complete,
        patch("vss_agents.api.custom_fastapi_worker.register_video_search_ingest_routes") as video_search_ingest,
        patch("vss_agents.api.custom_fastapi_worker.register_rtsp_ingest_routes") as rtsp_ingest,
        patch("vss_agents.api.custom_fastapi_worker.register_rtsp_delete_routes") as rtsp_delete,
        patch("vss_agents.api.custom_fastapi_worker.register_video_delete_routes") as video_delete,
    ):
        yield (
            video_upload,
            video_upload_complete,
            video_search_ingest,
            rtsp_ingest,
            rtsp_delete,
            video_delete,
        )


class TestRegisterStreamingRoutesDispatcher:
    """``CustomFastApiFrontEndWorker._register_streaming_routes``."""

    def test_universal_routes_register_unconditionally(self, patched_register_fns):
        """All six register fns fire on every profile, with no per-profile
        flag. Each handler self-skips downstream calls when its backing
        service isn't configured."""
        (
            video_upload,
            video_upload_complete,
            video_search_ingest,
            rtsp_ingest,
            rtsp_delete,
            video_delete,
        ) = patched_register_fns
        worker = _make_worker(MagicMock())  # any non-None streaming_ingest

        worker._register_streaming_routes(MagicMock())

        video_upload.assert_called_once()
        video_upload_complete.assert_called_once()
        video_search_ingest.assert_called_once()
        rtsp_ingest.assert_called_once()
        rtsp_delete.assert_called_once()
        video_delete.assert_called_once()

    def test_missing_streaming_ingest_raises(self, patched_register_fns):
        """Every profile must declare streaming_ingest so a misconfigured
        profile can't silently boot with no custom routes."""
        (
            video_upload,
            video_upload_complete,
            video_search_ingest,
            rtsp_ingest,
            rtsp_delete,
            video_delete,
        ) = patched_register_fns
        worker = _make_worker(_MISSING)

        with pytest.raises(ValueError, match="streaming_ingest"):
            worker._register_streaming_routes(MagicMock())

        video_upload.assert_not_called()
        video_upload_complete.assert_not_called()
        video_search_ingest.assert_not_called()
        rtsp_ingest.assert_not_called()
        rtsp_delete.assert_not_called()
        video_delete.assert_not_called()

    def test_legacy_stream_mode_in_yaml_raises(self, patched_register_fns):
        """A profile YAML that still carries the legacy ``stream_mode`` knob
        on streaming_ingest must fail loudly at startup."""
        (
            video_upload,
            video_upload_complete,
            video_search_ingest,
            rtsp_ingest,
            rtsp_delete,
            video_delete,
        ) = patched_register_fns
        cfg = StreamingIngestConfig(stream_mode="search")
        worker = _make_worker(cfg)

        with pytest.raises(ValueError, match="stream_mode is no longer supported"):
            worker._register_streaming_routes(MagicMock())

        video_upload.assert_not_called()
        video_upload_complete.assert_not_called()
        video_search_ingest.assert_not_called()
        rtsp_ingest.assert_not_called()
        rtsp_delete.assert_not_called()
        video_delete.assert_not_called()
