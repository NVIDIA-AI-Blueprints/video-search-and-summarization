######################################################################################################
# SPDX-FileCopyrightText: Copyright (c) 2025-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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
######################################################################################################

"""
Latency tracking for RTVI performance benchmarks.

This module provides a comprehensive latency tracking system that can be used
across all benchmark modes (single_file, file_burst, max_live_streams).
"""

import time
from typing import Any, Dict, List, Optional

import numpy as np


class LatencyTracker:
    """
    Comprehensive latency tracking for performance benchmarks.

    Supports:
    - Per-stream/per-file latency tracking
    - Statistical analysis (mean, min, max, p95)
    - Recent measurements analysis
    - Stability assessment
    - Detailed reporting
    """

    def __init__(self):
        self.stream_latencies = {}  # Dict[str, List[float]]
        self.stream_latency_records = {}  # Dict[str, List[Dict[str, float]]]
        self.max_latency = 0
        self._ignored_streams: set = set()  # stream IDs removed mid-test; ignored by record_latency

    def record_latency(
        self,
        latency_seconds: float,
        stream_id: str = None,
        recorded_at: Optional[float] = None,
    ):
        """
        Record a latency measurement.

        Args:
            latency_seconds: Latency measurement in seconds
            stream_id: Identifier for the stream/file (optional)
            recorded_at: Wall-clock timestamp when the latency was observed.
        """
        if stream_id is None:
            stream_id = "unknown"

        if stream_id in self._ignored_streams:
            return

        if stream_id not in self.stream_latencies:
            self.stream_latencies[stream_id] = []
        if stream_id not in self.stream_latency_records:
            self.stream_latency_records[stream_id] = []

        if recorded_at is None:
            recorded_at = time.time()

        self.stream_latencies[stream_id].append(latency_seconds)
        self.stream_latency_records[stream_id].append(
            {"latency": latency_seconds, "recorded_at": recorded_at}
        )
        self.max_latency = max(self.max_latency, latency_seconds)

    def get_stats(self) -> Dict[str, Any]:
        """
        Get combined latency statistics across all streams.

        Returns:
            Dictionary with avg_latency, max_latency, min_latency, total_measurements
        """
        if not self.stream_latencies:
            return {"avg_latency": 0, "max_latency": 0, "min_latency": 0, "total_measurements": 0}

        # Flatten all latencies from all streams
        all_latencies = []
        for stream_readings in self.stream_latencies.values():
            all_latencies.extend(stream_readings)

        moving_average_latencies = []
        for stream_readings in self.stream_latencies.values():
            if len(stream_readings) < 3:
                moving_average_latencies.extend(stream_readings)
                continue
            moving_average_latencies.extend(stream_readings[-3:])

        if not all_latencies:
            return {
                "avg_latency": 0,
                "max_latency": 0,
                "min_latency": 0,
                "total_measurements": 0,
                "moving_average_latency": 0,
            }

        return {
            "avg_latency": sum(all_latencies) / len(all_latencies),
            "max_latency": self.max_latency,
            "min_latency": min(all_latencies),
            "total_measurements": len(all_latencies),
            "moving_average_latency": sum(moving_average_latencies) / len(moving_average_latencies),
        }

    def is_stable(self, threshold: float = 30.0) -> bool:
        """
        Check if the system is stable by verifying that the p95 of the 3 most recent readings
        across all streams is below the specified threshold.

        Args:
            threshold: Maximum acceptable p95 latency for stability

        Returns:
            True if system is stable, False otherwise
        """
        if not self.stream_latencies:
            return True  # No data means stable

        # Use the existing get_recent_p95 method instead of recalculating
        recent_p95 = self.get_recent_p95()
        return recent_p95 <= threshold

    def get_recent_p95(self) -> float:
        """
        Get the p95 latency of the 3 most recent readings across all streams.

        Returns:
            P95 latency of recent measurements
        """
        if not self.stream_latencies:
            return 0.0

        # Collect the 3 most recent readings from all streams
        all_recent_readings = []
        for stream_id, latencies in self.stream_latencies.items():
            if latencies:
                # Get last 3 readings from this stream
                recent_readings = latencies[-3:]
                all_recent_readings.extend(recent_readings)

        if not all_recent_readings:
            return 0.0

        # Calculate p95 of all recent readings
        return float(np.percentile(all_recent_readings, 95))

    def get_recent_percentiles(self) -> Dict[str, float]:
        """
        Get a full set of latency percentiles (p50, p75, p90, p95, p99) computed from
        the 3 most recent readings per stream — the same window used by ``get_recent_p95``
        and ``is_stable``.

        Returns:
            Dict with keys ``p50``, ``p75``, ``p90``, ``p95``, ``p99`` (all in seconds).
            All values are 0.0 when there are no measurements.
        """
        empty = {"p50": 0.0, "p75": 0.0, "p90": 0.0, "p95": 0.0, "p99": 0.0}
        if not self.stream_latencies:
            return empty

        all_recent_readings = []
        for latencies in self.stream_latencies.values():
            if latencies:
                all_recent_readings.extend(latencies[-3:])

        if not all_recent_readings:
            return empty

        arr = np.array(all_recent_readings)
        return {
            "p50": float(np.percentile(arr, 50)),
            "p75": float(np.percentile(arr, 75)),
            "p90": float(np.percentile(arr, 90)),
            "p95": float(np.percentile(arr, 95)),
            "p99": float(np.percentile(arr, 99)),
        }

    def get_recent_per_stream_avg(self, recent_count: int = 3) -> Dict[str, float]:
        """
        Get average latency of most recent readings for each stream individually.

        Args:
            recent_count: Number of recent readings to average

        Returns:
            Dictionary mapping stream_id to average of recent latencies
        """
        if not self.stream_latencies:
            return {}

        recent_averages = {}
        for stream_id, latencies in self.stream_latencies.items():
            if not latencies:
                recent_averages[stream_id] = 0.0
                continue

            recent_latencies = latencies[-recent_count:]
            recent_averages[stream_id] = sum(recent_latencies) / len(recent_latencies)

        return recent_averages

    def get_recent_per_stream_values(self, recent_count: int = 3) -> Dict[str, List[float]]:
        """
        Get most recent readings for each stream individually.

        Args:
            recent_count: Number of recent readings to return

        Returns:
            Dictionary mapping stream_id to list of recent latency values
        """
        if not self.stream_latencies:
            return {}

        recent_values = {}
        for stream_id, latencies in self.stream_latencies.items():
            if not latencies:
                recent_values[stream_id] = []
                continue

            recent_values[stream_id] = latencies[-recent_count:]

        return recent_values

    def get_stream_measurement_counts(self) -> Dict[str, int]:
        """Return the current number of latency measurements recorded per stream."""
        return {
            stream_id: len(latencies)
            for stream_id, latencies in self.stream_latencies.items()
            if stream_id not in self._ignored_streams
        }

    def get_fresh_stream_coverage(
        self,
        baseline_counts: Dict[str, int],
        active_stream_ids: List[str],
        min_new_measurements: int = 1,
    ) -> Dict[str, Any]:
        """Measure how many active streams emitted new latency samples since a baseline."""
        if not active_stream_ids:
            return {
                "active_streams": 0,
                "fresh_streams": 0,
                "coverage": 0.0,
                "stale_streams": [],
            }

        current_counts = self.get_stream_measurement_counts()
        fresh_streams = []
        stale_streams = []

        for stream_id in active_stream_ids:
            new_measurements = current_counts.get(stream_id, 0) - baseline_counts.get(stream_id, 0)
            if new_measurements >= min_new_measurements:
                fresh_streams.append(stream_id)
            else:
                stale_streams.append(stream_id)

        return {
            "active_streams": len(active_stream_ids),
            "fresh_streams": len(fresh_streams),
            "coverage": len(fresh_streams) / len(active_stream_ids),
            "stale_streams": stale_streams,
        }

    def get_per_stream_stats(self) -> Dict[str, Dict[str, float]]:
        """
        Get detailed statistics for each stream individually.

        Returns:
            Dictionary mapping stream_id to stats dict (avg, max, min, count)
        """
        per_stream_stats = {}

        for stream_id, latencies in self.stream_latencies.items():
            if not latencies:
                per_stream_stats[stream_id] = {
                    "avg_latency": 0.0,
                    "max_latency": 0.0,
                    "min_latency": 0.0,
                    "total_measurements": 0,
                }
                continue

            per_stream_stats[stream_id] = {
                "avg_latency": sum(latencies) / len(latencies),
                "max_latency": max(latencies),
                "min_latency": min(latencies),
                "total_measurements": len(latencies),
            }

        return per_stream_stats

    def get_per_stream_stats_str(self) -> str:
        """
        Get detailed statistics for each stream individually including recent 3 values.

        Returns:
            Multi-line string with formatted per-stream statistics
        """
        per_stream_stats = self.get_per_stream_stats()
        recent_values = self.get_recent_per_stream_values(recent_count=3)

        result_lines = []
        for stream_id, stats in per_stream_stats.items():
            recent_vals = recent_values.get(stream_id, [])
            stats_with_recent = dict(stats)
            stats_with_recent["recent_3_values"] = [round(val, 2) for val in recent_vals]
            result_lines.append(f"{stream_id}: {stats_with_recent}")

        return "\n".join(result_lines)

    def get_all_latencies(self) -> Dict[str, List[float]]:
        """Get the full history of latencies for each stream"""
        return self.stream_latencies.copy()

    def get_all_latency_records(self) -> Dict[str, List[Dict[str, float]]]:
        """Get latency values with their local observation timestamps for each stream."""
        return {
            stream_id: [dict(record) for record in records]
            for stream_id, records in self.stream_latency_records.items()
        }

    def remove_stream(self, stream_id: str):
        """Remove a stream's latency data from tracking and ignore future recordings from it."""
        self.stream_latencies.pop(stream_id, None)
        self.stream_latency_records.pop(stream_id, None)
        self._ignored_streams.add(stream_id)

    def clear(self, reset_ignored: bool = False):
        """Reset latency history for a clean measurement window.

        By default this preserves ``_ignored_streams`` so monitoring threads
        from streams deleted mid-test cannot re-populate the tracker after a
        clear(). Pass ``reset_ignored=True`` at probe boundaries (e.g. after
        a binary-search teardown + cooldown) where a fully fresh baseline is
        wanted; the previous probe's ignored set is no longer relevant.
        """
        self.stream_latencies = {}
        self.stream_latency_records = {}
        self.max_latency = 0
        if reset_ignored:
            self._ignored_streams.clear()
