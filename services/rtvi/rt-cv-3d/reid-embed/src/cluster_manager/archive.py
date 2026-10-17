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

"""Archive update: merge a batch's clustering result into the persistent archive.

This is the only step that mutates state across batches. It:

1. Assigns a surviving cluster id to every clustered group (keeping the
   smallest id when multiple old clusters merge; allocating a new id
   when a group has no prior cluster).
2. Blends representative vectors (count-weighted or EMA).
3. Appends to each cluster's ``members_history``.
4. Retires absorbed old cluster ids.
5. Remaps cannot-merge cluster pairs through any retirements and
   promotes new within-batch sub-sub conflicts up to the cluster level.

Ported 1:1 from the notebook cell ``online_setup``.
"""

from __future__ import annotations

from typing import Dict, FrozenSet, Iterable, List, Mapping, Sequence, Set, Tuple

import numpy as np

from cluster_manager.representatives import (
    compute_quality_weighted_centroid,
    update_prototype_set,
)
from cluster_manager.types import (
    Archive,
    BatchStats,
    ClusterGroup,
    ClusterInfo,
    ClusterNode,
    MemberHistoryEntry,
    SubTracklet,
)


def _blend_representatives(
    old_reps: List[np.ndarray],
    old_counts: List[int],
    batch_rep: np.ndarray | None,
    n_batch: int,
    *,
    strategy: str,
    ema_alpha: float,
) -> Tuple[np.ndarray, int]:
    """Combine old cluster reps and the current batch rep into one normalized rep.

    Returns:
        ``(rep, total_n)`` where ``rep`` is L2-normalized.
    """
    if old_reps and batch_rep is not None:
        if strategy == "ema":
            if len(old_reps) == 1:
                combined_old = old_reps[0]
            else:
                combined_old = sum(n * r for n, r in zip(old_counts, old_reps))
            norm = np.linalg.norm(combined_old)
            if norm > 1e-12:
                combined_old = combined_old / norm
            rep = (1.0 - ema_alpha) * combined_old + ema_alpha * batch_rep
            total_n = sum(old_counts) + n_batch
        else:  # count-weighted
            parts = [n * r for n, r in zip(old_counts, old_reps)] + [n_batch * batch_rep]
            rep = sum(parts)
            total_n = sum(old_counts) + n_batch
    elif old_reps:
        parts = [n * r for n, r in zip(old_counts, old_reps)]
        rep = sum(parts)
        total_n = sum(old_counts)
    else:
        rep = batch_rep
        total_n = n_batch

    norm = np.linalg.norm(rep)
    if norm > 1e-12:
        rep = rep / norm
    return rep.astype(np.float32), int(total_n)


def _assign_surviving_ids(
    archive: Archive,
    cluster_members_idx: Sequence[Set[int]],
    node_meta: Sequence[ClusterNode],
) -> Tuple[List[ClusterGroup], Set[int], Dict[int, int]]:
    """Resolve cluster groups from constrained clustering into ``ClusterGroup`` records.

    Returns:
        ``(group_info, retired_ids, node_to_cid)``.
    """
    group_info: List[ClusterGroup] = []
    retired_ids: Set[int] = set()
    node_to_cid: Dict[int, int] = {}

    for members in cluster_members_idx:
        old_ids = [int(node_meta[i].id) for i in members if node_meta[i].kind == "cluster"]
        sub_names = [str(node_meta[i].id) for i in members if node_meta[i].kind == "sub"]

        if old_ids:
            surviving = min(old_ids)
            retired_here = [oid for oid in old_ids if oid != surviving]
            retired_ids.update(retired_here)
            status = "updated" if (sub_names or retired_here) else "unchanged"
        else:
            surviving = archive.allocate_cluster_id()
            status = "new"

        for i in members:
            node_to_cid[i] = surviving

        group_info.append(
            ClusterGroup(
                surviving=surviving,
                old_ids=old_ids,
                sub_names=sub_names,
                status=status,
                members=sorted(members),
            )
        )

    return group_info, retired_ids, node_to_cid


