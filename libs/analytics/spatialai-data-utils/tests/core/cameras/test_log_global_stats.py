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

"""Tests for ``core.cameras.clustering.log_global_stats``.

The deep ``CameraClusterManager`` multi-pass clustering paths are
covered by the existing ``test_camera_clustering.py``; this file
fills in the small standalone logger helper that the cluster
parameter-search code uses to surface threshold sanity warnings.
"""

import logging

from spatialai_data_utils.core.cameras.clustering import log_global_stats


class TestLogGlobalStats:
    def test_logs_distance_range_when_available(self, caplog):
        with caplog.at_level(logging.INFO):
            log_global_stats(
                stats={
                    "distance_min": 4.0, "distance_max": 20.0,
                    "overlap_min": 0.05, "overlap_max": 0.5,
                },
                overlap_threshold=0.2, distance_threshold=10.0,
                warn=False,
            )
        assert "Global distance range" in caplog.text
        assert "Global overlap range" in caplog.text

    def test_warns_when_distance_threshold_below_global_min(self, caplog):
        with caplog.at_level(logging.WARNING):
            log_global_stats(
                stats={
                    "distance_min": 5.0, "distance_max": 20.0,
                    "overlap_min": 0.05, "overlap_max": 0.5,
                },
                overlap_threshold=0.2, distance_threshold=1.0,  # below 5.0
                warn=True,
            )
        assert "BELOW global min" in caplog.text

    def test_warns_when_distance_threshold_above_global_max(self, caplog):
        with caplog.at_level(logging.WARNING):
            log_global_stats(
                stats={
                    "distance_min": 5.0, "distance_max": 20.0,
                    "overlap_min": 0.05, "overlap_max": 0.5,
                },
                overlap_threshold=0.2, distance_threshold=100.0,  # > 20.0
                warn=True,
            )
        assert "exceeds global max" in caplog.text

    def test_warns_when_overlap_threshold_below_global_min(self, caplog):
        with caplog.at_level(logging.WARNING):
            log_global_stats(
                stats={
                    "distance_min": 5.0, "distance_max": 20.0,
                    "overlap_min": 0.1, "overlap_max": 0.5,
                },
                overlap_threshold=0.01, distance_threshold=10.0,
                warn=True,
            )
        assert "BELOW global min" in caplog.text

    def test_warns_when_overlap_threshold_above_global_max(self, caplog):
        with caplog.at_level(logging.WARNING):
            log_global_stats(
                stats={
                    "distance_min": 5.0, "distance_max": 20.0,
                    "overlap_min": 0.05, "overlap_max": 0.5,
                },
                overlap_threshold=0.99, distance_threshold=10.0,
                warn=True,
            )
        assert "exceeds global max" in caplog.text

    def test_warns_when_distance_range_unavailable(self, caplog):
        """When ``distance_min`` is ``None``, the function emits the
        'range: unavailable' warning instead of any threshold check."""
        with caplog.at_level(logging.WARNING):
            log_global_stats(
                stats={"distance_min": None, "overlap_min": 0.05, "overlap_max": 0.5},
                overlap_threshold=0.2, distance_threshold=10.0,
            )
        assert "Global distance range: unavailable" in caplog.text

    def test_warns_when_overlap_range_unavailable(self, caplog):
        with caplog.at_level(logging.WARNING):
            log_global_stats(
                stats={"distance_min": 4.0, "distance_max": 20.0,
                       "overlap_min": None},
                overlap_threshold=0.2, distance_threshold=10.0,
            )
        assert "Global overlap range: unavailable" in caplog.text

    def test_warn_false_suppresses_threshold_warnings(self, caplog):
        """When ``warn=False`` the threshold sanity checks don't fire
        (the bare-range info logs still do)."""
        with caplog.at_level(logging.WARNING):
            log_global_stats(
                stats={
                    "distance_min": 5.0, "distance_max": 20.0,
                    "overlap_min": 0.05, "overlap_max": 0.5,
                },
                overlap_threshold=0.99,  # would normally trigger 'exceeds max'
                distance_threshold=100.0,
                warn=False,
            )
        # No warning records were emitted.
        assert "BELOW global min" not in caplog.text
        assert "exceeds global max" not in caplog.text
