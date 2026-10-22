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

"""Tests for :mod:`cluster_manager.clustering`."""

from __future__ import annotations

import numpy as np

from cluster_manager.clustering import cluster_by_constrained_affinity


def _identity_affinity(n: int) -> np.ndarray:
    """Affinity matrix: 1 on diagonal, 0 off-diagonal."""
    m = np.zeros((n, n), dtype=np.float32)
    np.fill_diagonal(m, 1.0)
    return m


def _all_high_affinity(n: int, value: float = 0.95) -> np.ndarray:
    m = np.full((n, n), value, dtype=np.float32)
    np.fill_diagonal(m, 1.0)
    return m


def _sort_clusters(clusters: list[set[int]]) -> list[set[int]]:
    """Canonicalize clusters for comparison."""
    return sorted(clusters, key=lambda s: (len(s), sorted(s)))


class TestClusterByConstrainedAffinity:
    def test_single_node(self) -> None:
        result = cluster_by_constrained_affinity(_identity_affinity(1), [], merge_threshold=0.5)
        assert result == [{0}]

    def test_no_valid_pairs_returns_singletons(self) -> None:
        result = cluster_by_constrained_affinity(_identity_affinity(4), [], merge_threshold=0.5)
        assert _sort_clusters(result) == [{0}, {1}, {2}, {3}]

    def test_all_high_affinity_merges_into_one(self) -> None:
        result = cluster_by_constrained_affinity(_all_high_affinity(5, 0.95), [], merge_threshold=0.5)
        assert result == [{0, 1, 2, 3, 4}]

    def test_two_groups_clear_separation(self) -> None:
        # Nodes 0,1,2 are strongly similar; nodes 3,4 are strongly similar.
        # Cross-group affinity is well below threshold.
        m = np.array(
            [
                [1.0, 0.95, 0.95, 0.10, 0.10],
                [0.95, 1.0, 0.95, 0.10, 0.10],
                [0.95, 0.95, 1.0, 0.10, 0.10],
                [0.10, 0.10, 0.10, 1.0, 0.95],
                [0.10, 0.10, 0.10, 0.95, 1.0],
            ],
            dtype=np.float32,
        )
        result = cluster_by_constrained_affinity(m, [], merge_threshold=0.5)
        assert _sort_clusters(result) == [{3, 4}, {0, 1, 2}]

    def test_cannot_link_blocks_merge(self) -> None:
        m = _all_high_affinity(3, 0.95)
        # Without constraints, all three would merge. Block 0-2.
        result = cluster_by_constrained_affinity(m, [frozenset({0, 2})], merge_threshold=0.5)
        # Expected: {0,1} and {2}, or {1,2} and {0}, but NOT all three in one cluster.
        assert all(not ({0, 2}.issubset(c)) for c in result)
        assert sum(len(c) for c in result) == 3

    def test_cannot_link_transitive_effect(self) -> None:
        # 4 nodes, all similar. Constraints: 0-2 and 1-3
        # Possible outcomes depend on greedy tie-breaking; must not contain
        # any blocked pair in the same cluster.
        m = _all_high_affinity(4, 0.95)
        result = cluster_by_constrained_affinity(m, [frozenset({0, 2}), frozenset({1, 3})], merge_threshold=0.5)
        for cluster in result:
            assert not {0, 2}.issubset(cluster)
            assert not {1, 3}.issubset(cluster)

    def test_threshold_at_exact_boundary_allows_merge(self) -> None:
        # affinity exactly = threshold → should merge (>= in the code)
        m = np.array(
            [
                [1.0, 0.5],
                [0.5, 1.0],
            ],
            dtype=np.float32,
        )
        result = cluster_by_constrained_affinity(m, [], merge_threshold=0.5)
        assert result == [{0, 1}]

    def test_threshold_above_affinity_keeps_singletons(self) -> None:
        m = np.array(
            [
                [1.0, 0.49],
                [0.49, 1.0],
            ],
            dtype=np.float32,
        )
        result = cluster_by_constrained_affinity(m, [], merge_threshold=0.5)
        assert _sort_clusters(result) == [{0}, {1}]

    def test_all_indices_appear_exactly_once(self) -> None:
        rng = np.random.default_rng(42)
        n = 15
        m = rng.uniform(0.0, 1.0, size=(n, n)).astype(np.float32)
        m = (m + m.T) / 2
        np.fill_diagonal(m, 1.0)
        result = cluster_by_constrained_affinity(m, [], merge_threshold=0.3)
        flat = [i for cluster in result for i in cluster]
        assert sorted(flat) == list(range(n))
