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

import os
from datetime import datetime
from enum import Enum
from typing import Annotated, List, Literal, Optional, Union
from uuid import UUID

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    field_validator,
)

from via_exception import ViaException

TIMESTAMP_PATTERN = r"^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(\.\d{3})?Z$"
FILE_NAME_PATTERN = r"^[A-Za-z0-9_.\- ]*$"
PATH_PATTERN = r"^[A-Za-z0-9_.\-/ ]*$"
DESCRIPTION_PATTERN = r'^[A-Za-z0-9_.\-"\' ,]*$'
CAMERA_ID_PATTERN = r"^(?:camera_(\d+)|video_(\d+)|default)?$"
UUID_LENGTH = 36
ERROR_CODE_PATTERN = r"^[A-Za-z]*$"
ERROR_MESSAGE_PATTERN = r'^[A-Za-z\-. ,_"\']*$'
LIVE_STREAM_URL_PATTERN = r"^rtsp://"
KEY_PATTERN = r"^[A-Za-z0-9]*$"
ANY_CHAR_PATTERN = r"^(.|\n)*$"
CV_PROMPT_PATTERN = r"^((([a-zA-Z0-9 ]+)(\s\.\s([a-zA-Z0-9 ]+))*)(;([0-9]*\.?[0-9]+))?)?$"

DEFAULT_CALLBACK_JSON_TEMPLATE = (
    "{ "
    '"streamId": "{{ streamId }}", '
    '"alertId": "{{ alertId }}", '
    '"ntpTimestamp": "{{ ntpTimestamp }}", '
    '"alertDetails": "{{ alertText }}", '
    '"detectedEvents": {{ detectedEvents }}'
    "}"
)


