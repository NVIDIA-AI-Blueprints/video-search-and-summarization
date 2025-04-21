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
"""Implements the VIA REST API.

Translates between requests/responses and ViaStreamHandler and AssetManager methods."""

from via_stream_handler import (  # isort:skip
    DEFAULT_CALLBACK_JSON_TEMPLATE,
    RequestInfo,
    ViaStreamHandler,
)

import argparse
import asyncio
import json
import os
import re
import sys
import time
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from enum import Enum
from typing import Annotated, List, Literal, Union
from uuid import UUID

import aiofiles
import aiofiles.os
import gi
import uvicorn
from fastapi import FastAPI, File, Form, Path, Query, Request, Response, UploadFile
from fastapi.exceptions import HTTPException, RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from prometheus_client import (
    GC_COLLECTOR,
    PLATFORM_COLLECTOR,
    PROCESS_COLLECTOR,
    REGISTRY,
    generate_latest,
)
from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    field_validator,
)
from sse_starlette.sse import EventSourceResponse

from asset_manager import Asset, AssetManager
from utils import (
    MediaFileInfo,
    StreamSettingsCache,
    get_available_gpus,
    get_avg_time_per_chunk,
)
from via_exception import ViaException
from via_logger import LOG_PERF_LEVEL, TimeMeasure, logger

gi.require_version("GstRtsp", "1.0")  # isort:skip

from gi.repository import GstRtsp  # noqa: E402

API_PREFIX = ""

# Remove some default metrics reported by prometheus client.
REGISTRY.unregister(PROCESS_COLLECTOR)
REGISTRY.unregister(PLATFORM_COLLECTOR)
REGISTRY.unregister(GC_COLLECTOR)


TIMESTAMP_PATTERN = r"^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(\.\d{3})Z$"
FILE_NAME_PATTERN = r"^[A-Za-z0-9_.\- ]*$"
PATH_PATTERN = r"^[A-Za-z0-9_.\-/ ]*$"
DESCRIPTION_PATTERN = r'^[A-Za-z0-9_.\-"\' ,]*$'
UUID_LENGTH = 36
ERROR_CODE_PATTERN = r"^[A-Za-z]*$"
ERROR_MESSAGE_PATTERN = r'^[A-Za-z\-. ,_"\']*$'
LIVE_STREAM_URL_PATTERN = r"^rtsp://"
KEY_PATTERN = r"^[A-Za-z0-9]*$"
ANY_CHAR_PATTERN = r"^(.|\n)*$"
CV_PROMPT_PATTERN = r"^((([a-zA-Z0-9 ]+)(\s\.\s([a-zA-Z0-9 ]+))*)(;([0-9]*\.?[0-9]+))?)?$"


# Common models
class ViaBaseModel(BaseModel):
    """VIA pydantic base model that does not allow unsupported params in requests"""

    model_config = ConfigDict(extra="forbid")


class ViaError(ViaBaseModel):
    """VIA Error Information."""

    code: str = Field(
        description="Error code", examples=["ErrorCode"], max_length=128, pattern=ERROR_CODE_PATTERN
    )
    message: str = Field(
        description="Detailed error message",
        examples=["Detailed error message"],
        max_length=1024,
        pattern=ERROR_MESSAGE_PATTERN,
    )


COMMON_ERROR_RESPONSES = {
    400: {
        "model": ViaError,
        "description": (
            "Bad Request. The server could not understand the request due to invalid syntax."
        ),
    },
    401: {"model": ViaError, "description": "Unauthorized request."},
    422: {"model": ViaError, "description": "Failed to process request."},
    500: {"model": ViaError, "description": "Internal Server Error."},
    429: {
        "model": ViaError,
        "description": "Rate limiting exceeded.",
    },
}


def add_common_error_responses(errors=[]):
    return (
        {err: COMMON_ERROR_RESPONSES[err] for err in (errors + [401, 429, 422])}
        if errors
        else COMMON_ERROR_RESPONSES
    )


# Validate RFC3339 timestamp string
def timestamp_validator(v: str, validation_info):
    try:
        # Attempt to parse the RFC3339 timestamp
        datetime.strptime(v, "%Y-%m-%dT%H:%M:%S.%fZ")
    except ValueError:
        raise ViaException(
            f"{validation_info.field_name} be a valid RFC3339 timestamp string",
            "InvalidParameters",
            422,
        )
    return v


# ===================== Models required by /files API


class MediaType(str, Enum):
    """Media type of the uploaded file."""

    VIDEO = "video"
    IMAGE = "image"


class Purpose(str, Enum):
    """Purpose for the file."""

    VISION = "vision"


class FileInfo(ViaBaseModel):
    """Information about an uploaded file."""

    id: UUID = Field(
        description="The file identifier, which can be referenced in the API endpoints."
    )
    bytes: int = Field(
        description="The size of the file, in bytes.",
        json_schema_extra={"format": "int64"},
        examples=[2000000],
        ge=0,
        le=100e9,
    )
    filename: str = Field(
        description="Filename along with path to be used.",
        max_length=256,
        examples=["myfile.mp4"],
        pattern=FILE_NAME_PATTERN,
    )

    purpose: Purpose = Field(
        description=(
            "The intended purpose of the uploaded file."
            " For VIA use-case this must be set to vision"
        ),
        examples=["vision"],
    )


class AddFileInfoResponse(FileInfo):
    """Response schema for the add file request."""

    media_type: MediaType = Field(description="Media type (image / video).")


class DeleteFileResponse(ViaBaseModel):
    """Response schema for delete file request."""

    id: UUID = Field(
        description="The file identifier, which can be referenced in the API endpoints."
    )
    object: Literal["file"] = Field(description="Type of response object.")
    deleted: bool = Field(description="Indicates if the file was deleted")


class ListFilesResponse(ViaBaseModel):
    """Response schema for the list files API."""

    data: list[AddFileInfoResponse] = Field(max_length=1000000)
    object: Literal["list"] = Field(description="Type of response object")


# ===================== Models required by Files API


# ===================== Models required by /live-stream API


class AddLiveStream(ViaBaseModel):
    """Parameters required to add a live stream."""

    liveStreamUrl: str = Field(
        description="Live RTSP Stream URL",
        max_length=256,
        pattern=LIVE_STREAM_URL_PATTERN,
        examples=["rtsp://localhost:8554/media/video1"],
    )
    description: str = Field(
        description="Live RTSP Stream description",
        max_length=256,
        examples=["Description of the live stream"],
        pattern=DESCRIPTION_PATTERN,
    )
    username: str = Field(
        default="",
        description="Username to access live stream URL.",
        max_length=256,
        examples=["username"],
        pattern=DESCRIPTION_PATTERN,
    )
    password: str = Field(
        default="",
        description="Password to access live stream URL.",
        max_length=256,
        examples=["password"],
        pattern=DESCRIPTION_PATTERN,
    )


class AddLiveStreamResponse(ViaBaseModel):
    """Response schema for the add live stream API."""

    id: UUID = Field(
        description="The stream identifier, which can be referenced in the API endpoints."
    )


class LiveStreamInfo(ViaBaseModel):
    """Live Stream Information."""

    id: UUID = Field(description="Unique identifier for the live stream")
    liveStreamUrl: str = Field(
        description="Live stream RTSP URL",
        max_length=256,
        examples=["rtsp://localhost:8554/media/video1"],
        pattern=LIVE_STREAM_URL_PATTERN,
    )
    description: str = Field(
        description="Description of live stream",
        max_length=256,
        examples=["Description of live stream"],
        pattern=DESCRIPTION_PATTERN,
    )
    chunk_duration: int = Field(
        description=(
            "Chunk Duration Time in Seconds."
            " Chunks would be created at the I-Frame boundry so duration might not be exact."
        ),
        json_schema_extra={"format": "int32"},
        examples=[60],
        ge=0,
        le=600,
    )
    chunk_overlap_duration: int = Field(
        description=(
            "Chunk Overlap Duration Time in Seconds."
            " Chunks would be created at the I-Frame boundry so duration might not be exact."
        ),
        json_schema_extra={"format": "int32"},
        examples=[10],
        ge=0,
        le=600,
    )
    summary_duration: int = Field(
        description="Summary Duration in Seconds.",
        json_schema_extra={"format": "int32"},
        examples=[300],
        ge=-1,
        le=3600,
    )


