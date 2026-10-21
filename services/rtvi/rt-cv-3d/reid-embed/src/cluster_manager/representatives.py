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

"""Representative extraction: one quality-weighted representative per sub-tracklet.

Each sub-tracklet's representative is the L2-normalized weighted mean of
its member embeddings, where weights are the inverse distance to the
centroid. Sub-tracklets with too few members are dropped (they contribute
neither to re-ranking nor to clustering).

Ported 1:1 from the notebook cell ``online_setup``.
"""

from __future__ import annotations

from typing import Dict, Mapping

import numpy as np

from cluster_manager.types import SubTracklet


def compute_quality_weighted_centroid(vecs: np.ndarray) -> np.ndarray | None:
    """Return the inverse-distance-to-centroid weighted mean of ``vecs``, L2-normalized.

    Args:
        vecs: ``(M, D)`` array of embeddings.

    Returns:
        A ``(D,)`` unit vector, or ``None`` if the input is empty.
    """
    if len(vecs) == 0:
        return None
    centroid = vecs.mean(axis=0)
    dists = np.linalg.norm(vecs - centroid, axis=1)
    weights = 1.0 / (dists + 1e-6)
    weight_sum = weights.sum()
    if weight_sum > 1e-12:
        weights = weights / weight_sum
    else:
        weights = np.full(len(vecs), 1.0 / len(vecs))
    rep = (weights[:, None] * vecs).sum(axis=0)
    norm = np.linalg.norm(rep)
    return rep / norm if norm > 1e-12 else rep


def update_prototype_set(
    prototypes: np.ndarray | None,
    counts: list[int] | None,
    new_rep: np.ndarray,
    n_new: int,
    *,
    k_max: int,
    spawn_threshold: float,
) -> tuple[np.ndarray, list[int]]:
    """Online-update a cluster's prototype set with one incoming unit rep.

    Assigns ``new_rep`` to its nearest existing prototype; count-blends it in
    when the cosine is ``>= spawn_threshold`` OR the set is already at
    ``k_max``; otherwise adds it as a new prototype (a distinct appearance
    mode). Count-weighting (not EMA) avoids the rep-poisoning that regressed
    the EMA experiment. Deterministic: nearest-prototype ties break to the
    lowest index.

    Args:
        prototypes: ``(k, D)`` current unit prototypes, or ``None``/empty.
        counts: per-prototype embedding counts (parallel to ``prototypes``).
        new_rep: ``(D,)`` incoming unit rep.
        n_new: embedding count behind ``new_rep``.
        k_max: maximum prototypes to retain.
        spawn_threshold: cosine below which a far rep spawns a new prototype
            (when room remains).

    Returns:
        ``(prototypes, counts)`` with prototypes L2-normalized, float32.
    """
    nr = np.asarray(new_rep, dtype=np.float32)
    if prototypes is None or len(prototypes) == 0:
        return nr.reshape(1, -1).copy(), [int(n_new)]
    protos = np.asarray(prototypes, dtype=np.float32)
    cts = [int(c) for c in counts] if counts else [1] * len(protos)
    sims = protos @ nr  # (k,) cosine, both unit
    best = int(np.argmax(sims))
    if sims[best] >= spawn_threshold or len(protos) >= k_max:
        c = cts[best]
        blended = c * protos[best] + int(n_new) * nr
        norm = np.linalg.norm(blended)
        protos = protos.copy()
        protos[best] = blended / norm if norm > 1e-12 else blended
        cts[best] = c + int(n_new)
        return protos.astype(np.float32), cts
    protos = np.vstack([protos, nr.reshape(1, -1)]).astype(np.float32)
    cts.append(int(n_new))
    return protos, cts


def compute_subtracklet_prototypes(
    sub_tracklets: Mapping[str, SubTracklet],
    min_embs_threshold: int,
    *,
    k_max: int,
    spawn_threshold: float,
) -> Dict[str, np.ndarray]:
    """Multi-prototype variant of :func:`compute_subtracklet_representatives`.

    A long sub-tracklet can drift across appearance modes even after the 0.95
    split (each consecutive step >0.95 yet endpoints far apart), so a single
    centroid blurs it. For each qualifying sub-tracklet this selects up to
    ``k_max`` prototypes by streaming its embeddings (entry_id order ->
    deterministic) through the same online assign/blend/spawn rule used for
    clusters, then DENOISES by dropping prototypes whose embedding-count is below
    a 10%-of-length floor (a single noisy frame must not mint a spurious mode).
    Falls back to the single quality-weighted centroid when no prototype clears
    the floor.

    Returns:
        Mapping from sub-tracklet name to its ``(k, D)`` unit prototype matrix.
    """
    out: Dict[str, np.ndarray] = {}
    for name, sub in sub_tracklets.items():
        vecs = sub.vecs
        if len(vecs) <= min_embs_threshold:
            continue
        order = np.argsort(np.asarray(sub.entry_ids, dtype=np.int64), kind="mergesort")
        protos = None
        counts = None
        for i in order:
            v = vecs[int(i)]
            nrm = np.linalg.norm(v)
            if nrm < 1e-12:
                continue
            protos, counts = update_prototype_set(
                protos,
                counts,
                (v / nrm).astype(np.float32),
                1,
                k_max=k_max,
                spawn_threshold=spawn_threshold,
            )
        if protos is None:
            rep = compute_quality_weighted_centroid(vecs)
            if rep is not None:
                out[name] = rep.reshape(1, -1)
            continue
        floor = max(2, int(0.10 * len(vecs)))
        keep = [j for j, c in enumerate(counts) if c >= floor]
        if keep:
            out[name] = protos[keep]
        else:
            rep = compute_quality_weighted_centroid(vecs)
            out[name] = rep.reshape(1, -1) if rep is not None else protos[:1]
    return out


def compute_subtracklet_representatives(
    sub_tracklets: Mapping[str, SubTracklet],
    min_embs_threshold: int,
) -> Dict[str, np.ndarray]:
    """Compute quality-weighted representatives for qualifying sub-tracklets.

    Sub-tracklets with ``<= min_embs_threshold`` embeddings are skipped
    (consistent with the notebook: "skip sub-tracklets with <= N").

    Args:
        sub_tracklets: Output of :func:`split_batch_into_subtracklets`.
        min_embs_threshold: Cutoff (exclusive) for minimum embedding count.

    Returns:
        Mapping from sub-tracklet name to its unit representative vector.
    """
    reps: Dict[str, np.ndarray] = {}
    for name, sub in sub_tracklets.items():
        if len(sub.vecs) <= min_embs_threshold:
            continue
        rep = compute_quality_weighted_centroid(sub.vecs)
        if rep is not None:
            reps[name] = rep
    return reps
