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

Pydantic models for the VIA REST API request/response contracts."""

import os
import re
from datetime import datetime
from enum import Enum
from typing import Annotated, List, Literal, Optional, Union
from uuid import UUID

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)
from pydantic.json_schema import SkipJsonSchema

from via_exception import ViaException

TIMESTAMP_PATTERN = r"^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(\.\d{3})?Z$"
FILE_NAME_PATTERN = r"^[A-Za-z0-9_\-/ ]*(?:\.[A-Za-z0-9_\-/ ]*)*$"
PATH_PATTERN = r"^[A-Za-z0-9_.\-/ ]*$"
PATH_OR_URL_PATTERN = r"^(?:[A-Za-z0-9_.\-/ ]*|https?://[A-Za-z0-9_.\-/:%?#&=+~,]+)$"
HTTP_URL_VALIDATION_PATTERN = r"^https?://[A-Za-z0-9_.\-/:%?#&=+~,]+$"

AWS_S3_OBJECT_URL_PATTERN = r"""^https?://
        (?:
            (?P<bucket_vh>[a-z0-9.-]+)
            \.s3[.-](?P<region_vh>[a-z0-9-]+)\.amazonaws\.com/
            (?P<key_vh>.+)
        |
            s3[.-](?P<region_ps>[a-z0-9-]+)\.amazonaws\.com/
            (?P<bucket_ps>[a-z0-9.-]+)/(?P<key_ps>.+)
        )
        $
    """
AWS_S3_URL_PATTERN = r"^s3://(?P<bucket>[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)*)/(?P<object>[^?\s]+)$"  # noqa: E501

AWS_S3_OBJECT_URL_VALIDATION_PATTERN = r"""^https?://
        (?:
            (?:[a-z0-9.-]+)
            \.s3[.-](?:[a-z0-9-]+)\.amazonaws\.com/
            (?:.+)
        |
            s3[.-](?:[a-z0-9-]+)\.amazonaws\.com/
            (?:[a-z0-9.-]+)/(?:.+)
        )
        $
    """
AWS_S3_URL_VALIDATION_PATTERN = r"^s3://(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)*)/(?:[^?\s]+)$"  # noqa: E501
DESCRIPTION_PATTERN = r'^[A-Za-z0-9_.\-"\' ,]*$'
CAMERA_ID_PATTERN = r"^(?:camera_(\d+)|video_(\d+)|default)?$"
UUID_LENGTH = 36
ERROR_CODE_PATTERN = r"^[A-Za-z]*$"
ERROR_MESSAGE_PATTERN = r'^[A-Za-z\-. ,_"\']*$'
LIVE_STREAM_URL_PATTERN = r"^rtsp://"
KEY_PATTERN = r"^[A-Za-z0-9]*$"
ANY_CHAR_PATTERN = r"^(.|\n)*$"
ANY_CHAR_PATTERN_LARGE = r"^[\s\S]*$"
MAX_PROMPT_LENGTH = 512000


# Common models
class ViaBaseModel(BaseModel):
    """VIA pydantic base model that does not allow unsupported params in requests"""

    model_config = ConfigDict(extra="forbid")


class LvsError(ViaBaseModel):
    """LVS Error Information."""

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


class Purpose(str, Enum):
    """Purpose for the file."""

    VISION = "vision"


class FileInfo(ViaBaseModel):
    """Information about an uploaded file. Matches RTVI-VLM's FileInfo contract."""

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
        description="Filename of the uploaded file.",
        max_length=256,
        examples=["myfile.mp4"],
    )
    purpose: Purpose = Field(
        description=(
            "The intended purpose of the uploaded file."
            " For VIA use-case this must be set to vision"
        ),
        examples=["vision"],
    )
    creation_time: Optional[str] = Field(
        default=None,
        description=(
            "Creation time of the file in ISO8601 format. "
            "If provided, this offsets the frame times in the response."
        ),
        examples=["2024-06-09T18:32:11.123Z"],
    )
    sensor_name: str = Field(
        default="",
        description="User-defined sensor name for the file.",
        max_length=256,
        examples=["camera-001"],
    )