# ===================== Models required by /live-stream API


# ===================== Models required by /models API
class ModelInfo(ViaBaseModel):
    """Describes an OpenAI model offering that can be used with the API."""

    id: str = Field(
        description="The model identifier, which can be referenced in the API endpoints.",
        pattern=FILE_NAME_PATTERN,
        max_length=2560,
    )
    created: int = Field(
        description="The Unix timestamp (in seconds) when the model was created.",
        examples=[1686935002],
        ge=0,
        le=4000000000,
        json_schema_extra={"format": "int64"},
    )
    object: Literal["model"] = Field(description="Type of object")
    owned_by: str = Field(
        description="The organization that owns the model.",
        examples=["NVIDIA"],
        max_length=10000,
        pattern=DESCRIPTION_PATTERN,
    )
    api_type: str = Field(
        description="API used to access model.",
        examples=["internal"],
        max_length=32,
        pattern=r"^[A-Za-z]*$",
    )


class ListModelsResponse(ViaBaseModel):
    """Lists and describes the various models available."""

    object: Literal["list"] = Field(description="Type of response object")
    data: list[ModelInfo] = Field(max_length=5)


# ===================== Models required by /models API


# ===================== Models required by /summarize API
class MediaInfoOffset(ViaBaseModel):
    """Media information using offset for files."""

    type: Literal["offset"] = Field(
        description="Information about a segment of media with start and end offsets."
    )
    start_offset: int = Field(
        default=None,
        description="Segment start offset in seconds from the beginning of the media.",
        ge=0,
        le=4000000000,
        examples=[0],
        json_schema_extra={"format": "int64"},
    )
    end_offset: int = Field(
        default=None,
        description="Segment end offset in seconds from the beginning of the media.",
        ge=0,
        le=4000000000,
        examples=[4000000000],
        json_schema_extra={"format": "int64"},
    )


class MediaInfoTimeStamp(ViaBaseModel):
    """Media information using offset for live-streams."""

    type: Literal["timestamp"] = Field(
        description="Information about a segment of live-stream with start and end timestamp."
    )
    start_timestamp: Annotated[str, AfterValidator(timestamp_validator)] = Field(
        default=None,
        description="Timestamp in the video to start processing from",
        min_length=24,
        max_length=24,
        examples=["2024-05-30T01:41:25.000Z"],
        pattern=TIMESTAMP_PATTERN,
    )
    end_timestamp: Annotated[str, AfterValidator(timestamp_validator)] = Field(
        default=None,
        description="Timestamp in the video to stop processing at",
        min_length=24,
        max_length=24,
        examples=["2024-05-30T02:14:51.000Z"],
        pattern=TIMESTAMP_PATTERN,
    )


class ResponseType(str, Enum):
    """Query Response Type."""

    JSON_OBJECT = "json_object"
    TEXT = "text"


class ResponseFormat(ViaBaseModel):
    """Query Response Format Object."""

    type: ResponseType = Field(description="Response format type")


class StreamOptions(ViaBaseModel):
    """Options for streaming response."""

    include_usage: bool = Field(
        default=False,
        description=(
            "If set, an additional chunk will be streamed before the `data: [DONE]` message."
            " The `usage` field on this chunk shows the token usage statistics"
            " for the entire request, and the `choices` field will always be an empty array."
            " All other chunks will also include a `usage` field, but with a null value."
        ),
    )


class ChatCompletionToolType(str, Enum):
    """Types of tools supported by VIA."""

    ALERT = "alert"


class AlertTool(ViaBaseModel):
    """Alert tool configuration."""

    name: str = Field(
        description="Name for the alert tool",
        pattern=ANY_CHAR_PATTERN,
        max_length=256,
    )
    events: list[Annotated[str, Field(max_length=1024, pattern=ANY_CHAR_PATTERN)]] = Field(
        description="List of events to trigger the alert for", max_length=100
    )


class ChatCompletionTool(ViaBaseModel):
    """Configuration of the tool to be used as part of the request."""

    type: ChatCompletionToolType = Field(
        description="The type of the tool. Currently, only `alert` is supported."
    )
    alert: AlertTool


