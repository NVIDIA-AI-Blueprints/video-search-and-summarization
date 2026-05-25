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
"""Live Stream API Models.

Includes both the original RTVI stream management models (/v1/streams/*)
and CV-compatible models (/v1/stream/*) for cross-service interoperability.
"""

from typing import Optional
from uuid import UUID

from pydantic import Field, field_validator

from .captions import (
    ABSOLUTE_PROMPT_MAX_LENGTH,
    DEFAULT_VLM_PROMPT_MAX_LENGTH,
    DEFAULT_VLM_SYSTEM_PROMPT_MAX_LENGTH,
    VLM_PROMPT_MAX_LENGTH_ENV,
    VLM_SYSTEM_PROMPT_MAX_LENGTH_ENV,
    get_vlm_prompt_max_length,
    get_vlm_system_prompt_max_length,
)
from .common import (
    ANY_CHAR_PATTERN,
    DEFAULT_MAX_GENERATION_TOKENS,
    DESCRIPTION_PATTERN,
    MAX_GENERATION_TOKENS,
    MAX_GENERATION_TOKENS_ENV,
    CommonBaseModel,
)

LIVE_STREAM_URL_PATTERN = r"^rtsp://"
# CV-compatible URL pattern: accepts rtsp://, file://, http://, https://
CV_STREAM_URL_PATTERN = r"^(rtsp://|file://|https?://)"


class AddLiveStream(CommonBaseModel):
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
    place_name: Optional[str] = Field(
        default="",
        description="Name of the place/location where the camera is located.",
        max_length=256,
        examples=["Dock Entrance-East"],
        pattern=ANY_CHAR_PATTERN,
    )
    place_type: Optional[str] = Field(
        default="",
        description="Type of place/location (e.g., warehouse-bay, parking-lot).",
        max_length=256,
        examples=["warehouse-bay"],
        pattern=ANY_CHAR_PATTERN,
    )
    place_lat: Optional[float] = Field(
        default=None,
        description="Latitude of the camera location.",
        examples=[37.3706],
        json_schema_extra={"format": "double"},
        le=90,
        ge=-90,
    )
    place_lon: Optional[float] = Field(
        default=None,
        description="Longitude of the camera location.",
        examples=[-121.9672],
        json_schema_extra={"format": "double"},
        le=180,
        ge=-180,
    )
    place_alt: Optional[float] = Field(
        default=None,
        description="Altitude of the camera location in meters.",
        examples=[0.0],
        json_schema_extra={"format": "double"},
        ge=-1000000,
        le=1000000,
    )
    place_coordinate_x: Optional[float] = Field(
        default=None,
        description="X coordinate of the camera within the place (local coordinates).",
        examples=[12.5],
        json_schema_extra={"format": "double"},
        ge=-1000000,
        le=1000000,
    )
    place_coordinate_y: Optional[float] = Field(
        default=None,
        description="Y coordinate of the camera within the place (local coordinates).",
        examples=[4.2],
        json_schema_extra={"format": "double"},
        ge=-1000000,
        le=1000000,
    )
    id: Optional[UUID] = Field(
        default=None,
        description="The UUID of the live stream. If not provided, a new ID will be generated.",
        examples=["cc06804c-7f11-4865-bb00-6b2db072086f"],
    )
    sensor_name: str = Field(
        default="",
        description="User-defined sensor name. Defaults to empty string if not provided.",
        max_length=256,
        examples=["Camera_123"],
        pattern=ANY_CHAR_PATTERN,
    )


class AddLiveStreamResponse(CommonBaseModel):
    """Response schema for the add live stream API."""

    id: UUID = Field(
        description="The stream identifier, which can be referenced in the API endpoints."
    )


class AddLiveStreams(CommonBaseModel):
    """Parameters required to add multiple live streams."""

    streams: list[AddLiveStream] = Field(
        description="List of live streams to add",
        min_length=1,
        max_length=256,
    )


