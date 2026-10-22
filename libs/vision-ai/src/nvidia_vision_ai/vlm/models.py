# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

"""Pydantic inputs and outputs for the VLM entry points."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated
from typing import Any
from typing import Literal
from urllib.parse import urlsplit

from pydantic import AfterValidator
from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field
from pydantic import SecretStr
from pydantic import model_validator

#: Request dialect. ``rt_vlm`` and ``cosmos_reason_nim`` share RT-VLM's schema;
#: ``vllm`` uses vLLM's video loader fields; ``openai`` is a plain chat completion.
VlmBackend = Literal["rt_vlm", "vllm", "cosmos_reason_nim", "openai"]

# Field bounds, shared with callers that collect these values ahead of a request
# (the CLI validates its config file with the same types).
Timeout = Annotated[int, Field(ge=1, le=3600, description="HTTP timeout per network operation, in seconds.")]
ModelId = Annotated[str, Field(min_length=1, max_length=1024, description="VLM model id.")]
Temperature = Annotated[float, Field(ge=0.0, le=1.0, description="Sampling temperature.")]
MaxTokens = Annotated[int, Field(ge=1, le=1_000_000, description="Maximum tokens to generate.")]
Seed = Annotated[int, Field(ge=1, le=2**32 - 1, description="Sampling seed for reproducible generation.")]
ChunkDuration = Annotated[
    int, Field(ge=0, le=3600, description="Video chunk duration in seconds (RT-VLM); 0 disables chunking.")
]
Fps = Annotated[float, Field(gt=0, le=256, description="Frames sampled per second. Unset: server default.")]
MaxFrames = Annotated[int, Field(ge=1, le=2**31 - 1, description="Upper bound on frames sent to the model.")]
TotalPixels = Annotated[
    int, Field(ge=1, le=2**31 - 1, description="Pixel budget for the whole clip (Qwen3-VL processors).")
]


def _check_base_url(value: str) -> str:
    parsed = urlsplit(value.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("must be an http(s) URL with a host, e.g. http://localhost:8000")
    return value.strip()


def _check_api_key(value: SecretStr) -> SecretStr:
    # A header value with CR/LF/NUL or non-ASCII makes the HTTP stack raise an
    # error that quotes the header -- key included. Reject it before it is sent.
    # The message must never contain the value.
    raw = value.get_secret_value()
    if not raw or any(not (0x21 <= ord(ch) <= 0x7E) for ch in raw):
        raise ValueError("must be non-empty printable ASCII without whitespace (check for a trailing newline)")
    return value


BaseUrl = Annotated[
    str, AfterValidator(_check_base_url), Field(description="OpenAI-compatible endpoint, e.g. http://localhost:8000.")
]
ApiKey = Annotated[SecretStr, AfterValidator(_check_api_key), Field(description="Bearer token.")]


class Endpoint(BaseModel):
    """An OpenAI-compatible endpoint: ``http://host:port``, ``.../v1`` or ``.../v1/chat/completions``."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    base_url: BaseUrl
    api_key: ApiKey | None = None
    #: Applied by httpx per connect/read/write/pool operation, not as a total
    #: deadline: a server that keeps sending within the window can run longer.
    timeout: Timeout = 30


class VlmRequest(BaseModel):
    """Ask one question about one video.

    Exactly one media source: ``media_url`` (sent as ``video_url``) or ``file``
    (read and sent inline as base64). Sampling fields left ``None`` take the
    server's defaults.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    endpoint: Endpoint
    model: ModelId
    backend: VlmBackend = "rt_vlm"
    prompt: str = Field(..., min_length=1, max_length=512_000)
    media_url: str | None = None
    file: Path | None = None
    temperature: Temperature | None = None
    max_tokens: MaxTokens | None = None
    seed: Seed | None = None
    enable_reasoning: bool | None = None
    chunk_duration: ChunkDuration | None = None
    fps: Fps | None = None
    max_frames: MaxFrames | None = None
    total_pixels: TotalPixels | None = None

    @model_validator(mode="after")
    def _one_media_source(self) -> VlmRequest:
        if (self.media_url is None) == (self.file is None):
            raise ValueError("exactly one of media_url or file is required")
        if self.file is not None and not self.file.is_file():
            raise ValueError(f"file does not exist or is not a regular file: {self.file}")
        if self.backend in {"vllm", "openai"} and self.chunk_duration not in (None, 0):
            raise ValueError(f"positive chunk_duration is not supported by the {self.backend} backend")
        return self


class VlmResponse(BaseModel):
    """The model's answer."""

    model_config = ConfigDict(frozen=True)

    answer: str
    model: str
    completion_id: str | None = None
    usage: dict[str, Any] | None = None


class ModelsResponse(BaseModel):
    """Model ids an endpoint reports from ``GET /v1/models``."""

    model_config = ConfigDict(frozen=True)

    models: list[str]