DEFAULT_CALLBACK_JSON_TEMPLATE = (
    "{ "
    '"streamId": "{{ streamId }}", '
    '"alertId": "{{ alertId }}", '
    '"ntpTimestamp": "{{ ntpTimestamp }}", '
    '"alertDetails": "{{ alertText }}", '
    '"detectedEvents": {{ detectedEvents }}'
    "}"
)


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
    camera_id: Optional[str] = Field(
        default=None,
        description="Camera ID to be used for the file.",
        max_length=256,
        examples=["camera_1", "video_1", "default"],
        pattern=CAMERA_ID_PATTERN,
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
    camera_id: Optional[str] = Field(
        default=None,
        description="Camera ID to be used for the live stream.",
        max_length=256,
        examples=["camera_1", "video_1", "default"],
        pattern=CAMERA_ID_PATTERN,
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
        pattern=ANY_CHAR_PATTERN,
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

    type: ResponseType = Field(
        description="Response format type", examples=[ResponseType.JSON_OBJECT, ResponseType.TEXT]
    )


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
        examples=[True, False],
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
    callbackUrl: HttpUrl = Field(
        description="URL to call when events are detected",
        examples=["http://localhost:12000/vss-alert-callback"],
        default=None,
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
        examples=[
            "123e4567-e89b-12d3-a456-426614174000",
            ["123e4567-e89b-12d3-a456-426614174000", "987fcdeb-51a2-43d1-b567-537725285111"],
        ],
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

    system_prompt: str = Field(
        default=os.environ.get("VLM_SYSTEM_PROMPT", ""),
        max_length=5000,
        description="System prompt for the VLM. To enable reasoning with Cosmos Reason1, add <think></think> and <answer></answer> tags to the system prompt.",  # noqa: E501
        pattern=ANY_CHAR_PATTERN,
        examples=[
            "You are a helpful assistant. Answer the user's question.",
        ],
    )

    prompt: str = Field(
        default="",
        max_length=5000,
        description="Prompt for summary generation",
        pattern=ANY_CHAR_PATTERN,
        examples=["Write a concise and clear dense caption for the provided warehouse video"],
    )
    model: str = Field(
        description="Model to use for this query.",
        examples=["cosmos-reason1"],
        max_length=1024,
        pattern=ANY_CHAR_PATTERN,
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
        examples=[
            ResponseFormat(type=ResponseType.TEXT),
            ResponseFormat(type=ResponseType.JSON_OBJECT),
        ],
    )
    stream: bool = Field(
        default=False,
        description=(
            "If set, partial message deltas will be sent, like in ChatGPT."
            " Tokens will be sent as data-only [server-sent events]"
            "(https://developer.mozilla.org/en-US/docs/Web/API/Server-sent_events/Using_server-sent_events#Event_stream_format)"  # noqa: E501
            " as they become available, with the stream terminated by a `data: [DONE]` message."
        ),
        examples=[True, False],
    )
    stream_options: StreamOptions | None = Field(
        description="Options for streaming response.",
        default=None,
        json_schema_extra={"nullable": True},
        examples=[{"include_usage": True}, {"include_usage": False}],
    )
    max_tokens: int = Field(
        default=None,
        examples=[512],
        ge=1,
        le=10240,
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

    tools: list[ChatCompletionTool] = Field(
        default=[],
        description="List of tools for the current summarization request",
        max_length=100,
    )

    summarize: bool = Field(
        default=None,
        description="Enable summarization for the group of chunks",
        examples=[True, False],
    )

    enable_chat: bool = Field(
        default=False,
        description="Enable chat Question & Answers on the input media",
        examples=[True, False],
    )

    enable_chat_history: bool = Field(
        default=True,
        description="Enable chat history during QnA for the input media",
        examples=[True, False],
    )

    enable_cv_metadata: bool = Field(
        default=False, description="Enable CV metadata", examples=[True, False]
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
        examples=[True, False],
    )

    enable_reasoning: bool = Field(
        default=False,
        description="Enable reasoning for VLM captions generation",
        examples=[True, False],
    )

    summarize_batch_size: int = Field(
        default=None,
        examples=[5],
        description="Summarization batch size",
        ge=1,
        le=1024,
        json_schema_extra={"format": "int32"},
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
    graph_db: str = Field(
        default=None,
        examples=["neo4j", "arango"],
        description="Graph database to use for RAG",
        pattern=r"^(neo4j|arango)$",
        max_length=32,
    )
    enable_cot: bool = Field(
        default=False,
        description="Enable CoT for the summarization",
    )
    enable_image: bool = Field(
        default=False,
        description="Enable image for the summarization",
    )
    collection_name: str = Field(
        default=None,
        description="User specified collection name for the graph rag",
        max_length=256,
        pattern="^[A-Za-z_][A-Za-z0-9_]*$",
    )

    custom_metadata: dict[
        Annotated[str, Field(max_length=1024, pattern=ANY_CHAR_PATTERN)],
        Annotated[str, Field(max_length=1024, pattern=ANY_CHAR_PATTERN)],
    ] = Field(
        default=None,
        description="Custom metadata to be added to the summarization request. This is a JSON\
             object with key-value pairs. Custom metadata is supported only with user managed\
                 milvus db collections.",
    )

    delete_external_collection: bool = Field(
        default=False,
        description="Delete the external collection at the end of the summarization request",
    )

    camera_id: Optional[str] = Field(
        default=None,
        description="Camera ID to be used for the summarization request.",
        max_length=256,
        examples=["camera_1", "video_1", "default"],
        pattern=CAMERA_ID_PATTERN,
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
        examples=["cosmos-reason1"],
        max_length=1024,
        pattern=ANY_CHAR_PATTERN,
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
        le=10240,
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
        examples=[True, False],
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
        description="The contents of the message. For VLM captions API, this field contains a "
        "combined response with timestamps for each chunk.",
        examples=[
            "Some summary of the video",
            "[00:00 - 01:00] A worker is walking down the aisle.\n\n"
            "[01:00 - 02:00] A man is driving a forklift in the warehouse.",
        ],
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
    VLM_CAPTIONS_COMPLETION = "vlm_captions.completion"
    VLM_CAPTIONS_PROGRESSING = "vlm_captions.progressing"


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
        examples=["cosmos-reason1"],
        max_length=1024,
        pattern=ANY_CHAR_PATTERN,
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


class VlmCaptionResponse(ViaBaseModel):
    """Represents a VLM caption response for a single chunk."""

    start_time: str = Field(
        description="Start time of the chunk (seconds for files, NTP timestamp for live streams)",
        max_length=50,
        pattern=r"^[0-9\.\-TZ]+$",
        examples=["15.5", "2024-05-30T01:41:25.000Z"],
    )
    end_time: str = Field(
        description="End time of the chunk (seconds for files, NTP timestamp for live streams)",
        max_length=50,
        pattern=r"^[0-9\.\-TZ]+$",
        examples=["30.2", "2024-05-30T01:41:35.000Z"],
    )
    content: str = Field(
        description="VLM caption content for this chunk",
        max_length=100000,
        pattern=ANY_CHAR_PATTERN,
    )
    reasoning_description: str = Field(
        description="Reasoning description for the VLM caption (if enable_reasoning is True)",
        max_length=100000,
        pattern=ANY_CHAR_PATTERN,
        default="",
    )


class VlmCaptionsCompletionResponse(ViaBaseModel):
    """Represents a VLM captions response without choices and object fields."""

    id: UUID = Field(description="Unique ID for the query")
    created: int = Field(
        json_schema_extra={"format": "int64"},
        ge=0,
        le=4000000000,
        examples=[1717405636],
        description=(
            "The Unix timestamp (in seconds) of when the VLM captions request" " was created."
        ),
    )
    model: str = Field(
        description="The model used for the VLM captions generation.",
        examples=["cosmos-reason1"],
        max_length=1024,
        pattern=ANY_CHAR_PATTERN,
    )
    media_info: MediaInfoTimeStamp | MediaInfoOffset = Field(
        description="Part of the file / live-stream for which this response is applicable."
    )
    usage: CompletionUsage | None = Field(default=None)
    chunk_responses: list[VlmCaptionResponse] = Field(
        description="List of individual chunk responses with timestamps and captions",
        default=[],
        max_length=10000,
    )


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


# ===================== Models required by /reviewAlert API


class ReviewAlertAPIVersion(str, Enum):
    """Alert review API Version."""

    V1_0 = "1.0"


class ReviewAlertSeverity(str, Enum):
    """Alert severity levels."""

    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    INFORMAL = "INFORMAL"


class ReviewAlertStatus(str, Enum):
    """Alert status levels."""

    REVIEW_PENDING = "REVIEW_PENDING"
    REVIEWED = "REVIEWED"
    REVIEW_FAILED = "REVIEW_FAILED"


class ReviewAlertType(str, Enum):
    """Alert types."""

    RESTRICTED_ACCESS = "RESTRICTED_ACCESS"
    INTRUSION_DETECTED = "INTRUSION_DETECTED"
    LOITERING_DETECTED = "LOITERING_DETECTED"
    MOTION_DETECTED = "MOTION_DETECTED"
    CROWD_DETECTED = "CROWD_DETECTED"


class VlmParams(ViaBaseModel):
    """VLM parameters configuration."""

    prompt: str = Field(
        max_length=5000,
        description="VLM prompt for alert verification",
        pattern=ANY_CHAR_PATTERN,
        examples=[
            "Is there person detected in restricted area?",
            "Analyze this video for unauthorized access",
            "Check for suspicious activity in the monitored area",
        ],
    )
    system_prompt: str = Field(
        default=(
            os.environ.get("ALERT_REVIEW_DEFAULT_VLM_SYSTEM_PROMPT", "")
            or "You are a helpful assistant. Answer the user's question."
        ),
        min_length=1,
        max_length=5000,
        description="System prompt for the VLM. To enable reasoning with Cosmos Reason1, add <think></think> and <answer></answer> tags to the system prompt.",  # noqa: E501
        pattern=ANY_CHAR_PATTERN,
        examples=[
            (
                "You are a helpful assistant. Answer the user's question."
                " (Caption only or Caption with boolean answer to use with do_verification=True)"
            ),
            (
                "You are a helpful assistant. Answer the user's question."
                " Answer in yes or no only. (Boolean only, use with do_verification=True)"
            ),
            (
                "You are a helpful assistant. Answer the user's question."
                " Answer in yes or no only. Answer the question in the following format:"
                " <think>\nyour reasoning\n</think>\n\n<answer>\nyour answer\n</answer>.\n"
            ),
        ],
    )

    response_format: Optional[ResponseFormat] = Field(
        description="Response format configuration",
        default=None,
        examples=[{"type": "json_object"}, {"type": "text"}],
    )
    max_tokens: int = Field(
        default=None,
        examples=[512],
        ge=1,
        le=10240,
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


class VssParams(ViaBaseModel):
    """VSS parameters configuration."""

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
        examples=[756, 1036, 1512],
        description="VLM Input Width for visual token budget control. Set 0 to use default.",
        ge=0,
        le=4096,
        json_schema_extra={"format": "int32"},
    )
    vlm_input_height: int = Field(
        default=0,
        examples=[392, 588, 840],
        description="VLM Input Height for visual token budget control. Set 0 to use default.",
        ge=0,
        le=4096,
        json_schema_extra={"format": "int32"},
    )
    cv_metadata_overlay: bool = Field(
        description="Enable CV metadata overlay", default=False, examples=[True, False]
    )
    enable_reasoning: bool = Field(
        description="Enable reasoning for VLM alert review",
        default=False,
        examples=[True, False],
    )
    do_verification: bool = Field(
        description="Enable verification for VLM alert review",
        default=False,
        examples=[True, False],
    )
    vlm_params: VlmParams = Field(description="VLM parameters")
    debug: bool = Field(
        description="Enable debug output in response", default=False, examples=[True, False]
    )


class ReviewAlertMetaLabel(ViaBaseModel):
    """Metadata label for alert review."""

    key: str = Field(
        description="Label key",
        max_length=1024,
        pattern=ANY_CHAR_PATTERN,
        examples=["location", "camera_id", "zone", "priority", "department"],
    )
    value: str = Field(
        description="Label value",
        max_length=1024,
        pattern=ANY_CHAR_PATTERN,
        examples=["warehouse_entrance", "cam_001", "restricted_area", "high", "security"],
    )


class ReviewAlertInfo(ViaBaseModel):
    """Information about the alert."""

    severity: ReviewAlertSeverity = Field(description="Alert severity")
    status: ReviewAlertStatus = Field(description="Alert status")
    type: str = Field(
        description="Alert type",
        max_length=1024,
        pattern=ANY_CHAR_PATTERN,
        examples=[
            "RESTRICTED_ACCESS",
            "INTRUSION_DETECTED",
            "LOITERING_DETECTED",
            "MOTION_DETECTED",
            "CROWD_DETECTED",
        ],
    )
    description: str = Field(
        description="Alert description",
        max_length=1024,
        pattern=ANY_CHAR_PATTERN,
        examples=[
            "Person detected in restricted area",
            "Multiple people detected in unauthorized zone",
            "Suspicious activity detected",
        ],
    )


class ReviewAlertEventInfo(ViaBaseModel):
    """Event information for the alert."""

    type: str = Field(
        description="Event type",
        max_length=1024,
        pattern=ANY_CHAR_PATTERN,
        examples=[
            "person_detected",
            "motion_detected",
            "crowd_detected",
            "vehicle_detected",
            "object_left",
        ],
    )
    description: str = Field(
        description="Event description",
        max_length=1024,
        pattern=ANY_CHAR_PATTERN,
        examples=[
            "Person detected in camera view",
            "Motion detected in restricted area",
            "Crowd of people detected",
            "Vehicle detected in pedestrian zone",
        ],
    )


class ReviewAlertDebugInfo(ViaBaseModel):
    """Debug information for the alert review."""

    selected_frames_ts: list[Annotated[float, Field(ge=0.0)]] = Field(
        description="Selected frames timestamps",
        max_length=1024,
        examples=[[0.0, 1.5, 3.0, 4.5, 6.0], [10.0, 12.5, 15.0]],
    )


class ReviewAlertReviewStatus(str, Enum):
    """Alert review status."""

    SUCCESS = "SUCCESS"
    FAILURE = "FAILURE"


class ReviewAlertResult(ViaBaseModel):
    """Review result for the alert."""

    status: ReviewAlertReviewStatus = Field(description="Alert review status")
    error_string: str = Field(
        description="Error details",
        max_length=1024,
        pattern=ANY_CHAR_PATTERN,
        default="",
        examples=["", "Failed to process video", "Model not available"],
    )
    verification_result: Optional[bool] = Field(
        description="Alert review result", examples=[True, False], default=None
    )
    reasoning: str = Field(
        description="Alert review reasoning",
        max_length=100000,
        pattern=ANY_CHAR_PATTERN,
        examples=[
            (
                "The VLM analysis confirmed the presence of a person in the restricted area. "
                "The individual was detected in multiple frames and appears to be unauthorized."
            ),
            "Analysis shows no unauthorized access detected in the video.",
        ],
    )
    review_method: str = Field(
        description="Review method",
        max_length=1024,
        pattern=ANY_CHAR_PATTERN,
        examples=["VLM_ANALYSIS", "AI_VERIFICATION", "MANUAL_REVIEW"],
    )
    reviewed_by: str = Field(
        description="Reviewed by",
        max_length=1024,
        pattern=ANY_CHAR_PATTERN,
        examples=["vila-1.5", "security_system", "human_operator"],
    )
    reviewed_at: str = Field(
        description="Reviewed at",
        pattern=TIMESTAMP_PATTERN,
        max_length=24,
        examples=["2024-05-30T01:41:25.000Z", "2024-05-30T02:15:30.000Z"],
    )
    notes: str = Field(
        description="Notes",
        max_length=1024,
        pattern=ANY_CHAR_PATTERN,
        examples=["", "Alert verified as false positive", "Requires further investigation"],
    )
    description: str = Field(
        description="VLM response",
        max_length=102400,
        pattern=ANY_CHAR_PATTERN,
        examples=[
            ("The scene contains ..."),
        ],
    )
    input_prompt: str = Field(
        max_length=5000,
        description="VLM prompt for alert review",
        pattern=ANY_CHAR_PATTERN,
        examples=[
            "Is there person detected in restricted area?",
            "Analyze this video for unauthorized access",
            "Check for suspicious activity in the monitored area",
        ],
    )
    debug: Optional[ReviewAlertDebugInfo] = Field(description="Debug information", default=None)


class ReviewAlertRequest(ViaBaseModel):
    """Request model for alert review."""

    version: ReviewAlertAPIVersion = Field(description="Alert review API version")
    id: UUID = Field(description="Unique request ID")
    timestamp: str = Field(
        description="NTP timestamp when the alert was generated",
        min_length=20,
        max_length=24,
        examples=["2024-05-30T01:41:25.000Z"],
        pattern=TIMESTAMP_PATTERN,
        validation_alias="@timestamp",
    )
    sensor_id: str = Field(
        description="Sensor identifier",
        max_length=1024,
        pattern=ANY_CHAR_PATTERN,
        examples=["camera-001", "sensor-west-entrance", "thermal-camera-01"],
    )
    video_path: str = Field(
        description="Path to video file relative to the VSS base media path",
        pattern=PATH_PATTERN,
        examples=["alerts/123e4567-e89b-12d3-a456-426614174000/video.mp4"],
        max_length=1024,
    )
    cv_metadata_path: str = Field(
        description="Path to CV metadata file relative to the VSS base media path",
        pattern=PATH_PATTERN,
        examples=["alerts/123e4567-e89b-12d3-a456-426614174000/cv_metadata.json"],
        max_length=1024,
        default="",
    )
    stream_name: Optional[str] = Field(
        description="Stream name",
        max_length=1024,
        pattern=ANY_CHAR_PATTERN,
        examples=["West Gate Entrance"],
        default=None,
    )
    confidence: float = Field(
        description="Confidence score", ge=0.0, le=1.0, examples=[0.95, 0.87, 0.72, 0.99]
    )
    start_time: Optional[float] = Field(
        description="Start time of the clip in seconds within the input video",
        ge=0.0,
        le=315360000.0,
        examples=[0.000, 0.333, 0.666],
        default=0.0,
    )
    end_time: Optional[float] = Field(
        description="End time of the clip in seconds within the input video",
        ge=0.0,
        le=315360000.0,
        examples=[0.000, 0.333, 0.666],
        default=0.0,
    )
    alert: ReviewAlertInfo = Field(description="Alert information")
    event: ReviewAlertEventInfo = Field(description="Event information")
    vss_params: VssParams = Field(description="VSS parameters")
    meta_labels: List[ReviewAlertMetaLabel] = Field(
        description="Metadata labels",
        max_length=1024,
        default=[],
        examples=[
            [
                {"key": "location", "value": "warehouse_entrance"},
                {"key": "camera_id", "value": "cam_001"},
                {"key": "priority", "value": "high"},
            ]
        ],
    )


class ReviewAlertResponse(ViaBaseModel):
    """Alert review response."""

    version: ReviewAlertAPIVersion = Field(description="Alert review API version")
    id: UUID = Field(description="Unique request ID")
    timestamp: str = Field(
        description="NTP timestamp when the alert was generated",
        min_length=20,
        max_length=24,
        examples=["2024-05-30T01:41:25.000Z"],
        pattern=TIMESTAMP_PATTERN,
        serialization_alias="@timestamp",
    )
    sensor_id: str = Field(
        description="Sensor identifier",
        max_length=1024,
        pattern=ANY_CHAR_PATTERN,
        examples=["camera-001", "sensor-west-entrance", "thermal-camera-01"],
    )
    video_path: str = Field(
        description="Path to video file relative to the VSS base media path",
        pattern=PATH_PATTERN,
        examples=["alerts/123e4567-e89b-12d3-a456-426614174000/video.mp4"],
        max_length=1024,
    )
    cv_metadata_path: str = Field(
        description="Path to CV metadata file relative to the VSS base media path",
        pattern=PATH_PATTERN,
        examples=["alerts/123e4567-e89b-12d3-a456-426614174000/cv_metadata.json"],
        max_length=1024,
        default="",
    )
    stream_name: Optional[str] = Field(
        description="Stream name",
        max_length=1024,
        pattern=ANY_CHAR_PATTERN,
        examples=["West Gate Entrance"],
        default=None,
    )
    confidence: float = Field(
        description="Confidence score", ge=0.0, le=1.0, examples=[0.95, 0.87, 0.72, 0.99]
    )
    start_time: Optional[float] = Field(
        description="Start time of the clip in seconds within the input video",
        ge=0.0,
        le=315360000.0,
        examples=[0.000, 0.333, 0.666],
        default=0.0,
    )
    end_time: Optional[float] = Field(
        description="End time of the clip in seconds within the input video",
        ge=0.0,
        le=315360000.0,
        examples=[0.000, 0.333, 0.666],
        default=0.0,
    )
    alert: ReviewAlertInfo = Field(description="Alert information")
    event: ReviewAlertEventInfo = Field(description="Event information")
    result: ReviewAlertResult = Field(description="Review result")
    meta_labels: List[ReviewAlertMetaLabel] = Field(
        description="Metadata labels",
        max_length=1024,
        default=[],
        examples=[
            [
                {"key": "location", "value": "warehouse_entrance"},
                {"key": "camera_id", "value": "cam_001"},
                {"key": "priority", "value": "high"},
            ]
        ],
    )


# ===================== Models required by /reviewAlert API


class VlmQuery(ViaBaseModel):
    """VLM Captions Query Request Fields."""

    id: Union[UUID, List[UUID]] = Field(
        description="Unique ID or list of IDs of the file(s)/live-stream(s) to generate VLM captions for",
        examples=[
            "123e4567-e89b-12d3-a456-426614174000",
            ["123e4567-e89b-12d3-a456-426614174000", "987fcdeb-51a2-43d1-b567-537725285111"],
        ],
        json_schema_extra={
            "anyOf": [
                {"type": "string", "format": "uuid"},
                {"type": "array", "items": {"type": "string", "format": "uuid"}, "maxItems": 50},
            ]
        },
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

    system_prompt: str = Field(
        default=os.environ.get("VLM_SYSTEM_PROMPT", ""),
        max_length=5000,
        description="System prompt for the VLM. To enable reasoning with Cosmos Reason1, add <think></think> and <answer></answer> tags to the system prompt.",  # noqa: E501
        pattern=ANY_CHAR_PATTERN,
        examples=[
            "You are a helpful assistant. Answer the user's question.",
        ],
    )

    prompt: str = Field(
        default="",
        max_length=5000,
        description="Prompt for VLM captions generation",
        pattern=ANY_CHAR_PATTERN,
        examples=["Write a concise and clear dense caption for the provided warehouse video"],
    )
    model: str = Field(
        description="Model to use for this query.",
        examples=["cosmos-reason1"],
        max_length=1024,
        pattern=ANY_CHAR_PATTERN,
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
        examples=[
            ResponseFormat(type=ResponseType.TEXT),
            ResponseFormat(type=ResponseType.JSON_OBJECT),
        ],
    )
    stream: bool = Field(
        default=False,
        description=(
            "If set, partial message deltas will be sent, like in ChatGPT."
            " Tokens will be sent as data-only [server-sent events]"
            "(https://developer.mozilla.org/en-US/docs/Web/API/Server-sent_events/Using_server-sent_events#Event_stream_format)"  # noqa: E501
            " as they become available, with the stream terminated by a `data: [DONE]` message."
        ),
        examples=[True, False],
    )
    stream_options: StreamOptions | None = Field(
        description="Options for streaming response.",
        default=None,
        json_schema_extra={"nullable": True},
        examples=[{"include_usage": True}, {"include_usage": False}],
    )
    max_tokens: int = Field(
        default=None,
        examples=[512],
        ge=1,
        le=10240,
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

    tools: list[ChatCompletionTool] = Field(
        default=[],
        description="List of tools for the current VLM captions request",
        max_length=100,
    )

    enable_cv_metadata: bool = Field(
        default=False, description="Enable CV metadata", examples=[True, False]
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

    enable_reasoning: bool = Field(
        default=False,
        description="Enable reasoning for VLM captions generation",
        examples=[True, False],
    )
