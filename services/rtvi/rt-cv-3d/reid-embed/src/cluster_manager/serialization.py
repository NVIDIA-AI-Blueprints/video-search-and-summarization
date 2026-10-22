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

"""JSON-based serialization for cluster manager state and checkpoints.

Wire format
-----------

A saved :class:`Archive` is a single JSON file. ``rep`` vectors are
encoded inline as plain float lists (archives typically hold at most a
few hundred clusters x 1280 dims, so JSON bloat is acceptable).

Checkpoints written during notebook execution bundle an
``.npz`` (one compressed archive of all numpy arrays) next to a
``.json`` sidecar keyed on the same labels. See
:func:`save_checkpoint` and :func:`load_checkpoint`.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Mapping, Tuple

import numpy as np

from cluster_manager.types import (
    Archive,
    BatchResult,
    BatchStats,
    ClusterInfo,
    MemberHistoryEntry,
    SubTracklet,
)

# ---------------------------------------------------------------------------
# Archive ↔ JSON
# ---------------------------------------------------------------------------


def _member_to_json(h: MemberHistoryEntry) -> Dict[str, Any]:
    return {
        "sub_name": h.sub_name,
        "source_batch": int(h.source_batch),
        "obj_id": int(h.obj_id),
        "frame_ids": [int(f) for f in h.frame_ids],
        "sensor_ids": list(h.sensor_ids),
        "entry_ids": [int(e) for e in h.entry_ids],
    }


def _member_from_json(d: Dict[str, Any]) -> MemberHistoryEntry:
    return MemberHistoryEntry(
        sub_name=str(d["sub_name"]),
        source_batch=int(d["source_batch"]),
        obj_id=int(d["obj_id"]),
        frame_ids=[int(f) for f in d["frame_ids"]],
        sensor_ids=[str(s) for s in d["sensor_ids"]],
        entry_ids=[int(e) for e in d["entry_ids"]],
    )


def _stats_to_json(s: BatchStats) -> Dict[str, Any]:
    return {
        "batch": s.batch,
        "n_groups": s.n_groups,
        "n_new_clusters": s.n_new_clusters,
        "n_updated": s.n_updated,
        "n_unchanged": s.n_unchanged,
        "n_retired": s.n_retired,
        "n_clusters_total": s.n_clusters_total,
        "n_cm_pairs": s.n_cm_pairs,
    }


def _stats_from_json(d: Dict[str, Any]) -> BatchStats:
    return BatchStats(
        batch=int(d["batch"]),
        n_groups=int(d["n_groups"]),
        n_new_clusters=int(d["n_new_clusters"]),
        n_updated=int(d["n_updated"]),
        n_unchanged=int(d["n_unchanged"]),
        n_retired=int(d["n_retired"]),
        n_clusters_total=int(d["n_clusters_total"]),
        n_cm_pairs=int(d["n_cm_pairs"]),
    )


_ARCHIVE_VERSION = 1


def archive_to_json_obj(archive: Archive) -> Dict[str, Any]:
    """Encode an :class:`Archive` as a JSON-serializable dict."""
    return {
        "version": _ARCHIVE_VERSION,
        "next_cluster_id": int(archive.next_cluster_id),
        "seen_entry_ids": sorted(int(x) for x in archive.seen_entry_ids),
        "cannot_merge_clusters": [sorted(int(x) for x in pair) for pair in archive.cannot_merge_clusters],
        "batch_log": [_stats_to_json(s) for s in archive.batch_log],
        "clusters": {
            str(cid): {
                "rep": [float(x) for x in info.rep],
                "n_embs": int(info.n_embs),
                "created_batch": int(info.created_batch),
                "last_updated_batch": int(info.last_updated_batch),
                "members_history": [_member_to_json(h) for h in info.members_history],
                "prototypes": ([[float(x) for x in p] for p in info.prototypes] if info.prototypes is not None else []),
                "proto_counts": [int(c) for c in info.proto_counts],
            }
            for cid, info in archive.clusters.items()
        },
    }


def archive_from_json_obj(data: Dict[str, Any]) -> Archive:
    """Decode an :class:`Archive` from the dict produced by :func:`archive_to_json_obj`."""
    version = data.get("version")
    if version != _ARCHIVE_VERSION:
        raise ValueError(f"Unsupported archive schema version: {version!r}")
    arch = Archive()
    arch.next_cluster_id = int(data["next_cluster_id"])
    arch.seen_entry_ids = {int(x) for x in data["seen_entry_ids"]}
    arch.cannot_merge_clusters = {frozenset(int(x) for x in pair) for pair in data["cannot_merge_clusters"]}
    arch.batch_log = [_stats_from_json(s) for s in data["batch_log"]]
    for cid_str, info_d in data["clusters"].items():
        cid = int(cid_str)
        arch.clusters[cid] = ClusterInfo(
            cluster_id=cid,
            rep=np.asarray(info_d["rep"], dtype=np.float32),
            n_embs=int(info_d["n_embs"]),
            created_batch=int(info_d["created_batch"]),
            last_updated_batch=int(info_d["last_updated_batch"]),
            members_history=[_member_from_json(h) for h in info_d["members_history"]],
            prototypes=(np.asarray(info_d["prototypes"], dtype=np.float32) if info_d.get("prototypes") else None),
            proto_counts=[int(c) for c in info_d.get("proto_counts", [])],
        )
    return arch


def save_archive(archive: Archive, path: str | Path) -> None:
    """Write ``archive`` to ``path`` as JSON."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        json.dump(archive_to_json_obj(archive), f)


