# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Existing VSS ingestion contracts, preserved for compatibility."""

from __future__ import annotations

import uuid
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, FiniteFloat, model_validator

from .vlm_contracts import (
    ChunkInfo,
    VlmCaptionsCompletionResponse,
    VlmChunkResponse,
)

__all__ = [
    "BoundingBox",
    "ChunkInfo",
    "CvChunkResponse",
    "CvCompletionResponse",
    "CvDetection",
    "EmbeddingChunkResponse",
    "EmbeddingCompletionResponse",
    "IngestionResponse",
    "RequestSource",
    "TextEmbeddingResponse",
    "VlmCaptionsCompletionResponse",
    "VlmChunkResponse",
    "VlmModelInfo",
    "VlmRequestParams",
]


class VersionedContract(BaseModel):
    """Base for JSON-serializable M1 ingestion contracts."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    schema_version: Literal["1.0"] = "1.0"


class RequestSource(VersionedContract):
    """Lightweight identity carrier for summarization requests."""

    url: str | None = None
    camera_id: str = "default"
    source_id: str = Field(default_factory=lambda: str(uuid.uuid4()))

    @classmethod
    def for_file(cls, url: str, camera_id: str = "default") -> RequestSource:
        """Create a file request with a synthetic source identifier."""

        return cls(url=url, camera_id=camera_id)


class VlmModelInfo(VersionedContract):
    """Model information."""

    id: str = ""
    created: int = Field(default=0, ge=0)
    owned_by: str = ""
    api_type: str = ""


class VlmRequestParams(VersionedContract):
    """Existing VLM request parameters."""

    vlm_generation_config: dict[str, Any] | None = Field(default=None)
    vlm_prompt: str | None = None


class EmbeddingChunkResponse(VersionedContract):
    """Per-chunk response from an embedding ingestion backend."""

    chunk: ChunkInfo
    embeddings: list[FiniteFloat] = Field(default_factory=list, max_length=10_000)
    error: str | None = None
    decode_start_time: float = Field(default=0.0, ge=0)
    decode_end_time: float = Field(default=0.0, ge=0)
    embedding_start_time: float = Field(default=0.0, ge=0)
    embedding_end_time: float = Field(default=0.0, ge=0)
    queue_time: float = Field(default=0.0, ge=0)
    processing_latency: float = Field(default=0.0, ge=0)
    rtvi_chunk_latency_s: float = Field(default=0.0, ge=0)
    rtvi_frame_count: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def require_embeddings_or_error(self) -> EmbeddingChunkResponse:
        """Require a vector for success while permitting chunk-local failures."""
        if not self.embeddings and not self.error:
            raise ValueError("embeddings must not be empty unless error is present")
        return self


class TextEmbeddingResponse(VersionedContract):
    """Embedding vector for one text input returned by RTVI-Embed."""

    text_input: str = Field(max_length=1_000)
    embeddings: list[FiniteFloat] = Field(min_length=1, max_length=10_000)


class BoundingBox(VersionedContract):
    """Axis-aligned source-pixel rectangle from RTVI-CV."""

    left: FiniteFloat = Field(ge=0)
    top: FiniteFloat = Field(ge=0)
    width: FiniteFloat = Field(ge=0)
    height: FiniteFloat = Field(ge=0)


class CvDetection(VersionedContract):
    """One normalized detection or tracked object from RTVI-CV."""

    timestamp_ns: int = Field(ge=0)
    frame_id: int | None = Field(default=None, ge=0)
    tracking_id: str | None = None
    object_id: str | None = None
    class_label: str | None = None
    confidence: FiniteFloat | None = Field(default=None, ge=0, le=1)
    bbox: BoundingBox | None = None
    embedding: list[FiniteFloat] | None = Field(default=None, max_length=10_000)


class CvChunkResponse(VersionedContract):
    """Detections aligned to one source-video interval."""

    chunk: ChunkInfo
    detections: list[CvDetection] = Field(default_factory=list)
    processing_latency: float = Field(default=0.0, ge=0)
    error: str | None = None


type EmbeddingCompletionResponse = list[EmbeddingChunkResponse]
type CvCompletionResponse = list[CvChunkResponse]
type IngestionResponse = (
    VlmCaptionsCompletionResponse | EmbeddingCompletionResponse | CvCompletionResponse
)