class SummarizationQuery(ViaBaseModel):
    """Summarization Query Request Fields."""

    id: Union[UUID, List[UUID]] = Field(
        description="Unique ID or list of IDs of the file(s)/live-stream(s) to summarize",
    )

    @field_validator("id", mode="after")
    def check_ids(cls, v, info):
        if isinstance(v, list) and len(v) > 50:
            raise ValueError("List of ids must not exceed 50 items")
        return v

    @property
    def id_list(self) -> List[UUID]:
        return [self.id] if isinstance(self.id, UUID) else self.id

    @property
    def get_query_json(self: ViaBaseModel) -> dict:
        return self.model_dump(mode="json")

    prompt: str = Field(
        default="",
        max_length=5000,
        description="Prompt for summary generation",
        pattern=ANY_CHAR_PATTERN,
        examples=["Write a concise and clear dense caption for the provided warehouse video"],
    )
    model: str = Field(
        description="Model to use for this query.",
        examples=["vila-1.5"],
        max_length=256,
        pattern=FILE_NAME_PATTERN,
    )
    api_type: str = Field(
        description="API used to access model.",
        examples=["internal"],
        max_length=32,
        pattern=r"^[A-Za-z]*$",
        default="",
    )
    response_format: ResponseFormat = Field(
        description="An object specifying the format that the model must output.",
        default=ResponseFormat(type=ResponseType.TEXT),
    )
    stream: bool = Field(
        default=False,
        description=(
            "If set, partial message deltas will be sent, like in ChatGPT."
            " Tokens will be sent as data-only [server-sent events]"
            "(https://developer.mozilla.org/en-US/docs/Web/API/Server-sent_events/Using_server-sent_events#Event_stream_format)"  # noqa: E501
            " as they become available, with the stream terminated by a `data: [DONE]` message."
        ),
    )
    stream_options: StreamOptions | None = Field(
        description="Options for streaming response.",
        default=None,
        json_schema_extra={"nullable": True},
    )
    max_tokens: int = Field(
        default=None,
        examples=[512],
        ge=1,
        le=1024,
        description="The maximum number of tokens to generate in any given call.",
        json_schema_extra={"format": "int32"},
    )
    temperature: float = Field(
        default=None,
        examples=[0.2],
        ge=0,
        le=1,
        description=(
            "The sampling temperature to use for text generation."
            " The higher the temperature value is, the less deterministic the output text will be."
        ),
    )
    top_p: float = Field(
        default=None,
        examples=[1],
        ge=0,
        le=1,
        description=(
            "The top-p sampling mass used for text generation."
            " The top-p value determines the probability mass that is sampled at sampling time."
        ),
    )
    top_k: float = Field(
        default=None,
        examples=[100],
        ge=1,
        le=1000,
        description=(
            "The number of highest probability vocabulary tokens to" " keep for top-k-filtering"
        ),
    )
    seed: int = Field(
        default=None,
        ge=1,
        le=(2**32 - 1),
        examples=[10],
        description="Seed value",
        json_schema_extra={"format": "int64"},
    )

    chunk_duration: int = Field(
        default=0,
        examples=[60],
        description="Chunk videos into `chunkDuration` seconds. Set `0` for no chunking",
        ge=0,
        le=3600,
        json_schema_extra={"format": "int32"},
    )
    chunk_overlap_duration: int = Field(
        default=0,
        examples=[10],
        description="Chunk Overlap Duration Time in Seconds. Set `0` for no overlap",
        ge=0,
        le=3600,
        json_schema_extra={"format": "int32"},
    )
    summary_duration: int = Field(
        default=0,
        examples=[60],
        description=(
            "Summarize every `summaryDuration` seconds of the video."
            " Applicable to live streams only."
        ),
        ge=-1,
        le=3600,
        json_schema_extra={"format": "int32"},
    )
    media_info: MediaInfoOffset | MediaInfoTimeStamp = Field(
        default=None,
        description=(
            "Provide Start and End times offsets for processing part of a video file."
            " Not applicable for live-streaming."
        ),
    )

    user: str = Field(
        default="",
        examples=["user-123"],
        max_length=256,
        description="A unique identifier for the user",
        pattern=r"^[a-zA-Z0-9-._]*$",
    )
    caption_summarization_prompt: str = Field(
        default="",
        max_length=5000,
        description="Prompt for caption summarization",
        examples=["Prompt for caption summarization"],
        pattern=ANY_CHAR_PATTERN,
    )

    summary_aggregation_prompt: str = Field(
        default="",
        max_length=5000,
        description="Prompt for summary aggregation",
        examples=["Prompt for summary aggregation"],
        pattern=ANY_CHAR_PATTERN,
    )

    graph_rag_prompt_yaml: str = Field(
        default="",
        max_length=50000,
        description="GraphRAG prompt config (yaml format). Note: this field should contain"
        " the contents of the file in yaml format as a string",
        examples=[""],
        pattern=ANY_CHAR_PATTERN,
    )

    tools: list[ChatCompletionTool] = Field(
        default=[],
        description="List of tools for the current summarization request",
        max_length=100,
    )

    summarize: bool = Field(
        default=None,
        description="Enable summarization for the group of chunks",
    )

    enable_chat: bool = Field(
        default=False,
        description="Enable chat Question & Answers on the input media",
    )

    enable_chat_history: bool = Field(
        default=True,
        description="Enable chat history during QnA for the input media",
    )

    enable_cv_metadata: bool = Field(
        default=False,
        description="Enable CV metadata",
    )

    cv_pipeline_prompt: str = Field(
        default="",
        max_length=1024,
        description="Prompt for CV pipeline",
        examples=["person . car . bicycle;0.5"],
        pattern=CV_PROMPT_PATTERN,
    )

    num_frames_per_chunk: int = Field(
        default=0,
        examples=[10],
        description="Number of frames per chunk to use for the VLM",
        ge=0,
        le=256,
        json_schema_extra={"format": "int32"},
    )
    vlm_input_width: int = Field(
        default=0,
        examples=[256],
        description="VLM Input Width",
        ge=0,
        le=4096,
        json_schema_extra={"format": "int32"},
    )
    vlm_input_height: int = Field(
        default=0,
        examples=[256],
        description="VLM Input Height",
        ge=0,
        le=4096,
        json_schema_extra={"format": "int32"},
    )
    enable_audio: bool = Field(
        default=False,
        description="Enable transcription of the audio stream in the media",
    )

    summarize_batch_size: int = Field(
        default=None,
        examples=[5],
        description="Summarization batch size",
        ge=1,
        le=1024,
        json_schema_extra={"format": "int32"},
    )

    rag_type: Literal["graph-rag", "vector-rag"] = Field(
        default=None,
        examples=["graph-rag", "vector-rag"],
        description="Specify the type of RAG",
    )

    rag_top_k: int = Field(
        default=None,
        examples=[5],
        description="RAG top k",
        ge=1,
        le=1024,
        json_schema_extra={"format": "int32"},
    )

    rag_batch_size: int = Field(
        default=None,
        examples=[5],
        description="RAG batch size",
        ge=1,
        le=1024,
        json_schema_extra={"format": "int32"},
    )

    summarize_max_tokens: int = Field(
        default=None,
        examples=[512],
        ge=1,
        le=10240,
        description="The maximum number of tokens to generate in any given summarization call.",
        json_schema_extra={"format": "int32"},
    )
    summarize_temperature: float = Field(
        default=None,
        examples=[0.2],
        ge=0,
        le=1,
        description=(
            "The sampling temperature to use for summary text generation."
            " The higher the temperature value is, the less deterministic the output text will be."
        ),
    )
    summarize_top_p: float = Field(
        default=None,
        examples=[1],
        ge=0,
        le=1,
        description=(
            "The top-p sampling mass used for summary text generation."
            " The top-p value determines the probability mass that is sampled at sampling time."
        ),
    )

    chat_max_tokens: int = Field(
        default=None,
        examples=[512],
        ge=1,
        le=10240,
        description="The maximum number of tokens to generate in any given QnA call.",
        json_schema_extra={"format": "int32"},
    )
    chat_temperature: float = Field(
        default=None,
        examples=[0.2],
        ge=0,
        le=1,
        description=(
            "The sampling temperature to use for QnA text generation."
            " The higher the temperature value is, the less deterministic the output text will be."
        ),
    )
    chat_top_p: float = Field(
        default=None,
        examples=[1],
        ge=0,
        le=1,
        description=(
            "The top-p sampling mass used for QnA text generation."
            " The top-p value determines the probability mass that is sampled at sampling time."
        ),
    )

    notification_max_tokens: int = Field(
        default=None,
        examples=[512],
        ge=1,
        le=10240,
        description="The maximum number of tokens to generate in any given call.",
        json_schema_extra={"format": "int32"},
    )
    notification_temperature: float = Field(
        default=None,
        examples=[0.2],
        ge=0,
        le=1,
        description=(
            "The sampling temperature to use for text generation."
            " The higher the temperature value is, the less deterministic the output text will be."
        ),
    )
    notification_top_p: float = Field(
        default=None,
        examples=[1],
        ge=0,
        le=1,
        description=(
            "The top-p sampling mass used for text generation."
            " The top-p value determines the probability mass that is sampled at sampling time."
        ),
    )


class CompletionFinishReason(str, Enum):
    """The reason the model stopped generating tokens."""

    STOP = "stop"
    LENGTH = "length"
    CONTENT_FILTER = "content_filter"
    TOOL_CALLS = "tool_calls"


class ChatCompletionMessageAlertTool(ViaBaseModel):
    """Alert trigerred by VIA."""

    name: str = Field(
        description="Name for the alert that was triggered.",
        pattern=DESCRIPTION_PATTERN,
        max_length=256,
    )
    ntpTimestamp: str | None = Field(
        description="NTP timestamp of when the event occurred (for live-streams).",
        min_length=24,
        max_length=24,
        examples=["2024-05-30T01:41:25.000Z"],
        pattern=TIMESTAMP_PATTERN,
        default=None,
    )
    offset: int = Field(
        description="Offset in seconds in the video file when the event occurred (for files).",
        ge=0,
        le=4000000,
        examples=[20],
        json_schema_extra={"format": "int64"},
        default=None,
    )
    detectedEvents: list[
        Annotated[str, Field(min_length=1, max_length=1024, pattern=DESCRIPTION_PATTERN)]
    ] = Field(max_length=100, description="List of events detected.")
    details: str = Field(
        max_length=10000, pattern=ANY_CHAR_PATTERN, description="Details of the alert."
    )


class ChatCompletionMessageToolCall(ViaBaseModel):
    """Tool calls generated by VIA."""

    type: ChatCompletionToolType
    alert: ChatCompletionMessageAlertTool


class ChatMessage(ViaBaseModel):
    """A chatbot chat message object. This object uniquely identify
    a query/response/other messages in a chatbot."""

    content: str = Field(
        description="The content of this message.",
        max_length=256000,
        pattern=ANY_CHAR_PATTERN,
    )
    role: Literal["system", "user", "assistant"] = Field(
        description="The role of the author of this message."
    )
    name: str = Field(
        description="An optional name for the participant. "
        "Provides the model information to differentiate between participants of the same role",
        max_length=256,
        pattern=r"^[\x00-\x7F]*$",
        default="",
    )


