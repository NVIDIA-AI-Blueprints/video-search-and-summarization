# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""``vision-ai vlm`` -- ask a VLM one question about one video.

    vision-ai configure vlm set base_url=http://localhost:8000 backend=vllm
    vision-ai vlm --prompt "What happens?" --media-url https://example.com/clip.mp4
    vision-ai vlm --prompt "What happens?" --file ./clip.mp4

The API key comes from ``VISION_AI_VLM_API_KEY`` or the config file, never a flag.
With no ``model`` configured, the endpoint's only listed model is used.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from typing import ClassVar

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field
from pydantic import model_validator

from nvidia_vision_ai import ConfigurationError
from nvidia_vision_ai.tool import Tool
from nvidia_vision_ai.vlm import Endpoint
from nvidia_vision_ai.vlm import VlmBackend
from nvidia_vision_ai.vlm import VlmRequest
from nvidia_vision_ai.vlm import VlmResponse
from nvidia_vision_ai.vlm import analyze
from nvidia_vision_ai.vlm import list_models
from nvidia_vision_ai.vlm.models import ApiKey
from nvidia_vision_ai.vlm.models import BaseUrl
from nvidia_vision_ai.vlm.models import ChunkDuration
from nvidia_vision_ai.vlm.models import Fps
from nvidia_vision_ai.vlm.models import MaxFrames
from nvidia_vision_ai.vlm.models import MaxTokens
from nvidia_vision_ai.vlm.models import ModelId
from nvidia_vision_ai.vlm.models import Seed
from nvidia_vision_ai.vlm.models import Temperature
from nvidia_vision_ai.vlm.models import Timeout
from nvidia_vision_ai.vlm.models import TotalPixels
from nvidia_vision_ai_cli.options import NEGATIVE_FLAG

#: Backends that take a frame rate or a frame count, never both (HTTP 400).
_ONE_OF_FPS_OR_FRAMES = frozenset({"rt_vlm", "cosmos_reason_nim"})


class VlmSettings(BaseModel):
    """Persistent VLM parameters (``~/.vision-ai/vlm/config.json``, ``VISION_AI_VLM_*``, flags)."""

    model_config = ConfigDict(extra="forbid")

    base_url: BaseUrl | None = None
    api_key: ApiKey | None = None
    model: ModelId | None = None
    backend: VlmBackend = Field("rt_vlm", description="Request dialect.")
    timeout: Timeout = 30
    temperature: Temperature | None = None
    max_tokens: MaxTokens | None = None
    seed: Seed | None = None
    enable_reasoning: bool | None = Field(
        None,
        description="Enable or disable reasoning output.",
        json_schema_extra={NEGATIVE_FLAG: "--disable-reasoning"},
    )
    chunk_duration: ChunkDuration | None = None
    fps: Fps | None = None
    max_frames: MaxFrames | None = None
    total_pixels: TotalPixels | None = None


class VlmInput(BaseModel):
    """One question about one video: exactly one of ``media_url`` or ``file``."""

    model_config = ConfigDict(extra="forbid")

    prompt: str = Field(..., min_length=1, max_length=512_000, description="Question to ask about the video.")
    media_url: str | None = Field(None, description="HTTP(S) video URL the VLM fetches itself.")
    file: Path | None = Field(None, description="Local video file, sent inline as base64.")

    @model_validator(mode="after")
    def _one_media_source(self) -> VlmInput:
        if (self.media_url is None) == (self.file is None):
            raise ValueError("exactly one of --media-url or --file is required")
        if self.file is not None and not self.file.is_file():
            raise ValueError(f"--file does not exist or is not a regular file: {self.file}")
        return self


def _endpoint(settings: VlmSettings) -> Endpoint:
    if not settings.base_url:
        raise ConfigurationError(
            "vlm base_url is not set: run `vision-ai configure vlm set base_url=<url>` or export VISION_AI_VLM_BASE_URL"
        )
    return Endpoint(base_url=settings.base_url, api_key=settings.api_key, timeout=settings.timeout)


async def _only_model(endpoint: Endpoint) -> str:
    models = (await list_models(endpoint)).models
    if len(models) == 1:
        return models[0]
    listed = ", ".join(models[:10]) + (", ..." if len(models) > 10 else "") if models else "none"
    raise ConfigurationError(
        f"the endpoint lists {len(models)} models ({listed}); "
        "pass --model or run `vision-ai configure vlm set model=<id>`"
    )


class VlmTool(Tool):
    name: ClassVar[str] = "vlm"
    summary: ClassVar[str] = "Ask a VLM a question about a video"
    Settings: ClassVar[type[BaseModel]] = VlmSettings
    Input: ClassVar[type[BaseModel]] = VlmInput

    def prepare(self, inputs: BaseModel, settings: BaseModel, explicit: frozenset[str]) -> VlmSettings:  # noqa: ARG002
        assert isinstance(settings, VlmSettings)
        _endpoint(settings)
        if settings.backend in {"vllm", "openai"} and settings.chunk_duration not in (None, 0):
            raise ValueError(f"positive chunk_duration is not supported by the {settings.backend} backend")
        # RT-VLM and the NIM take a rate or a frame count, not both. A frame
        # count asked for on this call (a benchmark's fixed --max-frames)
        # replaces a rate only inherited from env or config.
        if (
            settings.backend in _ONE_OF_FPS_OR_FRAMES
            and "max_frames" in explicit
            and "fps" not in explicit
            and settings.fps is not None
        ):
            return settings.model_copy(update={"fps": None})
        return settings

    async def execute(self, inputs: BaseModel, settings: BaseModel) -> VlmResponse:
        assert isinstance(inputs, VlmInput)
        assert isinstance(settings, VlmSettings)
        endpoint = _endpoint(settings)
        sampling = settings.model_dump(
            include={
                "backend",
                "temperature",
                "max_tokens",
                "seed",
                "enable_reasoning",
                "chunk_duration",
                "fps",
                "max_frames",
                "total_pixels",
            }
        )
        request = VlmRequest(
            endpoint=endpoint,
            model=settings.model or await _only_model(endpoint),
            prompt=inputs.prompt,
            media_url=inputs.media_url,
            file=inputs.file,
            **sampling,
        )
        return await analyze(request)

    async def check(self, settings: BaseModel) -> dict[str, Any]:
        assert isinstance(settings, VlmSettings)
        endpoint = _endpoint(settings)
        models = (await list_models(endpoint)).models
        report: dict[str, Any] = {"base_url": settings.base_url, "models": models}
        if settings.model is not None:
            report["model"] = settings.model
            report["model_listed"] = settings.model in models
        return report


TOOL = VlmTool()
