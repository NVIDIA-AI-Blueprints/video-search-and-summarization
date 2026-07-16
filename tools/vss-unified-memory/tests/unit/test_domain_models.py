# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from datetime import datetime, timezone

import pytest

from vss_unified_memory.domain.models import Event, MediaRef, Summary, TimeRange


def test_summary_derives_event_count_and_time_range() -> None:
    summary = Summary(
        id="summary:1",
        description="Summary",
        media_ref=MediaRef(source="vst", video_id="video-1"),
        created_at=datetime.now(timezone.utc),
        events=(
            Event("event:1", 1, TimeRange(10, 20), "First", "activity"),
            Event("event:2", 2, TimeRange(5, 30), "Second", "activity"),
        ),
    )
    assert summary.event_count == 2
    assert summary.time_range == TimeRange(5, 30)


def test_summary_without_events_has_no_time_range() -> None:
    summary = Summary(
        id="summary:1",
        description="Summary",
        media_ref=MediaRef(source="vst", video_id="video-1"),
        created_at=datetime.now(timezone.utc),
        events=(),
    )
    assert summary.time_range is None


def test_time_range_rejects_reversed_range() -> None:
    with pytest.raises(ValueError, match="end_seconds"):
        TimeRange(10, 5)


def test_summary_rejects_noncontiguous_event_ordinals() -> None:
    with pytest.raises(ValueError, match="ordinals"):
        Summary(
            id="summary:1",
            description="Summary",
            media_ref=MediaRef(source="vst", video_id="video-1"),
            created_at=datetime.now(timezone.utc),
            events=(Event("event:2", 2, TimeRange(1, 2), "Second", "activity"),),
        )