class ChatCompletionQuery(ViaBaseModel):
    """A chat completion query."""

    id: Union[UUID, List[UUID]] = Field(
        description="Unique ID or list of IDs of the file(s)/live-stream(s) to summarize"
    )

    @field_validator("id", mode="after")
    def check_ids(cls, v, info):
        if isinstance(v, list) and len(v) > 50:
            raise ValueError("List of ids must not exceed 50 items")
        return v

    @property
    def id_list(self) -> List[UUID]:
        return [self.id] if isinstance(self.id, UUID) else self.id

    messages: List[ChatMessage] = Field(
        description="The list of chat messages.", max_length=1000000
    )
    model: str = Field(
        description="Model to use for this query.",
        examples=["vila-1.5"],
        max_length=256,
        pattern=FILE_NAME_PATTERN,
    )
    api_type: str = Field(
        description="API used to access model.",
        examples=["internal"],
        max_length=32,
        pattern=r"^[A-Za-z]*$",
        default="",
    )
    response_format: ResponseFormat = Field(
        description="An object specifying the format that the model must output.",
        default=ResponseFormat(type=ResponseType.TEXT),
    )
    stream: bool = Field(
        default=False,
        description=(
            "If set, partial message deltas will be sent, like in ChatGPT."
            " Tokens will be sent as data-only [server-sent events]"
            "(https://developer.mozilla.org/en-US/docs/Web/API/Server-sent_events/Using_server-sent_events#Event_stream_format)"  # noqa: E501
            " as they become available, with the stream terminated by a `data: [DONE]` message."
        ),
    )
    stream_options: StreamOptions | None = Field(
        description="Options for streaming response.",
        default=None,
        json_schema_extra={"nullable": True},
    )
    max_tokens: int = Field(
        default=None,
        examples=[512],
        ge=1,
        le=1024,
        description="The maximum number of tokens to generate in any given call.",
        json_schema_extra={"format": "int32"},
    )
    temperature: float = Field(
        default=None,
        examples=[0.2],
        ge=0,
        le=1,
        description=(
            "The sampling temperature to use for text generation."
            " The higher the temperature value is, the less deterministic the output text will be."
        ),
    )
    top_p: float = Field(
        default=None,
        examples=[1],
        ge=0,
        le=1,
        description=(
            "The top-p sampling mass used for text generation."
            " The top-p value determines the probability mass that is sampled at sampling time."
        ),
    )
    top_k: float = Field(
        default=None,
        examples=[100],
        ge=1,
        le=1000,
        description=(
            "The number of highest probability vocabulary tokens to" " keep for top-k-filtering"
        ),
    )
    seed: int = Field(
        default=None,
        ge=1,
        le=(2**32 - 1),
        examples=[10],
        description="Seed value",
        json_schema_extra={"format": "int64"},
    )

    chunk_duration: int = Field(
        default=0,
        examples=[60],
        description="Chunk videos into `chunkDuration` seconds. Set `0` for no chunking",
        ge=0,
        le=3600,
        json_schema_extra={"format": "int32"},
    )
    chunk_overlap_duration: int = Field(
        default=0,
        examples=[10],
        description="Chunk Overlap Duration Time in Seconds. Set `0` for no overlap",
        ge=0,
        le=3600,
        json_schema_extra={"format": "int32"},
    )
    summary_duration: int = Field(
        default=0,
        examples=[60],
        description=(
            "Summarize every `summaryDuration` seconds of the video."
            " Applicable to live streams only."
        ),
        ge=-1,
        le=3600,
        json_schema_extra={"format": "int32"},
    )
    media_info: MediaInfoOffset | MediaInfoTimeStamp = Field(
        default=None,
        description=(
            "Provide Start and End times offsets for processing part of a video file."
            " Not applicable for live-streaming."
        ),
    )
    highlight: bool = Field(
        default=False,
        description="If true, generate a highlight for the video",
    )

    user: str = Field(
        default="",
        examples=["user-123"],
        max_length=256,
        description="A unique identifier for the user",
        pattern=r"^[a-zA-Z0-9-._]*$",
    )


class ChatCompletionResponseMessage(ViaBaseModel):
    """A chat completion message generated by the model."""

    content: str = Field(
        max_length=100000,
        description="The contents of the message.",
        examples=["Some summary of the video"],
        pattern=ANY_CHAR_PATTERN,
        json_schema_extra={"nullable": True},
    )
    tool_calls: list[ChatCompletionMessageToolCall] = Field(default=[], max_length=100)
    role: Literal["assistant"] = Field(description="The role of the author of this message.")


class CompletionResponseChoice(ViaBaseModel):
    """Completion Response Choice."""

    finish_reason: CompletionFinishReason = Field(
        description=(
            "The reason the model stopped generating tokens."
            " This will be `stop` if the model hit a natural stop point or a provided"
            " stop sequence,\n`length` if the maximum number of tokens specified in the"
            " request was reached,\n`content_filter` if content was omitted due to a flag"
            " from our content filters."
        ),
        examples=[CompletionFinishReason.STOP],
    )
    index: int = Field(
        description="The index of the choice in the list of choices.",
        ge=0,
        le=4000000000,
        examples=[1],
        json_schema_extra={"format": "int64"},
    )
    message: ChatCompletionResponseMessage


class CompletionObject(str, Enum):
    """Completion object type."""

    CHAT_COMPLETION = "chat.completion"
    SUMMARIZATION_COMPLETION = "summarization.completion"
    SUMMARIZATION_PROGRESSING = "summarization.progressing"


class CompletionUsage(ViaBaseModel):
    """An optional field that will only be present when you set
    `stream_options: {\"include_usage\": true}` in your request.

    When present, it contains a null value except for the last chunk which contains
    the token usage statistics for the entire request.
    """

    query_processing_time: int = Field(
        description="Summarization Query Processing Time in seconds.",
        ge=0,
        le=1000000,
        examples=[78],
        json_schema_extra={"format": "int32"},
    )
    total_chunks_processed: int = Field(
        description="Total Number of chunks processed.",
        ge=0,
        le=1000000,
        examples=[10],
        json_schema_extra={"format": "int32"},
    )


class CompletionResponse(ViaBaseModel):
    """Represents a summarization/chat completion response."""

    id: UUID = Field(description="Unique ID for the query")
    choices: list[CompletionResponseChoice] = Field(
        description=(
            "A list of chat completion choices. Can be more than one if `n` is greater than 1."
        ),
        max_length=10,
    )
    created: int = Field(
        json_schema_extra={"format": "int64"},
        ge=0,
        le=4000000000,
        examples=[1717405636],
        description=(
            "The Unix timestamp (in seconds) of when the chat completion/summary request"
            " was created."
        ),
    )
    model: str = Field(
        description="The model used for the chat completion/summarization.",
        examples=["vila-1.5"],
        max_length=256,
        pattern=FILE_NAME_PATTERN,
    )
    media_info: MediaInfoTimeStamp | MediaInfoOffset = Field(
        description="Part of the file / live-stream for which this response is applicable."
    )
    object: CompletionObject = Field(
        description=(
            "The object type, which can be `chat.completion` or `summarization.completion`"
            " or `summarization.progressing`."
        ),
        examples=[CompletionObject.SUMMARIZATION_COMPLETION],
    )
    usage: CompletionUsage | None = Field(default=None)


# ===================== Models required by /summarize API


# ===================== Models required by /recommended_config API
class RecommendedConfig(ViaBaseModel):
    """Recommended VIA Config."""

    video_length: int = Field(
        default=None,
        examples=[5, 10, 60, 300],
        ge=1,
        le=24 * 60 * 60 * 10000,
        description="The video length in seconds.",
        json_schema_extra={"format": "int32"},
    )
    target_response_time: int = Field(
        default=None,
        examples=[5, 10, 60, 300],
        ge=1,
        le=86400,
        description="The target response time of VIA in seconds.",
        json_schema_extra={"format": "int32"},
    )
    usecase_event_duration: int = Field(
        default=None,
        examples=[5, 10, 60, 300],
        ge=1,
        le=86400,
        description=(
            "The duration of the target event user wants to detect;"
            " example: it will take a box-falling event 3 seconds to happen."
        ),
        json_schema_extra={"format": "int32"},
    )


class RecommendedConfigResponse(ViaBaseModel):
    """Recommended VIA Config Response."""

    chunk_size: int = Field(
        default=None,
        examples=[5, 10, 60, 300],
        ge=0,
        le=86400,
        description="The recommended chunk size in seconds and no chunking is 0",
        json_schema_extra={"format": "int32"},
    )
    text: str = Field(
        description="Recommendation text",
        max_length=5000,
        examples=["Recommendation text"],
        pattern=DESCRIPTION_PATTERN,
    )


# ===================== Models required by /recommended_config API


# ===================== Models required by /alerts API