def load_archive(path: str | Path) -> Archive:
    """Read an :class:`Archive` from a JSON file produced by :func:`save_archive`."""
    path = Path(path)
    with path.open("r") as f:
        return archive_from_json_obj(json.load(f))


# ---------------------------------------------------------------------------
# Generic checkpoint bundle (for notebook stage dumps)
# ---------------------------------------------------------------------------
#
# A checkpoint bundle is a pair of files sharing a basename:
#   <basename>.npz    compressed numpy archive (one entry per array)
#   <basename>.json   metadata (everything else)
#
# Every ``np.ndarray`` in the input is replaced by a string sentinel
# ``"__array__:<key>"`` in the JSON tree; the actual array lives in the
# .npz under ``<key>``. Frozensets/sets become sorted lists with a
# ``"__kind__": "frozenset" | "set"`` marker so round-trips preserve type.


_ARRAY_PREFIX = "__array__:"


def _encode_payload(obj: Any, arrays: Dict[str, np.ndarray]) -> Any:
    if isinstance(obj, np.ndarray):
        key = f"arr_{len(arrays)}"
        arrays[key] = obj
        return _ARRAY_PREFIX + key
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating,)):
        return float(obj)
    if isinstance(obj, frozenset):
        # Use _sort_key so nested frozensets (or mixed-type elements) sort
        # deterministically without relying on default comparison, which
        # raises TypeError for non-comparable types.
        return {"__kind__": "frozenset", "items": [_encode_payload(x, arrays) for x in sorted(obj, key=_sort_key)]}
    if isinstance(obj, set):
        return {"__kind__": "set", "items": [_encode_payload(x, arrays) for x in sorted(obj, key=_sort_key)]}
    if isinstance(obj, dict):
        return {str(k): _encode_payload(v, arrays) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_encode_payload(x, arrays) for x in obj]
    if isinstance(obj, (int, float, str, bool)) or obj is None:
        return obj
    raise TypeError(f"Cannot encode object of type {type(obj).__name__}")


def _sort_key(x: Any) -> Any:
    if isinstance(x, frozenset):
        return tuple(sorted(x, key=_sort_key))
    return x


def _decode_payload(obj: Any, arrays: Dict[str, np.ndarray]) -> Any:
    if isinstance(obj, str) and obj.startswith(_ARRAY_PREFIX):
        key = obj[len(_ARRAY_PREFIX) :]
        return arrays[key]
    if isinstance(obj, dict):
        kind = obj.get("__kind__")
        if kind == "frozenset":
            return frozenset(_decode_payload(x, arrays) for x in obj["items"])
        if kind == "set":
            return {_decode_payload(x, arrays) for x in obj["items"]}
        return {k: _decode_payload(v, arrays) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_decode_payload(x, arrays) for x in obj]
    return obj


def save_checkpoint(basename: str | Path, payload: Dict[str, Any]) -> Tuple[Path, Path]:
    """Persist an arbitrary payload safely as ``<basename>.npz`` + ``<basename>.json``.

    Returns:
        ``(npz_path, json_path)`` that were written.
    """
    basename = Path(basename)
    basename.parent.mkdir(parents=True, exist_ok=True)
    arrays: Dict[str, np.ndarray] = {}
    encoded = _encode_payload(payload, arrays)
    npz_path = basename.with_suffix(".npz")
    json_path = basename.with_suffix(".json")
    if arrays:
        np.savez_compressed(npz_path, **arrays)
    else:
        # Still write an empty marker so load_checkpoint can rely on its presence
        np.savez_compressed(npz_path)
    with json_path.open("w") as f:
        json.dump(encoded, f)
    return npz_path, json_path


def load_checkpoint(basename: str | Path) -> Any:
    """Load a checkpoint previously written by :func:`save_checkpoint`."""
    basename = Path(basename)
    npz_path = basename.with_suffix(".npz")
    json_path = basename.with_suffix(".json")
    with json_path.open("r") as f:
        encoded = json.load(f)
    with np.load(npz_path, allow_pickle=False) as npz:
        arrays = {k: npz[k] for k in npz.files}
    return _decode_payload(encoded, arrays)


# ---------------------------------------------------------------------------
# Per-batch assignments file
# ---------------------------------------------------------------------------


