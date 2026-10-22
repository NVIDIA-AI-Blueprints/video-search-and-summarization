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

"""Per-dimension ``(count, mean)`` summaries and exact O(d) merging.

Every row in the compressed collection stands for the group of raw embeddings
that were assigned to it during compression: ``weight`` is the group size and
``mean`` is the per-dimension arithmetic mean of that group. Keeping the mean
alongside the representative lets later stages reason about the group's spread
without re-reading the raw vectors, which compression has already deleted --
for unit-norm members the total variance is exactly ``1 - ||mean||^2``, so the
mean carries it (see :mod:`cluster_stats.clt`).

The merge is the mean half of Chan's parallel combination algorithm, which
recovers the exact moments of ``A u B`` from the moments of ``A`` and ``B``
alone in O(d) time and space::

    N  = N_A + N_B
    d  = mean_B - mean_A
    mu = mean_A + d * (N_B / N)

All computation runs in float64 regardless of input dtype -- the merge is
applied repeatedly to the same row across compression cycles, so accumulating
rounding error in float32 would drift.

Per-dimension variances used to be carried here as well, for a spread model
that summed ``q_i^2 * sigma_i^2``. That model measured about half the true
spread on real ReID clusters (:mod:`cluster_stats.clt` explains why) and was
removed, along with the variance column. Restoring it means restoring the
population variance (divisor ``N``, which is what makes the combination exact;
sample variance does not compose this way) and the second half of Chan's
formula, kept here as::

    var    = (N_A * var_A + N_B * var_B) / N + (N_A * N_B / N^2) * d^2
    var_k  = sum_k (N_k / N) * (var_k + (mean_k - mu)^2)   # k-way form
"""

from __future__ import annotations

from typing import Sequence, Tuple

import numpy as np

# A cluster's whole summary: (count, mean).
#   count -- how many vectors the cluster holds (``weight`` on a compressed
#            row, ``N`` in the formulas above).
#   mean  -- [d] unnormalized arithmetic mean of those vectors, one entry per
#            embedding dimension. Not a unit vector: its shortfall below unit
#            length is the cluster's spread, and callers that need a direction
#            normalize it themselves.
MomentPair = Tuple[int, np.ndarray]

__all__ = [
    "MomentPair",
    "moments_from_vectors",
    "combine_two_clusters",
    "combine_cluster_moments",
]


def moments_from_vectors(vectors: np.ndarray) -> MomentPair:
    """Return ``(N, mean)`` for a batch of vectors.

    Args:
        vectors: ``[N, d]`` array. An empty batch is allowed as long as the
            second axis still carries the dimension (shape ``[0, d]``), in
            which case zero moments are returned.

    Raises:
        ValueError: if ``vectors`` is not 2-dimensional.
    """
    arr = np.asarray(vectors, dtype=np.float64)
    if arr.ndim != 2:
        raise ValueError(f"moments_from_vectors expects a 2-D [N, d] array, got shape {arr.shape}")
    if arr.shape[0] == 0:
        return 0, np.zeros(arr.shape[1], dtype=np.float64)
    return arr.shape[0], np.mean(arr, axis=0)


def combine_two_clusters(
    N_A: int,
    mean_A: np.ndarray,
    N_B: int,
    mean_B: np.ndarray,
) -> MomentPair:
    """Merge two moment summaries into one via Chan's parallel algorithm.

    Equivalent to recomputing the moments over the concatenation of both
    groups' raw vectors, but costs O(d) instead of O((N_A + N_B) * d).
    An empty group is absorbed by returning a copy of the other.
    """
    mean_A = np.asarray(mean_A, dtype=np.float64)
    mean_B = np.asarray(mean_B, dtype=np.float64)

    if N_A == 0:
        return int(N_B), mean_B.copy()
    if N_B == 0:
        return int(N_A), mean_A.copy()

    N_AB = int(N_A) + int(N_B)
    mean_AB = mean_A + (mean_B - mean_A) * (N_B / N_AB)
    return N_AB, mean_AB


def combine_cluster_moments(
    counts: Sequence[int],
    means: np.ndarray,
) -> MomentPair:
    """Merge an arbitrary number of moment summaries into one.

    The k-way generalization of :func:`combine_two_clusters`: the merged mean
    is the count-weighted mean of the group means. Grouping about the final
    mean rather than folding pairwise keeps the arithmetic in one pass.

    Args:
        counts: per-group vector counts, length ``k``.
        means: ``[k, d]`` per-group mean vectors.

    Raises:
        ValueError: on an empty group list or mismatched shapes.
    """
    counts_arr = np.asarray(counts, dtype=np.float64).reshape(-1)
    means_arr = np.asarray(means, dtype=np.float64)

    k = counts_arr.shape[0]
    if k == 0:
        raise ValueError("combine_cluster_moments needs at least one group")
    if means_arr.shape[0] != k:
        raise ValueError(f"shape mismatch: counts={counts_arr.shape}, means={means_arr.shape}")

    if k == 1:
        return int(counts_arr[0]), means_arr[0].copy()
    if k == 2:
        return combine_two_clusters(
            int(counts_arr[0]),
            means_arr[0],
            int(counts_arr[1]),
            means_arr[1],
        )

    total = counts_arr.sum()
    if total == 0:
        return 0, np.zeros(means_arr.shape[1], dtype=np.float64)

    weights = (counts_arr / total).reshape(-1, 1)
    return int(total), np.sum(weights * means_arr, axis=0)
