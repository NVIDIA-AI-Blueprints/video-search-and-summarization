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

"""Milvus-aware helpers for the clustering maintenance tick.

These wrap the three service-side operations the clustering step
needs to perform on a live Milvus deployment:

  * relabel main-collection rows to carry cluster ids,
  * delete Stage 2A-filtered rows from main,
  * snapshot main / compressed / tracking state to JSON for offline
    verification.

Kept outside ``cluster_manager/`` so the algorithm library stays
pymilvus-free; callers inject a ``pymilvus.Collection`` (or any
compatible duck type, for tests).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from cluster_manager.compression_labeling import cluster_id_to_object_id
from cluster_manager.types import Archive, EmbeddingRecord


def relabel_main_clusters(
    collection: Any,
    embeddings: Sequence[EmbeddingRecord],
    entry_to_cluster: Mapping[int, int],
    archive: Archive,
    *,
    batch_size: int,
    logger: Optional[logging.Logger] = None,
) -> int:
    """Rewrite main-collection rows so clustered entry_ids carry
    ``object_id = <cluster_rep_tracker_id>`` (digit-string) and
    ``is_clustered = True``.

    Sends a full-row upsert (``entry_id`` PK + ``object_id`` +
    ``sensor_id`` + ``frame_id`` + ``is_clustered`` + ``vector``).

    The rep is sourced from ``archive.rep_object_id``, which the
    cluster manager populates during ``merge_batch_into_archive``
    (locked at cluster formation; merges take the lower-min-frame
    rep). The matching ``is_clustered`` BOOL on the main schema is
    what downstream readers (the next clustering tick, and
    compression) use to tell apart already-clustered rows from new
    raw-tracker arrivals -- since both now share the int64 namespace.

    Returns the number of rows rewritten.
    """
    if not entry_to_cluster:
        return 0
    rows_by_eid = {int(e.entry_id): e for e in embeddings}
    targets: List[Tuple[int, int]] = []
    for eid, cid in entry_to_cluster.items():
        if int(eid) in rows_by_eid:
            targets.append((int(eid), int(cid)))
    if not targets:
        return 0
    for i in range(0, len(targets), batch_size):
        chunk = targets[i : i + batch_size]
        rows = []
        for eid, cid in chunk:
            rec = rows_by_eid[int(eid)]
            rows.append(
                {
                    "entry_id": int(eid),
                    "object_id": cluster_id_to_object_id(cid, archive),
                    "sensor_id": str(rec.sensor_id),
                    "frame_id": int(rec.frame_id),
                    "is_clustered": True,
                    "vector": rec.vector,
                }
            )
        collection.upsert(rows)
    return len(targets)


def relabel_retired_main_clusters(
    collection: Any,
    retired_to_surviving: Mapping[str, str],
    *,
    batch_size: int,
    logger: Optional[logging.Logger] = None,
) -> int:
    """Rewrite main-collection rows whose ``object_id`` is a retired
    cluster's rep to the surviving cluster's rep. Only ``is_clustered``
    rows are moved (the clustered members the merge owns).

    Companion to :func:`relabel_main_clusters`, which only handles the
    *current* batch's rows. When a later batch retires cluster A into
    cluster B, prior-batch rows still in main carry ``object_id = rep(A)``
    and would otherwise be returned by search as a separate identity.
    This pass closes that gap so the main DB stays consistent with the
    archive's merge decisions across batches.

    Mirrors :func:`service_adapters.compressed_relabel.relabel_compressed_clusters`
    for the compressed side: queries every column up front, then sends
    a FULL-ROW upsert.

    Partial update (``upsert(..., partial_update=True)`` with a smaller
    payload) was attempted first and empirically fails on Milvus 2.6.15
    for this call site, even though the same flag and a 3-field payload
    works for :func:`relabel_main_clusters`. The progression of server
    errors (``fieldSchema(sensor_id) ...`` -> ``fieldSchema(vector) ...``
    as fields are added) confirms the server is running per-field
    insert-style validation here, not the partial-merge path. The most
    likely cause is that the retired rows targeted here live in sealed
    segments (they were inserted in a prior tick and have since been
    flushed), and Milvus's server-side partial-update merge is only
    implemented against growing (in-memory) segments. The cost is one
    extra round-trip's worth of vector bytes per retired key per tick.

    Args:
        collection: a loaded ``pymilvus.Collection`` for the main schema.
        retired_to_surviving: mapping from retired cluster's rep
            (digit-string) to surviving rep (digit-string). The caller
            resolves cluster_ids to reps via ``archive.rep_object_id``.
        batch_size: chunk size for upsert calls.
        logger: optional logger for per-cluster relabel info + errors.

    Returns:
        Total number of rows rewritten across all retired keys.
    """
    if not retired_to_surviving:
        return 0

    _log = logger or logging.getLogger(__name__)

    total_rewritten = 0
    for retired_key, surviving_key in retired_to_surviving.items():
        if retired_key == surviving_key:
            # Surviving rep coincided with retired rep. Nothing to relabel.
            continue
        try:
            # Clustered rows only: a merge relabels rows clustering owns.
            # Fresh un-clustered rows sharing this retired rep's id are left
            # for the next clustering tick (keeps main + tracking consistent).
            prev = collection.query(
                expr=f"object_id == '{retired_key}' && is_clustered == true",
                output_fields=[
                    "entry_id",
                    "sensor_id",
                    "frame_id",
                    "is_clustered",
                    "vector",
                ],
            )
        except Exception as e:
            _log.error(f"Relabel: failed to query main for retired key={retired_key}: {e}")
            continue
        if not prev:
            continue
        _log.info(f"Relabel (main): cluster {retired_key} -> {surviving_key} ({len(prev)} rows)")
        try:
            for i in range(0, len(prev), batch_size):
                batch = prev[i : i + batch_size]
                rows = [
                    {
                        "entry_id": int(item["entry_id"]),
                        "object_id": surviving_key,
                        "sensor_id": str(item["sensor_id"]),
                        "frame_id": int(item["frame_id"]),
                        "is_clustered": bool(item.get("is_clustered", True)),
                        "vector": item["vector"],
                    }
                    for item in batch
                ]
                collection.upsert(rows)
        except Exception as e:
            _log.error(
                f"Relabel (main): upsert failed for {retired_key} -> "
                f"{surviving_key}: {e}. Some rows may still carry the "
                "retired key."
            )
            continue
        total_rewritten += len(prev)

    return total_rewritten


def delete_filtered_from_main(
    collection: Any,
    entry_ids: Sequence[int],
    *,
    batch_size: int,
) -> int:
    """Delete Stage 2A-filtered rows from the main collection.

    Called by the clustering step so compression doesn't have to.
    Rows that were *seen* by clustering this batch but didn't land
    in any cluster (short sub-tracklets pruned by
    ``min_embs_threshold``) are removed here. Idempotent against
    already-deleted ids.

    Returns the number of ids passed in (approximate -- Milvus
    doesn't report a per-batch delete count).
    """
    eids = list(entry_ids)
    if not eids:
        return 0
    for i in range(0, len(eids), batch_size):
        batch = eids[i : i + batch_size]
        collection.delete(expr=f"entry_id in {batch}")
    return len(eids)


def write_db_snapshots(
    batch_idx: int,
    *,
    output_dir: str | Path,
    main_collection: Optional[Any] = None,
    compressed_collection: Optional[Any] = None,
    tracking_db: Optional[Mapping[str, Mapping[str, Sequence[Any]]]] = None,
    tracking_lock: Optional[Any] = None,
    sections: Sequence[str] = ("main", "tracking", "compressed"),
    stage: str = "",
    iterator_batch_size: int = 16384,
    logger: Optional[logging.Logger] = None,
) -> Dict[str, str]:
    """Dump selected DB state(s) as JSON under
    ``<output_dir>/batch_<NNN>/<kind>_db[_<stage>].json``.

    ``stage`` is an optional suffix (``"initial"``,
    ``"post_clustering"``, ``"post_compression"``) so the three
    lifecycle points for one batch coexist without clobbering.

    Each section is optional and requires the matching handle:

        * ``"main"``      needs ``main_collection``
        * ``"compressed"`` needs ``compressed_collection``
        * ``"tracking"``  needs ``tracking_db`` and ``tracking_lock``

    Missing handles for a requested section are silently skipped.

    Vectors are intentionally excluded from the main/compressed
    dumps -- the verification workflow checks labels, not feature
    values, and 1280-dim vectors per row bloat the JSON ~10-100x.

    Returns ``{section: path}`` for each section that wrote
    successfully.
    """
    _log = logger or logging.getLogger(__name__)
    batch_dir = Path(output_dir) / f"batch_{batch_idx:03d}"
    batch_dir.mkdir(parents=True, exist_ok=True)
    suffix = f"_{stage}" if stage else ""
    paths: Dict[str, str] = {}

    if "main" in sections and main_collection is not None:
        try:
            rows = _query_iterator_to_list(
                main_collection,
                ["entry_id", "object_id", "sensor_id", "frame_id", "is_clustered"],
                iterator_batch_size,
            )
            p = batch_dir / f"main_db{suffix}.json"
            with p.open("w") as f:
                json.dump(rows, f, indent=2, default=str)
            paths["main"] = str(p)
        except Exception as e:
            _log.error(f"snapshot[{stage}]: main DB dump failed: {e}")

    if "compressed" in sections and compressed_collection is not None:
        try:
            rows = _query_iterator_to_list(
                compressed_collection,
                ["entry_id", "object_id", "sensor_id", "frame_id", "weight"],
                iterator_batch_size,
            )
            p = batch_dir / f"compressed_db{suffix}.json"
            with p.open("w") as f:
                json.dump(rows, f, indent=2, default=str)
            paths["compressed"] = str(p)
        except Exception as e:
            _log.error(f"snapshot[{stage}]: compressed DB dump failed: {e}")

    if "tracking" in sections and tracking_db is not None and tracking_lock is not None:
        try:
            snapshot: Dict[str, Dict[str, list]] = {}
            with tracking_lock:
                for oid, streams in tracking_db.items():
                    snapshot[str(oid)] = {
                        str(sid): [_tracked_to_dict(obj) for obj in tracked_list]
                        for sid, tracked_list in streams.items()
                    }
            p = batch_dir / f"tracking_db{suffix}.json"
            with p.open("w") as f:
                json.dump(snapshot, f, indent=2, default=str)
            paths["tracking"] = str(p)
        except Exception as e:
            _log.error(f"snapshot[{stage}]: tracking DB dump failed: {e}")

    return paths


def _query_iterator_to_list(
    collection: Any,
    output_fields: Sequence[str],
    batch_size: int,
) -> List[dict]:
    """Drain ``collection.query_iterator`` into a single list. Closes
    the iterator on every exit path, including exceptions.
    """
    rows: List[dict] = []
    it = collection.query_iterator(
        batch_size=batch_size,
        expr="",
        output_fields=list(output_fields),
    )
    try:
        while True:
            chunk = it.next()
            if not chunk:
                break
            rows.extend(chunk)
    finally:
        it.close()
    return rows


def _tracked_to_dict(obj: Any) -> dict:
    """Best-effort serialization of a tracking entry. Handles
    pydantic v2 (``.model_dump()``), pydantic v1 (``.dict()``), and
    plain dataclass/object fallbacks.
    """
    if hasattr(obj, "model_dump"):
        return obj.model_dump()
    if hasattr(obj, "dict"):
        return obj.dict()
    if hasattr(obj, "__dict__"):
        return dict(obj.__dict__)
    # Last resort: rely on json.dump's default=str to stringify.
    return {"_repr": str(obj)}
