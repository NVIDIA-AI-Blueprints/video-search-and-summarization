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
"""Captions API Models."""

import os
import re
from enum import Enum
from typing import Annotated, List, Optional
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from .common import (
    ANY_CHAR_PATTERN,
    DEFAULT_MAX_GENERATION_TOKENS,
    MAX_GENERATION_TOKENS,
    MAX_GENERATION_TOKENS_ENV,
    CommonBaseModel,
    CompletionUsage,
    MediaInfoOffset,
    MediaInfoTimeStamp,
    StreamOptions,
)

VLM_SYSTEM_PROMPT_MAX_LENGTH_ENV = "VLM_SYSTEM_PROMPT_MAX_LENGTH"
DEFAULT_VLM_SYSTEM_PROMPT_MAX_LENGTH = 10240


def get_vlm_system_prompt_max_length() -> int:
    raw_value = os.environ.get(VLM_SYSTEM_PROMPT_MAX_LENGTH_ENV)
    if not raw_value:
        return DEFAULT_VLM_SYSTEM_PROMPT_MAX_LENGTH
    try:
        max_length = int(raw_value)
    except ValueError:
        return DEFAULT_VLM_SYSTEM_PROMPT_MAX_LENGTH
    if max_length < 1:
        return DEFAULT_VLM_SYSTEM_PROMPT_MAX_LENGTH
    return max_length


VLM_SYSTEM_PROMPT_MAX_LENGTH = get_vlm_system_prompt_max_length()


VLM_PROMPT_MAX_LENGTH_ENV = "VLM_PROMPT_MAX_LENGTH"
DEFAULT_VLM_PROMPT_MAX_LENGTH = 10240


def get_vlm_prompt_max_length() -> int:
    raw_value = os.environ.get(VLM_PROMPT_MAX_LENGTH_ENV)
    if not raw_value:
        return DEFAULT_VLM_PROMPT_MAX_LENGTH
    try:
        max_length = int(raw_value)
    except ValueError:
        return DEFAULT_VLM_PROMPT_MAX_LENGTH
    if max_length < 1:
        return DEFAULT_VLM_PROMPT_MAX_LENGTH
    return max_length


VLM_PROMPT_MAX_LENGTH = get_vlm_prompt_max_length()


# Absolute upper bound for `prompt` / `system_prompt` length, used as the
# Pydantic `max_length=` on Optional[str] fields so the OWASP
# `api4:2023-string-limit` API-governance check (which inspects each `anyOf`
# branch) is satisfied. The runtime cap is the much smaller, env-tunable
# value enforced by the per-field validators (`VLM_PROMPT_MAX_LENGTH`,
# `VLM_SYSTEM_PROMPT_MAX_LENGTH`). 1 MiB is generous but bounded.
ABSOLUTE_PROMPT_MAX_LENGTH = 1024 * 1024


CAPTION_METRIC_MAX_VALUE = 2147483647
OptionalCaptionMetricInt32 = (
    Annotated[
        int,
        Field(
            ge=0,
            le=CAPTION_METRIC_MAX_VALUE,
            json_schema_extra={"format": "int32"},
        ),
    ]
    | None
)


class ResponseType(str, Enum):
    """Query Response Type."""

    JSON_OBJECT = "json_object"
    TEXT = "text"


class ResponseFormat(CommonBaseModel):
    """Query Response Format Object."""

    type: ResponseType = Field(
        description="Response format type", examples=[ResponseType.JSON_OBJECT, ResponseType.TEXT]
    )


