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

"""Estimate squared nearest-neighbor distances to a cluster from its moments
alone.

Compression keeps only one representative vector per group plus the group's
``(count, mean)`` summary (see :mod:`cluster_stats.moments`). A search that hits
the representative therefore knows one exact distance, when what it wants is
the distances to the group's actual nearest members.

This module recovers those without the raw vectors. For unit-norm embeddings
in high dimensions, the cosine similarity between a fixed query and a random
cluster member is a sum of many small contributions, so the CLT makes it
approximately Gaussian::

    S ~ N(mu_S, sigma_S^2),  mu_S = q . mu,  sigma_S = sigma_scale * (1 - ||mu||^2)

The k-th nearest member is the k-th largest similarity, whose expected
position in that Gaussian comes from Blom's order-statistic quantile, and
similarity converts to distance via ``D^2 = 2 - 2S`` for unit vectors. The
square root is never taken: Milvus reports squared L2, and estimates are
compared against those distances. Cost is O(d) per cluster regardless of how
many vectors it holds.

``mu_S`` and the Gaussian shape are both accurate on real data: against
brute-force distances over 17k (query, group) pairs from two ReID datasets,
``mu_S`` matches the true mean similarity to 1e-9, and feeding the *measured*
similarity spread into Blom's quantiles reproduces the true k-th distances to
1-3%. ``sigma_S`` is the whole difficulty, hence the section below.

Why sigma_S is the cluster's total variance
-------------------------------------------
Only the query's deviation from the cluster interacts with the members'
deviations, so the spread of ``S`` is the spread of ``delta_q . delta_x``. Two
vectors sharing ``m`` directions in random relative orientation have a dot
product of typical size ``||delta_q|| * ||delta_x|| / sqrt(m)``, and a member's
deviation has length ``sqrt(T)`` for the cluster's total variance
``T = sum_i sigma_i^2``. What differs is the query:

* A direction unrelated to the cluster has a unit-length deviation spread over
  all ``d`` dimensions, so it sees ``sqrt(T / d)`` -- which is exactly the
  textbook ``sqrt(sum_i q_i^2 sigma_i^2)`` this module used to offer as a
  second mode. Measured at 1.05x the true spread, so the algebra is sound.
* A real query is another view of the same person, so its deviation is a sample
  of the same cluster (``||delta_q|| ~ sqrt(T)``) confined to the same few
  directions the cluster varies along: about 6 of 1280, by the participation
  ratio of the covariance eigenvalues, because pose, lighting and scale each
  move many coordinates together. It sees ``T / sqrt(r)``.

The per-dimension form is therefore not merely small but the wrong shape: it
grows like ``sqrt(T)`` where the truth grows like ``T``. It measures 0.46x the
true spread on real queries, worsening from 0.69x at ``T = 0.016`` to 0.41x at
``T = 0.11``, which penalizes exactly the heavy, repeatedly merged rows. It was
removed once measured, leaving the total-variance form::

    sigma_S = sigma_scale * T,  T = sum_i sigma_i^2 = 1 - ||mu||^2

The identity on the right is exact for unit-norm members, which is what lets
the per-dimension variances go unstored and unfetched: the spread is a property
of the row's mean alone.

Fitting ``sigma_scale`` directly returns 0.325 on one dataset and 0.339 on
another, matching ``1 / sqrt(r)`` for the ``r`` measured independently from the
eigenvalues, so the form describes the embedding geometry rather than a tuned
constant. The shipped default (``CLT_SIGMA_SCALE`` in config.py) sits
deliberately below that fit. End to end through the search scoring, against
distances from the uncompressed collection, the median pooled distance over
8729 held-out queries runs 1.49x the truth with no estimation at all, 1.32x
under the per-dimension spread, 1.18x at the shipped scale and 0.99x at the
fitted 1/3, beating the pre-CLT weighted-representative scoring on ~85% of
queries.

``sigma_scale`` describes the embedding geometry, so re-fit it when changing
ReID model: hold out queries, score them against both the raw and compressed
collections, and minimize the error in the pooled distance rather than in
``sigma_S`` itself. The pooling keeps the k smallest estimates across a row's
groups, so it selects on the low tail and a too-large ``sigma_scale`` costs
more than a too-small one.

``mean`` must stay unnormalized. ``E[S] = q . mu`` follows from linearity of
expectation, and for unit-norm members ``||mu||^2 = 1 - T`` exactly, so the
mean's shortfall below unit length *is* the cluster's spread -- the one the
estimator now reads it back out of. Rescaling it to a unit centroid would
delete that spread from the distribution's location while ``sigma_S`` still
models it as scatter, inflating similarity by ``1 / ||mu||`` and understating
distance by 3-18% -- worst for tight clusters, where small ``D`` amplifies a
similarity error by ``1 / D^2``.

``clt_algorithm.md`` in this directory walks through all of the above with the
diagrams and states how each measurement was taken.
"""

