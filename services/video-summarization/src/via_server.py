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

"""Implements the VIA REST API.

Translates between requests/responses and ViaStreamHandler methods."""

import argparse
import asyncio
import json
import os
import re
import sys
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Annotated, Optional
from uuid import UUID

import requests.exceptions
import uvicorn
from fastapi import FastAPI, File, Form, Path, Query, Request, Response, UploadFile
from fastapi.exceptions import HTTPException, RequestValidationError
from fastapi.responses import JSONResponse
from prometheus_client import (
    GC_COLLECTOR,
    PLATFORM_COLLECTOR,
    PROCESS_COLLECTOR,
    REGISTRY,
    generate_latest,
)
from sse_starlette.sse import EventSourceResponse

from chunk_info import RequestSource
from rtvi_vlm_client import RtviError
from via_exception import ViaException
from via_logger import LOG_PERF_LEVEL, TimeMeasure, logger, patch_logger_handlers
from via_stream_handler import RequestInfo
from via_utils import StreamSettingsCache, get_avg_time_per_chunk
from vss_api_models import (
    UUID_LENGTH,
    AddFileInfoResponse,
    ChatCompletionQuery,
    ChatCompletionResponseMessage,
    CompletionFinishReason,
    CompletionObject,
    CompletionResponse,
    CompletionResponseChoice,
    CompletionUsage,
    DeleteFileResponse,
    GenerateCaptionsRequest,
    GenerateCaptionsResponse,
    ListFilesResponse,
    ListModelsResponse,
    LvsError,
    MediaInfoOffset,
    MediaType,
    Purpose,
    RecommendedConfig,
    RecommendedConfigResponse,
    StreamSummarizeRequest,
    SummarizationQuery,
    VlmCaptionResponse,
    VlmCaptionsCompletionResponse,
    VlmQuery,
)

API_PREFIX = (
    "/v1" if os.environ.get("VSS_API_ENABLE_VERSIONING", "").lower() in ["true", "1"] else ""
)

BUILD_COMMIT_SHA_FILE = "/opt/nvidia/via/BUILD_COMMIT_SHA"


def get_version():
    """Read the version from VERSION file.

    Returns:
        str: The version string from VERSION file, or "unknown" if unavailable.
    """
    try:
        with open("/opt/nvidia/via/VERSION", "r") as f:
            return f.read().strip()
    except Exception:
        return "unknown"


def convert_seconds_to_string(seconds, need_hour=False, millisec=False):
    """Convert seconds to a formatted string."""
    if seconds is None:
        return "N/A"

    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)

    if need_hour or hours > 0:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    else:
        return f"{minutes:02d}:{secs:02d}"


def get_build_commit_sha():
    """Read the build commit SHA from file.

    Returns:
        str: The Git commit SHA of the build, or "unknown" if unavailable.
    """
    try:
        if os.path.exists(BUILD_COMMIT_SHA_FILE):
            with open(BUILD_COMMIT_SHA_FILE, "r") as f:
                return f.read().strip()
    except Exception as e:
        logger.warning(f"Failed to read build commit SHA from {BUILD_COMMIT_SHA_FILE}: {e}")
    return "unknown"


# Remove some default metrics reported by prometheus client.
REGISTRY.unregister(PROCESS_COLLECTOR)
REGISTRY.unregister(PLATFORM_COLLECTOR)
REGISTRY.unregister(GC_COLLECTOR)

COMMON_ERROR_RESPONSES = {
    400: {
        "model": LvsError,
        "description": (
            "Bad Request. The server could not understand the request due to invalid syntax."
        ),
    },
    401: {"model": LvsError, "description": "Unauthorized request."},
    422: {"model": LvsError, "description": "Failed to process request."},
    500: {"model": LvsError, "description": "Internal Server Error."},
    429: {
        "model": LvsError,
        "description": "Rate limiting exceeded.",
    },
}


def add_common_error_responses(errors=[]):
    return (
        {err: COMMON_ERROR_RESPONSES[err] for err in (errors + [401, 429, 422])}
        if errors
        else COMMON_ERROR_RESPONSES
    )


