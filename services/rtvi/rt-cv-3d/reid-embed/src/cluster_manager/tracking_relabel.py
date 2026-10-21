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

"""In-memory relabel of tracking-DB buckets after cluster assignment.

When the clustering step assigns a cluster id to an ``(object_id,
sensor_id, frame_id)`` triple, the service-side tracking DB needs
its matching ``TrackedObject`` moved from the raw-tracker-id bucket
to the cluster-id bucket. This helper performs that move on a
generic ``{object_id: {stream_id: [tobj, ...]}}`` mapping, so the
service can drive it against its live ``object_tracking_db`` and
tests can drive it against plain dicts.

Kept in the pure library (no pymilvus, no mp.Queue) so the service
and offline tooling share one implementation.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Mapping, MutableMapping, Sequence, Tuple

from cluster_manager.compression_labeling import cluster_id_to_object_id
from cluster_manager.types import Archive

logger = logging.getLogger(__name__)

# (old_object_id, stream_id, frame_id, new_object_id). Payload produced
# by the service after a clustering batch assigns cluster ids.
TrackingRelabelUpdate = Tuple[str, str, int, str]


def apply_tracking_relabel(
    tracking_db: MutableMapping[str, MutableMapping[str, List[Any]]],
    tracking_lock: Any,
    updates: Sequence[TrackingRelabelUpdate],
) -> int:
    """Move TrackedObjects between ``tracking_db`` buckets.

    For each ``(old_oid, stream_id, frame_id, new_oid)`` tuple:
      * find the matching object (same ``.frame_id``) in
        ``tracking_db[old_oid][stream_id]``,
      * remove it from that list,
      * rewrite its ``.object_id`` attribute to ``new_oid``,
      * append it to ``tracking_db[new_oid][stream_id]`` (creating
        the bucket if absent).

    Groups updates by ``(old_oid, stream_id)`` so each source bucket
    is scanned once regardless of how many frames move.

    Args:
        tracking_db: mutable ``{oid: {stream: [tobj, ...]}}``; each
            ``tobj`` must expose mutable ``object_id`` and readable
            ``frame_id`` attributes (pydantic ``TrackedObject`` or
            any duck-typed stand-in).
        tracking_lock: context manager guarding ``tracking_db``.
            Typically ``threading.Lock`` in a single-process caller;
            any re-entrant-compatible lock works.
        updates: relabel instructions.

    Returns:
        Number of TrackedObjects actually moved. Updates for
        unknown ``(old_oid, stream_id)`` pairs or frame_ids that
        don't match any object are silently skipped.
    """
    if not updates:
        return 0

    # Defensive ``str``/``int`` coercion on incoming update fields: callers
    # occasionally pass int object_ids when the contract wants str (common
    # when deriving from raw tracker ids that look like ints). Normalizing
    # once here keeps the bucket-key path consistent and is exercised by
    # ``test_string_coercion_for_update_fields``.
    by_source: Dict[Tuple[str, str], Dict[int, str]] = {}
    for old_oid, stream_id, frame_id, new_oid in updates:
        by_source.setdefault((str(old_oid), str(stream_id)), {})[int(frame_id)] = str(new_oid)

    moved = 0
    with tracking_lock:
        for (old_oid, stream_id), frame_map in by_source.items():
            bucket = tracking_db.get(old_oid, {}).get(stream_id)
            if not bucket:
                continue
            kept: List[Any] = []
            by_target: Dict[str, List[Any]] = {}
            for tobj in bucket:
                # Objects with a malformed ``frame_id`` shouldn't crash the
                # whole relabel -- log a warning, keep the object in its
                # current bucket, and move on. This protects the maintenance
                # loop from a single bad tracking row in an ingest burst.
                try:
                    frame_key = int(tobj.frame_id)
                except (ValueError, TypeError) as exc:
                    logger.warning(
                        "tracking_relabel: skipping object in bucket " "(%r, %r) with non-integer frame_id=%r: %s",
                        old_oid,
                        stream_id,
                        getattr(tobj, "frame_id", None),
                        exc,
                    )
                    kept.append(tobj)
                    continue
                new_oid = frame_map.get(frame_key)
                if new_oid is None:
                    kept.append(tobj)
                    continue
                tobj.object_id = new_oid
                # Mark per-entry so coverage / per-batch tracking dict
                # builds can tell apart clustered relabel survivors from
                # fresh raw-tracker arrivals when the bucket key collides
                # with a cluster rep id.
                tobj.is_clustered = True
                by_target.setdefault(new_oid, []).append(tobj)
                moved += 1
            tracking_db[old_oid][stream_id] = kept
            for new_oid, moved_list in by_target.items():
                tracking_db.setdefault(new_oid, {}).setdefault(stream_id, []).extend(moved_list)
    return moved


def build_retired_cluster_tracking_updates(
    tracking_db: MutableMapping[str, MutableMapping[str, List[Any]]],
    tracking_lock: Any,
    retired_to_surviving: Mapping[int, int],
    archive: Archive,
) -> List[TrackingRelabelUpdate]:
    """Build per-frame updates moving tracking entries from each retired
    cluster bucket to the surviving cluster bucket. Output is consumable
    by :func:`apply_tracking_relabel`. Rows with non-integer ``frame_id``
    are skipped with a warning.

    Resolves cluster_id -> rep_object_id via the supplied ``archive``.
    The retired cluster's rep is still present in
    ``archive.rep_object_id`` because ``merge_batch_into_archive``
    intentionally does not prune retired entries -- the cascade depends
    on this.
    """
    if not retired_to_surviving:
        return []
    # Resolve cluster_ids -> rep object_ids once (no tracking_db access, so
    # no lock needed here), then drive the bucket move via the rep-map path.
    rep_relabel: Dict[str, str] = {}
    for retired_cid, surviving_cid in retired_to_surviving.items():
        retired_key = cluster_id_to_object_id(int(retired_cid), archive)
        surviving_key = cluster_id_to_object_id(int(surviving_cid), archive)
        if retired_key != surviving_key:
            rep_relabel[retired_key] = surviving_key
    return build_tracking_updates_from_rep_map(tracking_db, tracking_lock, rep_relabel)


def build_tracking_updates_from_rep_map(
    tracking_db: MutableMapping[str, MutableMapping[str, List[Any]]],
    tracking_lock: Any,
    rep_relabel: Mapping[str, str],
) -> List[TrackingRelabelUpdate]:
    """Build per-frame tracking updates from a resolved ``{old_rep: new_rep}``
    map (digit-strings), moving each ``old_rep`` bucket's **already-clustered**
    entries to the ``new_rep`` bucket.

    This is the common engine behind both merge cascades: the retired-cluster
    cascade (``build_retired_cluster_tracking_updates`` resolves cluster_ids to
    reps and delegates here) and the surviving-cluster rep-transition cascade
    (the service passes the augmented ``retired_rep_map`` straight in). Self-maps
    (``old == new``) and empty buckets are skipped.

    Only clustered rows move: a merge relabels rows clustering owns. Fresh
    (``is_clustered=False``) rows that merely share a retired rep's bucket
    (tracker re-ID feedback) are left untouched, so the next clustering tick
    assigns them and they stay visible to the coverage check.
    """
    if not rep_relabel:
        return []
    updates: List[TrackingRelabelUpdate] = []
    with tracking_lock:
        for retired_key, surviving_key in rep_relabel.items():
            if retired_key == surviving_key:
                continue
            bucket = tracking_db.get(retired_key)
            if not bucket:
                continue
            for stream_id, tracked_list in bucket.items():
                for tobj in tracked_list:
                    if not tobj.is_clustered:
                        continue
                    try:
                        frame_key = int(tobj.frame_id)
                    except (ValueError, TypeError) as exc:
                        logger.warning(
                            "tracking_relabel: skipping retired-bucket entry "
                            "(%r, %r) with non-integer frame_id=%r: %s",
                            retired_key,
                            stream_id,
                            getattr(tobj, "frame_id", None),
                            exc,
                        )
                        continue
                    updates.append((retired_key, str(stream_id), frame_key, surviving_key))
    return updates
