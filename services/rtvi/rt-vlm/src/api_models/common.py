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
"""Common API Models."""

import ipaddress
import os
from datetime import datetime
from typing import Annotated, Literal, Optional

from pydantic import AfterValidator, BaseModel, ConfigDict, Field
from pydantic_core import core_schema

from common.service_exception import ServiceException

TIMESTAMP_PATTERN = r"^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(\.\d{3})?Z$"
FILE_NAME_PATTERN = r"^[A-Za-z0-9_.\- ]*$"
PATH_PATTERN = r"^[A-Za-z0-9_.\-/ ]*$"
DESCRIPTION_PATTERN = r'^[A-Za-z0-9_.\-"\' ,]*$'
UUID_LENGTH = 36
ERROR_CODE_PATTERN = r"^[A-Za-z]*$"
ERROR_MESSAGE_PATTERN = r'^[A-Za-z\-. ,_"\']*$'
ANY_CHAR_PATTERN = r"^(.|\n)*$"
UUID_PATTERN = r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
MAX_GENERATION_TOKENS_ENV = "VLM_MAX_GENERATION_TOKENS"
DEFAULT_MAX_GENERATION_TOKENS = 16 * 1024


def get_max_generation_tokens() -> int:
    raw_value = os.environ.get(MAX_GENERATION_TOKENS_ENV)
    if not raw_value:
        return DEFAULT_MAX_GENERATION_TOKENS
    try:
        max_tokens = int(raw_value)
    except ValueError:
        return DEFAULT_MAX_GENERATION_TOKENS
    if max_tokens < 1:
        return DEFAULT_MAX_GENERATION_TOKENS
    return max_tokens


# API request cap for generated output tokens; model context limits are enforced downstream.
MAX_GENERATION_TOKENS = get_max_generation_tokens()

# Separate patterns for file paths and URLs
FILE_PATH_PATTERN = r"^[A-Za-z0-9_.\-/ ]*$"  # Allows spaces in local file paths
HTTP_URL_PATTERN = r"^https?://[A-Za-z0-9_.\-/:%?#&=+~,]+$"  # No spaces allowed in URLs
HTTP_OR_S3_URL_PATTERN = r"^(https?://[A-Za-z0-9_.\-/:%?#&=+~,()\\[\\]@!$'*;]+)|(s3://[A-Za-z0-9_.\-/:%?#&=+~,()\\[\\]@!$'*;]+)$"  # noqa: E501
# RFC 2397 data: branch — MIME type/subtype (letters case-insensitive on primary type),
# optional ;parameters (name[=value]), mandatory comma before payload, then either
# base64 alphabet (if ;base64 is present) or a restricted URL-safe / percent-encoded set.
# Parameter lists here avoid PCRE look-ahead (unsupported by pydantic's Rust regex). Stricter
# rejection of ambiguous headers (e.g. duplicate ";base64") is enforced in Python via
# DATA_URL_HEADER_PATTERN + field_validator on VideoEmbeddingsQuery.url.
_DATA_URI_BASE64 = (
    r"data:[A-Za-z]+/[A-Za-z0-9+.\-]+"
    r"(?:;[A-Za-z0-9\-]+(?:=[A-Za-z0-9\-]+)?)*"
    r";base64,[A-Za-z0-9+/=]*"
)
_DATA_URI_NON_BASE64 = (
    r"data:[A-Za-z]+/[A-Za-z0-9+.\-]+"
    r"(?:;[A-Za-z0-9\-]+(?:=[A-Za-z0-9\-]+)?)*,"
    r"[A-Za-z0-9%\-._~:/?#\[\]@!$&'()*+,;=]*"
)
# Extends HTTP_OR_S3_URL_PATTERN to also allow RFC 2397 data: URIs (e.g. data:video/mp4;base64,<payload>).
# Regex cannot cap payload size — use MAX_DATA_URL_SERIALIZED_LENGTH with Field(max_length=...) on the field.
HTTP_S3_OR_DATA_URL_PATTERN = (
    r"^("
    r"https?://[A-Za-z0-9_.\-/:%?#&=+~,()\\[\\]@!$'*;]+"  # HTTP/HTTPS
    r"|s3://[A-Za-z0-9_.\-/:%?#&=+~,()\\[\\]@!$'*;]+"  # S3
    r"|" + _DATA_URI_BASE64 + r"|" + _DATA_URI_NON_BASE64 + r")$"
)
# Mediatype through optional trailing ";base64" (no comma). For explicit header checks after split on ",".
DATA_URL_HEADER_PATTERN = (
    r"^data:[A-Za-z]+/[A-Za-z0-9+.\-]+"
    r"(?:;(?!base64(?:;|,|$))[A-Za-z0-9\-]+(?:=[A-Za-z0-9\-]+)?)*"
    r"(?:;base64)?$"
)
# Cap on serialized data: URI length (characters) in JSON requests.
# A 4 GiB base64 string decodes to ~3 GiB of raw data (base64 is 4/3× expansion).
# HTTP/HTTPS/S3 url strings are typically far shorter. Pair with HTTP_S3_OR_DATA_URL_PATTERN.
MAX_DATA_URL_SERIALIZED_LENGTH = 4 * 1024 * 1024 * 1024  # 4 GiB encoded base64 string
FILE_URL_PATTERN = r"^file://[A-Za-z0-9_.\-/ ]+$"  # file:// URLs
# Extends HTTP_S3_OR_DATA_URL_PATTERN to also allow file:// URLs.
# file:// access is gated at runtime by FILE_URL_ALLOWED_DIRS (realpath-based allowlist).
HTTP_S3_DATA_OR_FILE_URL_PATTERN = (
    r"^("
    r"https?://[A-Za-z0-9_.\-/:%?#&=+~,()\\[\\]@!$'*;]+"  # HTTP/HTTPS
    r"|s3://[A-Za-z0-9_.\-/:%?#&=+~,()\\[\\]@!$'*;]+"  # S3
    r"|file://[A-Za-z0-9_.\-/ ]+"  # file:// (safety enforced by FILE_URL_ALLOWED_DIRS)
    r"|" + _DATA_URI_BASE64 + r"|" + _DATA_URI_NON_BASE64 + r")$"
)
URL_PATTERN = (
    r"^(https?://|file://|s3://)[A-Za-z0-9_.\-/:%?#&=+~,()\\[\\]@!$'*; ]+$"  # General URL pattern
)

