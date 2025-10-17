######################################################################################################
# SPDX-FileCopyrightText: Copyright (c) 2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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
Latency tracking for VSS performance benchmarks.

This module provides a comprehensive latency tracking system that can be used
across all benchmark modes (single_file, file_burst, max_live_streams, alert_review_burst).
"""

from typing import Any, Dict, List

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
        self.max_latency = 0

    def record_latency(self, latency_seconds: float, stream_id: str = None):
        """
        Record a latency measurement.

        Args:
            latency_seconds: Latency measurement in seconds
            stream_id: Identifier for the stream/file (optional)
        """
        if stream_id is None:
            stream_id = "unknown"

        if stream_id not in self.stream_latencies:
            self.stream_latencies[stream_id] = []

        self.stream_latencies[stream_id].append(latency_seconds)
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

        if not all_latencies:
            return {"avg_latency": 0, "max_latency": 0, "min_latency": 0, "total_measurements": 0}

        return {
            "avg_latency": sum(all_latencies) / len(all_latencies),
            "max_latency": self.max_latency,
            "min_latency": min(all_latencies),
            "total_measurements": len(all_latencies),
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

    def remove_stream(self, stream_id: str):
        """Remove a stream's latency data from tracking"""
        if stream_id in self.stream_latencies:
            del self.stream_latencies[stream_id]

    def clear(self):
        self.stream_latencies = {}
        self.max_latency = 0
