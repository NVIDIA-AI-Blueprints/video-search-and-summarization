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

"""Stateful orchestration of the online clustering pipeline."""

from __future__ import annotations

import logging
from collections import defaultdict
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Set

import numpy as np

from cluster_manager.affinity import build_reciprocal_affinity_matrix
from cluster_manager.archive import merge_batch_into_archive
from cluster_manager.clustering import cluster_by_constrained_affinity
from cluster_manager.coexistence import (
    count_object_copresence_hits,
    detect_coexistence_conflicts,
    detect_coexistence_with_archive,
)
from cluster_manager.representatives import (
    compute_subtracklet_prototypes,
    compute_subtracklet_representatives,
)
from cluster_manager.serialization import load_archive, save_archive
from cluster_manager.splitting import split_batch_into_subtracklets
from cluster_manager.types import (
    Archive,
    BatchResult,
    BatchStats,
    ClusterGroup,
    ClusterNode,
    ClusterParams,
    EmbeddingRecord,
    SubTracklet,
    TrackingData,
)

logger = logging.getLogger(__name__)

DEFAULT_STATE_PATH = Path("/app/cluster_manager_archive.json")


class ClusterManager:
    """Online tracklet-based cluster manager.

    Runs the full pipeline per batch — tracklet splitting, representative
    extraction, coexistence conflict detection, affinity matrix
    construction, constrained clustering, and archive update — mutating
    a persistent :class:`Archive`. Intended to be held as a module-level
    singleton in long-running services.

    Usage::

        manager = ClusterManager(ClusterParams())
        for batch in batches:
            result = manager.process_batch(batch.embeddings, batch.tracking)
            ...  # persist result.entry_to_cluster
    """

    def __init__(
        self,
        params: ClusterParams,
        *,
        archive: Optional[Archive] = None,
    ) -> None:
        self._params = params
        self._archive = archive if archive is not None else Archive()

        # Last-batch intermediates, exposed for testing and debugging.
        self._last_subtracklets: Dict[str, object] = {}
        self._last_representatives: Dict[str, np.ndarray] = {}
        self._last_conflict_pairs: set = set()
        self._last_node_meta: List[ClusterNode] = []
        self._last_affinity: Optional[np.ndarray] = None
        self._last_affinity_pre: Optional[np.ndarray] = None
        self._last_cluster_members: List[set] = []

    # -----------------------------------------------------------------
    # Public API
    # -----------------------------------------------------------------

    @property
    def params(self) -> ClusterParams:
        return self._params

    @property
    def archive(self) -> Archive:
        return self._archive

    @property
    def last_subtracklets(self) -> Dict[str, object]:
        """Sub-tracklets produced by the most recent ``process_batch``."""
        return self._last_subtracklets

    def reset(self) -> None:
        """Clear all persistent clustering state."""
        self._archive.reset()
        logger.info("ClusterManager archive reset")

    @property
    def clustered_entry_ids(self) -> set[int]:
        """All ``entry_id`` values that have been absorbed into any cluster.

        Callers (e.g. maintenance loops) can use this to skip re-clustering
        work without mutating the source collection.
        """
        ids: set[int] = set()
        for info in self._archive.clusters.values():
            for history in info.members_history:
                ids.update(history.entry_ids)
        return ids

    def process_batch(
        self,
        embeddings: Iterable[EmbeddingRecord],
        tracking_data: TrackingData,
        *,
        batch_idx: Optional[int] = None,
    ) -> BatchResult:
        """Run one batch through the full pipeline and update the archive.

        Args:
            embeddings: Embeddings gathered for this batch (typically a
                frame-range slice from the main collection).
            tracking_data: Tracking lookup covering the same frame range.
            batch_idx: Batch index; defaults to ``len(archive.batch_log)``
                so callers that process in order get monotonically
                increasing ids without bookkeeping.

        Returns:
            :class:`BatchResult` with per-batch stats, group info, and
            an ``entry_to_cluster`` map.
        """
        embeddings = list(embeddings)
        resolved_batch = batch_idx if batch_idx is not None else len(self._archive.batch_log)
        params = self._params

        # Record every input entry_id (including those filtered out by
        # representative extraction) so callers can skip revisiting them
        # on subsequent ticks.
        self._archive.seen_entry_ids.update(int(e.entry_id) for e in embeddings)

        sub_tracklets = split_batch_into_subtracklets(
            embeddings, batch_idx=resolved_batch, cos_threshold=params.cos_threshold
        )
        reps = compute_subtracklet_representatives(sub_tracklets, min_embs_threshold=params.min_embs_threshold)
        # Sub-tracklet multi-prototype (only when n_prototypes>1): give each
        # qualifying sub-tracklet up to k prototypes (its appearance modes) so the
        # MAX-over-prototypes affinity matches on the nearest mode of BOTH sides,
        # not just a single blurred centroid.
        sub_protos = None
        # sub_n_prototypes decouples the sub-tracklet k from the cluster k
        # (0 = inherit n_prototypes). Lets us test sub-tracklet multi-proto with
        # cluster multi-proto OFF (n_prototypes=1, sub_n_prototypes=3).
        sub_k = getattr(params, "sub_n_prototypes", 0) or getattr(params, "n_prototypes", 1)
        if sub_k > 1:
            sub_protos = compute_subtracklet_prototypes(
                sub_tracklets,
                params.min_embs_threshold,
                k_max=sub_k,
                spawn_threshold=getattr(params, "sub_proto_spawn_threshold", 0.95),
            )
        new_cm_sub_pairs = detect_coexistence_conflicts(
            sub_tracklets,
            reps.keys(),
            tracking_data,
            frame_gap=params.coexist_frame_gap,
            min_pairs=params.coexist_min_pairs,
            max_iou=params.coexist_max_iou,
        )
        archive_blocked = detect_coexistence_with_archive(
            sub_tracklets,
            reps.keys(),
            self._archive,
            tracking_data,
            frame_gap=params.coexist_frame_gap,
            min_pairs=params.coexist_min_pairs,
            max_iou=params.coexist_max_iou,
        )

        # Persistent/cumulative coexistence: accumulate this batch's same-camera
        # low-IoU co-presence hits (by object_id pair) into the archive so the
        # evidence sums across batches and blocks provably-different people whose
        # per-window sampled co-presence is too sparse for the per-batch detector.
        if getattr(params, "coexist_persist", False):
            batch_hits = count_object_copresence_hits(
                sub_tracklets,
                reps.keys(),
                self._archive,
                tracking_data,
                frame_gap=params.coexist_frame_gap,
                max_iou=params.coexist_max_iou,
            )
            ch = self._archive.coexist_obj_hits
            for k, v in batch_hits.items():
                ch[k] = ch.get(k, 0) + v

        self._last_subtracklets = sub_tracklets
        self._last_representatives = reps
        self._last_conflict_pairs = new_cm_sub_pairs

        if not reps and not self._archive.clusters:
            # Nothing to cluster in this batch and no prior state.
            stats = self._record_empty_batch(resolved_batch)
            return BatchResult(stats=stats, group_info=[], entry_to_cluster={})

        node_meta = self._build_node_meta(reps)
        features = self._build_feature_matrix(node_meta, reps, sub_protos)

        if not features:
            stats = self._record_empty_batch(resolved_batch)
            return BatchResult(stats=stats, group_info=[], entry_to_cluster={})

        cm_idx_pairs = self._resolve_cannot_merge_indices(node_meta, new_cm_sub_pairs, archive_blocked)
        if getattr(params, "coexist_persist", False):
            cm_idx_pairs |= self._persistent_coexist_node_pairs(
                node_meta,
                sub_tracklets,
                getattr(params, "coexist_persist_min_hits", 20),
            )

        affinity, affinity_pre, cos_sim = build_reciprocal_affinity_matrix(
            features,
            cm_idx_pairs,
            lambda_blend=params.lambda_blend,
            cos_neighbor_threshold=params.cos_neighbor_threshold,
            k1_max=getattr(params, "rerank_k1_max", 20),
            k2=getattr(params, "rerank_k2", 1),
        )

        cluster_members = cluster_by_constrained_affinity(
            affinity,
            cm_idx_pairs,
            merge_threshold=params.merge_threshold,
            node_sensor_sets=self._build_node_sensor_sets(node_meta, sub_tracklets),
            cross_merge_threshold=getattr(params, "cross_merge_threshold", None),
            merge_min_affinity=getattr(params, "merge_min_affinity", None),
        )

        self._last_node_meta = node_meta
        self._last_affinity = affinity
        self._last_affinity_pre = affinity_pre
        self._last_cluster_members = cluster_members
        # snapshot rep->object_id BEFORE the merge (merge_batch_into_archive can
        # reindex cluster ids) so MERGELOG maps cluster nodes to the right rep.
        _rep_snapshot = dict(self._archive.rep_object_id)

        stats, group_info, _node_to_cid = merge_batch_into_archive(
            self._archive,
            batch_idx=resolved_batch,
            cluster_members_idx=cluster_members,
            node_meta=node_meta,
            new_sub_tracklets=sub_tracklets,
            new_cannot_merge_sub_pairs=new_cm_sub_pairs,
            blend_strategy=params.blend_strategy,
            ema_alpha=params.ema_alpha,
            n_prototypes=getattr(params, "n_prototypes", 1),
            proto_spawn_threshold=getattr(params, "proto_spawn_threshold", 0.85),
        )
        self._archive.batch_log.append(stats)

        # --- clustering-accuracy instrumentation (parseable: PARAMLOG/SPLITLOG/MERGELOG) ---
        # GT-label the logged object_ids offline (via tracking<->GT) to judge split &
        # merge correctness on the LIVE run, and see how affinity (cosine vs Jaccard
        # blend) drives each merge.
        if resolved_batch == 0:
            logger.warning(
                f"PARAMLOG cos={params.cos_threshold} min_embs={params.min_embs_threshold} "
                f"merge={params.merge_threshold} lambda={params.lambda_blend} "
                f"cos_nb={params.cos_neighbor_threshold} k1max={getattr(params, 'rerank_k1_max', 20)} "
                f"k2={getattr(params, 'rerank_k2', 1)} mma={getattr(params, 'merge_min_affinity', None)} "
                f"nproto={getattr(params, 'n_prototypes', 1)} coexist(gap={params.coexist_frame_gap},"
                f"pairs={params.coexist_min_pairs},iou={params.coexist_max_iou})"
            )
        n_objs = len({st.object_id for st in sub_tracklets.values()})
        logger.warning(f"SPLITLOG batch={resolved_batch} objs={n_objs} subs={len(sub_tracklets)} reps={len(reps)}")
        if getattr(params, "coexist_persist", False):
            ch = self._archive.coexist_obj_hits
            thr = getattr(params, "coexist_persist_min_hits", 20)
            n_blocked = sum(1 for h in ch.values() if h >= thr)
            logger.warning(
                f"COEXISTLOG batch={resolved_batch} obj_pairs_tracked={len(ch)} "
                f"obj_pairs_blocked(>={thr})={n_blocked}"
            )
        node_oid = {}
        for i, nm in enumerate(node_meta):
            if nm.kind == "cluster":
                node_oid[i] = _rep_snapshot.get(int(nm.id), -1)
            else:
                s = sub_tracklets.get(str(nm.id))
                node_oid[i] = int(s.object_id) if s is not None else -1
        for members in cluster_members:
            if len(members) < 2:
                continue
            idxs = sorted(int(m) for m in members)
            oids = [node_oid.get(i, -1) for i in idxs]
            kinds = [node_meta[i].kind[0] for i in idxs]  # 'c'=cluster 's'=sub
            pair_aff = [float(affinity[a, b]) for k, a in enumerate(idxs) for b in idxs[k + 1 :]]
            pair_cos = [float(cos_sim[a, b]) for k, a in enumerate(idxs) for b in idxs[k + 1 :]]
            if pair_aff:
                logger.warning(
                    f"MERGELOG batch={resolved_batch} size={len(idxs)} oids={oids} kinds={kinds} "
                    f"min_aff={min(pair_aff):.3f} avg_aff={sum(pair_aff)/len(pair_aff):.3f} "
                    f"min_cos={min(pair_cos):.3f} avg_cos={sum(pair_cos)/len(pair_cos):.3f}"
                )

        entry_to_cluster = self._build_entry_to_cluster_map(sub_tracklets, group_info)

        logger.info(
            "Batch %d: groups=%d new=%d updated=%d unchanged=%d retired=%d total=%d cm=%d",
            stats.batch,
            stats.n_groups,
            stats.n_new_clusters,
            stats.n_updated,
            stats.n_unchanged,
            stats.n_retired,
            stats.n_clusters_total,
            stats.n_cm_pairs,
        )

        return BatchResult(
            stats=stats,
            group_info=group_info,
            entry_to_cluster=entry_to_cluster,
        )

    # -----------------------------------------------------------------
    # Persistence
    # -----------------------------------------------------------------

    def save_state(self, path: str | Path = DEFAULT_STATE_PATH) -> None:
        """Write the archive to ``path`` as JSON."""
        save_archive(self._archive, path)
        logger.info("Archive saved to %s (%d clusters)", path, len(self._archive.clusters))

    def load_state(self, path: str | Path = DEFAULT_STATE_PATH) -> None:
        """Load the archive from a JSON file written by :meth:`save_state`."""
        self._archive = load_archive(path)
        logger.info("Archive loaded from %s (%d clusters)", path, len(self._archive.clusters))

    # -----------------------------------------------------------------
    # Internal helpers
    # -----------------------------------------------------------------

    def _record_empty_batch(self, batch_idx: int) -> BatchStats:
        stats = BatchStats(
            batch=int(batch_idx),
            n_groups=0,
            n_new_clusters=0,
            n_updated=0,
            n_unchanged=0,
            n_retired=0,
            n_clusters_total=len(self._archive.clusters),
            n_cm_pairs=len(self._archive.cannot_merge_clusters),
        )
        self._archive.batch_log.append(stats)
        return stats

    def _build_node_meta(self, reps: Mapping[str, np.ndarray]) -> List[ClusterNode]:
        nodes: List[ClusterNode] = [ClusterNode(kind="cluster", id=cid) for cid in sorted(self._archive.clusters)]
        nodes.extend(ClusterNode(kind="sub", id=name) for name in sorted(reps))
        return nodes

    def _build_feature_matrix(
        self,
        node_meta: Sequence[ClusterNode],
        reps: Mapping[str, np.ndarray],
        sub_protos: Mapping[str, np.ndarray] | None = None,
    ) -> List[np.ndarray]:
        features: List[np.ndarray] = []
        for node in node_meta:
            if node.kind == "cluster":
                # (k, D) prototype matrix; falls back to [rep] when n_prototypes==1.
                features.append(self._archive.clusters[int(node.id)].protos())
            else:
                name = str(node.id)
                if sub_protos is not None and name in sub_protos:
                    # Sub-tracklet's own (k, D) appearance modes.
                    features.append(sub_protos[name])
                else:
                    features.append(reps[name].reshape(1, -1))
        return features

    def _build_node_sensor_sets(
        self,
        node_meta: Sequence[ClusterNode],
        sub_tracklets: Mapping[str, SubTracklet],
    ) -> List[Set[str]]:
        """Per-node set of sensor ids, aligned with ``node_meta``.

        Cluster nodes take the union of sensors across their full
        ``members_history``; sub nodes take their own ``sensor_ids``. Used by
        the cross-camera merge incentive to detect disjoint-sensor pairs.
        """
        sets: List[Set[str]] = []
        for node in node_meta:
            if node.kind == "cluster":
                s: Set[str] = set()
                for h in self._archive.clusters[int(node.id)].members_history:
                    s.update(str(x) for x in h.sensor_ids)
                sets.append(s)
            else:
                sub = sub_tracklets.get(str(node.id))
                sets.append({str(x) for x in sub.sensor_ids} if sub is not None else set())
        return sets

    def _resolve_cannot_merge_indices(
        self,
        node_meta: Sequence[ClusterNode],
        new_cm_sub_pairs: Iterable[frozenset],
        archive_blocked: Iterable = (),
    ) -> set:
        idx_of_cluster = {int(m.id): i for i, m in enumerate(node_meta) if m.kind == "cluster"}
        idx_of_sub = {str(m.id): i for i, m in enumerate(node_meta) if m.kind == "sub"}
        pairs: set = set()
        for pair in self._archive.cannot_merge_clusters:
            a, b = list(pair)
            if a in idx_of_cluster and b in idx_of_cluster:
                pairs.add(frozenset({idx_of_cluster[a], idx_of_cluster[b]}))
        for pair in new_cm_sub_pairs:
            a, b = list(pair)
            if a in idx_of_sub and b in idx_of_sub:
                pairs.add(frozenset({idx_of_sub[a], idx_of_sub[b]}))
        for sub_name, cid in archive_blocked:
            if sub_name in idx_of_sub and cid in idx_of_cluster:
                pairs.add(frozenset({idx_of_sub[sub_name], idx_of_cluster[cid]}))
        return pairs

    def _persistent_coexist_node_pairs(
        self,
        node_meta: Sequence[ClusterNode],
        sub_tracklets: Mapping[str, SubTracklet],
        min_pairs: int,
    ) -> set:
        """Block merging any two nodes whose object_id sets contain an object_id
        pair with cumulative co-presence hits >= min_pairs (coexist_persist)."""
        ch = self._archive.coexist_obj_hits
        blocked_obj = {p for p, h in ch.items() if h >= min_pairs}
        if not blocked_obj:
            return set()
        blocked_flat: set = set()
        for p in blocked_obj:
            blocked_flat |= p
        # node index -> the blocked object_ids it carries
        obj_to_nodes: Dict[int, set] = defaultdict(set)
        for idx, m in enumerate(node_meta):
            if m.kind == "cluster":
                info = self._archive.clusters.get(int(m.id))
                oids = {int(h.obj_id) for h in info.members_history} if info else set()
            else:
                s = sub_tracklets.get(str(m.id))
                oids = {int(s.object_id)} if s is not None else set()
            for o in oids & blocked_flat:
                obj_to_nodes[o].add(idx)
        pairs: set = set()
        for p in blocked_obj:
            a, b = tuple(p)
            for i in obj_to_nodes.get(a, ()):
                for j in obj_to_nodes.get(b, ()):
                    if i != j:
                        pairs.add(frozenset({i, j}))
        return pairs

    @staticmethod
    def _build_entry_to_cluster_map(
        sub_tracklets: Mapping[str, SubTracklet],
        group_info: Sequence[ClusterGroup],
    ) -> Dict[int, int]:
        sub_to_cluster: Dict[str, int] = {}
        for group in group_info:
            for name in group.sub_names:
                sub_to_cluster[name] = group.surviving
        entry_to_cluster: Dict[int, int] = {}
        for name, sub in sub_tracklets.items():
            cid = sub_to_cluster.get(name)
            if cid is None:
                continue  # short sub-tracklet dropped during representative extraction
            for entry_id in sub.entry_ids:
                entry_to_cluster[int(entry_id)] = int(cid)
        return entry_to_cluster