class AddLiveStreamsResponse(CommonBaseModel):
    """Response schema for the batch add live streams API."""

    results: list[AddLiveStreamResponse] = Field(
        description="List of successfully added stream identifiers",
        min_length=0,
        max_length=256,
    )
    errors: list[dict] = Field(
        default_factory=list,
        description="List of errors for streams that failed to add",
        min_length=0,
        max_length=256,
    )


class DeleteLiveStreamsRequest(CommonBaseModel):
    """Request schema for batch delete live streams API."""

    stream_ids: list[UUID] = Field(
        description="List of stream identifiers to delete",
        min_length=1,
        max_length=256,
    )
    blocking: bool = Field(
        default=False,
        description=(
            "When true, wait for live-stream pipeline drain before returning. "
            "The wait is bounded by drain_timeout_seconds, or by "
            "RTVI_STREAM_DELETE_BLOCKING_TIMEOUT_SEC when no request override is provided."
        ),
    )
    drain_timeout_seconds: Optional[float] = Field(
        default=None,
        description=(
            "Optional per-stream drain timeout override for this batch delete. "
            "When omitted, non-blocking deletes use RTVI_STREAM_DELETE_DRAIN_TIMEOUT_SEC "
            "and blocking deletes use RTVI_STREAM_DELETE_BLOCKING_TIMEOUT_SEC."
        ),
        examples=[300.0],
        json_schema_extra={"format": "double"},
        ge=0,
        le=3600,
    )


class DeleteLiveStreamsResponse(CommonBaseModel):
    """Response schema for the batch delete live streams API."""

    deleted: list[UUID] = Field(
        description="List of successfully deleted stream identifiers",
        min_length=0,
        max_length=256,
    )
    errors: list[dict] = Field(
        default_factory=list,
        description="List of errors for streams that failed to delete",
        min_length=0,
        max_length=256,
    )


class LiveStreamInfo(CommonBaseModel):
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
        le=3600,
    )
    chunk_overlap_duration: int = Field(
        description=(
            "Chunk Overlap Duration Time in Seconds."
            " Chunks would be created at the I-Frame boundry so duration might not be exact."
        ),
        json_schema_extra={"format": "int32"},
        examples=[10],
        ge=-3600,
        le=3600,
    )
    place_name: str = Field(
        default="",
        description="Name of the place/location where the camera is located.",
        max_length=256,
        examples=["Dock Entrance-East"],
        pattern=ANY_CHAR_PATTERN,
    )
    place_type: str = Field(
        default="",
        description="Type of place/location (e.g., warehouse-bay, parking-lot).",
        max_length=256,
        examples=["warehouse-bay"],
        pattern=ANY_CHAR_PATTERN,
    )
    place_lat: Optional[float] = Field(
        default=None,
        description="Latitude of the camera location.",
        examples=[37.3706],
        json_schema_extra={"format": "double"},
        le=90,
        ge=-90,
    )
    place_lon: Optional[float] = Field(
        default=None,
        description="Longitude of the camera location.",
        examples=[-121.9672],
        json_schema_extra={"format": "double"},
        le=180,
        ge=-180,
    )
    place_alt: Optional[float] = Field(
        default=None,
        description="Altitude of the camera location in meters.",
        examples=[0.0],
        json_schema_extra={"format": "double"},
        ge=-1000000,
        le=1000000,
    )
    place_coordinate_x: Optional[float] = Field(
        default=None,
        description="X coordinate of the camera within the place (local coordinates).",
        examples=[12.5],
        json_schema_extra={"format": "double"},
        ge=-1000000,
        le=1000000,
    )
    place_coordinate_y: Optional[float] = Field(
        default=None,
        description="Y coordinate of the camera within the place (local coordinates).",
        examples=[4.2],
        json_schema_extra={"format": "double"},
        ge=-1000000,
        le=1000000,
    )


# =============================================================================
# CV-Compatible Stream API Models (/v1/stream/add, /v1/stream/remove)
#
# These models follow the RTVI-CV payload schema so all three microservices
# (VLM, Embed, CV) can be managed with the same request format.
# Bug: 5881430
# =============================================================================