class RecentAlertInfo(ViaBaseModel):
    """Information about a recent alert."""

    alert_name: str = Field(
        description="Name of the alert", max_length=1000, pattern=DESCRIPTION_PATTERN
    )
    alert_id: UUID = Field(description="ID of the alert")
    live_stream_id: UUID = Field(description="ID of the live stream that generated the alert")
    detected_events: list[
        Annotated[str, Field(min_length=1, max_length=1024, pattern=DESCRIPTION_PATTERN)]
    ] = Field(
        description="List of events that were detected",
        max_length=100,
        examples=[["Fire", "More than 5 people"]],
    )
    alert_text: str = Field(
        description="Detailed description of the alert", max_length=10000, pattern=ANY_CHAR_PATTERN
    )
    ntp_timestamp: str = Field(
        description="NTP timestamp when the alert was generated",
        min_length=24,
        max_length=24,
        examples=["2024-05-30T01:41:25.000Z"],
        pattern=TIMESTAMP_PATTERN,
    )


class AddAlertInfo(ViaBaseModel):
    """Information required to add an alert."""

    name: str = Field(
        description="Name of the alert", max_length=1000, pattern=DESCRIPTION_PATTERN, default=""
    )
    liveStreamId: UUID = Field(description="ID of the live stream to configure the alert for")
    events: list[Annotated[str, Field(min_length=1, max_length=1024, pattern=ANY_CHAR_PATTERN)]] = (
        Field(
            description="List of events to generate alert for",
            max_length=100,
            examples=[["Fire", "More than 5 people"]],
        )
    )
    callback: HttpUrl = Field(
        description="URL to call when events are detected",
        examples=["http://localhost:12000/via-callback-handler"],
    )
    callbackJsonTemplate: str = Field(
        description=(
            "JSON Template for the callback body. Supported placeholders:"
            " {{streamId}}, {{alertId}}, {{ntpTimestamp}}, {{alertText}}, {{detectedEvents}}"
        ),
        max_length=1024,
        default=DEFAULT_CALLBACK_JSON_TEMPLATE,
        pattern=ANY_CHAR_PATTERN,
    )
    callbackToken: str = Field(
        description="Bearer token to use when calling the callback URL",
        default=None,
        examples=["eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"],
        max_length=10000,
        pattern=FILE_NAME_PATTERN,
    )


class AddAlertResponse(ViaBaseModel):
    """Response of the add alert API."""

    id: UUID = Field(description="ID of the newly added alert")


class AlertInfo(ViaBaseModel):
    """Information about an alert added to the server."""

    liveStreamId: UUID = Field(description="ID of the live stream to configure the alert for")
    events: list[
        Annotated[str, Field(min_length=1, max_length=1024, pattern=DESCRIPTION_PATTERN)]
    ] = Field(
        description="List of events to generate alert for",
        max_length=100,
        examples=[["Fire", "More than 5 people"]],
    )
    alertId: UUID = Field(description="ID of the alert")
    name: str = Field(description="Name of the alert", max_length=1000, pattern=DESCRIPTION_PATTERN)


# ===================== Models required by /alerts API


