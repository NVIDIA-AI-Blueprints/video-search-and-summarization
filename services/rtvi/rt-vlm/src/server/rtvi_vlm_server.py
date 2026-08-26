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

import argparse
import asyncio
import base64
import functools
import gc
import hashlib
import json
import math
import os
import re
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Annotated, List, Optional, Union
from uuid import UUID, uuid4

import aiofiles
import aiofiles.os
import gi
import uvicorn
from fastapi import FastAPI, File, Form, Path, Query, Request, Response, UploadFile
from fastapi.exceptions import HTTPException, RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from pydantic import Field, ValidationError
from sse_starlette.sse import EventSourceResponse

from api_models.captions import (
    VlmCaptionResponse,
    VlmCaptionsCompletionResponse,
    VlmQuery,
)
from api_models.common import (
    ANY_CHAR_PATTERN,
    FILE_NAME_PATTERN,
    PATH_PATTERN,
    TIMESTAMP_PATTERN,
    URL_PATTERN,
    UUID_LENGTH,
    AfterValidator,
    CompletionUsage,
    MetadataResponse,
    ServiceError,
    timestamp_validator,
)
from api_models.config import (
    CONFIG_ALERT_TYPE,
    CONFIG_CHANGE_TYPE,
    ConfigRequest,
    ConfigResponse,
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
    STREAM_ADD_CHANGE_TYPES,
    STREAM_REMOVE_CHANGE_TYPES,
    VIOS_CAMERA_STATUS_CHANGE_ALERT_TYPE,
    AddLiveStreams,
    AddLiveStreamsResponse,
    DeleteLiveStreamsRequest,
    DeleteLiveStreamsResponse,
    LiveStreamInfo,
    StreamAddInput,
    StreamAddResponse,
    StreamInfo,
    StreamInfoResponse,
    StreamRemoveInput,
    StreamRemoveResponse,
    ViosStreamAddRequest,
    ViosStreamRemoveRequest,
    normalize_stream_add_request,
    normalize_stream_remove_request,
)
from api_models.models import ListModelsResponse
from api_models.nim_compat import (
    ChatCompletionChoice,
    ChatCompletionRequest,
    ChatCompletionResponse,
    ChatCompletionUsage,
    ChatMessage,
    CompletionRequest,
    CompletionResponse,
    ManifestResponse,
    VersionResponse,
)
from common.logger import LOG_PERF_LEVEL, TimeMeasure, logger
from common.service_exception import ServiceException
from common.version import VERSION
from utils.asset_manager import Asset, AssetManager
from utils.media_file_info import MediaFileInfo
from utils.media_io_kwargs import get_frame_sampling_params_from_media_io_kwargs

gi.require_version("GstRtsp", "1.0")  # isort:skip

from gi.repository import GstRtsp  # noqa: E402

API_PREFIX = "/v1"
CHAT_COMPLETION_STREAM_POLL_INTERVAL_SEC = 0.001
GENERATE_CAPTIONS_STREAM_POLL_INTERVAL_SEC = 0.001
DEFAULT_INPUT_MEDIA_VERIFICATION_TIMEOUT_SEC = 300.0
INPUT_MEDIA_VERIFICATION_TIMEOUT_ENV = "VSS_INPUT_MEDIA_VERIFICATION_TIMEOUT_SEC"

# Cache environment variables for performance
_SKIP_INPUT_MEDIA_VERIFICATION = not os.environ.get("VSS_SKIP_INPUT_MEDIA_VERIFICATION", "")
_FORCE_GC = bool(os.environ.get("FORCE_PYTHON_GC"))
_ENABLE_AUDIO = os.environ.get("VLM_MODEL_SUPPORTS_AUDIO", "false").lower() == "true"

VLM_CAPTIONS_ERROR_MESSAGE = "Failed to generate VLM captions: %s"


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

RESOURCE_IN_USE_RESPONSE = {
    "model": ServiceError,
    "description": "Resource is in use and cannot be removed.",
}

# Compile regex patterns at module level for performance
_FILE_NAME_REGEX = re.compile(FILE_NAME_PATTERN)


def _create_vlm_query(query_data: dict) -> VlmQuery:
    try:
        return VlmQuery(**query_data)
    except ValidationError as e:
        raise ServiceException(str(e), "InvalidParameters", 400) from e


def _input_media_verification_timeout_sec() -> float:
    raw_timeout = os.environ.get(INPUT_MEDIA_VERIFICATION_TIMEOUT_ENV, "")
    if not raw_timeout:
        return DEFAULT_INPUT_MEDIA_VERIFICATION_TIMEOUT_SEC

    try:
        timeout_sec = float(raw_timeout)
    except ValueError:
        timeout_sec = 0

    if not math.isfinite(timeout_sec) or timeout_sec <= 0:
        logger.warning(
            "Invalid %s=%r; using default %.0fs",
            INPUT_MEDIA_VERIFICATION_TIMEOUT_ENV,
            raw_timeout,
            DEFAULT_INPUT_MEDIA_VERIFICATION_TIMEOUT_SEC,
        )
        return DEFAULT_INPUT_MEDIA_VERIFICATION_TIMEOUT_SEC
    return timeout_sec


async def _get_media_info_with_timeout(media_path: str, media_label: str):
    timeout_sec = _input_media_verification_timeout_sec()
    try:
        return await asyncio.wait_for(
            MediaFileInfo.get_info_async(media_path),
            timeout=timeout_sec,
        )
    except asyncio.TimeoutError as err:
        raise ServiceException(
            f"Timed out verifying {media_label} after {timeout_sec:g}s",
            "InvalidFile",
            400,
        ) from err


def _build_chat_content_with_think_tags(content: str, reasoning_description: str = "") -> str:
    reasoning_description = reasoning_description.strip()
    if not reasoning_description:
        return content

    reasoning_content = f"<think>\n{reasoning_description}\n</think>"
    if content:
        return f"{reasoning_content}\n\n{content}"
    return reasoning_content


def _build_chat_assistant_message(content: str, reasoning_description: str = "") -> ChatMessage:
    message_kwargs = {
        "role": "assistant",
        "content": _build_chat_content_with_think_tags(content, reasoning_description),
    }
    if reasoning_description:
        message_kwargs["reasoning_description"] = reasoning_description
    return ChatMessage(**message_kwargs)


def _combine_reasoning_descriptions(resp_list) -> str:
    reasoning_descriptions = [
        reasoning_description.strip()
        for resp in resp_list
        if resp.vlm_model_output
        and (reasoning_description := resp.vlm_model_output.reasoning_description)
        and reasoning_description.strip()
    ]
    return "\n".join(reasoning_descriptions)


def add_common_error_responses(errors=None):
    if errors is None:
        return COMMON_ERROR_RESPONSES
    return {err: COMMON_ERROR_RESPONSES[err] for err in (errors + [401, 429, 422])}


async def _await_file_release(asset, file_id: str) -> None:
    """Wait for any in-flight inference holding this file to finish, bounded
    by ``RTVI_STREAM_DELETE_DRAIN_TIMEOUT_SEC`` (default 30 s).

    A client read-timeout on ``/v1/generate_captions`` can fire while the
    server is still processing — ``use_count`` stays > 0 until the work
    drains. Without a bound the benchmark sees an immediate 409 and orphans
    the file. With this wait, brief in-flight work gets a chance to finish;
    on timeout we proceed and let ``cleanup_asset`` surface 409 only if the
    file is genuinely stuck.
    """
    loop = asyncio.get_running_loop()
    timeout_sec = float(os.environ.get("RTVI_STREAM_DELETE_DRAIN_TIMEOUT_SEC", "30"))
    deadline = loop.time() + timeout_sec
    while asset.use_count > 0:
        if loop.time() >= deadline:
            logger.warning(
                f"Timeout waiting for file {file_id} to be released "
                f"(use_count={asset.use_count}, waited {timeout_sec}s); "
                f"proceeding with delete"
            )
            return
        await asyncio.sleep(0.1)


def _raise_if_asset_in_use(asset, asset_id: str) -> None:
    """Reject deleting a *file* asset that still has an in-flight reference.

    Files only. Do not use this on a live stream: there ``use_count`` equals the
    number of active caption requests, so every live stream worth deleting has
    ``use_count >= 1`` and this would reject every delete. Use
    ``_raise_if_live_setup_in_flight`` instead.
    """
    if asset.use_count > 0:
        raise ServiceException(
            f"Resource {asset_id} is currently being used",
            "ResourceInUse",
            409,
        )