class StreamMetadata(CommonBaseModel):
    """CV metadata fields plus optional inference parameters for VLM and Embed.

    When sent with a stream/add request, the metadata can carry camera-level
    information (resolution, codec, framerate) and, optionally, inference
    parameters.  The server decides whether to start inference based on:
    - VLM: ``prompt`` is provided → start generate_captions
    - Embed: ``model`` is provided → start generate_video_embeddings
    If neither is set, the stream is added in passthrough mode (no inference).
    """

    # --- CV metadata fields ---
    resolution: Optional[str] = Field(
        default=None,
        description="Camera resolution (e.g. '1920x1080').",
        max_length=32,
        pattern=r"^[0-9]+x[0-9]+$",
        examples=["1920x1080", "3840x2160"],
    )
    codec: Optional[str] = Field(
        default=None,
        description="Video codec used by the camera stream.",
        max_length=64,
        pattern=r"^[A-Za-z0-9._\-]+$",
        examples=["H264", "H265", "MJPEG"],
    )
    framerate: Optional[float] = Field(
        default=None,
        description="Camera stream framerate in FPS.",
        ge=0.1,
        le=240.0,
        examples=[30.0, 25.0, 15.0],
    )

    # --- VLM inference parameters (all optional, mirrors VlmQuery) ---
    # `max_length=` on Field is set to a generous absolute ceiling
    # (`ABSOLUTE_PROMPT_MAX_LENGTH`) so Pydantic emits the constraint inside
    # `anyOf[0]` (the string branch) — required by the OWASP
    # `api4:2023-string-limit` governance check, which inspects every nested
    # string schema. The actual configurable limit is enforced at request time
    # by the @field_validator below, which re-reads the env each request and
    # produces a tailored error message naming the override env var.
    prompt: Optional[str] = Field(
        default=None,
        max_length=ABSOLUTE_PROMPT_MAX_LENGTH,
        description=(
            "Prompt for VLM captions. If provided, inference starts automatically. "
            "Not used by RTVI Embed. "
            f"Default max length is {DEFAULT_VLM_PROMPT_MAX_LENGTH}; set "
            f"{VLM_PROMPT_MAX_LENGTH_ENV} to allow a larger prompt."
        ),
        pattern=ANY_CHAR_PATTERN,
        examples=["Write a concise caption for this warehouse video"],
        json_schema_extra={
            "x-env-override": VLM_PROMPT_MAX_LENGTH_ENV,
            "x-default-max-length": DEFAULT_VLM_PROMPT_MAX_LENGTH,
        },
    )

    @field_validator("prompt", mode="after")
    def validate_prompt_length(cls, v):
        if v is None:
            return v
        max_length = get_vlm_prompt_max_length()
        if len(v) > max_length:
            raise ValueError(
                f"prompt length {len(v)} exceeds the configured limit "
                f"{max_length}. Set {VLM_PROMPT_MAX_LENGTH_ENV} to a higher "
                "positive integer to allow longer prompts."
            )
        return v

    system_prompt: Optional[str] = Field(
        default=None,
        max_length=ABSOLUTE_PROMPT_MAX_LENGTH,
        description=(
            "System prompt for the VLM. Not used by RTVI Embed. "
            f"Default max length is {DEFAULT_VLM_SYSTEM_PROMPT_MAX_LENGTH}; set "
            f"{VLM_SYSTEM_PROMPT_MAX_LENGTH_ENV} to allow a larger prompt."
        ),
        pattern=ANY_CHAR_PATTERN,
        json_schema_extra={
            "x-env-override": VLM_SYSTEM_PROMPT_MAX_LENGTH_ENV,
            "x-default-max-length": DEFAULT_VLM_SYSTEM_PROMPT_MAX_LENGTH,
        },
    )

    @field_validator("system_prompt", mode="after")
    def validate_system_prompt_length(cls, v):
        if v is None:
            return v
        max_length = get_vlm_system_prompt_max_length()
        if len(v) > max_length:
            raise ValueError(
                f"system_prompt length {len(v)} exceeds the configured limit "
                f"{max_length}. Set {VLM_SYSTEM_PROMPT_MAX_LENGTH_ENV} to a higher "
                "positive integer to allow longer system prompts."
            )
        return v

    model: Optional[str] = Field(
        default=None,
        description="Model to use for inference.",
        max_length=256,
        pattern=ANY_CHAR_PATTERN,
    )
    max_tokens: Optional[int] = Field(
        default=None,
        ge=1,
        le=MAX_GENERATION_TOKENS,
        description="Maximum number of tokens to generate per call. Not used by RTVI Embed.",
        json_schema_extra={
            "anyOf": [
                {
                    "type": "integer",
                    "format": "int32",
                    "minimum": 1,
                    "maximum": MAX_GENERATION_TOKENS,
                },
                {"type": "null"},
            ],
            "x-env-override": MAX_GENERATION_TOKENS_ENV,
            "x-default-maximum": DEFAULT_MAX_GENERATION_TOKENS,
        },
    )
    min_tokens: Optional[int] = Field(
        default=None,
        ge=1,
        le=MAX_GENERATION_TOKENS,
        description=(
            "Minimum number of tokens to generate before the model is allowed to stop. "
            "Use with `ignore_eos=true` for fixed-length generation. Not used by RTVI Embed."
        ),
        json_schema_extra={
            "anyOf": [
                {
                    "type": "integer",
                    "format": "int32",
                    "minimum": 1,
                    "maximum": MAX_GENERATION_TOKENS,
                },
                {"type": "null"},
            ],
            "x-env-override": MAX_GENERATION_TOKENS_ENV,
            "x-default-maximum": DEFAULT_MAX_GENERATION_TOKENS,
        },
    )
    temperature: Optional[float] = Field(
        default=None, ge=0, le=1, description="Sampling temperature. Not used by RTVI Embed."
    )
    top_p: Optional[float] = Field(
        default=None, ge=0, le=1, description="Top-p sampling mass. Not used by RTVI Embed."
    )
    top_k: Optional[float] = Field(
        default=None, ge=1, le=1000, description="Top-k filtering. Not used by RTVI Embed."
    )
    ignore_eos: Optional[bool] = Field(
        default=None, description="Ignore EOS token. Not used by RTVI Embed."
    )
    seed: Optional[int] = Field(
        default=None,
        ge=1,
        le=(2**32 - 1),
        description="Seed value. Not used by RTVI Embed.",
        json_schema_extra={
            "anyOf": [
                {"type": "integer", "format": "int64", "minimum": 1, "maximum": 4294967295},
                {"type": "null"},
            ]
        },
    )
    chunk_duration: Optional[int] = Field(
        default=None,
        ge=0,
        le=3600,
        description="Chunk duration (s). 0=no chunking.",
        json_schema_extra={
            "anyOf": [
                {"type": "integer", "format": "int32", "minimum": 0, "maximum": 3600},
                {"type": "null"},
            ]
        },
    )
    chunk_overlap_duration: Optional[int] = Field(
        default=None,
        ge=-3600,
        le=3600,
        description="Chunk overlap (s).",
        json_schema_extra={
            "anyOf": [
                {"type": "integer", "format": "int32", "minimum": -3600, "maximum": 3600},
                {"type": "null"},
            ]
        },
    )
    num_frames_per_second_or_fixed_frames_chunk: Optional[float] = Field(
        default=None,
        ge=0,
        le=256,
        description="FPS (if use_fps_for_chunking) or fixed frame count per chunk. Not used by RTVI Embed.",
    )
    use_fps_for_chunking: Optional[bool] = Field(
        default=None, description="Interpret num_frames as FPS if True. Not used by RTVI Embed."
    )
    vlm_input_width: Optional[int] = Field(
        default=None,
        ge=0,
        le=4096,
        description="VLM input width. Not used by RTVI Embed.",
        json_schema_extra={
            "anyOf": [
                {"type": "integer", "format": "int32", "minimum": 0, "maximum": 4096},
                {"type": "null"},
            ]
        },
    )
    vlm_input_height: Optional[int] = Field(
        default=None,
        ge=0,
        le=4096,
        description="VLM input height. Not used by RTVI Embed.",
        json_schema_extra={
            "anyOf": [
                {"type": "integer", "format": "int32", "minimum": 0, "maximum": 4096},
                {"type": "null"},
            ]
        },
    )
    enable_reasoning: Optional[bool] = Field(
        default=None, description="Enable reasoning. Not used by RTVI Embed."
    )
    enable_audio: Optional[bool] = Field(
        default=None, description="Enable audio transcription. Not used by RTVI Embed."
    )
    alert_category: Optional[str] = Field(
        default=None,
        max_length=500,
        pattern=ANY_CHAR_PATTERN,
        description="Alert category. Not used by RTVI Embed.",
    )
    mm_processor_kwargs: Optional[dict] = Field(
        default=None, description="Extra multimodal processor kwargs. Not used by RTVI Embed."
    )
    response_format_type: Optional[str] = Field(
        default=None,
        max_length=32,
        pattern=r"^(text|json_object)$",
        description="Response format: 'text' or 'json_object'.",
    )
    stream: Optional[bool] = Field(
        default=None, description="If True, return SSE for caption responses."
    )

    @property
    def has_vlm_inference_params(self) -> bool:
        """Return True if VLM inference should start (prompt is provided)."""
        return self.prompt is not None

    @property
    def has_embed_inference_params(self) -> bool:
        """Return True if Embed inference should start (model is provided)."""
        return self.model is not None

    @property
    def has_inference_params(self) -> bool:
        """Return True if any inference should start (VLM or Embed)."""
        return self.has_vlm_inference_params or self.has_embed_inference_params


