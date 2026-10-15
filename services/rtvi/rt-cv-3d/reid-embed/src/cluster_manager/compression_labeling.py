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

"""Pure helpers that decide the effective ``object_id`` for a compression row.

Extracted from ``main.py`` so they can be unit-tested without standing up
Milvus. See ``test/test_compression_labeling.py``.

Behavior summary:

* ``ENABLE_CLUSTERING=False`` -> every row compresses with its raw
  ``object_id``; nothing is skipped. This preserves the original
  "compression without clustering" mode.
* ``ENABLE_CLUSTERING=True``:
    - Row whose ``entry_id`` appears in the cluster archive -> compress
      under the cluster's representative tracker_id (digit-string)
      instead of raw ``object_id``.
    - Row seen-but-not-clustered (Stage 2A filtered) -> skip (do not
      write to compressed DB) and delete from the main collection.
    - Row not-yet-seen by clustering (race condition: clustering hasn't
      fired on this entry yet) -> leave in main for the next tick.

Cluster identity in Milvus / tracking-DB is the cluster's representative
tracker_id (the original ``object_id`` of the member with the earliest
``(frame_id, sensor_id, entry_id)`` tuple, locked at cluster formation).
The ``is_clustered`` BOOL column on the main collection plus the
parallel ``clustered_object_ids`` set on the tracking DB tell apart
cluster-rep keys from raw-tracker keys when their integer values would
otherwise collide.
"""

from __future__ import annotations

from typing import Dict, Iterable, List, Optional, Set, Tuple

from cluster_manager.types import Archive

DISPOSITION_PROCESS = "process"
DISPOSITION_SKIP_FILTERED = "skip_filtered"
DISPOSITION_DEFER_UNSEEN = "defer_unseen"


# DEBUG: shift cluster-rep object_ids by this amount when emitted to
# Milvus / tracking DB so search responses make it obvious which IDs are
# cluster reps (>= CLUSTER_REP_SHIFTED_THRESHOLD) vs raw tracker IDs (<).
# Set CLUSTER_REP_DEBUG_OFFSET to 0 to disable. Internal
# archive.rep_object_id is untouched.
#
# Raw tracker IDs in this deployment start at 10000 and are assumed to
# stay below 20000. Shifted cluster reps therefore live in [20000, 30000).
# If real tracker IDs ever cross CLUSTER_REP_SHIFTED_THRESHOLD they'll be
# mistaken for already-shifted reps and won't get the offset.
CLUSTER_REP_DEBUG_OFFSET = 0
CLUSTER_REP_SHIFTED_THRESHOLD = 20000


def cluster_id_to_object_id(cluster_id: int, archive: Archive) -> str:
    """Return the ``object_id`` (digit-string) for a cluster.

    Resolves to the cluster's representative tracker_id from
    ``archive.rep_object_id``. The rep is locked at cluster formation
    (earliest member by ``(frame_id, sensor_id, entry_id)``) and may
    change only on merge, when the surviving cluster takes whichever
    rep had the lower ``min_frame_id``.

    Returns a plain digit-string (no prefix) so DeepStream's
    ``uint64_t TargetId`` -- which doesn't accept a string namespace --
    can round-trip through Milvus.

    Raises ``KeyError`` if the cluster is unknown to the archive (e.g.
    process restart wiped state and the restart hook hasn't run, or a
    bookkeeping bug). Callers should not catch this -- it always
    indicates a real inconsistency.
    """
    raw = int(archive.rep_object_id[int(cluster_id)])
    # Idempotent: when a cluster is extended across batches, the rep stored
    # in the archive can already be a shifted value (it was read back from
    # Milvus where prior emits wrote the offset). Re-applying would push it
    # to 30000+, 40000+, etc. Treat anything >= CLUSTER_REP_SHIFTED_THRESHOLD
    # as already shifted.
    if CLUSTER_REP_DEBUG_OFFSET and raw < CLUSTER_REP_SHIFTED_THRESHOLD:
        raw += CLUSTER_REP_DEBUG_OFFSET
    return str(raw)


