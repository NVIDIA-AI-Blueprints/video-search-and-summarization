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

"""Thin Vision AI tool adapter over the public VSS library APIs."""

from __future__ import annotations

import asyncio
from typing import Literal
from typing import cast

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field

from nvidia_vision_ai import Tool
from vss.ingestion import DEFAULT_INGESTION_PROMPT
from vss.ingestion import IngestionResponse
from vss.ingestion import RequestSource
from vss.ingestion import VlmChunkResponse
from vss.ingestion import ingest
from vss.summarization import CompletionResponse
from vss.summarization import summarize_video


class VssSettings(BaseModel):
    """Deployment settings stay owned by the VSS libraries and environment."""

    model_config = ConfigDict(extra="forbid")


class VssInput(BaseModel):
    """Stable caller-controlled options for one VSS workflow."""

    model_config = ConfigDict(extra="forbid")

    source: str = Field(min_length=1, description="Video path or URI.")
    camera_id: str = Field(default="default", min_length=1)
    backends: str = Field(
        default="rtvi",
        description="Comma-separated ingestion backend names.",
    )
    decoder: Literal["auto", "opencv", "pynv"] | None = None
    single_decode: bool = False
    cuda_ipc: bool | None = None
    chunk_duration: int = Field(default=60, gt=0)
    chunk_overlap_duration: int = Field(default=0, ge=0)
    prompt: str | None = None
    model: str | None = None
    summarize: bool = False
    summary_prompt: str | None = None
    summary_model: str | None = None
    correlation_id: str | None = Field(default=None, min_length=1, max_length=256)


class VssResult(BaseModel):
    """Versioned VSS result without flattening backend-specific contracts."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"] = "1.0"
    source: RequestSource
    backend_results: dict[str, IngestionResponse]
    summary: CompletionResponse | None = None
    requested_decoder: str | None = None
    requested_frame_transport: Literal["cuda_ipc", "owned_cpu", "deployment_default"]
    correlation_id: str | None = None
    warnings: list[str] = Field(default_factory=list)


def _backend_names(value: str) -> list[str]:
    names = [name.strip() for name in value.split(",") if name.strip()]
    if not names:
        raise ValueError("backends must contain at least one backend name")
    if len(names) != len(set(names)):
        raise ValueError("backends must not contain duplicate names")
    return names


def _caption_chunks(
    results: dict[str, IngestionResponse],
    requested_backends: list[str],
) -> list[VlmChunkResponse]:
    chunks: list[VlmChunkResponse] = []
    for backend in requested_backends:
        result = results.get(backend)
        if result and all(isinstance(item, VlmChunkResponse) for item in result):
            chunks.extend(cast(list[VlmChunkResponse], result))
    return chunks


def _transport(
    cuda_ipc: bool | None,
) -> Literal["cuda_ipc", "owned_cpu", "deployment_default"]:
    if cuda_ipc is True:
        return "cuda_ipc"
    if cuda_ipc is False:
        return "owned_cpu"
    return "deployment_default"


class VssTool(Tool):
    """Run VSS ingestion and optional summarization as a Vision AI tool."""

    name = "vss"
    summary = "Ingest video with VSS backends and optionally summarize captions"
    Settings = VssSettings
    Input = VssInput

    async def execute(self, inputs: BaseModel, settings: BaseModel) -> VssResult:
        assert isinstance(inputs, VssInput)
        assert isinstance(settings, VssSettings)

        backends = _backend_names(inputs.backends)
        source = RequestSource.for_file(inputs.source, camera_id=inputs.camera_id)
        job = ingest(
            source,
            backends=backends,
            prompt=inputs.prompt or DEFAULT_INGESTION_PROMPT,
            model=inputs.model,
            decoder=inputs.decoder,
            single_decode=inputs.single_decode,
            cuda_ipc=inputs.cuda_ipc,
            chunk_duration=inputs.chunk_duration,
            chunk_overlap_duration=inputs.chunk_overlap_duration,
        )
        try:
            results = await job
        except asyncio.CancelledError:
            await asyncio.shield(job.wait_closed())
            raise

        summary = None
        if inputs.summarize:
            chunks = _caption_chunks(results, backends)
            if not chunks:
                raise ValueError("summarize requires at least one backend with VLM caption results")
            summary = await summarize_video(
                chunks,
                prompt=inputs.summary_prompt,
                model=inputs.summary_model,
            )

        return VssResult(
            source=source,
            backend_results=results,
            summary=summary,
            requested_decoder=inputs.decoder,
            requested_frame_transport=_transport(inputs.cuda_ipc),
            correlation_id=inputs.correlation_id,
        )


TOOL = VssTool()


__all__ = [
    "TOOL",
    "VssInput",
    "VssResult",
    "VssSettings",
    "VssTool",
]
