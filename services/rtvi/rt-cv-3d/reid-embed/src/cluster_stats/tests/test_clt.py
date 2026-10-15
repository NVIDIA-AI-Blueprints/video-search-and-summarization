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

"""Tests for :mod:`cluster_stats.clt`.

The estimator replaces distances the raw vectors would have provided, so the
central test is accuracy against brute force: build a cluster, keep only its
``(count, mean)`` summary, and check the estimated k-th nearest distances
against the ones computed from the vectors themselves.

Accuracy is asserted on the geometry the estimator models -- spread confined to
a few directions, probed by a query inside that subspace, which is what
:func:`_low_rank_cluster` produces and what real ReID clusters look like. The
isotropic :func:`_cluster` is kept for the structural properties (ordering,
clamping, dtypes), which hold for any cluster.
"""

from __future__ import annotations

import numpy as np
import pytest
from scipy.stats import norm

from cluster_stats.clt import blom_z_scores, estimate_knn_normal_clt, similarity_sigma
from cluster_stats.moments import moments_from_vectors
from config import CLT_SIGMA_SCALE

DIM = 256
# Accuracy is asserted at this spread constant, passed explicitly rather than
# taken from the configured default so an environment override cannot retune the
# tests. It is tied to the cluster geometry: clt.py's derivation makes the scale
# 1 / sqrt(r) for a cluster spreading along r directions, so a synthetic cluster
# has to vary along 1 / scale^2 of them for the scale to be the right answer.
# ``test_configured_scale_stays_near_the_tested_one`` guards the config value
# against this one.
SIGMA_SCALE = 1.0 / 4.5
LOW_RANK = round(1.0 / SIGMA_SCALE**2)


def _unit(v: np.ndarray) -> np.ndarray:
    return v / np.linalg.norm(v)


def _cluster(rng: np.random.Generator, n: int, spread: float) -> np.ndarray:
    """``n`` unit-norm members scattered around one random direction."""
    center = _unit(rng.normal(size=DIM))
    members = center + rng.normal(scale=spread, size=(n, DIM))
    return members / np.linalg.norm(members, axis=1, keepdims=True)


def _low_rank_cluster(rng: np.random.Generator, n: int, spread: float, rank: int = LOW_RANK):
    """``n + 1`` unit-norm vectors varying along only ``rank`` directions.

    Returns ``(members, query)``, the query drawn from the same distribution
    so that -- like a tracker probing with another view of the same person --
    it lies in the subspace the members vary along. That alignment is what
    makes the spread proportional to the cluster's total variance.
    """
    center = _unit(rng.normal(size=DIM))
    basis = np.linalg.qr(rng.normal(size=(DIM, rank)))[0].T
    coeffs = rng.normal(scale=spread, size=(n + 1, rank))
    vectors = center + coeffs @ basis
    vectors /= np.linalg.norm(vectors, axis=1, keepdims=True)
    return vectors[:-1], vectors[-1]


def _query_near(rng: np.random.Generator, members: np.ndarray, spread: float) -> np.ndarray:
    """A query drawn near the cluster, as a tracker probe would be."""
    return _unit(members.mean(axis=0) + rng.normal(scale=spread, size=DIM))


class TestBlomZScores:
    def test_matches_the_specified_formula(self) -> None:
        z = blom_z_scores(100, 5)
        k = np.arange(1, 6)
        np.testing.assert_allclose(z, norm.ppf(1.0 - (k - 0.375) / 100.25))

    def test_descends_with_k(self) -> None:
        """k = 1 is the nearest neighbor, so its similarity quantile is the
        highest."""
        z = blom_z_scores(50, 8)
        assert np.all(np.diff(z) < 0)

    def test_truncates_to_cluster_size(self) -> None:
        assert len(blom_z_scores(3, 10)) == 3
        assert len(blom_z_scores(0, 10)) == 0

    def test_quantiles_stay_finite_at_the_boundary(self) -> None:
        """k == count is the worst case for p_k leaving (0, 1)."""
        for count in (1, 2, 5, 1000):
            assert np.all(np.isfinite(blom_z_scores(count, count)))

    def test_single_member_sits_at_the_median(self) -> None:
        np.testing.assert_allclose(blom_z_scores(1, 1), [0.0])

    def test_cached_result_is_read_only(self) -> None:
        """Callers share the cached array, so it must not be writable."""
        with pytest.raises(ValueError):
            blom_z_scores(10, 3)[0] = 1.0