def entry_to_cluster_map_from_archive(archive) -> Dict[int, int]:
    """Flatten ``archive.clusters[cid].members_history`` into ``entry_id -> cluster_id``.

    Every entry that was clustered (i.e. was part of a qualifying
    sub-tracklet and survived into Stage 4) appears here. Filtered
    entries do not.
    """
    out: Dict[int, int] = {}
    for cid, info in archive.clusters.items():
        for history in info.members_history:
            for eid in history.entry_ids:
                out[int(eid)] = int(cid)
    return out


def retired_to_surviving_from_groups(group_info) -> Dict[int, int]:
    """Return ``{retired_cid: surviving_cid}`` for every merge in this batch.

    ``group_info`` is the list of :class:`ClusterGroup` returned by
    ``ClusterManager.process_batch`` via ``BatchResult.group_info``.
    When a group has multiple ``old_ids``, the smallest wins and the
    others are retired.
    """
    out: Dict[int, int] = {}
    for group in group_info:
        surviving = int(group.surviving)
        for oid in group.old_ids:
            oid_int = int(oid)
            if oid_int != surviving:
                out[oid_int] = surviving
    return out


def add_surviving_rep_transitions(
    rep_relabel: Dict[str, str],
    survivors: Iterable[int],
    pre_rep_object_ids: Dict[int, str],
    archive: Archive,
) -> Dict[str, str]:
    """Augment a ``{retired_rep: surviving_rep}`` map with *surviving*
    clusters whose own rep changed during a merge.

    A merge keeps ``surviving_cid = min(old_ids)`` but sets the merged
    rep to the lower-``min_frame`` member across all merged clusters
    (``archive._update_rep_and_min_frame``). When the *absorbed* cluster
    supplied that earlier rep, the surviving cluster adopts a new rep --
    its own prior rows (live / tracking / compressed) still carry the
    pre-merge rep, and the retired-only cascade never moves them (the
    ``{retired_rep: surviving_rep}`` entry collapses to a self-map and is
    skipped). This helper adds an explicit
    ``{old_surviving_rep: new_surviving_rep}`` for each such survivor so
    all three cascades migrate the otherwise-orphaned rows.

    Mutates and returns ``rep_relabel`` in place.

    Args:
        rep_relabel: ``{retired_rep: surviving_rep}`` (digit-strings).
        survivors: surviving cluster_ids of this batch's merges
            (typically ``set(retired_map.values())``).
        pre_rep_object_ids: snapshot of ``cluster_id_to_object_id`` for
            every cluster taken *before* ``process_batch`` ran, keyed by
            cluster_id. Survivors absent from the snapshot were freshly
            allocated and have no prior rows to strand.
        archive: the post-merge archive (source of the new rep).
    """
    for cid in {int(s) for s in survivors}:
        old_obj = pre_rep_object_ids.get(cid)
        if old_obj is None:
            continue
        new_obj = cluster_id_to_object_id(cid, archive)
        if old_obj != new_obj:
            rep_relabel[old_obj] = new_obj
    return rep_relabel


