# SPDX-FileCopyrightText: Copyright (c) 2024-2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

"""Implements the LVS MCP Server.

Exposes the same functionality as the REST API through MCP tools."""

import json
import os
import traceback
from typing import Any, Dict, List, Optional

from mcp.server import Server
from mcp.server.sse import SseServerTransport
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

from via_logger import logger

# Get API prefix from environment (same as via_server.py)
API_PREFIX = (
    "/v1" if os.environ.get("VSS_API_ENABLE_VERSIONING", "").lower() in ["true", "1"] else ""
)


class LvsMCPServer:
    """MCP Server that exposes LVS functionality as tools."""

    def __init__(self, lvs_server_instance):
        """Initialize MCP server with a reference to LvsServer instance.

        Args:
            lvs_server_instance: Instance of ViaServer class containing the backend logic
        """
        self._lvs_server = lvs_server_instance
        self._server = Server("lvs-engine")
        self._setup_handlers()

    def _setup_handlers(self):
        """Set up MCP tool handlers."""

        @self._server.list_tools()
        async def list_tools() -> List[Tool]:
            """Return list of available tools."""
            return [
                # Health Check
                Tool(
                    name="health_ready",
                    description="Check if LVS server is ready to accept requests",
                    inputSchema={
                        "type": "object",
                        "properties": {},
                    },
                ),
                Tool(
                    name="health_live",
                    description="Check if LVS server is alive",
                    inputSchema={
                        "type": "object",
                        "properties": {},
                    },
                ),
                # Models API
                Tool(
                    name="list_models",
                    description="List available VLM models",
                    inputSchema={
                        "type": "object",
                        "properties": {},
                    },
                ),
                # Summarization API
                Tool(
                    name="summarize_video",
                    description="Generate a summary of video content. Either 'id' (file UUID) or 'url' must be provided, but not both.",  # noqa: E501
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "id": {
                                "type": "string",
                                "description": "File or stream UUID to summarize. Either URL or ID must be provided, but not both.",  # noqa: E501
                            },
                            "url": {
                                "type": "string",
                                "description": "URL of the video to summarize. Either URL or ID must be provided, but not both.",  # noqa: E501
                            },
                            "prompt": {
                                "type": "string",
                                "description": "Prompt for VLM caption generation. Required when CA-RAG is enabled for the initial captioning phase, or for direct summarization when CA-RAG is disabled.",  # noqa: E501
                            },
                            "model": {
                                "type": "string",
                                "description": "Model to use for summarization",
                            },
                            "chunk_duration": {
                                "type": "integer",
                                "description": "Duration of each chunk in seconds",
                                "default": 60,
                            },
                            "chunk_overlap_duration": {
                                "type": "integer",
                                "description": "Overlap between chunks in seconds",
                                "default": 0,
                            },
                            "stream": {
                                "type": "boolean",
                                "description": "Enable streaming response",
                                "default": False,
                            },
                            "max_tokens": {
                                "type": "integer",
                                "description": "Maximum tokens in response",
                            },
                            "temperature": {
                                "type": "number",
                                "description": "Sampling temperature",
                            },
                            "schema": {
                                "type": "string",
                                "description": "JSON schema for structured \
                                output extraction from video content",  # noqa: E501
                            },
                            "batch_response_method": {
                                "type": "string",
                                "description": "Method for batch response processing",
                            },
                            "scenario": {
                                "type": "string",
                                "description": "Scenario or use case context for the summarization. \
                                    Examples: 'warehouse', 'public safety', \
                                    'police body camera monitoring'",
                            },
                            "events": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "List of events to detect or extract from the video",
                            },
                            "auto_generate_prompt": {
                                "type": "boolean",
                                "description": "Enable automatic prompt generation based on schema and events",  # noqa: E501
                            },
                            "time_metadata_keys": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "List of metadata keys containing time information",
                            },
                            "override_vlm_prompt": {
                                "type": "boolean",
                                "description": "Override the VLM prompt with the user supplied prompt. Please set this to True when you want to use a custom prompt for VLM caption generation and pass the prompt in the prompt field.",  # noqa: E501
                                "default": False,
                            },
                            "enable_vlm_structured_output": {
                                "type": "boolean",
                                "description": "Enable VLM structured output",
                                "default": True,
                            },
                            "objects_of_interest": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "List of objects of interest to detect or extract from the video.",  # noqa: E501
                            },
                        },
                        "required": ["model", "scenario", "events"],
                    },
                ),
                Tool(
                    name="generate_vlm_captions",
                    description="Generate VLM captions for video frames. Either 'id' (file UUID) or 'url' must be provided.",  # noqa: E501
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "id": {
                                "type": "string",
                                "description": "File or stream UUID. Either id or url must be provided.",
                            },
                            "url": {
                                "type": "string",
                                "description": "URL of the video. Either id or url must be provided.",
                            },
                            "prompt": {
                                "type": "string",
                                "description": "Prompt for caption generation",
                            },
                            "model": {
                                "type": "string",
                                "description": "Model to use",
                            },
                            "chunk_duration": {
                                "type": "integer",
                                "description": "Duration of each chunk in seconds",
                                "default": 60,
                            },
                        },
                        "required": ["prompt", "model"],
                    },
                ),
                # Stream Captioning API
                Tool(
                    name="generate_captions",
                    description=(
                        "Start VLM captioning on a stream. The stream must have "
                        "been previously added via RTVI stream/add. Returns "
                        "immediately once captioning is acknowledged."
                    ),
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "id": {
                                "type": "string",
                                "description": "Stream UUID (from RTVI stream/add)",
                            },
                            "model": {
                                "type": "string",
                                "description": "Model to use for caption generation",
                            },
                            "prompt": {
                                "type": "string",
                                "description": "VLM prompt for caption generation",
                            },
                            "chunk_duration": {
                                "type": "integer",
                                "description": "Chunk duration in seconds (0 = no chunking)",
                                "default": 0,
                            },
                            "scenario": {
                                "type": "string",
                                "description": "Scenario for auto-prompt generation",
                            },
                            "events": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "Events for auto-prompt generation",
                            },
                            "objects_of_interest": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "Objects of interest for auto-prompt generation",
                            },
                            "enable_vlm_structured_output": {
                                "type": "boolean",
                                "description": "Enable structured VLM output",
                                "default": True,
                            },
                            "override_vlm_prompt": {
                                "type": "boolean",
                                "description": "Use prompt as-is instead of auto-generating",
                                "default": False,
                            },
                            "max_tokens": {
                                "type": "integer",
                                "description": "Maximum tokens per chunk",
                            },
                            "temperature": {
                                "type": "number",
                                "description": "Sampling temperature",
                            },
                        },
                        "required": ["id", "model"],
                    },
                ),
                # Stream Summarize API
                Tool(
                    name="stream_summarize",
                    description=(
                        "Summarize a stream by aggregating existing captions from "
                        "the database. The stream must have been started with "
                        "generate_captions first."
                    ),
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "id": {
                                "type": "string",
                                "description": "Stream UUID to summarize",
                            },
                            "model": {
                                "type": "string",
                                "description": "Model identifier",
                            },
                            "start_time": {
                                "type": "number",
                                "description": "Time window start (seconds, 0 = no filter)",
                                "default": 0,
                            },
                            "end_time": {
                                "type": "number",
                                "description": "Time window end (seconds, 0 = no filter)",
                                "default": 0,
                            },
                            "summarize_max_tokens": {
                                "type": "integer",
                                "description": "Max tokens for LLM aggregation",
                            },
                            "summarize_temperature": {
                                "type": "number",
                                "description": "Temperature for LLM aggregation",
                            },
                        },
                        "required": ["id", "model"],
                    },
                ),
                # Recommended Config API
                Tool(
                    name="get_recommended_config",
                    description="Get recommended configuration for video processing",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "video_length": {
                                "type": "integer",
                                "description": "Video length in seconds",
                            },
                            "target_response_time": {
                                "type": "integer",
                                "description": "Target response time in seconds",
                            },
                            "usecase_event_duration": {
                                "type": "integer",
                                "description": "Expected event duration in seconds",
                            },
                        },
                        "required": ["video_length", "target_response_time"],
                    },
                ),
                # Metrics
                Tool(
                    name="get_metrics",
                    description="Get LVS server metrics in Prometheus format",
                    inputSchema={
                        "type": "object",
                        "properties": {},
                    },
                ),
            ]

        @self._server.call_tool()
        async def call_tool(name: str, arguments: Dict[str, Any]) -> List[TextContent]:
            """Handle tool calls by delegating to _invoke_call_tool."""
            return await self._invoke_call_tool(name, arguments)

    async def _invoke_call_tool(self, name: str, arguments: Dict[str, Any]) -> List[TextContent]:
        """Execute a tool by name with given arguments. Used by the registered handler and tests."""
        try:
            result = await self._handle_tool_call(name, arguments)
            return [TextContent(type="text", text=json.dumps(result, indent=2))]
        except Exception as e:
            error_msg = f"Error executing tool '{name}': {str(e)}\n{traceback.format_exc()}"
            logger.error(error_msg)
            return [
                TextContent(
                    type="text",
                    text=json.dumps({"error": str(e), "type": type(e).__name__}),
                )
            ]

    async def _handle_tool_call(self, name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """Route tool calls to appropriate LvsServer methods."""

        # Health Check
        if name == "health_ready":
            return {"status": "ready", "code": 200}

        elif name == "health_live":
            return {"status": "alive", "code": 200}

        # Models API
        elif name == "list_models":
            return await self._list_models()

        # Summarization API
        elif name == "summarize_video":
            return await self._summarize_video(arguments)

        elif name == "generate_vlm_captions":
            return await self._generate_vlm_captions(arguments)

        # Stream APIs
        elif name == "generate_captions":
            return await self._generate_captions(arguments)

        elif name == "stream_summarize":
            return await self._stream_summarize(arguments)

        # Recommended Config API
        elif name == "get_recommended_config":
            return await self._get_recommended_config(arguments)

        # Metrics
        elif name == "get_metrics":
            return await self._get_metrics()

        else:
            raise ValueError(f"Unknown tool: {name}")

    # Implementation methods that delegate to LvsServer

    async def _call_http_api(
        self, method: str, path: str, return_text: bool = False, **kwargs
    ) -> Dict[str, Any]:
        """Helper to call LvsServer's HTTP API internally.

        Args:
            method: HTTP method (GET, POST, DELETE, etc.)
            path: API path (e.g., "/files" or "/v1/files" depending on API_PREFIX)
            return_text: If True, return response text instead of JSON
            **kwargs: Additional arguments to pass to the HTTP client (json, params, data, files, etc.)

        Returns:
            Response JSON as dictionary or text string
        """
        from httpx import ASGITransport, AsyncClient

        # Use ASGITransport to call the FastAPI app directly
        transport = ASGITransport(app=self._lvs_server._app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.request(method, path, **kwargs)

            if response.status_code >= 400:
                try:
                    error_data = response.json()
                    logger.error(
                        f"Error response from API: status={response.status_code}, data={error_data}, type={type(error_data)}"  # noqa: E501
                    )

                    # Handle both dict and other response formats
                    if isinstance(error_data, dict):
                        code = error_data.get(
                            "code", error_data.get("detail", {}).get("code", "Error")
                        )
                        message = error_data.get(
                            "message",
                            error_data.get("detail", {}).get("message", str(error_data)),
                        )
                        error_msg = f"{code}: {message}"
                    else:
                        error_msg = f"HTTP {response.status_code}: {str(error_data)}"
                except Exception as e:
                    logger.error(
                        f"Failed to parse error response: status={response.status_code}, text={response.text[:500]}, exception={e}"  # noqa: E501
                    )
                    error_msg = f"HTTP {response.status_code}: {response.text}"
                raise ValueError(error_msg)

            # Handle different response types
            if response.status_code == 204:  # No content
                return {}

            if return_text:
                return {"text": response.text}

            return response.json()

    async def _list_models(self) -> Dict[str, Any]:
        """List available models by calling the HTTP API."""
        return await self._call_http_api("GET", f"{API_PREFIX}/models")

    async def _summarize_video(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """Summarize a video by calling the HTTP API."""
        return await self._call_http_api("POST", f"{API_PREFIX}/summarize", json=args)

    async def _generate_vlm_captions(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """Generate VLM captions by calling the HTTP API."""
        return await self._call_http_api("POST", f"{API_PREFIX}/generate_vlm_captions", json=args)

    async def _generate_captions(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """Start stream captioning by calling the HTTP API."""
        return await self._call_http_api("POST", "/v1/generate_captions", json=args)

    async def _stream_summarize(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """Summarize a stream by calling the HTTP API."""
        return await self._call_http_api("POST", "/v1/stream_summarize", json=args)

    async def _get_recommended_config(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """Get recommended config by calling the HTTP API."""
        return await self._call_http_api("POST", f"{API_PREFIX}/recommended_config", json=args)

    async def _get_metrics(self) -> Dict[str, Any]:
        """Get metrics by calling the HTTP API."""
        result = await self._call_http_api("GET", f"{API_PREFIX}/metrics", return_text=True)
        return {"metrics": result["text"], "format": "prometheus"}

    async def run(self, port: Optional[int] = None):
        """Run the MCP server on stdio or SSE (if port specified).

        Args:
            port: If specified, run MCP server on SSE transport with this port.
                  If None, run on stdio transport (default).
        """
        if port is not None:
            # Run with SSE transport on specified port
            logger.info(f"Starting LVS MCP server on SSE (http://0.0.0.0:{port}/sse)...")

            from starlette.requests import Request

            # Create SSE transport - this manages sessions internally
            sse = SseServerTransport("/messages")

            async def handle_sse(request: Request) -> None:
                """Handle SSE endpoint - establishes the event stream."""
                async with sse.connect_sse(
                    request.scope,
                    request.receive,
                    request._send,
                ) as streams:
                    # Run the MCP server protocol over these streams
                    await self._server.run(
                        streams[0],
                        streams[1],
                        self._server.create_initialization_options(),
                    )

            # Create a custom ASGI app that routes requests
            async def app_router(scope, receive, send):
                """Route requests to appropriate handlers."""
                if scope["type"] == "http":
                    path = scope["path"]
                    method = scope["method"]

                    # Handle /messages POST
                    if path == "/messages" and method == "POST":
                        await sse.handle_post_message(scope, receive, send)
                        return

                    # Handle /sse GET (for SSE connection)
                    if path == "/sse" and method == "GET":
                        # Create a Request object for the handler
                        from starlette.requests import Request

                        request = Request(scope, receive, send)
                        await handle_sse(request)
                        return

                    # 404 for other paths
                    await send(
                        {
                            "type": "http.response.start",
                            "status": 404,
                            "headers": [[b"content-type", b"text/plain"]],
                        }
                    )
                    await send(
                        {
                            "type": "http.response.body",
                            "body": b"Not Found",
                        }
                    )
                else:
                    # Handle other ASGI types if needed
                    pass

            import uvicorn

            config = uvicorn.Config(app_router, host="0.0.0.0", port=port, log_level="info")
            server = uvicorn.Server(config)
            await server.serve()
        else:
            # Run with stdio transport (default)
            logger.info("Starting LVS MCP server on stdio...")
            async with stdio_server() as (read_stream, write_stream):
                await self._server.run(
                    read_stream,
                    write_stream,
                    self._server.create_initialization_options(),
                )


async def run_mcp_server(lvs_server_instance):
    """Entry point to run MCP server with a LvsServer instance.

    Checks LVS_MCP_PORT environment variable:
    - If set to a port number: runs MCP server on SSE transport at that port
    - If not set or empty: runs MCP server on stdio transport (default)
    """
    mcp_port_str = os.environ.get("LVS_MCP_PORT", "").strip()
    mcp_port = None

    if mcp_port_str:
        try:
            mcp_port = int(mcp_port_str)
            logger.info(f"LVS_MCP_PORT={mcp_port} detected, will use SSE transport")
        except ValueError:
            logger.warning(f"Invalid LVS_MCP_PORT value '{mcp_port_str}', falling back to stdio")

    mcp_server = LvsMCPServer(lvs_server_instance)
    await mcp_server.run(port=mcp_port)