class StreamAddValue(CommonBaseModel):
    """Value payload for POST /v1/stream/add."""

    camera_id: str = Field(
        description="User-provided unique camera identifier.",
        max_length=256,
        pattern=ANY_CHAR_PATTERN,
        examples=["camera-001"],
    )
    camera_name: Optional[str] = Field(
        default=None,
        max_length=256,
        pattern=ANY_CHAR_PATTERN,
        description="Human-readable camera name.",
    )
    camera_url: str = Field(
        description="Stream URL (rtsp://, file://, http://, https://).",
        max_length=1024,
        pattern=CV_STREAM_URL_PATTERN,
        examples=["rtsp://host:port/live/video"],
    )
    change: str = Field(
        description="Operation type (e.g. 'camera_add').",
        max_length=32,
        pattern=r"^[A-Za-z_]+$",
        examples=["camera_add"],
    )
    creation_time: Optional[str] = Field(
        default=None,
        max_length=64,
        pattern=ANY_CHAR_PATTERN,
        description="ISO 8601 creation timestamp of the stream source.",
    )
    metadata: Optional[StreamMetadata] = Field(
        default=None,
        description="Optional metadata and VLM inference parameters.",
    )


class StreamAddHeaders(CommonBaseModel):
    """Optional headers for stream/add or stream/remove requests."""

    source: Optional[str] = Field(
        default=None,
        max_length=256,
        pattern=ANY_CHAR_PATTERN,
        examples=["vst"],
    )
    created_at: Optional[str] = Field(
        default=None,
        max_length=64,
        pattern=ANY_CHAR_PATTERN,
    )


