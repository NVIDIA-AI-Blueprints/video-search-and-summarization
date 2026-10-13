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

"""Latency percentiles, without a numpy dependency.

:func:`percentile` reproduces ``numpy.percentile``'s default ``linear``
interpolation exactly, so figures stay comparable with results produced by a
numpy-based harness. The dependency is dropped because this skill is run with
``uv run --with requests --with pyyaml`` and every extra wheel is one more thing
that can fail on a machine that is only meant to send HTTP requests.
"""

from __future__ import annotations

import math
import statistics
from collections import defaultdict

from .models import RequestRecord


def percentile(sorted_values: list[float], pct: float) -> float:
    """``pct``-th percentile of an already-sorted list, linear interpolation.

    Matches ``numpy.percentile(a, pct)``: the index is ``(n-1) * pct/100`` and a
    fractional index interpolates linearly between its neighbours.
    """
    if not sorted_values:
        raise ValueError("percentile of an empty sample")
    n = len(sorted_values)
    if n == 1:
        return float(sorted_values[0])
    pos = (n - 1) * (pct / 100.0)
    low = math.floor(pos)
    high = math.ceil(pos)
    if low == high:
        return float(sorted_values[int(pos)])
    lower = sorted_values[low]
    upper = sorted_values[high]
    return float(lower + (upper - lower) * (pos - low))


def compute_per_query_stats(
    records: list[RequestRecord],
) -> dict[str, dict[str, float]]:
    """Bucket measured (non-warmup, successful) records by query_id.

    Warmup is excluded because it runs while the worker pool is still
    assembling, and failures because a timed-out request's latency is the
    latency of the timeout -- counting either would describe something other
    than the service under load.
    """
    buckets: dict[str, list[float]] = defaultdict(list)
    for r in records:
        if r.is_warmup or not r.succeeded:
            continue
        buckets[r.query_id].append(r.latency_sec)
    return {qid: latency_stats(latencies) for qid, latencies in buckets.items()}


def compute_run_stats(records: list[RequestRecord]) -> dict[str, float]:
    """The same figures over every measured, successful record of the run."""
    latencies = [r.latency_sec for r in records if not r.is_warmup and r.succeeded]
    return latency_stats(latencies) if latencies else {}


def latency_stats(latencies: list[float]) -> dict[str, float]:
    ordered = sorted(latencies)
    return {
        "count": float(len(ordered)),
        "p50": percentile(ordered, 50),
        "p95": percentile(ordered, 95),
        "p99": percentile(ordered, 99),
        "mean": float(statistics.fmean(ordered)),
        "min": float(ordered[0]),
        "max": float(ordered[-1]),
        "std": float(statistics.pstdev(ordered)) if len(ordered) > 1 else 0.0,
    }


def format_duration(seconds: float) -> str:
    """``5m02.8s`` / ``47.3s``, matching the console style of the run log."""
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes, rest = divmod(seconds, 60)
    return f"{int(minutes)}m{rest:04.1f}s"
