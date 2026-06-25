# SPDX-FileCopyrightText: Copyright (c) 2024-2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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
"""NIM-compatible API Models for OpenAI compatibility."""

from typing import List, Literal, Optional, Union
from uuid import UUID

from pydantic import Field, model_validator

from .common import (
    ANY_CHAR_PATTERN,
    DEFAULT_MAX_GENERATION_TOKENS,
    MAX_GENERATION_TOKENS,
    MAX_GENERATION_TOKENS_ENV,
    CommonBaseModel,
)


class ImageUrl(CommonBaseModel):
    """Image URL object for multimodal content."""

    url: str = Field(
        description="URL of the image or base64 encoded image data",
        max_length=10000000,  # Support large base64-encoded images (up to ~7.5MB raw)
        pattern=ANY_CHAR_PATTERN,
    )
    detail: Optional[Literal["auto", "low", "high"]] = Field(
        default="auto",
        description="Image detail level",
    )


class VideoUrl(CommonBaseModel):
    """Video URL object for multimodal content."""

    url: str = Field(
        description="URL of the video",
        max_length=10000000,
        pattern=ANY_CHAR_PATTERN,
    )


class ContentPart(CommonBaseModel):
    """A content part in multimodal message format."""

    type: Literal["text", "image_url", "video_url"] = Field(
        description="The type of content part",
    )
    text: Optional[str] = Field(
        default=None,
        description="Text content (when type is 'text')",
        max_length=100000,
        pattern=ANY_CHAR_PATTERN,
    )
    image_url: Optional[ImageUrl] = Field(
        default=None,
        description="Image URL object (when type is 'image_url')",
    )
    video_url: Optional[VideoUrl] = Field(
        default=None,
        description="Video URL object (when type is 'video_url')",
    )

    @model_validator(mode="after")
    def validate_content_type(self) -> "ContentPart":
        """Validate that the appropriate field is set based on type."""
        if self.type == "text" and self.text is None:
            raise ValueError("'text' field is required when type is 'text'")
        if self.type == "image_url" and self.image_url is None:
            raise ValueError("'image_url' field is required when type is 'image_url'")
        if self.type == "video_url" and self.video_url is None:
            raise ValueError("'video_url' field is required when type is 'video_url'")
        return self


class ChatMessage(CommonBaseModel):
    """A message in the chat completion request.

    Supports both simple string content and multimodal content parts.
    Supports turn-by-turn conversations with system, user, and assistant messages.
    """

    role: Literal["system", "user", "assistant"] = Field(
        description=(
            "The role of the message author. "
            "Use 'system' for system instructions, 'user' for user messages, "
            "and 'assistant' for previous assistant responses in multi-turn conversations."
        ),
        examples=["user", "assistant", "system"],
    )
    content: Union[str, List[ContentPart]] = Field(
        description=(
            "The contents of the message. Can be a string or list of content parts "
            "for multimodal input."
        ),
        json_schema_extra={
            "anyOf": [
                {"type": "string", "maxLength": 1000000, "pattern": ANY_CHAR_PATTERN},
                {
                    "type": "array",
                    "maxItems": 100,
                    "items": {"$ref": "#/components/schemas/ContentPart"},
                },
            ]
        },
    )
    reasoning_description: Optional[str] = Field(
        default=None,
        description=(
            "Reasoning text produced by the model when reasoning is enabled. "
            "Only populated on assistant response messages."
        ),
        max_length=1000000,
        pattern=ANY_CHAR_PATTERN,
    )

    def get_text_content(self) -> str:
        """Extract text content from message, regardless of format."""
        if isinstance(self.content, str):
            return self.content
        # Extract text from multimodal content parts
        text_parts = []
        for part in self.content:
            if part.type == "text" and part.text:
                text_parts.append(part.text)
        return "\n".join(text_parts)

    def get_media_urls(self) -> tuple[list[str], list[str]]:
        """Extract image and video URLs from multimodal content.

        Returns:
            Tuple of (image_urls, video_urls)
        """
        if isinstance(self.content, str):
            return [], []

        image_urls = []
        video_urls = []
        for part in self.content:
            if part.type == "image_url" and part.image_url:
                image_urls.append(part.image_url.url)
            elif part.type == "video_url" and part.video_url:
                video_urls.append(part.video_url.url)
        return image_urls, video_urls


