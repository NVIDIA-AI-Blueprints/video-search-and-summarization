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
