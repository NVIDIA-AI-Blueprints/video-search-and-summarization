######################################################################################################
# SPDX-FileCopyrightText: Copyright (c) 2024-2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: LicenseRef-NvidiaProprietary
#
# NVIDIA CORPORATION, its affiliates and licensors retain all intellectual
# property and proprietary rights in and to this material, related
# documentation and any modifications thereto. Any use, reproduction,
# disclosure or distribution of this material and related documentation
# without an express license agreement from NVIDIA CORPORATION or
# its affiliates is strictly prohibited.
######################################################################################################
"""Implements the RTVI REST API.

Translates between requests/responses and RTVIStreamHandler and AssetManager methods.
"""

from .rtvi_stream_handler import (  # isort:skip
    RequestInfo,
    RTVIStreamHandler,
)

import argparse
import asyncio
import gc
import json
import os
import re
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Annotated
from uuid import UUID

import aiofiles
import aiofiles.os
import gi
import uvicorn
from fastapi import FastAPI, File, Form, Path, Query, Request, Response, UploadFile
from fastapi.exceptions import HTTPException, RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from pydantic import Field
from sse_starlette.sse import EventSourceResponse

from api_models.captions import (
    CompletionUsage,
    MediaInfoOffset,
    VlmCaptionResponse,
    VlmCaptionsCompletionResponse,
    VlmQuery,
)
from api_models.common import FILE_NAME_PATTERN, PATH_PATTERN, UUID_LENGTH, ServiceError
from api_models.file import (
    AddFileInfoResponse,
    DeleteFileResponse,
    FileInfo,
    ListFilesResponse,
    MediaType,
    Purpose,
)
from api_models.live_stream import AddLiveStream, AddLiveStreamResponse, LiveStreamInfo
from api_models.models import ListModelsResponse
from common.logger import LOG_PERF_LEVEL, TimeMeasure, logger
from common.service_exception import ServiceException
from common.version import VERSION
from utils.asset_manager import Asset, AssetManager
from utils.media_file_info import MediaFileInfo

gi.require_version("GstRtsp", "1.0")  # isort:skip

from gi.repository import GstRtsp  # noqa: E402

API_PREFIX = "/v1"

# Cache environment variables for performance
_SKIP_INPUT_MEDIA_VERIFICATION = not os.environ.get("VSS_SKIP_INPUT_MEDIA_VERIFICATION", "")
_FORCE_GC = bool(os.environ.get("FORCE_PYTHON_GC"))
_ENABLE_AUDIO = os.environ.get("VLM_MODEL_SUPPORTS_AUDIO", "false").lower() == "true"

COMMON_ERROR_RESPONSES = {
    400: {
        "model": ServiceError,
        "description": (
            "Bad Request. The server could not understand the request due to invalid syntax."
        ),
    },
    401: {"model": ServiceError, "description": "Unauthorized request."},
    422: {"model": ServiceError, "description": "Failed to process request."},
    500: {"model": ServiceError, "description": "Internal Server Error."},
    429: {
        "model": ServiceError,
        "description": "Rate limiting exceeded.",
    },
}

# Compile regex patterns at module level for performance
_FILE_NAME_REGEX = re.compile(FILE_NAME_PATTERN)


def add_common_error_responses(errors=None):
    if errors is None:
        return COMMON_ERROR_RESPONSES
    return {err: COMMON_ERROR_RESPONSES[err] for err in (errors + [401, 429, 422])}