def _build_cluster_prototypes(
    archive: Archive,
    group: ClusterGroup,
    new_sub_tracklets: Mapping[str, SubTracklet],
    rep: np.ndarray,
    total_n: int,
    *,
    n_prototypes: int,
    proto_spawn_threshold: float,
) -> Tuple[np.ndarray, List[int]]:
    """Build ``(prototypes, proto_counts)`` for a group's surviving cluster.

    ``n_prototypes <= 1`` returns the single mean ``rep`` (exact legacy
    behavior). Otherwise it seeds from the merged old clusters' prototype sets
    and absorbs each absorbed sub-tracklet's quality-weighted rep online
    (assign / count-blend / spawn). ``old_ids`` and ``sub_names`` are processed
    in sorted order so the result is deterministic.
    """
    if n_prototypes <= 1:
        return rep.reshape(1, -1).astype(np.float32), [int(total_n)]
    protos: np.ndarray | None = None
    counts: List[int] | None = None
    for oid in sorted(int(o) for o in group.old_ids):
        old = archive.clusters[oid]
        op = old.protos()
        oc = old.proto_counts if old.proto_counts else [int(old.n_embs)] * len(op)
        for p, c in zip(op, oc):
            protos, counts = update_prototype_set(
                protos,
                counts,
                p,
                int(c),
                k_max=n_prototypes,
                spawn_threshold=proto_spawn_threshold,
            )
    for name in sorted(group.sub_names):
        sub = new_sub_tracklets[name]
        sub_rep = compute_quality_weighted_centroid(sub.vecs)
        if sub_rep is not None:
            protos, counts = update_prototype_set(
                protos,
                counts,
                sub_rep,
                int(len(sub.vecs)),
                k_max=n_prototypes,
                spawn_threshold=proto_spawn_threshold,
            )
    if protos is None:
        return rep.reshape(1, -1).astype(np.float32), [int(total_n)]
    return protos, counts


def _update_single_cluster(
    archive: Archive,
    group: ClusterGroup,
    new_sub_tracklets: Mapping[str, SubTracklet],
    *,
    batch_idx: int,
    blend_strategy: str,
    ema_alpha: float,
    n_prototypes: int = 1,
    proto_spawn_threshold: float = 0.85,
) -> ClusterInfo:
    """Compute the post-batch ``ClusterInfo`` for a single group."""
    if group.status == "unchanged":
        # Preserve the existing cluster untouched (deep copy of the mutable list
        # so later mutations of the old cluster's history don't leak here).
        old = archive.clusters[group.surviving]
        return ClusterInfo(
            cluster_id=old.cluster_id,
            rep=old.rep,
            n_embs=old.n_embs,
            created_batch=old.created_batch,
            last_updated_batch=old.last_updated_batch,
            members_history=list(old.members_history),
            prototypes=old.prototypes,
            proto_counts=list(old.proto_counts),
        )

    batch_vecs_parts: List[np.ndarray] = []
    members_history: List[MemberHistoryEntry] = []
    for oid in group.old_ids:
        members_history.extend(archive.clusters[oid].members_history)

    for name in group.sub_names:
        sub = new_sub_tracklets[name]
        batch_vecs_parts.append(sub.vecs)
        members_history.append(
            MemberHistoryEntry(
                sub_name=name,
                source_batch=int(batch_idx),
                obj_id=int(sub.object_id),
                frame_ids=[int(f) for f in sub.frame_ids.tolist()],
                sensor_ids=list(sub.sensor_ids),
                entry_ids=list(sub.entry_ids),
            )
        )

    if batch_vecs_parts:
        batch_vecs = np.concatenate(batch_vecs_parts, axis=0)
    else:
        batch_vecs = np.empty((0, 0), dtype=np.float32)
    n_batch = len(batch_vecs)
    batch_rep = compute_quality_weighted_centroid(batch_vecs) if n_batch > 0 else None

    old_reps = [archive.clusters[oid].rep for oid in group.old_ids]
    old_counts = [archive.clusters[oid].n_embs for oid in group.old_ids]
    if group.old_ids:
        created_batch = min(archive.clusters[oid].created_batch for oid in group.old_ids)
    else:
        created_batch = batch_idx

    rep, total_n = _blend_representatives(
        old_reps,
        old_counts,
        batch_rep,
        n_batch,
        strategy=blend_strategy,
        ema_alpha=ema_alpha,
    )

    prototypes, proto_counts = _build_cluster_prototypes(
        archive,
        group,
        new_sub_tracklets,
        rep,
        total_n,
        n_prototypes=n_prototypes,
        proto_spawn_threshold=proto_spawn_threshold,
    )

    return ClusterInfo(
        cluster_id=group.surviving,
        rep=rep,
        n_embs=total_n,
        created_batch=int(created_batch),
        last_updated_batch=int(batch_idx),
        members_history=members_history,
        prototypes=prototypes,
        proto_counts=proto_counts,
    )


