# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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
"""Video Embeddings API Models."""

import ipaddress
import re
import socket
from typing import Annotated, Dict, List, Optional
from urllib.parse import urlparse
from uuid import UUID

from pydantic import Field, field_validator

from common.logger import logger

from .common import (
    ANY_CHAR_PATTERN,
    AWS_S3_OBJECT_URL_PATTERN,
    AWS_S3_URL_PATTERN,
    BLOCKED_IP_RANGES,
    DATA_URL_HEADER_PATTERN,
    HTTP_S3_DATA_OR_FILE_URL_PATTERN,
    HTTP_URL_PATTERN,
    MAX_DATA_URL_SERIALIZED_LENGTH,
    TIMESTAMP_PATTERN,
    CommonBaseModel,
    CompletionUsage,
    MediaInfoOffset,
    MediaInfoTimeStamp,
    StreamOptions,
)
from .file import MediaType

TEXT_INPUT_MAX_LENGTH = 1000
TEXT_INPUT_MAX_ITEMS = 100

EMBEDDING_METRIC_MAX_VALUE = 2147483647


BLOCKED_HOSTNAMES = [
    "localhost",
    "metadata.google.internal",  # GCP metadata
    "metadata",
]


def _validate_ip_against_blocked_ranges(
    ip: ipaddress.IPv4Address | ipaddress.IPv6Address, context: str
) -> None:
    """Validate that an IP address is not in blocked ranges or reserved categories.

    Args:
        ip: The IP address to validate
        context: Context string for error messages (e.g., IP itself or "hostname resolves to")

    Raises:
        ValueError: If the IP is blocked or in a reserved category
    """
    # Check against blocked IP ranges
    for blocked_range in BLOCKED_IP_RANGES:
        if ip in blocked_range:
            raise ValueError(
                f"{context} {ip} is not allowed for security reasons "
                f"(SSRF protection: {blocked_range})"
            )

    # Additional checks for special IPs
    if ip.is_loopback:
        raise ValueError(f"{context} {ip} is a loopback address (SSRF protection)")
    if ip.is_link_local:
        raise ValueError(f"{context} {ip} is a link-local address (SSRF protection)")
    if ip.is_reserved:
        raise ValueError(f"{context} {ip} is a reserved IP address (SSRF protection)")
    if ip.is_multicast:
        raise ValueError(f"{context} {ip} is a multicast address (SSRF protection)")


def validate_url_against_ssrf(url: str) -> None:
    """Validate URL to prevent SSRF attacks.

    Args:
        url: The URL to validate

    Raises:
        ValueError: If the URL poses an SSRF risk
    """
    if not url or url.startswith("s3://"):
        # S3 URLs are handled separately and are considered safe
        return

    parsed = urlparse(url)

    if parsed.scheme not in ["http", "https"]:
        raise ValueError(
            f"Unsupported URL scheme: {parsed.scheme}. Only http, https, and s3 are allowed."
        )

    hostname = parsed.hostname
    if not hostname:
        raise ValueError("Invalid URL: missing hostname")

    # Check against blocked hostnames
    hostname_lower = hostname.lower()
    for blocked in BLOCKED_HOSTNAMES:
        if blocked in hostname_lower:
            raise ValueError(
                f"Access to '{hostname}' is not allowed for security reasons (SSRF protection)"
            )

    # Check if hostname is an IP address
    is_ip = False
    try:
        ip = ipaddress.ip_address(hostname)
        is_ip = True
    except (ValueError, ipaddress.AddressValueError):
        pass  # Not an IP address — fall through to DNS resolution

    if is_ip:
        # It's a raw IP — validate against blocked ranges and return
        _validate_ip_against_blocked_ranges(ip, "Access to IP address")
        return

    # Not a valid IP address, it's a hostname — resolve via DNS
    try:
        addr_info = socket.getaddrinfo(hostname, None, socket.AF_UNSPEC, socket.SOCK_STREAM)

        for result in addr_info:
            ip_str = result[4][0]
            # Remove zone index for IPv6 if present
            ip_str = ip_str.split("%")[0]

            try:
                resolved_ip = ipaddress.ip_address(ip_str)
                _validate_ip_against_blocked_ranges(
                    resolved_ip, f"Hostname '{hostname}' resolves to"
                )
            except (ValueError, ipaddress.AddressValueError) as parse_err:
                # ValueError from ip_address() on malformed IPs;
                # AddressValueError on some Python versions
                logger.warning("Could not parse resolved IP %s: %s", ip_str, parse_err)
                continue
            # Let ValueError from validation propagate

    except socket.gaierror as e:
        raise ValueError(f"Cannot resolve hostname '{hostname}': {e}") from e


# ===================== Models required by /generate_embeddings API


