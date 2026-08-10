# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Frozen, storage-independent VSS memory entities."""

from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class RecordType(str, Enum):
    """Stable record categories shared by application queries and storage adapters."""

    VIDEO_SUMMARY = "video_summary"
    VIDEO_EVENT = "video_event"
    ALERT_RECORD = "alert_record"
    SEARCH_SESSION = "search_session"
    SEARCH_HIT = "search_hit"


@dataclass(frozen=True, slots=True)
class TimeRange:
    start_seconds: float
    end_seconds: float

    def __post_init__(self) -> None:
        if self.start_seconds < 0:
            raise ValueError("start_seconds cannot be negative")
        if self.end_seconds < self.start_seconds:
            raise ValueError("end_seconds must follow start_seconds")


@dataclass(frozen=True, slots=True)
class MediaRef:
    source: str
    video_id: str
    stream_id: str | None = None
    name: str | None = None

    def __post_init__(self) -> None:
        if not self.source.strip():
            raise ValueError("source cannot be empty")
        if not self.video_id.strip():
            raise ValueError("video_id cannot be empty")


@dataclass(frozen=True, slots=True)
class Event:
    id: str
    ordinal: int
    time_range: TimeRange
    description: str
    event_type: str

    def __post_init__(self) -> None:
        if not self.id.strip():
            raise ValueError("event id cannot be empty")
        if self.ordinal < 1:
            raise ValueError("ordinal must be one-based")
        if not self.description.strip():
            raise ValueError("event description cannot be empty")
        if not self.event_type.strip():
            raise ValueError("event_type cannot be empty")


@dataclass(frozen=True, slots=True)
class Summary:
    id: str
    description: str
    media_ref: MediaRef
    created_at: datetime
    events: tuple[Event, ...]

    def __post_init__(self) -> None:
        if not self.id.strip():
            raise ValueError("summary id cannot be empty")
        if not self.description.strip():
            raise ValueError("summary description cannot be empty")
        if self.created_at.tzinfo is None or self.created_at.utcoffset() is None:
            raise ValueError("created_at must be timezone-aware")
        expected_ordinals = tuple(range(1, len(self.events) + 1))
        actual_ordinals = tuple(event.ordinal for event in self.events)
        if actual_ordinals != expected_ordinals:
            raise ValueError("event ordinals must be contiguous and one-based")

    @property
    def event_count(self) -> int:
        return len(self.events)

    @property
    def time_range(self) -> TimeRange | None:
        if not self.events:
            return None
        return TimeRange(
            start_seconds=min(event.time_range.start_seconds for event in self.events),
            end_seconds=max(event.time_range.end_seconds for event in self.events),
        )


MemoryEntity = Summary | Event