# Combined pattern: matches either a local file path OR a strict HTTP/HTTPS URL
# This pattern reuses HTTP_URL_PATTERN logic to avoid duplication and ensure URLs prohibit spaces
PATH_OR_URL_PATTERN = r"^(?:[A-Za-z0-9_.\-/ ]*|https?://[A-Za-z0-9_.\-/:%?#&=+~,]+)$"

# Version pattern (SemVer: MAJOR.MINOR.PATCH)
VERSION_PATTERN = r"^\d+\.\d+\.\d+(-[A-Za-z0-9\-\.]+)?(\+[A-Za-z0-9\-\.]+)?$"

AWS_S3_OBJECT_URL_PATTERN = r"""^https?://
        (?:                             # virtual-hosted-style
            (?P<bucket_vh>[a-z0-9.-]+)
            \.s3[.-](?P<region_vh>[a-z0-9-]+)\.amazonaws\.com/
            (?P<key_vh>.+)
        |
            s3[.-](?P<region_ps>[a-z0-9-]+)\.amazonaws\.com/
            (?P<bucket_ps>[a-z0-9.-]+)/(?P<key_ps>.+)   # path-style
        )
        $
    """
AWS_S3_URL_PATTERN = r"^s3://(?P<bucket>[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)*)/(?P<object>[^?\s]+)$"  # noqa: E501

# SSRF Protection: Blocked IP ranges and hostnames
BLOCKED_IP_RANGES = [
    ipaddress.ip_network("127.0.0.0/8"),  # Loopback
    ipaddress.ip_network("::1/128"),  # IPv6 loopback
    ipaddress.ip_network("169.254.0.0/16"),  # Link-local (AWS metadata)
    ipaddress.ip_network("fe80::/10"),  # IPv6 link-local
    ipaddress.ip_network("fc00::/7"),  # IPv6 unique local
    ipaddress.ip_network("0.0.0.0/8"),  # Current network
    ipaddress.ip_network("224.0.0.0/4"),  # Multicast
    ipaddress.ip_network("240.0.0.0/4"),  # Reserved
]


# Common models
class CommonBaseModel(BaseModel):
    """Common pydantic base model that does not allow unsupported params in requests"""

    model_config = ConfigDict(extra="forbid")


class ServiceError(CommonBaseModel):
    """Service Error Information."""

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
def timestamp_validator(v: str, validation_info: core_schema.FieldValidationInfo):
    try:
        # Attempt to parse the RFC3339 timestamp
        datetime.strptime(v, "%Y-%m-%dT%H:%M:%S.%fZ")
        return v
    except ValueError:
        pass

    # Also try without microseconds
    try:
        datetime.strptime(v, "%Y-%m-%dT%H:%M:%SZ")
        return v
    except ValueError as e:
        raise ServiceException(
            f"{validation_info.field_name} be a valid RFC3339 timestamp string",
            "InvalidParameters",
            422,
        ) from e


class MediaInfoOffset(CommonBaseModel):
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


class MediaInfoTimeStamp(CommonBaseModel):
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


class StreamOptions(CommonBaseModel):
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


class CompletionUsage(CommonBaseModel):
    """An optional field that will only be present when you set
    `stream_options: {\"include_usage\": true}` in your request.

    When present, it contains a null value except for the last chunk which contains
    the usage statistics for the entire request.
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
    prompt_tokens: Optional[int] = Field(
        default=None,
        description="Number of tokens in the prompt across all chunks.",
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
        description="Number of tokens in the completion across all chunks.",
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
        description="Total number of tokens used across all chunks.",
        ge=0,
        le=1000000000,
        json_schema_extra={
            "anyOf": [
                {"type": "integer", "format": "int64", "minimum": 0, "maximum": 1000000000},
                {"type": "null"},
            ]
        },
    )


class MetadataResponse(CommonBaseModel):
    """Metadata information about the service."""

    version: str = Field(
        description="Service version.",
        examples=["3.2.0"],
        max_length=64,
        pattern=VERSION_PATTERN,
    )