class ChatCompletionRequest(CommonBaseModel):
    """OpenAI-compatible chat completion request.

    Supports turn-by-turn conversations. Include the full conversation history
    in the messages array, with assistant messages from previous turns to maintain context.
    """

    messages: List[ChatMessage] = Field(
        description=(
            "A list of messages comprising the conversation so far. "
            "For multi-turn conversations, include all previous user and assistant messages. "
            "The conversation will be formatted as 'User: ... Assistant: ... User: ...' "
            "to provide full context to the model."
        ),
        min_length=1,
        max_length=100,
    )
    model: str = Field(
        description="The model to use.",
        max_length=256,
        pattern=ANY_CHAR_PATTERN,
    )
    frequency_penalty: Optional[float] = Field(
        default=0.0,
        description=(
            "Number between -2.0 and 2.0. Positive values penalize new tokens "
            "based on their existing frequency in the text so far."
        ),
        ge=-2.0,
        le=2.0,
    )
    logit_bias: Optional[dict[str, float]] = Field(
        default=None,
        description="Modify the likelihood of specified tokens appearing in the completion.",
    )
    logprobs: Optional[bool] = Field(
        default=None,
        description="Whether to return log probabilities of the output tokens or not.",
    )
    top_logprobs: Optional[int] = Field(
        default=None,
        description="An integer between 0 and 5 specifying the number of most likely tokens.",
        ge=0,
        le=5,
        json_schema_extra={
            "anyOf": [
                {"type": "integer", "format": "int32", "minimum": 0, "maximum": 5},
                {"type": "null"},
            ]
        },
    )
    max_tokens: Optional[int] = Field(
        default=None,
        description="The maximum number of tokens to generate.",
        ge=1,
        le=MAX_GENERATION_TOKENS,
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
    n: Optional[int] = Field(
        default=1,
        description="How many chat completion choices to generate for each input message.",
        ge=1,
        le=1,
        json_schema_extra={
            "anyOf": [
                {"type": "integer", "format": "int32", "minimum": 1, "maximum": 1},
                {"type": "null"},
            ]
        },
    )
    presence_penalty: Optional[float] = Field(
        default=0.0,
        description=(
            "Number between -2.0 and 2.0. Positive values penalize new tokens "
            "based on whether they appear in the text so far."
        ),
        ge=-2.0,
        le=2.0,
    )
    response_format: Optional[dict] = Field(
        default=None,
        description="An object specifying the format that the model must output.",
    )
    seed: Optional[int] = Field(
        default=None,
        description="If specified, the system will make a best effort to sample deterministically.",
        ge=1,
        le=2**32 - 1,
        json_schema_extra={
            "anyOf": [
                {"type": "integer", "format": "int64", "minimum": 1, "maximum": 4294967295},
                {"type": "null"},
            ]
        },
    )
    stop: Optional[Union[str, List[str]]] = Field(
        default=None,
        description="Up to 4 sequences where the API will stop generating further tokens.",
        json_schema_extra={
            "anyOf": [
                {"type": "string", "maxLength": 1000, "pattern": ANY_CHAR_PATTERN},
                {
                    "type": "array",
                    "maxItems": 4,
                    "items": {"type": "string", "maxLength": 1000, "pattern": ANY_CHAR_PATTERN},
                },
            ]
        },
    )
    stream: Optional[bool] = Field(
        default=False,
        description="If set, partial message deltas will be sent as server-sent events.",
    )
    stream_options: Optional[dict] = Field(
        default=None,
        description="Options for streaming response.",
    )
    temperature: Optional[float] = Field(
        default=None,
        description="What sampling temperature to use, between 0 and 2.",
        ge=0.0,
        le=2.0,
    )
    top_p: Optional[float] = Field(
        default=None,
        description="An alternative to sampling with temperature.",
        ge=0.0,
        le=1.0,
    )
    top_k: Optional[int] = Field(
        default=None,
        description="The number of highest probability vocabulary tokens to keep.",
        ge=1,
        le=1000,
        json_schema_extra={
            "anyOf": [
                {"type": "integer", "format": "int32", "minimum": 1, "maximum": 1000},
                {"type": "null"},
            ]
        },
    )
    ignore_eos: Optional[bool] = Field(
        default=None,
        description="Ignore EOS token in the output.",
    )
    min_tokens: Optional[int] = Field(
        default=None,
        description="Minimum number of tokens to generate before the model is allowed to stop.",
        ge=1,
        le=MAX_GENERATION_TOKENS,
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
    max_completion_tokens: Optional[int] = Field(
        default=None,
        description="The maximum number of tokens to generate.",
        ge=1,
        le=MAX_GENERATION_TOKENS,
        json_schema_extra={
            "anyOf": [
                {
                    "type": "integer",
                    "format": "int32",
                    "minimum": 1,
                    "maximum": MAX_GENERATION_TOKENS,
                }
            ],
            "x-env-override": MAX_GENERATION_TOKENS_ENV,
            "x-default-maximum": DEFAULT_MAX_GENERATION_TOKENS,
        },
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
    mm_processor_kwargs: Optional[dict] = Field(
        default=None,
        description="Additional keyword arguments for the multimodal processor (e.g., size, shortest_edge, longest_edge).",  # noqa: E501
    )
    # RTVI-specific fields for video/image processing
    id: Optional[Union[UUID, List[UUID]]] = Field(
        default=None,
        description="Unique ID or list of IDs of the file(s)/live-stream(s) to process.",
        json_schema_extra={
            "anyOf": [
                {"type": "string", "format": "uuid"},
                {"type": "array", "maxItems": 100, "items": {"type": "string", "format": "uuid"}},
            ]
        },
    )
    chunk_duration: Optional[int] = Field(
        default=0,
        description="Chunk videos into `chunk_duration` seconds. Set `0` for no chunking",
        ge=0,
        le=3600,
        json_schema_extra={
            "anyOf": [
                {"type": "integer", "format": "int32", "minimum": 0, "maximum": 3600},
                {"type": "null"},
            ]
        },
    )
    chunk_overlap_duration: Optional[int] = Field(
        default=0,
        description="Chunk Overlap Duration Time in Seconds. Set `0` for no overlap",
        ge=-3600,
        le=3600,
        json_schema_extra={
            "anyOf": [
                {"type": "integer", "format": "int32", "minimum": -3600, "maximum": 3600},
                {"type": "null"},
            ]
        },
    )
    enable_audio: Optional[bool] = Field(
        default=False,
        description="Enable transcription of the audio stream in the media",
    )
    enable_reasoning: Optional[bool] = Field(
        default=False,
        description="Enable reasoning for VLM captions generation",
    )
    num_frames_per_second_or_fixed_frames_chunk: Optional[float] = Field(
        default=0,
        description=(
            "Number of frames per second or fixed frames per chunk. Set to -1 with "
            "fixed-frame chunking to select all decoded frames in each chunk."
        ),
        ge=-1,
        le=256,
    )
    use_fps_for_chunking: Optional[bool] = Field(
        default=False,
        description="Use FPS for chunking if True, else fixed frames per chunk",
    )
    vlm_input_width: Optional[int] = Field(
        default=0,
        description="VLM Input Width",
        ge=0,
        le=4096,
        json_schema_extra={
            "anyOf": [
                {"type": "integer", "format": "int32", "minimum": 0, "maximum": 4096},
                {"type": "null"},
            ]
        },
    )
    vlm_input_height: Optional[int] = Field(
        default=0,
        description="VLM Input Height",
        ge=0,
        le=4096,
        json_schema_extra={
            "anyOf": [
                {"type": "integer", "format": "int32", "minimum": 0, "maximum": 4096},
                {"type": "null"},
            ]
        },
    )


class ChatCompletionChoice(CommonBaseModel):
    """A chat completion choice."""

    index: int = Field(
        description="The index of the choice in the list of choices.",
        ge=0,
        le=1000,
        json_schema_extra={"format": "int32"},
    )
    message: ChatMessage = Field(description="A chat completion message.")
    finish_reason: Optional[Literal["stop", "length", "tool_calls"]] = Field(
        default=None,
        description="The reason the model stopped generating tokens.",
    )


class ChatCompletionUsage(CommonBaseModel):
    """Usage statistics for the completion request."""

    prompt_tokens: Optional[int] = Field(
        default=None,
        description="Number of tokens in the prompt.",
        ge=0,
        le=1000000000,
        json_schema_extra={
            "anyOf": [
                {"type": "integer", "format": "int64", "minimum": 0, "maximum": 1000000000},
                {"type": "null"},
            ]
        },
    )
    completion_tokens: Optional[int] = Field(
        default=None,
        description="Number of tokens in the completion.",
        ge=0,
        le=1000000000,
        json_schema_extra={
            "anyOf": [
                {"type": "integer", "format": "int64", "minimum": 0, "maximum": 1000000000},
                {"type": "null"},
            ]
        },
    )
    total_tokens: Optional[int] = Field(
        default=None,
        description="Total number of tokens used in the request.",
        ge=0,
        le=1000000000,
        json_schema_extra={
            "anyOf": [
                {"type": "integer", "format": "int64", "minimum": 0, "maximum": 1000000000},
                {"type": "null"},
            ]
        },
    )


class ChatCompletionResponse(CommonBaseModel):
    """OpenAI-compatible chat completion response."""

    id: str = Field(
        description="A unique identifier for the chat completion.",
        max_length=256,
        pattern=ANY_CHAR_PATTERN,
    )
    object: Literal["chat.completion"] = Field(
        default="chat.completion",
        description="The object type, which is always 'chat.completion'.",
    )
    created: int = Field(
        description="The Unix timestamp (in seconds) of when the chat completion was created.",
        ge=0,
        le=4000000000,
        json_schema_extra={"format": "int64"},
    )
    model: str = Field(
        description="The model used for the chat completion.",
        max_length=256,
        pattern=ANY_CHAR_PATTERN,
    )
    choices: List[ChatCompletionChoice] = Field(
        description="A list of chat completion choices.",
        max_length=1,
    )
    usage: Optional[ChatCompletionUsage] = Field(
        default=None,
        description="Usage statistics for the completion request.",
    )


class CompletionRequest(CommonBaseModel):
    """OpenAI-compatible completion request."""

    prompt: Union[str, List[str]] = Field(
        description="The prompt(s) to generate completions for.",
        json_schema_extra={
            "anyOf": [
                {"type": "string", "maxLength": 100000, "pattern": ANY_CHAR_PATTERN},
                {
                    "type": "array",
                    "maxItems": 100,
                    "items": {"type": "string", "maxLength": 100000, "pattern": ANY_CHAR_PATTERN},
                },
            ]
        },
    )
    model: str = Field(
        description="The model to use.",
        max_length=256,
        pattern=ANY_CHAR_PATTERN,
    )
    max_tokens: Optional[int] = Field(
        default=16,
        description="The maximum number of tokens to generate.",
        ge=1,
        le=MAX_GENERATION_TOKENS,
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
        default=1.0,
        description="What sampling temperature to use.",
        ge=0.0,
        le=2.0,
    )
    top_p: Optional[float] = Field(
        default=1.0,
        description="An alternative to sampling with temperature.",
        ge=0.0,
        le=1.0,
    )
    top_k: Optional[int] = Field(
        default=None,
        description="The number of highest probability vocabulary tokens to keep.",
        ge=1,
        le=1000,
        json_schema_extra={
            "anyOf": [
                {"type": "integer", "format": "int32", "minimum": 1, "maximum": 1000},
                {"type": "null"},
            ]
        },
    )
    n: Optional[int] = Field(
        default=1,
        description="How many completions to generate for each prompt.",
        ge=1,
        le=1,
        json_schema_extra={
            "anyOf": [
                {"type": "integer", "format": "int32", "minimum": 1, "maximum": 1},
                {"type": "null"},
            ]
        },
    )
    stream: Optional[bool] = Field(
        default=False,
        description="If set, partial message deltas will be sent as server-sent events.",
    )
    stop: Optional[Union[str, List[str]]] = Field(
        default=None,
        description="Up to 4 sequences where the API will stop generating further tokens.",
        json_schema_extra={
            "anyOf": [
                {"type": "string", "maxLength": 1000, "pattern": ANY_CHAR_PATTERN},
                {
                    "type": "array",
                    "maxItems": 4,
                    "items": {"type": "string", "maxLength": 1000, "pattern": ANY_CHAR_PATTERN},
                },
            ]
        },
    )
    seed: Optional[int] = Field(
        default=None,
        description="If specified, the system will make a best effort to sample deterministically.",
        ge=1,
        le=2**32 - 1,
        json_schema_extra={
            "anyOf": [
                {"type": "integer", "format": "int64", "minimum": 1, "maximum": 4294967295},
                {"type": "null"},
            ]
        },
    )


class CompletionChoice(CommonBaseModel):
    """A completion choice."""

    text: str = Field(
        description="The generated text.",
        max_length=1000000,
        pattern=ANY_CHAR_PATTERN,
    )
    index: int = Field(
        description="The index of the choice in the list of choices.",
        ge=0,
        le=1000,
        json_schema_extra={"format": "int32"},
    )
    finish_reason: Optional[Literal["stop", "length"]] = Field(
        default=None,
        description="The reason the model stopped generating tokens.",
    )


class CompletionResponse(CommonBaseModel):
    """OpenAI-compatible completion response."""

    id: str = Field(
        description="A unique identifier for the completion.",
        max_length=256,
        pattern=ANY_CHAR_PATTERN,
    )
    object: Literal["text_completion"] = Field(
        default="text_completion",
        description="The object type, which is always 'text_completion'.",
    )
    created: int = Field(
        description="The Unix timestamp (in seconds) of when the completion was created.",
        ge=0,
        le=4000000000,
        json_schema_extra={"format": "int64"},
    )
    model: str = Field(
        description="The model used for the completion.",
        max_length=256,
        pattern=ANY_CHAR_PATTERN,
    )
    choices: List[CompletionChoice] = Field(
        description="A list of completion choices.",
        max_length=1,
    )
    usage: Optional[ChatCompletionUsage] = Field(
        default=None,
        description="Usage statistics for the completion request.",
    )


class VersionResponse(CommonBaseModel):
    """Version information response."""

    release: str = Field(
        description="Service release version.",
        max_length=64,
        pattern=ANY_CHAR_PATTERN,
    )
    api: str = Field(
        description="API version.",
        max_length=64,
        pattern=ANY_CHAR_PATTERN,
    )


class ManifestResponse(CommonBaseModel):
    """Manifest information response."""

    version: str = Field(
        description="Service version.",
        max_length=64,
        pattern=ANY_CHAR_PATTERN,
    )
    model: str = Field(
        description="Model identifier.",
        max_length=256,
        pattern=ANY_CHAR_PATTERN,
    )
