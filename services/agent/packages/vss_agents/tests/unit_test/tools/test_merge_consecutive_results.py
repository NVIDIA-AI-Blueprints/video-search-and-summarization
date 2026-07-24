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
"""Tests for _merge_consecutive_results improvements: similarity threshold and sort order."""

import pytest

from vss_agents.tools.search import _SIMILARITY_RATIO_THRESHOLD
from vss_agents.tools.search import SearchResult
from vss_agents.tools.search import _merge_consecutive_results


def _make_result(start: str, end: str, similarity: float, sensor_id: str = "s1") -> SearchResult:
    return SearchResult(
        video_name="v.mp4",
        description="desc",
        start_time=start,
        end_time=end,
        sensor_id=sensor_id,
        screenshot_url="",
        similarity=similarity,
    )


# ---------------------------------------------------------------------------
# Improvement 1: similarity threshold prevents merging dissimilar chunks
# ---------------------------------------------------------------------------


class TestSimilarityThreshold:
    def test_compatible_chunks_are_merged(self):
        # similarity 0.90 and 0.95 → ratio = 0.90/0.95 ≈ 0.947 ≥ 0.9
        r1 = _make_result("2025-01-01T00:00:00Z", "2025-01-01T00:01:00Z", 0.90)
        r2 = _make_result("2025-01-01T00:00:30Z", "2025-01-01T00:01:30Z", 0.95)
        results = _merge_consecutive_results([r1, r2])
        assert len(results) == 1, "Compatible chunks should be merged into one"

    def test_incompatible_chunks_are_not_merged(self):
        # similarity 0.95 and 0.50 → ratio = 0.50/0.95 ≈ 0.526 < 0.9
        r1 = _make_result("2025-01-01T00:00:00Z", "2025-01-01T00:01:00Z", 0.95)
        r2 = _make_result("2025-01-01T00:00:30Z", "2025-01-01T00:01:30Z", 0.50)
        results = _merge_consecutive_results([r1, r2])
        assert len(results) == 2, "Incompatible similarity chunks must NOT be merged"

    def test_non_overlapping_chunks_not_merged_regardless_of_similarity(self):
        # No time overlap; should never merge even with identical similarity
        r1 = _make_result("2025-01-01T00:00:00Z", "2025-01-01T00:01:00Z", 0.90)
        r2 = _make_result("2025-01-01T00:02:00Z", "2025-01-01T00:03:00Z", 0.91)
        results = _merge_consecutive_results([r1, r2])
        assert len(results) == 2

    def test_boundary_ratio_at_threshold(self):
        # ratio == _SIMILARITY_RATIO_THRESHOLD exactly should be merged (>= threshold)
        r1 = _make_result("2025-01-01T00:00:00Z", "2025-01-01T00:01:00Z", 1.0)
        r2 = _make_result("2025-01-01T00:00:30Z", "2025-01-01T00:01:30Z", _SIMILARITY_RATIO_THRESHOLD)
        results = _merge_consecutive_results([r1, r2])
        assert len(results) == 1

    def test_both_zero_similarity_merged(self):
        # pair_max == 0 branch: both zeros should merge
        r1 = _make_result("2025-01-01T00:00:00Z", "2025-01-01T00:01:00Z", 0.0)
        r2 = _make_result("2025-01-01T00:00:30Z", "2025-01-01T00:01:30Z", 0.0)
        results = _merge_consecutive_results([r1, r2])
        assert len(results) == 1


# ---------------------------------------------------------------------------
# Improvement 2: output is sorted by descending similarity
# ---------------------------------------------------------------------------


class TestSortByDescendingSimilarity:
    def test_results_ordered_high_to_low_similarity(self):
        # Two non-overlapping groups on same sensor; lower-similarity group first in input
        r1 = _make_result("2025-01-01T00:00:00Z", "2025-01-01T00:01:00Z", 0.60)
        r2 = _make_result("2025-01-01T00:02:00Z", "2025-01-01T00:03:00Z", 0.95)
        results = _merge_consecutive_results([r1, r2])
        assert len(results) == 2
        assert results[0].similarity >= results[1].similarity, "Best match should come first"

    def test_single_result_unchanged(self):
        r1 = _make_result("2025-01-01T00:00:00Z", "2025-01-01T00:01:00Z", 0.75)
        results = _merge_consecutive_results([r1])
        assert len(results) == 1
        assert results[0].similarity == pytest.approx(0.75)

    def test_merged_result_sorted_among_others(self):
        # sensor s1: two compatible overlapping chunks (0.78, 0.82 → ratio 0.951 ≥ 0.9) merge to avg 0.80
        # sensor s2: single chunk with similarity 0.90
        # Expected order: s2 (0.90), then s1 merged (0.80)
        s1_r1 = _make_result("2025-01-01T00:00:00Z", "2025-01-01T00:01:00Z", 0.78, sensor_id="s1")
        s1_r2 = _make_result("2025-01-01T00:00:30Z", "2025-01-01T00:01:30Z", 0.82, sensor_id="s1")
        s2_r1 = _make_result("2025-01-01T00:00:00Z", "2025-01-01T00:01:00Z", 0.90, sensor_id="s2")
        results = _merge_consecutive_results([s1_r1, s1_r2, s2_r1])
        assert len(results) == 2
        assert results[0].sensor_id == "s2"
        assert results[1].sensor_id == "s1"
        assert results[0].similarity > results[1].similarity

    def test_no_timestamps_pass_through_sorted_by_similarity(self):
        # Results without timestamps bypass time-merge; should still be sorted by similarity
        r1 = SearchResult(
            video_name="v",
            description="d",
            start_time="",
            end_time="",
            sensor_id="s1",
            screenshot_url="",
            similarity=0.4,
        )
        r2 = SearchResult(
            video_name="v",
            description="d",
            start_time="",
            end_time="",
            sensor_id="s1",
            screenshot_url="",
            similarity=0.9,
        )
        results = _merge_consecutive_results([r1, r2])
        assert len(results) == 2
        assert results[0].similarity >= results[1].similarity
