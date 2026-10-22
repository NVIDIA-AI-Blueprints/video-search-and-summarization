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

"""Coexistence conflict detection: find sub-tracklet pairs that cannot represent the same person.

Two sub-tracklets appearing **simultaneously on the same camera** with
non-overlapping bounding boxes cannot be the same identity. For each
pair we scan: for every frame of sub-tracklet A that is within
``frame_gap`` of a frame of sub-tracklet B on the same camera, we check
bbox IoU. If at least ``min_pairs`` such comparisons fall below
``max_iou``, the pair is flagged as a cannot-merge.

Ported 1:1 from the notebook cell ``online_setup``.
"""

from __future__ import annotations

import os
from collections import defaultdict
from typing import Dict, FrozenSet, Iterable, Mapping, Set, Tuple

from cluster_manager.geometry import compute_iou
from cluster_manager.types import Archive, SubTracklet, TrackingData


def detect_coexistence_with_archive(
    sub_tracklets: Mapping[str, SubTracklet],
    candidate_names: Iterable[str],
    archive: Archive,
    tracking_data: TrackingData,
    *,
    frame_gap: int,
    min_pairs: int,
    max_iou: float,
) -> Set[Tuple[str, int]]:
    """Return (sub_name, cluster_id) pairs that cannot merge.

    For each new sub-tracklet, checks whether it coexists on the same camera
    at the same time (within frame_gap) with any historical member of an
    existing archive cluster. If they have enough low-IoU frame pairs they
    cannot be the same identity, so the sub-tracklet must not be absorbed into
    that cluster.
    """
    # Build per-sub-tracklet cam->frame->bbox map
    sub_cam_frame: Dict[str, Dict[str, Dict[int, Tuple[float, float, float, float]]]] = defaultdict(
        lambda: defaultdict(dict)
    )
    for name in candidate_names:
        sub = sub_tracklets[name]
        for i, frame_id in enumerate(list(sub.frame_ids)):
            key = (sub.object_id, sub.sensor_ids[i], int(frame_id))
            bbox = tracking_data.get(key)
            if bbox is None:
                continue
            sub_cam_frame[name][sub.sensor_ids[i]][int(frame_id)] = (
                bbox["leftX"],
                bbox["topY"],
                bbox["rightX"],
                bbox["bottomY"],
            )

    blocked: Set[Tuple[str, int]] = set()

    # A single DeepStream tracker object_id can carry two simultaneous
    # non-overlapping boxes (an NvDCF artifact during occlusion). The low-IoU
    # test below would then bar that object_id from merging into a cluster that
    # already holds the SAME object_id, splitting one tracker id against itself
    # into a frozen orphan cluster. When enabled, skip the cannot-merge check
    # whenever the candidate's object_id is already present in the cluster's
    # history: a tracker id can never be two distinct people, so this can only
    # reunite a self-split fragment, never collapse two real identities.
    exempt_same_obj = os.getenv("CLUSTER_COEXIST_EXEMPT_SAME_OBJ", "0") == "1"

    for cid, cluster_info in archive.clusters.items():
        # Build cam->frame->bbox for all historical members of this cluster
        arch_cam_frame: Dict[str, Dict[int, Tuple[float, float, float, float]]] = defaultdict(dict)
        for h in cluster_info.members_history:
            obj_id = int(h.obj_id)
            for fid, sid in zip(list(h.frame_ids), list(h.sensor_ids)):
                key = (obj_id, str(sid), int(fid))
                bbox = tracking_data.get(key)
                if bbox is None:
                    continue
                arch_cam_frame[str(sid)][int(fid)] = (
                    bbox["leftX"],
                    bbox["topY"],
                    bbox["rightX"],
                    bbox["bottomY"],
                )

        if not arch_cam_frame:
            continue

        cluster_obj_ids = {int(h.obj_id) for h in cluster_info.members_history} if exempt_same_obj else None

        for name in candidate_names:
            if name not in sub_cam_frame:
                continue
            if cluster_obj_ids is not None and int(sub_tracklets[name].object_id) in cluster_obj_ids:
                continue
            shared_cams = set(sub_cam_frame[name]) & set(arch_cam_frame)
            if not shared_cams:
                continue

            low_iou_hits = 0
            for cam in shared_cams:
                frames_sub = sorted(sub_cam_frame[name][cam])
                frames_arch = sorted(arch_cam_frame[cam])
                boxes_sub = sub_cam_frame[name][cam]
                boxes_arch = arch_cam_frame[cam]
                jb = 0
                for fa in frames_sub:
                    while jb < len(frames_arch) and frames_arch[jb] < fa - frame_gap:
                        jb += 1
                    for fb in frames_arch[jb:]:
                        if fb > fa + frame_gap:
                            break
                        if compute_iou(boxes_sub[fa], boxes_arch[fb]) < max_iou:
                            low_iou_hits += 1

            if low_iou_hits >= min_pairs:
                blocked.add((name, int(cid)))

    return blocked