def write_batch_assignments(
    result: BatchResult,
    sub_tracklets: Mapping[str, SubTracklet],
    archive: Archive,
    frame_start: int,
    frame_end: int,
    output_dir: str | Path,
) -> Path:
    """Persist a per-batch ``assignments.json`` record.

    One JSON file per batch under
    ``<output_dir>/batch_<NNN>/assignments.json`` (zero-padded batch
    index). Layout matches the notebook replay harness so downstream
    tools can join across runs without knowing which produced the
    file.

    Payload fields:
        ``batch_idx``, ``frame_window``, ``n_embeddings_assigned``,
        ``n_clusters_touched``, ``n_clusters_total_after_batch``,
        ``entry_to_cluster`` (stringified keys for JSON),
        ``entry_tuples`` (``entry_id`` -> ``[obj_id, sensor_id,
        frame_id]``; stable across runs, unlike insert-time
        ``entry_id``), and ``cluster_details[cid]`` with
        ``status`` / ``sub_names`` / ``n_added_this_batch`` /
        ``n_total_after_batch`` / ``entry_ids_added`` /
        ``entry_tuples_added``.

    Args:
        result: return value of ``ClusterManager.process_batch``.
        sub_tracklets: the batch's sub-tracklets, typically obtained
            as ``dict(cluster_manager._last_subtracklets)`` right
            after ``process_batch`` returns.
        archive: the archive post-``process_batch`` so
            ``n_total_after_batch`` is accurate.
        frame_start, frame_end: inclusive frame range of the batch.
        output_dir: root directory. The ``batch_NNN`` subdir is
            created if absent.

    Returns:
        Absolute path to the written JSON file.
    """
    batch_idx = int(result.stats.batch)
    batch_dir = Path(output_dir) / f"batch_{batch_idx:03d}"
    batch_dir.mkdir(parents=True, exist_ok=True)

    # entry_id -> (obj_id, sensor_id, frame_id) for this batch's embeddings.
    # Emitted as ``entry_tuples`` so downstream tools can join across
    # runs via the stable triple instead of the insert-time entry_id.
    entry_to_tuple: Dict[int, Tuple[int, str, int]] = {}
    for sub in sub_tracklets.values():
        for i, eid in enumerate(sub.entry_ids):
            entry_to_tuple[int(eid)] = (
                int(sub.object_id),
                str(sub.sensor_ids[i]),
                int(sub.frame_ids[i]),
            )

    cluster_details: Dict[str, Dict[str, Any]] = {}
    for group in result.group_info:
        cid = int(group.surviving)
        cluster_entries: list = []
        cluster_triples: list = []
        for sn in group.sub_names:
            sub = sub_tracklets.get(sn)
            if sub is None:
                continue
            for i, eid in enumerate(sub.entry_ids):
                cluster_entries.append(int(eid))
                cluster_triples.append(
                    [
                        int(sub.object_id),
                        str(sub.sensor_ids[i]),
                        int(sub.frame_ids[i]),
                    ]
                )
        n_total = int(archive.clusters[cid].n_embs) if cid in archive.clusters else 0
        # Rep tracker_id this cluster carries in main/tracking after the
        # tick. Read by the analysis notebook to join post_clustering
        # snapshots (keyed by rep, not cluster_id) back to the cluster_id.
        # Empty string if rep_object_id isn't populated yet (e.g. brand-new
        # cluster whose rep hasn't been computed before serialization).
        rep_oid = archive.rep_object_id.get(cid)
        rep_str = str(int(rep_oid)) if rep_oid is not None else ""
        cluster_details[str(cid)] = {
            "status": group.status,
            "sub_names": list(group.sub_names),
            # Every cluster id pulled into this group. ``surviving`` is
            # ``min(old_ids)`` (one of these entries); any other entry
            # was retired in this batch and merged into ``surviving``.
            # Downstream readers (e.g. the analysis notebook) use this
            # to compute the cumulative retired -> surviving cascade so
            # they can match the archive's "currently alive" cluster
            # count.
            "old_ids": [int(o) for o in group.old_ids],
            "rep_object_id": rep_str,
            "n_added_this_batch": len(cluster_entries),
            "n_total_after_batch": n_total,
            "entry_ids_added": cluster_entries,
            "entry_tuples_added": cluster_triples,
        }

    payload = {
        "batch_idx": batch_idx,
        "frame_window": [int(frame_start), int(frame_end)],
        "n_embeddings_assigned": len(result.entry_to_cluster),
        "n_clusters_touched": len(result.group_info),
        "n_clusters_total_after_batch": int(result.stats.n_clusters_total),
        "entry_to_cluster": {str(k): int(v) for k, v in result.entry_to_cluster.items()},
        "entry_tuples": {str(eid): [obj, sensor, frame] for eid, (obj, sensor, frame) in entry_to_tuple.items()},
        "cluster_details": cluster_details,
    }

    out_path = batch_dir / "assignments.json"
    with out_path.open("w") as f:
        json.dump(payload, f, indent=2)
    return out_path


__all__ = [
    "archive_from_json_obj",
    "archive_to_json_obj",
    "load_archive",
    "load_checkpoint",
    "save_archive",
    "save_checkpoint",
    "write_batch_assignments",
]
