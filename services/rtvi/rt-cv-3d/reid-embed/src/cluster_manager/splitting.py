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

"""Tracklet splitting: partition a batch's embeddings into per-object sub-tracklets.

For each ``object_id`` present in the batch, the algorithm runs a greedy
cosine-similarity sweep. It seeds a new sub-tracklet from the earliest-frame
unassigned embedding and iteratively absorbs remaining embeddings whose
maximum cosine similarity to any member already in the sub-tracklet is at
or above ``cos_threshold``. When no more can be absorbed, the next
unassigned embedding seeds a new sub-tracklet and the sweep repeats.

Ported 1:1 from the notebook cell ``online_setup``.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Iterable, List

import numpy as np

from cluster_manager.types import EmbeddingRecord, SubTracklet

# ---------------------------------------------------------------------------
# Internal helpers (pure, tested indirectly via split_batch_into_subtracklets)
# ---------------------------------------------------------------------------


def _max_cos_sim(emb: np.ndarray, emb_norm: float, sub_embs: List[np.ndarray]) -> float:
    """Return the maximum cosine similarity between ``emb`` and any vector in ``sub_embs``."""
    arr = np.asarray(sub_embs)
    norms = np.linalg.norm(arr, axis=1)
    sims = arr @ emb / (norms * emb_norm + 1e-12)
    return float(sims.max())


def _sweep_assign(
    unassigned: List[int],
    vecs: np.ndarray,
    norms: np.ndarray,
    sub_embs: List[np.ndarray],
    threshold: float,
) -> tuple[List[int], List[int]]:
    """Iteratively absorb unassigned indices into ``sub_embs`` until a full sweep adds nothing.

    ``sub_embs`` is mutated in-place: absorbed vectors are appended.

    Returns:
        ``(assigned, remaining)`` index lists (disjoint subsets of ``unassigned``).
    """
    remaining = list(unassigned)
    assigned: List[int] = []
    while True:
        newly: List[int] = []
        still: List[int] = []
        for i in remaining:
            if _max_cos_sim(vecs[i], norms[i], sub_embs) >= threshold:
                newly.append(i)
                sub_embs.append(vecs[i])
            else:
                still.append(i)
        assigned.extend(newly)
        remaining = still
        if not newly:
            break
    return assigned, remaining


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def split_batch_into_subtracklets(
    embeddings: Iterable[EmbeddingRecord],
    batch_idx: int,
    cos_threshold: float,
) -> dict[str, SubTracklet]:
    """Split all embeddings of a single batch into per-object sub-tracklets.

    Args:
        embeddings: All :class:`EmbeddingRecord` instances in this batch.
        batch_idx: Monotonically increasing batch index (used in sub-tracklet names).
        cos_threshold: Minimum max-cosine similarity to absorb an embedding
            into an existing sub-tracklet.

    Returns:
        Mapping from globally unique sub-tracklet name
        ``b{batch_idx}_{object_id}_{sub_id}`` to :class:`SubTracklet`.
    """
    by_obj: dict[int, List[EmbeddingRecord]] = defaultdict(list)
    for rec in embeddings:
        by_obj[rec.object_id].append(rec)

    sub_tracklets: dict[str, SubTracklet] = {}
    for object_id in sorted(by_obj):
        records = sorted(by_obj[object_id], key=lambda r: r.frame_id)
        if not records:
            continue
        vecs = np.stack([r.vector for r in records])
        norms = np.linalg.norm(vecs, axis=1)
        frame_ids = np.asarray([r.frame_id for r in records], dtype=np.int64)
        sensor_ids = [r.sensor_id for r in records]
        entry_ids = [r.entry_id for r in records]

        n_records = len(records)
        sub_ids = np.full(n_records, -1, dtype=int)
        next_sid = 0
        unassigned = list(range(n_records))

        while unassigned:
            seed = unassigned.pop(0)
            sid = next_sid
            next_sid += 1
            sub_ids[seed] = sid
            embs = [vecs[seed]]
            assigned, unassigned = _sweep_assign(unassigned, vecs, norms, embs, cos_threshold)
            for i in assigned:
                sub_ids[i] = sid

        for sid in sorted({int(s) for s in sub_ids if s >= 0}):
            members = np.where(sub_ids == sid)[0]
            name = f"b{batch_idx}_{object_id}_{sid}"
            sub_tracklets[name] = SubTracklet(
                name=name,
                batch=batch_idx,
                object_id=object_id,
                sub_id=sid,
                vecs=vecs[members].copy(),
                frame_ids=frame_ids[members].copy(),
                sensor_ids=[sensor_ids[i] for i in members],
                entry_ids=[entry_ids[i] for i in members],
            )
    return sub_tracklets