class VlmCaptionResponse(CommonBaseModel):
    """Represents a VLM caption response for a single chunk."""

    chunk_id: int = Field(
        default=0,
        description="Zero-based chunk index within the processing session.",
        json_schema_extra={"format": "int32"},
        ge=0,
        le=100000,
    )
    start_time: str = Field(
        description=(
            "Start time of the chunk."
            "For live streams, it is the NTP timestamp of the chunk."
            "For files, if creation_time is provided, "
            "it is the start time of the chunk in ISO 8601 format based on the creation time of the file."
            "For files, if creation_time is not provided, "
            "it is the start time of the chunk in seconds from the start of the file."
        ),
        max_length=50,
        pattern=r"^[0-9\.\-TZ:]+$",
        examples=["15.5", "2024-05-30T01:41:25.000Z"],
    )
    end_time: str = Field(
        description=(
            "End time of the chunk."
            "For live streams, it is the NTP timestamp of the chunk."
            "For files, if creation_time is provided, "
            "it is the end time of the chunk in ISO 8601 format based on the creation time of the file."
            "For files, if creation_time is not provided, "
            "it is the end time of the chunk in seconds from the start of the file."
        ),
        max_length=50,
        pattern=r"^[0-9\.\-TZ:]+$",
        examples=["30.2", "2024-05-30T01:41:35.000Z"],
    )
    content: str = Field(
        description="VLM caption content for this chunk",
        max_length=100000,
        pattern=ANY_CHAR_PATTERN,
    )
    embeddings: List[float] = Field(
        description="Embeddings for this chunk",
        default_factory=list,
        max_length=10000,
    )
    reasoning_description: str = Field(
        description="Reasoning description for the VLM caption (if enable_reasoning is True)",
        max_length=100000,
        pattern=ANY_CHAR_PATTERN,
        default="",
    )
    audio_transcript: Optional[str] = Field(
        description="Audio transcript for this chunk. Only present when non-empty.",
        max_length=100000,
        pattern=ANY_CHAR_PATTERN,
        default=None,
    )
    decode_latency_ms: Optional[float] = Field(
        default=None,
        description="Video decode latency for this chunk in milliseconds.",
        json_schema_extra={"format": "double", "nullable": True},
        ge=0,
    )
    vlm_latency_ms: Optional[float] = Field(
        default=None,
        description="VLM inference latency for this chunk in milliseconds.",
        json_schema_extra={"format": "double", "nullable": True},
        ge=0,
    )
    chunk_latency_ms: Optional[float] = Field(
        default=None,
        description="End-to-end chunk processing latency from decode start to VLM response in milliseconds.",
        json_schema_extra={"format": "double", "nullable": True},
        ge=0,
    )
    queue_time_s: Optional[float] = Field(
        default=None,
        description="Time this chunk spent queued before processing in seconds.",
        json_schema_extra={"format": "double", "nullable": True},
        ge=0,
    )
    processing_latency_s: Optional[float] = Field(
        default=None,
        description="Server-side processing latency for this chunk in seconds.",
        json_schema_extra={"format": "double", "nullable": True},
        ge=0,
    )
    frame_count: OptionalCaptionMetricInt32 = Field(
        default=None,
        description="Number of selected frames processed for this chunk.",
    )
    input_tokens: OptionalCaptionMetricInt32 = Field(
        default=None,
        description="Number of input tokens used by the VLM request for this chunk.",
    )
    output_tokens: OptionalCaptionMetricInt32 = Field(
        default=None,
        description="Number of output tokens generated by the VLM for this chunk.",
    )


class VlmCaptionsCompletionResponse(CommonBaseModel):
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
        max_length=256,
        pattern=ANY_CHAR_PATTERN,
    )
    media_info: MediaInfoTimeStamp | MediaInfoOffset = Field(
        description="Part of the file / live-stream for which this response is applicable."
    )
    usage: CompletionUsage | None = Field(default=None)
    chunk_responses: list[VlmCaptionResponse] = Field(
        description="List of individual chunk responses with timestamps and captions",
        default_factory=list,
        max_length=10000,
    )


# ===================== Models required by /summarize API


