# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""VSS video summarization and aggregation."""

from .aggregation import aggregate_events
from .contracts import (
    ChatCompletionResponseMessage,
    CompletionFinishReason,
    CompletionObject,
    CompletionResponse,
    CompletionResponseChoice,
    CompletionUsage,
    MediaInfoOffset,
    MediaInfoTimeStamp,
)
from .exceptions import (
    SummarizationError,
    SummaryConfigurationError,
    SummaryGenerationError,
)
from .generators import (
    GeneratedSummary,
    OpenAIAPIType,
    OpenAIConfig,
    OpenAISummaryGenerator,
    SummaryGenerator,
    available_generators,
    get_generator,
    register_generator,
)
from .service import (
    DEFAULT_CHUNK_STORE,
    summarize_from_store,
    summarize_video,
    summarize_video_sync,
)
from .storage import AdapterChunkStore, ChunkStore, FileChunkStore, InMemoryChunkStore

__all__ = [
    "DEFAULT_CHUNK_STORE",
    "ChatCompletionResponseMessage",
    "ChunkStore",
    "AdapterChunkStore",
    "FileChunkStore",
    "CompletionFinishReason",
    "CompletionObject",
    "CompletionResponse",
    "CompletionResponseChoice",
    "CompletionUsage",
    "GeneratedSummary",
    "InMemoryChunkStore",
    "MediaInfoOffset",
    "MediaInfoTimeStamp",
    "OpenAIAPIType",
    "OpenAIConfig",
    "OpenAISummaryGenerator",
    "SummaryGenerator",
    "SummarizationError",
    "SummaryConfigurationError",
    "SummaryGenerationError",
    "aggregate_events",
    "available_generators",
    "get_generator",
    "register_generator",
    "summarize_from_store",
    "summarize_video",
    "summarize_video_sync",
]