def _raise_if_live_setup_in_flight(stream_handler, asset, stream_id: str) -> None:
    """Reject a live-stream delete that lands in the middle of stream setup.

    ``generate_vlm_captions`` takes the asset lock and registers its
    ``RequestInfo`` under the handler lock, so a live asset that is locked but
    has no registered caption request is mid-setup. Deleting in that window
    races the setup and can strand the stream, so reject it.

    Once the request *is* registered the delete is legitimate no matter how
    high ``use_count`` is: ``remove_rtsp_stream`` drains the shared decoder,
    finalizes all caption requests for the asset, and releases their locks
    before ``cleanup_asset`` runs. Gating on ``use_count`` here instead would
    reject every delete of an active stream.
    """
    if asset.use_count > 0 and not stream_handler.has_active_live_caption_request(stream_id):
        raise ServiceException(
            f"Resource {stream_id} is currently being used",
            "ResourceInUse",
            409,
        )


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
    directly without spinning up the full FastAPI/VlmPipeline/AssetManager stack.
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

            _raise_if_live_setup_in_flight(stream_handler, asset, stream_id)

            await loop.run_in_executor(
                executor,
                functools.partial(
                    stream_handler.remove_rtsp_stream,
                    asset,
                    drain_timeout_sec=drain_timeout_sec,
                    abort_inflight=request.blocking,
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
            logger.error("Failed to delete live stream %s: %s", stream_id, str(e), exc_info=True)

    logger.info(
        "Batch delete live streams completed: %d succeeded, %d failed",
        len(deleted),
        len(errors),
    )
    if request.blocking and not errors:
        try:
            released = await loop.run_in_executor(
                executor,
                stream_handler.release_idle_vlm_resources,
            )
            logger.info("Blocking batch delete idle VLM resource release: %s", released)
        except Exception as ex:
            logger.error(
                "Blocking batch delete idle VLM resource release failed: %s",
                ex,
                exc_info=True,
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
        self._temporary_chat_asset_cleanup_tasks = set()

        # Use FastAPI to implement the REST API
        openapi_tags = [
            {
                "name": "Config",
                "description": "Operations to update runtime service configuration.",
            },
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
            {
                "name": "Stream",
                "description": "Stream management endpoints.",
            },
            {"name": "Metrics", "description": "Operations to get metrics."},
            {
                "name": "Models",
                "description": "List and describe the various models available in the API.",
            },
            {"name": "Metadata", "description": "Operations to get service metadata."},
            {
                "name": "NIM Compatible",
                "description": "NIM-compatible OpenAI API endpoints for interoperability.",
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

        # Setup exception handlers BEFORE routes to ensure they catch all exceptions
        self._setup_exception_handlers()
        self._setup_routes()
        self._setup_openapi_schema()

        if logger.level <= LOG_PERF_LEVEL:

            @self._app.middleware("http")
            async def measure_time(request: Request, call_next):
                with TimeMeasure(f"{request.method} {request.url.path}"):
                    response = await call_next(request)
                return response

        self._sse_active_clients = {}

        self._server = None
        from server.rtvi_stream_handler import RTVIStreamHandler

        # Initialize OpenTelemetry if enabled (optional)
        try:
            from utils.otel_helper import init_otel

            # Get histogram views from RTVIStreamHandler for proper bucket configuration
            metric_views = RTVIStreamHandler.get_histogram_views()
            init_otel(service_name="rtvi-vlm", service_version=VERSION, metric_views=metric_views)
        except Exception as e:
            logger.warning(f"OTEL initialization failed: {e}")

        try:
            # Start the RTVI stream handler
            self._stream_handler = RTVIStreamHandler(self._args, service_name="rtvi-vlm")
            self._stream_handler.set_cleanup_executor(self._cleanup_executor)
        except Exception as ex:
            raise ServiceException(
                f"Failed to load RTVI stream handler - {ex!s}",
                "InternalServerError",
                500,
            ) from ex

    async def _handle_text_only_chat(
        self,
        request_body,
        system_prompt: str,
        user_prompt: str,
    ):
        """Handle text-only chat completion (no video/image input).

        Routes through the VLM pipeline with a text-only chunk (no frames).
        Works with all model backends (vllm-compatible, openai-compat).
        """
        from common.chunk_info import ChunkInfo

        model_info = self._stream_handler.get_models_info()

        # Build VlmQuery for the text-only request
        from api_models.captions import ResponseFormat, ResponseType

        response_format = ResponseFormat.model_validate(
            request_body.response_format or {"type": ResponseType.TEXT}
        )

        # Truncate prompt to fit VlmQuery.prompt max_length (5000 chars).
        # For text-only, the actual messages go via chat_messages — prompt is metadata only.
        truncated_prompt = user_prompt[:5000] if len(user_prompt) > 5000 else user_prompt

        vlm_query_dict = {
            "id": uuid4(),
            "prompt": truncated_prompt,
            "system_prompt": system_prompt,
            "model": request_body.model,
            "stream": bool(request_body.stream),
            "response_format": response_format,
            "chunk_duration": 0,
            "chunk_overlap_duration": 0,
            "preserve_reasoning_tags": True,
        }
        if request_body.max_completion_tokens is not None:
            vlm_query_dict["max_tokens"] = request_body.max_completion_tokens
        elif request_body.max_tokens is not None:
            vlm_query_dict["max_tokens"] = request_body.max_tokens
        if request_body.min_tokens is not None:
            vlm_query_dict["min_tokens"] = request_body.min_tokens
        if request_body.temperature is not None:
            vlm_query_dict["temperature"] = request_body.temperature
        if request_body.top_p is not None:
            vlm_query_dict["top_p"] = request_body.top_p
        if request_body.top_k is not None:
            vlm_query_dict["top_k"] = request_body.top_k
        if request_body.seed is not None:
            vlm_query_dict["seed"] = request_body.seed
        if request_body.enable_reasoning:
            vlm_query_dict["enable_reasoning"] = True
        if request_body.ignore_eos is not None:
            vlm_query_dict["ignore_eos"] = request_body.ignore_eos

        vlm_query = _create_vlm_query(vlm_query_dict)

        request_id = str(uuid4())
        created = int(time.time())

        logger.info(
            "Text-only chat completion: request_id=%s, model=%s, messages=%d, stream=%s",
            request_id,
            request_body.model,
            len(request_body.messages),
            request_body.stream,
        )

        # Build structured messages for multi-turn conversation
        chat_messages = []
        if system_prompt:
            chat_messages.append({"role": "system", "content": system_prompt})
        for msg in request_body.messages:
            if msg.role == "system":
                continue  # already handled above
            chat_messages.append({"role": msg.role, "content": msg.get_text_content()})

        # Context window overflow protection: truncate oldest messages if needed.
        max_model_len = int(os.environ.get("VLM_MAX_MODEL_LEN", "32768"))
        max_response_tokens = vlm_query_dict.get("max_tokens", 512)
        max_prompt_tokens = max_model_len - max_response_tokens - 100  # 100 token overhead
        context_warning = None

        def _estimate_tokens(msgs):
            return sum(len(m.get("content", "")) // 4 + 4 for m in msgs)

        estimated_tokens = _estimate_tokens(chat_messages)
        if estimated_tokens > max_prompt_tokens and len(chat_messages) > 2:
            system_msgs = [m for m in chat_messages if m["role"] == "system"]
            non_system_msgs = [m for m in chat_messages if m["role"] != "system"]
            dropped_count = 0

            while (
                _estimate_tokens(system_msgs + non_system_msgs) > max_prompt_tokens
                and len(non_system_msgs) > 1
            ):
                non_system_msgs.pop(0)
                dropped_count += 1

            chat_messages = system_msgs + non_system_msgs
            context_warning = (
                f"Context window exceeded: dropped {dropped_count} oldest message(s) "
                f"to fit within ~{max_prompt_tokens} token limit. "
                f"Reduce conversation history for complete context."
            )
            logger.warning("Text-only chat: %s", context_warning)

        # Create a text-only chunk and enqueue through the VLM pipeline
        text_chunk = ChunkInfo(
            chunk_type="text",
            text_input=user_prompt,
            chunkIdx=0,
        )

        # Use an asyncio Event to wait for the pipeline callback
        result_event = asyncio.Event()
        result_holder = {}
        main_loop = asyncio.get_event_loop()

        def on_chunk_result(chunk_result):
            result_holder["result"] = chunk_result
            # Set event from the main event loop thread (callback runs in pipeline thread)
            main_loop.call_soon_threadsafe(result_event.set)

        try:
            self._stream_handler._vlm_pipeline.enqueue_vlm_text_chunk(
                chunk=text_chunk,
                on_chunk_result=on_chunk_result,
                vlm_query=vlm_query,
                request_id=request_id,
                chat_messages=chat_messages,
            )
        except Exception as ex:
            logger.error("Failed to enqueue text-only chunk: %s", str(ex), exc_info=True)
            raise ServiceException(
                f"Failed to process text-only request: {str(ex)}",
                "InternalServerError",
                500,
            ) from ex

        loop = asyncio.get_running_loop()

        if request_body.stream:
            # True token-level streaming via token stream queue
            import queue as queue_module

            token_queue = self._stream_handler._vlm_pipeline.get_token_stream_queue()
            # Track chunk_id for filtering (concurrent requests share the queue)
            expected_chunk_id = None
            max_streaming_duration = 600  # 10 minutes max

            async def text_only_stream_generator():
                nonlocal expected_chunk_id
                stream_start = time.time()
                try:
                    # Send context truncation warning as first SSE event if applicable
                    if context_warning:
                        warning_response = {
                            "id": request_id,
                            "object": "chat.completion.chunk",
                            "created": created,
                            "model": model_info.id,
                            "choices": [
                                {
                                    "index": 0,
                                    "delta": {"role": "system", "content": context_warning},
                                    "finish_reason": None,
                                }
                            ],
                            "warning": context_warning,
                        }
                        yield json.dumps(warning_response)

                    while True:
                        # Overall streaming timeout
                        if time.time() - stream_start > max_streaming_duration:
                            logger.warning(
                                "Text-only streaming timeout after %d seconds",
                                max_streaming_duration,
                            )
                            yield json.dumps(
                                {"error": {"code": "Timeout", "message": "Streaming timeout"}}
                            )
                            break

                        try:
                            msg = await loop.run_in_executor(
                                None, lambda: token_queue.get(timeout=0.1)
                            )
                        except queue_module.Empty:
                            continue

                        # Filter by chunk_id for concurrent request isolation
                        msg_chunk_id = msg.get("chunk_id")
                        if expected_chunk_id is None and msg_chunk_id is not None:
                            expected_chunk_id = msg_chunk_id
                        if msg_chunk_id is not None and msg_chunk_id != expected_chunk_id:
                            # Message belongs to another request — put it back
                            token_queue.put(msg)
                            continue

                        if msg.get("type") == "error":
                            yield json.dumps(
                                {
                                    "error": {
                                        "code": "InternalServerError",
                                        "message": msg.get("message", "Streaming failed"),
                                    }
                                }
                            )
                            break

                        if msg.get("type") == "done":
                            finish_response = {
                                "id": request_id,
                                "object": "chat.completion.chunk",
                                "created": created,
                                "model": model_info.id,
                                "choices": [
                                    {
                                        "index": 0,
                                        "delta": {},
                                        "finish_reason": "stop",
                                    }
                                ],
                            }
                            yield json.dumps(finish_response)
                            yield "[DONE]"
                            break

                        if msg.get("type") == "token" and msg.get("delta"):
                            chunk_response = {
                                "id": request_id,
                                "object": "chat.completion.chunk",
                                "created": created,
                                "model": model_info.id,
                                "choices": [
                                    {
                                        "index": 0,
                                        "delta": {"content": msg["delta"]},
                                        "finish_reason": None,
                                    }
                                ],
                            }
                            yield json.dumps(chunk_response)
                except Exception as ex:
                    logger.error("Text-only streaming error: %s", str(ex), exc_info=True)
                    yield json.dumps({"error": {"code": "StreamingError", "message": str(ex)}})

            return EventSourceResponse(text_only_stream_generator(), send_timeout=5, ping=1)

        else:
            # Non-streaming: wait for pipeline callback
            try:
                await asyncio.wait_for(result_event.wait(), timeout=300)
            except asyncio.TimeoutError:
                raise ServiceException(
                    "Text-only chat completion timed out after 300 seconds.",
                    "Timeout",
                    504,
                )

            chunk_result = result_holder.get("result")
            content = ""
            reasoning_description = ""
            input_tokens = 0
            output_tokens = 0

            if chunk_result and chunk_result.vlm_model_output:
                content = chunk_result.vlm_model_output.output or ""
                reasoning_description = chunk_result.vlm_model_output.reasoning_description or ""
                input_tokens = chunk_result.vlm_model_output.input_tokens
                output_tokens = chunk_result.vlm_model_output.output_tokens

            choice = ChatCompletionChoice(
                index=0,
                message=_build_chat_assistant_message(content, reasoning_description),
                finish_reason="stop",
            )
            usage = ChatCompletionUsage(
                prompt_tokens=input_tokens,
                completion_tokens=output_tokens,
                total_tokens=input_tokens + output_tokens,
            )
            response = ChatCompletionResponse(
                id=request_id,
                created=created,
                model=model_info.id,
                choices=[choice],
                usage=usage,
            )
            if context_warning:
                return JSONResponse(
                    content=response.model_dump(mode="json"),
                    headers={"X-Warning": context_warning},
                )
            return response

    @staticmethod
    def _data_url_file_extension(header: str, media_type: MediaType) -> str:
        if "image/png" in header:
            return ".png"
        if "image/jpeg" in header or "image/jpg" in header:
            return ".jpg"
        if "image/gif" in header:
            return ".gif"
        if "image/webp" in header:
            return ".webp"
        if "video/mp4" in header:
            return ".mp4"
        if "video/quicktime" in header or "video/mov" in header:
            return ".mov"
        if "video/x-msvideo" in header or "video/avi" in header:
            return ".avi"
        if "video/webm" in header:
            return ".webm"
        if "video/mkv" in header or "video/x-matroska" in header:
            return ".mkv"
        if media_type == MediaType.VIDEO:
            return ".mp4"
        return ".bin"

    def _register_data_url_asset_sync(
        self,
        media_url: str,
        media_type: MediaType,
        file_id: str,
    ) -> tuple[str, int, str]:
        from urllib.parse import unquote

        try:
            header, encoded_data = media_url.split(",", 1)
        except ValueError as e:
            raise ServiceException(
                "Failed to decode base64 data URL: missing comma separator",
                "InvalidDataUrl",
                400,
            ) from e

        file_ext = self._data_url_file_extension(header, media_type)
        if ";base64" in header:
            missing_padding = len(encoded_data) % 4
            if missing_padding:
                encoded_data += "=" * (4 - missing_padding)
            media_data = base64.b64decode(encoded_data)
        else:
            media_data = unquote(encoded_data).encode()

        digest = hashlib.sha256(media_data).hexdigest()
        cache_dir = os.path.join(self._asset_manager._asset_dir, "_data_url_cache")
        os.makedirs(cache_dir, exist_ok=True)
        file_name = f"data_url_{digest[:16]}{file_ext}"
        file_path = os.path.join(cache_dir, f"{digest}{file_ext}")

        if not os.path.exists(file_path):
            tmp_path = os.path.join(cache_dir, f".{digest}.{uuid4()}.tmp")
            try:
                with open(tmp_path, "wb") as f:
                    f.write(media_data)
                os.replace(tmp_path, file_path)
            finally:
                if os.path.exists(tmp_path):
                    try:
                        os.remove(tmp_path)
                    except OSError:
                        pass

        asset = Asset(
            asset_id=file_id,
            path=file_path,
            fileName=file_name,
            purpose=Purpose.VISION.value,
            media_type=media_type.value,
            asset_dir="",
            username="",
            password="",
            description="",
            video_fps=None,
            creation_time=None,
        )

        self._asset_manager._asset_map[file_id] = asset
        return file_id, len(media_data), file_path

    async def _register_data_url_asset(
        self,
        media_url: str,
        media_type: MediaType,
        file_id: str,
    ) -> tuple[str, int, str]:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            self._async_executor,
            functools.partial(
                self._register_data_url_asset_sync,
                media_url,
                media_type,
                file_id,
            ),
        )

    def _register_file_url_asset(
        self,
        media_url: str,
        media_type: MediaType,
        file_id: str,
    ) -> tuple[str, str]:
        from urllib.parse import unquote, urlparse

        parsed_url = urlparse(media_url)
        if parsed_url.scheme != "file" or parsed_url.netloc not in ("", "localhost"):
            raise ServiceException(
                "Only local file URLs with an empty or localhost authority are supported",
                "InvalidFileUrl",
                400,
            )

        file_path = os.path.abspath(unquote(parsed_url.path))
        allowed_paths = os.getenv(
            "RTVI_ALLOWED_LOCAL_MEDIA_PATHS",
            "/opt/nvidia/rtvi/streams/perf",
        )
        allowed_roots = [
            os.path.abspath(os.path.expanduser(path.strip()))
            for path in allowed_paths.split(os.pathsep)
            if path.strip()
        ]

        if allowed_roots:
            is_allowed = False
            for allowed_root in allowed_roots:
                try:
                    if os.path.commonpath([allowed_root, file_path]) == allowed_root:
                        is_allowed = True
                        break
                except ValueError:
                    continue
            if not is_allowed:
                raise ServiceException(
                    f"Local media path is not allowed: {file_path}",
                    "InvalidFileUrl",
                    400,
                )

        asset_id = self._asset_manager.add_file(
            file_path=file_path,
            purpose=Purpose.VISION.value,
            media_type=media_type.value,
            creation_time=None,
            file_id=file_id,
        )
        return asset_id, file_path

    async def _cleanup_temporary_chat_assets(self, temp_asset_ids: list[str]) -> None:
        if not temp_asset_ids:
            return

        loop = asyncio.get_running_loop()
        cleaned_asset_ids = set()
        for temp_asset_id in temp_asset_ids:
            if temp_asset_id in cleaned_asset_ids:
                continue
            cleaned_asset_ids.add(temp_asset_id)

            try:
                asset = self._asset_manager.get_asset(temp_asset_id)
                await _await_file_release(asset, temp_asset_id)
            except ServiceException as e:
                logger.warning(
                    "Failed to inspect temporary chat asset %s before cleanup: %s",
                    temp_asset_id,
                    e.message,
                )
                continue
            except Exception as e:
                logger.warning(
                    "Unexpected error inspecting temporary chat asset %s before cleanup: %s",
                    temp_asset_id,
                    e,
                    exc_info=True,
                )
                continue

            try:
                await loop.run_in_executor(
                    self._async_executor,
                    self._stream_handler.remove_video_file,
                    asset,
                )
            except Exception as e:
                logger.warning(
                    "Failed to deregister temporary chat asset %s from VLM handler: %s",
                    temp_asset_id,
                    e,
                    exc_info=True,
                )

            try:
                await loop.run_in_executor(
                    self._async_executor,
                    functools.partial(
                        self._asset_manager.cleanup_asset,
                        temp_asset_id,
                        executor=self._cleanup_executor,
                    ),
                )
            except ServiceException as e:
                logger.warning(
                    "Failed to clean temporary chat asset %s: %s",
                    temp_asset_id,
                    e.message,
                )
            except Exception as e:
                logger.warning(
                    "Unexpected error cleaning temporary chat asset %s: %s",
                    temp_asset_id,
                    e,
                    exc_info=True,
                )

    def _track_temporary_chat_asset_cleanup_task(self, task: asyncio.Task) -> None:
        self._temporary_chat_asset_cleanup_tasks.add(task)

        def _cleanup_done(completed_task: asyncio.Task) -> None:
            self._temporary_chat_asset_cleanup_tasks.discard(completed_task)
            try:
                completed_task.result()
            except asyncio.CancelledError:
                logger.warning("Temporary chat asset cleanup task was cancelled")
            except Exception as e:
                logger.warning(
                    "Unexpected error in temporary chat asset cleanup task: %s",
                    e,
                    exc_info=True,
                )

        task.add_done_callback(_cleanup_done)

    async def _cleanup_temporary_chat_assets_after_stream_close(
        self, temp_asset_ids: list[str]
    ) -> None:
        if not temp_asset_ids:
            return

        cleanup_task = asyncio.create_task(self._cleanup_temporary_chat_assets(temp_asset_ids))
        self._track_temporary_chat_asset_cleanup_task(cleanup_task)
        try:
            await asyncio.shield(cleanup_task)
        except asyncio.CancelledError:
            logger.info(
                "Temporary chat asset cleanup will continue after stream cancellation: %s",
                ",".join(temp_asset_ids),
            )
            raise

    def _build_vlm_query_from_cv_metadata(self, asset_id, metadata, is_live=True):
        """Build VlmQuery from CV StreamMetadata for auto-inference."""

        if not metadata.prompt:
            raise ValueError("metadata.prompt is required for VLM inference")

        model_name = metadata.model or self._stream_handler.get_models_info().id
        query_data = {
            "id": [asset_id],
            "prompt": metadata.prompt,
            "model": model_name,
            "stream": is_live,
            "chunk_duration": (
                metadata.chunk_duration if metadata.chunk_duration is not None else 10
            ),
        }
        # Add optional fields only if explicitly set
        optional_fields = [
            "system_prompt",
            "max_tokens",
            "min_tokens",
            "temperature",
            "top_p",
            "top_k",
            "ignore_eos",
            "seed",
            "chunk_overlap_duration",
            "num_frames_per_second_or_fixed_frames_chunk",
            "use_fps_for_chunking",
            "vlm_input_width",
            "vlm_input_height",
            "enable_reasoning",
            "enable_audio",
            "alert_category",
            "mm_processor_kwargs",
        ]
        for field in optional_fields:
            val = getattr(metadata, field, None)
            if val is not None:
                query_data[field] = val
        if metadata.response_format_type:
            query_data["response_format"] = {"type": metadata.response_format_type}
        return _create_vlm_query(query_data)

    def _remove_asset(self, asset: Asset):
        if asset.is_live:
            self._stream_handler.remove_rtsp_stream(asset)
        else:
            self._stream_handler.remove_video_file(asset)
        return True

    def _resolve_file_url(self, url: str) -> str:
        """Resolve a ``file://`` URL to a real local path with safety checks.

        Enforces the same path-traversal protections used in
        ``_process_vlm_request``: the path must resolve inside one of the
        directories listed in ``FILE_URL_ALLOWED_DIRS`` (comma-separated),
        and the file must exist.

        Raises ``ServiceException`` with a 403/400 status on violations.
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

    @staticmethod
    def _is_vios_file_sensor(
        request: StreamAddInput, camera_url: str, camera_type: Optional[str] = None
    ) -> bool:
        """Return whether this VIOS stream event represents a file sensor."""
        return isinstance(request, ViosStreamAddRequest) and (
            camera_type == "file"
            or camera_url.startswith("file://")
            or (camera_type != "rtsp" and camera_url.startswith(("http://", "https://")))
        )

    def _resolve_vios_file_sensor_path(self, camera_url: str) -> str:
        """Resolve a VIOS file sensor URL to the local path expected by file APIs."""
        from urllib.parse import unquote

        decoded_url = "file://" + unquote(camera_url[len("file://") :])
        return self._resolve_file_url(decoded_url)

    async def _add_vios_file_sensor_asset(self, value, url_headers: Optional[dict] = None) -> str:
        """Register or download a VIOS file sensor and return the asset id."""
        if value.camera_url.startswith("file://"):
            file_path = self._resolve_vios_file_sensor_path(value.camera_url)
            return self._asset_manager.add_file(
                file_path=file_path,
                purpose="vision",
                media_type="video",
                creation_time=value.creation_time,
                sensor_name=value.camera_id,
                camera_id=value.camera_id,
            )

        if re.match(r"^https?://", value.camera_url):
            from urllib.parse import urlparse

            parsed = urlparse(value.camera_url)
            file_name = os.path.basename(parsed.path) or "vios_file.mp4"
            return await self._asset_manager.download_file(
                url=value.camera_url,
                file_name=file_name,
                purpose="vision",
                media_type="video",
                creation_time=value.creation_time,
                file_id=None,
                url_headers=url_headers,
                sensor_name=value.camera_id,
                camera_id=value.camera_id,
            )

        raise ServiceException(
            "VIOS file sensors support file://, http://, or https:// camera_url values.",
            "InvalidParameters",
            422,
        )

    async def _process_vlm_request(
        self,
        vlm_query: VlmQuery,
        video_id_list: List[str],
        log_prefix: str = "VLM",
        is_chat_completion: bool = False,
    ) -> tuple[str, Asset, List[Asset]]:
        """
        Common helper method to process VLM requests (validate, get assets, generate request ID).

        Returns:
            tuple: (request_id, primary_asset, asset_list)
        """
        # --- URL-based file processing (LVS GA v3.2.0) ---
        if vlm_query.url:
            if not vlm_query.id_list or len(vlm_query.id_list) != 1:
                raise ServiceException(
                    "When 'url' is provided, 'id' must be a single UUID.",
                    "BadParameters",
                    400,
                )
            asset_id = str(vlm_query.id_list[0])

            if self._asset_manager.check_asset_exists(asset_id):
                raise ServiceException(
                    f"Asset with id {asset_id} already exists.",
                    "AssetAlreadyExists",
                    400,
                )

            url = vlm_query.url
            media_type = vlm_query.media_type or "video"
            creation_time_val = vlm_query.creation_time

            try:
                if re.match(r"^https?://", url):
                    from urllib.parse import urlparse

                    parsed = urlparse(url)
                    file_name = os.path.basename(parsed.path) or "media_file"
                    video_id_from_url = await self._asset_manager.download_file(
                        url=url,
                        file_name=file_name,
                        purpose="vision",
                        media_type=media_type,
                        creation_time=creation_time_val,
                        file_id=asset_id,
                        url_headers=vlm_query.url_headers,
                    )
                elif re.match(r"^s3://", url):
                    video_id_from_url = await self._asset_manager.download_file_from_s3(
                        url=url,
                        file_name="s3_media_file",
                        purpose="vision",
                        media_type=media_type,
                        creation_time=creation_time_val,
                        file_id=asset_id,
                    )
                elif url.startswith("file://"):
                    local_path = self._resolve_file_url(url)
                    # Register local file as asset via path-based upload
                    video_id_from_url = self._asset_manager.add_file(
                        file_path=local_path,
                        purpose="vision",
                        media_type=media_type,
                        creation_time=creation_time_val,
                        file_id=asset_id,
                    )
                else:
                    raise ServiceException(
                        f"Unsupported URL scheme: {url}",
                        "BadParameters",
                        400,
                    )
                logger.info(
                    "URL asset created: id=%s, url=%s",
                    video_id_from_url,
                    url[:100],
                )
                video_id_list = [video_id_from_url]
            except ServiceException:
                raise
            except Exception as e:
                logger.error("Failed to process URL %s: %s", url[:100], str(e), exc_info=True)
                raise ServiceException(
                    f"Failed to download/register URL: {str(e)}",
                    "DownloadFailed",
                    400,
                ) from e

        asset_list = []

        # Validate multi-file support (only images supported)
        if len(video_id_list) > 1:
            for video_id in video_id_list:
                try:
                    asset = self._asset_manager.get_asset(video_id)
                except ServiceException:
                    # Re-raise ServiceException as-is (will be handled by exception handler)
                    raise
                except Exception as ex:
                    # Wrap unexpected exceptions
                    raise ServiceException(
                        f"Failed to get asset {video_id}: {str(ex)}", "InternalServerError", 500
                    ) from ex
                asset_list.append(asset)
                if asset.media_type != "image":
                    raise ServiceException(
                        "Multi-file summarize: Only image files supported."
                        f" {asset.filename} is not an image",
                        "BadParameters",
                        400,
                    )

        # Get primary asset
        video_id = video_id_list[0]
        try:
            asset = self._asset_manager.get_asset(video_id)
        except ServiceException:
            # Re-raise ServiceException as-is (will be handled by exception handler)
            raise
        except Exception as ex:
            # Wrap unexpected exceptions
            raise ServiceException(
                f"Failed to get asset {video_id}: {str(ex)}", "InternalServerError", 500
            ) from ex

        # Validate model
        model_info = self._stream_handler.get_models_info()
        if vlm_query.model != model_info.id:
            raise ServiceException(f"No such model '{vlm_query.model}'", "BadParameters", 400)

        # Validate live stream streaming requirement
        if asset.is_live and not vlm_query.stream:
            raise ServiceException(
                "Only streaming output is supported for live-streams", "BadParameters", 400
            )

        loop = asyncio.get_event_loop()

        # Generate request ID
        if asset.is_live:
            # Each live generate request is an independent query, even when it
            # targets a stream that was already added through /streams/add.
            try:
                generate_captions = self._stream_handler.generate_vlm_captions
                if is_chat_completion:
                    generate_captions = functools.partial(
                        generate_captions,
                        is_chat_completion=True,
                    )
                request_id = await loop.run_in_executor(
                    self._async_executor,
                    generate_captions,
                    [asset],  # Pass as list for consistency
                    vlm_query,
                    True,  # is_rtsp=True for rtsp stream
                )
            except Exception as ex:
                self._stream_handler._send_error_message_to_kafka(
                    VLM_CAPTIONS_ERROR_MESSAGE % str(ex),
                    video_id,
                )
                logger.error(VLM_CAPTIONS_ERROR_MESSAGE, str(ex), exc_info=True)
                raise ex from None
            logger.info("Created live stream query %s for videoId %s", request_id, video_id)
        else:
            if len(video_id_list) == 1:
                asset_list = [asset]
            # Summarize on a file or multiple files
            try:
                generate_captions = self._stream_handler.generate_vlm_captions
                if is_chat_completion:
                    generate_captions = functools.partial(
                        generate_captions,
                        is_chat_completion=True,
                    )
                request_id = await loop.run_in_executor(
                    self._async_executor,
                    generate_captions,
                    asset_list,
                    vlm_query,
                    False,  # is_rtsp=False for file
                )
                logger.info("Created video file query %s for videoId %s", request_id, video_id)
            except Exception as ex:
                self._stream_handler._send_error_message_to_kafka(
                    VLM_CAPTIONS_ERROR_MESSAGE % str(ex),
                    video_id,
                )
                logger.error(VLM_CAPTIONS_ERROR_MESSAGE, str(ex), exc_info=True)
                raise ex from None

        logger.info("Waiting for results of query %s", request_id)
        return request_id, asset, asset_list

    def _build_chunk_response(self, resp, is_live: bool, enable_audio: bool, creation_time: str):
        """Build a single chunk response dictionary."""

        if is_live:
            start_time = resp.chunk.start_ntp
            end_time = resp.chunk.end_ntp
        else:
            from server.rtvi_stream_handler import convert_pts_to_absolute_timestamp

            if creation_time:
                start_time = convert_pts_to_absolute_timestamp(creation_time, resp.chunk.start_pts)
                end_time = convert_pts_to_absolute_timestamp(creation_time, resp.chunk.end_pts)
            else:
                start_time = str(resp.chunk.start_pts / 1e9)
                end_time = str(resp.chunk.end_pts / 1e9)

        chunk_response = {
            "chunk_id": resp.chunk.chunkIdx if resp.chunk else 0,
            "start_time": start_time,
            "end_time": end_time,
            "content": resp.vlm_model_output.output if resp.vlm_model_output else "",
        }
        if resp.decode_start_time and resp.decode_end_time:
            chunk_response["decode_latency_ms"] = round(
                (resp.decode_end_time - resp.decode_start_time) * 1000,
                3,
            )
        if resp.vlm_start_time and resp.vlm_end_time:
            chunk_response["vlm_latency_ms"] = round(
                (resp.vlm_end_time - resp.vlm_start_time) * 1000,
                3,
            )
        if resp.decode_start_time and resp.vlm_end_time:
            chunk_response["chunk_latency_ms"] = round(
                (resp.vlm_end_time - resp.decode_start_time) * 1000,
                3,
            )
        if resp.queue_time:
            chunk_response["queue_time_s"] = round(resp.queue_time, 3)
        if resp.processing_latency:
            chunk_response["processing_latency_s"] = round(resp.processing_latency, 3)
        if resp.frame_times:
            chunk_response["frame_count"] = len(resp.frame_times)
        if resp.vlm_model_output:
            chunk_response["input_tokens"] = resp.vlm_model_output.input_tokens
            chunk_response["output_tokens"] = resp.vlm_model_output.output_tokens
        # Add reasoning description if available
        if resp.vlm_model_output and resp.vlm_model_output.reasoning_description:
            chunk_response["reasoning_description"] = resp.vlm_model_output.reasoning_description
        if enable_audio and resp.audio_transcript and resp.audio_transcript.strip():
            chunk_response["audio_transcript"] = resp.audio_transcript.strip()
        return chunk_response

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
            # Ensure content is always a string (handle None case)
            if content is None:
                content = "# Metrics not available\n"
            return Response(content=content, media_type="text/plain")

        # ======================= Health check API
        @self._app.get(
            f"{API_PREFIX}/ready",
            summary="Get RTVI VLM Microservice readiness status",
            description="Get RTVI VLM Microservice readiness status.",
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
            summary="Get RTVI VLM Microservice liveness status",
            description="Get RTVI VLM Microservice liveness status.",
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
            summary="Get RTVI VLM Microservice startup status",
            description="Get RTVI VLM Microservice startup status.",
            responses={
                200: {"model": None, "description": "Successful Response."},
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
                "A null max_storage_usage_gb means no storage cap is configured; "
                "a null max_asset_age_hours means TTL eviction is disabled. "
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
            summary="Get RTVI VLM Microservice metadata",
            description="Get RTVI VLM Microservice metadata including version information.",
            responses={
                200: {"model": MetadataResponse, "description": "Successful Response."},
                **add_common_error_responses([500]),
            },
            tags=["Metadata"],
        )
        async def get_metadata() -> MetadataResponse:
            return MetadataResponse(version=VERSION)

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
            url: Annotated[
                Optional[str],
                Form(
                    description=(
                        "URL of a media file to fetch server-side. "
                        "Supported schemes: http://, https://, s3://, file://. "
                        "Subject to SSRF protection (http/https) and the global "
                        "download size limit."
                    ),
                    max_length=2048,
                    examples=[
                        "https://example.com/video.mp4",
                        "s3://bucket/video.mp4",
                        "file:///opt/data/video.mp4",
                    ],
                    pattern=URL_PATTERN,
                ),
            ] = None,
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
                " media_type %s have file %r, filename - %s, url - %s",
                purpose,
                media_type,
                file,
                filename,
                url[:100] if url else "",
            )

            provided = sum(1 for v in (file, filename, url) if v)
            if provided == 0:
                raise ServiceException(
                    "Exactly one of 'file', 'filename' or 'url' must be specified",
                    "InvalidParameters",
                    422,
                )
            if provided > 1:
                raise ServiceException(
                    "Only one of 'file', 'filename' or 'url' may be specified.",
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
                elif url:
                    # URL — fetch server-side via asset_manager.
                    # Mirrors the dispatch logic in /v1/generate_captions.
                    if re.match(r"^https?://", url):
                        from urllib.parse import urlparse

                        parsed = urlparse(url)
                        url_basename = os.path.basename(parsed.path) or "asset"
                        video_id = await self._asset_manager.download_file(
                            url=url,
                            file_name=url_basename,
                            purpose=purpose,
                            media_type=media_type,
                            creation_time=creation_time,
                            file_id=id,
                        )
                    elif re.match(r"^s3://", url):
                        url_basename = os.path.basename(url) or "asset"
                        video_id = await self._asset_manager.download_file_from_s3(
                            url=url,
                            file_name=url_basename,
                            purpose=purpose,
                            media_type=media_type,
                            creation_time=creation_time,
                            file_id=id,
                        )
                    elif url.startswith("file://"):
                        local_path = self._resolve_file_url(url)
                        video_id = self._asset_manager.add_file(
                            local_path,
                            purpose,
                            media_type,
                            reuse_asset=False,
                            creation_time=creation_time,
                            file_id=id,
                            sensor_name=sensor_name,
                        )
                    else:
                        raise ServiceException(
                            f"Unsupported URL scheme: {url[:100]}. "
                            "Supported: http://, https://, s3://, file://",
                            "InvalidParameters",
                            422,
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
                failed_name = (
                    filename or (url[:100] if url else "") or (file.filename if file else "")
                )
                self._stream_handler._send_error_message_to_kafka(
                    f"Failed to add file {failed_name}",
                )
                raise

            asset = self._asset_manager.get_asset(video_id)
            media_type_value = (
                media_type.value if isinstance(media_type, MediaType) else str(media_type)
            )
            try:
                if _SKIP_INPUT_MEDIA_VERIFICATION:
                    media_info = await _get_media_info_with_timeout(
                        asset.path,
                        f"{media_type_value} file {asset.filename}",
                    )
                    if not media_info.video_codec:
                        raise Exception("Invalid file")
                    if (media_type == "image") != media_info.is_image:
                        raise Exception("Invalid file")

                    # Cache video FPS in the asset
                    if media_type == "video" and hasattr(media_info, "video_fps"):
                        asset.update_video_fps(float(media_info.video_fps))
            except ServiceException as e:
                logger.error(
                    "Failed to verify %s file %s: %s",
                    media_type_value,
                    asset.filename,
                    e.message,
                )
                self._asset_manager.cleanup_asset(video_id)
                self._stream_handler._send_error_message_to_kafka(
                    f"File does not seem to be a valid {media_type_value} file: {e.message}",
                    asset.asset_id,
                )
                raise
            except Exception as e:
                logger.error("".join(traceback.format_exception(e)))
                self._asset_manager.cleanup_asset(video_id)
                self._stream_handler._send_error_message_to_kafka(
                    f"File does not seem to be a valid {media_type_value} file", asset.asset_id
                )
                raise ServiceException(
                    f"File does not seem to be a valid {media_type_value} file",
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
            _raise_if_asset_in_use(asset, file_id)
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
            asset = self._asset_manager.get_asset(file_id)
            if asset.is_live:
                raise ServiceException(f"No such resource {file_id}", "BadParameter", 400)
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
            asset = self._asset_manager.get_asset(str(file_id))
            if asset.is_live:
                raise ServiceException(f"No such resource {str(file_id)}", "BadParameter", 400)
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
            from server.rtvi_stream_handler import RequestInfo

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
                409: RESOURCE_IN_USE_RESPONSE,
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
                self._stream_handler._send_error_message_to_kafka(
                    f"No such live-stream {stream_id}", stream_id
                )
                raise ServiceException(f"No such live-stream {stream_id}", "InvalidParameter", 400)
            loop = asyncio.get_running_loop()

            _raise_if_live_setup_in_flight(self._stream_handler, asset, stream_id)

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

        # ======================= Runtime Config API
        @self._app.post(
            "/api/v1/config",
            include_in_schema=False,
            response_model_exclude_none=True,
        )
        @self._app.post(
            f"{API_PREFIX}/config",
            summary="Update runtime message bus configuration",
            description=(
                "Update generated-message output routing from a VSS config event. "
                "Kafka and Redis connection details remain configured at startup."
            ),
            response_model_exclude_none=True,
            responses={
                200: {"description": "Successful Response."},
                **add_common_error_responses([400, 500]),
            },
            tags=["Config"],
        )
        async def update_runtime_config(request: ConfigRequest) -> ConfigResponse:
            if request.alert_type != CONFIG_ALERT_TYPE:
                raise ServiceException(
                    f"Unsupported alert_type: {request.alert_type}. "
                    f"Expected '{CONFIG_ALERT_TYPE}'.",
                    "BadRequest",
                    400,
                )
            if request.event.change != CONFIG_CHANGE_TYPE:
                raise ServiceException(
                    f"Unsupported change type: {request.event.change}. "
                    f"Expected '{CONFIG_CHANGE_TYPE}'.",
                    "BadRequest",
                    400,
                )

            metadata = request.event.metadata
            result = self._stream_handler.configure_message_bus(
                metadata.messagingbus.value,
                metadata.topic_prefix,
                create_topic=metadata.create_topic,
                topic_partition=metadata.topic_partition,
            )
            error_result = None
            if metadata.errorbus is not None:
                error_result = self._stream_handler.configure_error_bus(
                    metadata.errorbus.value,
                    metadata.error_topic_prefix,
                    create_topic=metadata.create_topic,
                    topic_partition=metadata.topic_partition,
                )
            return ConfigResponse(
                txn_id=request.txn_id,
                status="updated",
                messagingbus=result["messagingbus"],
                topic=result["topic"],
                errorbus=error_result["errorbus"] if error_result else None,
                error_topic=error_result["topic"] if error_result else None,
                source=request.source,
                created_at=request.created_at,
                warnings=result.get("warnings"),
            )

        # ======================= CV-Compatible Stream API
        @self._app.post("/api/v1/camera/add", include_in_schema=False)
        @self._app.put("/api/v1/camera/streaming", include_in_schema=False)
        @self._app.post(f"{API_PREFIX}/camera/add", include_in_schema=False)
        @self._app.put(f"{API_PREFIX}/camera/streaming", include_in_schema=False)
        @self._app.post(
            f"{API_PREFIX}/stream/add",
            summary="Add a video stream ",
            description=(
                "Add a live stream using the RTVI-CV schema. "
                "If metadata contains VLM inference params (prompt), "
                "inference starts automatically. Results delivered via Kafka and optionally SSE."
            ),
            responses={
                200: {"description": "Successful Response."},
                **add_common_error_responses(),
            },
            tags=["Stream"],
        )
        async def cv_stream_add(
            request: StreamAddInput, http_request: Request
        ) -> StreamAddResponse:
            value, _headers = normalize_stream_add_request(request)
            is_vios_request = isinstance(request, ViosStreamAddRequest)

            if is_vios_request and request.alert_type != VIOS_CAMERA_STATUS_CHANGE_ALERT_TYPE:
                raise ServiceException(
                    f"Unsupported alert_type: {request.alert_type}. "
                    f"Expected '{VIOS_CAMERA_STATUS_CHANGE_ALERT_TYPE}'.",
                    "BadRequest",
                    400,
                )

            if value.change not in STREAM_ADD_CHANGE_TYPES:
                raise ServiceException(
                    f"Unsupported change type: {value.change}. "
                    f"Expected one of {STREAM_ADD_CHANGE_TYPES}.",
                    "BadRequest",
                    400,
                )

            if not value.camera_url:
                if is_vios_request and value.change == "camera_add":
                    logger.info(
                        "Received VIOS camera_add registration without stream URL: camera_id=%s",
                        value.camera_id,
                    )
                    return StreamAddResponse(
                        camera_id=value.camera_id,
                        asset_id="",
                        status="added",
                        inference=False,
                    )
                raise ServiceException(
                    "camera_url is required for stream creation.",
                    "BadRequest",
                    400,
                )

            logger.info(
                "Received CV stream/add: camera_id=%s, url=%s",
                value.camera_id,
                value.camera_url,
            )

            is_vios_file_sensor = self._is_vios_file_sensor(
                request, value.camera_url, value.camera_type
            )
            if is_vios_file_sensor:
                video_id = await self._add_vios_file_sensor_asset(
                    value,
                    url_headers=_headers.url_headers if _headers else None,
                )
                logger.info(
                    "[AssetManager] VIOS file sensor added - camera_id: %s, asset_id: %s",
                    value.camera_id,
                    video_id,
                )
            else:
                # Add stream via existing asset manager with camera_id tracking
                video_id = self._asset_manager.add_live_stream(
                    url=value.camera_url,
                    description=value.camera_name or value.camera_id,
                    camera_id=value.camera_id,
                    sensor_name=value.camera_id,
                )

                logger.info(
                    "[AssetManager] CV stream added - camera_id: %s, asset_id: %s",
                    value.camera_id,
                    video_id,
                )

            inference_started = False

            # If metadata has inference params, start VLM processing
            if value.metadata and value.metadata.has_inference_params:
                try:
                    query = self._build_vlm_query_from_cv_metadata(
                        video_id, value.metadata, is_live=not is_vios_file_sensor
                    )
                    logger.info(
                        "Starting VLM inference for CV stream camera_id=%s, asset_id=%s",
                        value.camera_id,
                        video_id,
                    )
                    request_id, asset, assetList = await self._process_vlm_request(
                        query, [video_id], log_prefix="cv_stream_add"
                    )
                    inference_started = True
                    logger.info(
                        "VLM inference started for camera_id=%s, request_id=%s",
                        value.camera_id,
                        request_id,
                    )
                except ServiceException as e:
                    logger.error(
                        "Failed to start inference for camera_id=%s: %s",
                        value.camera_id,
                        e.message,
                    )
                    # Stream was added but inference failed — return added status
                except Exception as e:
                    logger.error(
                        "Unexpected error starting inference for camera_id=%s: %s",
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
            "/api/v1/camera/remove",
            include_in_schema=False,
        )
        @self._app.delete(
            "/api/v1/camera/remove",
            include_in_schema=False,
        )
        @self._app.post(
            f"{API_PREFIX}/camera/remove",
            include_in_schema=False,
        )
        @self._app.delete(
            f"{API_PREFIX}/camera/remove",
            include_in_schema=False,
        )
        @self._app.post(
            f"{API_PREFIX}/stream/remove",
            summary="Remove a video stream ",
            description="Remove a live stream by camera_id, stopping inference if active.",
            responses={
                200: {"description": "Successful Response."},
                **add_common_error_responses(),
                409: RESOURCE_IN_USE_RESPONSE,
            },
            tags=["Stream"],
        )
        async def cv_stream_remove(request: StreamRemoveInput) -> StreamRemoveResponse:
            value, _headers = normalize_stream_remove_request(request)
            is_vios_request = isinstance(request, ViosStreamRemoveRequest)
            if is_vios_request and request.alert_type != VIOS_CAMERA_STATUS_CHANGE_ALERT_TYPE:
                raise ServiceException(
                    f"Unsupported alert_type: {request.alert_type}. "
                    f"Expected '{VIOS_CAMERA_STATUS_CHANGE_ALERT_TYPE}'.",
                    "BadRequest",
                    400,
                )

            if value.change not in STREAM_REMOVE_CHANGE_TYPES:
                raise ServiceException(
                    f"Unsupported change type: {value.change}. "
                    f"Expected one of {STREAM_REMOVE_CHANGE_TYPES}.",
                    "BadRequest",
                    400,
                )

            asset_id = self._asset_manager.get_asset_id_by_camera_id(value.camera_id)
            if not asset_id:
                if is_vios_request:
                    logger.info(
                        "Received VIOS camera_remove for camera without stream asset: camera_id=%s",
                        value.camera_id,
                    )
                    return StreamRemoveResponse(camera_id=value.camera_id, asset_id="")
                raise ServiceException(
                    f"No stream found with camera_id: {value.camera_id}",
                    "NotFound",
                    404,
                )

            logger.info("CV stream/remove: camera_id=%s, asset_id=%s", value.camera_id, asset_id)

            asset = self._asset_manager.get_asset(asset_id)
            loop = asyncio.get_running_loop()

            if asset.is_live:
                _raise_if_live_setup_in_flight(self._stream_handler, asset, asset_id)
            else:
                _raise_if_asset_in_use(asset, asset_id)
                await _await_file_release(asset, asset_id)

            await loop.run_in_executor(self._async_executor, self._remove_asset, asset)
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
            description=("List all live streams with camera_id" " and inference status."),
            responses={
                200: {"description": "Successful Response."},
                **add_common_error_responses(),
            },
            tags=["Stream"],
        )
        async def cv_get_stream_info() -> StreamInfoResponse:
            from server.rtvi_stream_handler import RequestInfo

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

            logger.info("CV stream/get-stream-info: %d streams", len(stream_list))
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
            summary="Generate VLM captions and audio transcripts for a video with alerts",
            description="Run video VLM captions and audio transcripts generation query.",
            response_model_exclude_none=True,
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

            from server.rtvi_stream_handler import RequestInfo

            videoIdListUUID = query.id_list
            videoIdList = [str(uuid_obj) for uuid_obj in videoIdListUUID]

            logger.info(
                "Received generate_captions query: id=%s, query=%s",
                ", ".join(videoIdList),
                query.model_dump_json(exclude_none=True),
            )

            # Validate api_type if specified
            model_info = self._stream_handler.get_models_info()
            if query.api_type and query.api_type != model_info.api_type:
                raise ServiceException(
                    f"api_type {query.api_type} not supported by model '{query.model}'",
                    "BadParameters",
                    400,
                )

            # Use common helper to process VLM request
            # ServiceException from _process_vlm_request will be caught by exception handler
            try:
                request_id, asset, assetList = await self._process_vlm_request(
                    query, videoIdList, log_prefix="generate_captions"
                )
            except ServiceException:
                # Re-raise ServiceException to be handled by FastAPI exception handler
                raise
            except Exception as ex:
                # Wrap unexpected exceptions
                logger.error("Unexpected error in _process_vlm_request: %s", str(ex), exc_info=True)
                raise ServiceException(
                    f"Failed to process VLM request: {str(ex)}", "InternalServerError", 500
                ) from ex

            videoId = videoIdList[0]
            sse_client_key = request_id
            loop = asyncio.get_event_loop()

            if query.stream:
                # Allow only one SSE reader for this request. Multiple requests
                # may independently target the same file or live stream asset.
                if time.time() - self._sse_active_clients.get(sse_client_key, 0) < 3:
                    raise ServiceException(
                        "Another client is already connected to live stream", "Conflict", 409
                    )

                # Server side events generator
                async def message_generator():
                    last_status_report_time = 0
                    last_status = None
                    total_prompt_tokens = 0
                    total_completion_tokens = 0
                    try:
                        while True:
                            self._sse_active_clients[sse_client_key] = time.time()
                            try:
                                if await request.is_disconnected():
                                    logger.info(
                                        "Client %s disconnected for live-stream %s",
                                        request.client.host if request.client else "unknown",
                                        videoId,
                                    )
                                    return
                            except RuntimeError:
                                logger.warning(
                                    "Disconnect polling failed for request %s; closing SSE generator",
                                    request_id,
                                    exc_info=True,
                                )
                                break

                            # Get current response status from the pipeline
                            try:
                                if request_id not in self._stream_handler._request_info_map:
                                    break
                                req_info, resp_list = self._stream_handler.get_response(
                                    request_id, 1
                                )
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
                                await asyncio.sleep(GENERATE_CAPTIONS_STREAM_POLL_INTERVAL_SEC)
                                continue

                            # Set the start/end time info for current response.
                            while resp_list:
                                creation_time = (
                                    req_info.assets[0].creation_time
                                    if req_info.assets and len(req_info.assets) > 0
                                    else None
                                )
                                from server.rtvi_stream_handler import (
                                    build_media_info_dict,
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

                                # Build chunk responses for VLM captions
                                chunk_responses = [
                                    self._build_chunk_response(
                                        resp,
                                        req_info.is_live,
                                        query.enable_audio,
                                        creation_time,
                                    )
                                    for resp in resp_list
                                ]

                                for resp in resp_list:
                                    if resp.vlm_model_output:
                                        total_prompt_tokens += resp.vlm_model_output.input_tokens
                                        total_completion_tokens += (
                                            resp.vlm_model_output.output_tokens
                                        )

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
                    finally:
                        self._sse_active_clients.pop(sse_client_key, None)

                    # Generate usage data and send as server-sent event if requested
                    if (
                        query.stream_options
                        and query.stream_options.include_usage
                        and request_id in self._stream_handler._request_info_map
                    ):
                        try:
                            req_info, _ = self._stream_handler.get_response(request_id, 0)
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
                                    "prompt_tokens": total_prompt_tokens,
                                    "completion_tokens": total_completion_tokens,
                                    "total_tokens": total_prompt_tokens + total_completion_tokens,
                                },
                            }
                            yield json.dumps(response)
                        except ServiceException:
                            pass
                    yield "[DONE]"

                try:
                    return EventSourceResponse(message_generator(), send_timeout=5, ping=1)
                except Exception as ex:
                    self._stream_handler._send_error_message_to_kafka(
                        VLM_CAPTIONS_ERROR_MESSAGE % str(ex),
                        videoId,
                    )
                    logger.error(VLM_CAPTIONS_ERROR_MESSAGE, str(ex), exc_info=True)
                    raise ServiceException(
                        "Failed to generate VLM captions.", "InternalServerError", 500
                    ) from ex
            else:
                # Non-streaming output. Wait for request to be completed.
                try:
                    await loop.run_in_executor(
                        self._async_executor, self._stream_handler.wait_for_request_done, request_id
                    )
                    req_info, resp_list = self._stream_handler.get_response(request_id)
                    if req_info.status == RequestInfo.Status.FAILED:
                        raise ServiceException(
                            req_info.error_message or "Failed to generate VLM captions",
                            (
                                "InternalServerError"
                                if req_info.error_status_code >= 500
                                else "RequestError"
                            ),
                            req_info.error_status_code,
                        )

                    creation_time = (
                        req_info.assets[0].creation_time
                        if req_info.assets and len(req_info.assets) > 0
                        else None
                    )
                    from server.rtvi_stream_handler import (  # isort:skip
                        build_media_info_dict_non_streaming,
                    )

                    media_info = build_media_info_dict_non_streaming(
                        creation_time, req_info.start_timestamp, req_info.end_timestamp
                    )

                    # Create response json and return it
                    return VlmCaptionsCompletionResponse(
                        id=request_id,
                        model=model_info.id,
                        created=int(req_info.queue_time),
                        media_info=media_info,
                        chunk_responses=(
                            [
                                VlmCaptionResponse(
                                    **self._build_chunk_response(
                                        resp,
                                        req_info.is_live,
                                        query.enable_audio,
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
                            prompt_tokens=sum(
                                (r.vlm_model_output.input_tokens or 0)
                                for r in (resp_list or [])
                                if r.vlm_model_output
                            ),
                            completion_tokens=sum(
                                (r.vlm_model_output.output_tokens or 0)
                                for r in (resp_list or [])
                                if r.vlm_model_output
                            ),
                            total_tokens=sum(
                                (r.vlm_model_output.input_tokens or 0)
                                + (r.vlm_model_output.output_tokens or 0)
                                for r in (resp_list or [])
                                if r.vlm_model_output
                            ),
                        ),
                    )
                except ServiceException:
                    raise
                except Exception as ex:
                    self._stream_handler._send_error_message_to_kafka(
                        VLM_CAPTIONS_ERROR_MESSAGE % str(ex),
                        videoId,
                    )
                    logger.error(VLM_CAPTIONS_ERROR_MESSAGE, str(ex), exc_info=True)
                    raise ServiceException(
                        "Failed to generate VLM captions.", "InternalServerError", 500
                    ) from ex

        # ======================= Summarize API
        # ======================= Stop Live Stream VLM API
        @self._app.delete(
            f"{API_PREFIX}/generate_captions/{{stream_id}}",
            summary="Stop a live stream from generating captions and alerts",
            description=(
                "API for stopping a live stream from generating captions and alerts"
                " matching `stream_id`."
            ),
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
                    description="Unique identifier for the live stream for which VLM processing is to be stopped."  # noqa: E501
                ),
            ],
            request_id: Annotated[
                Optional[UUID],
                Query(
                    description=(
                        "Optional caption request ID to stop one subscriber while keeping "
                        "other caption requests for the same stream active."
                    )
                ),
            ] = None,
        ):
            stream_id = str(stream_id)
            logger.info("Received stop live stream VLM request for %s", stream_id)

            asset = self._asset_manager.get_asset(stream_id)
            if not asset.is_live:
                self._stream_handler._send_error_message_to_kafka(
                    f"No such live-stream {stream_id}", stream_id
                )
                raise ServiceException(f"No such live-stream {stream_id}", "InvalidParameter", 400)
            loop = asyncio.get_running_loop()

            # Remove RTSP stream from the pipeline if it is being summarized.
            # With request_id, stop just that live caption subscriber.
            if request_id is not None:
                await loop.run_in_executor(
                    self._async_executor,
                    self._stream_handler.remove_rtsp_stream_request,
                    asset,
                    str(request_id),
                )
            else:
                await loop.run_in_executor(
                    self._async_executor, self._stream_handler.remove_rtsp_stream, asset
                )
            return Response(status_code=200)

        # ======================= Stop Live Stream VLM API
        # ======================= NIM-Compatible APIs
        @self._app.post(
            f"{API_PREFIX}/chat/completions",
            summary="OpenAI-compatible chat endpoint",
            description="OpenAI-compatible chat completion endpoint for VLM processing.",
            response_model=None,
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
            tags=["NIM Compatible"],
        )
        async def chat_completions(
            request_body: ChatCompletionRequest, request: Request
        ) -> Union[ChatCompletionResponse, EventSourceResponse]:
            """Handle OpenAI-compatible chat completion requests.

            Supports two modes:
            1. Pre-uploaded assets: Provide 'id' field with file/stream UUID
            2. Direct URL: Provide video_url/image_url in message content (OpenAI multimodal format)
            """
            from api_models.captions import ResponseFormat, ResponseType
            from server.rtvi_stream_handler import RequestInfo

            # Convert chat completion request to VLM query format
            # Extract system prompt, conversation history, and media URLs from messages
            system_prompt = ""
            system_prompt_count = 0
            conversation_parts = []
            all_image_urls = []
            all_video_urls = []
            has_user_message = False

            for msg in request_body.messages:
                if msg.role == "system":
                    system_prompt = msg.get_text_content()  # Use last system message
                    system_prompt_count += 1
                elif msg.role == "user":
                    has_user_message = True
                    user_content = msg.get_text_content()
                    conversation_parts.append(f"User: {user_content}")
                    # Extract media URLs from multimodal content (only from user messages)
                    image_urls, video_urls = msg.get_media_urls()
                    all_image_urls.extend(image_urls)
                    all_video_urls.extend(video_urls)
                elif msg.role == "assistant":
                    assistant_content = msg.get_text_content()
                    conversation_parts.append(f"Assistant: {assistant_content}")

            if not has_user_message:
                raise ServiceException(
                    "At least one user message is required", "InvalidParameters", 400
                )

            if system_prompt_count > 1:
                logger.warning(
                    "Multiple system messages (%d) in chat request — only the last one is used.",
                    system_prompt_count,
                )

            # Build conversation prompt from all messages (user and assistant)
            # Format: "User: ... Assistant: ... User: ..."
            # This allows the model to understand the conversation context and respond appropriately
            user_prompt = "\n".join(conversation_parts)

            # Track if we created temporary assets from URLs (for cleanup on error)
            temp_asset_ids = []

            # Get asset(s) - either from 'id' field or from video_url/image_url in content
            asset_id_from_url = None

            # If no 'id' provided, try to get asset from video_url or image_url
            if not request_body.id and (all_video_urls or all_image_urls):
                # Download media from URL and create temporary asset
                media_url = all_video_urls[0] if all_video_urls else all_image_urls[0]
                media_type = MediaType.VIDEO if all_video_urls else MediaType.IMAGE

                logger.info(
                    "Processing media for chat completion: %s (type: %s)",
                    media_url[:100] if len(media_url) > 100 else media_url,
                    media_type.value,
                )

                try:
                    # Generate a new file ID for the asset
                    new_file_id = str(uuid4())

                    # Check if this is a base64 data URL
                    if media_url.startswith("data:"):
                        try:
                            asset_id_from_url, media_size, file_path = (
                                await self._register_data_url_asset(
                                    media_url,
                                    media_type,
                                    new_file_id,
                                )
                            )

                            logger.info(
                                "Created temporary asset from data URL: id=%s, type=%s,"
                                " size=%d bytes, path=%s",
                                asset_id_from_url,
                                media_type.value,
                                media_size,
                                file_path,
                            )
                        except ServiceException:
                            raise
                        except Exception as e:
                            logger.error("Failed to decode base64 data URL: %s", str(e))
                            raise ServiceException(
                                f"Failed to decode base64 data URL: {str(e)}",
                                "InvalidDataUrl",
                                400,
                            ) from e
                    elif media_url.startswith("file:"):
                        asset_id_from_url, file_path = self._register_file_url_asset(
                            media_url,
                            media_type,
                            new_file_id,
                        )
                        logger.info(
                            "Created temporary asset from file URL: id=%s, path=%s",
                            asset_id_from_url,
                            file_path,
                        )
                    else:
                        # Regular URL - download the file
                        from urllib.parse import urlparse

                        parsed_url = urlparse(media_url)
                        file_name = os.path.basename(parsed_url.path) or "media_file"

                        # Download the file
                        asset_id_from_url = await self._asset_manager.download_file(
                            url=media_url,
                            file_name=file_name,
                            purpose=Purpose.VISION.value,
                            media_type=media_type.value,
                            creation_time=None,
                            file_id=new_file_id,
                        )
                        logger.info(
                            "Created temporary asset from URL: id=%s, url=%s",
                            asset_id_from_url,
                            media_url[:100],
                        )

                    temp_asset_ids.append(asset_id_from_url)

                except ServiceException:
                    raise
                except Exception as e:
                    logger.error("Failed to process media: %s", str(e))
                    raise ServiceException(
                        f"Failed to process media: {str(e)}",
                        "MediaProcessingFailed",
                        400,
                    ) from e

            # Determine asset ID to use
            if request_body.id:
                asset_uuid = request_body.id
            elif asset_id_from_url:
                asset_uuid = UUID(asset_id_from_url)
            else:
                # Text-only request — no media provided, bypass VLM pipeline
                return await self._handle_text_only_chat(request_body, system_prompt, user_prompt)

            # Convert ID to list format
            videoIdListUUID = [asset_uuid] if isinstance(asset_uuid, UUID) else asset_uuid
            videoIdList = [str(uuid_obj) for uuid_obj in videoIdListUUID]

            logger.info(
                "Received NIM chat completion request: id=%s, model=%s, messages=%d",
                ", ".join(videoIdList),
                request_body.model,
                len(request_body.messages),
            )

            # Check if this is a live stream - live streams require chunk_duration > 0
            is_live_stream = False
            if videoIdList:
                try:
                    asset = self._asset_manager.get_asset(videoIdList[0])
                    is_live_stream = asset.is_live
                except ServiceException:
                    # Asset doesn't exist yet, will be validated later in _process_vlm_request
                    pass

            # Handle response_format
            response_format = ResponseFormat.model_validate(
                request_body.response_format or {"type": ResponseType.TEXT}
            )

            # Convert to VlmQuery format
            # Only include optional fields if they are not None to avoid Pydantic validation errors

            # Set default chunk_duration for live streams if not provided
            # Live streams require chunk_duration > 0 (validated in rtvi_stream_handler)
            chunk_duration = (
                request_body.chunk_duration if request_body.chunk_duration is not None else 0
            )
            if is_live_stream and chunk_duration == 0:
                # Live streams require chunk_duration > 0, use default of 60 seconds
                chunk_duration = 60

            vlm_query_dict = {
                "id": asset_uuid,
                "prompt": user_prompt,
                "system_prompt": system_prompt,
                "model": request_body.model,
                "stream": request_body.stream or False,
                "response_format": response_format,
                "chunk_duration": chunk_duration,
                "chunk_overlap_duration": request_body.chunk_overlap_duration or 0,
                "enable_audio": request_body.enable_audio or False,
                "enable_reasoning": request_body.enable_reasoning or False,
                "preserve_reasoning_tags": True,
                "num_frames_per_second_or_fixed_frames_chunk": (
                    request_body.num_frames_per_second_or_fixed_frames_chunk or 0
                ),
                "use_fps_for_chunking": request_body.use_fps_for_chunking or False,
                "vlm_input_width": request_body.vlm_input_width or 0,
                "vlm_input_height": request_body.vlm_input_height or 0,
            }
            # Add optional fields only if they are not None
            if request_body.ignore_eos is not None:
                vlm_query_dict["ignore_eos"] = request_body.ignore_eos
            if request_body.max_completion_tokens is not None:
                vlm_query_dict["max_tokens"] = request_body.max_completion_tokens
            elif request_body.max_tokens is not None:
                vlm_query_dict["max_tokens"] = request_body.max_tokens
            if request_body.min_tokens is not None:
                vlm_query_dict["min_tokens"] = request_body.min_tokens
            if request_body.temperature is not None:
                vlm_query_dict["temperature"] = request_body.temperature
            if request_body.top_p is not None:
                vlm_query_dict["top_p"] = request_body.top_p
            if request_body.top_k is not None:
                vlm_query_dict["top_k"] = request_body.top_k
            if request_body.seed is not None:
                vlm_query_dict["seed"] = request_body.seed
            if request_body.mm_processor_kwargs is not None:
                vlm_query_dict["mm_processor_kwargs"] = request_body.mm_processor_kwargs
            if request_body.media_io_kwargs is not None:
                vlm_query_dict["media_io_kwargs"] = request_body.media_io_kwargs
                # Map media_io_kwargs to RTVI frame extraction params
                # NIM API: {"video": {"fps": 3.0}} or {"video": {"num_frames": 16}}.
                # num_frames=-1 selects all decoded frames in the chunk.
                # media_io_kwargs overrides num_frames_per_second_or_fixed_frames_chunk
                try:
                    vlm_query_dict.update(
                        get_frame_sampling_params_from_media_io_kwargs(request_body.media_io_kwargs)
                    )
                except (ValueError, TypeError) as e:
                    await self._cleanup_temporary_chat_assets(temp_asset_ids)
                    raise ServiceException(
                        f"Invalid media_io_kwargs.video value: {e}",
                        "InvalidParameters",
                        400,
                    ) from e
            try:
                vlm_query = _create_vlm_query(vlm_query_dict)
            except Exception:
                await self._cleanup_temporary_chat_assets(temp_asset_ids)
                raise

            # Use common helper to process VLM request
            # ServiceException from _process_vlm_request will be caught by exception handler
            try:
                request_id, asset, assetList = await self._process_vlm_request(
                    vlm_query,
                    videoIdList,
                    log_prefix="NIM chat completion",
                    is_chat_completion=True,
                )
            except ServiceException:
                await self._cleanup_temporary_chat_assets(temp_asset_ids)
                # Re-raise ServiceException to be handled by FastAPI exception handler
                raise
            except Exception as ex:
                await self._cleanup_temporary_chat_assets(temp_asset_ids)
                # Wrap unexpected exceptions
                logger.error("Unexpected error in _process_vlm_request: %s", str(ex), exc_info=True)
                raise ServiceException(
                    f"Failed to process VLM request: {str(ex)}", "InternalServerError", 500
                ) from ex

            videoId = videoIdList[0]
            sse_client_key = request_id

            # Get model info for response
            model_info = self._stream_handler.get_models_info()

            # Get event loop for non-streaming path
            loop = asyncio.get_event_loop()

            if vlm_query.stream:
                # Allow only one SSE reader for this request. Multiple requests
                # may independently target the same file or live stream asset.
                if time.time() - self._sse_active_clients.get(sse_client_key, 0) < 3:
                    await self._cleanup_temporary_chat_assets(temp_asset_ids)
                    raise ServiceException(
                        "Another client is already connected to live stream", "Conflict", 409
                    )

                # Server side events generator for OpenAI-compatible streaming
                async def chat_message_generator():
                    last_status_report_time = 0
                    last_status = None
                    final_created = int(time.time())
                    try:
                        while True:
                            self._sse_active_clients[sse_client_key] = time.time()
                            try:
                                if await request.is_disconnected():
                                    logger.info(
                                        "Client %s disconnected for live-stream %s",
                                        request.client.host if request.client else "unknown",
                                        videoId,
                                    )
                                    return
                            except RuntimeError:
                                logger.warning(
                                    "Disconnect polling failed for request %s; closing SSE generator",
                                    request_id,
                                    exc_info=True,
                                )
                                break

                            # Get current response status from the pipeline
                            try:
                                if request_id not in self._stream_handler._request_info_map:
                                    break
                                req_info, resp_list = self._stream_handler.get_response(
                                    request_id, 1
                                )
                                final_created = int(req_info.queue_time)
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
                                        # Send error as OpenAI-compatible format
                                        response = {
                                            "id": str(request_id),
                                            "object": "chat.completion.chunk",
                                            "created": int(req_info.queue_time),
                                            "model": model_info.id,
                                            "choices": [
                                                {
                                                    "index": 0,
                                                    "delta": {},
                                                    "finish_reason": None,
                                                }
                                            ],
                                        }
                                        # EventSourceResponse adds "data: " prefix automatically
                                        yield json.dumps(response)
                                    break
                                await asyncio.sleep(CHAT_COMPLETION_STREAM_POLL_INTERVAL_SEC)
                                continue

                            # Process chunk responses and convert to OpenAI streaming format
                            while resp_list:
                                creation_time = (
                                    req_info.assets[0].creation_time
                                    if req_info.assets and len(req_info.assets) > 0
                                    else None
                                )
                                from server.rtvi_stream_handler import (
                                    build_media_info_dict,
                                )

                                for resp in resp_list:
                                    content = (
                                        resp.vlm_model_output.output
                                        if resp.vlm_model_output
                                        else ""
                                    )
                                    reasoning_description = (
                                        resp.vlm_model_output.reasoning_description
                                        if resp.vlm_model_output
                                        else ""
                                    )
                                    content = _build_chat_content_with_think_tags(
                                        content, reasoning_description
                                    )
                                    delta = {"content": content}
                                    if reasoning_description:
                                        delta["reasoning_description"] = reasoning_description
                                    media_info = build_media_info_dict(
                                        req_info.is_live, resp, creation_time
                                    )
                                    if req_info.is_live:
                                        dt = datetime.strptime(
                                            resp.chunk.end_ntp, "%Y-%m-%dT%H:%M:%S.%fZ"
                                        ).replace(tzinfo=timezone.utc)
                                        current_time = datetime.now(timezone.utc)
                                        self._stream_handler.update_live_stream_captions_latency(
                                            (current_time - dt).total_seconds()
                                        )

                                    # Send as NIM-compatible streaming chunk.
                                    # Extends OpenAI format with reasoning_description.
                                    response = {
                                        "id": str(request_id),
                                        "object": "chat.completion.chunk",
                                        "created": int(req_info.queue_time),
                                        "model": model_info.id,
                                        "chunk_id": resp.chunk.chunkIdx if resp.chunk else 0,
                                        "media_info": media_info,
                                        "choices": [
                                            {
                                                "index": 0,
                                                "delta": delta,
                                                "finish_reason": None,
                                            }
                                        ],
                                    }
                                    # EventSourceResponse adds "data: " prefix automatically
                                    yield json.dumps(response)
                                try:
                                    req_info, resp_list = self._stream_handler.get_response(
                                        request_id, 1
                                    )
                                    final_created = int(req_info.queue_time)
                                except ServiceException:
                                    break
                        # Send final chunk with finish_reason
                        response = {
                            "id": str(request_id),
                            "object": "chat.completion.chunk",
                            "created": final_created,
                            "model": model_info.id,
                            "choices": [
                                {
                                    "index": 0,
                                    "delta": {},
                                    "finish_reason": "stop",
                                }
                            ],
                        }
                        # EventSourceResponse adds "data: " prefix automatically
                        yield json.dumps(response)
                        # Send final done message - EventSourceResponse handles this
                        yield "[DONE]"
                    finally:
                        self._sse_active_clients.pop(sse_client_key, None)
                        await self._cleanup_temporary_chat_assets_after_stream_close(temp_asset_ids)

                try:
                    return EventSourceResponse(chat_message_generator(), send_timeout=5, ping=1)
                except ServiceException:
                    await self._cleanup_temporary_chat_assets(temp_asset_ids)
                    raise
                except Exception as ex:
                    await self._cleanup_temporary_chat_assets(temp_asset_ids)
                    self._stream_handler._send_error_message_to_kafka(
                        VLM_CAPTIONS_ERROR_MESSAGE % str(ex),
                        videoId,
                    )
                    logger.error(VLM_CAPTIONS_ERROR_MESSAGE, str(ex), exc_info=True)
                    raise ServiceException(
                        "Failed to generate chat completion.", "InternalServerError", 500
                    ) from ex
            else:
                # Non-streaming output. Wait for request to be completed.
                try:
                    await loop.run_in_executor(
                        self._async_executor, self._stream_handler.wait_for_request_done, request_id
                    )
                    req_info, resp_list = self._stream_handler.get_response(request_id)
                    if req_info.status == RequestInfo.Status.FAILED:
                        raise ServiceException(
                            req_info.error_message or "Failed to generate VLM captions",
                            (
                                "InternalServerError"
                                if req_info.error_status_code >= 500
                                else "RequestError"
                            ),
                            req_info.error_status_code,
                        )

                    # Convert response to OpenAI format
                    # Combine all chunk responses into a single message
                    combined_content = "\n".join(
                        [
                            resp.vlm_model_output.output if resp.vlm_model_output else ""
                            for resp in resp_list
                        ]
                    )
                    reasoning_description = _combine_reasoning_descriptions(resp_list)

                    choice = ChatCompletionChoice(
                        index=0,
                        message=_build_chat_assistant_message(
                            combined_content, reasoning_description
                        ),
                        finish_reason="stop",
                    )

                    # Aggregate token counts from all VLM model outputs
                    total_output_tokens = sum(
                        resp.vlm_model_output.output_tokens
                        for resp in resp_list
                        if resp.vlm_model_output
                    )

                    # Count text prompt tokens only (excluding vision tokens)
                    # Use the model's tokenizer for accurate counting
                    full_text_prompt = (
                        f"{system_prompt}\n{user_prompt}" if system_prompt else user_prompt
                    )
                    try:
                        # Try to get tokenizer from the stream handler's VLM pipeline
                        tokenizer = self._stream_handler._vlm_pipeline.tokenizer
                        text_prompt_tokens = len(tokenizer.encode(full_text_prompt))
                    except Exception:
                        # Fallback: word-based approximation (~1.3 tokens per word)
                        text_prompt_tokens = int(len(full_text_prompt.split()) * 1.3)

                    usage = ChatCompletionUsage(
                        prompt_tokens=text_prompt_tokens,  # Text prompt tokens only (vision tokens excluded)
                        completion_tokens=total_output_tokens,
                        total_tokens=text_prompt_tokens + total_output_tokens,
                    )

                    return ChatCompletionResponse(
                        id=str(request_id),
                        created=int(req_info.queue_time),
                        model=model_info.id,
                        choices=[choice],
                        usage=usage,
                    )
                except ServiceException:
                    raise
                except Exception as ex:
                    self._stream_handler._send_error_message_to_kafka(
                        VLM_CAPTIONS_ERROR_MESSAGE % str(ex),
                        videoId,
                    )
                    logger.error(VLM_CAPTIONS_ERROR_MESSAGE, str(ex), exc_info=True)
                    raise ServiceException(
                        "Failed to generate chat completion.", "InternalServerError", 500
                    ) from ex
                finally:
                    await self._cleanup_temporary_chat_assets(temp_asset_ids)

        @self._app.post(
            f"{API_PREFIX}/completions",
            summary="OpenAI-compatible completions endpoint",
            description="OpenAI-compatible text completion endpoint. Converts prompt to chat format.",
            responses={
                200: {"description": "Successful Response."},
                **add_common_error_responses(),
            },
            tags=["NIM Compatible"],
        )
        async def completions(
            request_body: CompletionRequest,
        ) -> CompletionResponse:
            """Handle OpenAI-compatible completion requests."""
            # Convert completion request to chat completion format
            # Extract prompt(s)
            prompt_text = (
                request_body.prompt
                if isinstance(request_body.prompt, str)
                else "\n".join(request_body.prompt)
            )

            if not prompt_text:
                raise ServiceException(
                    "Prompt is required for completion", "InvalidParameters", 400
                )

            # Check if user has specified the model that is initialized
            model_info = self._stream_handler.get_models_info()
            if request_body.model != model_info.id:
                raise ServiceException(
                    f"No such model '{request_body.model}'", "BadParameters", 400
                )

            logger.info(
                "Received NIM completion request: model=%s, prompt_length=%d",
                request_body.model,
                len(prompt_text),
            )

            # For VLM, completions require video/image input
            # Since completions endpoint doesn't have 'id' field, we need to inform user
            raise ServiceException(
                "Text-only completions without video/image input are not supported. "
                "Please use /v1/chat/completions with video/image ID in the 'id' field, "
                "or use /v1/generate_captions for VLM processing.",
                "InvalidParameters",
                400,
            )

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
                api="3.1.0",  # OpenAPI version
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

        # Health check aliases for NIM compatibility
        @self._app.get(
            f"{API_PREFIX}/health/live",
            summary="Health Live (NIM compatible)",
            description="Get RTVI VLM Microservice liveness status (NIM-compatible endpoint).",
            responses={
                200: {"model": None, "description": "Service is healthy and live."},
                503: {"model": None, "description": "Service is unhealthy."},
                **add_common_error_responses([500]),
            },
            tags=["NIM Compatible", "Health Check"],
        )
        async def health_live_nim(
            detailed: Annotated[
                bool,
                Query(description="Return detailed health status including all component checks."),
            ] = False,
        ):
            """NIM-compatible liveness probe."""
            # Reuse existing health_live_probe logic
            health_status = self._stream_handler.get_health_status()
            is_healthy = health_status["healthy"]

            if detailed:
                health_status["checks"] = [check.to_dict() for check in health_status["checks"]]
                if is_healthy:
                    return JSONResponse(status_code=200, content=health_status)
                else:
                    return JSONResponse(status_code=503, content=health_status)
            else:
                if is_healthy:
                    return JSONResponse(
                        status_code=200,
                        content={"object": "health.response", "message": "Service is healthy"},
                    )
                else:
                    return JSONResponse(
                        status_code=503,
                        content={"object": "health.response", "message": "Service is unhealthy"},
                    )

        @self._app.get(
            f"{API_PREFIX}/health/ready",
            summary="Health Ready (NIM compatible)",
            description="Get RTVI VLM Microservice readiness status (NIM-compatible endpoint).",
            responses={
                200: {
                    "model": None,
                    "description": "Service is healthy and ready to serve requests.",
                },
                503: {"model": None, "description": "Service is unhealthy."},
                **add_common_error_responses([500]),
            },
            tags=["NIM Compatible", "Health Check"],
        )
        async def health_ready_nim(
            detailed: Annotated[
                bool,
                Query(description="Return detailed health status including all component checks."),
            ] = False,
        ):
            """NIM-compatible readiness probe."""
            # Reuse existing health_ready_probe logic
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
                    return JSONResponse(
                        status_code=200,
                        content={"object": "health.response", "message": "Service is ready."},
                    )
                else:
                    return JSONResponse(
                        status_code=503,
                        content={"object": "health.response", "message": "Service is not ready."},
                    )

        # ======================= NIM-Compatible APIs
        # End of _setup_routes

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
            if err["loc"][-1] == "prompt" and "prompt must not be empty" in msg:
                msg = "prompt must not be empty"
                return JSONResponse(
                    status_code=422, content={"code": "InvalidParameters", "message": msg}
                )
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
        # Note: This handler should be last, after ServiceException and HTTPException handlers
        @self._app.exception_handler(Exception)
        async def handle_exception(request, ex: Exception) -> ServiceError:
            # Log the exception for debugging
            logger.error("Unhandled exception: %s", str(ex), exc_info=True)
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
            # Find the body schema (name may include _v1_ prefix depending on API_PREFIX)
            schemas = openapi_schema.get("components", {}).get("schemas", {})
            body_schema = schemas.get(f"Body_add_video_file{prefix}_files_post")
            if body_schema:
                body_schema["description"] = "Request body schema for adding a file."
                file_props = body_schema.get("properties", {}).get("file")
                if isinstance(file_props, dict) and file_props.get("type") == "string":
                    file_props["maxLength"] = 100e9

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
        from server.rtvi_stream_handler import RTVIStreamHandler

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
