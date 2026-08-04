# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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
Unit tests for src/lvs_mcp.py

Tests LvsMCPServer initialization, tool listing, tool dispatching, HTTP API
delegation, error handling, and the run_mcp_server entry point.
"""

import asyncio
import json
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_lvs_server():
    """Mock ViaServer instance."""
    server = MagicMock()
    server._app = MagicMock()
    return server


@pytest.fixture
def mcp_server(mock_lvs_server):
    """LvsMCPServer with mocked dependencies."""
    from lvs_mcp import LvsMCPServer

    return LvsMCPServer(mock_lvs_server)


# ---------------------------------------------------------------------------
# LvsMCPServer.__init__
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestLvsMCPServerInit:
    def test_stores_server_reference(self, mcp_server, mock_lvs_server):
        assert mcp_server._lvs_server is mock_lvs_server

    def test_creates_mcp_server(self, mcp_server):
        assert mcp_server._server is not None

    def test_server_name(self, mcp_server):
        assert mcp_server._server.name == "lvs-engine"


# ---------------------------------------------------------------------------
# Tool listing
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestToolListing:
    def test_list_tools_returns_expected_tools(self, mcp_server):
        expected_tools = [
            "health_ready",
            "health_live",
            "list_models",
            "summarize_video",
            "generate_vlm_captions",
            "get_recommended_config",
            "get_metrics",
        ]
        mcp_server._list_models = AsyncMock(return_value={"object": "list", "data": []})
        mcp_server._summarize_video = AsyncMock(return_value={"id": "123"})
        mcp_server._generate_vlm_captions = AsyncMock(return_value={"id": "123"})
        mcp_server._get_recommended_config = AsyncMock(return_value={"chunk_size": 50})
        mcp_server._get_metrics = AsyncMock(
            return_value={"metrics": "# HELP ...", "format": "prometheus"}
        )

        for tool_name in expected_tools:

            def _run(name=tool_name):
                return asyncio.run(
                    mcp_server._handle_tool_call(
                        name,
                        {
                            "model": "m",
                            "scenario": "s",
                            "events": ["e"],
                            "id": "123",
                            "prompt": "p",
                            "video_length": 100,
                            "target_response_time": 10,
                        },
                    )
                )

            result = _run()
            assert isinstance(result, dict), f"Tool '{tool_name}' did not return a dict"

    def test_unknown_tool_raises_value_error(self, mcp_server):
        with pytest.raises(ValueError, match="Unknown tool"):
            asyncio.run(mcp_server._handle_tool_call("nonexistent_tool", {}))


# ---------------------------------------------------------------------------
# _handle_tool_call routing
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestHandleToolCallRouting:
    def test_health_ready(self, mcp_server):
        async def _run():
            return await mcp_server._handle_tool_call("health_ready", {})

        result = asyncio.run(_run())
        assert result["status"] == "ready"
        assert result["code"] == 200

    def test_health_live(self, mcp_server):
        async def _run():
            return await mcp_server._handle_tool_call("health_live", {})

        result = asyncio.run(_run())
        assert result["status"] == "alive"
        assert result["code"] == 200

    def test_list_models(self, mcp_server):
        mcp_server._list_models = AsyncMock(return_value={"object": "list", "data": []})

        async def _run():
            return await mcp_server._handle_tool_call("list_models", {})

        result = asyncio.run(_run())
        assert result["object"] == "list"
        mcp_server._list_models.assert_awaited_once()

    def test_summarize_video(self, mcp_server):
        args = {"id": "123", "model": "test-model"}
        mcp_server._summarize_video = AsyncMock(return_value={"id": "123"})

        async def _run():
            return await mcp_server._handle_tool_call("summarize_video", args)

        result = asyncio.run(_run())
        assert result["id"] == "123"
        mcp_server._summarize_video.assert_awaited_once_with(args)

    def test_generate_vlm_captions(self, mcp_server):
        args = {"id": "123", "prompt": "test", "model": "m"}
        mcp_server._generate_vlm_captions = AsyncMock(return_value={"id": "123"})

        async def _run():
            return await mcp_server._handle_tool_call("generate_vlm_captions", args)

        result = asyncio.run(_run())
        assert result["id"] == "123"

    def test_get_recommended_config(self, mcp_server):
        args = {"video_length": 600, "target_response_time": 60}
        mcp_server._get_recommended_config = AsyncMock(return_value={"chunk_size": 50})

        async def _run():
            return await mcp_server._handle_tool_call("get_recommended_config", args)

        result = asyncio.run(_run())
        assert result["chunk_size"] == 50

    def test_get_metrics(self, mcp_server):
        mcp_server._get_metrics = AsyncMock(
            return_value={"metrics": "# HELP ...", "format": "prometheus"}
        )

        async def _run():
            return await mcp_server._handle_tool_call("get_metrics", {})

        result = asyncio.run(_run())
        assert result["format"] == "prometheus"

    def test_unknown_tool_raises(self, mcp_server):
        async def _run():
            await mcp_server._handle_tool_call("nonexistent_tool", {})

        with pytest.raises(ValueError, match="Unknown tool"):
            asyncio.run(_run())


# ---------------------------------------------------------------------------
# _call_http_api
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCallHttpApi:
    def test_successful_json_response(self, mcp_server):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"data": "test"}

        with patch("httpx.AsyncClient") as MockClient:
            mock_client_instance = AsyncMock()
            mock_client_instance.request.return_value = mock_response
            mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
            mock_client_instance.__aexit__ = AsyncMock(return_value=None)
            MockClient.return_value = mock_client_instance

            async def _run():
                return await mcp_server._call_http_api("GET", "/models")

            result = asyncio.run(_run())
            assert result == {"data": "test"}

    def test_successful_text_response(self, mcp_server):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = "# HELP metric"

        with patch("httpx.AsyncClient") as MockClient:
            mock_client_instance = AsyncMock()
            mock_client_instance.request.return_value = mock_response
            mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
            mock_client_instance.__aexit__ = AsyncMock(return_value=None)
            MockClient.return_value = mock_client_instance

            async def _run():
                return await mcp_server._call_http_api("GET", "/metrics", return_text=True)

            result = asyncio.run(_run())
            assert result == {"text": "# HELP metric"}

    def test_204_no_content(self, mcp_server):
        mock_response = MagicMock()
        mock_response.status_code = 204

        with patch("httpx.AsyncClient") as MockClient:
            mock_client_instance = AsyncMock()
            mock_client_instance.request.return_value = mock_response
            mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
            mock_client_instance.__aexit__ = AsyncMock(return_value=None)
            MockClient.return_value = mock_client_instance

            async def _run():
                return await mcp_server._call_http_api("DELETE", "/files/123")

            result = asyncio.run(_run())
            assert result == {}

    def test_error_response_dict(self, mcp_server):
        mock_response = MagicMock()
        mock_response.status_code = 400
        mock_response.json.return_value = {"code": "BadParam", "message": "Invalid input"}

        with patch("httpx.AsyncClient") as MockClient:
            mock_client_instance = AsyncMock()
            mock_client_instance.request.return_value = mock_response
            mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
            mock_client_instance.__aexit__ = AsyncMock(return_value=None)
            MockClient.return_value = mock_client_instance

            async def _run():
                await mcp_server._call_http_api("POST", "/summarize", json={})

            with pytest.raises(ValueError, match="BadParam: Invalid input"):
                asyncio.run(_run())

    def test_error_response_non_dict(self, mcp_server):
        mock_response = MagicMock()
        mock_response.status_code = 422
        mock_response.json.return_value = "string error"

        with patch("httpx.AsyncClient") as MockClient:
            mock_client_instance = AsyncMock()
            mock_client_instance.request.return_value = mock_response
            mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
            mock_client_instance.__aexit__ = AsyncMock(return_value=None)
            MockClient.return_value = mock_client_instance

            async def _run():
                await mcp_server._call_http_api("POST", "/summarize", json={})

            with pytest.raises(ValueError, match="HTTP 422"):
                asyncio.run(_run())

    def test_error_response_json_parse_failure(self, mcp_server):
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.json.side_effect = Exception("parse fail")
        mock_response.text = "Internal Server Error"

        with patch("httpx.AsyncClient") as MockClient:
            mock_client_instance = AsyncMock()
            mock_client_instance.request.return_value = mock_response
            mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
            mock_client_instance.__aexit__ = AsyncMock(return_value=None)
            MockClient.return_value = mock_client_instance

            async def _run():
                await mcp_server._call_http_api("GET", "/bad")

            with pytest.raises(ValueError, match="HTTP 500"):
                asyncio.run(_run())


# ---------------------------------------------------------------------------
# Delegation methods
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestDelegationMethods:
    def test_list_models_calls_api(self, mcp_server):
        mcp_server._call_http_api = AsyncMock(return_value={"object": "list"})

        async def _run():
            return await mcp_server._list_models()

        result = asyncio.run(_run())
        mcp_server._call_http_api.assert_awaited_once_with("GET", "/models")
        assert result["object"] == "list"

    def test_summarize_video_calls_api(self, mcp_server):
        mcp_server._call_http_api = AsyncMock(return_value={"id": "r1"})
        args = {"id": "123", "model": "m"}

        async def _run():
            return await mcp_server._summarize_video(args)

        result = asyncio.run(_run())
        mcp_server._call_http_api.assert_awaited_once_with("POST", "/summarize", json=args)
        assert result["id"] == "r1"

    def test_generate_vlm_captions_calls_api(self, mcp_server):
        mcp_server._call_http_api = AsyncMock(return_value={"id": "r2"})
        args = {"id": "123", "prompt": "test", "model": "m"}

        async def _run():
            return await mcp_server._generate_vlm_captions(args)

        asyncio.run(_run())
        mcp_server._call_http_api.assert_awaited_once_with(
            "POST", "/generate_vlm_captions", json=args
        )

    def test_get_recommended_config_calls_api(self, mcp_server):
        mcp_server._call_http_api = AsyncMock(return_value={"chunk_size": 60})
        args = {"video_length": 600, "target_response_time": 60}

        async def _run():
            return await mcp_server._get_recommended_config(args)

        result = asyncio.run(_run())
        mcp_server._call_http_api.assert_awaited_once_with("POST", "/recommended_config", json=args)
        assert result["chunk_size"] == 60

    def test_get_metrics_calls_api(self, mcp_server):
        mcp_server._call_http_api = AsyncMock(return_value={"text": "# metrics"})

        async def _run():
            return await mcp_server._get_metrics()

        result = asyncio.run(_run())
        mcp_server._call_http_api.assert_awaited_once_with("GET", "/metrics", return_text=True)
        assert result["metrics"] == "# metrics"
        assert result["format"] == "prometheus"


# ---------------------------------------------------------------------------
# Tool call error handling (call_tool wrapper)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCallToolErrorHandling:
    def test_tool_call_success_returns_dict(self, mcp_server):
        result = asyncio.run(mcp_server._handle_tool_call("health_ready", {}))
        assert isinstance(result, dict)
        assert result["status"] == "ready"
        assert result["code"] == 200

    def test_tool_call_unknown_tool_raises(self, mcp_server):
        with pytest.raises(ValueError, match="Unknown tool"):
            asyncio.run(mcp_server._handle_tool_call("nonexistent_tool", {}))


# ---------------------------------------------------------------------------
# LvsMCPServer.run() — SSE transport path
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestMCPServerRun:
    def test_run_with_port_uses_sse(self, mcp_server):
        mock_server_inst = MagicMock()
        mock_server_inst.serve = AsyncMock()

        with (
            patch("uvicorn.Config", return_value=MagicMock()) as mock_config,
            patch("uvicorn.Server", return_value=mock_server_inst),
        ):
            asyncio.run(mcp_server.run(port=8001))

            mock_config.assert_called_once()
            config_args = mock_config.call_args
            assert config_args.kwargs.get("port") == 8001 or config_args[1].get("port") == 8001
            mock_server_inst.serve.assert_awaited_once()

    def test_run_without_port_uses_stdio(self, mcp_server):
        with patch("lvs_mcp.stdio_server") as mock_stdio:
            mock_read = AsyncMock()
            mock_write = AsyncMock()
            mock_ctx = AsyncMock()
            mock_ctx.__aenter__ = AsyncMock(return_value=(mock_read, mock_write))
            mock_ctx.__aexit__ = AsyncMock(return_value=None)
            mock_stdio.return_value = mock_ctx

            mcp_server._server.run = AsyncMock()
            mcp_server._server.create_initialization_options = MagicMock(return_value={})

            asyncio.run(mcp_server.run(port=None))
            mcp_server._server.run.assert_awaited_once()


# ---------------------------------------------------------------------------
# run_mcp_server entry point
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRunMcpServer:
    def test_with_valid_port(self):
        with (
            patch.dict(os.environ, {"LVS_MCP_PORT": "9001"}, clear=False),
            patch("lvs_mcp.LvsMCPServer") as MockMCP,
        ):
            mock_instance = AsyncMock()
            MockMCP.return_value = mock_instance

            from lvs_mcp import run_mcp_server

            asyncio.run(run_mcp_server(MagicMock()))
            mock_instance.run.assert_awaited_once_with(port=9001)

    def test_with_invalid_port_falls_back_to_stdio(self):
        with (
            patch.dict(os.environ, {"LVS_MCP_PORT": "not-a-number"}, clear=False),
            patch("lvs_mcp.LvsMCPServer") as MockMCP,
        ):
            mock_instance = AsyncMock()
            MockMCP.return_value = mock_instance

            from lvs_mcp import run_mcp_server

            asyncio.run(run_mcp_server(MagicMock()))
            mock_instance.run.assert_awaited_once_with(port=None)

    def test_with_empty_port_uses_stdio(self):
        with (
            patch.dict(os.environ, {"LVS_MCP_PORT": ""}, clear=False),
            patch("lvs_mcp.LvsMCPServer") as MockMCP,
        ):
            mock_instance = AsyncMock()
            MockMCP.return_value = mock_instance

            from lvs_mcp import run_mcp_server

            asyncio.run(run_mcp_server(MagicMock()))
            mock_instance.run.assert_awaited_once_with(port=None)

    def test_with_no_env_var(self):
        env = os.environ.copy()
        env.pop("LVS_MCP_PORT", None)
        with (
            patch.dict(os.environ, env, clear=True),
            patch("lvs_mcp.LvsMCPServer") as MockMCP,
        ):
            mock_instance = AsyncMock()
            MockMCP.return_value = mock_instance

            from lvs_mcp import run_mcp_server

            asyncio.run(run_mcp_server(MagicMock()))
            mock_instance.run.assert_awaited_once_with(port=None)


# ---------------------------------------------------------------------------
# SSE app_router paths (inside run method)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSseAppRouter:
    def _run_with_sse(self, mcp_server):
        """Helper to run the MCP server with SSE and capture the app_router."""
        mock_server_inst = MagicMock()
        mock_server_inst.serve = AsyncMock()
        with (
            patch("uvicorn.Config") as mock_config,
            patch("uvicorn.Server", return_value=mock_server_inst),
        ):
            asyncio.run(mcp_server.run(port=8001))
            config_call = mock_config.call_args
            return config_call[0][0] if config_call[0] else config_call.kwargs.get("app")

    def test_sse_messages_post_route(self, mcp_server):
        app_router = self._run_with_sse(mcp_server)
        assert app_router is not None

    def test_404_for_unknown_path(self, mcp_server):
        app_router = self._run_with_sse(mcp_server)

        send = AsyncMock()
        scope = {"type": "http", "path": "/unknown", "method": "GET"}
        receive = AsyncMock()

        asyncio.run(app_router(scope, receive, send))

        assert send.call_count == 2
        first_call = send.call_args_list[0][0][0]
        assert first_call["status"] == 404

    def test_non_http_scope(self, mcp_server):
        app_router = self._run_with_sse(mcp_server)

        send = AsyncMock()
        scope = {"type": "websocket", "path": "/ws"}
        receive = AsyncMock()

        asyncio.run(app_router(scope, receive, send))
        send.assert_not_awaited()


# ---------------------------------------------------------------------------
# API_PREFIX from environment
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestAPIPrefix:
    def test_default_prefix_no_versioning(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("VSS_API_ENABLE_VERSIONING", None)
            import importlib

            import lvs_mcp

            importlib.reload(lvs_mcp)
            assert lvs_mcp.API_PREFIX == ""

    def test_prefix_with_versioning(self):
        with patch.dict(os.environ, {"VSS_API_ENABLE_VERSIONING": "true"}, clear=False):
            import importlib

            import lvs_mcp

            importlib.reload(lvs_mcp)
            assert lvs_mcp.API_PREFIX == "/v1"
            os.environ.pop("VSS_API_ENABLE_VERSIONING", None)
            importlib.reload(lvs_mcp)


# ---------------------------------------------------------------------------
# Delegation with API prefix
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestDelegationWithPrefix:
    def test_list_models_uses_prefix(self, mcp_server):
        mcp_server._call_http_api = AsyncMock(return_value={"object": "list"})
        from lvs_mcp import API_PREFIX

        asyncio.run(mcp_server._list_models())
        mcp_server._call_http_api.assert_awaited_once_with("GET", f"{API_PREFIX}/models")

    def test_summarize_uses_prefix(self, mcp_server):
        mcp_server._call_http_api = AsyncMock(return_value={})
        from lvs_mcp import API_PREFIX

        asyncio.run(mcp_server._summarize_video({"model": "m"}))
        mcp_server._call_http_api.assert_awaited_once_with(
            "POST", f"{API_PREFIX}/summarize", json={"model": "m"}
        )


# ---------------------------------------------------------------------------
# call_tool error-handling wrapper (registered via @server.call_tool())
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCallToolWrapperErrorHandling:
    """Test the try/except wrapper inside the registered call_tool handler.

    The handler is registered by _setup_handlers via @self._server.call_tool().
    We exercise it by patching _handle_tool_call to raise and then invoking
    the handler through the MCP server's call_tool_handler (if accessible) or
    by verifying the observable effect through _handle_tool_call.
    """

    def test_call_tool_error_wraps_to_text_content(self, mcp_server):
        """When _handle_tool_call raises, call_tool returns a TextContent with error info."""
        from mcp.types import TextContent

        mcp_server._handle_tool_call = AsyncMock(side_effect=ValueError("something broke"))

        result = asyncio.run(mcp_server._invoke_call_tool("health_ready", {}))
        assert isinstance(result, list)
        assert len(result) == 1
        assert isinstance(result[0], TextContent)
        payload = json.loads(result[0].text)
        assert "error" in payload
        assert payload["type"] == "ValueError"

    def test_call_tool_error_contains_exception_message(self, mcp_server):
        """The error TextContent includes the original exception message."""
        mcp_server._handle_tool_call = AsyncMock(side_effect=RuntimeError("gpu exploded"))

        result = asyncio.run(mcp_server._invoke_call_tool("health_live", {}))
        payload = json.loads(result[0].text)
        assert "gpu exploded" in payload["error"]


# ---------------------------------------------------------------------------
# _call_http_api – error dict with nested "detail" key (missing branch)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCallHttpApiDetailBranch:
    def test_error_dict_with_detail_key_extracts_code_and_message(self, mcp_server):
        """When error dict has 'detail' sub-dict instead of top-level code/message."""
        mock_response = MagicMock()
        mock_response.status_code = 400
        mock_response.json.return_value = {
            "detail": {"code": "DetailCode", "message": "detail message"}
        }

        with patch("httpx.AsyncClient") as MockClient:
            mock_client_instance = AsyncMock()
            mock_client_instance.request.return_value = mock_response
            mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
            mock_client_instance.__aexit__ = AsyncMock(return_value=None)
            MockClient.return_value = mock_client_instance

            async def _run():
                await mcp_server._call_http_api("POST", "/summarize", json={})

            with pytest.raises(ValueError, match="DetailCode"):
                asyncio.run(_run())

    def test_error_dict_missing_both_code_and_message_uses_defaults(self, mcp_server):
        """When error dict has neither 'code' nor 'message' at any level."""
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.json.return_value = {"unexpected_key": "value"}

        with patch("httpx.AsyncClient") as MockClient:
            mock_client_instance = AsyncMock()
            mock_client_instance.request.return_value = mock_response
            mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
            mock_client_instance.__aexit__ = AsyncMock(return_value=None)
            MockClient.return_value = mock_client_instance

            async def _run():
                await mcp_server._call_http_api("GET", "/bad")

            with pytest.raises(ValueError, match="Error"):
                asyncio.run(_run())


# ---------------------------------------------------------------------------
# SSE app_router – /messages POST and /sse GET paths
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSseAppRouterPaths:
    def _get_app_router(self, mcp_server):
        """Run MCP server with SSE and capture the inner app_router callable."""
        mock_server_inst = MagicMock()
        mock_server_inst.serve = AsyncMock()

        with (
            patch("uvicorn.Config") as mock_config,
            patch("uvicorn.Server", return_value=mock_server_inst),
        ):
            asyncio.run(mcp_server.run(port=8002))
            config_call = mock_config.call_args
            return config_call[0][0] if config_call[0] else config_call.kwargs.get("app")

    def test_messages_post_delegates_to_sse_handle_post_message(self, mcp_server):
        """/messages POST calls sse.handle_post_message."""
        mock_sse = MagicMock()
        mock_sse.handle_post_message = AsyncMock()
        mock_server_inst = MagicMock()
        mock_server_inst.serve = AsyncMock()

        with (
            patch("lvs_mcp.SseServerTransport", return_value=mock_sse),
            patch("uvicorn.Config") as mock_config,
            patch("uvicorn.Server", return_value=mock_server_inst),
        ):
            asyncio.run(mcp_server.run(port=8003))
            config_call = mock_config.call_args
            app_router = config_call[0][0] if config_call[0] else config_call.kwargs.get("app")

        scope = {"type": "http", "path": "/messages", "method": "POST"}
        receive = AsyncMock()
        send = AsyncMock()

        asyncio.run(app_router(scope, receive, send))
        mock_sse.handle_post_message.assert_awaited_once_with(scope, receive, send)

    def test_sse_get_calls_handle_sse(self, mcp_server):
        """/sse GET calls the handle_sse inner coroutine."""
        mock_sse = MagicMock()
        mock_sse.connect_sse = MagicMock()
        mock_sse_ctx = AsyncMock()
        mock_sse_ctx.__aenter__ = AsyncMock(return_value=(AsyncMock(), AsyncMock()))
        mock_sse_ctx.__aexit__ = AsyncMock(return_value=None)
        mock_sse.connect_sse.return_value = mock_sse_ctx
        mock_server_inst = MagicMock()
        mock_server_inst.serve = AsyncMock()

        mcp_server._server.run = AsyncMock()
        mcp_server._server.create_initialization_options = MagicMock(return_value={})

        with (
            patch("lvs_mcp.SseServerTransport", return_value=mock_sse),
            patch("uvicorn.Config") as mock_config,
            patch("uvicorn.Server", return_value=mock_server_inst),
        ):
            asyncio.run(mcp_server.run(port=8004))
            config_call = mock_config.call_args
            app_router = config_call[0][0] if config_call[0] else config_call.kwargs.get("app")

        scope = {
            "type": "http",
            "path": "/sse",
            "method": "GET",
            "headers": [],
            "query_string": b"",
        }
        receive = AsyncMock()
        send = AsyncMock()

        asyncio.run(app_router(scope, receive, send))
        # connect_sse was invoked as part of handle_sse
        mock_sse.connect_sse.assert_called_once()