class TextEmbeddingsQuery(CommonBaseModel):
    """Text Embeddings Query Request Fields."""

    text_input: str | List[str] = Field(
        description="Text input to generate embeddings for",
        examples=["Hello, world!", ["Hello, world!", "Hello, world!"]],
        json_schema_extra={
            "anyOf": [
                {"type": "string", "maxLength": TEXT_INPUT_MAX_LENGTH, "pattern": ANY_CHAR_PATTERN},
                {
                    "type": "array",
                    "items": {
                        "type": "string",
                        "maxLength": TEXT_INPUT_MAX_LENGTH,
                        "pattern": ANY_CHAR_PATTERN,
                    },
                    "maxItems": TEXT_INPUT_MAX_ITEMS,
                },
            ]
        },
    )

    @field_validator("text_input", mode="after")
    def check_text_input(cls, v):
        if isinstance(v, list) and len(v) > TEXT_INPUT_MAX_ITEMS:
            raise ValueError("List of text inputs must not exceed 100 items")
        if isinstance(v, str) and len(v) > TEXT_INPUT_MAX_LENGTH:
            raise ValueError("Text input must not exceed 1000 characters")
        return v

    @property
    def text_input_list(self) -> List[str]:
        return [self.text_input] if isinstance(self.text_input, str) else self.text_input

    model: str = Field(
        description="The model used for the Text Embeddings generation.",
        examples=["cosmos-embed1-448p"],
        max_length=256,
        pattern=ANY_CHAR_PATTERN,
    )


class TextEmbeddingsResponse(CommonBaseModel):
    """Text Embedding response for a single text input."""

    text_input: str = Field(
        description="Text input to generate embeddings for",
        examples=["Hello, world!"],
        max_length=TEXT_INPUT_MAX_LENGTH,
        pattern=ANY_CHAR_PATTERN,
    )
    embeddings: List[float] = Field(
        description="Embeddings for this text input",
        max_length=10000,
    )


class TextEmbeddingsCompletionResponse(CommonBaseModel):
    """Text Embeddings response."""

    id: UUID = Field(description="Unique ID for the query")
    created: int = Field(
        json_schema_extra={"format": "int64"},
        ge=0,
        le=4000000000,
        examples=[1717405636],
        description="The Unix timestamp (in seconds) of when the Text Embeddings request was created.",
    )
    model: str = Field(
        description="The model used for the Text Embeddings generation.",
        examples=["cosmos-embed1-448p"],
        max_length=256,
        pattern=ANY_CHAR_PATTERN,
    )
    data: List[TextEmbeddingsResponse] = Field(
        description="List of individual text responses with embeddings",
        default=[],
        max_length=10000,
    )