class ViaServer:
    def __init__(self, args) -> None:
        self._args = args

        self._asset_manager = AssetManager(
            args.asset_dir,
            max_storage_usage_gb=args.max_asset_storage_size,
            asset_removal_callback=self._remove_asset,
        )

        self._async_executor = ThreadPoolExecutor(max_workers=args.max_live_streams)

        # Use FastAPI to implement the REST API
        self._app = FastAPI(
            contact={"name": "NVIDIA", "url": "https://nvidia.com"},
            description="Visual Insights Agent API.",
            title="Visual Insights Agent API",
            openapi_tags=[
                {
                    "name": "Alerts",
                    "description": "Operations to configure live stream alerts.",
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
                {
                    "name": "Recommended Config",
                    "description": "Operations related to querying recommended"
                    " VIA request parameters.",
                },
                {
                    "name": "Summarization",
                    "description": "Operations related to video summarization.",
                },
            ],
            servers=[
                {"url": "/", "description": "VIA microservice local endpoint.", "x-internal": False}
            ],
            version="v1",
        )
        self._app.config = {}
        self._app.config["host"] = args.host
        self._app.config["port"] = args.port

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

        self._stream_settings_cache = StreamSettingsCache(logger=logger)

    def _remove_asset(self, asset: Asset):
        if asset.is_live:
            self._stream_handler.remove_rtsp_stream(asset)
        else:
            self._stream_handler.remove_video_file(asset)
        return True

    def run(self):
        try:
            # Start the VIA stream handler
            self._stream_handler = ViaStreamHandler(self._args)
        except Exception as ex:
            raise ViaException(f"Failed to load VIA stream handler - {str(ex)}")

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
            summary="Get VIA metrics",
            description="Get VIA metrics in prometheus format.",
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
            f"{API_PREFIX}/health/ready",
            summary="Get VIA readiness status",
            description="Get VIA readiness status.",
            responses={
                200: {"model": None, "description": "Successful Response."},
                **add_common_error_responses([500]),
            },
            tags=["Health Check"],
        )
        async def health_ready_probe():
            return Response(status_code=200)

        @self._app.get(
            f"{API_PREFIX}/health/live",
            summary="Get VIA liveness status",
            description="Get VIA liveness status.",
            responses={
                200: {"model": None, "description": "Successful Response."},
                **add_common_error_responses([500]),
            },
            tags=["Health Check"],
        )
        async def health__live_probe():
            return Response(status_code=200)

        # ======================= Health check API

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
                        " For VIA use-case this must be set to vision"
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
                raise ViaException(
                    "At least one of 'file' or 'filename' must be specified",
                    "InvalidParameters",
                    422,
                )
            if file and filename:
                raise ViaException(
                    "Only one of 'file' or 'filename' must be specified. Both are not allowed.",
                    "InvalidParameters",
                    422,
                )

            if media_type != "video" and media_type != "image":
                raise ViaException(
                    "Currently only 'video', 'image' media_type is supported.",
                    "InvalidParameters",
                    422,
                )
            if file:
                if not re.compile(FILE_NAME_PATTERN).match(file.filename):
                    raise ViaException(
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

            try:
                if not os.environ.get("VSS_SKIP_INPUT_MEDIA_VERIFICATION", ""):
                    media_info = await MediaFileInfo.get_info_async(
                        self._asset_manager.get_asset(video_id).path
                    )
                    if not media_info.video_codec:
                        raise Exception("Invalid file")
                    if (media_type == "image") != media_info.is_image:
                        raise Exception("Invalid file")
            except Exception as e:
                logger.error("".join(traceback.format_exception(e)))
                self._asset_manager.cleanup_asset(video_id)
                raise ViaException(
                    f"File does not seem to be a valid {media_type} file",
                    "InvalidFile",
                    400,
                )

            asset = self._asset_manager.get_asset(video_id)
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
                409: {"model": ViaError, "description": "File is in use and cannot be deleted."},
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
                raise ViaException(f"No such file {file_id}", "BadParameter", 400)
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                self._async_executor, self._stream_handler.remove_video_file, asset
            )
            await loop.run_in_executor(
                self._async_executor, self._asset_manager.cleanup_asset, file_id
            )

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
                raise ViaException(f"No such resource {file_id}", "BadParameter", 400)
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
                raise ViaException(f"No such resource {str(file_id)}", "BadParameter", 400)
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
                        raise ViaException(
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
                raise ViaException(
                    "Either both 'username' and 'password' should be specified"
                    " or neither should be specified",
                    "InvalidParameters",
                    422,
                )
            try:
                # Check if the RTSP URL contains valid video as well as the passed
                # username/password are correct before adding it to the server.
                if not os.environ.get("VSS_SKIP_INPUT_MEDIA_VERIFICATION", ""):
                    media_info = await MediaFileInfo.get_info_async(
                        query.liveStreamUrl, query.username, query.password
                    )
                    if not media_info.video_codec:
                        raise Exception("Invalid file")
            except Exception:
                raise ViaException(
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
                if id not in self._stream_handler._live_stream_info_map:
                    return 0, 0, 0
                info = self._stream_handler._live_stream_info_map[id]
                if info.live_stream_ended:
                    return 0, 0, 0

                if info.req_info and info.req_info[0].status == RequestInfo.Status.PROCESSING:
                    summary_duration = (
                        info.req_info[0].summary_duration
                        if info.req_info and info.req_info[0].summary_duration
                        else info.chunk_size
                    )
                    return info.chunk_size, 0, summary_duration
                return 0, 0, 0

            live_stream_list = [
                {
                    "id": asset.asset_id,
                    "liveStreamUrl": asset.path,
                    "description": asset.description,
                    "chunk_duration": get_stream_params(asset.asset_id)[0],
                    "chunk_overlap_duration": get_stream_params(asset.asset_id)[1],
                    "summary_duration": get_stream_params(asset.asset_id)[2],
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
            description="API for removing live / camerea stream matching `stream_id`.",
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

            if not self._asset_manager.get_asset(stream_id).is_live:
                raise ViaException(f"No such live-stream {stream_id}", "InvalidParameter", 400)

            asset = self._asset_manager.get_asset(stream_id)
            loop = asyncio.get_event_loop()
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

        # ======================= Summarize API

        @self._app.post(
            f"{API_PREFIX}/summarize",
            summary="Summarize a video",
            description="Run video summarization query.",
            responses={
                200: {"description": "Successful Response."},
                **add_common_error_responses(),
                503: {
                    "model": ViaError,
                    "description": (
                        "Server is busy processing another file / live-stream."
                        " Client may try again in some time."
                    ),
                },
            },
            tags=["Summarization"],
        )
        async def summarize(query: SummarizationQuery, request: Request) -> CompletionResponse:

            videoIdListUUID = query.id_list
            videoIdList = [str(uuid_obj) for uuid_obj in videoIdListUUID]
            assetList = []

            if len(videoIdList) > 1:
                for videoId in videoIdList:
                    asset = self._asset_manager.get_asset(videoId)
                    assetList.append(asset)
                    if asset.media_type != "image":
                        raise ViaException(
                            "Multi-file summarize: Only image files supported."
                            f" {asset._filename} is a not an image",
                            "BadParameters",
                            400,
                        )

            if query.enable_audio:
                for videoId in videoIdList:
                    asset = self._asset_manager.get_asset(videoId)
                    if asset.media_type == "image":
                        raise ViaException(
                            "Audio transcription is not supported for image files."
                            f" {asset._filename} is an image",
                            "BadParameters",
                            400,
                        )

            videoId = videoIdList[
                0
            ]  # Note: Other files processed only for multi-image summarize() below
            asset = self._asset_manager.get_asset(videoId)

            llm_generation_config = {}
            # Extract user specified llm output parameters
            if query.max_tokens is not None:
                llm_generation_config["max_new_tokens"] = query.max_tokens
            if query.top_p is not None:
                llm_generation_config["top_p"] = query.top_p
            if query.top_k is not None:
                llm_generation_config["top_k"] = query.top_k
            if query.temperature is not None:
                llm_generation_config["temperature"] = query.temperature
            if query.seed is not None:
                llm_generation_config["seed"] = query.seed

            media_info_start = None
            media_info_end = None

            if query.media_info:
                # Extract user specified start/end time filter.
                # For files, it is in terms of "offset" - start/end time in seconds
                # For live stream, it is in terms of "timetamp" - start/end NTP timestamp.
                if query.media_info.type == "offset":
                    media_info_start = query.media_info.start_offset
                    media_info_end = query.media_info.end_offset
                if query.media_info.type == "timetamp":
                    media_info_start = query.media_info.start_timestamp
                    media_info_end = query.media_info.end_timestamp

            logger.info(
                "Received summarize query, id - %s (live-stream=%d), "
                "chunk_duration=%d, chunk_overlap_duration=%d, "
                "media-offset-type=%s, media-start-time=%r, "
                "media-end-time=%r, modelParams=%s, "
                "summary_duration=%d, stream=%r num_frames_per_chunk=%d "
                "vlm_input_width = %d, "
                "vlm_input_height = %d, "
                "summarize_batch_size = %s, "
                "summarize_max_tokens = %s, "
                "summarize_temperature = %s, "
                "summarize_top_p = %s, "
                "rag_type = %s, "
                "rag_top_k = %s, "
                "rag_batch_size = %s, "
                "chat_max_tokens = %s, "
                "chat_temperature = %s, "
                "chat_top_p = %s, "
                "notification_max_tokens = %s, "
                "notification_temperature = %s, "
                "notification_top_p = %s, "
                "cv_pipeline_prompt = %s, "
                "enable_cv_metadata = %d, "
                "enable_chat_history = %d",
                ", ".join(videoIdList),
                asset.is_live,
                query.chunk_duration,
                query.chunk_overlap_duration,
                query.media_info and query.media_info.type,
                media_info_start,
                media_info_end,
                json.dumps(llm_generation_config),
                query.summary_duration,
                query.stream,
                query.num_frames_per_chunk,
                query.vlm_input_width,
                query.vlm_input_height,
                query.summarize_batch_size,
                query.summarize_max_tokens,
                query.summarize_temperature,
                query.summarize_top_p,
                query.rag_type,
                query.rag_top_k,
                query.rag_batch_size,
                query.chat_max_tokens,
                query.chat_temperature,
                query.chat_top_p,
                query.notification_max_tokens,
                query.notification_temperature,
                query.notification_top_p,
                query.cv_pipeline_prompt,
                query.enable_cv_metadata,
                query.enable_chat_history,
            )

            caption_summarization_prompt = query.caption_summarization_prompt
            summary_aggregation_prompt = query.summary_aggregation_prompt

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

            # Only streaming output is supported for live streams
            if asset.is_live and not query.stream:
                raise ViaException(
                    "Only streaming output is supported for live-streams", "BadParameters", 400
                )
            # For non-CA RAG usecase, only streaming output is supported
            if self._stream_handler._ctx_mgr is None and not query.stream:
                raise ViaException(
                    "Only streaming output is supported for files when CA-RAG is disabled",
                    "BadParameters",
                    400,
                )

            loop = asyncio.get_event_loop()

            if asset.is_live:
                # Check if summarization is already running / already completed.
                if videoId in self._stream_handler._live_stream_info_map:
                    # Reconnect client to existing summarization stream
                    request_id = (
                        self._stream_handler._live_stream_info_map[videoId].req_info[0].request_id
                    )
                    logger.info(
                        "Re-connecting to existing live stream query %s for videoId %s",
                        request_id,
                        videoId,
                    )
                else:
                    # Add live stream to the pipeline and start summarization
                    self._stream_handler.add_rtsp_stream(asset, query.chunk_duration)
                    try:
                        request_id = await loop.run_in_executor(
                            self._async_executor,
                            self._stream_handler.add_rtsp_stream_query,
                            asset,
                            query.prompt,
                            query.chunk_duration,
                            llm_generation_config,
                            query.summary_duration,
                            caption_summarization_prompt,
                            summary_aggregation_prompt,
                            query.summarize,
                            query.enable_chat,
                            query.enable_chat_history,
                            query.enable_cv_metadata,
                            "",  # query.graph_rag_prompt_yaml,
                            query.num_frames_per_chunk,
                            query.vlm_input_width,
                            query.vlm_input_height,
                            query.summarize_batch_size,
                            query.rag_type,
                            query.rag_top_k,
                            query.rag_batch_size,
                            query.enable_audio,
                            query.summarize_top_p,
                            query.summarize_temperature,
                            query.summarize_max_tokens,
                            query.chat_top_p,
                            query.chat_temperature,
                            query.chat_max_tokens,
                            query.notification_top_p,
                            query.notification_temperature,
                            query.notification_max_tokens,
                            query.cv_pipeline_prompt,
                        )
                    except Exception as ex:
                        self._stream_handler._live_stream_info_map.pop(asset.asset_id, None)
                        asset.unlock()
                        raise ex from None
                    logger.info("Created live stream query %s for videoId %s", request_id, videoId)

                    for tool in query.tools:
                        if tool.type == ChatCompletionToolType.ALERT:
                            self._stream_handler.add_live_stream_alert(
                                liveStreamId=asset.asset_id,
                                events=tool.alert.events,
                                isCallback=False,
                                alertName=tool.alert.name,
                            )
            else:
                if len(videoIdList) == 1:
                    assetList = [asset]
                # Summarize on a file or multiple files
                request_id = await loop.run_in_executor(
                    self._async_executor,
                    self._stream_handler.summarize,
                    assetList,
                    query.prompt,
                    query.chunk_duration,
                    query.chunk_overlap_duration,
                    llm_generation_config,
                    media_info_start,
                    media_info_end,
                    caption_summarization_prompt,
                    summary_aggregation_prompt,
                    query.summarize,
                    query.enable_chat,
                    query.enable_chat_history,
                    query.enable_cv_metadata,
                    "",  # query.graph_rag_prompt_yaml,
                    query.num_frames_per_chunk,
                    query.summarize_batch_size,
                    query.rag_type,
                    query.rag_top_k,
                    query.rag_batch_size,
                    query.vlm_input_width,
                    query.vlm_input_height,
                    query.enable_audio,
                    query.summarize_top_p,
                    query.summarize_temperature,
                    query.summarize_max_tokens,
                    query.chat_top_p,
                    query.chat_temperature,
                    query.chat_max_tokens,
                    query.notification_top_p,
                    query.notification_temperature,
                    query.notification_max_tokens,
                    query.cv_pipeline_prompt,
                )
                logger.info("Created video file query %s for videoId %s", request_id, videoId)

                if query.tools:
                    for tool in query.tools:
                        if tool.type == ChatCompletionToolType.ALERT:
                            if not query.stream:
                                raise ViaException(
                                    "Only streaming output is supported for alerts",
                                    "BadParameters",
                                    400,
                                )
                            self._stream_handler.add_alert(
                                requestId=request_id,
                                assetId=asset.asset_id,
                                events=tool.alert.events,
                                isCallback=False,
                                alertName=tool.alert.name,
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

                        while req_info.alerts:
                            alert = req_info.alerts.pop(0)
                            # Create the response json
                            response = {
                                "id": request_id,
                                "model": model_info.id,
                                "created": int(req_info.queue_time),
                                "object": "summarization.progressing",
                                "choices": [
                                    {
                                        "finish_reason": CompletionFinishReason.TOOL_CALLS.value,
                                        "index": 0,
                                        "message": {
                                            "tool_calls": [
                                                {
                                                    "type": "alert",
                                                    "alert": {
                                                        "name": alert.name,
                                                        "detectedEvents": alert.detectedEvents,
                                                        "details": alert.details,
                                                        **(
                                                            {"ntpTimestamp": alert.ntpTimestamp}
                                                            if req_info.is_live
                                                            else {"offset": alert.offset}
                                                        ),
                                                    },
                                                }
                                            ],
                                            "role": "assistant",
                                        },
                                    }
                                ],
                                "usage": None,
                            }
                            yield json.dumps(response)

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
                                        "object": "summarization.progressing",
                                        "choices": [
                                            {
                                                "finish_reason": CompletionFinishReason.STOP.value,
                                                "index": 0,
                                                "message": {
                                                    "content": "Summarization failed",
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
                        if req_info.is_live:
                            media_info = {
                                "type": "timestamp",
                                "start_timestamp": resp_list[0].start_timestamp,
                                "end_timestamp": resp_list[0].end_timestamp,
                            }
                        else:
                            media_info = {
                                "type": "offset",
                                "start_offset": int(resp_list[0].start_timestamp),
                                "end_offset": int(resp_list[0].end_timestamp),
                            }

                        # Create the response json
                        response = {
                            "id": request_id,
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
                await self._stream_handler.wait_for_request_done(request_id)
                req_info, resp_list = self._stream_handler.get_response(request_id)
                self._stream_handler.check_status_remove_req_id(request_id)
                if req_info.status == RequestInfo.Status.FAILED:
                    raise ViaException("Failed to generate summary", "InternalServerError", 500)

                # Create response json and return it
                return {
                    "id": request_id,
                    "model": model_info.id,
                    "created": int(req_info.queue_time),
                    "object": "summarization.completion",
                    "media_info": {
                        "type": "offset",
                        "start_offset": int(req_info.start_timestamp),
                        "end_offset": int(req_info.end_timestamp),
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
                        "query_processing_time": int(req_info.end_time - req_info.start_time),
                    },
                }

        # ======================= Summarize API

        # ======================= VIA Q&A API

        def adding_video_path(input_data, video_path):
            """Add video path to either a JSON string or dictionary.

            Args:
                input_data (Union[str, dict]): Either a string representation of a dictionary
                    or a dictionary
                video_path (str): Path to the video file

            Returns:
                str: A JSON string with the video path added
            """
            try:

                json_data = input_data

                # Add video path
                json_data["video"] = video_path

                # Convert back to JSON string
                return json.dumps(json_data)

            except json.JSONDecodeError as e:
                print(f"Error decoding JSON: {e}")
                return None

        @self._app.post(
            f"{API_PREFIX}/chat/completions",
            summary="VIA Chat or Q&A",
            description="Run video interactive question and answer.",
            responses={
                200: {"description": "Successful Response."},
                **add_common_error_responses(),
                503: {
                    "model": ViaError,
                    "description": (
                        "Server is busy processing another file / live-stream."
                        " Client may try again in some time."
                    ),
                },
            },
            tags=["Summarization"],
        )
        async def qa(query: ChatCompletionQuery, request: Request) -> CompletionResponse:

            videoIdListUUID = query.id_list
            logger.debug(f"{videoIdListUUID}")
            videoIdList = [str(uuid_obj) for uuid_obj in videoIdListUUID]
            assetList = []

            def json_to_string(input):
                try:
                    return json.dumps(input)
                except TypeError:
                    return input

            if len(videoIdList) > 1:
                for videoId in videoIdList:
                    asset = self._asset_manager.get_asset(videoId)
                    assetList.append(asset)
                    if asset.media_type != "image":
                        raise ViaException(
                            "Multi-file Q&A: Only image files supported."
                            f" {asset._filename} is a not an image",
                            "BadParameters",
                            400,
                        )

            videoId = videoIdList[0]  # Note: Other files processed only for multi-image qa() below
            asset = self._asset_manager.get_asset(videoId)

            logger.debug(f"Q&A; messages={query.messages}")

            llm_generation_config = {}
            # Extract user specified llm output parameters
            if query.max_tokens is not None:
                llm_generation_config["max_new_tokens"] = query.max_tokens
            if query.top_p is not None:
                llm_generation_config["top_p"] = query.top_p
            if query.top_k is not None:
                llm_generation_config["top_k"] = query.top_k
            if query.temperature is not None:
                llm_generation_config["temperature"] = query.temperature
            if query.seed is not None:
                llm_generation_config["seed"] = query.seed

            media_info_start = 0
            media_info_end = 0

            if query.media_info:
                # Extract user specified start/end time filter.
                # For files, it is in terms of "offset" - start/end time in seconds
                # For live stream, it is in terms of "timetamp" - start/end NTP timestamp.
                if query.media_info.type == "offset":
                    media_info_start = query.media_info.start_offset
                    media_info_end = query.media_info.end_offset
                if query.media_info.type == "timetamp":
                    media_info_start = query.media_info.start_timestamp
                    media_info_end = query.media_info.end_timestamp

            logger.info(
                "Received QA query, id - %s (live-stream=%d), "
                "chunk_duration=%d, chunk_overlap_duration=%d, "
                "media-offset-type=%s, media-start-time=%r, "
                "media-end-time=%r, modelParams=%s, summary_duration=%d, stream=%r",
                ", ".join(videoIdList),
                asset.is_live,
                query.chunk_duration,
                query.chunk_overlap_duration,
                query.media_info and query.media_info.type,
                media_info_start,
                media_info_end,
                json.dumps(llm_generation_config),
                query.summary_duration,
                query.stream,
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
            if self._stream_handler._ctx_mgr is None:
                raise ViaException(
                    "Chat functionality disabled",
                    "BadParameters",
                    400,
                )

            loop = asyncio.get_event_loop()
            request_id = str(uuid.uuid4())

            if len(videoIdList) == 1:
                assetList = [asset]

            answer_resp = await loop.run_in_executor(
                self._async_executor,
                self._stream_handler.qa,
                assetList,
                str(query.messages[-1].content),
                llm_generation_config,
                media_info_start,
                media_info_end,
                query.highlight,
            )
            logger.info("Created query %s for id %s", request_id, videoId)
            logger.info("Waiting for results of query %s", request_id)

            logger.debug(f"Q&A answer:{answer_resp}")
            if len(answer_resp) > 0 and answer_resp[0] == "{":
                try:
                    json_resp = json.loads(answer_resp)
                    if json_resp.get("type") == "highlight":
                        video_path = self._asset_manager.get_asset(videoId).path
                        highlight_resp_with_path = adding_video_path(
                            json_resp["highlightResponse"], video_path
                        )
                        json_resp["highlightResponse"] = json.loads(highlight_resp_with_path)
                        answer_resp = json.dumps(json_resp)
                except json.JSONDecodeError:
                    # If JSON parsing fails, proceed with original behavior
                    pass
            response = {
                "id": str(request_id),
                "model": model_info.id,
                "created": int(0),
                "object": "summarization.completion",
                "media_info": {
                    "type": "offset",
                    "start_offset": media_info_start,
                    "end_offset": media_info_end,
                },
                "choices": [
                    {
                        "finish_reason": CompletionFinishReason.STOP.value,
                        "index": 0,
                        "message": {
                            "content": answer_resp,
                            "role": "assistant",
                        },
                    }
                ],
                "usage": {
                    "total_chunks_processed": 0,
                    "query_processing_time": int(0),
                },
            }
            return response

        # ======================= Q&A API

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

            if self._args and self._args.vlm_model_type:
                model_id = str(self._args.vlm_model_type)
            else:
                model_id = "openai-compat"

            try:
                loop = asyncio.get_event_loop()
                gpus = await loop.run_in_executor(self._async_executor, get_available_gpus)
                if gpus:
                    avg_time_per_chunk = get_avg_time_per_chunk(
                        gpus[0]["name"], model_id, "/opt/nvidia/via/default_runtime_stats.yaml"
                    )
                    avg_time_per_chunk = round_up(avg_time_per_chunk)
                    # Equation is: query.target_response_time =
                    #           avg_time_per_chunk * (video_leng / chunk_size)
                    recommended_chunk_size = (
                        avg_time_per_chunk * query.video_length
                    ) / query.target_response_time
                    # Chunk size needed for usecase would be:
                    # usecase_requirement_for_chunk_size =
                    #         query.usecase_event_duration * num_frames_per_chunk
                    if recommended_chunk_size > query.video_length:
                        recommended_chunk_size = query.video_length
                    logger.info(f"recommended_chunk_size is {recommended_chunk_size}")
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

        # ======================= Alerts API
        @self._app.post(
            f"{API_PREFIX}/alerts",
            summary="Add an alerts",
            description="Add an alert for a live stream.",
            responses={
                200: {"description": "Successful Response."},
                405: {"description": "Alert functionality not enabled."},
                **add_common_error_responses(),
            },
            tags=["Alerts"],
        )
        def add_alert(query: AddAlertInfo) -> AddAlertResponse:
            logger.info(
                "Received add alert request: live-stream-id %s, events [%s],"
                " callbackJsonTemplate %s",
                str(query.liveStreamId),
                ", ".join(query.events),
                query.callbackJsonTemplate,
            )

            if query.name:
                alertName = query.name
            elif query.events:
                alertName = query.events[0]
            else:
                raise ViaException("Alert name or events are required", "BadParameters", 400)

            alert = self._stream_handler.add_live_stream_alert(
                liveStreamId=str(query.liveStreamId),
                events=query.events,
                callbackUrl=str(query.callback),
                callbackJsonTemplate=query.callbackJsonTemplate,
                callbackToken=query.callbackToken,
                isCallback=True,
                alertName=alertName,
            )
            logger.info("Added alert with id %s", alert.alert_id)

            return {"id": alert.alert_id}

        @self._app.get(
            f"{API_PREFIX}/alerts",
            summary="List all live stream alerts",
            description="List all live stream alerts added to the VIA Server.",
            responses={
                200: {"description": "Successful Response."},
                **add_common_error_responses(),
            },
            tags=["Alerts"],
        )
        def list_alerts() -> Annotated[List[AlertInfo], Field(max_length=1000)]:
            alerts = [
                {
                    "liveStreamId": alert.liveStreamId,
                    "events": alert.events,
                    "alertId": alert.alert_id,
                    "name": alert.name,
                }
                for alert in self._stream_handler.live_stream_alerts()
            ]
            logger.info(
                "Received list alerts request. Responding with %d alerts info",
                len(alerts),
            )
            return alerts

        @self._app.delete(
            f"{API_PREFIX}/alerts/{{alert_id}}",
            summary="Delete a live stream alert",
            description="Delete a live stream alert added to the VIA Server.",
            responses={
                200: {"description": "Successful Response."},
                **add_common_error_responses(),
            },
            tags=["Alerts"],
        )
        def delete_alert(
            alert_id: Annotated[UUID, Path(description="Unique ID of the alert to be deleted.")],
        ):
            logger.info("Received delete alert request for %s", str(alert_id))
            self._stream_handler.remove_live_stream_alert(str(alert_id))

        @self._app.get(
            f"{API_PREFIX}/alerts/recent",
            summary="Get recent alerts",
            description="Get recently generated alerts. Optionally filter by live stream ID.",
            responses={
                200: {"description": "Successful Response."},
                **add_common_error_responses(),
            },
            tags=["Alerts"],
        )
        def get_recent_alerts(
            live_stream_id: Annotated[
                UUID | None,
                Query(
                    description="Optional live stream ID to filter alerts.",
                ),
            ] = None,
        ) -> Annotated[List[RecentAlertInfo], Field(max_length=1000)]:
            """Get recent alerts.

            Returns:
                List[RecentAlertInfo]: List of recent alerts with timestamps
            """
            logger.info(
                "Received get recent alerts request%s",
                f" for stream {live_stream_id}" if live_stream_id else "all",
            )
            alerts = self._stream_handler.get_recent_alert(str(live_stream_id or ""))
            logger.info("Responding with %d recent alerts", len(alerts))
            return [
                {
                    "alert_name": alert.name,
                    "alert_id": alert.alertId,
                    "live_stream_id": alert.streamId,
                    "detected_events": alert.detectedEvents,
                    "alert_text": alert.details,
                    "ntp_timestamp": alert.ntpTimestamp,
                }
                for alert in reversed(alerts)
            ]

        # ======================= Alerts API

    def _setup_exception_handlers(self):
        # Handle incorrect request schema (user error)
        @self._app.exception_handler(RequestValidationError)
        async def handle_validation_error(request, ex) -> ViaError:
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
        async def handle_via_exception(request, ex: ViaException) -> ViaError:
            return JSONResponse(
                status_code=ex.status_code, content={"code": ex.code, "message": ex.message}
            )

        # Handle exceptions and return error details in format specified in the API schema.
        @self._app.exception_handler(HTTPException)
        async def handle_http_exception(request, ex: HTTPException) -> ViaError:
            return JSONResponse(
                status_code=ex.status_code, content={"code": ex.detail, "message": ex.detail}
            )

        # Unhandled backend errors. Return error details in format specified in the API schema.
        @self._app.exception_handler(Exception)
        async def handle_exception(request, ex: Exception) -> ViaError:
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

            openapi_schema["components"]["schemas"]["Body_add_video_file_files_post"][
                "description"
            ] = "Request body schema for adding a file."
            openapi_schema["components"]["schemas"]["Body_add_video_file_files_post"]["properties"][
                "file"
            ]["maxLength"] = 100e9
            openapi_schema["components"]["schemas"]["SummarizationQuery"]["properties"]["id"][
                "anyOf"
            ][1]["maxItems"] = 50
            openapi_schema["components"]["schemas"]["ChatCompletionQuery"]["properties"]["id"][
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
        parser.add_argument(
            "--max-asset-storage-size",
            type=int,
            help="Maximum size of asset storage directory",
            default=None,
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
