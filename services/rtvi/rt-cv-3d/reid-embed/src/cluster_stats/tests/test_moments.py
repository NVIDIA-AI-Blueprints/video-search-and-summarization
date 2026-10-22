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

"""Tests for :mod:`cluster_stats.moments`.

The contract every test checks is the same: a merged summary must be
indistinguishable from recomputing the moments over the concatenated raw
vectors. That is what lets compression throw the raw vectors away.
"""

from __future__ import annotations

import numpy as np
import pytest

from cluster_stats.moments import (
    combine_cluster_moments,
    combine_two_clusters,
    moments_from_vectors,
)


def _unit_rows(rng: np.random.Generator, n: int, d: int) -> np.ndarray:
    """Random L2-normalized rows, matching how embeddings reach compression."""
    v = rng.normal(size=(n, d))
    return v / np.linalg.norm(v, axis=1, keepdims=True)


class TestMomentsFromVectors:
    def test_matches_numpy(self) -> None:
        rng = np.random.default_rng(0)
        vectors = _unit_rows(rng, 17, 8)
        n, mean = moments_from_vectors(vectors)
        assert n == 17
        np.testing.assert_allclose(mean, vectors.mean(axis=0))

    def test_single_vector_is_its_own_mean(self) -> None:
        n, mean = moments_from_vectors(np.array([[0.6, 0.8]]))
        assert n == 1
        np.testing.assert_allclose(mean, [0.6, 0.8])

    def test_empty_batch_keeps_dimension(self) -> None:
        n, mean = moments_from_vectors(np.zeros((0, 5)))
        assert n == 0
        assert mean.shape == (5,) and not mean.any()

    def test_promotes_float32_to_float64(self) -> None:
        _, mean = moments_from_vectors(np.ones((3, 4), dtype=np.float32))
        assert mean.dtype == np.float64

    def test_rejects_1d_input(self) -> None:
        with pytest.raises(ValueError, match="2-D"):
            moments_from_vectors(np.array([1.0, 2.0, 3.0]))


class TestCombineTwoClusters:
    def test_matches_recomputation_from_raw(self) -> None:
        rng = np.random.default_rng(1)
        a = _unit_rows(rng, 13, 16)
        b = _unit_rows(rng, 29, 16)
        n, mean = combine_two_clusters(*moments_from_vectors(a), *moments_from_vectors(b))
        expected_n, expected_mean = moments_from_vectors(np.vstack([a, b]))
        assert n == expected_n == 42
        np.testing.assert_allclose(mean, expected_mean)

    def test_absorbs_empty_group(self) -> None:
        rng = np.random.default_rng(2)
        n_a, mean_a = moments_from_vectors(_unit_rows(rng, 5, 6))
        zeros = np.zeros(6)

        n, mean = combine_two_clusters(n_a, mean_a, 0, zeros)
        assert n == n_a
        np.testing.assert_allclose(mean, mean_a)

        n, mean = combine_two_clusters(0, zeros, n_a, mean_a)
        assert n == n_a
        np.testing.assert_allclose(mean, mean_a)

    def test_result_does_not_alias_inputs(self) -> None:
        mean_a = np.array([1.0, 1.0])
        _, mean = combine_two_clusters(1, mean_a, 0, mean_a)
        mean[0] = 99.0
        assert mean_a[0] == 1.0

    def test_is_symmetric(self) -> None:
        rng = np.random.default_rng(3)
        a = moments_from_vectors(_unit_rows(rng, 7, 4))
        b = moments_from_vectors(_unit_rows(rng, 11, 4))
        n_ab, mean_ab = combine_two_clusters(*a, *b)
        n_ba, mean_ba = combine_two_clusters(*b, *a)
        assert n_ab == n_ba
        np.testing.assert_allclose(mean_ab, mean_ba)


class TestCombineClusterMoments:
    @pytest.mark.parametrize("k", [1, 2, 3, 7])
    def test_matches_recomputation_from_raw(self, k: int) -> None:
        rng = np.random.default_rng(4 + k)
        groups = [_unit_rows(rng, int(rng.integers(1, 20)), 12) for _ in range(k)]
        summaries = [moments_from_vectors(g) for g in groups]

        n, mean = combine_cluster_moments(
            [s[0] for s in summaries],
            np.vstack([s[1] for s in summaries]),
        )
        expected_n, expected_mean = moments_from_vectors(np.vstack(groups))
        assert n == expected_n
        np.testing.assert_allclose(mean, expected_mean, atol=1e-12)

    def test_repeated_merges_stay_exact(self) -> None:
        """Compression re-merges the same row every cycle, so drift across
        many sequential merges is the failure mode that matters."""
        rng = np.random.default_rng(11)
        batches = [_unit_rows(rng, 9, 10) for _ in range(40)]
        state = moments_from_vectors(batches[0])
        for batch in batches[1:]:
            state = combine_two_clusters(*state, *moments_from_vectors(batch))

        expected_n, expected_mean = moments_from_vectors(np.vstack(batches))
        assert state[0] == expected_n
        np.testing.assert_allclose(state[1], expected_mean, atol=1e-12)

    def test_merged_mean_still_carries_the_total_variance(self) -> None:
        """The mean is kept alone because ``1 - ||mu||^2`` is the total variance
        of unit-norm members (see :mod:`cluster_stats.clt`). Merging must not
        break that, or a recompressed row would misreport its spread."""
        rng = np.random.default_rng(12)
        groups = [_unit_rows(rng, int(rng.integers(2, 30)), 24) for _ in range(6)]
        summaries = [moments_from_vectors(g) for g in groups]

        _n, mean = combine_cluster_moments(
            [s[0] for s in summaries],
            np.vstack([s[1] for s in summaries]),
        )

        union = np.vstack(groups)
        assert 1.0 - float(np.dot(mean, mean)) == pytest.approx(float(union.var(axis=0).sum()))

    def test_all_empty_groups_yield_zero_moments(self) -> None:
        n, mean = combine_cluster_moments([0, 0, 0], np.zeros((3, 4)))
        assert n == 0
        assert mean.shape == (4,) and not mean.any()

    def test_rejects_empty_group_list(self) -> None:
        with pytest.raises(ValueError, match="at least one group"):
            combine_cluster_moments([], np.zeros((0, 3)))

    def test_rejects_shape_mismatch(self) -> None:
        with pytest.raises(ValueError, match="shape mismatch"):
            combine_cluster_moments([1, 2], np.zeros((3, 4)))

    def test_does_not_mutate_inputs(self) -> None:
        means = np.arange(9, dtype=np.float64).reshape(3, 3)
        means_copy = means.copy()
        combine_cluster_moments([3, 4, 5], means)
        np.testing.assert_array_equal(means, means_copy)
