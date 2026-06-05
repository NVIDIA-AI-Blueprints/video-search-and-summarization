# SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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
"""Implements the RTVI REST API.

Translates between requests/responses and RTVIStreamHandler and AssetManager methods.
"""

from .rtvi_stream_handler import (  # isort:skip
    build_media_info_dict,
    build_media_info_dict_non_streaming,
    convert_pts_to_absolute_timestamp,
    RequestInfo,
    RTVIStreamHandler,
)

import argparse
import asyncio
import functools
import gc
import json
import os
import re
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Annotated, Optional
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

from api_models.captions import VlmQuery
from api_models.common import (
    ANY_CHAR_PATTERN,
    AWS_S3_OBJECT_URL_PATTERN,
    AWS_S3_URL_PATTERN,
    FILE_NAME_PATTERN,
    PATH_PATTERN,
    TIMESTAMP_PATTERN,
    UUID_LENGTH,
    AfterValidator,
    CompletionUsage,
    MetadataResponse,
    ServiceError,
    timestamp_validator,
)
from api_models.embeddings import (
    TextEmbeddingsCompletionResponse,
    TextEmbeddingsQuery,
    TextEmbeddingsResponse,
    VideoEmbeddingsCompletionResponse,
    VideoEmbeddingsQuery,
    VideoEmbeddingsResponse,
)
from api_models.file import (
    AddFileInfoResponse,
    DeleteFileResponse,
    FileInfo,
    ListFilesResponse,
    MediaType,
    Purpose,
)
from api_models.live_stream import (
    AddLiveStreams,
    AddLiveStreamsResponse,
    DeleteLiveStreamsRequest,
    DeleteLiveStreamsResponse,
    LiveStreamInfo,
    StreamAddRequest,
    StreamAddResponse,
    StreamInfo,
    StreamInfoResponse,
    StreamRemoveRequest,
    StreamRemoveResponse,
)
from api_models.models import ListModelsResponse
from api_models.nim_compat import ManifestResponse, VersionResponse
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
_ENABLE_AUDIO = os.environ.get("ENABLE_AUDIO", "false").lower() == "true"


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

VIDEO_EMBEDDINGS_ERROR_MESSAGE = "Failed to generate video embeddings: %s"
TEXT_EMBEDDINGS_ERROR_MESSAGE = "Failed to generate text embeddings: %s"


def _query_log_safe(query: VideoEmbeddingsQuery) -> str:
    """Return a JSON-serialisable string of the query, truncating data: URIs."""
    d = query.model_dump(exclude_none=True)
    url = d.get("url", "")
    if isinstance(url, str) and url.startswith("data:"):
        comma = url.find(",")
        header = url[:comma] if comma != -1 else url[:30]
        payload_len = len(url) - comma - 1 if comma != -1 else 0
        d["url"] = f"{header},<{payload_len} bytes>"
    return json.dumps(d, default=str)


def add_common_error_responses(errors=None):
    if errors is None:
        return COMMON_ERROR_RESPONSES
    return {err: COMMON_ERROR_RESPONSES[err] for err in (errors + [401, 429, 422])}


async def _await_stream_setup_complete(asset, stream_id: str) -> None:
    """Wait for a live-stream's setup to finish before allowing a delete to
    proceed, bounded by ``RTVI_STREAM_DELETE_DRAIN_TIMEOUT_SEC`` (default 30 s).

    During add_live_stream setup ``use_count`` is bumped to 2 and returns to 1
    once setup completes. Without a bound, a stuck setup could hang any delete
    request that arrives during the window. On timeout we log a warning and
    let the caller proceed — the downstream drain + cleanup paths remain
    responsible for correctness if setup never finishes.
    """
    loop = asyncio.get_running_loop()
    timeout_sec = float(os.environ.get("RTVI_STREAM_DELETE_DRAIN_TIMEOUT_SEC", "30"))
    deadline = loop.time() + timeout_sec
    while asset.use_count > 1:
        if loop.time() >= deadline:
            logger.warning(
                f"Timeout waiting for live-stream {stream_id} setup to complete "
                f"(use_count={asset.use_count}, waited {timeout_sec}s); "
                f"proceeding with delete"
            )
            return
        await asyncio.sleep(0.1)