class RTVIServer:
    def __init__(self, args) -> None:
        self._args = args

        self._asset_manager = AssetManager(
            args.asset_dir,
            max_storage_usage_gb=args.max_asset_storage_size,
            asset_removal_callback=self._remove_asset,
        )

        self._async_executor = ThreadPoolExecutor(
            max_workers=args.max_live_streams, thread_name_prefix="rtvi-async-worker"
        )

        # Use FastAPI to implement the REST API
        openapi_tags = [
            {
                "name": "Captions",
                "description": "Operations to generate captions for a video.",
            },
            {
                "name": "Files",
                "description": "Files are used to upload and manage media files.",
            },
            {"name": "Health Check", "description": "Operations to check system health."},
            {"name": "Live Stream", "description": "Operations related to live streams."},
            {"name": "Metrics", "description": "Operations to get metrics."},
            {
                "name": "Models",
                "description": "List and describe the various models available in the API.",
            },
        ]
        openapi_tags.sort(key=lambda x: x["name"])
        self._app = FastAPI(
            contact={"name": "NVIDIA", "url": "https://nvidia.com"},
            description="NVIDIA RTVI VLM API.",
            title="RTVI API",
            openapi_tags=openapi_tags,
            servers=[
                {
                    "url": "/",
                    "description": "RTVI microservice local endpoint.",
                    "x-internal": False,
                }
            ],
            version="v1",
        )

        self._setup_routes()
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

    def _remove_asset(self, asset: Asset):
        if asset.is_live:
            self._stream_handler.remove_rtsp_stream(asset)
        else:
            self._stream_handler.remove_video_file(asset)
        return True

    @staticmethod
    def _build_media_info_dict(is_live: bool, first_resp):
        """Build media_info dictionary based on live/file response."""
        if is_live:
            return {
                "type": "timestamp",
                "start_timestamp": first_resp.chunk.start_ntp,
                "end_timestamp": first_resp.chunk.end_ntp,
            }
        else:
            return {
                "type": "offset",
                "start_offset": int(first_resp.chunk.start_pts / 1e9),
                "end_offset": int(first_resp.chunk.end_pts / 1e9),
            }

    @staticmethod
    def _build_chunk_response(resp, is_live: bool, enable_audio: bool):
        """Build a single chunk response dictionary."""
        chunk_response = {
            "start_time": (resp.chunk.start_ntp if is_live else str(resp.chunk.start_pts / 1e9)),
            "end_time": (resp.chunk.end_ntp if is_live else str(resp.chunk.end_pts / 1e9)),
            "content": resp.vlm_model_output.output if resp.vlm_model_output else "",
        }
        # Add reasoning description if available
        if resp.vlm_model_output and resp.vlm_model_output.reasoning_description:
            chunk_response["reasoning_description"] = resp.vlm_model_output.reasoning_description
        if enable_audio and resp.audio_transcript and resp.audio_transcript.strip():
            chunk_response["audio_transcript"] = resp.audio_transcript.strip()
        return chunk_response

    def run(self):
        # Initialize OpenTelemetry if enabled (optional)
        try:
            from utils.otel_helper import init_otel

            # Get histogram views from RTVIStreamHandler for proper bucket configuration
            metric_views = RTVIStreamHandler.get_histogram_views()
            init_otel(
                service_name="alert-verification",
                service_version=VERSION,
                metric_views=metric_views,
            )
        except Exception as e:
            logger.debug(f"OTEL initialization failed: {e}")

        try:
            # Start the RTVI stream handler
            self._stream_handler = RTVIStreamHandler(self._args, service_name="alert-verification")
        except Exception as ex:
            raise ServiceException(f"Failed to load RTVI stream handler - {str(ex)}")

        # Configure and start the uvicorn web server
        config = uvicorn.Config(
            self._app, host=self._args.host, port=int(self._args.port), reload=True
        )
        self._server = uvicorn.Server(config)
        self._server.run()
        self._server = None

        self._stream_handler.stop()

    def _setup_routes(self):
        # Mount the ASGI app exposed by prometheus client as a FastAPI endpoint.
        @self._app.get(
            f"{API_PREFIX}/metrics",
            summary="Get RTVI metrics",
            description="Get RTVI metrics in Prometheus format.",
            responses={
                200: {"description": "Successful Response."},
                **add_common_error_responses([500]),
            },
            tags=["Metrics"],
        )
        def metrics():
            from utils.otel_helper import get_prometheus_metrics

            content = get_prometheus_metrics()
            return Response(content=content, media_type="text/plain")

        # ======================= Health check API
        @self._app.get(
            f"{API_PREFIX}/health/ready",
            summary="Get RTVI readiness status",
            description="Get RTVI readiness status.",
            responses={
                200: {
                    "model": None,
                    "description": "Service is healthy and ready to serve requests.",
                },
                503: {"model": None, "description": "Service is unhealthy."},
                **add_common_error_responses([500]),
            },
            tags=["Health Check"],
        )
        async def health_ready_probe(
            detailed: Annotated[
                bool,
                Query(description="Return detailed health status including all component checks."),
            ] = False,
        ):
            health_status = self._stream_handler.get_health_status(readiness=True)
            is_healthy = health_status["healthy"]
            if detailed:
                health_status["checks"] = [check.to_dict() for check in health_status["checks"]]
                if is_healthy:
                    return JSONResponse(status_code=200, content=health_status)
                else:
                    return JSONResponse(status_code=503, content=health_status)
            else:
                if is_healthy:
                    return Response(status_code=200, content="Service is healthy")
                else:
                    return Response(status_code=503, content="Service is not healthy")

        @self._app.get(
            f"{API_PREFIX}/health/live",
            summary="Get RTVI liveness status",
            description="Get RTVI liveness status.",
            responses={
                200: {"model": None, "description": "Service is healthy and live."},
                503: {"model": None, "description": "Service is unhealthy."},
                **add_common_error_responses([500]),
            },
            tags=["Health Check"],
        )
        async def health_live_probe(
            detailed: Annotated[
                bool,
                Query(description="Return detailed health status including all component checks."),
            ] = False,
        ):
            health_status = self._stream_handler.get_health_status()
            is_healthy = health_status["healthy"]

            if detailed:
                health_status["checks"] = [check.to_dict() for check in health_status["checks"]]
                if is_healthy:
                    return JSONResponse(status_code=200, content=health_status)
                else:
                    return JSONResponse(status_code=503, content=health_status)
            else:
                # Return simple status
                if is_healthy:
                    return Response(status_code=200, content="Service is healthy")
                else:
                    return Response(status_code=503, content="Service is not healthy")

        # ======================= Files API
        @self._app.post(
            f"{API_PREFIX}/files",
            summary="API for uploading a media file",
            description="Files are used to upload media files.",
            responses={
                200: {"description": "Successful Response."},
                **add_common_error_responses(),
            },
            tags=["Files"],
        )
        async def add_video_file(
            purpose: Annotated[
                Purpose,
                Form(
                    description=(
                        "The intended purpose of the uploaded file."
                        " For RTVI use-case this must be set to vision"
                    )
                ),
            ],
            media_type: Annotated[MediaType, Form(description="Media type (image / video).")],
            file: Annotated[
                UploadFile, File(description="File object (not file name) to be uploaded.")
            ] = None,
            filename: Annotated[
                str,
                Form(
                    description="Filename along with path to be used.",
                    max_length=256,
                    examples=["/home/ubuntu/myfile.mp4"],
                    pattern=PATH_PATTERN,
                ),
            ] = "",
        ) -> AddFileInfoResponse:

            logger.info(
                "Received add video file request - purpose %s,"
                " media_type %s have file %r, filename - %s",
                purpose,
                media_type,
                file,
                filename,
            )

            if not file and not filename:
                raise ServiceException(
                    "At least one of 'file' or 'filename' must be specified",
                    "InvalidParameters",
                    422,
                )
            if file and filename:
                raise ServiceException(
                    "Only one of 'file' or 'filename' must be specified. Both are not allowed.",
                    "InvalidParameters",
                    422,
                )

            if media_type not in ("video", "image"):
                raise ServiceException(
                    "Currently only 'video', 'image' media_type is supported.",
                    "InvalidParameters",
                    422,
                )
            if file:
                if not _FILE_NAME_REGEX.match(file.filename):
                    raise ServiceException(
                        f"filename should match pattern '{FILE_NAME_PATTERN}'", "BadParameters", 400
                    )
                # File uploaded by user
                video_id = await self._asset_manager.save_file(
                    file, file.filename, purpose, media_type
                )
            else:
                # File added as path
                video_id = self._asset_manager.add_file(
                    filename, purpose, media_type, reuse_asset=False
                )

            asset = self._asset_manager.get_asset(video_id)
            try:
                if _SKIP_INPUT_MEDIA_VERIFICATION:
                    media_info = await MediaFileInfo.get_info_async(asset.path)
                    if not media_info.video_codec:
                        raise Exception("Invalid file")
                    if (media_type == "image") != media_info.is_image:
                        raise Exception("Invalid file")

                    # Cache video FPS in the asset
                    if media_type == "video" and hasattr(media_info, "video_fps"):
                        asset.update_video_fps(float(media_info.video_fps))
            except Exception as e:
                logger.error("".join(traceback.format_exception(e)))
                self._asset_manager.cleanup_asset(video_id)
                raise ServiceException(
                    f"File does not seem to be a valid {media_type} file",
                    "InvalidFile",
                    400,
                )
            try:
                fsize = (await aiofiles.os.stat(asset.path)).st_size
            except Exception:
                fsize = 0
            return {
                "id": video_id,
                "bytes": fsize,
                "filename": asset.filename,
                "media_type": media_type,
                "purpose": "vision",
            }

        @self._app.delete(
            f"{API_PREFIX}/files/{{file_id}}",
            summary="Delete a file",
            description="The ID of the file to use for this request.",
            responses={
                200: {"description": "Successful Response."},
                **add_common_error_responses(),
                409: {
                    "model": ServiceError,
                    "description": "File is in use and cannot be deleted.",
                },
            },
            tags=["Files"],
        )
        async def delete_video_file(
            file_id: Annotated[UUID, Path(description="File having 'file_id' to be deleted.")],
        ) -> DeleteFileResponse:
            file_id = str(file_id)
            logger.info("Received delete video file request for %s", file_id)
            asset = self._asset_manager.get_asset(file_id)
            if asset.is_live:
                raise ServiceException(f"No such file {file_id}", "BadParameter", 400)
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                self._async_executor, self._stream_handler.remove_video_file, asset
            )
            await loop.run_in_executor(
                self._async_executor, self._asset_manager.cleanup_asset, file_id
            )

            # Force Garbage Collect for tests
            if _FORCE_GC:
                print("Force Garbage Collect in RTVI Server")
                gc.collect()

            return {"id": file_id, "object": "file", "deleted": True}

        @self._app.get(
            f"{API_PREFIX}/files",
            description="Returns a list of files.",
            summary="Returns list of files",
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
                    title="Only return files with the given purpose.",
                    pattern=r"^[a-zA-Z]*$",
                ),
            ],
        ) -> ListFilesResponse:
            if purpose != "vision":
                return {"data": [], "object": "list"}
            video_file_list = [
                {
                    "id": asset.asset_id,
                    "filename": asset.filename,
                    "purpose": "vision",
                    "bytes": (
                        (await aiofiles.os.stat(asset.path)).st_size
                        if (await aiofiles.os.path.isfile(asset.path))
                        else 0
                    ),
                    "media_type": asset.media_type,
                }
                for asset in self._asset_manager.list_assets()
                if not asset.is_live
            ]
            logger.info(
                "Received list files request. Responding with %d files info", len(video_file_list)
            )
            return {"data": video_file_list, "object": "list"}

        @self._app.get(
            f"{API_PREFIX}/files/{{file_id}}",
            summary="Returns information about a specific file",
            description="Returns information about a specific file.",
            responses={
                200: {"description": "Successful Response."},
                **add_common_error_responses(),
            },
            tags=["Files"],
        )
        async def get_file_info(
            file_id: Annotated[
                UUID, Path(description="The ID of the file to use for this request.")
            ],
        ) -> FileInfo:
            file_id = str(file_id)
            asset = self._asset_manager.get_asset(file_id)
            if asset.is_live:
                raise ServiceException(f"No such resource {file_id}", "BadParameter", 400)
            try:
                fsize = (await aiofiles.os.stat(asset.path)).st_size
            except Exception:
                fsize = 0
            return {"id": file_id, "bytes": fsize, "filename": asset.filename, "purpose": "vision"}

        @self._app.get(
            f"{API_PREFIX}/files/{{file_id}}/content",
            summary="Returns the contents of the specified file",
            description="Returns the contents of the specified file.",
            responses={
                200: {"description": "Successful Response."},
                **add_common_error_responses(),
            },
            tags=["Files"],
        )
        async def get_file_content(
            file_id: Annotated[
                UUID, Path(description="The ID of the file to use for this request.")
            ],
        ):
            asset = self._asset_manager.get_asset(str(file_id))
            if asset.is_live:
                raise ServiceException(f"No such resource {str(file_id)}", "BadParameter", 400)
            return FileResponse(asset.path)

        # ======================= Files API

        # ======================= Live Stream API
        @self._app.post(
            f"{API_PREFIX}/live-stream",
            summary="Add a live stream",
            description="API for adding live / camera stream.",
            responses={
                200: {"description": "Successful Response."},
                **add_common_error_responses(),
            },
            tags=["Live Stream"],
        )
        async def add_live_stream(query: AddLiveStream) -> AddLiveStreamResponse:
            url = GstRtsp.RTSPUrl()
            result, url = GstRtsp.rtsp_url_parse(query.liveStreamUrl)
            if url and result == GstRtsp.RTSPResult.OK:
                if (url.user is not None) and (url.passwd is not None):
                    if bool(query.username) or bool(query.password):
                        raise ServiceException(
                            "'username' and 'password' should be specified"
                            " in query or url, not both",
                            "InvalidParameters",
                            422,
                        )
                    else:
                        query.username = url.user
                        query.password = url.passwd
                        query.liveStreamUrl = query.liveStreamUrl.replace(
                            "rtsp://" + query.username + ":" + query.password + "@", "rtsp://"
                        )

            logger.info(
                "Received add live stream request: url - %s, description - %s",
                query.liveStreamUrl,
                query.description,
            )
            if bool(query.username) != bool(query.password):
                raise ServiceException(
                    "Either both 'username' and 'password' should be specified"
                    " or neither should be specified",
                    "InvalidParameters",
                    422,
                )
            try:
                # Check if the RTSP URL contains valid video as well as the passed
                # username/password are correct before adding it to the server.
                if _SKIP_INPUT_MEDIA_VERIFICATION:
                    media_info = await MediaFileInfo.get_info_async(
                        query.liveStreamUrl, query.username, query.password
                    )
                    if not media_info.video_codec:
                        raise Exception("Invalid file")

                    # Store media_info for later FPS caching
                    cached_media_info = media_info
                else:
                    cached_media_info = None
            except Exception:
                raise ServiceException(
                    "Could not connect to the RTSP URL or"
                    " there is no video stream from the RTSP URL",
                    "InvalidFile",
                    400,
                )
            video_id = self._asset_manager.add_live_stream(
                url=query.liveStreamUrl,
                description=query.description,
                username=query.username,
                password=query.password,
            )

            # Cache video FPS in the asset if media info was retrieved
            if cached_media_info and hasattr(cached_media_info, "video_fps"):
                asset = self._asset_manager.get_asset(video_id)
                asset.update_video_fps(float(cached_media_info.video_fps))

            return {"id": video_id}

        @self._app.get(
            f"{API_PREFIX}/live-stream",
            summary="List all live streams",
            description="List all live streams.",
            responses={
                200: {"description": "Successful Response."},
                **add_common_error_responses([500]),
            },
            tags=["Live Stream"],
        )
        async def list_live_stream() -> Annotated[list[LiveStreamInfo], Field(max_length=1024)]:
            def get_stream_params(id: str):
                req_info = self._stream_handler._get_live_stream_request(id)
                if not req_info or req_info.status != RequestInfo.Status.PROCESSING:
                    return 0, 0

                # Get parameters from the query object
                if req_info.query:
                    return (
                        req_info.query.chunk_duration,
                        req_info.query.chunk_overlap_duration,
                    )
                return 0, 0

            live_stream_list = [
                {
                    "id": asset.asset_id,
                    "liveStreamUrl": asset.path,
                    "description": asset.description,
                    "chunk_duration": get_stream_params(asset.asset_id)[0],
                    "chunk_overlap_duration": get_stream_params(asset.asset_id)[1],
                }
                for asset in self._asset_manager.list_assets()
                if asset.is_live
            ]
            logger.info(
                "Received list live streams request. Responding with %d live streams info",
                len(live_stream_list),
            )
            return live_stream_list

        @self._app.delete(
            f"{API_PREFIX}/live-stream/{{stream_id}}",
            summary="Remove a live stream",
            description="API for removing live / camera stream matching `stream_id`.",
            responses={
                200: {"description": "Successful Response."},
                **add_common_error_responses(),
            },
            tags=["Live Stream"],
        )
        async def delete_live_stream(
            stream_id: Annotated[
                UUID, Path(description="Unique identifier for the live stream to be deleted.")
            ],
        ):
            stream_id = str(stream_id)
            logger.info("Received delete live stream request for %s", stream_id)

            asset = self._asset_manager.get_asset(stream_id)
            if not asset.is_live:
                raise ServiceException(f"No such live-stream {stream_id}", "InvalidParameter", 400)
            loop = asyncio.get_event_loop()

            # Live stream is being set up, wait for it to be ready
            while asset.use_count > 1:
                await asyncio.sleep(1)

            # Remove RTSP stream from the pipeline if it is being summarized
            await loop.run_in_executor(
                self._async_executor, self._stream_handler.remove_rtsp_stream, asset
            )
            await loop.run_in_executor(
                self._async_executor, self._asset_manager.cleanup_asset, stream_id
            )
            return Response(status_code=200)

        # ======================= Live Stream API

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
                "audio_support": _ENABLE_AUDIO,
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

        @self._app.post(
            f"{API_PREFIX}/generate_captions",
            summary="Generate VLM captions and audio transcripts for a video",
            description="Run video VLM captions and audio transcripts generation query.",
            responses={
                200: {"description": "Successful Response."},
                **add_common_error_responses(),
                503: {
                    "model": ServiceError,
                    "description": (
                        "Server is busy processing another file / live-stream."
                        " Client may try again in some time."
                    ),
                },
            },
            tags=["Captions"],
        )
        async def generate_captions(
            query: VlmQuery, request: Request
        ) -> VlmCaptionsCompletionResponse:

            videoIdListUUID = query.id_list
            videoIdList = [str(uuid_obj) for uuid_obj in videoIdListUUID]
            assetList = []

            if len(videoIdList) > 1:
                for videoId in videoIdList:
                    asset = self._asset_manager.get_asset(videoId)
                    assetList.append(asset)
                    if asset.media_type != "image":
                        raise ServiceException(
                            "Multi-file summarize: Only image files supported."
                            f" {asset._filename} is a not an image",
                            "BadParameters",
                            400,
                        )

            videoId = videoIdList[
                0
            ]  # Note: Other files processed only for multi-image summarize() below
            asset = self._asset_manager.get_asset(videoId)

            logger.info(
                "Received generate_captions query: id=%s, is_live=%s, query=%s",
                ", ".join(videoIdList),
                asset.is_live,
                query.model_dump_json(exclude_none=True),
            )

            # Check if user has specified the model that is initialized
            model_info = self._stream_handler.get_models_info()
            if query.model != model_info.id:
                raise ServiceException(f"No such model '{query.model}'", "BadParameters", 400)

            if query.api_type and query.api_type != model_info.api_type:
                raise ServiceException(
                    f"api_type {query.api_type} not supported by model '{query.model}'",
                    "BadParameters",
                    400,
                )

            # Only streaming output is supported for live streams
            if asset.is_live and not query.stream:
                raise ServiceException(
                    "Only streaming output is supported for live-streams", "BadParameters", 400
                )
            loop = asyncio.get_event_loop()

            if asset.is_live:
                # Check if summarization is already running / already completed.
                existing_request = self._stream_handler._get_live_stream_request(videoId)
                if existing_request:
                    # Reconnect client to existing summarization stream
                    request_id = existing_request.request_id
                    logger.info(
                        "Re-connecting to existing live stream query %s for videoId %s",
                        request_id,
                        videoId,
                    )
                else:
                    # Generate VLM captions (includes stream setup and validation)
                    try:
                        request_id = await loop.run_in_executor(
                            self._async_executor,
                            self._stream_handler.generate_vlm_captions,
                            [asset],  # Pass as list for consistency
                            query,
                            True,  # is_rtsp=True for rtsp stream
                        )
                    except Exception as ex:
                        asset.unlock()
                        raise ex from None
                    logger.info("Created live stream query %s for videoId %s", request_id, videoId)

            else:
                if len(videoIdList) == 1:
                    assetList = [asset]
                # Summarize on a file or multiple files
                request_id = await loop.run_in_executor(
                    self._async_executor,
                    self._stream_handler.generate_vlm_captions,
                    assetList,
                    query,
                    False,  # is_rtsp=False for file
                )
                logger.info("Created video file query %s for videoId %s", request_id, videoId)

            logger.info("Waiting for results of query %s", request_id)

            if query.stream:
                # Allow only a single client for streaming output per live stream
                if time.time() - self._sse_active_clients.get(videoId, 0) < 3:
                    raise ServiceException(
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
                            if request_id not in self._stream_handler._request_info_map:
                                break
                            req_info, resp_list = self._stream_handler.get_response(request_id, 1)
                        except ServiceException:
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
                            media_info = self._build_media_info_dict(req_info.is_live, resp_list[0])

                            if req_info.is_live:
                                dt = datetime.strptime(
                                    resp_list[0].chunk.end_ntp, "%Y-%m-%dT%H:%M:%S.%fZ"
                                ).replace(tzinfo=timezone.utc)
                                current_time = datetime.now(timezone.utc)
                                self._stream_handler.update_live_stream_captions_latency(
                                    (current_time - dt).total_seconds()
                                )

                            # Build chunk responses for VLM captions
                            chunk_responses = [
                                self._build_chunk_response(
                                    resp,
                                    req_info.is_live,
                                    query.enable_audio,
                                )
                                for resp in resp_list
                            ]

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
                            except ServiceException:
                                break

                    # Generate usage data and send as server-sent event if requested
                    if (
                        query.stream_options
                        and query.stream_options.include_usage
                        and request_id in self._stream_handler._request_info_map
                    ):
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
                        except ServiceException:
                            pass
                    yield "[DONE]"
                    self._sse_active_clients.pop(videoId, None)

                return EventSourceResponse(message_generator(), send_timeout=5, ping=1)
            else:
                # Non-streaming output. Wait for request to be completed.
                await loop.run_in_executor(
                    self._async_executor, self._stream_handler.wait_for_request_done, request_id
                )
                req_info, resp_list = self._stream_handler.get_response(request_id)
                if req_info.status == RequestInfo.Status.FAILED:
                    raise ServiceException(
                        "Failed to generate VLM captions", "InternalServerError", 500
                    )

                # Create response json and return it
                return VlmCaptionsCompletionResponse(
                    id=request_id,
                    model=model_info.id,
                    created=int(req_info.queue_time),
                    media_info=MediaInfoOffset(
                        type="offset",
                        start_offset=int(req_info.start_timestamp),
                        end_offset=int(req_info.end_timestamp),
                    ),
                    chunk_responses=(
                        [
                            VlmCaptionResponse(
                                **self._build_chunk_response(
                                    resp,
                                    req_info.is_live,
                                    query.enable_audio,
                                )
                            )
                            for resp in resp_list
                        ]
                        if resp_list
                        else []
                    ),
                    usage=CompletionUsage(
                        total_chunks_processed=req_info.chunk_count,
                        query_processing_time=int(req_info.end_time - req_info.start_time),
                    ),
                )

        # ======================= Summarize API

    def _setup_exception_handlers(self):
        # Handle incorrect request schema (user error)
        @self._app.exception_handler(RequestValidationError)
        async def handle_validation_error(request, ex) -> ServiceError:
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
        @self._app.exception_handler(ServiceException)
        async def handle_rtvi_exception(request, ex: ServiceException) -> ServiceError:
            return JSONResponse(
                status_code=ex.status_code, content={"code": ex.code, "message": ex.message}
            )

        # Handle exceptions and return error details in format specified in the API schema.
        @self._app.exception_handler(HTTPException)
        async def handle_http_exception(request, ex: HTTPException) -> ServiceError:
            return JSONResponse(
                status_code=ex.status_code, content={"code": ex.detail, "message": ex.detail}
            )

        # Unhandled backend errors. Return error details in format specified in the API schema.
        @self._app.exception_handler(Exception)
        async def handle_exception(request, ex: Exception) -> ServiceError:
            return JSONResponse(
                status_code=500,
                content={
                    "code": "InternalServerError",
                    "message": "An internal server error occurred",
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

            openapi_schema["components"]["schemas"]["Body_add_video_file_files_post"][
                "description"
            ] = "Request body schema for adding a file."
            openapi_schema["components"]["schemas"]["Body_add_video_file_files_post"]["properties"][
                "file"
            ]["maxLength"] = 100e9

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
        RTVIStreamHandler.populate_argument_parser(parser)

        parser.add_argument("--host", type=str, help="Address to run server on", default="0.0.0.0")
        parser.add_argument("--port", type=str, help="port to run server on", default="8000")
        parser.add_argument(
            "--log-level",
            type=str,
            choices=["error", "warn", "info", "debug", "perf"],
            default="info",
            help="Application log level",
        )
        parser.add_argument(
            "--max-asset-storage-size",
            type=int,
            help="Maximum size of asset storage directory",
            default=None,
        )

    @staticmethod
    def get_argument_parser():
        parser = argparse.ArgumentParser(
            "RTVI Server", formatter_class=argparse.ArgumentDefaultsHelpFormatter
        )
        RTVIServer.populate_argument_parser(parser)
        return parser


if __name__ == "__main__":

    parser = RTVIServer.get_argument_parser()
    args = parser.parse_args()

    server = RTVIServer(args)
    server.run()