class TestSimilaritySigma:
    def test_scales_the_total_variance_read_off_the_mean(self) -> None:
        """Pins the formula, and with it the identity it relies on: for
        unit-norm members the total variance is ``1 - ||mu||^2``, so the mean
        alone carries the spread. Called without a scale, so it also pins that
        the configured one is what the estimator falls back to."""
        rng = np.random.default_rng(20)
        members = _cluster(rng, 50, 0.1)
        _count, mean = moments_from_vectors(members)

        total_variance = float(members.var(axis=0).sum())
        assert similarity_sigma(mean) == pytest.approx(CLT_SIGMA_SCALE * (1.0 - float(np.dot(mean, mean))))
        assert similarity_sigma(mean) == pytest.approx(CLT_SIGMA_SCALE * total_variance, rel=1e-6)

    def test_configured_scale_stays_near_the_tested_one(self) -> None:
        """The deployed constant has to stay in the range the accuracy tests
        below were fitted over -- 1/6 undershoots the spread badly and 1/3 is
        the direct fit, past which pooling starts turning every group into a
        false near-match."""
        assert 1.0 / 6.0 <= CLT_SIGMA_SCALE <= 1.0 / 3.0

    def test_tracks_the_measured_spread_of_a_low_rank_cluster(self) -> None:
        """What ties the default scale to something observable: on a cluster
        spreading along ``LOW_RANK`` directions, the predicted spread has to
        match the spread the query actually sees. Held on average rather than
        per draw, since which directions a single query lands in moves the
        spread it sees by tens of percent."""
        rng = np.random.default_rng(21)
        ratios = []
        for _ in range(10):
            members, query = _low_rank_cluster(rng, 60, 0.15)
            _count, mean = moments_from_vectors(members)
            ratios.append(similarity_sigma(mean, SIGMA_SCALE) / float((members @ query).std()))

        assert np.mean(ratios) == pytest.approx(1.0, rel=0.2)
        assert np.max(np.abs(np.array(ratios) - 1.0)) < 0.4

    def test_is_linear_in_the_scale(self) -> None:
        rng = np.random.default_rng(22)
        _count, mean = moments_from_vectors(_cluster(rng, 30, 0.1))
        assert similarity_sigma(mean, 0.4) == pytest.approx(2.0 * similarity_sigma(mean, 0.2))

    def test_single_member_cluster_gets_a_positive_floor(self) -> None:
        """Its mean is the member itself, so the total variance is zero and the
        Gaussian would be degenerate."""
        rng = np.random.default_rng(23)
        _count, mean = moments_from_vectors(_unit(rng.normal(size=DIM))[None, :])
        assert 0.0 < similarity_sigma(mean) <= 1e-6