def _remap_cannot_merge(
    archive: Archive,
    group_info: List[ClusterGroup],
    new_cannot_merge_sub_pairs: Iterable[FrozenSet[str]],
    node_meta: Sequence[ClusterNode],
    node_to_cid: Dict[int, int],
) -> Set[FrozenSet[int]]:
    """Remap existing pairs through retirements and promote new sub-sub conflicts."""
    old_to_new: Dict[int, int] = {}
    for g in group_info:
        for oid in g.old_ids:
            old_to_new[oid] = g.surviving

    remapped: Set[FrozenSet[int]] = set()
    for pair in archive.cannot_merge_clusters:
        a, b = list(pair)
        a_new = old_to_new.get(a, a)
        b_new = old_to_new.get(b, b)
        if a_new != b_new and a_new in archive.clusters and b_new in archive.clusters:
            remapped.add(frozenset({a_new, b_new}))

    sub_name_to_idx = {str(m.id): i for i, m in enumerate(node_meta) if m.kind == "sub"}
    for pair in new_cannot_merge_sub_pairs:
        a_name, b_name = list(pair)
        if a_name not in sub_name_to_idx or b_name not in sub_name_to_idx:
            continue
        a_cid = node_to_cid.get(sub_name_to_idx[a_name])
        b_cid = node_to_cid.get(sub_name_to_idx[b_name])
        if a_cid is not None and b_cid is not None and a_cid != b_cid:
            remapped.add(frozenset({a_cid, b_cid}))

    return remapped


def _update_rep_and_min_frame(
    archive: Archive,
    group: ClusterGroup,
    new_sub_tracklets: Mapping[str, SubTracklet],
) -> None:
    """Populate ``archive.rep_object_id[surviving]`` and
    ``archive.min_frame_id[surviving]`` for one cluster, applying the
    lock-at-formation rule:

    * ``new``: pick the earliest member across the cluster's
      sub-tracklets (rep = that member's original tracker_id, min_frame
      = its frame_id). If that tracker_id is already assigned as a rep
      to another cluster, fall back to a unique id (``cid``, then scan
      upward).
    * ``updated`` with one old_id: pure extension. Keep the old
      cluster's rep/min unchanged -- new members do not shift the rep.
    * ``updated`` with multiple old_ids: merge. Pick the lower-min-frame
      across the old clusters' existing reps. New sub-tracklets joining
      the merge do not influence the rep (lock-at-formation also applies
      to merges).
    * ``unchanged``: no change.

    Retired cluster entries are pruned from both dicts by the caller.
    """
    if group.status == "unchanged":
        return

    cid = int(group.surviving)

    if group.status == "new":
        # Pick the sub-tracklet member with the smallest
        # (frame_id, sensor_id, entry_id) tuple.
        best: Tuple[int, str, int, int] | None = None  # (frame, sensor, entry, rep)
        for sub_name in group.sub_names:
            sub = new_sub_tracklets.get(sub_name)
            if sub is None or len(sub.frame_ids) == 0:
                continue
            for i in range(len(sub.frame_ids)):
                key = (
                    int(sub.frame_ids[i]),
                    str(sub.sensor_ids[i]),
                    int(sub.entry_ids[i]),
                    int(sub.object_id),
                )
                if best is None or key < best:
                    best = key
        if best is not None:
            archive.min_frame_id[cid] = best[0]
            preferred_rep = int(best[3])
            used_reps = set(archive.rep_object_id.values())
            if preferred_rep not in used_reps:
                archive.rep_object_id[cid] = preferred_rep
            else:
                rep = cid
                while rep in used_reps:
                    rep += 1
                archive.rep_object_id[cid] = rep
        return

    # status == "updated": old_ids is non-empty by construction.
    if len(group.old_ids) == 1:
        # Pure extension. Lock at formation: keep old values intact
        # even if new sub-tracklets have an earlier frame.
        oid = int(group.old_ids[0])
        if oid != cid:
            # surviving id changed (shouldn't happen for single-old extension
            # since min(old_ids) == oid, but be defensive).
            archive.rep_object_id[cid] = archive.rep_object_id.get(oid, archive.rep_object_id.get(cid, 0))
            archive.min_frame_id[cid] = archive.min_frame_id.get(oid, archive.min_frame_id.get(cid, 0))
        return

    # Merge: multiple old_ids. Pick the lower-min-frame.
    candidates: List[Tuple[int, int]] = []  # (min_frame, rep_object_id)
    for oid in group.old_ids:
        oid_int = int(oid)
        if oid_int in archive.min_frame_id and oid_int in archive.rep_object_id:
            candidates.append((archive.min_frame_id[oid_int], archive.rep_object_id[oid_int]))
    if candidates:
        min_frame, rep = min(candidates)
        archive.min_frame_id[cid] = min_frame
        archive.rep_object_id[cid] = rep


