# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Recording-timeline construction — byte-faithful port of vst_common::getRecordTimelines.

video_record_details stores per-segment start_time (epoch ms) and file_duration (ms). The C++
merges contiguous/overlapping segments into [startTime, endTime] ranges and splits when the gap
between consecutive segments exceeds MAX_TOLERANCE_SECS. Timestamps render as ISO8601 UTC with
millisecond precision (convertEpocToISO8601_2, which takes microseconds = ms*1000).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

MAX_TOLERANCE_MS = 2 * 1000  # MAX_TOLERANCE_SECS = 2 (device_manager.h:57)


def epoch_ms_to_iso(ms: int) -> str:
    """Epoch milliseconds -> "YYYY-MM-DDTHH:MM:SS.mmmZ" (matches convertEpocToISO8601_2)."""
    dt = datetime.fromtimestamp(ms // 1000, tz=timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S") + f".{ms % 1000:03d}Z"


def iso_to_epoch_ms(iso: str) -> int:
    """ISO8601 (optionally trailing Z) -> epoch milliseconds. 0 on empty/parse failure."""
    if not iso:
        return 0
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp() * 1000)
    except (ValueError, TypeError):
        return 0


def build_timeline(rows: list[tuple[int, int]]) -> list[dict[str, Any]]:
    """Merge ordered (start_ms, duration_ms) segments into [{startTime, endTime}] ranges.

    Ported from getRecordTimelines: open a range at the first segment; while consecutive segments
    overlap or are contiguous (gap <= MAX_TOLERANCE_MS) keep extending; close the range and open a
    new one when a larger gap is seen; close the final range at the last segment's end.
    """
    out: list[dict[str, Any]] = []
    cur: dict[str, Any] | None = None
    n = len(rows)
    for i in range(n):
        start, dur = rows[i]
        if cur is None:
            cur = {"startTime": epoch_ms_to_iso(start)}
        if i + 1 < n:
            nxt_start = rows[i + 1][0]
            seg_end = start + dur
            if nxt_start < seg_end:           # overlap -> merge, keep extending
                continue
            if (nxt_start - seg_end) > MAX_TOLERANCE_MS:   # gap -> close range, start new
                cur["endTime"] = epoch_ms_to_iso(seg_end)
                out.append(cur)
                cur = {"startTime": epoch_ms_to_iso(nxt_start)}
            # else contiguous within tolerance: leave cur open, continue
        else:                                  # last segment -> close range
            cur["endTime"] = epoch_ms_to_iso(start + dur)
            out.append(cur)
    return out