class StreamAddRequest(CommonBaseModel):
    """Request body for POST /v1/stream/add."""

    key: str = Field(max_length=256, pattern=ANY_CHAR_PATTERN, examples=["sensor"])
    value: StreamAddValue
    headers: Optional[StreamAddHeaders] = Field(default=None)


class StreamAddResponse(CommonBaseModel):
    """Response body for POST /v1/stream/add."""

    camera_id: str = Field(
        description="Camera identifier from the request.",
        max_length=256,
        pattern=ANY_CHAR_PATTERN,
    )
    asset_id: str = Field(
        description="RTVI internal asset UUID.",
        max_length=256,
        pattern=ANY_CHAR_PATTERN,
    )
    status: str = Field(
        description="'processing' or 'added'.",
        max_length=32,
        pattern=r"^(processing|added)$",
        examples=["processing", "added"],
    )
    inference: bool = Field(description="Whether VLM inference was started.")


class StreamRemoveValue(CommonBaseModel):
    """Value payload for POST /v1/stream/remove."""

    camera_id: str = Field(
        max_length=256,
        pattern=ANY_CHAR_PATTERN,
        description="Camera ID to remove.",
    )
    camera_name: Optional[str] = Field(
        default=None,
        max_length=256,
        pattern=ANY_CHAR_PATTERN,
        description="Human-readable camera name.",
    )
    camera_url: Optional[str] = Field(
        default=None,
        max_length=1024,
        pattern=CV_STREAM_URL_PATTERN,
    )
    change: str = Field(
        max_length=32,
        pattern=r"^[A-Za-z_]+$",
        examples=["camera_remove"],
    )
    metadata: Optional[StreamMetadata] = Field(
        default=None,
        description="Optional metadata for the stream.",
    )