async def _delete_live_streams_batch_impl(
    asset_manager,
    stream_handler,
    executor,
    request: DeleteLiveStreamsRequest,
    cleanup_executor=None,
) -> DeleteLiveStreamsResponse:
    """Core implementation for DELETE /v1/streams/delete-batch.

    Deletes all streams in parallel via asyncio.gather. Each per-stream step
    (the blocking ``remove_rtsp_stream`` and ``cleanup_asset`` calls) runs on
    ``executor`` so the async tasks can overlap. Result ordering mirrors the
    input order because ``asyncio.gather`` preserves positional ordering.

    ``cleanup_executor`` is an optional pool that runs the per-asset
    ``shutil.rmtree`` off the critical path (fire-and-forget). When None,
    rmtree runs inline on ``executor``.

    Factored out of the endpoint closure so unit tests can invoke the logic
    directly without spinning up the full FastAPI/EmbedPipeline/AssetManager stack.
    """
    loop = asyncio.get_running_loop()
    drain_timeout_sec = request.drain_timeout_seconds
    if drain_timeout_sec is None:
        if request.blocking:
            drain_timeout_sec = float(
                os.environ.get("RTVI_STREAM_DELETE_BLOCKING_TIMEOUT_SEC", "300")
            )
        else:
            drain_timeout_sec = float(os.environ.get("RTVI_STREAM_DELETE_DRAIN_TIMEOUT_SEC", "30"))

    async def _delete_one(stream_id_uuid):
        stream_id = str(stream_id_uuid)
        try:
            asset = asset_manager.get_asset(stream_id)
            if not asset.is_live:
                raise ServiceException(f"No such live-stream {stream_id}", "InvalidParameter", 400)

            await _await_stream_setup_complete(asset, stream_id)

            await loop.run_in_executor(
                executor,
                functools.partial(
                    stream_handler.remove_rtsp_stream,
                    asset,
                    drain_timeout_sec=drain_timeout_sec,
                ),
            )
            await loop.run_in_executor(
                executor,
                functools.partial(
                    asset_manager.cleanup_asset, stream_id, executor=cleanup_executor
                ),
            )
            return ("ok", stream_id_uuid)
        except ServiceException as e:
            return ("svc_err", stream_id, e)
        except Exception as e:
            return ("err", stream_id, e)

    results = await asyncio.gather(
        *[_delete_one(sid) for sid in request.stream_ids], return_exceptions=False
    )

    deleted, errors = [], []
    for r in results:
        if r[0] == "ok":
            deleted.append(r[1])
        elif r[0] == "svc_err":
            _, stream_id, e = r
            errors.append(
                {
                    "stream_id": stream_id,
                    "error": e.message,
                    "error_code": e.code,
                    "status_code": e.status_code,
                }
            )
            stream_handler._send_error_message_to_kafka(e.message, stream_id)
            logger.error("Failed to delete live stream %s: %s", stream_id, e.message)
        else:
            _, stream_id, e = r
            errors.append(
                {
                    "stream_id": stream_id,
                    "error": str(e),
                    "error_code": "InternalError",
                    "status_code": 500,
                }
            )
            stream_handler._send_error_message_to_kafka(str(e), stream_id)
            logger.error("Failed to delete live stream %s: %s", stream_id, str(e), exc_info=True)

    logger.info(
        "Batch delete live streams completed: %d succeeded, %d failed",
        len(deleted),
        len(errors),
    )
    return DeleteLiveStreamsResponse(deleted=deleted, errors=errors)


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

        # Dedicated fixed-size pool for fire-and-forget filesystem cleanup
        # (shutil.rmtree of per-asset dirs). Kept small because rmtree is
        # I/O bound and we only need enough parallelism to keep up with
        # concurrent deletes — not one thread per stream.
        self._cleanup_executor = ThreadPoolExecutor(
            max_workers=4, thread_name_prefix="rtvi-cleanup"
        )

        # Use FastAPI to implement the REST API
        openapi_tags = [
            {
                "name": "Embeddings",
                "description": "Operations to generate embeddings for a video.",
            },
            {
                "name": "Files",
                "description": "Files are used to upload and manage media files.",
            },
            {"name": "Health Check", "description": "Operations to check system health."},
            {"name": "Live Stream", "description": "Operations related to live streams."},
            {"name": "Metrics", "description": "Operations to get metrics."},
            {
                "name": "Stream",
                "description": "Stream management endpoints.",
            },
            {
                "name": "Models",
                "description": "List and describe the various models available in the API.",
            },
            {"name": "Metadata", "description": "Operations to get service metadata."},
            {
                "name": "NIM Compatible",
                "description": "NIM-compatible metadata and health endpoints.",
            },
        ]
        openapi_tags.sort(key=lambda x: x["name"])
        self._app = FastAPI(
            contact={"name": "NVIDIA", "url": "https://nvidia.com"},
            description="NVIDIA RTVI Embed Microservice API.",
            title="RTVI Embed Microservice API",
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

        # Initialize OpenTelemetry if enabled (optional)
        try:
            from utils.otel_helper import init_otel

            # Get histogram views from RTVIStreamHandler for proper bucket configuration
            metric_views = RTVIStreamHandler.get_histogram_views()
            init_otel(service_name="rtvi-embed", service_version=VERSION, metric_views=metric_views)
        except Exception as e:
            logger.warning(f"OTEL initialization failed: {e}")

        try:
            # Start the RTVI stream handler
            self._stream_handler = RTVIStreamHandler(self._args, service_name="rtvi-embed")
            self._stream_handler.set_cleanup_executor(self._cleanup_executor)
        except Exception as ex:
            raise ServiceException(
                f"Failed to load RTVI stream handler - {ex!s}",
                "InternalServerError",
                500,
            ) from ex

    def _remove_asset(self, asset: Asset):
        if asset.is_live:
            self._stream_handler.remove_rtsp_stream(asset)
        else:
            self._stream_handler.remove_video_file(asset)
        return True

    def _build_embed_query_from_cv_metadata(self, asset_id, metadata):
        """Build VideoEmbeddingsQuery from CV StreamMetadata for auto-inference."""
        from api_models.embeddings import VideoEmbeddingsQuery

        model_name = metadata.model or self._stream_handler.get_models_info().id
        query_data = {
            "id": [asset_id],
            "model": model_name,
            "stream": True,  # live streams always require streaming output
            "chunk_duration": (
                metadata.chunk_duration if metadata.chunk_duration is not None else 10
            ),
        }
        if metadata.chunk_overlap_duration is not None:
            query_data["chunk_overlap_duration"] = metadata.chunk_overlap_duration
        return VideoEmbeddingsQuery(**query_data)

    def _build_chunk_response(self, resp, is_live: bool, creation_time: str):
        """Build a single chunk response dictionary."""
        if is_live:
            start_time = resp.chunk.start_ntp
            end_time = resp.chunk.end_ntp
        else:

            if creation_time:
                start_time = convert_pts_to_absolute_timestamp(creation_time, resp.chunk.start_pts)
                end_time = convert_pts_to_absolute_timestamp(creation_time, resp.chunk.end_pts)
            else:
                start_time = str(resp.chunk.start_pts / 1e9)
                end_time = str(resp.chunk.end_pts / 1e9)

        chunk_response = {
            "start_time": start_time,
            "end_time": end_time,
            "embeddings": (
                resp.vlm_model_output.embeddings
                if resp.vlm_model_output and resp.vlm_model_output.embeddings
                else []
            ),
        }
        if resp.decode_start_time is not None and resp.decode_end_time is not None:
            chunk_response["decode_latency_ms"] = round(
                (resp.decode_end_time - resp.decode_start_time) * 1000,
                3,
            )
        if resp.vlm_start_time is not None and resp.vlm_end_time is not None:
            chunk_response["inference_latency_ms"] = round(
                (resp.vlm_end_time - resp.vlm_start_time) * 1000,
                3,
            )
        if resp.decode_start_time is not None and resp.vlm_end_time is not None:
            chunk_response["chunk_latency_ms"] = round(
                (resp.vlm_end_time - resp.decode_start_time) * 1000,
                3,
            )
        if resp.queue_time is not None:
            chunk_response["queue_time_s"] = round(resp.queue_time, 3)
        if resp.processing_latency is not None:
            chunk_response["processing_latency_s"] = round(resp.processing_latency, 3)
        if resp.frame_times is not None:
            chunk_response["frame_count"] = len(resp.frame_times)
        return chunk_response

    def _resolve_file_url(self, url: str) -> str:
        """Resolve a ``file://`` URL to a real local path with safety checks.

        The path must resolve inside one of the directories listed in
        ``FILE_URL_ALLOWED_DIRS`` (comma-separated env var), and the file must
        exist.  Uses ``os.path.realpath`` to block ``..`` traversal and symlinks
        that escape the allowlist.

        Raises ``ServiceException`` with 403/400 on violations.
        """
        local_path = os.path.realpath(url[len("file://") :])
        allowed_dirs_env = os.environ.get("FILE_URL_ALLOWED_DIRS", "")
        allowed_dirs = [
            os.path.realpath(d.strip()) for d in allowed_dirs_env.split(",") if d.strip()
        ]
        if not allowed_dirs:
            raise ServiceException(
                "file:// URLs are disabled. Set FILE_URL_ALLOWED_DIRS to enable.",
                "Forbidden",
                403,
            )
        if not any(local_path.startswith(d + os.sep) or local_path == d for d in allowed_dirs):
            raise ServiceException(
                "Access denied: path is not within allowed directories.",
                "Forbidden",
                403,
            )
        if not os.path.isfile(local_path):
            raise ServiceException(
                f"File not found: {local_path}",
                "FileNotFound",
                400,
            )
        return local_path

    def run(self):
        # Configure and start the uvicorn web server
        config = uvicorn.Config(
            self._app, host=self._args.host, port=int(self._args.port), reload=True
        )
        self._server = uvicorn.Server(config)
        self._server.run()
        self._server = None

        self._stream_handler.stop()

        # Drain the fire-and-forget rmtree pool so temp dirs aren't
        # orphaned when the process exits.
        try:
            self._cleanup_executor.shutdown(wait=True)
        except Exception as e:
            logger.warning("Error shutting down cleanup executor: %s", e)

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
            f"{API_PREFIX}/ready",
            summary="Get RTVI Embed Microservice readiness status",
            description="Get RTVI Embed Microservice readiness status.",
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
            f"{API_PREFIX}/live",
            summary="Get RTVI Embed Microservice liveness status",
            description="Get RTVI Embed Microservice liveness status.",
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

        @self._app.get(
            f"{API_PREFIX}/startup",
            summary="Get RTVI Embed Microservice startup status",
            description="Get RTVI Embed Microservice startup status.",
            responses={
                200: {"model": None, "description": "Service is ready to serve requests."},
                **add_common_error_responses([500]),
            },
            tags=["Health Check"],
        )
        async def health_startup_probe():
            return Response(status_code=200, content="Service is ready to serve requests.")

        @self._app.get(
            f"{API_PREFIX}/assets/stats",
            summary="Get asset storage statistics",
            description=(
                "Returns asset counts, oldest asset age, storage limits, and TTL configuration. "
                "Useful for monitoring tmpfs/disk usage and diagnosing age-out behaviour."
            ),
            responses={
                200: {"model": None, "description": "Asset storage statistics."},
                **add_common_error_responses([500]),
            },
            tags=["Health Check"],
        )
        async def asset_stats():
            return JSONResponse(status_code=200, content=self._asset_manager.get_stats())

        # ======================= Metadata API
        @self._app.get(
            f"{API_PREFIX}/metadata",
            summary="Get RTVI Embed Microservice metadata",
            description="Get RTVI Embed Microservice metadata including version information.",
            responses={
                200: {"model": MetadataResponse, "description": "Successful Response."},
                **add_common_error_responses([500]),
            },
            tags=["Metadata"],
        )
        async def get_metadata() -> MetadataResponse:
            return MetadataResponse(version=VERSION)

        @self._app.get(
            f"{API_PREFIX}/version",
            summary="Version",
            description="Get service version information.",
            responses={
                200: {"description": "Successful Response."},
                **add_common_error_responses([500]),
            },
            tags=["NIM Compatible"],
        )
        async def get_version() -> VersionResponse:
            """Get service version."""
            return VersionResponse(
                release=VERSION,
                api="3.1.0",  # OpenAPI version (align with RTVI VLM NIM-compatible API)
            )

        @self._app.get(
            f"{API_PREFIX}/manifest",
            summary="Manifest",
            description="Get service manifest information.",
            responses={
                200: {"description": "Successful Response."},
                **add_common_error_responses([500]),
            },
            tags=["NIM Compatible"],
        )
        async def get_manifest() -> ManifestResponse:
            """Get service manifest."""
            model_info = self._stream_handler.get_models_info()
            return ManifestResponse(
                version=VERSION,
                model=model_info.id,
            )

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
            creation_time: Annotated[
                Optional[str | None],
                Form(
                    description=(
                        "Creation time of the file in ISO8601 format. "
                        "If provided, this offsets the frame times in the response. "
                        "If not provided, the frame times will be relative to the start of the file."
                    ),
                    min_length=24,
                    max_length=24,
                    examples=["2024-06-09T18:32:11.123Z"],
                    pattern=TIMESTAMP_PATTERN,
                ),
                AfterValidator(lambda v, info: timestamp_validator(v, info) if v else None),
            ] = None,
            id: Annotated[
                Optional[UUID],
                Form(
                    description=(
                        "The UUID associated with the file. "
                        "If not provided, a new ID will be generated."
                    ),
                ),
            ] = None,
            sensor_name: Annotated[
                str,
                Form(
                    description="User-defined sensor name for the file.",
                    max_length=256,
                    examples=["camera-001"],
                    pattern=ANY_CHAR_PATTERN,
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
            try:
                if file:
                    if not _FILE_NAME_REGEX.match(file.filename):
                        raise ServiceException(
                            f"filename should match pattern '{FILE_NAME_PATTERN}'",
                            "BadParameters",
                            400,
                        )
                    # File uploaded by user
                    video_id = await self._asset_manager.save_file(
                        file,
                        file.filename,
                        purpose,
                        media_type,
                        creation_time=creation_time,
                        file_id=id,
                        url=None,
                        sensor_name=sensor_name,
                    )
                else:
                    # File added as path
                    video_id = self._asset_manager.add_file(
                        filename,
                        purpose,
                        media_type,
                        reuse_asset=False,
                        creation_time=creation_time,
                        file_id=id,
                        sensor_name=sensor_name,
                    )
            except Exception:
                failed_name = filename or (file.filename if file else "")
                self._stream_handler._send_error_message_to_kafka(
                    f"Failed to add file {failed_name}",
                )
                raise

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
                self._stream_handler._send_error_message_to_kafka(
                    f"File does not seem to be a valid {media_type} file", asset.asset_id
                )
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
                "creation_time": creation_time,
                "sensor_name": asset.sensor_name,
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
                self._stream_handler._send_error_message_to_kafka(
                    f"Cannot delete {file_id}: Asset is a live stream, not a file", file_id
                )
                raise ServiceException(f"No such file {file_id}", "BadParameter", 400)
            loop = asyncio.get_running_loop()
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
                    "creation_time": asset.creation_time,
                    "sensor_name": asset.sensor_name,
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
            try:
                asset = self._asset_manager.get_asset(file_id)
                if asset.is_live:
                    raise ServiceException(f"No such resource {file_id}", "BadParameter", 400)
            except ServiceException as e:
                self._stream_handler._send_error_message_to_kafka(e.message, file_id)
                raise
            try:
                fsize = (await aiofiles.os.stat(asset.path)).st_size
            except Exception:
                fsize = 0
            return {
                "id": file_id,
                "bytes": fsize,
                "filename": asset.filename,
                "purpose": "vision",
                "creation_time": asset.creation_time,
                "sensor_name": asset.sensor_name,
            }

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
            file_id_str = str(file_id)
            try:
                asset = self._asset_manager.get_asset(file_id_str)
                if asset.is_live:
                    raise ServiceException(f"No such resource {file_id_str}", "BadParameter", 400)
            except ServiceException as e:
                self._stream_handler._send_error_message_to_kafka(e.message, file_id_str)
                raise
            return FileResponse(asset.path)

        # ======================= Files API

        # ======================= Live Stream API
        @self._app.post(
            f"{API_PREFIX}/streams/add",
            summary="Add live stream(s)",
            description="API for adding one or more live / camera streams in a single request.",
            responses={
                200: {"description": "Successful Response."},
                **add_common_error_responses(),
            },
            tags=["Live Stream"],
        )
        async def add_live_streams(
            request: AddLiveStreams,
        ) -> AddLiveStreamsResponse:
            results = []
            errors = []

            for idx, query in enumerate(request.streams):
                try:
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
                                    "rtsp://" + query.username + ":" + query.password + "@",
                                    "rtsp://",
                                )

                    if len(request.streams) == 1:
                        logger.info(
                            "Received add live stream request: url - %s, description - %s",
                            query.liveStreamUrl,
                            query.description,
                        )
                    else:
                        logger.info(
                            "Received add live stream request [%d/%d]: url - %s, description - %s",
                            idx + 1,
                            len(request.streams),
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

                    # Check if the RTSP URL contains valid video
                    cached_media_info = None
                    if _SKIP_INPUT_MEDIA_VERIFICATION:
                        try:
                            media_info = await MediaFileInfo.get_info_async(
                                query.liveStreamUrl, query.username, query.password
                            )
                            if not media_info.video_codec:
                                raise Exception("Invalid file")
                            cached_media_info = media_info
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
                        place_name=query.place_name,
                        place_type=query.place_type,
                        place_lat=query.place_lat,
                        place_lon=query.place_lon,
                        place_alt=query.place_alt,
                        place_coordinate_x=query.place_coordinate_x,
                        place_coordinate_y=query.place_coordinate_y,
                        stream_id=query.id,
                        sensor_name=query.sensor_name,
                    )

                    # Cache video FPS in the asset if media info was retrieved
                    if cached_media_info and hasattr(cached_media_info, "video_fps"):
                        asset = self._asset_manager.get_asset(video_id)
                        asset.update_video_fps(float(cached_media_info.video_fps))

                    results.append({"id": video_id})
                except ServiceException as e:
                    errors.append(
                        {
                            "index": idx,
                            "url": query.liveStreamUrl,
                            "error": e.message,
                            "error_code": e.code,
                            "status_code": e.status_code,
                        }
                    )
                    self._stream_handler._send_error_message_to_kafka(
                        f"Failed to add live stream {query.liveStreamUrl}",
                    )
                    logger.error(
                        "Failed to add live stream [%d/%d] %s: %s",
                        idx + 1,
                        len(request.streams),
                        query.liveStreamUrl,
                        e.message,
                    )
                except Exception as e:
                    errors.append(
                        {
                            "index": idx,
                            "url": query.liveStreamUrl,
                            "error": str(e),
                            "error_code": "InternalError",
                            "status_code": 500,
                        }
                    )
                    self._stream_handler._send_error_message_to_kafka(
                        f"Failed to add live stream {query.liveStreamUrl}",
                    )
                    logger.error(
                        "Failed to add live stream [%d/%d] %s: %s",
                        idx + 1,
                        len(request.streams),
                        query.liveStreamUrl,
                        str(e),
                        exc_info=True,
                    )

            if len(request.streams) == 1:
                logger.info(
                    "Add live stream completed: %d succeeded, %d failed",
                    len(results),
                    len(errors),
                )
            else:
                logger.info(
                    "Batch add live streams completed: %d succeeded, %d failed",
                    len(results),
                    len(errors),
                )
            return AddLiveStreamsResponse(results=results, errors=errors)

        @self._app.get(
            f"{API_PREFIX}/streams/get-stream-info",
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
                    "place_name": asset.place_name,
                    "place_type": asset.place_type,
                    "place_lat": asset.place_lat,
                    "place_lon": asset.place_lon,
                    "place_alt": asset.place_alt,
                    "place_coordinate_x": asset.place_coordinate_x,
                    "place_coordinate_y": asset.place_coordinate_y,
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
            f"{API_PREFIX}/streams/delete/{{stream_id}}",
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

            try:
                asset = self._asset_manager.get_asset(stream_id)
                if not asset.is_live:
                    raise ServiceException(
                        f"No such live-stream {stream_id}", "InvalidParameter", 400
                    )
            except ServiceException as e:
                self._stream_handler._send_error_message_to_kafka(e.message, stream_id)
                raise
            loop = asyncio.get_running_loop()

            await _await_stream_setup_complete(asset, stream_id)

            # Remove RTSP stream from the pipeline if it is being summarized
            await loop.run_in_executor(
                self._async_executor, self._stream_handler.remove_rtsp_stream, asset
            )
            await loop.run_in_executor(
                self._async_executor,
                functools.partial(
                    self._asset_manager.cleanup_asset,
                    stream_id,
                    executor=self._cleanup_executor,
                ),
            )
            return Response(status_code=200)

        @self._app.delete(
            f"{API_PREFIX}/streams/delete-batch",
            summary="Remove multiple live streams",
            description="API for removing multiple live / camera streams in a single request.",
            responses={
                200: {"description": "Successful Response."},
                **add_common_error_responses(),
            },
            tags=["Live Stream"],
        )
        async def delete_live_streams_batch(
            request: DeleteLiveStreamsRequest,
        ) -> DeleteLiveStreamsResponse:
            return await _delete_live_streams_batch_impl(
                self._asset_manager,
                self._stream_handler,
                self._async_executor,
                request,
                cleanup_executor=self._cleanup_executor,
            )

        # ======================= Live Stream API

        # ======================= CV-Compatible Stream API

        @self._app.post(
            f"{API_PREFIX}/stream/add",
            summary="Add a video stream",
            description=(
                "Add a video stream. "
                "If metadata contains video embeddings params (model) "
                "video embeddings generation starts automatically."
            ),
            responses={
                200: {"description": "Successful Response."},
                **add_common_error_responses(),
            },
            tags=["Stream"],
        )
        async def cv_stream_add(
            request: StreamAddRequest, http_request: Request
        ) -> StreamAddResponse:
            value = request.value

            if value.change not in ("camera_add", "add"):
                raise ServiceException(
                    f"Unsupported change type: {value.change}. Expected 'camera_add'.",
                    "BadRequest",
                    400,
                )

            logger.info(
                "Received stream/add request: camera_id=%s, url=%s",
                value.camera_id,
                value.camera_url,
            )

            # Add stream via existing asset manager with camera_id tracking
            video_id = self._asset_manager.add_live_stream(
                url=value.camera_url,
                description=value.camera_name or value.camera_id,
                camera_id=value.camera_id,
                sensor_name=value.camera_id,
            )

            logger.info(
                "[AssetManager] Video stream added - camera_id: %s, asset_id: %s",
                value.camera_id,
                video_id,
            )

            inference_started = False

            # If metadata has embed inference params, start embedding generation
            if value.metadata and value.metadata.has_embed_inference_params:
                try:
                    query = self._build_embed_query_from_cv_metadata(video_id, value.metadata)
                    logger.info(
                        "Starting video embeddings generation for camera_id=%s, asset_id=%s",
                        value.camera_id,
                        video_id,
                    )

                    asset = self._asset_manager.get_asset(video_id)

                    # Validate model
                    model_info = self._stream_handler.get_models_info()
                    if query.model != model_info.id:
                        raise ServiceException(
                            f"No such model '{query.model}'", "BadParameters", 400
                        )

                    # Live streams require streaming output
                    vlm_query = VlmQuery(
                        id=query.id,
                        model=query.model,
                        stream=True,
                        chunk_duration=query.chunk_duration,
                        chunk_overlap_duration=query.chunk_overlap_duration,
                        prompt="dummy",
                    )

                    loop = asyncio.get_event_loop()
                    request_id = await loop.run_in_executor(
                        self._async_executor,
                        self._stream_handler.generate_vlm_captions,
                        [asset],
                        vlm_query,
                        True,  # is_rtsp=True
                    )

                    inference_started = True
                    logger.info(
                        "Video embeddings generation started for camera_id=%s, request_id=%s",
                        value.camera_id,
                        request_id,
                    )
                except ServiceException as e:
                    logger.error(
                        "Failed to start video embeddings generation for camera_id=%s: %s",
                        value.camera_id,
                        e.message,
                    )
                    # Stream was added but inference failed — return added status
                except Exception as e:
                    logger.error(
                        "Unexpected error while starting video embeddings generation for camera_id=%s: %s",
                        value.camera_id,
                        str(e),
                        exc_info=True,
                    )

            return StreamAddResponse(
                camera_id=value.camera_id,
                asset_id=video_id,
                status="processing" if inference_started else "added",
                inference=inference_started,
            )

        @self._app.post(
            f"{API_PREFIX}/stream/remove",
            summary="Remove a video stream.",
            description="Remove a video stream by camera_id, stopping video embeddings generation if active.",
            responses={
                200: {"description": "Successful Response."},
                **add_common_error_responses(),
            },
            tags=["Stream"],
        )
        async def cv_stream_remove(request: StreamRemoveRequest) -> StreamRemoveResponse:
            value = request.value
            if value.change not in ("camera_remove", "remove"):
                raise ServiceException(
                    f"Unsupported change type: {value.change}. Expected 'camera_remove'.",
                    "BadRequest",
                    400,
                )

            asset_id = self._asset_manager.get_asset_id_by_camera_id(value.camera_id)
            if not asset_id:
                raise ServiceException(
                    f"No stream found with camera_id: {value.camera_id}",
                    "NotFound",
                    404,
                )

            logger.info(
                "stream/remove request: camera_id=%s, asset_id=%s", value.camera_id, asset_id
            )

            asset = self._asset_manager.get_asset(asset_id)
            loop = asyncio.get_running_loop()

            await _await_stream_setup_complete(asset, asset_id)

            # Remove RTSP stream from the pipeline if it is being processed
            await loop.run_in_executor(
                self._async_executor, self._stream_handler.remove_rtsp_stream, asset
            )
            await loop.run_in_executor(
                self._async_executor,
                functools.partial(
                    self._asset_manager.cleanup_asset,
                    asset_id,
                    executor=self._cleanup_executor,
                ),
            )

            return StreamRemoveResponse(camera_id=value.camera_id, asset_id=asset_id)

        @self._app.get(
            f"{API_PREFIX}/stream/get-stream-info",
            summary="List all streams",
            description=("List all video streams with camera_id" " and inference status."),
            responses={
                200: {"description": "Successful Response."},
                **add_common_error_responses(),
            },
            tags=["Stream"],
        )
        async def cv_get_stream_info() -> StreamInfoResponse:
            stream_list = []
            for asset in self._asset_manager.list_assets():
                if not asset.is_live:
                    continue
                # Check if inference is active
                req_info = self._stream_handler._get_live_stream_request(asset.asset_id)
                inference_active = (
                    req_info is not None and req_info.status == RequestInfo.Status.PROCESSING
                )
                chunk_dur, overlap = 0, 0
                if req_info and req_info.query:
                    chunk_dur = req_info.query.chunk_duration
                    overlap = req_info.query.chunk_overlap_duration

                stream_list.append(
                    StreamInfo(
                        camera_id=asset.camera_id or asset.asset_id,
                        camera_name=asset.description or asset.sensor_name or None,
                        camera_url=asset.path,
                        asset_id=asset.asset_id,
                        inference_active=inference_active,
                        chunk_duration=chunk_dur,
                        chunk_overlap_duration=overlap,
                    )
                )

            logger.info("stream/get-stream-info request: %d streams", len(stream_list))
            return StreamInfoResponse(
                status="ok",
                stream_count=len(stream_list),
                stream_list=stream_list,
            )

        # ======================= CV-Compatible Stream API

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
                "audio_support": False,
            }

        # ======================= Models API

        # ======================= Embeddings API

        @self._app.post(
            f"{API_PREFIX}/generate_text_embeddings",
            summary="Generate embeddings for a given text input.",
            description="Run text embeddings generation query.",
            responses={
                200: {"description": "Successful Response."},
                **add_common_error_responses(),
                503: {
                    "model": ServiceError,
                    "description": (
                        "Server is busy processing another file or text. "
                        "Client may try again in some time."
                    ),
                },
            },
            tags=["Embeddings"],
        )
        async def generate_text_embeddings(
            query: TextEmbeddingsQuery, request: Request
        ) -> TextEmbeddingsCompletionResponse:
            """
            Generate embeddings for a given text input.
            """
            if not query.text_input:
                raise ServiceException(
                    "Text input required for text embeddings.",
                    "BadParameters",
                    400,
                )
            logger.info(
                "Received generate_text_embeddings query. num_texts=%d, model=%s",
                len(query.text_input_list),
                getattr(query, "model", "default"),
            )

            model_info = self._stream_handler.get_models_info()
            if query.model != model_info.id:
                raise ServiceException(f"No such model '{query.model}'", "BadParameters", 400)

            text_input_list = query.text_input_list
            loop = asyncio.get_event_loop()

            # Call the stream handler's text embeddings method if available
            try:

                request_id = None
                # Trigger embedding request
                request_id = await loop.run_in_executor(
                    self._async_executor,
                    self._stream_handler.generate_text_embeddings,
                    query,
                )

                logger.info("Request ID for text embeddings request: %s", request_id)

                # Wait for result
                await loop.run_in_executor(
                    self._async_executor, self._stream_handler.wait_for_request_done, request_id
                )
                req_info, resp_list = self._stream_handler.get_response(request_id)
                if req_info.status == RequestInfo.Status.FAILED:
                    raise ServiceException(
                        "Failed to generate embeddings", "InternalServerError", 500
                    )

                # Calculate latencies for profiling
                e2e_latency = req_info.end_time - req_info.start_time
                total_inference_latency = sum(
                    resp.vlm_end_time - resp.vlm_start_time
                    for resp in resp_list
                    if resp.vlm_end_time > resp.vlm_start_time
                )
                avg_inference_latency = total_inference_latency / len(resp_list) if resp_list else 0

                logger.info(
                    "Generated text embeddings successfully. Request ID: %s, "
                    "e2e_latency=%.3f ms, avg_inference_latency=%.3f ms, num_texts=%d",
                    request_id,
                    e2e_latency * 1000,
                    avg_inference_latency * 1000,
                    len(text_input_list),
                )

                # Validate response count matches input count
                if len(resp_list) != len(text_input_list):
                    raise ServiceException(
                        f"Expected {len(text_input_list)} responses, got {len(resp_list)}",
                        "InternalServerError",
                        500,
                    )

                embeddings_result = [
                    TextEmbeddingsResponse(
                        text_input=text_input,
                        embeddings=resp.vlm_model_output.embeddings,
                    )
                    for text_input, resp in zip(text_input_list, resp_list)
                ]

                response = TextEmbeddingsCompletionResponse(
                    id=request_id,
                    created=int(req_info.start_time),
                    model=model_info.id,
                    data=embeddings_result,
                )

                return response

            except AttributeError:
                raise ServiceException(
                    "Text embeddings not supported by this deployment.",
                    "NotImplemented",
                    501,
                )
            except Exception as e:
                self._stream_handler._send_error_message_to_kafka(
                    TEXT_EMBEDDINGS_ERROR_MESSAGE % str(e),
                    "",
                )
                logger.error(TEXT_EMBEDDINGS_ERROR_MESSAGE, str(e), exc_info=True)
                raise ServiceException(
                    "Failed to generate text embeddings.",
                    "TextEmbeddingsError",
                    500,
                )
            finally:
                if request_id:
                    # Remove the request info from the request info map
                    self._stream_handler._request_info_map.pop(request_id)

        @self._app.post(
            f"{API_PREFIX}/generate_video_embeddings",
            summary="Generate embeddings for a video file, image, "
            "or live stream using specified chunk duration",
            description="Run video embeddings generation query.",
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
            tags=["Embeddings"],
        )
        async def generate_video_embeddings(
            query: VideoEmbeddingsQuery, request: Request
        ) -> VideoEmbeddingsCompletionResponse:

            videoIdListUUID = query.id_list
            videoIdList = [str(uuid_obj) for uuid_obj in videoIdListUUID]

            assetList = []
            videoId = None

            if query.url:
                # Check that a single ID is given as input if url is given.
                if query.id_list and len(query.id_list) > 1:
                    raise ServiceException(
                        "If 'url' is provided, 'id' must be a single UUID, not a list.",
                        "BadParameters",
                        400,
                    )
                if len(query.id_list) == 0:
                    raise ServiceException(
                        "No id provided for the file.",
                        "BadParameters",
                        400,
                    )
                asset_id = str(query.id_list[0])
                # Check if asset exists with same url throw error
                if self._asset_manager.check_asset_exists(asset_id):
                    raise ServiceException(
                        f"Asset with id {asset_id} already exists.",
                        "AssetAlreadyExists",
                        400,
                    )
                if re.match(AWS_S3_URL_PATTERN, query.url) or re.match(
                    AWS_S3_OBJECT_URL_PATTERN, query.url
                ):
                    # File from AWS S3
                    video_id = await self._asset_manager.download_file_from_s3(
                        query.url,
                        "input_file.mp4",
                        "vision",
                        query.media_type,
                        query.creation_time,
                        asset_id,
                    )
                elif query.url and (
                    query.url.startswith("http://") or query.url.startswith("https://")
                ):
                    # file added as url
                    video_id = await self._asset_manager.download_file(
                        query.url,
                        "input_file.mp4",
                        "vision",
                        query.media_type,
                        query.creation_time,
                        asset_id,
                        url_headers=query.url_headers,
                    )
                elif query.url and query.url.startswith("data:"):
                    # RFC 2397 data: URI — inline base64-encoded video/image
                    video_id = await self._asset_manager.save_from_base64(
                        query.url,
                        query.media_type,
                        query.creation_time,
                        asset_id,
                    )
                elif query.url and query.url.startswith("file://"):
                    # Local file path — resolve with allowlist + traversal protection
                    local_path = self._resolve_file_url(query.url)
                    video_id = self._asset_manager.add_file(
                        local_path,
                        "vision",
                        query.media_type,
                        creation_time=query.creation_time,
                        file_id=asset_id,
                    )
                else:
                    raise ServiceException(
                        f"Invalid URL format: {query.url}. "
                        "Must be a valid HTTP/HTTPS, AWS S3, file://, or data: URI.",
                        "InvalidParameters",
                        422,
                    )

                videoIdList = [video_id]

            if len(videoIdList) > 1:
                for videoId in videoIdList:
                    try:
                        asset = self._asset_manager.get_asset(videoId)
                    except ServiceException as e:
                        self._stream_handler._send_error_message_to_kafka(e.message, videoId)
                        raise
                    assetList.append(asset)
                    if asset.media_type != "image":
                        raise ServiceException(
                            "Multi-file generate_video_embeddings: Only image files supported."
                            f" {asset._filename} is a not an image",
                            "BadParameters",
                            400,
                        )

            videoId = videoIdList[
                0
            ]  # Note: Other files processed only for multi-image summarize() below

            try:
                asset = self._asset_manager.get_asset(videoId)
            except ServiceException as e:
                self._stream_handler._send_error_message_to_kafka(e.message, videoId)
                raise

            logger.info(
                "Received generate_video_embeddings query: id=%s, is_live=%s, query=%s",
                ", ".join(videoIdList),
                asset.is_live,
                _query_log_safe(query),
            )

            # Check if user has specified the model that is initialized
            model_info = self._stream_handler.get_models_info()
            if query.model != model_info.id:
                raise ServiceException(f"No such model '{query.model}'", "BadParameters", 400)

            # if query.api_type and query.api_type != model_info.api_type:
            #     raise ServiceException(
            #         f"api_type {query.api_type} not supported by model '{query.model}'",
            #         "BadParameters",
            #         400,
            #     )

            # Only streaming output is supported for live streams
            if asset.is_live and not query.stream:
                raise ServiceException(
                    "Only streaming output is supported for live-streams", "BadParameters", 400
                )
            loop = asyncio.get_event_loop()

            vlm_query = VlmQuery(
                id=query.id,
                model=query.model,
                stream=query.stream,
                stream_options=query.stream_options,
                chunk_duration=query.chunk_duration,
                chunk_overlap_duration=query.chunk_overlap_duration,
                media_info=query.media_info,
                prompt="dummy",
            )

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
                    # Generate Video Embeddings (includes stream setup and validation)
                    try:
                        request_id = await loop.run_in_executor(
                            self._async_executor,
                            self._stream_handler.generate_vlm_captions,
                            [asset],  # Pass as list for consistency
                            vlm_query,
                            True,  # is_rtsp=True for rtsp stream
                        )
                    except Exception as ex:
                        self._stream_handler._send_error_message_to_kafka(
                            VIDEO_EMBEDDINGS_ERROR_MESSAGE % str(ex),
                            videoId,
                        )
                        logger.error(VIDEO_EMBEDDINGS_ERROR_MESSAGE, str(ex), exc_info=True)
                        asset.unlock()
                        raise ex from None
                    logger.info("Created live stream query %s for videoId %s", request_id, videoId)

            else:
                if len(videoIdList) == 1:
                    assetList = [asset]
                # Summarize on a file or multiple files
                try:
                    request_id = await loop.run_in_executor(
                        self._async_executor,
                        self._stream_handler.generate_vlm_captions,
                        assetList,
                        vlm_query,
                        False,  # is_rtsp=False for file
                    )
                except Exception as ex:
                    self._stream_handler._send_error_message_to_kafka(
                        VIDEO_EMBEDDINGS_ERROR_MESSAGE % str(ex),
                        videoId,
                    )
                    logger.error(VIDEO_EMBEDDINGS_ERROR_MESSAGE, str(ex), exc_info=True)
                    raise ex from None
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
                            await asyncio.sleep(0.01)
                            continue

                        # Set the start/end time info for current response.
                        while resp_list:
                            creation_time = (
                                req_info.assets[0].creation_time
                                if req_info.assets and len(req_info.assets) > 0
                                else None
                            )
                            media_info = build_media_info_dict(
                                req_info.is_live, resp_list[0], creation_time
                            )

                            if req_info.is_live:
                                dt = datetime.strptime(
                                    resp_list[0].chunk.end_ntp, "%Y-%m-%dT%H:%M:%S.%fZ"
                                ).replace(tzinfo=timezone.utc)
                                current_time = datetime.now(timezone.utc)
                                self._stream_handler.update_live_stream_captions_latency(
                                    (current_time - dt).total_seconds()
                                )

                            # Build chunk responses for video embeddings
                            chunk_responses = [
                                self._build_chunk_response(resp, req_info.is_live, creation_time)
                                for resp in resp_list
                            ]

                            if query.stream_options and query.stream_options.include_usage:
                                end_time = (
                                    req_info.end_time
                                    if req_info.end_time is not None
                                    else time.time()
                                )
                                usage = {
                                    "total_chunks_processed": req_info.chunk_count,
                                    "query_processing_time": int(end_time - req_info.start_time),
                                }
                            else:
                                usage = None

                            # Create the response json
                            response = {
                                "id": request_id,
                                "model": model_info.id,
                                "created": int(req_info.queue_time),
                                "media_info": media_info,
                                "chunk_responses": chunk_responses,
                                "usage": usage,
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
                            usage = {
                                "total_chunks_processed": req_info.chunk_count,
                                "query_processing_time": int(end_time - req_info.start_time),
                            }
                            response = {
                                "id": request_id,
                                "model": model_info.id,
                                "created": int(req_info.queue_time),
                                "media_info": None,
                                "usage": usage,
                            }
                            yield json.dumps(response)
                        except ServiceException:
                            pass
                    yield "[DONE]"
                    self._sse_active_clients.pop(videoId, None)

                try:
                    return EventSourceResponse(message_generator(), send_timeout=5, ping=1)
                except Exception as ex:
                    self._stream_handler._send_error_message_to_kafka(
                        VIDEO_EMBEDDINGS_ERROR_MESSAGE % str(ex), str(videoId), "functional"
                    )
                    logger.error(VIDEO_EMBEDDINGS_ERROR_MESSAGE, str(ex), exc_info=True)
                    raise ServiceException(
                        "Failed to generate video embeddings.", "InternalServerError", 500
                    ) from ex
                finally:
                    if query.url and videoId is not None:
                        logger.info("Deleting asset %s", videoId)
                        self._asset_manager.cleanup_asset(videoId)
            else:
                try:
                    # Non-streaming output. Wait for request to be completed.
                    await loop.run_in_executor(
                        self._async_executor, self._stream_handler.wait_for_request_done, request_id
                    )
                    req_info, resp_list = self._stream_handler.get_response(request_id)
                    if req_info.status == RequestInfo.Status.FAILED:
                        raise ServiceException(
                            "Failed to generate embeddings", "InternalServerError", 500
                        )
                    creation_time = (
                        req_info.assets[0].creation_time
                        if req_info.assets and len(req_info.assets) > 0
                        else None
                    )

                    media_info = build_media_info_dict_non_streaming(
                        creation_time, req_info.start_timestamp, req_info.end_timestamp
                    )

                    # Create response json and return it
                    return VideoEmbeddingsCompletionResponse(
                        id=request_id,
                        model=model_info.id,
                        created=int(req_info.queue_time),
                        media_info=media_info,
                        chunk_responses=(
                            [
                                VideoEmbeddingsResponse(
                                    **self._build_chunk_response(
                                        resp,
                                        req_info.is_live,
                                        creation_time,
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
                except Exception as e:
                    self._stream_handler._send_error_message_to_kafka(
                        VIDEO_EMBEDDINGS_ERROR_MESSAGE % str(e), videoId, "functional"
                    )
                    logger.error(VIDEO_EMBEDDINGS_ERROR_MESSAGE, str(e), exc_info=True)
                    raise ServiceException(
                        "Failed to generate video embeddings.", "InternalServerError", 500
                    )
                finally:
                    if query.url and video_id is not None:
                        logger.info("Deleting asset %s", video_id)
                        self._asset_manager.cleanup_asset(video_id)

        # ======================= Embeddings API

        # ======================= Stop Live Stream API
        @self._app.delete(
            f"{API_PREFIX}/generate_video_embeddings/{{stream_id}}",
            summary="Stop a live stream from generating video embeddings",
            description="API for stopping a live stream from generating video embeddings"
            " matching `stream_id`.",
            responses={
                200: {"description": "Successful Response."},
                **add_common_error_responses(),
            },
            tags=["Live Stream"],
        )
        async def stop_live_stream(
            stream_id: Annotated[
                UUID,
                Path(
                    description="Unique identifier for the live stream for"
                    " which video embeddings generation is to be stopped."
                ),
            ],
        ):
            stream_id = str(stream_id)
            logger.info(
                "Received stop live stream video embeddings generation request for %s", stream_id
            )

            asset = self._asset_manager.get_asset(stream_id)
            if not asset.is_live:
                self._stream_handler._send_error_message_to_kafka(
                    f"Stop live stream: No such live-stream {stream_id}", stream_id
                )
                raise ServiceException(f"No such live-stream {stream_id}", "InvalidParameter", 400)
            loop = asyncio.get_running_loop()

            await _await_stream_setup_complete(asset, stream_id)

            # Remove RTSP stream from the pipeline if it is being summarized
            await loop.run_in_executor(
                self._async_executor, self._stream_handler.remove_rtsp_stream, asset
            )
            return Response(status_code=200)

        # ======================= Stop Live Stream API

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

            prefix = API_PREFIX.replace("/", "_")
            openapi_schema["components"]["schemas"][f"Body_add_video_file{prefix}_files_post"][
                "description"
            ] = "Request body schema for adding a file."
            openapi_schema["components"]["schemas"][f"Body_add_video_file{prefix}_files_post"][
                "properties"
            ]["file"]["maxLength"] = 100e9

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