class ViaServer:
    def __init__(self, args) -> None:
        self._args = args

        self._async_executor = ThreadPoolExecutor(
            max_workers=args.max_live_streams, thread_name_prefix="vss-async-worker"
        )

        # Use FastAPI to implement the REST API
        self._app = FastAPI(
            contact={"name": "NVIDIA", "url": "https://nvidia.com"},
            description="Accelerated long video summarization and insight extraction service.",
            title="Long Video Summarization API",
            openapi_tags=[
                # {
                #     "name": "Files",
                #     "description": "Files are used to upload and manage media files.",
                # },
                {"name": "Health Check", "description": "Operations to check system health."},
                # {"name": "Live Stream", "description": "Operations related to live streams."},
                {"name": "Metrics", "description": "Operations to get metrics."},
                {
                    "name": "Models",
                    "description": "List and describe the various models available in the API.",
                },
                {
                    "name": "Recommended Config",
                    "description": "Operations related to querying recommended"
                    " LVS request parameters.",
                },
                {
                    "name": "Summarization",
                    "description": "Operations related to video summarization.",
                },
                {
                    "name": "Chat",
                    "description": "Graph-based QA over summarized video/stream content.",
                },
            ],
            servers=[
                {"url": "/", "description": "LVS microservice local endpoint.", "x-internal": False}
            ],
            version="v1",
        )
        self._app.config = {}
        self._app.config["host"] = args.host
        self._app.config["port"] = args.port

        self._setup_routes()

        if os.environ.get("VIA_DEV_API", "").lower() in ["true", "1"]:
            self._setup_dev_routes()

        self._setup_exception_handlers()
        self._setup_openapi_schema()

        if logger.level <= LOG_PERF_LEVEL:

            @self._app.middleware("http")
            async def measure_time(request: Request, call_next):
                with TimeMeasure(f"{request.method} {request.url.path}"):
                    response = await call_next(request)
                return response

        self._sse_active_clients = {}

        self._server = None

        self._stream_settings_cache = StreamSettingsCache(logger=logger)

    def run(self):
        from via_stream_handler import ViaStreamHandler

        # Initialize OpenTelemetry if enabled (optional)
        try:
            from otel_helper import init_otel

            init_otel(service_name="via-engine")
        except Exception as e:
            logger.debug(f"OTEL initialization failed: {e}")

        try:
            # Start the VIA stream handler
            self._stream_handler = ViaStreamHandler(self._args)
        except Exception as ex:
            logger.debug(f"Failed to load VIA stream handler: {traceback.format_exc()}")
            raise ViaException(f"Failed to load VIA stream handler - {str(ex)}")

        # Check if we should start MCP server
        enable_mcp = os.environ.get("LVS_ENABLE_MCP", "").lower() in ["true", "1"]

        # Patch uvicorn/root loggers so shutdown logging doesn't fail on closed streams
        for _name in (None, "uvicorn", "uvicorn.error", "uvicorn.access"):
            patch_logger_handlers(_name)

        if enable_mcp:
            # Run both HTTP and MCP servers concurrently
            logger.info("Starting both HTTP and MCP servers...")
            asyncio.run(self._run_both_servers())
        else:
            # Configure and start the uvicorn web server only
            config = uvicorn.Config(
                self._app,
                host=self._args.host,
                port=int(self._args.port),
                reload=False,
                log_config=None,
            )
            self._server = uvicorn.Server(config)
            self._server.run()
            self._server = None

        self._stream_handler.stop()
        self._async_executor.shutdown(wait=False)

    async def _run_both_servers(self):
        """Run both HTTP server and MCP server concurrently."""
        logger.info("Starting both HTTP and MCP servers...")

        # Import MCP server
        try:
            from lvs_mcp import run_mcp_server
        except ImportError as e:
            logger.error(f"Failed to import MCP server: {e}")
            logger.error("Install mcp package: pip install mcp")
            # Fall back to HTTP only
            config = uvicorn.Config(
                self._app,
                host=self._args.host,
                port=int(self._args.port),
                reload=False,
                log_config=None,
            )
            self._server = uvicorn.Server(config)
            await self._server.serve()
            return

        # Check if MCP port is configured
        mcp_port = os.environ.get("LVS_MCP_PORT", "").strip()

        # Require MCP to use SSE transport when running alongside HTTP server
        if not mcp_port:
            logger.error("LVS_MCP_PORT must be set when running both HTTP and MCP servers")
            logger.error("MCP cannot use stdio transport when HTTP server is also running")
            logger.error("Example: export LVS_MCP_PORT=8001")
            logger.error("Falling back to HTTP server only")
            # Fall back to HTTP only
            config = uvicorn.Config(
                self._app,
                host=self._args.host,
                port=int(self._args.port),
                reload=False,
                log_config=None,
            )
            self._server = uvicorn.Server(config)
            await self._server.serve()
            return

        # Create HTTP server task
        config = uvicorn.Config(
            self._app,
            host=self._args.host,
            port=int(self._args.port),
            reload=False,
            log_config=None,
        )
        self._server = uvicorn.Server(config)
        http_task = asyncio.create_task(self._server.serve())

        # Create MCP server task
        mcp_task = asyncio.create_task(run_mcp_server(self))

        logger.info(f"HTTP server running on http://{self._args.host}:{self._args.port}")
        logger.info(f"MCP server running on SSE at http://0.0.0.0:{mcp_port}/sse")

        # Wait for either server to complete (they should run indefinitely)
        try:
            await asyncio.gather(http_task, mcp_task)
        except KeyboardInterrupt:
            logger.info("Shutting down servers...")
        finally:
            self._server = None

    def _format_chunk_response(self, resp, req_info):
        """Format a chunk response with timestamp for display.

        Args:
            resp: Response object with start_timestamp, end_timestamp, and response fields
            req_info: Request info object with is_live field

        Returns:
            str: Formatted chunk response with timestamp
        """
        if req_info.is_live:
            start_time = resp.start_timestamp
            end_time = resp.end_timestamp
        else:
            start_time = str(resp.start_timestamp)
            end_time = str(resp.end_timestamp)

        return f"[{start_time} - {end_time}] {resp.response}"

    def _setup_dev_routes(self):

        # ======================= Files API (pure RTVI proxy) =======================
        # These routes proxy directly to RTVI-VLM's /v1/files API.
        # No local file storage or AssetManager involved.

        @self._app.post(
            f"{API_PREFIX}/files",
            summary="Upload a media file (proxied to RTVI-VLM)",
            description="Uploads a file to the RTVI-VLM backend.",
            responses={
                200: {"description": "Successful Response."},
                **add_common_error_responses(),
            },
            tags=["Files"],
        )
        async def add_video_file(
            purpose: Annotated[
                Purpose,
                Form(description="The intended purpose of the uploaded file. Must be 'vision'."),
            ],
            media_type: Annotated[MediaType, Form(description="Media type (image / video).")],
            file: Annotated[UploadFile, File(description="File object to be uploaded.")] = None,
            filename: Annotated[
                str,
                Form(
                    description="Local path to a file (container-accessible).",
                    max_length=8196,
                ),
            ] = "",
            creation_time: Annotated[
                Optional[str],
                Form(
                    description="Creation time in ISO8601 format (e.g. 2024-06-09T18:32:11.123Z).",
                ),
            ] = None,
            id: Annotated[
                Optional[UUID],
                Form(
                    description="Optional UUID for the file. If not provided, RTVI generates one."
                ),
            ] = None,
            sensor_name: Annotated[
                str,
                Form(description="User-defined sensor name.", max_length=256),
            ] = "",
        ) -> AddFileInfoResponse:
            logger.info(
                "Received add file request (RTVI proxy) - purpose=%s, media_type=%s, "
                "file=%r, filename=%s, id=%s, sensor_name=%s",
                purpose,
                media_type,
                file,
                filename,
                id,
                sensor_name,
            )

            if not file and not filename:
                raise ViaException(
                    "At least one of 'file' or 'filename' must be specified",
                    "InvalidParameters",
                    422,
                )
            if file and filename:
                raise ViaException(
                    "Only one of 'file' or 'filename' must be specified.",
                    "InvalidParameters",
                    422,
                )

            try:
                file_obj_or_path = file.file if file else filename
                rtvi_resp = self._stream_handler._vlm_pipeline.upload_file(
                    file_obj_or_path,
                    purpose=purpose,
                    media_type=media_type,
                    creation_time=creation_time,
                    file_id=id,
                    sensor_name=sensor_name,
                )
            except Exception as e:
                logger.error("RTVI-VLM file upload failed: %s", e)
                raise ViaException(
                    f"Failed to upload file to RTVI-VLM: {e}",
                    getattr(e, "code", "InternalServerError"),
                    getattr(e, "status_code", 500),
                )

            return rtvi_resp

        @self._app.delete(
            f"{API_PREFIX}/files/{{file_id}}",
            summary="Delete a file (proxied to RTVI-VLM)",
            description="Deletes a file from the RTVI-VLM backend.",
            responses={
                200: {"description": "Successful Response."},
                **add_common_error_responses(),
            },
            tags=["Files"],
        )
        async def delete_video_file(
            file_id: Annotated[UUID, Path(description="File ID to delete.")],
        ) -> DeleteFileResponse:
            file_id = str(file_id)
            logger.info("Received delete file request (RTVI proxy) for %s", file_id)
            try:
                self._stream_handler._vlm_pipeline.delete_file(file_id)
            except Exception as e:
                logger.error("RTVI-VLM delete failed for %s: %s", file_id, e)
                raise ViaException(
                    f"Failed to delete file from RTVI-VLM: {e}",
                    getattr(e, "code", "InternalServerError"),
                    getattr(e, "status_code", 500),
                )
            # Streaming Kafka path: also drop the Elasticsearch index that
            # Logstash populated for this asset. Idempotent on missing
            # index. No-op when KAFKA_ENABLED=false.
            try:
                drop_result = self._stream_handler.drop_collection_for_asset(file_id)
                logger.debug("drop_collection result for %s: %s", file_id, drop_result)
            except Exception as e:
                logger.warning("drop_collection_for_asset failed for %s: %s", file_id, e)
            return {"id": file_id, "object": "file", "deleted": True}

        @self._app.get(
            f"{API_PREFIX}/files",
            description="Returns a list of files from RTVI-VLM.",
            summary="List files (proxied to RTVI-VLM)",
            responses={
                200: {"description": "Successful Response."},
                **add_common_error_responses([500]),
            },
            tags=["Files"],
        )
        async def list_video_files(
            purpose: Annotated[
                str,
                Query(
                    description="Only return files with the given purpose.",
                    max_length=36,
                    pattern=r"^[a-zA-Z]*$",
                ),
            ],
        ) -> ListFilesResponse:
            if purpose != "vision":
                return {"data": [], "object": "list"}
            try:
                rtvi_resp = self._stream_handler._vlm_pipeline.list_files(purpose)
            except Exception as e:
                logger.error("RTVI-VLM list files failed: %s", e)
                raise ViaException(
                    f"Failed to list files from RTVI-VLM: {e}",
                    getattr(e, "code", "InternalServerError"),
                    getattr(e, "status_code", 500),
                )
            logger.info("List files (RTVI proxy): %d files", len(rtvi_resp.get("data", [])))
            return rtvi_resp

        # ======================= End Files API

        @self._app.post(
            f"{API_PREFIX}/generate_vlm_captions",
            summary="Generate VLM captions for a video",
            description="Run video VLM captions generation query.",
            responses={
                200: {"description": "Successful Response."},
                **add_common_error_responses(),
                503: {
                    "model": LvsError,
                    "description": (
                        "Server is busy processing another file / live-stream."
                        " Client may try again in some time."
                    ),
                },
            },
            tags=["Summarization"],
        )
        async def generate_vlm_captions(
            query: VlmQuery, request: Request
        ) -> VlmCaptionsCompletionResponse:

            # ---- Build RequestSource ----
            if not query.url:
                raise ViaException(
                    "url is required for file-based captions",
                    "InvalidParameters",
                    422,
                )
            client_id = str(query.id_list[0]) if query.id else None
            source = RequestSource.for_file(query.url, query.camera_id)
            if client_id:
                source.source_id = client_id

            videoId = source.source_id

            logger.info(
                "Received generate_vlm_captions query: source_id=%s, "
                "url=%s, chunk_duration=%d, stream=%r, enable_reasoning=%d",
                videoId,
                source.url or "",
                query.chunk_duration,
                query.stream,
                query.enable_reasoning,
            )

            # Save stream settings to json file
            filtered_query_json = self._stream_settings_cache.transform_query(query.get_query_json)
            logger.debug(f"Filtered Query JSON: {filtered_query_json}")
            self._stream_settings_cache.update_stream_settings(videoId, filtered_query_json)

            # Check if user has specified the model that is initialized
            model_info = self._stream_handler.get_models_info()
            if query.model != model_info.id:
                raise ViaException(f"No such model '{query.model}'", "BadParameters", 400)

            if query.api_type and query.api_type != model_info.api_type:
                raise ViaException(
                    f"api_type {query.api_type} not supported by model '{query.model}'",
                    "BadParameters",
                    400,
                )

            loop = asyncio.get_event_loop()

            # Convert VlmQuery to SummarizationQuery for internal processing
            query_dict = {
                "id": query.id,
                "url": query.url,
                "prompt": query.prompt,
                "model": query.model,
                "api_type": query.api_type,
                "response_format": query.response_format,
                "stream": query.stream,
                "chunk_duration": query.chunk_duration,
                "chunk_overlap_duration": query.chunk_overlap_duration,
                "vlm_input_width": query.vlm_input_width,
                "vlm_input_height": query.vlm_input_height,
                "enable_reasoning": query.enable_reasoning,
                # VLM captions specific defaults (no summarization)
                "summarize": False,
                "scenario": "",
                "events": [],
            }

            if query.system_prompt:
                query_dict["system_prompt"] = query.system_prompt
            if query.stream_options is not None:
                query_dict["stream_options"] = query.stream_options
            if query.max_tokens is not None:
                query_dict["max_tokens"] = query.max_tokens
            if query.min_tokens is not None:
                query_dict["min_tokens"] = query.min_tokens
            if query.ignore_eos is not None:
                query_dict["ignore_eos"] = query.ignore_eos
            if query.temperature is not None:
                query_dict["temperature"] = query.temperature
            if query.top_p is not None:
                query_dict["top_p"] = query.top_p
            if query.top_k is not None:
                query_dict["top_k"] = query.top_k
            if query.seed is not None:
                query_dict["seed"] = query.seed
            if query.media_info is not None:
                query_dict["media_info"] = query.media_info

            summarization_query = SummarizationQuery(**query_dict)

            # Dispatch to stream handler (file-based only)
            request_id = await loop.run_in_executor(
                self._async_executor,
                self._stream_handler.generate_vlm_captions,
                source,
                summarization_query,
                False,  # is_rtsp=False, always file
            )
            logger.info(
                "Created generate_vlm_captions query %s for %s",
                request_id,
                videoId,
            )

            logger.info("Waiting for results of query %s", request_id)

            if query.stream:
                # Allow only a single client for streaming output per live stream
                if time.time() - self._sse_active_clients.get(videoId, 0) < 3:
                    raise ViaException(
                        "Another client is already connected to live stream", "Conflict", 409
                    )

                # Server side events generator
                async def message_generator():
                    last_status_report_time = 0
                    last_status = None
                    while True:
                        self._sse_active_clients[videoId] = time.time()
                        try:
                            message = await asyncio.wait_for(request._receive(), timeout=0.01)
                            if message.get("type") == "http.disconnect":
                                self._sse_active_clients.pop(videoId, None)
                                logger.info(
                                    "Client %s disconnected for live-stream %s",
                                    request.client.host,
                                    videoId,
                                )
                                return
                        except Exception:
                            pass

                        # Get current response status from the pipeline
                        try:
                            req_info, resp_list = self._stream_handler.get_response(request_id, 1)
                        except ViaException:
                            break
                        if (
                            time.time() - last_status_report_time >= 10
                            or resp_list
                            or last_status != req_info.status
                        ):
                            last_status_report_time = time.time()
                            last_status = req_info.status
                            logger.info(
                                "Status for query %s is %s, percent complete is %.2f,"
                                " size of response list is %d",
                                req_info.request_id,
                                req_info.status.value,
                                req_info.progress,
                                len(resp_list),
                            )

                        # Response list is empty. Stop generation if request is completed or failed.
                        if not resp_list:
                            if req_info.status in [
                                RequestInfo.Status.SUCCESSFUL,
                                RequestInfo.Status.FAILED,
                            ]:
                                if req_info.status == RequestInfo.Status.FAILED:
                                    # Create the response json
                                    response = {
                                        "id": request_id,
                                        "model": model_info.id,
                                        "created": int(req_info.queue_time),
                                        "usage": None,
                                    }
                                    yield json.dumps(response)
                                break
                            await asyncio.sleep(1)
                            continue

                        # Set the start/end time info for current response.
                        while resp_list:
                            if req_info.is_live:
                                media_info = {
                                    "type": "timestamp",
                                    "start_timestamp": resp_list[0].start_timestamp,
                                    "end_timestamp": resp_list[0].end_timestamp,
                                }
                                dt = datetime.strptime(
                                    resp_list[0].end_timestamp, "%Y-%m-%dT%H:%M:%S.%fZ"
                                ).replace(tzinfo=timezone.utc)
                                current_time = datetime.now(timezone.utc)
                                self._stream_handler.update_live_stream_captions_latency(
                                    (current_time - dt).total_seconds()
                                )
                            else:
                                media_info = {
                                    "type": "offset",
                                    "start_offset": int(resp_list[0].start_timestamp),
                                    "end_offset": int(resp_list[0].end_timestamp),
                                }

                            # Build chunk responses for VLM captions
                            chunk_responses = []
                            for resp in resp_list:
                                chunk_response = {
                                    "start_time": (
                                        resp.start_timestamp
                                        if req_info.is_live
                                        else str(resp.start_timestamp)
                                    ),
                                    "end_time": (
                                        resp.end_timestamp
                                        if req_info.is_live
                                        else str(resp.end_timestamp)
                                    ),
                                    "content": resp.response,
                                }
                                # Add reasoning description if available
                                if (
                                    hasattr(resp, "reasoning_description")
                                    and resp.reasoning_description
                                ):
                                    chunk_response["reasoning_description"] = (
                                        resp.reasoning_description
                                    )
                                chunk_responses.append(chunk_response)

                            # Create the response json
                            response = {
                                "id": request_id,
                                "model": model_info.id,
                                "created": int(req_info.queue_time),
                                "media_info": media_info,
                                "chunk_responses": chunk_responses,
                                "usage": None,
                            }
                            # Yield to generate a server-sent event
                            yield json.dumps(response)
                            try:
                                req_info, resp_list = self._stream_handler.get_response(
                                    request_id, 1
                                )
                            except ViaException:
                                break

                    # Generate usage data and send as server-sent event if requested
                    if query.stream_options and query.stream_options.include_usage:
                        try:
                            req_info, resp_list = self._stream_handler.get_response(request_id, 0)
                            end_time = (
                                req_info.end_time if req_info.end_time is not None else time.time()
                            )
                            response = {
                                "id": request_id,
                                "model": model_info.id,
                                "created": int(req_info.queue_time),
                                "media_info": None,
                                "usage": {
                                    "total_chunks_processed": req_info.chunk_count,
                                    "query_processing_time": int(end_time - req_info.start_time),
                                },
                            }
                            yield json.dumps(response)
                        except ViaException:
                            pass
                    yield "[DONE]"
                    self._sse_active_clients.pop(videoId, None)
                    self._stream_handler.check_status_remove_req_id(request_id)

                return EventSourceResponse(message_generator(), send_timeout=5, ping=1)
            else:
                # Non-streaming output. Wait for request to be completed.
                await loop.run_in_executor(
                    self._async_executor, self._stream_handler.wait_for_request_done, request_id
                )
                req_info, resp_list = self._stream_handler.get_response(request_id)
                self._stream_handler.check_status_remove_req_id(request_id)
                if req_info.status == RequestInfo.Status.FAILED:
                    raise ViaException(
                        "Failed to generate VLM captions", "InternalServerError", 500
                    )

                # Create response json and return it
                return VlmCaptionsCompletionResponse(
                    id=request_id,
                    model=model_info.id,
                    created=int(req_info.queue_time),
                    media_info=MediaInfoOffset(
                        type="offset",
                        start_offset=int(req_info.start_timestamp or 0),
                        end_offset=int(req_info.end_timestamp or 0),
                    ),
                    chunk_responses=(
                        [
                            VlmCaptionResponse(
                                start_time=(
                                    resp.start_timestamp
                                    if req_info.is_live
                                    else str(resp.start_timestamp)
                                ),
                                end_time=(
                                    resp.end_timestamp
                                    if req_info.is_live
                                    else str(resp.end_timestamp)
                                ),
                                content=resp.response,
                                reasoning_description=getattr(resp, "reasoning_description", ""),
                            )
                            for resp in resp_list
                        ]
                        if resp_list
                        else []
                    ),
                    usage=CompletionUsage(
                        total_chunks_processed=req_info.chunk_count,
                        query_processing_time=int(
                            (req_info.end_time or 0) - (req_info.start_time or 0)
                        ),
                    ),
                )

    def _setup_routes(self):

        # Mount the ASGI app exposed by prometheus client as a FastAPI endpoint.
        @self._app.get(
            f"{API_PREFIX}/metrics",
            summary="Get LVS metrics",
            description="Get LVS metrics in prometheus format.",
            responses={
                200: {"description": "Successful Response."},
                **add_common_error_responses([500]),
            },
            tags=["Metrics"],
        )
        def metrics():
            return Response(content=generate_latest(), media_type="text/plain")

        # ======================= Health check API
        @self._app.get(
            "/v1/live",
            summary="Get LVS liveness status",
            description="Get LVS liveness status (v1 endpoint).",
            responses={
                200: {"model": None, "description": "Successful Response."},
                **add_common_error_responses([500]),
            },
            tags=["Health Check"],
        )
        async def v1_live_probe():
            return Response(status_code=200)

        @self._app.get(
            "/v1/ready",
            summary="Get LVS readiness status",
            description="Get LVS readiness status (v1 endpoint).",
            responses={
                200: {"model": None, "description": "Successful Response."},
                503: {
                    "model": LvsError,
                    "description": "Service not ready. A required dependency is unavailable.",
                },
                **add_common_error_responses([500]),
            },
            tags=["Health Check"],
        )
        async def v1_ready_probe():
            if not hasattr(self, "_stream_handler"):
                return JSONResponse(
                    status_code=503,
                    content={
                        "code": "DependencyUnavailable",
                        "message": "VIA stream handler not initialized",
                    },
                )
            try:
                rtvi_ok = self._stream_handler._vlm_pipeline.check_health()
            except Exception:
                rtvi_ok = False
            if not rtvi_ok:
                return JSONResponse(
                    status_code=503,
                    content={
                        "code": "DependencyUnavailable",
                        "message": "RTVI VLM service is not ready",
                    },
                )
            return Response(status_code=200)

        @self._app.get(
            "/v1/startup",
            summary="Get LVS startup status",
            description="Get LVS startup status (v1 endpoint).",
            responses={
                200: {"model": None, "description": "Successful Response."},
                **add_common_error_responses([500]),
            },
            tags=["Health Check"],
        )
        async def v1_startup_probe():
            return Response(status_code=200)

        @self._app.get(
            "/v1/healthz",
            summary="Get VIA service health status",
            description="Get VIA service health status with version information.",
            responses={
                200: {"description": "Successful Response."},
                **add_common_error_responses([500]),
            },
            tags=["Health Check"],
        )
        async def v1_healthz():
            return JSONResponse(content={"status": "ok", "version": get_version()})

        @self._app.get(
            "/v1/metadata",
            summary="Get LVS service metadata",
            description="Get LVS service metadata information.",
            responses={
                200: {"description": "Successful Response."},
                **add_common_error_responses([500]),
            },
            tags=["Health Check"],
        )
        async def v1_metadata():
            metadata = {
                "version": "3.0.0",
                "sub_version": get_build_commit_sha(),
                "licenseInfo": {
                    "name": "LicenseRef-NvidiaProprietary",
                    "path": "/opt/mm/LICENSE",
                },
                "host": self._args.host,
                "port": self._args.port,
            }
            return JSONResponse(content=metadata)

        # ======================= Health check API

        # ======================= Models API
        @self._app.get(
            f"{API_PREFIX}/models",
            summary=(
                "Lists the currently available models, and provides basic information"
                " about each one such as the owner and availability"
            ),
            description=(
                "Lists the currently available models, and provides basic information"
                " about each one such as the owner and availability."
            ),
            responses={
                200: {"description": "Successful Response."},
                **add_common_error_responses([500]),
            },
            tags=["Models"],
        )
        async def list_models() -> ListModelsResponse:

            # Get the loaded model information from pipeline
            minfo = self._stream_handler.get_models_info()

            logger.info("Received list models request. Responding with 1 models info")
            return {
                "object": "list",
                "data": [
                    {
                        "id": minfo.id,
                        "created": int(minfo.created),
                        "object": "model",
                        "owned_by": minfo.owned_by,
                        "api_type": minfo.api_type,
                    }
                ],
            }

        # ======================= Models API

        # ======================= Stream Captioning API

        @self._app.post(
            "/v1/generate_captions",
            summary="Start stream captioning",
            description=(
                "Fire-and-forget: kicks off VLM captioning on RTVI for a stream "
                "that was previously added via RTVI stream/add. Returns immediately "
                "once RTVI acknowledges the request."
            ),
            responses={
                200: {"model": GenerateCaptionsResponse, "description": "Captioning started."},
                **add_common_error_responses(),
            },
            tags=["Stream"],
        )
        async def generate_captions(
            request_body: GenerateCaptionsRequest,
        ) -> GenerateCaptionsResponse:
            if not self._stream_handler._kafka_enabled:
                raise ViaException(
                    "Livestream APIs require KAFKA_ENABLED=true",
                    "BadConfiguration",
                    400,
                )

            stream_id = str(request_body.id)

            model_info = self._stream_handler.get_models_info()
            if request_body.model != model_info.id:
                raise ViaException(f"No such model '{request_body.model}'", "BadParameters", 400)

            logger.info(
                "Received generate_captions request: stream_id=%s, model=%s, " "chunk_duration=%d",
                stream_id,
                request_body.model,
                request_body.chunk_duration,
            )

            loop = asyncio.get_event_loop()
            try:
                await loop.run_in_executor(
                    self._async_executor,
                    self._stream_handler.start_stream_captions,
                    request_body,
                )
            except ViaException:
                raise
            except Exception as ex:
                if isinstance(ex, RtviError):
                    raise ViaException(ex.message, ex.code, ex.status_code)
                if isinstance(
                    ex, (requests.exceptions.ConnectionError, requests.exceptions.Timeout)
                ):
                    rtvi_url = getattr(self._stream_handler, "_vlm_pipeline", None)
                    rtvi_url = getattr(rtvi_url, "_base_url", "unknown") if rtvi_url else "unknown"
                    logger.error("RTVI dependency is down (url=%s): %s", rtvi_url, ex)
                    raise ViaException(
                        f"RTVI dependency is down at {rtvi_url}",
                        "DependencyUnavailable",
                        503,
                    )
                raise

            return GenerateCaptionsResponse(
                id=stream_id,
                status="accepted",
                model=model_info.id,
            )

        # ======================= Stream Captioning API

        # ======================= Stream Summarize API

        @self._app.post(
            "/v1/stream_summarize",
            summary="Summarize a stream",
            description=(
                "Aggregate existing captions for a stream from the database via "
                "CA-RAG and return a structured summary. The stream must have been "
                "previously started with /v1/generate_captions."
            ),
            responses={
                200: {"description": "Successful Response."},
                **add_common_error_responses(),
                503: {
                    "model": LvsError,
                    "description": "Server is busy. Client may try again later.",
                },
            },
            tags=["Stream"],
        )
        async def stream_summarize(
            request_body: StreamSummarizeRequest,
        ) -> CompletionResponse:
            if not self._stream_handler._kafka_enabled:
                raise ViaException(
                    "Livestream APIs require KAFKA_ENABLED=true",
                    "BadConfiguration",
                    400,
                )

            stream_id = str(request_body.id)

            model_info = self._stream_handler.get_models_info()
            if request_body.model != model_info.id:
                raise ViaException(f"No such model '{request_body.model}'", "BadParameters", 400)

            if self._stream_handler._args.disable_ca_rag:
                raise ViaException(
                    "CA-RAG is required for stream summarization",
                    "BadParameters",
                    400,
                )

            logger.info(
                "Received stream_summarize request: stream_id=%s, model=%s, "
                "start_time=%s, end_time=%s",
                stream_id,
                request_body.model,
                request_body.start_time,
                request_body.end_time,
            )

            loop = asyncio.get_event_loop()
            request_id = await loop.run_in_executor(
                self._async_executor,
                self._stream_handler.summarize_stream,
                request_body,
            )

            logger.info(
                "Created stream_summarize query %s for stream %s",
                request_id,
                stream_id,
            )

            await loop.run_in_executor(
                self._async_executor,
                self._stream_handler.wait_for_request_done,
                request_id,
            )
            req_info, resp_list = self._stream_handler.get_response(request_id)
            self._stream_handler.check_status_remove_req_id(request_id)

            if req_info.status == RequestInfo.Status.FAILED:
                rtvi_status = getattr(req_info, "rtvi_status_code", None)
                rtvi_code = getattr(req_info, "rtvi_error_code", None)
                error_msg = req_info.error_message or "Unknown error"
                raise ViaException(
                    error_msg,
                    rtvi_code or "InternalServerError",
                    rtvi_status or 500,
                )

            return CompletionResponse(
                id=request_id,
                video_id=stream_id,
                model=model_info.id,
                created=int(req_info.queue_time),
                object=CompletionObject.SUMMARIZATION_COMPLETION,
                media_info=MediaInfoOffset(type="offset", start_offset=0, end_offset=0),
                choices=(
                    [
                        CompletionResponseChoice(
                            finish_reason=CompletionFinishReason.STOP,
                            index=0,
                            message=ChatCompletionResponseMessage(
                                content=resp_list[0].response,
                                role="assistant",
                            ),
                        )
                    ]
                    if resp_list
                    else []
                ),
                usage=CompletionUsage(
                    total_chunks_processed=req_info.chunk_count,
                    query_processing_time=int(
                        (req_info.end_time or 0) - (req_info.start_time or 0)
                    ),
                ),
            )

        # ======================= Stream Summarize API

        # ======================= Chat Completions API (Graph-based QA)

        @self._app.post(
            "/v1/chat/completions",
            summary="Ask a question about a summarized video/stream",
            description=(
                "QA over a previously summarized video or stream. "
                "The asset must have been processed with enable_qa=true so "
                "that the knowledge was built. Sends the last user "
                "message as the question to the retriever function."
            ),
            responses={
                200: {"description": "Successful Response."},
                **add_common_error_responses(),
            },
            tags=["Chat"],
        )
        async def chat_completions(
            query: ChatCompletionQuery,
        ) -> CompletionResponse:
            if self._stream_handler._args.disable_ca_rag:
                raise ViaException(
                    "CA-RAG is required for chat completions",
                    "BadConfiguration",
                    400,
                )

            model_info = self._stream_handler.get_models_info()
            if query.model != model_info.id:
                raise ViaException(f"No such model '{query.model}'", "BadParameters", 400)

            asset_id = str(query.id)

            logger.info(
                "Received chat/completions request: asset_id=%s, model=%s",
                asset_id,
                query.model,
            )

            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                self._async_executor,
                self._stream_handler.chat_completion,
                query,
            )

            answer = result.get("answer", "")

            return CompletionResponse(
                id=asset_id,
                video_id=asset_id,
                model=model_info.id,
                created=int(time.time()),
                object=CompletionObject.CHAT_COMPLETION,
                media_info=MediaInfoOffset(type="offset", start_offset=0, end_offset=0),
                choices=[
                    CompletionResponseChoice(
                        finish_reason=CompletionFinishReason.STOP,
                        index=0,
                        message=ChatCompletionResponseMessage(
                            content=answer,
                            role="assistant",
                        ),
                    )
                ],
                usage=CompletionUsage(
                    total_chunks_processed=0,
                    query_processing_time=0,
                ),
            )

        # ======================= Chat Completions API

        # ======================= Summarize API

        @self._app.post(
            f"{API_PREFIX}/summarize",
            summary="Summarize a video file",
            description="Run video file summarization.",
            responses={
                200: {"description": "Successful Response."},
                **add_common_error_responses(),
                503: {
                    "model": LvsError,
                    "description": (
                        "Server is busy processing another file."
                        " Client may try again in some time."
                    ),
                },
            },
            tags=["Summarization"],
        )
        @self._app.post(
            "/v1/summarize",
            summary="Summarize a video file",
            description="Run video file summarization.",
            responses={
                200: {"description": "Successful Response."},
                **add_common_error_responses(),
                503: {
                    "model": LvsError,
                    "description": (
                        "Server is busy processing another file."
                        " Client may try again in some time."
                    ),
                },
            },
            tags=["Summarization"],
        )
        async def summarize(query: SummarizationQuery, request: Request) -> CompletionResponse:
            # ---- File-based summarization ----
            # Use client-provided id if present; otherwise a new id is generated
            client_id = str(query.id_list[0]) if query.id else None
            # Allow id-only when asset was pre-uploaded via /files
            if not query.url and not client_id:
                raise ViaException(
                    "url or id is required for file summarization",
                    "InvalidParameters",
                    422,
                )
            source = RequestSource.for_file(query.url, query.camera_id)
            if client_id:
                source.source_id = client_id
                logger.info(
                    "File summarize: source_id=%s (client), url=%s",
                    source.source_id,
                    source.url or "(pre-uploaded)",
                )
            else:
                logger.info(
                    "File summarize: source_id=%s (generated), url=%s",
                    source.source_id,
                    source.url,
                )

            media_info_start = None
            media_info_end = None

            if query.media_info:
                if query.media_info.type == "offset":
                    media_info_start = query.media_info.start_offset
                    media_info_end = query.media_info.end_offset
                if query.media_info.type == "timestamp":
                    media_info_start = query.media_info.start_timestamp
                    media_info_end = query.media_info.end_timestamp

            logger.info(
                "Received summarize query, source_id=%s, url=%s, "
                "chunk_duration=%s, chunk_overlap_duration=%s, "
                "media-offset-type=%s, media-start-time=%r, "
                "media-end-time=%r, modelParams=%s, "
                "stream=%r, "
                "vlm_input_width=%s, "
                "vlm_input_height=%s, "
                "summarize_batch_size=%s, "
                "summarize_max_tokens=%s, "
                "summarize_temperature=%s, "
                "summarize_top_p=%s, "
                "summarization_enabled=%s, "
                "collection_name=%s, "
                "custom_metadata=%s, "
                "delete_external_collection=%s, "
                "camera_id=%s, "
                "enable_audio=%s, "
                "enable_vlm_structured_output=%s, "
                "events=%s, "
                "objects_of_interest=%s, "
                "scenario=%s, "
                "override_vlm_prompt=%s",
                source.source_id,
                source.url,
                query.chunk_duration,
                query.chunk_overlap_duration,
                query.media_info and query.media_info.type,
                media_info_start,
                media_info_end,
                json.dumps(
                    {
                        "max_tokens": query.max_tokens,
                        "temperature": query.temperature,
                        "top_p": query.top_p,
                        "top_k": query.top_k,
                    }
                ),
                query.stream,
                query.vlm_input_width,
                query.vlm_input_height,
                query.summarize_batch_size,
                query.summarize_max_tokens,
                query.summarize_temperature,
                query.summarize_top_p,
                query.summarize,
                query.collection_name,
                str(query.custom_metadata),
                query.delete_external_collection,
                query.camera_id,
                query.enable_audio,
                query.enable_vlm_structured_output,
                query.events,
                query.objects_of_interest,
                query.scenario,
                query.override_vlm_prompt,
            )

            # Save stream settings to json file
            filtered_query_json = self._stream_settings_cache.transform_query(query.get_query_json)
            logger.debug(f"Filtered Query JSON: {filtered_query_json}")
            self._stream_settings_cache.update_stream_settings(
                source.source_id, filtered_query_json
            )

            # Check if user has specified the model that is initialized
            model_info = self._stream_handler.get_models_info()
            if query.model != model_info.id:
                raise ViaException(f"No such model '{query.model}'", "BadParameters", 400)

            if query.api_type and query.api_type != model_info.api_type:
                raise ViaException(
                    f"api_type {query.api_type} not supported by model '{query.model}'",
                    "BadParameters",
                    400,
                )

            # For non-CA RAG usecase, only streaming output is supported
            if self._stream_handler._ctx_mgr is None and not query.stream:
                raise ViaException(
                    "Only streaming output is supported for files when CA-RAG is disabled",
                    "BadParameters",
                    400,
                )

            loop = asyncio.get_event_loop()
            videoId = source.source_id

            # File-based summarization
            request_id = await loop.run_in_executor(
                self._async_executor,
                self._stream_handler.summarize,
                source,
                query,
            )
            logger.info("Created video file query %s for source %s", request_id, videoId)

            logger.info("Waiting for results of query %s", request_id)

            if query.stream:
                # Allow only a single client for streaming output per source
                if time.time() - self._sse_active_clients.get(videoId, 0) < 3:
                    raise ViaException(
                        "Another client is already connected to this source", "Conflict", 409
                    )

                # Server side events generator
                async def message_generator():
                    last_status_report_time = 0
                    last_status = None
                    while True:
                        self._sse_active_clients[videoId] = time.time()
                        try:
                            message = await asyncio.wait_for(request._receive(), timeout=0.01)
                            if message.get("type") == "http.disconnect":
                                self._sse_active_clients.pop(videoId, None)
                                logger.info(
                                    "Client %s disconnected for source %s",
                                    request.client.host,
                                    videoId,
                                )
                                return
                        except Exception:
                            pass

                        # Get current response status from the pipeline
                        try:
                            req_info, resp_list = self._stream_handler.get_response(request_id, 1)
                        except ViaException:
                            break
                        if (
                            time.time() - last_status_report_time >= 10
                            or resp_list
                            or last_status != req_info.status
                        ):
                            last_status_report_time = time.time()
                            last_status = req_info.status
                            logger.info(
                                "Status for query %s is %s, percent complete is %.2f,"
                                " size of response list is %d",
                                req_info.request_id,
                                req_info.status.value,
                                req_info.progress,
                                len(resp_list),
                            )

                        # Response list is empty. Stop generation if request is completed or failed.
                        if not resp_list:
                            if req_info.status in [
                                RequestInfo.Status.SUCCESSFUL,
                                RequestInfo.Status.FAILED,
                            ]:
                                if req_info.status == RequestInfo.Status.FAILED:
                                    # Create the response json (include media_info for API consistency)
                                    _start = (
                                        int(req_info.start_timestamp)
                                        if req_info.start_timestamp is not None
                                        else 0
                                    )
                                    _end = (
                                        int(req_info.end_timestamp)
                                        if req_info.end_timestamp is not None
                                        else 0
                                    )
                                    response = {
                                        "id": request_id,
                                        "video_id": videoId,
                                        "model": model_info.id,
                                        "created": int(req_info.queue_time),
                                        "object": "summarization.progressing",
                                        "media_info": {
                                            "type": "offset",
                                            "start_offset": _start,
                                            "end_offset": _end,
                                        },
                                        "choices": [
                                            {
                                                "finish_reason": CompletionFinishReason.STOP.value,
                                                "index": 0,
                                                "message": {
                                                    "content": "Summarization failed."
                                                    + " "
                                                    + req_info.error_message,
                                                    "role": "assistant",
                                                },
                                            }
                                        ],
                                        "usage": None,
                                    }
                                    yield json.dumps(response)
                                break
                            await asyncio.sleep(1)
                            continue

                        # Set the start/end time info for current response.
                        while resp_list:
                            if req_info.is_live:
                                media_info = {
                                    "type": "timestamp",
                                    "start_timestamp": resp_list[0].start_timestamp,
                                    "end_timestamp": resp_list[0].end_timestamp,
                                }

                                dt = datetime.strptime(
                                    resp_list[0].end_timestamp, "%Y-%m-%dT%H:%M:%S.%fZ"
                                ).replace(tzinfo=timezone.utc)
                                current_time = datetime.now(timezone.utc)
                                self._stream_handler.update_live_stream_summary_latency(
                                    (current_time - dt).total_seconds()
                                )
                            else:
                                media_info = {
                                    "type": "offset",
                                    "start_offset": int(resp_list[0].start_timestamp),
                                    "end_offset": int(resp_list[0].end_timestamp),
                                }

                            # Create the response json
                            response = {
                                "id": request_id,
                                "video_id": videoId,
                                "model": model_info.id,
                                "created": int(req_info.queue_time),
                                "object": "summarization.progressing",
                                "media_info": media_info,
                                "choices": [
                                    {
                                        "finish_reason": CompletionFinishReason.STOP.value,
                                        "index": 0,
                                        "message": {
                                            "content": resp_list[0].response,
                                            "role": "assistant",
                                        },
                                    }
                                ],
                                "usage": None,
                            }
                            # Yield to generate a server-sent event
                            yield json.dumps(response)
                            try:
                                req_info, resp_list = self._stream_handler.get_response(
                                    request_id, 1
                                )
                            except ViaException:
                                break

                    # Generate usage data and send as server-sent event if requested
                    if query.stream_options and query.stream_options.include_usage:
                        try:
                            req_info, resp_list = self._stream_handler.get_response(request_id, 0)
                            end_time = (
                                req_info.end_time if req_info.end_time is not None else time.time()
                            )
                            response = {
                                "id": request_id,
                                "video_id": videoId,
                                "model": model_info.id,
                                "created": int(req_info.queue_time),
                                "object": "summarization.completion",
                                "media_info": None,
                                "choices": [],
                                "usage": {
                                    "total_chunks_processed": req_info.chunk_count,
                                    "query_processing_time": int(end_time - req_info.start_time),
                                },
                            }
                            yield json.dumps(response)
                        except ViaException:
                            pass
                    yield "[DONE]"
                    self._sse_active_clients.pop(videoId, None)
                    self._stream_handler.check_status_remove_req_id(request_id)

                return EventSourceResponse(message_generator(), send_timeout=5, ping=1)
            else:
                # Non-streaming output. Wait for request to be completed.
                await loop.run_in_executor(
                    self._async_executor, self._stream_handler.wait_for_request_done, request_id
                )
                req_info, resp_list = self._stream_handler.get_response(request_id)
                self._stream_handler.check_status_remove_req_id(request_id)
                if req_info.status == RequestInfo.Status.FAILED:
                    # Forward RTVI error codes if available. A
                    # classified ES dependency error sets
                    # `dependency_http_status` (503) and
                    # `dependency_error_code` ("DependencyError") on
                    # req_info; prefer those over the generic 500/
                    # InternalServerError default so /v1/summarize
                    # surfaces a 503 with the classified message rather
                    # than hanging or returning 500.
                    rtvi_status = getattr(req_info, "rtvi_status_code", None)
                    rtvi_code = getattr(req_info, "rtvi_error_code", None)
                    dep_status = getattr(req_info, "dependency_http_status", None)
                    dep_code = getattr(req_info, "dependency_error_code", None)
                    raise ViaException(
                        req_info.error_message or "Failed to generate summary",
                        rtvi_code or dep_code or "InternalServerError",
                        rtvi_status or dep_status or 500,
                    )

                # Create response json and return it
                return {
                    "id": request_id,
                    "video_id": videoId,
                    "model": model_info.id,
                    "created": int(req_info.queue_time),
                    "object": "summarization.completion",
                    "media_info": {
                        "type": "offset",
                        "start_offset": int(req_info.start_timestamp or 0),
                        "end_offset": int(req_info.end_timestamp or 0),
                    },
                    "choices": (
                        [
                            {
                                "finish_reason": CompletionFinishReason.STOP.value,
                                "index": 0,
                                "message": {"content": resp_list[0].response, "role": "assistant"},
                            }
                        ]
                        if resp_list
                        else []
                    ),
                    "usage": {
                        "total_chunks_processed": req_info.chunk_count,
                        "query_processing_time": int(
                            (req_info.end_time or 0) - (req_info.start_time or 0)
                        ),
                        "summary_tokens": req_info.usage.summary_tokens,
                        "aggregation_tokens": req_info.usage.aggregation_tokens,
                        "summary_requests": req_info.usage.summary_requests,
                        "summary_latency": req_info.usage.summary_latency,
                        "aggregation_latency": req_info.usage.aggregation_latency,
                    },
                }

        # ======================= Summarize API

        # ======================= Summarize API

        def _format_chunk_response(resp, req_info):
            """Format a chunk response with timestamp for display.

            Args:
                resp: Response object with start_timestamp, end_timestamp, and response fields
                req_info: Request info object with is_live field

            Returns:
                str: Formatted chunk response with timestamp
            """
            if req_info.is_live:
                start_time = resp.start_timestamp
                end_time = resp.end_timestamp
            else:
                start_time = str(resp.start_timestamp)
                end_time = str(resp.end_timestamp)

            return f"[{start_time} - {end_time}] {resp.response}"

        # ======================= Summarize API

        # NOTE: Live-stream summarization uses the dedicated APIs:
        # POST /v1/generate_captions + POST /v1/stream_summarize.
        # The standalone POST /v1/aggregate_live_stream endpoint that briefly
        # existed in commit 339b61c has been removed. Captioning is triggered upstream by RTVI on
        # POST /v1/stream/add when metadata.prompt is set (auto-inference);
        # LVS no longer triggers RTVI captioning. See
        # docs/streaming_rtvi_kafka_logstash.md for the operator workflow.

        # ======================= Recommended Config API

        # Returns recommended config viz: chunk-size
        # based on /opt/nvidia/via/default_runtime_stats.yaml
        # Notes:
        # 1) return chunk-size = 0 if GPU config unavailable in the yaml file
        @self._app.post(
            f"{API_PREFIX}/recommended_config",
            summary="Recommend config for a video",
            description="Recommend config for a video.",
            responses={
                200: {"description": "Successful Response."},
                **add_common_error_responses(),
            },
            tags=["Recommended Config"],
        )
        async def recommended_config(
            query: RecommendedConfig, request: Request
        ) -> RecommendedConfigResponse:
            def round_up(s):
                """
                Rounds up a string representation of a number to an integer.

                Example:
                >>> round_up("7.9s")
                8
                """
                # Strip any non-numeric characters from the string
                num_str = re.sub(r"[a-zA-Z]+", "", s)

                # Convert the string to a float and round up to the nearest integer
                num = float(num_str)
                return -(-num // 1)  # equivalent to math.ceil(num) in Python 3.x

            logger.info(
                f"recommended_config(); chunk_size={query.video_length};"
                f" target_response_time={query.target_response_time};"
                f" usecase_event_duration={query.usecase_event_duration}"
            )
            recommended_chunk_size = 60
            recommendation_text = "NA"

            model_id = "rtvi"

            try:
                # In RTVI-only mode there are no local GPUs. Use a sensible default
                # based on the runtime stats file if available.
                try:
                    avg_time_per_chunk = get_avg_time_per_chunk(
                        "RTVI", model_id, "/opt/nvidia/via/default_runtime_stats.yaml"
                    )
                    avg_time_per_chunk = round_up(avg_time_per_chunk)
                    recommended_chunk_size = (
                        avg_time_per_chunk * query.video_length
                    ) / query.target_response_time
                    if recommended_chunk_size > query.video_length:
                        recommended_chunk_size = query.video_length
                    logger.info(f"recommended_chunk_size is {recommended_chunk_size}")
                except Exception:
                    recommended_chunk_size = 60
            except Exception:
                exc_type, exc_value, exc_traceback = sys.exc_info()
                error_string = "".join(
                    traceback.format_exception(exc_type, exc_value, exc_traceback)
                )
                logger.info(error_string)
                recommended_chunk_size = 0

            # Create response json and return it
            return {"chunk_size": int(recommended_chunk_size), "text": recommendation_text}

        # ======================= Recommended Config API

    def _setup_exception_handlers(self):
        # Handle incorrect request schema (user error)
        @self._app.exception_handler(RequestValidationError)
        async def handle_validation_error(request, ex) -> LvsError:
            err = ex.args[0][0]
            loc = str(err["loc"])
            try:
                loc = str(err["loc"])
            except Exception:
                loc = ".".join(str(err["loc"]))
            msg = err["msg"].replace("UploadFile", "'bytes'").replace("<class 'str'>", "'string'")
            if err["type"] in ["value_error", "uuid_parsing", "string_pattern_mismatch"]:
                msg += f" (input: {json.dumps(err['input'])})"
            return JSONResponse(
                status_code=422, content={"code": "InvalidParameters", "message": f"{loc}: {msg}"}
            )

        # Handle exceptions and return error details in format specified in the API schema.
        @self._app.exception_handler(ViaException)
        async def handle_via_exception(request, ex: ViaException) -> LvsError:
            return JSONResponse(
                status_code=ex.status_code, content={"code": ex.code, "message": ex.message}
            )

        # Handle exceptions and return error details in format specified in the API schema.
        @self._app.exception_handler(HTTPException)
        async def handle_http_exception(request, ex: HTTPException) -> LvsError:
            return JSONResponse(
                status_code=ex.status_code, content={"code": ex.detail, "message": ex.detail}
            )

        # Unhandled backend errors. Return error details in format specified in the API schema.
        @self._app.exception_handler(Exception)
        async def handle_exception(request, ex: Exception) -> LvsError:
            return JSONResponse(
                status_code=500,
                content={
                    "code": "InternalServerError",
                    "message": "An internal server error occured",
                },
            )

    def _setup_openapi_schema(self):
        orig_openapi = self._app.openapi

        def custom_openapi():
            if self._app.openapi_schema:
                return self._app.openapi_schema
            openapi_schema = orig_openapi()
            openapi_schema["security"] = [{"Token": []}]
            openapi_schema["components"]["securitySchemes"] = {
                "Token": {"type": "http", "scheme": "bearer"}
            }

            # Only adjust schemas for dev-only routes when dev API is enabled.
            if os.environ.get("VIA_DEV_API", "").lower() in ["true", "1"]:
                try:
                    openapi_schema["components"]["schemas"]["Body_add_video_file_files_post"][
                        "description"
                    ] = "Request body schema for adding a file."
                    openapi_schema["components"]["schemas"]["Body_add_video_file_files_post"][
                        "properties"
                    ]["file"]["maxLength"] = 100e9
                except KeyError:
                    logger.debug("Skipping files schema customization; schema not found.")

            openapi_schema["components"]["schemas"]["SummarizationQuery"]["properties"]["id"][
                "anyOf"
            ][1]["maxItems"] = 50

            def search_dict(d):
                if isinstance(d, dict):
                    for k, v in d.items():
                        if isinstance(v, dict):
                            search_dict(v)
                        elif isinstance(v, list):
                            for item in v:
                                search_dict(item)
                        else:
                            if k == "format" and v == "uuid":
                                d["maxLength"] = UUID_LENGTH
                                d["minLength"] = UUID_LENGTH
                                break
                    if "enum" in d and "const" in d:
                        d.pop("const")
                elif isinstance(d, list):
                    for item in d:
                        search_dict(item)

            search_dict(openapi_schema)

            self._app.openapi_schema = openapi_schema
            return self._app.openapi_schema

        self._app.openapi = custom_openapi

    @staticmethod
    def populate_argument_parser(parser: argparse.ArgumentParser):
        from via_stream_handler import ViaStreamHandler

        ViaStreamHandler.populate_argument_parser(parser)

        parser.add_argument("--host", type=str, help="Address to run server on", default="0.0.0.0")
        parser.add_argument("--port", type=str, help="port to run server on", default="8000")
        parser.add_argument(
            "--log-level",
            type=str,
            choices=["error", "warn", "info", "debug", "perf"],
            default="info",
            help="Application log level",
        )

    @staticmethod
    def get_argument_parser():
        parser = argparse.ArgumentParser(
            "VIA Server", formatter_class=argparse.ArgumentDefaultsHelpFormatter
        )
        ViaServer.populate_argument_parser(parser)
        return parser


if __name__ == "__main__":

    parser = ViaServer.get_argument_parser()
    args = parser.parse_args()

    server = ViaServer(args)
    server.run()
