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

"""Affinity matrix: re-ranked pairwise affinity across clusters and sub-tracklets.

Implements the k-reciprocal re-ranking of Zhong et al. (CVPR 2017) blended
with raw cosine distance, followed by constraint enforcement that zeros
out cannot-merge pairs.

Ported 1:1 from the notebook cell ``online_setup``.
"""

from __future__ import annotations

import logging
from typing import FrozenSet, Iterable, Sequence, Tuple

import numpy as np

logger = logging.getLogger(__name__)

_JACCARD_BLOCK_SIZE = 256
# Column tiling lets peak memory scale as O(row_block * col_block * n)
# instead of O(row_block * n * n). Keeping col block == row block by
# default; can be lowered further if n is very large.
_JACCARD_COL_BLOCK_SIZE = 256


def build_reciprocal_affinity_matrix(
    features: Sequence[np.ndarray],
    cannot_merge_idx_pairs: Iterable[FrozenSet[int]],
    *,
    lambda_blend: float,
    cos_neighbor_threshold: float,
    k1_max: int = 20,
    k2: int = 1,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Compute the re-ranked affinity matrix from node feature vectors.

    ``k1_max`` caps the k-reciprocal neighborhood size (k1 = max(2, min(k1_max,
    n//5))); ``k2`` is the query-expansion depth. Both were hardcoded (20, 1);
    exposed so the Jaccard re-ranking can be swept.

    Args:
        features: Iterable of ``(D,)`` unit vectors, one per graph node.
        cannot_merge_idx_pairs: Pairs of node indices whose affinity must be
            forced to zero.
        lambda_blend: Weight on the raw cosine distance in the final blend;
            ``(1 - lambda)`` weights Jaccard distance.
        cos_neighbor_threshold: Minimum cosine similarity for an edge to
            count in k-reciprocal neighborhoods.

    Returns:
        Tuple ``(affinity, affinity_pre_constraint, cos_sim)``:
        * ``affinity``: ``(N, N)`` matrix with diagonal 1 and cannot-merge
          pairs forced to 0.
        * ``affinity_pre_constraint``: Same matrix *before* cannot-merge
          suppression (useful for visualization).
        * ``cos_sim``: Raw pairwise cosine similarities, clipped to
          ``[-1, 1]``.
    """
    # Each "feature" is a (k_i, D) prototype matrix (k_i >= 1); a (D,) vector is
    # accepted and treated as a single prototype. Node-node cosine similarity is
    # the MAX over all prototype pairs, so a node matches another via its nearest
    # appearance mode. With k_i == 1 for every node this reduces exactly to the
    # original single-vector ``feature_matrix @ feature_matrix.T``.
    node_protos = [np.atleast_2d(np.asarray(f, dtype=np.float32)) for f in features]
    n_nodes = len(node_protos)
    proto_counts = [p.shape[0] for p in node_protos]
    proto_stack = np.concatenate(node_protos, axis=0)  # (M, D), M = sum k_i
    offs = np.concatenate([[0], np.cumsum(proto_counts)]).astype(int)  # (N+1,)
    gram = np.clip(proto_stack @ proto_stack.T, -1.0, 1.0)  # (M, M) prototype pairs
    cos_sim = np.empty((n_nodes, n_nodes), dtype=np.float32)
    for i in range(n_nodes):
        row = gram[offs[i] : offs[i + 1]]  # (k_i, M)
        for j in range(n_nodes):
            cos_sim[i, j] = row[:, offs[j] : offs[j + 1]].max()
    np.fill_diagonal(cos_sim, 1.0)
    original_dist = 1.0 - cos_sim
    rank_idx = np.argsort(original_dist, axis=1)

    k1 = max(2, min(k1_max, n_nodes // 5))
    # k2 (query-expansion depth) is now a parameter

    def _knn(q: int, k: int) -> set[int]:
        return {j for j in rank_idx[q, 1 : k + 1].tolist() if cos_sim[q, j] >= cos_neighbor_threshold}

    # Step 2: k-reciprocal neighborhood expansion
    expanded_neighbors = np.zeros((n_nodes, n_nodes), dtype=np.float32)
    for i in range(n_nodes):
        forward = _knn(i, k1)
        reciprocal = np.array([j for j in forward if i in _knn(j, k1)])
        k_half = max(1, k1 // 2)
        expanded = set(reciprocal.tolist())
        for j in reciprocal:
            forward_h = _knn(int(j), k_half)
            reciprocal_h = np.array([jj for jj in forward_h if int(j) in _knn(int(jj), k_half)])
            if len(reciprocal_h) and len(set(reciprocal.tolist()) & set(reciprocal_h.tolist())) >= (2 / 3) * len(
                reciprocal
            ):
                expanded.update(reciprocal_h.tolist())
        expanded_idx = np.array(sorted(expanded))
        if len(expanded_idx) == 0:
            continue
        w = np.exp(-original_dist[i, expanded_idx])
        ws = w.sum()
        expanded_neighbors[i, expanded_idx] = w / ws if ws > 1e-12 else w

    # Step 3: k2-NN query expansion
    if k2 > 0:
        qe = np.zeros_like(expanded_neighbors)
        for i in range(n_nodes):
            qe[i] = expanded_neighbors[rank_idx[i, : k2 + 1]].mean(axis=0)
        expanded_neighbors = qe

    # Step 4: Jaccard distance. Double-block both rows and columns so the
    # broadcasted temporaries stay O(row_block * col_block * n_nodes)
    # instead of O(row_block * n_nodes * n_nodes) -- previously the
    # row-only blocking produced (row_block, n_nodes, n_nodes)
    # intermediates that blew up to tens of GB on realistic batches.
    jaccard = np.zeros((n_nodes, n_nodes), dtype=np.float32)
    for row_start in range(0, n_nodes, _JACCARD_BLOCK_SIZE):
        row_stop = min(row_start + _JACCARD_BLOCK_SIZE, n_nodes)
        row_block = expanded_neighbors[row_start:row_stop, None, :]
        for col_start in range(0, n_nodes, _JACCARD_COL_BLOCK_SIZE):
            col_stop = min(col_start + _JACCARD_COL_BLOCK_SIZE, n_nodes)
            col_block = expanded_neighbors[None, col_start:col_stop, :]
            mn = np.minimum(row_block, col_block).sum(axis=2)
            mx = np.maximum(row_block, col_block).sum(axis=2)
            jaccard[row_start:row_stop, col_start:col_stop] = 1.0 - mn / (mx + 1e-12)
    jaccard = np.clip(jaccard, 0.0, 1.0)

    # Suppress pairs below cosine threshold (force max Jaccard distance)
    below = cos_sim < cos_neighbor_threshold
    np.fill_diagonal(below, False)
    jaccard[below] = 1.0

    # Step 5: blend and produce affinity
    final_dist = (1.0 - lambda_blend) * jaccard + lambda_blend * original_dist
    np.fill_diagonal(final_dist, 0.0)
    affinity = 1.0 - final_dist
    np.fill_diagonal(affinity, 1.0)
    affinity_pre_constraint = affinity.copy()

    # Step 6: apply cannot-merge constraints. Each pair must hold exactly
    # two distinct ints; malformed pairs (singletons, None, wrong types)
    # are skipped with a warning rather than raising, so a bad upstream
    # call doesn't crash the whole clustering tick.
    for pair in cannot_merge_idx_pairs:
        try:
            nodes = tuple(pair)
        except TypeError:
            logger.warning("affinity: skipping non-iterable cannot-merge pair %r", pair)
            continue
        if len(nodes) != 2:
            logger.warning(
                "affinity: skipping cannot-merge pair with %d elements (expected 2): %r",
                len(nodes),
                pair,
            )
            continue
        i, j = int(nodes[0]), int(nodes[1])
        affinity[i, j] = 0.0
        affinity[j, i] = 0.0

    return affinity, affinity_pre_constraint, cos_sim
