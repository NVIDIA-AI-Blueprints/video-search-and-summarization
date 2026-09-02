# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Small, dependency-free Server-Sent Events parser."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import BinaryIO


@dataclass(frozen=True, slots=True)
class SseFrame:
    event: str | None
    data: str
    id: str | None = None


def iter_sse(stream: BinaryIO) -> Iterator[SseFrame]:
    """Yield complete SSE frames from a binary line stream."""

    event: str | None = None
    event_id: str | None = None
    data: list[str] = []

    for raw_line in stream:
        line = raw_line.decode("utf-8", errors="replace").rstrip("\r\n")
        if not line:
            if data:
                yield SseFrame(event=event, data="\n".join(data), id=event_id)
            event = None
            event_id = None
            data = []
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