def count_object_copresence_hits(
    sub_tracklets: Mapping[str, SubTracklet],
    candidate_names: Iterable[str],
    archive: Archive,
    tracking_data: TrackingData,
    *,
    frame_gap: int,
    max_iou: float,
) -> Dict[FrozenSet[int], int]:
    """Count same-camera low-IoU co-present frame pairs per DISTINCT object_id
    pair for this batch (sub-vs-sub among candidates + sub-vs-archive members).

    Unlike :func:`detect_coexistence_conflicts`/`_with_archive` (which threshold
    at ``min_pairs`` per call), this returns the raw hit COUNT for every
    object_id pair with >=1 co-present low-IoU frame, so the caller can
    ACCUMULATE evidence across batches and block once the cumulative count
    clears the threshold. Keyed by object_id (a stable label), so the running
    total survives cluster merges/retirements with no remapping.
    Same-object_id self-pairs are skipped (one tracker id is one person; its
    own split fragments must never block each other).
    """
    candidate_names = list(candidate_names)
    # per-sub cam -> {frame: bbox}
    sub_cf: Dict[str, Dict[str, Dict[int, Tuple[float, float, float, float]]]] = defaultdict(lambda: defaultdict(dict))
    for name in candidate_names:
        sub = sub_tracklets[name]
        for i, fid in enumerate(list(sub.frame_ids)):
            bbox = tracking_data.get((sub.object_id, sub.sensor_ids[i], int(fid)))
            if bbox is None:
                continue
            sub_cf[name][sub.sensor_ids[i]][int(fid)] = (bbox["leftX"], bbox["topY"], bbox["rightX"], bbox["bottomY"])

    hits: Dict[FrozenSet[int], int] = defaultdict(int)

    def _count(cf_a, cf_b, oid_a: int, oid_b: int) -> None:
        if oid_a == oid_b:
            return
        key = frozenset({int(oid_a), int(oid_b)})
        for cam in set(cf_a) & set(cf_b):
            fa_sorted = sorted(cf_a[cam])
            fb_sorted = sorted(cf_b[cam])
            jb = 0
            for fa in fa_sorted:
                while jb < len(fb_sorted) and fb_sorted[jb] < fa - frame_gap:
                    jb += 1
                for fb in fb_sorted[jb:]:
                    if fb > fa + frame_gap:
                        break
                    if compute_iou(cf_a[cam][fa], cf_b[cam][fb]) < max_iou:
                        hits[key] += 1

    # sub-vs-sub
    names = sorted(sub_cf)
    for i, a in enumerate(names):
        for b in names[i + 1 :]:
            _count(sub_cf[a], sub_cf[b], sub_tracklets[a].object_id, sub_tracklets[b].object_id)

    # sub-vs-archive members
    for cid, info in archive.clusters.items():
        arch_obj_cf: Dict[int, Dict[str, Dict[int, Tuple[float, float, float, float]]]] = defaultdict(
            lambda: defaultdict(dict)
        )
        for h in info.members_history:
            oid = int(h.obj_id)
            for fid, sid in zip(list(h.frame_ids), list(h.sensor_ids)):
                bbox = tracking_data.get((oid, str(sid), int(fid)))
                if bbox is None:
                    continue
                arch_obj_cf[oid][str(sid)][int(fid)] = (bbox["leftX"], bbox["topY"], bbox["rightX"], bbox["bottomY"])
        if not arch_obj_cf:
            continue
        for name in names:
            sub_oid = sub_tracklets[name].object_id
            for arch_oid, cf_arch in arch_obj_cf.items():
                _count(sub_cf[name], cf_arch, sub_oid, arch_oid)

    return dict(hits)


def detect_coexistence_conflicts(
    sub_tracklets: Mapping[str, SubTracklet],
    candidate_names: Iterable[str],
    tracking_data: TrackingData,
    *,
    frame_gap: int,
    min_pairs: int,
    max_iou: float,
) -> Set[FrozenSet[str]]:
    """Return pairs of sub-tracklet names that must not be merged.

    Args:
        sub_tracklets: All sub-tracklets from the tracklet-splitting step.
        candidate_names: Names of sub-tracklets to consider (typically the
            qualifying representatives).
        tracking_data: Tracking lookup keyed by ``(object_id, sensor_id, frame_id)``.
        frame_gap: Maximum frame delta for two frames to count as "simultaneous".
        min_pairs: Minimum number of low-IoU frame comparisons to declare a conflict.
        max_iou: Boxes with IoU below this are considered "not overlapping".

    Returns:
        Set of ``frozenset({name_a, name_b})`` pairs.
    """
    # Build per-name, per-camera, per-frame bbox map
    per_name_cam_frame: dict[str, dict[str, dict[int, tuple[float, float, float, float]]]] = defaultdict(
        lambda: defaultdict(dict)
    )
    for name in candidate_names:
        sub = sub_tracklets[name]
        for i, frame_id in enumerate(sub.frame_ids.tolist()):
            key = (sub.object_id, sub.sensor_ids[i], int(frame_id))
            bbox = tracking_data.get(key)
            if bbox is None:
                continue
            per_name_cam_frame[name][sub.sensor_ids[i]][int(frame_id)] = (
                bbox["leftX"],
                bbox["topY"],
                bbox["rightX"],
                bbox["bottomY"],
            )

    conflicts: Set[FrozenSet[str]] = set()
    names = sorted(per_name_cam_frame)
    for i, name_a in enumerate(names):
        for name_b in names[i + 1 :]:
            shared_cams = set(per_name_cam_frame[name_a]) & set(per_name_cam_frame[name_b])
            if not shared_cams:
                continue
            low_iou_hits = 0
            for cam in shared_cams:
                frames_a = sorted(per_name_cam_frame[name_a][cam])
                frames_b = sorted(per_name_cam_frame[name_b][cam])
                boxes_a = per_name_cam_frame[name_a][cam]
                boxes_b = per_name_cam_frame[name_b][cam]
                jb = 0
                for fa in frames_a:
                    while jb < len(frames_b) and frames_b[jb] < fa - frame_gap:
                        jb += 1
                    for fb in frames_b[jb:]:
                        if fb > fa + frame_gap:
                            break
                        if compute_iou(boxes_a[fa], boxes_b[fb]) < max_iou:
                            low_iou_hits += 1
            if low_iou_hits >= min_pairs:
                conflicts.add(frozenset({name_a, name_b}))
    return conflicts