from __future__ import annotations

from functools import lru_cache

import numpy as np
from scipy.stats import norm

from config import CLT_SIGMA_SCALE

__all__ = ["estimate_knn_normal_clt", "blom_z_scores", "similarity_sigma"]

# Floor on the similarity spread. A single-vector cluster has a unit-norm mean
# and hence zero total variance, which would make the Gaussian degenerate; this
# keeps sigma_S positive without perturbing the estimate.
_MIN_SIGMA_S = 1e-6


@lru_cache(maxsize=4096)
def blom_z_scores(count: int, k_max: int) -> np.ndarray:
    """Standard-normal quantiles of the top-``k`` order statistics of
    ``count`` samples, via Blom's plotting position::

        p_k = 1 - (k - 0.375) / (count + 0.25)
        z_k = Phi^-1(p_k)

    Depends only on ``(count, k_max)``, so results are cached: the search
    path re-derives them for every hit, and ``scipy.stats.norm.ppf`` is far
    more expensive than a dict lookup (68us vs 0.1us for five quantiles). The
    returned array is read-only because callers share the cached instance.

    ``count`` is a compressed row's weight, which is unbounded, so a long
    enough run can hold more than ``maxsize`` distinct weights and start
    evicting. That costs one ppf call, not correctness. If it ever shows up in
    a profile, note that ``z_k`` moves by under 0.05 for a 10% change in
    ``count``, so rounding large counts onto a coarse grid would bound the key
    space without measurably moving the estimate.

    Returns ``min(k_max, count)`` values, descending: ``k = 1`` is the
    largest similarity quantile, hence the nearest neighbor. Clamping
    ``k_max`` to ``count`` also keeps ``p_k`` inside ``(0, 1)``, where the
    inverse CDF is finite. Asking for more neighbors than the cluster holds
    is silently truncated rather than padded, since those neighbors do not
    exist.
    """
    k_eff = min(k_max, count)
    if k_eff <= 0:
        empty = np.empty(0, dtype=np.float64)
        empty.flags.writeable = False
        return empty
    k_vals = np.arange(1, k_eff + 1, dtype=np.float64)
    p_k = 1.0 - ((k_vals - 0.375) / (count + 0.25))
    z = norm.ppf(p_k)
    z.flags.writeable = False
    return z


def similarity_sigma(mean: np.ndarray, sigma_scale: float = CLT_SIGMA_SCALE) -> float:
    """Standard deviation of ``q . x`` over a cluster's members.

    Takes no query: as the module docstring derives, the spread a real query
    sees is a property of the cluster, proportional to its total variance. For
    unit-norm members that total is exactly ``1 - ||mu||^2``, so the mean is
    the only input needed -- the reason a search never fetches per-dimension
    variances.
    """
    mean = np.asarray(mean, dtype=np.float64)
    total_variance = max(0.0, 1.0 - float(np.dot(mean, mean)))
    return max(_MIN_SIGMA_S, sigma_scale * total_variance)


def estimate_knn_normal_clt(
    query_vec: np.ndarray,
    mean: np.ndarray,
    count: int = 1,
    k_max: int = 5,
    sigma_scale: float = CLT_SIGMA_SCALE,
) -> np.ndarray:
    """Estimated **squared** distances from ``query_vec`` to a cluster's
    ``k_max`` nearest members, nearest first.

    Squared to match Milvus, which reports squared L2 for
    ``metric_type="L2"``; estimates are pooled with those distances, so both
    sides have to be in the same units.

    Args:
        query_vec: ``[d]`` unit-norm query embedding.
        mean: ``[d]`` unnormalized mean of the cluster's unit-norm members. Do
            not normalize it; see the module docstring.
        count: number of members (a compressed row's ``weight``).
        k_max: how many neighbor distances to estimate.
        sigma_scale: ``sigma_S`` per unit of the cluster's total variance.

    Returns:
        ``min(k_max, count)`` non-decreasing values of ``2 - 2S``, empty when
        the cluster is empty. Clamped at 0, since a similarity above 1
        (possible when the Gaussian tail overshoots) has no real distance.
    """
    z_k = blom_z_scores(int(count), int(k_max))
    if z_k.size == 0:
        return np.empty(0, dtype=np.float64)

    query_vec = np.asarray(query_vec, dtype=np.float64)
    mean = np.asarray(mean, dtype=np.float64)

    mu_S = float(np.dot(query_vec, mean))
    sigma_S = similarity_sigma(mean, sigma_scale)

    # z_k descends with k, so similarity descends and distance ascends: the
    # result is ordered nearest-first.
    similarities = mu_S + z_k * sigma_S
    return np.maximum(0.0, 2.0 - 2.0 * similarities)