def merge_batch_into_archive(
    archive: Archive,
    batch_idx: int,
    cluster_members_idx: Sequence[Set[int]],
    node_meta: Sequence[ClusterNode],
    new_sub_tracklets: Mapping[str, SubTracklet],
    new_cannot_merge_sub_pairs: Iterable[FrozenSet[str]],
    *,
    blend_strategy: str,
    ema_alpha: float,
    n_prototypes: int = 1,
    proto_spawn_threshold: float = 0.85,
) -> Tuple[BatchStats, List[ClusterGroup], Dict[int, int]]:
    """Update the ``archive`` in place with a batch's clustering result.

    Args:
        archive: The persistent :class:`Archive` to mutate.
        batch_idx: Current batch index.
        cluster_members_idx: Output of :func:`cluster_by_constrained_affinity`.
        node_meta: Node metadata aligned with the affinity matrix rows/cols.
        new_sub_tracklets: Sub-tracklets from this batch.
        new_cannot_merge_sub_pairs: Coexistence conflicts (pairs of sub-tracklet names).
        blend_strategy: ``"count"`` or ``"ema"``.
        ema_alpha: EMA weight (only used when ``blend_strategy == "ema"``).

    Returns:
        Tuple ``(stats, group_info, node_to_cid)``.
    """
    group_info, retired_ids, node_to_cid = _assign_surviving_ids(archive, cluster_members_idx, node_meta)

    updated_clusters: Dict[int, ClusterInfo] = {}
    for group in group_info:
        updated_clusters[group.surviving] = _update_single_cluster(
            archive,
            group,
            new_sub_tracklets,
            batch_idx=batch_idx,
            blend_strategy=blend_strategy,
            ema_alpha=ema_alpha,
            n_prototypes=n_prototypes,
            proto_spawn_threshold=proto_spawn_threshold,
        )
        # Populate rep_object_id and min_frame_id for the surviving id
        # (lock-at-formation; merges pick the lower-min-frame).
        _update_rep_and_min_frame(archive, group, new_sub_tracklets)

    # Commit: preserve untouched old clusters, overwrite updated ones, drop retired ids
    final_clusters: Dict[int, ClusterInfo] = {}
    for cid, info in archive.clusters.items():
        if cid in retired_ids:
            continue
        final_clusters[cid] = updated_clusters.get(cid, info)
    for cid, info in updated_clusters.items():
        final_clusters.setdefault(cid, info)
    archive.clusters = final_clusters

    # Note: retired cluster ids are NOT pruned from rep_object_id /
    # min_frame_id. The service-side relabel cascade needs to look up
    # each retired cluster's rep AFTER process_batch returns to drive
    # the compressed-DB merge cascade; pruning here would lose that
    # information. cluster_ids are monotonic via allocate_cluster_id, so
    # stale entries never collide with future allocations.

    archive.cannot_merge_clusters = _remap_cannot_merge(
        archive, group_info, new_cannot_merge_sub_pairs, node_meta, node_to_cid
    )

    stats = BatchStats(
        batch=int(batch_idx),
        n_groups=len(group_info),
        n_new_clusters=sum(1 for g in group_info if g.status == "new"),
        n_updated=sum(1 for g in group_info if g.status == "updated"),
        n_unchanged=sum(1 for g in group_info if g.status == "unchanged"),
        n_retired=len(retired_ids),
        n_clusters_total=len(archive.clusters),
        n_cm_pairs=len(archive.cannot_merge_clusters),
    )
    return stats, group_info, node_to_cid