class StreamRemoveRequest(CommonBaseModel):
    """Request body for POST /v1/stream/remove."""

    key: str = Field(max_length=256, pattern=ANY_CHAR_PATTERN, examples=["sensor"])
    value: StreamRemoveValue
    headers: Optional[StreamAddHeaders] = Field(default=None)


class StreamRemoveResponse(CommonBaseModel):
    """Response body for POST /v1/stream/remove."""

    camera_id: str = Field(
        description="Camera ID that was removed.",
        max_length=256,
        pattern=ANY_CHAR_PATTERN,
    )
    asset_id: str = Field(
        description="RTVI asset ID that was removed.",
        max_length=256,
        pattern=ANY_CHAR_PATTERN,
    )
    status: str = Field(
        default="removed",
        max_length=32,
        pattern=r"^(removed)$",
    )


class StreamInfo(CommonBaseModel):
    """Information about an active video stream including camera ID and inference status."""

    camera_id: str = Field(
        description="User-provided camera identifier.",
        max_length=256,
        pattern=ANY_CHAR_PATTERN,
    )
    camera_name: Optional[str] = Field(
        default=None,
        max_length=256,
        pattern=ANY_CHAR_PATTERN,
    )
    camera_url: str = Field(
        description="Stream URL.",
        max_length=1024,
        pattern=CV_STREAM_URL_PATTERN,
    )
    asset_id: str = Field(
        description="RTVI internal asset UUID.",
        max_length=256,
        pattern=ANY_CHAR_PATTERN,
    )
    source_id: Optional[int] = Field(
        default=None,
        ge=0,
        le=10000,
        json_schema_extra={
            "anyOf": [
                {"type": "integer", "format": "int32", "minimum": 0, "maximum": 10000},
                {"type": "null"},
            ]
        },
    )
    sensor_id: Optional[str] = Field(
        default=None,
        max_length=256,
        pattern=ANY_CHAR_PATTERN,
    )
    inference_active: bool = Field(description="Whether VLM inference is active.")
    chunk_duration: int = Field(
        default=0,
        json_schema_extra={"format": "int32"},
        ge=0,
        le=3600,
    )
    chunk_overlap_duration: int = Field(
        default=0,
        json_schema_extra={"format": "int32"},
        ge=-3600,
        le=3600,
    )


class StreamInfoResponse(CommonBaseModel):
    """Response body for GET /v1/stream/get-stream-info."""

    status: str = Field(
        default="ok",
        max_length=32,
        pattern=r"^[A-Za-z_]+$",
    )
    stream_count: int = Field(
        description="Number of active streams.",
        json_schema_extra={"format": "int32"},
        ge=0,
        le=100000,
    )
    stream_list: list[StreamInfo] = Field(
        default_factory=list,
        max_length=1024,
    )
