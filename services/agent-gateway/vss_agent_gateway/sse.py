# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Small, dependency-free Server-Sent Events parser."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import BinaryIO

MAX_SSE_LINE_BYTES = 2_000_000
MAX_SSE_FRAME_BYTES = 2_000_000


@dataclass(frozen=True, slots=True)
class SseFrame:
    event: str | None
    data: str
    id: str | None = None


def iter_bounded_lines(stream: BinaryIO) -> Iterator[bytes]:
    """Read protocol lines with an upper bound before a delimiter arrives."""

    while True:
        raw_line = stream.readline(MAX_SSE_LINE_BYTES + 1)
        if not raw_line:
            return
        if len(raw_line) > MAX_SSE_LINE_BYTES:
            raise ValueError("backend emitted an oversized streaming line")
        yield raw_line


def iter_sse(stream: BinaryIO) -> Iterator[SseFrame]:
    """Yield complete SSE frames from a binary line stream."""

    event: str | None = None
    event_id: str | None = None
    data: list[str] = []
    frame_bytes = 0

    for raw_line in iter_bounded_lines(stream):
        frame_bytes += len(raw_line)
        if frame_bytes > MAX_SSE_FRAME_BYTES:
            raise ValueError("backend emitted an oversized SSE frame")
        line = raw_line.decode("utf-8", errors="replace").rstrip("\r\n")
        if not line:
            if data:
                yield SseFrame(event=event, data="\n".join(data), id=event_id)
            event = None
            event_id = None
            data = []
            frame_bytes = 0
            continue
        if line.startswith(":"):
            continue
        field, separator, value = line.partition(":")
        if separator and value.startswith(" "):
            value = value[1:]
        if field == "event":
            event = value
        elif field == "data":
            data.append(value)
        elif field == "id" and "\0" not in value:
            event_id = value

    if data:
        yield SseFrame(event=event, data="\n".join(data), id=event_id)
