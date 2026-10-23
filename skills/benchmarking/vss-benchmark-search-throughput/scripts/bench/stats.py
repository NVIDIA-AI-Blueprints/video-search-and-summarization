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
from typing import Any

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


def latency_samples(records: list[RequestRecord]) -> list[RequestRecord]:
    """The records a latency figure describes: measured, successful, on target.

    Warmup is excluded because it runs while the worker pool is still
    assembling, failures because a timed-out request's latency is the latency
    of the timeout, and off-target requests because they ran another path's
    pipeline. All three still count where they belong: failures in the
    reliability figures, off-target requests in throughput and path stability.
    """
    return [r for r in records if not r.is_warmup and r.succeeded and r.on_target]


def compute_per_query_stats(
    records: list[RequestRecord],
    metric: str = "latency_sec",
) -> dict[str, dict[str, float]]:
    """:func:`latency_stats` of ``metric`` per query_id, over the latency samples."""
    buckets: dict[str, list[float]] = defaultdict(list)
    for r in latency_samples(records):
        buckets[r.query_id].append(getattr(r, metric))
    return {qid: latency_stats(values) for qid, values in buckets.items()}


def compute_run_stats(
    records: list[RequestRecord],
    metric: str = "latency_sec",
) -> dict[str, float]:
    """The same figures over every latency sample of the run."""
    values = [getattr(r, metric) for r in latency_samples(records)]
    return latency_stats(values) if values else {}


#: CLI stage label -> the step of a request it belongs to. Labels not listed
#: here are still reported, under step ``other``.
STAGE_STEPS: dict[str, str] = {
    "search: retrieval": "retrieval (wall)",
    "critic: verify": "verify (wall)",
    "embed_search: generate query embedding": "encode",
    "attribute_search: generate text embedding": "encode",
    "fusion: generate attribute embeddings (once)": "encode",
    "embed_search: ES search execution": "knn",
    "attribute_search: search behavior embeddings": "knn",
    "search: object_ids behavior KNN": "knn",
    "attribute_search: frame lookups": "frame lookups",
    "vlm: resolve VST clip url": "resolve clip url",
    "critic: resolve VST timeline": "resolve clip url",
    "vlm: prepare media": "download clip (client)",
    "vlm: inference": "verify clip (VLM)",
}

#: Steps this script measures itself rather than reading from the CLI.
DERIVED_STAGES = {
    "decompose": "decompose",
    "cli overhead": "cli overhead",
    "search total": "search total",
    "e2e": "e2e",
}


def compute_stage_stats(records: list[RequestRecord]) -> dict[str, dict[str, Any]]:
    """Per stage: how often it runs per request and what it costs.

    ``p50``..``mean`` are over the per-request sum of the stage's
    ``total_s``. For a stage that runs several times concurrently inside one
    request (``vlm: inference`` once per verified hit, fusion's per-candidate
    lookups) that sum is work, not elapsed time -- read ``per_call_mean`` for
    those, and the ``(wall)`` steps for elapsed time. ``concurrent`` says the
    stage's children overlapped in at least one request.

    ``cli overhead`` is the CLI's wall clock minus its own ``total_s``:
    interpreter start, config load, client setup.
    """
    samples = latency_samples(records)
    per_request: dict[str, list[float]] = defaultdict(list)
    calls: dict[str, float] = defaultdict(float)
    concurrent: dict[str, bool] = defaultdict(bool)
    for r in samples:
        per_request["decompose"].append(r.decompose_sec)
        per_request["e2e"].append(r.e2e_latency_sec)
        if r.search_total_sec is not None:
            per_request["search total"].append(r.search_total_sec)
            per_request["cli overhead"].append(max(0.0, r.latency_sec - r.search_total_sec))
        for label, entry in (r.stage_timings or {}).items():
            per_request[label].append(float(entry.get("total_s") or 0.0))
            calls[label] += float(entry.get("calls") or 0.0)
            concurrent[label] |= bool(entry.get("concurrent_children"))

    out: dict[str, dict[str, Any]] = {}
    for label, values in per_request.items():
        stats: dict[str, Any] = latency_stats(values)
        derived = label in DERIVED_STAGES
        stats["step"] = DERIVED_STAGES.get(label) or STAGE_STEPS.get(label, "other")
        stats["calls_per_request"] = 1.0 if derived else calls[label] / len(values)
        total_calls = len(values) if derived else calls[label]
        stats["per_call_mean"] = sum(values) / total_calls if total_calls else None
        stats["concurrent"] = concurrent[label]
        out[label] = stats
    return out


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
