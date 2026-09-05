# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

"""Retrieval metrics.

Vendored from ``run_eval.py`` so this flow owns its scoring and that script can
be deleted without taking the metrics with it. The definitions are deliberately
byte-for-byte equivalent, not "improved" -- changing them would silently
invalidate every baseline captured with the old runner.

``tests/test_search_eval_flows.py`` asserts this module and ``run_eval.py``
produce identical output for the same input, for as long as both exist. When
``run_eval.py`` goes, that test goes with it and this becomes the only
definition.
"""

from __future__ import annotations

import copy
from datetime import datetime, timedelta
from typing import Any

#: Ground truth is annotated on a 5-second grid, so retrieved windows are split
#: onto the same grid before matching.
SEGMENT_SIZE = 5

#: k values reported as HIT@k.
HIT_K_VALUES = [1, 3, 5, 10]


def parse_ts(ts: str, ceiling: bool = False) -> datetime:
    """Parse an ISO timestamp, dropping sub-second precision.

    ``ceiling`` rounds a fractional second up, so a window ending at
    00:00:13.2 covers the segment it partially overlaps rather than losing it.
    """
    ts_clean = ts.replace("Z", "").replace("+00:00", "")
    dt = datetime.fromisoformat(ts_clean)
    if ceiling and dt.microsecond > 0:
        dt = dt + timedelta(seconds=1)
    return dt.replace(microsecond=0)


def align_ts_to_segment(start: datetime, end: datetime) -> list[tuple[datetime, datetime]]:
    """Split ``[start, end)`` onto the 5-second grid the ground truth uses."""
    if start >= end:
        return []
    start = start.replace(microsecond=0)
    end = end.replace(microsecond=0)
    sec = start.second
    floored = (sec // SEGMENT_SIZE) * SEGMENT_SIZE
    seg_start = start.replace(second=floored, microsecond=0)
    segments = []
    while seg_start < end:
        seg_end = seg_start + timedelta(seconds=SEGMENT_SIZE)
        segments.append((seg_start, seg_end))
        seg_start = seg_end
    return segments


def video_name_matches(api_name: str, gt_name: str) -> bool:
    """Prefix match, because VST renames uploads.

    Ground truth says ``warehouse_sample``; search returns
    ``warehouse_sample_20250101_000000_e0482.mp4``.
    """
    return api_name.replace(".mp4", "").startswith(gt_name)


def match_segment(api_result: dict, gt_segments: list[dict]) -> tuple[int, dict | None]:
    """Find the ground-truth segment a result matches, or ``(-1, None)``."""
    actual_video = api_result.get("video_name", "")
    actual_start = parse_ts(api_result.get("start_time", ""))
    for idx, gt in enumerate(gt_segments):
        gt_video = gt.get("video_name", "")
        gt_start = parse_ts(gt.get("start_time", ""))
        if not all([actual_video, actual_start, gt_video, gt_start]):
            continue
        if video_name_matches(actual_video, gt_video) and actual_start == gt_start:
            return idx, gt
    return -1, None


def post_process_api_results(api_results: list[dict]) -> list[dict]:
    """Expand each hit into the 5-second segments it spans.

    These segments -- not the original hits -- are what gets counted, so a
    merged window contributes more of them than an unmerged one. That is why
    merging changes precision even when retrieval is identical.
    """
    final = []
    for r in api_results:
        s = parse_ts(r.get("start_time", ""))
        e = parse_ts(r.get("end_time", ""), ceiling=True)
        for seg_s, seg_e in align_ts_to_segment(s, e):
            seg = copy.deepcopy(r)
            seg["start_time"] = seg_s.strftime("%Y-%m-%dT%H:%M:%SZ")
            seg["end_time"] = seg_e.strftime("%Y-%m-%dT%H:%M:%SZ")
            final.append(seg)
    return final


def evaluate_query(
    query: str,
    api_results: list[dict],
    expected_segments: list[dict],
    latency_s: float,
) -> dict[str, Any]:
    """Score one query's results against its ground truth.

    Each ground-truth segment can be claimed once; a second result matching an
    already-found segment counts as a false positive rather than a bonus.

    Average precision divides by the ground-truth count, not by the number of
    hits found, so a query cannot reach 1.0 without retrieving everything.
    """
    found_gt = set()
    matched = []
    relevance_at_rank = []

    processed = post_process_api_results(api_results)

    for rank, r in enumerate(processed, 1):
        is_match = False
        gt_idx, _gt_seg = match_segment(r, expected_segments)
        if gt_idx > -1 and gt_idx not in found_gt:
            is_match = True
            found_gt.add(gt_idx)
            matched.append({"rank": rank, "gt_idx": gt_idx})
        relevance_at_rank.append(1 if is_match else 0)

    tp = len(matched)
    total_retrieved = len(processed)
    total_relevant = len(expected_segments)

    precision = tp / total_retrieved if total_retrieved > 0 else 0
    recall = tp / total_relevant if total_relevant > 0 else 0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0

    first_rank = min((m["rank"] for m in matched), default=None)
    rr = 1.0 / first_rank if first_rank else 0.0

    hit_at_k = {}
    for k in HIT_K_VALUES:
        hit_at_k[k] = 1 if any(m["rank"] <= k for m in matched) else 0

    if total_relevant > 0:
        prec_sum = 0.0
        rel_count = 0
        for rank, is_rel in enumerate(relevance_at_rank, 1):
            if is_rel:
                rel_count += 1
                prec_sum += rel_count / rank
        ap = prec_sum / total_relevant
    else:
        ap = 0.0

    missed = [i for i in range(total_relevant) if i not in found_gt]

    return {
        "query": query,
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "average_precision": ap,
        "reciprocal_rank": rr,
        "hit_at_k": hit_at_k,
        "true_positives": tp,
        "false_positives": total_retrieved - tp,
        "total_relevant": total_relevant,
        "total_retrieved": total_retrieved,
        "latency_s": latency_s,
        "missed_gt_indices": missed,
    }


def format_inline(m: dict[str, Any], latency_s: float | None = None) -> str:
    """One-line per-query summary for the console."""
    parts = [
        f"P={m['precision']:.3f}",
        f"R={m['recall']:.3f}",
        f"F1={m['f1']:.3f}",
        f"AP={m['average_precision']:.3f}",
        f"RR={m['reciprocal_rank']:.3f}",
    ]
    if latency_s is not None:
        parts.append(f"Latency={latency_s:.2f}s")
    parts.append(f"Hits={m['true_positives']}/{m['total_relevant']}")
    return "  ".join(parts)