class VlmQuery(CommonBaseModel):
    """VLM Captions Query Request Fields."""

    id: UUID | List[UUID] = Field(
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
    def check_ids(cls, v):
        if isinstance(v, list) and len(v) > 50:
            raise ValueError("List of ids must not exceed 50 items")
        return v

    @property
    def id_list(self) -> List[UUID]:
        return [self.id] if isinstance(self.id, UUID) else self.id

    @property
    def get_query_json(self: CommonBaseModel) -> dict:
        return self.model_dump(mode="json")

    url: Optional[str] = Field(
        default=None,
        max_length=2048,
        description=(
            "URL of the video/image to process. Supported schemes: "
            "http://, https://, s3://, file://. "
            "When provided, 'id' must also be specified as a single UUID."
        ),
        pattern=r"^(https?://|s3://|file://).*",
        examples=[
            "https://example.com/video.mp4",
            "s3://bucket/video.mp4",
            "file:///data/videos/clip.mp4",
        ],
    )

    url_headers: Optional[dict] = Field(
        default=None,
        description=(
            "Optional HTTP headers for URL download (e.g., authorization). "
            "Overrides server-level ASSET_DOWNLOAD_AUTH_TOKENS for this request."
        ),
        json_schema_extra={"nullable": True},
        examples=[{"Authorization": "Bearer token123"}],
    )

    @field_validator("url", mode="after")
    def check_url(cls, v, _info):
        if not v:
            return v
        # RTSP explicitly rejected with specific error message
        if v.startswith("rtsp://"):
            raise ValueError("RTSP not supported via url. Use /v1/streams/add instead.")
        # Validate scheme
        if not re.match(r"^(https?://|s3://|file://)", v):
            raise ValueError("Invalid URL scheme. Supported: http://, https://, s3://, file://")
        # SSRF validation for HTTP/HTTPS
        if re.match(r"^https?://", v):
            from api_models.embeddings import validate_url_against_ssrf

            validate_url_against_ssrf(v)
        return v

    media_type: str = Field(
        default="video",
        description="Media type for the url input: 'image' or 'video'.",
        examples=["video", "image"],
        max_length=10,
        pattern=r"^(video|image)$",
    )

    creation_time: Optional[str] = Field(
        default=None,
        description=(
            "Creation time in ISO 8601 format. "
            "If provided, offsets frame timestamps in the response."
        ),
        max_length=30,
        pattern=ANY_CHAR_PATTERN,
        examples=["2024-06-09T18:32:11.123Z"],
    )

    system_prompt: str = Field(
        default=os.environ.get("VLM_SYSTEM_PROMPT", ""),
        description=(
            "System prompt for the VLM. To enable reasoning with Cosmos Reason1, add "
            "<think></think> and <answer></answer> tags to the system prompt. "
            f"Default max length is {DEFAULT_VLM_SYSTEM_PROMPT_MAX_LENGTH}; set "
            f"{VLM_SYSTEM_PROMPT_MAX_LENGTH_ENV} to allow a larger prompt."
        ),
        pattern=ANY_CHAR_PATTERN,
        examples=[
            "You are a helpful assistant. Answer the user's question.",
        ],
        json_schema_extra={
            "maxLength": VLM_SYSTEM_PROMPT_MAX_LENGTH,
            "x-env-override": VLM_SYSTEM_PROMPT_MAX_LENGTH_ENV,
        },
    )

    @field_validator("system_prompt", mode="after")
    def validate_system_prompt_length(cls, v):
        max_length = get_vlm_system_prompt_max_length()
        if len(v) > max_length:
            raise ValueError(
                f"system_prompt length {len(v)} exceeds the configured limit "
                f"{max_length}. Set {VLM_SYSTEM_PROMPT_MAX_LENGTH_ENV} to a higher "
                "positive integer to allow longer system prompts."
            )
        return v

    prompt: str = Field(
        description=(
            "Prompt for VLM captions generation. "
            f"Default max length is {DEFAULT_VLM_PROMPT_MAX_LENGTH}; set "
            f"{VLM_PROMPT_MAX_LENGTH_ENV} to allow a larger prompt."
        ),
        pattern=ANY_CHAR_PATTERN,
        examples=["Write a concise and clear dense caption for the provided warehouse video"],
        json_schema_extra={
            "maxLength": VLM_PROMPT_MAX_LENGTH,
            "x-env-override": VLM_PROMPT_MAX_LENGTH_ENV,
        },
    )

    @field_validator("prompt", mode="after")
    def validate_prompt_length(cls, v):
        if not v.strip():
            raise ValueError("prompt must not be empty")
        max_length = get_vlm_prompt_max_length()
        if len(v) > max_length:
            raise ValueError(
                f"prompt length {len(v)} exceeds the configured limit "
                f"{max_length}. Set {VLM_PROMPT_MAX_LENGTH_ENV} to a higher "
                "positive integer to allow longer prompts."
            )
        return v

    model: str = Field(
        description="Model to use for this query.",
        examples=["cosmos-reason1"],
        max_length=256,
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
        le=MAX_GENERATION_TOKENS,
        description="The maximum number of tokens to generate in any given call.",
        json_schema_extra={
            "format": "int32",
            "x-env-override": MAX_GENERATION_TOKENS_ENV,
            "x-default-maximum": DEFAULT_MAX_GENERATION_TOKENS,
        },
    )
    min_tokens: int = Field(
        default=None,
        examples=[100],
        ge=1,
        le=MAX_GENERATION_TOKENS,
        description=(
            "Minimum number of tokens to generate before the model is allowed to "
            "stop. Used together with `ignore_eos=true` for fixed-length "
            "generation. The model will not stop on EOS or any stop string before "
            "producing this many output tokens."
        ),
        json_schema_extra={
            "format": "int32",
            "x-env-override": MAX_GENERATION_TOKENS_ENV,
            "x-default-maximum": DEFAULT_MAX_GENERATION_TOKENS,
        },
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
    ignore_eos: Optional[bool] = Field(
        default=None,
        description="Ignore EOS token in the output.",
        examples=[True, False],
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
        ge=-3600,
        le=3600,
        json_schema_extra={"format": "int32"},
    )
    media_info: MediaInfoOffset | None = Field(
        default=None,
        description=(
            "Provide Start and End times offsets for processing part of a video file."
            " Not applicable for live-streaming."
        ),
    )

    num_frames_per_second_or_fixed_frames_chunk: float = Field(
        default=0,
        examples=[10],
        description=(
            "Number of frames per second to use for the VLM, when use_fps_for_chunking is True, "
            "else it will be fixed number of frames per chunk. If use_fps_for_chunking is True, "
            "then this field will be used to determine the number of frames per second to use for the VLM, "
            "else it will be fixed number of frames per chunk. Set to -1 with fixed-frame "
            "chunking to select all decoded frames in the chunk."
        ),
        ge=-1,
        le=256,
        json_schema_extra={"format": "float32"},
    )

    use_fps_for_chunking: Optional[bool] = Field(
        default=False,
        description=(
            "Use FPS for chunking, if True, then num_frames_per_second_or_fixed_frames_chunk "
            "will be interpreted as FPS for sampling frames from the video, "
            "else it will be interpreted as the fixed number of frames per chunk"
        ),
        examples=[True, False],
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
    preserve_reasoning_tags: bool = Field(
        default=False,
        description=(
            "Internal flag for NIM-compatible chat completions. When true, model adapters "
            "preserve raw reasoning tags in the output instead of stripping them into "
            "reasoning_description."
        ),
        exclude=True,
    )
    enable_audio: bool = Field(
        default=False,
        description="Enable transcription of the audio stream in the media",
        examples=[True, False],
    )
    mm_processor_kwargs: Optional[dict] = Field(
        default=None,
        description="Additional keyword arguments for the multimodal processor (e.g., size, shortest_edge, longest_edge).",  # noqa: E501
    )
    media_io_kwargs: Optional[dict] = Field(
        default=None,
        description=(
            'Media I/O pipeline kwargs. For video: {"video": {"fps": 3.0}} or '
            '{"video": {"num_frames": 16}}. Positive fps or num_frames values override '
            "num_frames_per_second_or_fixed_frames_chunk and use_fps_for_chunking. "
            "Use num_frames=-1 to select all decoded frames in the chunk. "
            "fps and num_frames are mutually exclusive."
        ),
        examples=[
            {"video": {"fps": 3.0}},
            {"video": {"num_frames": 16}},
            {"video": {"num_frames": -1}},
        ],
    )

    @field_validator("media_io_kwargs", mode="after")
    def validate_media_io_kwargs(cls, v):
        if v is None:
            return v
        video = v.get("video", {}) if isinstance(v, dict) else {}
        if not isinstance(video, dict):
            raise ValueError("media_io_kwargs['video'] must be a dict")
        has_fps = "fps" in video
        has_num_frames = "num_frames" in video
        if has_fps and has_num_frames:
            raise ValueError("media_io_kwargs.video: fps and num_frames are mutually exclusive")
        if has_fps:
            fps_val = video["fps"]
            if not isinstance(fps_val, (int, float)) or fps_val <= 0:
                raise ValueError("media_io_kwargs.video.fps must be a positive number")
        if has_num_frames:
            nf_val = video["num_frames"]
            if not isinstance(nf_val, (int, float)) or (nf_val <= 0 and nf_val != -1):
                raise ValueError("media_io_kwargs.video.num_frames must be a positive number or -1")
        return v

    @model_validator(mode="after")
    def validate_frame_sampling(self):
        if self.use_fps_for_chunking and self.num_frames_per_second_or_fixed_frames_chunk == -1:
            raise ValueError(
                "num_frames_per_second_or_fixed_frames_chunk=-1 is only valid "
                "when use_fps_for_chunking is false"
            )
        return self

    alert_category: Optional[str] = Field(
        default=None,
        max_length=500,
        pattern=ANY_CHAR_PATTERN,
        description="Alert type identifier (e.g., 'Worker PPE Violation'). Used for incident.category.",
        examples=["Worker PPE Violation", "Pathway Obstruction"],
    )
