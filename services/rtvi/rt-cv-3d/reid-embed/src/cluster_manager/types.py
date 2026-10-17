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

"""Shared dataclasses and type aliases for the online cluster manager."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, FrozenSet, List, Tuple

import numpy as np

from config import CLUSTER_ID_START

# ---------------------------------------------------------------------------
# Type aliases
# ---------------------------------------------------------------------------

BBox = Tuple[float, float, float, float]
"""Axis-aligned bounding box (leftX, topY, rightX, bottomY)."""

TrackingKey = Tuple[int, str, int]
"""(object_id, sensor_id, frame_id)."""

TrackingData = Dict[TrackingKey, Dict[str, float]]
"""Mapping from tracking key to bbox dict with keys leftX/topY/rightX/bottomY."""

NodeKind = str  # 'cluster' | 'sub'


# ---------------------------------------------------------------------------
# Parameters
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ClusterParams:
    """Tunable parameters for the online clustering pipeline.

    Defaults match the notebook ``online_setup`` cell.
    """

    # Tracklet splitting
    cos_threshold: float = 0.95

    # Representative extraction
    min_embs_threshold: int = 100

    # Coexistence conflict detection
    coexist_frame_gap: int = 30
    coexist_min_pairs: int = 5
    coexist_max_iou: float = 0.1
    # Persistent/cumulative coexistence: accumulate same-camera low-IoU
    # co-presence hits per object_id pair ACROSS batches (not just per-window),
    # and permanently block merging any two nodes whose object_id sets contain a
    # pair with cumulative hits >= coexist_persist_min_hits. Catches
    # provably-different people whose per-window sampled co-presence is too
    # sparse to trip the per-batch detector. False = exact current behavior.
    coexist_persist: bool = False
    # Cumulative-hit threshold for the persistent blocker. Higher than the
    # per-batch coexist_min_pairs because hits sum across ~40+ batches: genuinely
    # co-present people accumulate thousands of hits, while a coincidental
    # phantom-box (tracker ID-swap) self-overlap accrues only a handful, so a
    # higher floor blocks true different-people without fragmenting one person.
    coexist_persist_min_hits: int = 20

    # Affinity matrix (k-reciprocal re-ranking)
    lambda_blend: float = 0.5
    cos_neighbor_threshold: float = 0.8
    # k-reciprocal Jaccard params (were hardcoded 20/1), exposed for sweeping.
    rerank_k1_max: int = 20
    rerank_k2: int = 1

    # Constrained agglomerative clustering
    merge_threshold: float = 0.8

    # Cross-camera merge incentive (item 10): lower the merge bar to this value
    # ONLY for cluster pairs whose sensor sets are disjoint AND that are mutual
    # reciprocal-nearest neighbors. None disables the feature (default path).
    cross_merge_threshold: float | None = None

    # Variance/purity gate at the merge step (complete-linkage floor). When set,
    # a merge is rejected if the weakest cross-pair affinity is below this value,
    # blocking the over-merge/contamination failure mode. None = off.
    merge_min_affinity: float | None = None

    # Archive update blending
    blend_strategy: str = "count"  # "count" | "ema"
    ema_alpha: float = 0.3

    # Multi-prototype cluster representatives. n_prototypes=1 reproduces the
    # single-centroid behavior exactly. >1 keeps up to k quality-weighted
    # prototypes per cluster (its appearance modes across cameras/poses) and
    # makes cluster<->cluster / cluster<->sub affinity the MAX over prototype
    # pairs, so a hard-pose sub-tracklet matches the cluster's nearest mode
    # instead of a single mean dominated by other cameras.
    n_prototypes: int = 1
    proto_spawn_threshold: float = 0.85
    # Sub-tracklet multi-prototype: a long sub-tracklet can drift across modes even
    # after the 0.95 split, so represent it with up to n_prototypes modes too (not
    # just one centroid). Higher spawn bar than clusters since sub-tracklets are
    # already appearance-coherent. Only active when n_prototypes > 1.
    sub_proto_spawn_threshold: float = 0.95
    # Decouple sub-tracklet prototype count from the cluster count. 0 = inherit
    # n_prototypes (default). Set independently to test sub-tracklet multi-proto
    # in isolation, e.g. n_prototypes=1 (cluster single-rep) + sub_n_prototypes=3.
    sub_n_prototypes: int = 0


# ---------------------------------------------------------------------------
# Records
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class EmbeddingRecord:
    """A single embedding produced by the encoder.

    Matches the shape persisted in the Milvus main collection.
    """

    entry_id: int
    object_id: int
    sensor_id: str
    frame_id: int
    vector: np.ndarray  # shape: (D,), float32

    @classmethod
    def from_dict(cls, d: Dict) -> "EmbeddingRecord":
        vec = d["vector"]
        if not isinstance(vec, np.ndarray):
            vec = np.asarray(vec, dtype=np.float32)
        return cls(
            entry_id=int(d["entry_id"]),
            object_id=int(d["object_id"]),
            sensor_id=str(d["sensor_id"]),
            frame_id=int(d["frame_id"]),
            vector=vec,
        )


@dataclass(slots=True)
class SubTracklet:
    """A sub-tracklet produced by the tracklet-splitting step within one batch.

    ``name`` is globally unique across batches: ``b{batch_idx}_{object_id}_{sub_id}``.
    """

    name: str
    batch: int
    object_id: int
    sub_id: int
    vecs: np.ndarray  # (M, D)
    frame_ids: np.ndarray  # (M,), int
    sensor_ids: List[str]  # length M
    entry_ids: List[int]  # length M


@dataclass(slots=True)
class ClusterNode:
    """One node in the affinity graph.

    Existing persistent clusters enter the graph as ``kind='cluster'``
    (with an integer id); new sub-tracklets qualifying from the current
    batch enter as ``kind='sub'`` (with their sub-tracklet name as id).
    """

    kind: NodeKind  # 'cluster' or 'sub'
    id: int | str  # int for existing clusters, str (sub name) for new sub-tracklets


@dataclass(slots=True)
class ClusterGroup:
    """A cluster output from constrained clustering, resolved back to
    its surviving cluster id during the archive update step."""

    surviving: int
    old_ids: List[int]
    sub_names: List[str]
    status: str  # 'new' | 'updated' | 'unchanged'
    members: List[int]  # node indices from the affinity graph


@dataclass(slots=True)
class MemberHistoryEntry:
    """Per-batch record of a sub-tracklet that was absorbed into a cluster."""

    sub_name: str
    source_batch: int
    obj_id: int
    frame_ids: List[int]
    sensor_ids: List[str]
    entry_ids: List[int]


@dataclass(slots=True)
class ClusterInfo:
    """Persistent record for a single cluster in the archive."""

    cluster_id: int
    rep: np.ndarray  # (D,) float32, L2-normalized (the mean; drives rep_object_id)
    n_embs: int
    created_batch: int
    last_updated_batch: int
    members_history: List[MemberHistoryEntry] = field(default_factory=list)
    # Multi-prototype representation (n_prototypes>1). (k, D) unit vectors and
    # their parallel embedding counts. None/empty => fall back to [rep], which
    # is the exact single-centroid behavior.
    prototypes: np.ndarray | None = None
    proto_counts: List[int] = field(default_factory=list)

    def protos(self) -> np.ndarray:
        """Prototype matrix ``(k, D)``; falls back to ``[rep]`` when unset
        (n_prototypes==1, or an archive saved before multi-prototype)."""
        if self.prototypes is not None and len(self.prototypes) > 0:
            return self.prototypes
        return self.rep.reshape(1, -1)


@dataclass(slots=True)
class BatchStats:
    """Per-batch summary counters."""

    batch: int
    n_groups: int
    n_new_clusters: int
    n_updated: int
    n_unchanged: int
    n_retired: int
    n_clusters_total: int
    n_cm_pairs: int


@dataclass(slots=True)
class BatchResult:
    """Return value of ``ClusterManager.process_batch``."""

    stats: BatchStats
    group_info: List[ClusterGroup]
    entry_to_cluster: Dict[int, int]


# ---------------------------------------------------------------------------
# Archive
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class Archive:
    """Persistent clustering state across batches.

    ``seen_entry_ids`` tracks every embedding ever passed to
    :meth:`ClusterManager.process_batch`, whether or not it survived the
    representative-extraction filter. Callers use it to skip entries
    that have already been considered, so no embedding is revisited in
    a later batch. This is a superset of the entries recorded in
    ``clusters[...].members_history``.

    ``rep_object_id`` and ``min_frame_id`` map each live cluster_id to
    its representative tracker_id (locked at cluster formation -- the
    member with the earliest ``(frame_id, sensor_id, entry_id)`` tuple)
    and to that member's frame_id respectively. On cluster merge the
    surviving cluster keeps the lower-min-frame rep across both
    clusters; the retired cluster's entries are deleted from these
    dicts. ``rep_object_id`` is what the service writes into Milvus's
    ``object_id`` field for clustered rows.
    """

    clusters: Dict[int, ClusterInfo] = field(default_factory=dict)
    cannot_merge_clusters: set[FrozenSet[int]] = field(default_factory=set)
    next_cluster_id: int = CLUSTER_ID_START
    seen_entry_ids: set[int] = field(default_factory=set)
    batch_log: List[BatchStats] = field(default_factory=list)
    rep_object_id: Dict[int, int] = field(default_factory=dict)
    min_frame_id: Dict[int, int] = field(default_factory=dict)
    # Cumulative same-camera low-IoU co-presence hits per object_id pair, summed
    # across batches (coexist_persist mode). object_ids are stable labels so no
    # remapping is needed across cluster merges/retirements.
    coexist_obj_hits: Dict[FrozenSet[int], int] = field(default_factory=dict)

    def reset(self) -> None:
        """Clear all state back to an empty archive."""
        self.clusters.clear()
        self.cannot_merge_clusters.clear()
        self.next_cluster_id = CLUSTER_ID_START
        self.seen_entry_ids.clear()
        self.batch_log.clear()
        self.rep_object_id.clear()
        self.min_frame_id.clear()
        self.coexist_obj_hits.clear()

    def allocate_cluster_id(self) -> int:
        """Return the next unused cluster id and advance the counter."""
        cid = self.next_cluster_id
        self.next_cluster_id += 1
        return cid

    def rep_for(self, cluster_id: int) -> int:
        """Return the representative tracker_id for ``cluster_id``.

        Raises ``KeyError`` if the cluster has no rep yet (e.g. it was
        formed before the rep machinery was in place, or was retired in
        a merge).
        """
        return self.rep_object_id[int(cluster_id)]

    def min_frame_for(self, cluster_id: int) -> int:
        """Return the cluster's earliest frame_id (locked at formation)."""
        return self.min_frame_id[int(cluster_id)]
