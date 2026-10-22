# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Public contracts returned by frame-level VLM processing."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


def get_timestamp_str(timestamp: float) -> str:
    """Return an RFC3339 timestamp string."""
    return (
        datetime.fromtimestamp(timestamp, UTC).strftime("%Y-%m-%dT%H:%M:%S")
        + f".{(int(timestamp * 1000) % 1000):03d}Z"
    )


class _VersionedContract(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    schema_version: Literal["1.0"] = "1.0"


class ChunkInfo(_VersionedContract):
    """Represents a video chunk."""

    sourceId: str = Field(default="")
    chunkIdx: int = Field(default=0)
    file: str = Field(default="")
    pts_offset_ns: int = Field(default=0)
    start_pts: int = Field(default=0)
    end_pts: int = Field(default=-1)
    start_ntp: str = Field(default="")
    end_ntp: str = Field(default="")
    start_ntp_float: float = Field(default=0.0)
    end_ntp_float: float = Field(default=0.0)
    is_first: bool = Field(default=False)
    is_last: bool = False
    cv_metadata_json_file: str = Field(default="")
    osd_output_video_file: str = Field(default="")
    cached_frames_cv_meta: list[Any] = Field(default_factory=list)

    def __repr__(self) -> str:
        if self.file.startswith("rtsp://"):
            return (
                f"Chunk {self.chunkIdx}: start={self.start_pts / 1_000_000_000.0}"
                f" end={self.end_pts / 1_000_000_000.0}"
                f" start_ntp={self.start_ntp} end_ntp={self.end_ntp}"
                f" file={self.file}"
            )
        return (
            f"Chunk {self.chunkIdx}: start={self.start_pts / 1_000_000_000.0}"
            f" end={self.end_pts / 1_000_000_000.0} file={self.file}"
        )

    def __str__(self) -> str:
        return self.__repr__()

    def get_timestamp(self, frame_pts: float) -> str:
        if self.file.startswith("rtsp://"):
            timestamp = (
                self.start_ntp_float + frame_pts - self.start_pts / 1_000_000_000.0
            )
            return get_timestamp_str(timestamp)
        return str(frame_pts)


class VlmChunkResponse(_VersionedContract):
    """Per-chunk response from frame-level processing or RTVI."""

    chunk: ChunkInfo | None = None
    vlm_response: str | None = None
    audio_transcript: str | None = None
    reasoning_description: str | None = None
    error: str | None = None
    is_live_stream_ended: bool = False
    decode_start_time: float = Field(default=0.0, ge=0)
    decode_end_time: float = Field(default=0.0, ge=0)
    vlm_start_time: float = Field(default=0.0, ge=0)
    vlm_end_time: float = Field(default=0.0, ge=0)
    add_doc_start_time: float = Field(default=0.0, ge=0)
    add_doc_end_time: float = Field(default=0.0, ge=0)
    queue_time: float = Field(default=0.0, ge=0)
    processing_latency: float = Field(default=0.0, ge=0)
    rtvi_chunk_latency_s: float = Field(default=0.0, ge=0)
    rtvi_frame_count: int = Field(default=0, ge=0)
    vlm_stats: dict[str, Any] = Field(default_factory=dict)

    def __str__(self) -> str:
        def format_window(start: float, end: float) -> str:
            return f"{start:.3f}-{end:.3f}" if start and end else "N/A"

        timings = {
            "decode": format_window(self.decode_start_time, self.decode_end_time),
            "vlm": format_window(self.vlm_start_time, self.vlm_end_time),
            "add_doc": format_window(self.add_doc_start_time, self.add_doc_end_time),
            "queue_s": (f"{self.queue_time:.3f}" if self.queue_time else "N/A"),
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
            f"timings={timings}, tokens={tokens}, "
            f"frames={self.rtvi_frame_count}, "
            f"transcript={bool(self.audio_transcript)}, "
            f"reasoning={bool(self.reasoning_description)})"
        )


VlmCaptionsCompletionResponse = list[VlmChunkResponse]
