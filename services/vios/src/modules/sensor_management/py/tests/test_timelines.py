# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Recording-timeline build tests (vst_common::getRecordTimelines parity)."""
from __future__ import annotations

from sensor_ms.core.timelines import MAX_TOLERANCE_MS, build_timeline, epoch_ms_to_iso, iso_to_epoch_ms


def test_epoch_iso_roundtrip():
    import re
    ms = 1781150626316
    iso = epoch_ms_to_iso(ms)
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.316Z", iso), iso
    assert iso_to_epoch_ms(iso) == ms


def test_single_segment():
    tl = build_timeline([(1000_000, 60_000)])  # start 1000s, dur 60s
    assert len(tl) == 1
    assert tl[0]["startTime"] == epoch_ms_to_iso(1000_000)
    assert tl[0]["endTime"] == epoch_ms_to_iso(1060_000)


def test_contiguous_segments_merge_into_one_range():
    # three back-to-back 1s segments (no gap) -> one range start..last-end
    rows = [(0, 1000), (1000, 1000), (2000, 1000)]
    tl = build_timeline(rows)
    assert len(tl) == 1
    assert tl[0]["startTime"] == epoch_ms_to_iso(0)
    assert tl[0]["endTime"] == epoch_ms_to_iso(3000)


def test_gap_splits_into_two_ranges():
    # gap > MAX_TOLERANCE_MS between seg1 end and seg2 start -> two ranges
    rows = [(0, 1000), (1000 + MAX_TOLERANCE_MS + 5000, 1000)]
    tl = build_timeline(rows)
    assert len(tl) == 2
    assert tl[0]["endTime"] == epoch_ms_to_iso(1000)
    assert tl[1]["startTime"] == epoch_ms_to_iso(1000 + MAX_TOLERANCE_MS + 5000)


def test_empty():
    assert build_timeline([]) == []
