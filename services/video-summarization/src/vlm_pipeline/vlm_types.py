# SPDX-FileCopyrightText: Copyright (c) 2024-2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

"""
Lightweight data classes for RTVI-VLM responses. GPU-dep-free.
"""

from typing import Optional

from chunk_info import ChunkInfo


class VlmModelInfo:
    """Model information"""

    def __init__(self) -> None:
        self.id = ""
        self.created = 0
        self.owned_by = ""
        self.api_type = ""


class VlmRequestParams:
    vlm_generation_config: Optional[dict] = None
    vlm_prompt: Optional[str] = None

    def __eq__(self, other) -> bool:
        if isinstance(other, VlmRequestParams):
            return (
                self.vlm_prompt == other.vlm_prompt
                and self.vlm_generation_config == other.vlm_generation_config
            )
        return False


class VlmChunkResponse:
    """Per-chunk response from RTVI. Timing fields may be synthesized from
    `*_latency_ms` durations anchored at SSE arrival when RTVI doesn't
    send absolute timestamps."""

    chunk: ChunkInfo = None
    vlm_response: str | None = None
    audio_transcript: str | None = None
    reasoning_description: str | None = None
    error: str | None = None
    is_live_stream_ended = False

    decode_start_time = 0
    decode_end_time = 0
    vlm_start_time = 0
    vlm_end_time = 0
    add_doc_start_time = 0
    add_doc_end_time = 0

    queue_time: float = 0.0
    processing_latency: float = 0.0
    rtvi_chunk_latency_s: float = 0.0
    rtvi_frame_count: int = 0

    def __init__(self):
        self.vlm_stats = {}

    def __str__(self) -> str:
        """String representation of the chunk response for debugging"""

        def fmt_window(start, end):
            return f"{start:.3f}-{end:.3f}" if start and end else "N/A"

        timings = {
            "decode": fmt_window(self.decode_start_time, self.decode_end_time),
            "vlm": fmt_window(self.vlm_start_time, self.vlm_end_time),
            "add_doc": fmt_window(self.add_doc_start_time, self.add_doc_end_time),
            "queue_s": f"{self.queue_time:.3f}" if self.queue_time else "N/A",
            "processing_s": (
                f"{self.processing_latency:.3f}" if self.processing_latency else "N/A"
            ),
        }

        chunk_info = f"chunk[{self.chunk.chunkIdx}]" if self.chunk else "No chunk"
        tokens = (
            f"in:{self.vlm_stats.get('input_tokens', 0)}/"
            f"out:{self.vlm_stats.get('output_tokens', 0)}"
            if self.vlm_stats
            else "N/A"
        )

        return (
            f"VlmChunkResponse({chunk_info}, error={bool(self.error)}, "
            f"timings={timings}, tokens={tokens}, frames={self.rtvi_frame_count}, "
            f"transcript={bool(self.audio_transcript)}, "
            f"reasoning={bool(self.reasoning_description)})"
        )