class AddFileInfoResponse(FileInfo):
    """Response schema for the add file request."""

    media_type: MediaType = Field(description="Media type (video only).")


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


class SummarizationQuery(ViaBaseModel):
    """Summarization Query Request Fields."""

    ignore_eos: Optional[bool] = Field(
        default=None,
        examples=[True, False],
        description=(
            "If true, ignore end-of-sequence token and continue generating until max_tokens."
            " Useful for benchmarking with fixed output length."
        ),
    )

    id: Union[UUID, List[UUID], None] = Field(
        default=None,
        description="Unique ID or list of IDs of the file(s)/live-stream(s) to summarize",
        examples=[
            "123e4567-e89b-12d3-a456-426614174000",
            ["123e4567-e89b-12d3-a456-426614174000", "987fcdeb-51a2-43d1-b567-537725285111"],
        ],
    )

    url: str | None = Field(
        default=None,
        description="URL of the video to summarize",
        examples=[
            "https://www.example.com/video.mp4",
            "s3://bucket/video.mp4",
        ],
        pattern=f"({AWS_S3_URL_VALIDATION_PATTERN})|({AWS_S3_OBJECT_URL_VALIDATION_PATTERN})|({HTTP_URL_VALIDATION_PATTERN})",  # noqa: E501
    )

    @model_validator(mode="before")
    @classmethod
    def strip_excluded_fields(cls, values):
        """Silently remove any internal/excluded fields provided by the user."""
        if not isinstance(values, dict):
            return values

        excluded_fields = [
            name for name, field_info in cls.model_fields.items() if field_info.exclude
        ]
        for field in excluded_fields:
            values.pop(field, None)

        return values

    @field_validator("url", mode="after")
    def check_url(cls, v, info):
        if v is None:
            return v
        if not (
            re.match(AWS_S3_URL_PATTERN, v)
            or re.match(AWS_S3_OBJECT_URL_PATTERN, v)
            or v.startswith("http://")
            or v.startswith("https://")
        ):
            raise ValueError(f"Invalid URL format: {v}. Must be a valid HTTP/HTTPS or AWS S3 URL.")
        return v

    @field_validator("id", mode="after")
    def check_ids(cls, v, info):
        if isinstance(v, list) and len(v) > 50:
            raise ValueError("List of ids must not exceed 50 items")
        return v

    @model_validator(mode="after")
    def _alias_deprecated_num_frames_per_chunk(self):
        """Translate deprecated `num_frames_per_chunk` into the new chunking fields.

        Mapping (when `num_frames_per_chunk > 0`):
            num_frames_per_second_or_fixed_frames_chunk = num_frames_per_chunk
            use_fps_for_chunking = False  # deprecated field was fixed-frames only

        Raises ValueError if the user has set conflicting values on both APIs.
        """
        nfpc = self.num_frames_per_chunk
        if not nfpc:  # default 0 → nothing to do
            return self

        # Conflict: deprecated field is fixed-frames, can't coexist with FPS mode
        if self.use_fps_for_chunking:
            raise ValueError(
                "Conflicting params: `num_frames_per_chunk` (deprecated, fixed-frames only) "
                "is set with `use_fps_for_chunking=true`. Drop `num_frames_per_chunk` and "
                "use `num_frames_per_second_or_fixed_frames_chunk` with "
                "`use_fps_for_chunking=true` instead."
            )

        # Conflict: explicit different value on the new field
        new_field = self.num_frames_per_second_or_fixed_frames_chunk
        if new_field is not None and float(new_field) != float(nfpc):
            raise ValueError(
                f"Conflicting params: `num_frames_per_chunk={nfpc}` does not match "
                f"`num_frames_per_second_or_fixed_frames_chunk={new_field}`. "
                "Use only one (prefer `num_frames_per_second_or_fixed_frames_chunk`)."
            )

        # Alias: forward the deprecated value to the new field
        if new_field is None:
            self.num_frames_per_second_or_fixed_frames_chunk = float(nfpc)
        # `use_fps_for_chunking` stays at its default (False) — fixed-frames mode

        return self

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
        max_length=MAX_PROMPT_LENGTH,
        description="Prompt for summary generation",
        pattern=ANY_CHAR_PATTERN_LARGE,
        examples=["Write a concise and clear dense caption for the provided warehouse video"],
    )
    model: str = Field(
        description="Model to use for this query.",
        examples=["cosmos-reason1"],
        max_length=1024,
        pattern=ANY_CHAR_PATTERN,
    )
    api_type: SkipJsonSchema[str] = Field(
        description="API used to access model.",
        examples=["internal"],
        max_length=32,
        pattern=r"^[A-Za-z]*$",
        default="",
        exclude=True,
        json_schema_extra={"exclude": True},
    )
    response_format: SkipJsonSchema[ResponseFormat] = Field(
        description="An object specifying the format that the model must output.",
        default=ResponseFormat(type=ResponseType.TEXT),
        examples=[
            ResponseFormat(type=ResponseType.TEXT),
            ResponseFormat(type=ResponseType.JSON_OBJECT),
        ],
        exclude=True,
        json_schema_extra={"exclude": True},
    )
    stream: SkipJsonSchema[bool] = Field(
        default=False,
        description=(
            "If set, partial message deltas will be sent, like in ChatGPT."
            " Tokens will be sent as data-only [server-sent events]"
            "(https://developer.mozilla.org/en-US/docs/Web/API/Server-sent_events/Using_server-sent_events#Event_stream_format)"  # noqa: E501
            " as they become available, with the stream terminated by a `data: [DONE]` message."
        ),
        examples=[True, False],
    )
    stream_options: SkipJsonSchema[StreamOptions | None] = Field(
        description="Options for streaming response.",
        default=None,
        json_schema_extra={"nullable": True},
        examples=[{"include_usage": True}, {"include_usage": False}],
    )
    max_tokens: int = Field(
        default=None,
        examples=[512],
        ge=1,
        le=1000000,
        description="The maximum number of tokens to generate in any given call.",
        json_schema_extra={"format": "int32"},
    )
    min_tokens: Optional[int] = Field(
        default=None,
        examples=[100],
        ge=1,
        le=1000000,
        description=(
            "Minimum number of tokens to generate before the model is allowed to stop. "
            "Used with ignore_eos for fixed-length generation."
        ),
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

    summarize: SkipJsonSchema[bool] = Field(
        default=None,
        description="Enable summarization for the group of chunks",
        examples=[True, False],
        exclude=True,
        json_schema_extra={"exclude": True},
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

    num_frames_per_chunk: int = Field(
        default=0,
        examples=[10],
        ge=0,
        le=120,
        description=(
            "DEPRECATED: Use `num_frames_per_second_or_fixed_frames_chunk` "
            "(with `use_fps_for_chunking=false`) instead. "
            "Number of frames per chunk to use for the VLM. "
        ),
        json_schema_extra={"format": "int32", "deprecated": True},
        deprecated=True,
    )
    num_frames_per_second_or_fixed_frames_chunk: float = Field(
        default=None,
        examples=[1.0, 10.0],
        ge=0,
        le=120,
        description=(
            "Number of frames per second (if use_fps_for_chunking=true) or "
            "fixed number of frames per chunk (if use_fps_for_chunking=false)."
        ),
    )
    use_fps_for_chunking: bool = Field(
        default=False,
        description=(
            "If true, use num_frames_per_second_or_fixed_frames_chunk as FPS. "
            "If false, use it as a fixed frame count per chunk."
        ),
        examples=[True, False],
    )
    creation_time: str = Field(
        default=None,
        description=(
            "Creation time of the media in ISO 8601 format (e.g. 2024-06-09T18:32:11.123Z). "
            "If provided, offsets frame timestamps in the response."
        ),
        min_length=24,
        max_length=24,
        examples=["2024-06-09T18:32:11.123Z"],
    )
    alert_category: str = Field(
        default=None,
        max_length=256,
        description="Alert category for VLM captions (e.g. 'Worker PPE Violation').",
        examples=["Worker PPE Violation"],
    )
    mm_processor_kwargs: dict = Field(
        default=None,
        description=(
            "Additional keyword arguments for the multimodal processor "
            "(e.g., size, shortest_edge, longest_edge)."
        ),
        examples=[{"shortest_edge": 384}],
    )

    summarize_batch_size: SkipJsonSchema[int] = Field(
        default=None,
        examples=[5],
        description="Summarization batch size",
        ge=1,
        le=1024,
        json_schema_extra={"format": "int32", "exclude": True},
        exclude=True,
    )

    summarize_max_tokens: SkipJsonSchema[int] = Field(
        default=None,
        examples=[512],
        ge=1,
        le=65536,
        description="The maximum number of tokens to generate in any given summarization call.",
        json_schema_extra={"format": "int32", "exclude": True},
        exclude=True,
    )
    summarize_temperature: SkipJsonSchema[float] = Field(
        default=None,
        examples=[0.2],
        ge=0,
        le=1,
        description=(
            "The sampling temperature to use for summary text generation."
            " The higher the temperature value is, the less deterministic the output text will be."
        ),
        exclude=True,
        json_schema_extra={"exclude": True},
    )
    summarize_top_p: SkipJsonSchema[float] = Field(
        default=None,
        examples=[1],
        ge=0,
        le=1,
        description=(
            "The top-p sampling mass used for summary text generation."
            " The top-p value determines the probability mass that is sampled at sampling time."
        ),
        exclude=True,
        json_schema_extra={"exclude": True},
    )

    collection_name: SkipJsonSchema[str] = Field(
        default=None,
        description="User specified collection name for the graph rag",
        max_length=256,
        pattern="^[A-Za-z_][A-Za-z0-9_]*$",
        exclude=True,
        json_schema_extra={"exclude": True},
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

    camera_id: SkipJsonSchema[Optional[str]] = Field(
        default="default",
        description="Camera ID to be used for the summarization request.",
        max_length=256,
        examples=["camera_1", "video_1", "default"],
        pattern=CAMERA_ID_PATTERN,
        exclude=True,
        json_schema_extra={"exclude": True},
    )

    schema: str = Field(
        default=None,
        max_length=50000,
        description="JSON schema for structured output extraction from video content",
        pattern=ANY_CHAR_PATTERN,
        examples=['{"type": "object", "properties": {"events": {"type": "array"}}}'],
        alias="schema",
    )

    batch_response_method: str = Field(
        default=None,
        max_length=256,
        description="Method for batch response processing",
        pattern=r"^[A-Za-z_]*$",
        examples=["json_schema", "text"],
    )

    scenario: str = Field(
        default=...,  # required
        max_length=1024,
        description="Scenario or use case context for the summarization",
        pattern=ANY_CHAR_PATTERN,
        examples=["warehouse", "retail", "security"],
    )

    events: list[Annotated[str, Field(max_length=1024, pattern=ANY_CHAR_PATTERN)]] = Field(
        default=...,  # required
        description="List of events to detect or extract from the video",
        max_length=1000,
        examples=[["fire", "theft", "accident"], ["safety violation", "unauthorized access"]],
    )

    auto_generate_prompt: bool = Field(
        default=None,
        description="Enable automatic prompt generation based on schema and events",
        examples=[True, False],
    )

    time_metadata_keys: SkipJsonSchema[
        list[Annotated[str, Field(max_length=256, pattern=r"^[A-Za-z_]*$")]]
    ] = Field(
        default=None,
        description="List of metadata keys containing time information",
        max_length=100,
        examples=[["start_pts", "end_pts"], ["timestamp", "duration"]],
    )

    override_vlm_prompt: bool = Field(
        default=False,
        description="Override the VLM prompt with the user supplied prompt",
        examples=[True, False],
    )

    enable_vlm_structured_output: bool = Field(
        default=True,
        description="Enable VLM structured output",
        examples=[True, False],
    )

    objects_of_interest: list[Annotated[str, Field(max_length=256, pattern=ANY_CHAR_PATTERN)]] = (
        Field(
            default=[],
            description="List of objects of interest to detect or extract from the video",
            max_length=1000,
            examples=[["person", "car", "bicycle"], ["package", "forklift", "worker"]],
        )
    )

    enable_qa: bool = Field(
        default=False,
        description=(
            "Enable graph-based QA. When true, captions are ingested into a "
            "knowledge graph after summarization, enabling subsequent "
            "/chat/completions queries against this video/stream."
        ),
        examples=[True, False],
    )


class CompletionFinishReason(str, Enum):
    """The reason the model stopped generating tokens."""

    STOP = "stop"
    LENGTH = "length"
    CONTENT_FILTER = "content_filter"


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
    """Request body for POST /chat/completions — graph-based QA over summarized video."""

    id: UUID = Field(
        description="Stream or video file UUID (must have been previously summarized with enable_qa=true).",  # noqa: E501
        examples=["123e4567-e89b-12d3-a456-426614174000"],
    )

    messages: List[ChatMessage] = Field(
        description="Chat messages. The last user message is sent as the question.",
        max_length=1000,
    )

    model: str = Field(
        description="Model to use for this query.",
        examples=["cosmos-reason1"],
        max_length=1024,
        pattern=ANY_CHAR_PATTERN,
    )

    max_tokens: Optional[int] = Field(
        default=None,
        examples=[512],
        ge=1,
        le=1000000,
        description="Maximum tokens to generate.",
        json_schema_extra={"format": "int32"},
    )

    temperature: Optional[float] = Field(
        default=None,
        examples=[0.2],
        ge=0,
        le=1,
        description="Sampling temperature for text generation.",
    )

    top_p: Optional[float] = Field(
        default=None,
        examples=[1],
        ge=0,
        le=1,
        description="Top-p sampling mass for text generation.",
    )

    top_k: Optional[float] = Field(
        default=None,
        examples=[100],
        ge=1,
        le=1000,
        description="Top-k filtering for text generation.",
    )

    is_live: bool = Field(
        default=False,
        description="Whether the source is a live stream.",
        examples=[True, False],
    )


class ChatCompletionResponseMessage(ViaBaseModel):
    """A chat completion message generated by the model."""

    content: str = Field(
        max_length=1000000,
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
    summary_tokens: int = Field(
        default=0,
        description="Total Number of tokens used for summary.",
        ge=0,
        le=1000000,
        examples=[100],
        json_schema_extra={"format": "int32"},
    )
    aggregation_tokens: int = Field(
        default=0,
        description="Total Number of tokens used for aggregation.",
        ge=0,
        le=1000000,
        examples=[100],
        json_schema_extra={"format": "int32"},
    )
    summary_requests: int = Field(
        default=0,
        description="Total Number of requests for summary.",
        ge=0,
        le=1000000,
        examples=[10],
        json_schema_extra={"format": "int32"},
    )
    summary_latency: float = Field(
        default=0.0,
        description="Total latency for summary.",
        ge=0.0,
        le=1000000.0,
        examples=[1.0],
        json_schema_extra={"format": "float32"},
    )
    aggregation_latency: float = Field(
        default=0.0,
        description="Total latency for aggregation.",
        ge=0.0,
        le=1000000.0,
        examples=[1.0],
        json_schema_extra={"format": "float32"},
    )


class CompletionResponse(ViaBaseModel):
    """Represents a summarization/chat completion response."""

    id: UUID = Field(description="Unique ID for the query")
    video_id: UUID = Field(description="Unique ID for the video")
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


# NOTE: AggregateLiveStreamQuery / AggregateLiveStreamEvent /
# AggregateLiveStreamResponse were removed. Live-stream summarization now
# uses the dedicated POST /v1/generate_captions + POST /v1/stream_summarize
# APIs. The aggregator output is wrapped as JSON inside
# CompletionResponse.choices[0].message.content.


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
        description="The target response time of LVS in seconds.",
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


class VlmQuery(ViaBaseModel):
    """VLM Captions Query Request Fields."""

    id: Union[UUID, List[UUID], None] = Field(
        default=None,
        description="Unique ID or list of IDs. Optional (generated if not provided).",
        examples=[
            "123e4567-e89b-12d3-a456-426614174000",
            ["123e4567-e89b-12d3-a456-426614174000", "987fcdeb-51a2-43d1-b567-537725285111"],
        ],
    )

    url: str | None = Field(
        default=None,
        description="URL of the video to generate captions for.",
        examples=[
            "https://www.example.com/video.mp4",
            "s3://bucket/video.mp4",
        ],
        pattern=f"({AWS_S3_URL_VALIDATION_PATTERN})|({AWS_S3_OBJECT_URL_VALIDATION_PATTERN})|({HTTP_URL_VALIDATION_PATTERN})",  # noqa: E501
    )

    camera_id: Optional[str] = Field(
        default="default",
        description="Camera ID / sensor identifier for the source.",
        max_length=256,
        examples=["camera_1", "default"],
    )

    @field_validator("id", mode="after")
    def check_ids(cls, v, info):
        if v is not None and isinstance(v, list) and len(v) > 50:
            raise ValueError("List of ids must not exceed 50 items")
        return v

    @property
    def id_list(self) -> List[UUID]:
        if self.id is None:
            return []
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
        max_length=MAX_PROMPT_LENGTH,
        description="Prompt for VLM captions generation",
        pattern=ANY_CHAR_PATTERN_LARGE,
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
    stream: SkipJsonSchema[bool] = Field(
        default=False,
        description=(
            "If set, partial message deltas will be sent, like in ChatGPT."
            " Tokens will be sent as data-only [server-sent events]"
            "(https://developer.mozilla.org/en-US/docs/Web/API/Server-sent_events/Using_server-sent_events#Event_stream_format)"  # noqa: E501
            " as they become available, with the stream terminated by a `data: [DONE]` message."
        ),
        examples=[True, False],
    )
    stream_options: SkipJsonSchema[StreamOptions | None] = Field(
        description="Options for streaming response.",
        default=None,
        json_schema_extra={"nullable": True},
        examples=[{"include_usage": True}, {"include_usage": False}],
    )
    max_tokens: int = Field(
        default=None,
        examples=[512],
        ge=1,
        le=1000000,
        description="The maximum number of tokens to generate in any given call.",
        json_schema_extra={"format": "int32"},
    )
    min_tokens: Optional[int] = Field(
        default=None,
        examples=[100],
        ge=1,
        le=1000000,
        description=(
            "Minimum number of tokens to generate before the model is allowed to stop. "
            "Used with ignore_eos for fixed-length generation."
        ),
        json_schema_extra={"format": "int32"},
    )
    ignore_eos: Optional[bool] = Field(
        default=None,
        description=(
            "If true, ignore end-of-sequence token and continue generating until max_tokens."
        ),
        examples=[True, False],
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


# ===================== Models for stream captioning / summarization APIs


class GenerateCaptionsRequest(ViaBaseModel):
    """Request body for POST /v1/generate_captions (stream captioning)."""

    id: UUID = Field(
        description="Stream ID (from RTVI stream/add).",
        examples=["123e4567-e89b-12d3-a456-426614174000"],
    )

    model: str = Field(
        description="Model to use for caption generation.",
        examples=["cosmos-reason1"],
        max_length=1024,
        pattern=ANY_CHAR_PATTERN,
    )

    prompt: str = Field(
        default="",
        max_length=MAX_PROMPT_LENGTH,
        description="VLM prompt for caption generation.",
        pattern=ANY_CHAR_PATTERN_LARGE,
        examples=["Write a concise and clear dense caption for the provided video"],
    )

    system_prompt: str = Field(
        default=os.environ.get("VLM_SYSTEM_PROMPT", ""),
        max_length=5000,
        description="System prompt for the VLM.",
        pattern=ANY_CHAR_PATTERN,
    )

    chunk_duration: int = Field(
        default=0,
        examples=[60],
        description="Chunk videos into chunkDuration seconds. 0 for no chunking.",
        ge=0,
        le=3600,
        json_schema_extra={"format": "int32"},
    )

    chunk_overlap_duration: int = Field(
        default=0,
        examples=[10],
        description="Chunk overlap duration in seconds. 0 for no overlap.",
        ge=0,
        le=3600,
        json_schema_extra={"format": "int32"},
    )

    num_frames_per_second_or_fixed_frames_chunk: Optional[float] = Field(
        default=None,
        description="Number of frames per second or fixed frames per chunk.",
    )

    use_fps_for_chunking: bool = Field(
        default=False,
        description="Use FPS for chunking instead of fixed frame count.",
    )

    max_tokens: Optional[int] = Field(
        default=None,
        examples=[512],
        ge=1,
        le=1000000,
        description="Maximum number of tokens to generate per chunk.",
        json_schema_extra={"format": "int32"},
    )

    temperature: Optional[float] = Field(
        default=None,
        examples=[0.2],
        ge=0,
        le=1,
        description="Sampling temperature for VLM text generation.",
    )

    top_p: Optional[float] = Field(
        default=None,
        examples=[1],
        ge=0,
        le=1,
        description="Top-p sampling mass for VLM text generation.",
    )

    top_k: Optional[float] = Field(
        default=None,
        examples=[100],
        ge=1,
        le=1000,
        description="Top-k filtering for VLM text generation.",
    )

    seed: Optional[int] = Field(
        default=None,
        ge=1,
        le=(2**32 - 1),
        examples=[10],
        description="Seed value for reproducibility.",
        json_schema_extra={"format": "int64"},
    )

    enable_reasoning: bool = Field(
        default=False,
        description="Enable reasoning for VLM captions generation.",
    )

    enable_audio: bool = Field(
        default=False,
        description="Enable audio ASR alongside video captioning.",
    )

    vlm_input_width: int = Field(
        default=0,
        examples=[256],
        description="VLM input width (0 = model default).",
        ge=0,
        le=4096,
        json_schema_extra={"format": "int32"},
    )

    vlm_input_height: int = Field(
        default=0,
        examples=[256],
        description="VLM input height (0 = model default).",
        ge=0,
        le=4096,
        json_schema_extra={"format": "int32"},
    )

    mm_processor_kwargs: Optional[dict] = Field(
        default=None,
        description="Optional multimodal processor kwargs.",
    )

    alert_category: Optional[str] = Field(
        default=None,
        description="Alert category for structured captioning.",
        max_length=256,
    )

    creation_time: Optional[str] = Field(
        default=None,
        description="ISO 8601 creation timestamp for the stream.",
        max_length=24,
    )

    scenario: str = Field(
        default="",
        max_length=5000,
        description="Scenario description for auto-prompt generation.",
        pattern=ANY_CHAR_PATTERN_LARGE,
    )

    events: List[str] = Field(
        default=[],
        max_length=1000,
        description="Event types for auto-prompt generation.",
    )

    objects_of_interest: List[str] = Field(
        default=[],
        max_length=1000,
        description="Objects of interest for auto-prompt generation.",
    )

    enable_vlm_structured_output: bool = Field(
        default=True,
        description="Enable structured VLM output (controls auto-prompt format).",
    )

    override_vlm_prompt: bool = Field(
        default=False,
        description=("If true, use prompt as-is instead of auto-generating from scenario/events."),
    )

    enable_qa: bool = Field(
        default=False,
        description=(
            "Enable graph-based QA. When true, captions are ingested into a "
            "knowledge graph, enabling subsequent /chat/completions queries."
        ),
        examples=[True, False],
    )


class GenerateCaptionsResponse(ViaBaseModel):
    """Response for POST /v1/generate_captions."""

    id: str = Field(description="Stream ID that captioning was started for.")
    status: str = Field(description="Status of the captioning request.")
    model: str = Field(description="Model used for caption generation.")


class StreamSummarizeRequest(ViaBaseModel):
    """Request body for POST /v1/stream_summarize (stream summarization)."""

    id: UUID = Field(
        description="Stream ID to summarize.",
        examples=["123e4567-e89b-12d3-a456-426614174000"],
    )

    model: str = Field(
        description="Model identifier (must match the loaded model).",
        examples=["cosmos-reason1"],
        max_length=1024,
        pattern=ANY_CHAR_PATTERN,
    )

    start_time: Optional[Union[float, str]] = Field(
        default=0,
        description=(
            "Time window start for summarization. "
            "Accepts seconds as a float (e.g. 15.04) or an ISO 8601 timestamp string "
            '(e.g. "2026-04-30T10:39:20.934Z"). 0 or empty string = no filter.'
        ),
        examples=[0, 15.04, "2026-04-30T10:39:20.934Z"],
    )

    end_time: Optional[Union[float, str]] = Field(
        default=0,
        description=(
            "Time window end for summarization. "
            "Accepts seconds as a float (e.g. 30.0) or an ISO 8601 timestamp string "
            '(e.g. "2026-04-30T10:45:00.000Z"). 0 or empty string = no filter.'
        ),
        examples=[0, 30.0, "2026-04-30T10:45:00.000Z"],
    )

    enable_vlm_structured_output: bool = Field(
        default=True,
        description="Enable structured VLM output for summarization.",
    )

    camera_id: Optional[str] = Field(
        default="default",
        description="Camera / sensor identifier.",
        max_length=256,
        examples=["camera_1", "default"],
    )

    summarize_max_tokens: Optional[int] = Field(
        default=None,
        ge=1,
        le=1000000,
        description="Max tokens for LLM aggregation.",
        json_schema_extra={"format": "int32"},
    )

    summarize_temperature: Optional[float] = Field(
        default=None,
        ge=0,
        le=1,
        description="Temperature for LLM aggregation.",
    )

    summarize_top_p: Optional[float] = Field(
        default=None,
        ge=0,
        le=1,
        description="Top-p for LLM aggregation.",
    )

    summarize_batch_size: Optional[int] = Field(
        default=None,
        ge=1,
        description="Batch size for summarization.",
        json_schema_extra={"format": "int32"},
    )

    schema_field: Optional[str] = Field(
        default=None,
        alias="schema",
        description="Schema for unstructured output format.",
    )

    batch_response_method: Optional[str] = Field(
        default=None,
        description="Batch response method for summarization.",
    )

    auto_generate_prompt: Optional[bool] = Field(
        default=None,
        description="Auto-generate summarization prompt from scenario/events.",
    )

    time_metadata_keys: Optional[List[str]] = Field(
        default=None,
        description="Time metadata keys for summarization.",
    )

    collection_name: Optional[str] = Field(
        default=None,
        description="External collection name for DB storage.",
        max_length=256,
    )

    custom_metadata: Optional[dict] = Field(
        default=None,
        description="Custom metadata for DB storage.",
    )

    delete_external_collection: bool = Field(
        default=False,
        description="Delete external collection after summarization.",
    )

    enable_qa: bool = Field(
        default=False,
        description=(
            "Enable graph-based QA. When true, captions are ingested into a "
            "knowledge graph after summarization, enabling subsequent "
            "/chat/completions queries against this stream."
        ),
        examples=[True, False],
    )
