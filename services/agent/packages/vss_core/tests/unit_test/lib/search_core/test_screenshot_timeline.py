# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Screenshot-timestamp → VST replay-timeline mapping.

File-ingested sources are indexed on a synthetic, midnight-anchored epoch
(e.g. 2025-01-01T00:01:00Z = 60s into the file) while VST anchors the replay
timeline at ingest wall-clock. Screenshot URLs built from raw ES timestamps
point outside the recording and VST answers
``VMSInternalError: no valid stream found for given timestamps`` (HTTP 500).
"""

from __future__ import annotations

from vss_core.vios import map_interval_to_timeline
from vss_core.vios import map_timestamp_to_timeline

TL_START = "2026-07-18T04:15:21.640Z"
TL_END = "2026-07-18T04:18:51.640Z"


def test_synthetic_epoch_timestamp_is_rebased_onto_timeline() -> None:
    # 60s into the file, indexed at the canonical file epoch.
    mapped = map_timestamp_to_timeline("2025-01-01T00:01:00Z", TL_START, TL_END)
    assert mapped == "2026-07-18T04:16:21.640Z"


def test_wall_clock_timestamp_inside_timeline_passes_through() -> None:
    live = "2026-07-18T04:17:00.000Z"
    assert map_timestamp_to_timeline(live, TL_START, TL_END) == live


def test_offset_beyond_timeline_is_clamped_to_end() -> None:
    # 2h into a 3.5-minute recording: clamp instead of letting VST 500.
    mapped = map_timestamp_to_timeline("2025-01-01T02:00:00Z", TL_START, TL_END)
    assert mapped == "2026-07-18T04:18:51.640Z"


def test_timeline_boundaries_pass_through() -> None:
    assert map_timestamp_to_timeline(TL_START, TL_START, TL_END) == TL_START
    assert map_timestamp_to_timeline(TL_END, TL_START, TL_END) == TL_END


def test_unparseable_inputs_return_original_timestamp() -> None:
    assert map_timestamp_to_timeline("not-a-time", TL_START, TL_END) == "not-a-time"
    assert map_timestamp_to_timeline("2025-01-01T00:01:00Z", "junk", TL_END) == "2025-01-01T00:01:00Z"


def test_wall_clock_before_timeline_start_is_rebased_not_clamped_blindly() -> None:
    # A timestamp 30s-of-day lands at timeline start + 30s once rebased —
    # midnight-anchored offsets always rebase, never silently pin to start.
    mapped = map_timestamp_to_timeline("2025-01-01T00:00:30Z", TL_START, TL_END)
    assert mapped == "2026-07-18T04:15:51.640Z"


def test_cross_midnight_interval_preserves_duration() -> None:
    start, end = map_interval_to_timeline(
        "2025-01-01T23:59:50Z",
        "2025-01-02T00:00:10Z",
        "2026-07-18T04:15:21.640Z",
        "2026-07-19T04:16:21.640Z",
    )
    assert start == "2026-07-19T04:15:11.640Z"
    assert end == "2026-07-19T04:15:31.640Z"


def test_multi_day_file_offset_preserves_day_component() -> None:
    start, end = map_interval_to_timeline(
        "2025-01-02T01:00:00Z",
        "2025-01-02T01:00:20Z",
        "2026-07-18T04:15:21.640Z",
        "2026-07-20T04:15:21.640Z",
    )
    assert start == "2026-07-19T05:15:21.640Z"
    assert end == "2026-07-19T05:15:41.640Z"


def test_unknown_stream_in_populated_timelines_means_no_url() -> None:
    """Stale ES docs reference streams VST no longer knows; their picture URLs
    are guaranteed VMSInternalError 500s (observed: eval run 29637290995,
    hit-2 referenced a prior deploy registration). With a populated timelines
    map, an unknown stream must yield no screenshot rather than a dead URL."""
    from vss_core.search_core.primitives._attribute_helpers import _map_to_timeline

    timelines = {"known-stream": (TL_START, TL_END)}
    assert _map_to_timeline("2025-01-01T00:01:00Z", "stale-stream", timelines) is None
    assert _map_to_timeline("2025-01-01T00:01:00Z", "known-stream", timelines) == "2026-07-18T04:16:21.640Z"
    # Empty map = timelines unavailable; best-effort identity applies.
    assert _map_to_timeline("2025-01-01T00:01:00Z", "stale-stream", {}) == "2025-01-01T00:01:00Z"
