# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""VSS ingestion contracts and lazily loaded inference capabilities.

Contract imports require only base dependencies. Accessing backend, queue or
service exports loads the requested capability and its optional dependencies.
Existing public names remain discoverable through ``__all__`` and ``dir()``.
"""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any

from .contracts import (
    BoundingBox,
    ChunkInfo,
    CvChunkResponse,
    CvCompletionResponse,
    CvDetection,
    EmbeddingChunkResponse,
    EmbeddingCompletionResponse,
    IngestionResponse,
    RequestSource,
    TextEmbeddingResponse,
    VlmCaptionsCompletionResponse,
    VlmChunkResponse,
    VlmModelInfo,
    VlmRequestParams,
)
from .exceptions import (
    IngestionBackendError,
    IngestionError,
    IngestionQueueFullError,
    RTEmbedBackendError,
    RTVIBackendError,
    RTVICvBackendError,
    VLMBackendError,
)
from .source_identity import ImmutableSourceIdentity, SourceIdentityProvider

if TYPE_CHECKING:
    from .backend import (
        CPUVLMBackend,
        GPUVLMBackend,
        IngestionBackend,
        RTEmbedBackend,
        RTVIBackend,
        RTVICvBackend,
        VLMIngestionBackend,
        available_backends,
        get_backend,
        register_backend,
    )
    from .queue import IngestionJob, IngestionJobState, IngestionQueue
    from .service import DEFAULT_INGESTION_PROMPT, ingest, ingest_sync
    from .vlm import (
        OpenAICompatibleVLMBackend,
        VLMBackend,
        VLMFrameChunk,
        available_vlm_backends,
        get_vlm_backend,
        register_vlm_backend,
    )

_LAZY_EXPORTS = {
    **dict.fromkeys(
        (
            "CPUVLMBackend",
            "GPUVLMBackend",
            "IngestionBackend",
            "RTEmbedBackend",
            "RTVIBackend",
            "RTVICvBackend",
            "VLMIngestionBackend",
            "available_backends",
            "get_backend",
            "register_backend",
        ),
        ".backend",
    ),
    **dict.fromkeys(("IngestionJob", "IngestionJobState", "IngestionQueue"), ".queue"),
    **dict.fromkeys(("DEFAULT_INGESTION_PROMPT", "ingest", "ingest_sync"), ".service"),
    **dict.fromkeys(
        (
            "OpenAICompatibleVLMBackend",
            "VLMBackend",
            "VLMFrameChunk",
            "available_vlm_backends",
            "get_vlm_backend",
            "register_vlm_backend",
        ),
        ".vlm",
    ),
}


def __getattr__(name: str) -> Any:
    module = _LAZY_EXPORTS.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(module, __name__), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))


__all__ = [
    "BoundingBox",
    "CPUVLMBackend",
    "ChunkInfo",
    "CvChunkResponse",
    "CvCompletionResponse",
    "CvDetection",
    "DEFAULT_INGESTION_PROMPT",
    "EmbeddingChunkResponse",
    "EmbeddingCompletionResponse",
    "GPUVLMBackend",
    "IngestionBackendError",
    "IngestionError",
    "IngestionBackend",
    "IngestionJob",
    "IngestionJobState",
    "IngestionQueue",
    "IngestionQueueFullError",
    "IngestionResponse",
    "RTEmbedBackend",
    "RTEmbedBackendError",
    "ImmutableSourceIdentity",
    "OpenAICompatibleVLMBackend",
    "RTVIBackend",
    "RTVIBackendError",
    "RTVICvBackend",
    "RTVICvBackendError",
    "RequestSource",
    "SourceIdentityProvider",
    "TextEmbeddingResponse",
    "VLMBackend",
    "VLMBackendError",
    "VLMFrameChunk",
    "VLMIngestionBackend",
    "VlmCaptionsCompletionResponse",
    "VlmChunkResponse",
    "VlmModelInfo",
    "VlmRequestParams",
    "available_backends",
    "available_vlm_backends",
    "get_backend",
    "get_vlm_backend",
    "ingest",
    "ingest_sync",
    "register_backend",
    "register_vlm_backend",
]
