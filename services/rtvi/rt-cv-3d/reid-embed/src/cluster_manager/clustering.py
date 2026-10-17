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

"""Constrained agglomerative clustering over the affinity matrix.

Greedy average-linkage merges on the affinity matrix until no valid pair
meets ``merge_threshold``. A pair of clusters is *valid* only when no
index in one is connected to an index in the other via a cannot-link
constraint.

Ported 1:1 from the notebook cell ``online_setup``.
"""

from __future__ import annotations

from typing import FrozenSet, Iterable, List, Set

import numpy as np


def _can_merge(
    cluster_a: Set[int],
    cluster_b: Set[int],
    cannot_link: Set[FrozenSet[int]],
) -> bool:
    """Return ``True`` if no pair ``(i, j)`` with ``i in a, j in b`` is a cannot-link."""
    for i in cluster_a:
        for j in cluster_b:
            if frozenset({i, j}) in cannot_link:
                return False
    return True


def _average_linkage(affinity: np.ndarray, cluster_a: Set[int], cluster_b: Set[int]) -> float:
    """Mean pairwise affinity between ``cluster_a`` and ``cluster_b``."""
    return float(affinity[np.ix_(sorted(cluster_a), sorted(cluster_b))].mean())


def _min_linkage(affinity: np.ndarray, cluster_a: Set[int], cluster_b: Set[int]) -> float:
    """Minimum pairwise affinity between ``cluster_a`` and ``cluster_b`` (complete-linkage).

    Used as a variance/purity gate: if the WEAKEST cross-pair is too low, the
    merged group would span dissimilar appearances (likely different people),
    so the merge is rejected even when the average clears ``merge_threshold``.
    """
    return float(affinity[np.ix_(sorted(cluster_a), sorted(cluster_b))].min())


def _group_sensors(node_sensor_sets: List[Set[str]], group: Set[int]) -> Set[str]:
    """Union of the sensor sets of every node index in ``group``."""
    out: Set[str] = set()
    for i in group:
        out |= node_sensor_sets[i]
    return out


def _best_partner(
    affinity: np.ndarray,
    members: List[Set[int]],
    idx: int,
    cannot_link: Set[FrozenSet[int]],
) -> int | None:
    """Index of ``idx``'s highest average-linkage can-merge partner, or None."""
    best_aff = -1.0
    best_j: int | None = None
    for j in range(len(members)):
        if j == idx:
            continue
        if not _can_merge(members[idx], members[j], cannot_link):
            continue
        aff = _average_linkage(affinity, members[idx], members[j])
        if aff > best_aff:
            best_aff = aff
            best_j = j
    return best_j


def cluster_by_constrained_affinity(
    affinity: np.ndarray,
    cannot_link_idx_set: Iterable[FrozenSet[int]],
    *,
    merge_threshold: float,
    node_sensor_sets: List[Set[str]] | None = None,
    cross_merge_threshold: float | None = None,
    merge_min_affinity: float | None = None,
) -> List[Set[int]]:
    """Greedily merge node groups by affinity subject to cannot-link constraints.

    Args:
        affinity: ``(N, N)`` symmetric affinity matrix with diagonal 1.
        cannot_link_idx_set: Frozensets of index pairs that must stay in
            different groups.
        merge_threshold: Minimum average-linkage affinity to accept a merge.
        node_sensor_sets: Per-node-index set of sensor ids. Required to enable
            the cross-camera merge incentive.
        cross_merge_threshold: When set (and ``node_sensor_sets`` given), a pair
            of groups whose sensor sets are DISJOINT may merge at this lower
            bar, but only if it is mutually reciprocal-nearest (each is the
            other's best can-merge partner). Guards against merging two distinct
            people who simply occupy disjoint zones. ``None`` disables this and
            reproduces the original behavior exactly.

        merge_min_affinity: Variance/purity gate (complete-linkage floor). When
            set, a merge is rejected if the WEAKEST pairwise affinity between the
            two groups is below this value, even if the average clears
            ``merge_threshold``. Prevents a cluster from absorbing a dissimilar
            member (e.g. a different person dragged in by a strong-average but
            weak-min link), which is the over-merge/contamination failure mode.
            ``None`` disables it (default behavior unchanged).

    Returns:
        List of node-index sets forming the final clusters. Every index
        appears in exactly one set; singleton clusters are included.
    """
    n_nodes = affinity.shape[0]
    members: List[Set[int]] = [{i} for i in range(n_nodes)]
    cannot_link = set(cannot_link_idx_set)
    cross_on = cross_merge_threshold is not None and node_sensor_sets is not None

    while True:
        best_aff = -1.0
        best_pair: tuple[int, int] | None = None
        for a in range(len(members)):
            for b in range(a + 1, len(members)):
                aff = _average_linkage(affinity, members[a], members[b])
                eff_thr = merge_threshold
                is_cross = False
                if cross_on:
                    sa = _group_sensors(node_sensor_sets, members[a])
                    sb = _group_sensors(node_sensor_sets, members[b])
                    if sa and sb and sa.isdisjoint(sb):
                        eff_thr = cross_merge_threshold
                        is_cross = True
                if aff < eff_thr:
                    continue
                if not _can_merge(members[a], members[b], cannot_link):
                    continue
                # Variance/purity gate: reject if the weakest cross-pair is too
                # low (the merged group would span dissimilar appearances).
                if (
                    merge_min_affinity is not None
                    and _min_linkage(affinity, members[a], members[b]) < merge_min_affinity
                ):
                    continue
                # A cross-camera pair admitted only by the relaxed bar must be
                # ONE-DIRECTIONAL reciprocal-nearest: accept if EITHER group is
                # the other's best can-merge partner (relaxed from the stricter
                # mutual-AND, which barely fired). Still blocks a weak cross link
                # where neither side prefers the other.
                if is_cross and aff < merge_threshold:
                    if (
                        _best_partner(affinity, members, a, cannot_link) != b
                        and _best_partner(affinity, members, b, cannot_link) != a
                    ):
                        continue
                if aff > best_aff:
                    best_aff = aff
                    best_pair = (a, b)
        if best_pair is None:
            break
        a, b = best_pair
        members[a] |= members[b]
        members.pop(b)
    return members