class VideoEmbeddingsQuery(CommonBaseModel):
    """Video Embeddings Query Request Fields."""

    id: UUID | List[UUID] = Field(
        description="Unique ID or list of IDs of the file(s)/live-stream(s) to generate video embeddings for",
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

    # Serialized length cap (see MAX_DATA_URL_SERIALIZED_LENGTH): 4 GiB encoded base64 string
    # (~3 GiB decoded), separate from the decoded limit enforced in AssetManager.
    url: str | None = Field(
        default=None,
        description=(
            "URL of the video to generate embeddings for. "
            "Supports HTTP/HTTPS, S3 (s3://), file:// (local path), and RFC 2397 data: URIs "
            "(e.g. data:video/mp4;base64,<encoded>). "
            "file:// URLs require FILE_URL_ALLOWED_DIRS to be set and are restricted to "
            "paths within those directories. "
            "For data: URIs, the serialized string length is capped separately from the decoded "
            "file size limit applied when saving the asset."
        ),
        max_length=MAX_DATA_URL_SERIALIZED_LENGTH,
        examples=[
            "https://www.example.com/video.mp4",
            "s3://bucket/video.mp4",
            "file:///data/videos/video.mp4",
            "data:video/mp4;base64,AAAA...",
        ],
        pattern=HTTP_S3_DATA_OR_FILE_URL_PATTERN,
    )

    media_type: MediaType | None = Field(
        default="video",
        description="Media type (image / video) for the url input. Default is video.",
        examples=["image", "video"],
    )
    creation_time: str | None = Field(
        default=None,
        description=(
            "Creation time of the file in ISO8601 format."
            "If provided, this offsets the frame times in the response. "
            "If not provided, the frame times will be relative to the start of the file."
        ),
        min_length=24,
        max_length=24,
        examples=["2024-06-09T18:32:11.123Z"],
        pattern=TIMESTAMP_PATTERN,
    )

    url_headers: Optional[
        Dict[str, Annotated[str, Field(max_length=8192, pattern=r"^[^\r\n]*$")]]
    ] = Field(
        default=None,
        description=(
            "Optional HTTP headers for URL download (e.g., authorization). "
            "Overrides server-level ASSET_DOWNLOAD_AUTH_TOKENS for this request. "
            "Only sent to the original host over HTTPS."
        ),
        json_schema_extra={"nullable": True},
        examples=[{"Authorization": "Basic dXNlcjp0b2tlbg=="}],
    )

    @field_validator("media_type", mode="after")
    def check_media_type(cls, v, info):
        if info.data.get("url") and v not in ["image", "video"]:
            raise ValueError("Media type must be either image or video if url is provided")
        return v

    @field_validator("url", mode="after")
    def check_url(cls, v, info):
        if not v:
            return v

        # RFC 2397 data: URIs are self-contained encoded payloads — no network
        # access occurs, so SSRF validation is not applicable.
        if v.startswith("data:"):
            if "," not in v:
                raise ValueError(
                    "Invalid data URL: missing ',' separator between header and payload."
                )
            header, _ = v.split(",", 1)
            if not re.match(DATA_URL_HEADER_PATTERN, header):
                raise ValueError(
                    f"Invalid data URL header '{header}'. "
                    "Expected format: data:<type>/<subtype>[;parameters][;base64]"
                )
            return v

        # file:// URLs are local filesystem paths — no network access, no SSRF risk.
        # Runtime path safety (FILE_URL_ALLOWED_DIRS allowlist + realpath traversal check)
        # is enforced in the server handler.
        if v.startswith("file://"):
            return v

        # For HTTP/HTTPS and S3: validate format then check for SSRF threats.
        if not (
            re.match(AWS_S3_URL_PATTERN, v)
            or re.match(AWS_S3_OBJECT_URL_PATTERN, v)
            or re.match(HTTP_URL_PATTERN, v)
        ):
            raise ValueError(f"Invalid URL format: {v}. Must be a valid HTTP/HTTPS or AWS S3 URL.")

        try:
            validate_url_against_ssrf(v)
        except ValueError as exc:
            raise ValueError(f"URL security validation failed: {str(exc)}") from exc

        return v

    @property
    def get_query_json(self: CommonBaseModel) -> dict:
        return self.model_dump(mode="json")

    model: str = Field(
        description="Model to use for this query.",
        examples=["cosmos-embed1-448p"],
        max_length=256,
        pattern=ANY_CHAR_PATTERN,
    )

    # response_format: ResponseFormat = Field(
    #     description="An object specifying the format that the model must output.",
    #     default=ResponseFormat(type=ResponseType.TEXT),
    #     examples=[
    #         ResponseFormat(type=ResponseType.TEXT),
    #         ResponseFormat(type=ResponseType.JSON_OBJECT),
    #     ],
    # )

    stream: bool = Field(
        default=False,
        description=(
            "If set, partial message deltas containing embeddings for processed chunks will be sent."
            " Embeddings will be sent as data-only [server-sent events]"
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
    # TODO: Check if this can be supported
    # num_frames_per_second_or_fixed_frames_chunk: int = Field(
    #     default=0,
    #     examples=[10],
    #     description="Number of frames per chunk to use for the VLM",
    #     ge=0,
    #     le=256,
    #     json_schema_extra={"format": "int32"},
    # )
    # model_input_width: int = Field(
    #     default=0,
    #     examples=[256],
    #     description="Embeddings Model Input Width",
    #     ge=0,
    #     le=4096,
    #     json_schema_extra={"format": "int32"},
    # )
    # model_input_height: int = Field(
    #     default=0,
    #     examples=[256],
    #     description="Embeddings Model Input Height",
    #     ge=0,
    #     le=4096,
    #     json_schema_extra={"format": "int32"},
    # )


class VideoEmbeddingsResponse(CommonBaseModel):
    """Represents a Video Embedding response for a single chunk."""

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
        examples=["2024-05-30T01:41:25.000Z", "15.5"],
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
    embeddings: List[float] = Field(
        description="Embeddings for this chunk",
        max_length=10000,
    )
    decode_latency_ms: Optional[float] = Field(
        default=None,
        description="Video decode latency for this chunk in milliseconds.",
        json_schema_extra={"format": "double", "nullable": True},
        ge=0,
    )
    inference_latency_ms: Optional[float] = Field(
        default=None,
        description="Embedding model inference latency for this chunk in milliseconds.",
        json_schema_extra={"format": "double", "nullable": True},
        ge=0,
    )
    chunk_latency_ms: Optional[float] = Field(
        default=None,
        description=(
            "End-to-end chunk processing latency from decode start to embedding "
            "response in milliseconds."
        ),
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
    frame_count: Optional[
        Annotated[
            int,
            Field(
                ge=0,
                le=EMBEDDING_METRIC_MAX_VALUE,
                json_schema_extra={"format": "int32"},
            ),
        ]
    ] = Field(
        default=None,
        description="Number of selected frames processed for this chunk.",
        json_schema_extra={"nullable": True},
    )


class VideoEmbeddingsCompletionResponse(CommonBaseModel):
    """Represents a Video Embeddings response."""

    id: UUID = Field(description="Unique ID for the query")
    created: int = Field(
        json_schema_extra={"format": "int64"},
        ge=0,
        le=4000000000,
        examples=[1717405636],
        description=(
            "The Unix timestamp (in seconds) of when the Video Embeddings request" " was created."
        ),
    )
    model: str = Field(
        description="The model used for the Video Embeddings generation.",
        examples=["cosmos-embed1-448p"],
        max_length=256,
        pattern=ANY_CHAR_PATTERN,
    )
    media_info: MediaInfoTimeStamp | MediaInfoOffset = Field(
        description="Part of the file / live-stream for which this response is applicable."
    )
    usage: CompletionUsage | None = Field(default=None)
    chunk_responses: list[VideoEmbeddingsResponse] = Field(
        description="List of individual chunk responses with embeddings",
        default_factory=list,
        max_length=10000,
    )