class TestEstimateKnnNormalClt:
    def test_single_member_cluster_is_exact(self) -> None:
        """With one member the mean *is* that vector and the spread is zero,
        so the estimate should reproduce the true distance."""
        rng = np.random.default_rng(0)
        member = _unit(rng.normal(size=DIM))
        query = _unit(rng.normal(size=DIM))
        count, mean = moments_from_vectors(member[None, :])

        est = estimate_knn_normal_clt(query, mean, count, k_max=5)

        assert len(est) == 1
        np.testing.assert_allclose(est[0], np.linalg.norm(member - query) ** 2, rtol=1e-6)

    def test_distances_ascend_with_k(self) -> None:
        rng = np.random.default_rng(1)
        members = _cluster(rng, 200, 0.1)
        count, mean = moments_from_vectors(members)
        est = estimate_knn_normal_clt(_query_near(rng, members, 0.1), mean, count, k_max=6)
        assert np.all(np.diff(est) >= 0)

    def test_truncates_to_the_cluster_size(self) -> None:
        """A row cannot supply more neighbors than the vectors it stands for."""
        rng = np.random.default_rng(2)
        count, mean = moments_from_vectors(_cluster(rng, 3, 0.1))
        assert len(estimate_knn_normal_clt(_unit(rng.normal(size=DIM)), mean, count, k_max=10)) == 3

    @pytest.mark.parametrize(
        "n,spread",
        [
            (20, 0.1),
            (60, 0.15),
            (200, 0.1),
            (200, 0.25),
            (1000, 0.15),
        ],
    )
    def test_tracks_brute_force_knn(self, n: int, spread: float) -> None:
        """Estimated k-th nearest distances must stay close to the ones the
        raw vectors give, across cluster sizes and spreads."""
        rng = np.random.default_rng(11)
        errors = []
        for _ in range(30):
            members, query = _low_rank_cluster(rng, n, spread)
            count, mean = moments_from_vectors(members)
            true = np.sort(np.linalg.norm(members - query, axis=1))[:5]
            # Estimates are squared; compare as plain distances so the
            # tolerance stays a relative error on distance, not on its square.
            est = np.sqrt(estimate_knn_normal_clt(query, mean, count, k_max=5, sigma_scale=SIGMA_SCALE))
            errors.append((est - true[: len(est)]) / true[: len(est)])

        errors = np.array(errors)
        assert np.abs(errors.mean()) < 0.1, f"bias {errors.mean():+.3f}"
        assert np.abs(errors).mean() < 0.13

    def test_pins_the_formula_term_by_term(self) -> None:
        rng = np.random.default_rng(13)
        members = _cluster(rng, 40, 0.1)
        count, mean = moments_from_vectors(members)
        query = _query_near(rng, members, 0.1)

        est = estimate_knn_normal_clt(query, mean, count, k_max=4, sigma_scale=0.4)

        mu_s = float(np.dot(query, mean))
        sigma_s = 0.4 * (1.0 - float(np.dot(mean, mean)))
        k = np.arange(1, 5)
        z = norm.ppf(1.0 - (k - 0.375) / (count + 0.25))
        expected = np.maximum(0.0, 2.0 - 2.0 * (mu_s + z * sigma_s))
        np.testing.assert_allclose(est, expected)

    def test_normalizing_the_mean_would_erase_the_spread(self) -> None:
        """Why the stored mean must stay unnormalized: its shortfall below unit
        length *is* the cluster's spread, so a unit centroid reports a cluster
        whose members all sit on top of each other."""
        rng = np.random.default_rng(4)
        members = _cluster(rng, 40, 0.1)
        count, mean = moments_from_vectors(members)
        query = _query_near(rng, members, 0.1)

        est = estimate_knn_normal_clt(query, mean, count, k_max=4, sigma_scale=SIGMA_SCALE)
        from_unit_centroid = estimate_knn_normal_clt(query, _unit(mean), count, k_max=4, sigma_scale=SIGMA_SCALE)

        assert np.all(np.diff(est) > 0)
        np.testing.assert_allclose(from_unit_centroid, from_unit_centroid[0], atol=1e-5)

    def test_returns_squared_distances(self) -> None:
        """Milvus reports squared L2 for metric_type=L2, and the search path
        pools estimates with those distances, so the units must agree."""
        rng = np.random.default_rng(5)
        members, query = _low_rank_cluster(rng, 80, 0.15)
        count, mean = moments_from_vectors(members)

        est = estimate_knn_normal_clt(query, mean, count, k_max=5, sigma_scale=SIGMA_SCALE)

        # These clusters sit ~0.35 squared units away, so an unsquared return
        # (~0.59) would miss this tolerance outright.
        true_squared = np.sort(((members - query) ** 2).sum(axis=1))[:5]
        np.testing.assert_allclose(est, true_squared, rtol=0.15)

    def test_empty_cluster_returns_nothing(self) -> None:
        est = estimate_knn_normal_clt(np.ones(DIM), np.zeros(DIM), 0, k_max=5)
        assert est.size == 0

    def test_zero_spread_collapses_all_neighbors_together(self) -> None:
        """Every member sits at the mean, so all k distances coincide."""
        rng = np.random.default_rng(6)
        member = _unit(rng.normal(size=DIM))
        count, mean = moments_from_vectors(np.tile(member, (20, 1)))
        est = estimate_knn_normal_clt(_unit(rng.normal(size=DIM)), mean, count, k_max=5)
        np.testing.assert_allclose(est, est[0], atol=1e-5)

    def test_distances_never_go_negative(self) -> None:
        """A query on top of a loose cluster can push the Gaussian past
        similarity 1, which has no real distance."""
        rng = np.random.default_rng(7)
        mean = 0.7 * _unit(rng.normal(size=DIM))
        est = estimate_knn_normal_clt(_unit(mean), mean, 10_000, k_max=5, sigma_scale=SIGMA_SCALE)
        assert np.all(est == 0.0), "the clamp is no longer being exercised"

    def test_accepts_float32_inputs(self) -> None:
        """Moments arrive from Milvus as float32 arrays."""
        rng = np.random.default_rng(8)
        members = _cluster(rng, 30, 0.1)
        count, mean = moments_from_vectors(members)
        est = estimate_knn_normal_clt(
            _query_near(rng, members, 0.1).astype(np.float32),
            mean.astype(np.float32),
            count,
            k_max=3,
        )
        assert est.dtype == np.float64 and len(est) == 3