def classify_row(
    entry_id: int,
    raw_object_id: str,
    entry_to_cluster: Dict[int, int],
    seen_entry_ids: Set[int],
    clustering_enabled: bool,
    archive: Optional[Archive] = None,
) -> Tuple[Optional[str], str]:
    """Decide how a single main-collection row should flow through compression.

    Returns:
        ``(effective_object_id, disposition)``.

        * ``disposition == DISPOSITION_PROCESS`` -> include the row in
          compression under ``effective_object_id``.
        * ``disposition == DISPOSITION_SKIP_FILTERED`` -> do not write
          to compressed DB; caller still deletes from main.
        * ``disposition == DISPOSITION_DEFER_UNSEEN`` -> leave the row
          in main for a later tick; ``effective_object_id`` is ``None``.

    ``archive`` is required when ``clustering_enabled`` is True so the
    helper can resolve cluster_id -> rep_object_id via
    :func:`cluster_id_to_object_id`.
    """
    if not clustering_enabled:
        return raw_object_id, DISPOSITION_PROCESS
    if archive is None:
        raise ValueError("classify_row requires archive when clustering_enabled=True")
    cid = entry_to_cluster.get(int(entry_id))
    if cid is not None:
        return cluster_id_to_object_id(cid, archive), DISPOSITION_PROCESS
    if int(entry_id) in seen_entry_ids:
        return None, DISPOSITION_SKIP_FILTERED
    return None, DISPOSITION_DEFER_UNSEEN


def apply_compressed_relabel(
    state: Dict[str, List[dict]],
    retired_to_surviving: Dict[int, int],
    archive: Archive,
) -> int:
    """Rewrite an in-memory compressed state dict when cluster ids merge.

    ``state`` is ``{object_id_string -> [item dicts]}``, keyed by the
    cluster's representative tracker_id (digit-string). For each
    ``retired_cid -> surviving_cid`` pair:

      * pop ``state[retired_rep_str]`` (if any),
      * set each item's ``object_id`` to ``surviving_rep_str``,
      * extend ``state[surviving_rep_str]`` (creating the key if
        absent) with those items.

    When the surviving key already holds items, the retired items are
    appended. The caller is expected to run ``recompress_single_object``
    on any key that may have collided to dedup / resample; this helper
    only moves bytes around.

    Mirrors the Milvus upsert performed by
    :func:`compressed_relabel.relabel_compressed_clusters`.
    Returns the number of items moved.
    """
    if not retired_to_surviving:
        return 0
    moved = 0
    for retired_cid, surviving_cid in retired_to_surviving.items():
        retired_key = cluster_id_to_object_id(retired_cid, archive)
        surviving_key = cluster_id_to_object_id(surviving_cid, archive)
        if retired_key not in state:
            continue
        items = state.pop(retired_key)
        for item in items:
            item["object_id"] = surviving_key
        state.setdefault(surviving_key, []).extend(items)
        moved += len(items)
    return moved


def partition_rows(
    rows: Iterable[dict],
    *,
    entry_to_cluster: Dict[int, int],
    seen_entry_ids: Set[int],
    clustering_enabled: bool,
    archive: Optional[Archive] = None,
) -> Tuple[List[dict], List[int], List[int]]:
    """Split main-collection rows into (to_compress, filtered_eids, deferred_eids).

    ``to_compress`` is the subset that should flow into per-object
    compression; the caller is expected to use each row's (possibly
    rewritten) ``object_id`` as the grouping key. This function mutates
    ``object_id`` in place on rows that are clustered so the downstream
    grouping-by-object_id code needs no further changes. Raw rows (when
    clustering is disabled) pass through untouched.

    ``filtered_eids`` are Stage 2A-filtered entries that should be
    deleted from main but not written to compressed.

    ``deferred_eids`` are rows clustering hasn't looked at yet; they
    should stay in main for the next tick.

    ``archive`` is required when ``clustering_enabled`` is True.
    """
    to_compress: List[dict] = []
    filtered: List[int] = []
    deferred: List[int] = []
    for item in rows:
        eid = int(item["entry_id"])
        raw_oid = str(item["object_id"])
        eff, disp = classify_row(
            eid,
            raw_oid,
            entry_to_cluster,
            seen_entry_ids,
            clustering_enabled,
            archive,
        )
        if disp == DISPOSITION_PROCESS:
            item["object_id"] = eff
            to_compress.append(item)
        elif disp == DISPOSITION_SKIP_FILTERED:
            filtered.append(eid)
        else:
            deferred.append(eid)
    return to_compress, filtered, deferred
