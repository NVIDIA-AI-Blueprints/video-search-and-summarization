# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

"""Alert Bridge application metrics for one measurement window.

Reuses the PromQL helpers of ``services/alert/test/latency/prometheus_latency.py``
(the tool behind docs/performance-alert-verification.mdx) so perf numbers
and the manual latency report agree by construction.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, Optional

# core -> benchmark -> perf -> dev-profile-alerts -> developer-profiles -> docker -> deploy -> repo root
_REPO_ROOT = Path(__file__).resolve().parents[7]
_LATENCY_TOOL_DIR = _REPO_ROOT / "services" / "alert" / "test" / "latency"
if str(_LATENCY_TOOL_DIR) not in sys.path:
    sys.path.insert(0, str(_LATENCY_TOOL_DIR))

import prometheus_latency as pl  # noqa: E402  (path set above)

# metric key -> Alert Bridge histogram (services/alert/src/metrics/prometheus_metrics.py)
HISTOGRAMS = {
    "e2e": "alert_bridge_e2e_duration_seconds",
    "upstream": "alert_bridge_upstream_duration_seconds",
    "kafka_lag": "alert_bridge_kafka_lag_duration_seconds",
    "queue_wait": "alert_bridge_worker_queue_wait_duration_seconds",
    "vst": "alert_bridge_vst_duration_seconds",
    "vlm": "alert_bridge_vlm_duration_seconds",
    "worker": "alert_bridge_worker_processing_seconds",
}

# Highest finite bucket per histogram. A quantile at or above it is clipped:
# histogram_quantile cannot see past the last bucket.
HISTOGRAM_TOP_BUCKET = {
    "e2e": 120.0,
    "upstream": 60.0,
    "kafka_lag": 5.0,
    "queue_wait": 10.0,
    "vst": 10.0,
    "vlm": 30.0,
    "worker": 60.0,
}


def _round(value: Optional[float], digits: int = 3) -> Optional[float]:
    return None if value is None else round(float(value), digits)


def prometheus_healthy(prom_url: str) -> bool:
    try:
        pl.requests.get(f"{prom_url}/-/healthy", timeout=5).raise_for_status()
        return True
    except pl.requests.RequestException:
        return False


def collect_window(prom_url: str, window_seconds: int, num_streams: int) -> Dict[str, Any]:
    """Return metrics for the last ``window_seconds``, shaped for test_cases[].metrics.

    Call right after the window ends: every query is an instant query at
    "now" over ``[window_seconds]``.
    """
    window = f"{int(window_seconds)}s"
    latency: Dict[str, Any] = {}
    clipped = []
    for key, metric in HISTOGRAMS.items():
        avg, p50, p90, p99, count = pl.prom_fetch_row(prom_url, metric, window)
        latency[f"{key}_avg_seconds"] = _round(avg)
        latency[f"{key}_p50_seconds"] = _round(p50)
        latency[f"{key}_p90_seconds"] = _round(p90)
        latency[f"{key}_p99_seconds"] = _round(p99)
        if key == "e2e":
            latency["e2e_samples"] = count
        top = HISTOGRAM_TOP_BUCKET[key]
        if p99 is not None and p99 >= top:
            clipped.append(key)
    latency["clipped_quantiles"] = clipped

    def counter(metric: str, labels: str = "") -> int:
        return pl.prom_counter(prom_url, metric, window, labels)

    events = counter("alert_bridge_events_total")
    confirmed = counter("alert_bridge_events_total", 'verdict="confirmed"')
    rejected = counter("alert_bridge_events_total", 'verdict="rejected"')
    failed = counter("alert_bridge_events_total", 'verdict="verification-failed"')
    after_dedup = counter("alert_bridge_events_after_dedup_total")
    dropped = counter("alert_bridge_events_dropped_total")
    failures = counter("alert_bridge_verification_failures_total")

    minutes = window_seconds / 60.0
    streams = max(1, int(num_streams))
    throughput = {
        "verified_events": events,
        "verified_per_min": round(events / minutes, 3) if minutes else None,
        "verified_per_min_per_stream": round(events / minutes / streams, 3) if minutes else None,
        "confirmed_events": confirmed,
        "rejected_events": rejected,
        "events_after_dedup": after_dedup,
        "events_dropped": dropped,
    }
    reliability = {
        "verification_failed_events": failed,
        "verification_failures": failures,
        "verification_failure_rate": round(failures / events, 4) if events else None,
    }
    return {"latency": latency, "throughput": throughput, "reliability": reliability}
