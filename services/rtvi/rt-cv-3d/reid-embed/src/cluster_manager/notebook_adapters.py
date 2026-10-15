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

"""Conversion helpers between the notebook's dict-based shapes and the
:mod:`cluster_manager` dataclasses.

The original notebook represents sub-tracklets and the persistent archive
as nested dicts of numpy arrays. The refactored modules use
:class:`SubTracklet`, :class:`Archive`, etc. These adapters make it
trivial for the refactored notebook to call into ``cluster_manager``
without changing its driver cell.

All conversions are **lossless**; round-trip tests live in
``cluster_manager/tests/test_notebook_adapters.py``.
"""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Sequence

import numpy as np

from cluster_manager.types import (
    Archive,
    BatchStats,
    ClusterGroup,
    ClusterInfo,
    ClusterNode,
    EmbeddingRecord,
    MemberHistoryEntry,
    SubTracklet,
)

# ---------------------------------------------------------------------------
# Embeddings
# ---------------------------------------------------------------------------


def dataframe_rows_to_records(rows: Sequence[Mapping[str, Any]]) -> List[EmbeddingRecord]:
    """Convert a sequence of dataframe-row dicts to :class:`EmbeddingRecord` list.

    Expects keys ``entry_id``, ``object_id``, ``sensor_id``, ``frame_id``,
    ``parsed_vector``. A synthetic ``entry_id`` falls back to the row's
    positional index when the column is missing.
    """
    out: List[EmbeddingRecord] = []
    for i, row in enumerate(rows):
        vec = row["parsed_vector"]
        if not isinstance(vec, np.ndarray):
            vec = np.asarray(vec, dtype=np.float32)
        out.append(
            EmbeddingRecord(
                entry_id=int(row["entry_id"]) if "entry_id" in row else i,
                object_id=int(row["object_id"]),
                sensor_id=str(row["sensor_id"]),
                frame_id=int(row["frame_id"]),
                vector=vec,
            )
        )
    return out


# ---------------------------------------------------------------------------
# Sub-tracklets
# ---------------------------------------------------------------------------


def subtracklet_to_dict(sub: SubTracklet) -> Dict[str, Any]:
    """Convert a :class:`SubTracklet` dataclass to the notebook's dict shape."""
    return {
        "name": sub.name,
        "batch": sub.batch,
        "obj_id": sub.object_id,
        "sub_id": sub.sub_id,
        "vecs": sub.vecs,
        "frame_ids": sub.frame_ids,
        "sensor_ids": sub.sensor_ids,
        "entry_ids": sub.entry_ids,
    }


def subtracklets_to_dicts(subs: Mapping[str, SubTracklet]) -> Dict[str, Dict[str, Any]]:
    return {name: subtracklet_to_dict(s) for name, s in subs.items()}


def subtracklet_from_dict(d: Mapping[str, Any]) -> SubTracklet:
    return SubTracklet(
        name=d["name"],
        batch=d["batch"],
        object_id=d["obj_id"],
        sub_id=d["sub_id"],
        vecs=d["vecs"],
        frame_ids=d["frame_ids"],
        sensor_ids=list(d["sensor_ids"]),
        entry_ids=list(d.get("entry_ids", [])),
    )


def subtracklets_from_dicts(d: Mapping[str, Mapping[str, Any]]) -> Dict[str, SubTracklet]:
    return {name: subtracklet_from_dict(v) for name, v in d.items()}


# ---------------------------------------------------------------------------
# Archive
# ---------------------------------------------------------------------------


def _member_history_from_dict(d: Mapping[str, Any]) -> MemberHistoryEntry:
    return MemberHistoryEntry(
        sub_name=d["sub_name"],
        source_batch=d["source_batch"],
        obj_id=d["obj_id"],
        frame_ids=list(d["frame_ids"]),
        sensor_ids=list(d["sensor_ids"]),
        entry_ids=list(d.get("entry_ids", [])),
    )


def _member_history_to_dict(h: MemberHistoryEntry) -> Dict[str, Any]:
    return {
        "sub_name": h.sub_name,
        "source_batch": h.source_batch,
        "obj_id": h.obj_id,
        "frame_ids": list(h.frame_ids),
        "sensor_ids": list(h.sensor_ids),
        "entry_ids": list(h.entry_ids),
    }


def archive_from_dict(archive_dict: Mapping[str, Any]) -> Archive:
    """Build an :class:`Archive` from the notebook's nested-dict shape."""
    arch = Archive()
    arch.next_cluster_id = int(archive_dict["next_cluster_id"])
    arch.cannot_merge_clusters = set(archive_dict["cannot_merge_clusters"])
    for cid, info_dict in archive_dict["clusters"].items():
        cid_int = int(cid)
        arch.clusters[cid_int] = ClusterInfo(
            cluster_id=cid_int,
            rep=info_dict["rep"],
            n_embs=int(info_dict["n_embs"]),
            created_batch=int(info_dict["created_batch"]),
            last_updated_batch=int(info_dict["last_updated_batch"]),
            members_history=[_member_history_from_dict(h) for h in info_dict.get("members_history", [])],
        )
    return arch


def archive_to_dict(archive: Archive) -> Dict[str, Any]:
    """Render an :class:`Archive` back to the notebook's nested-dict shape."""
    return {
        "next_cluster_id": archive.next_cluster_id,
        "cannot_merge_clusters": set(archive.cannot_merge_clusters),
        "clusters": {
            cid: {
                "rep": info.rep,
                "n_embs": info.n_embs,
                "created_batch": info.created_batch,
                "last_updated_batch": info.last_updated_batch,
                "members_history": [_member_history_to_dict(h) for h in info.members_history],
            }
            for cid, info in archive.clusters.items()
        },
    }


def write_archive_into_dict(archive: Archive, out: Dict[str, Any]) -> None:
    """Write archive state into an existing dict in place (preserves identity)."""
    out.update(archive_to_dict(archive))


# ---------------------------------------------------------------------------
# Stage-5 results
# ---------------------------------------------------------------------------


def batch_stats_to_dict(stats: BatchStats) -> Dict[str, Any]:
    return {
        "batch": stats.batch,
        "n_groups": stats.n_groups,
        "n_new_clusters": stats.n_new_clusters,
        "n_updated": stats.n_updated,
        "n_unchanged": stats.n_unchanged,
        "n_retired": stats.n_retired,
        "n_clusters_total": stats.n_clusters_total,
        "n_cm_pairs": stats.n_cm_pairs,
    }


def group_info_to_dicts(groups: Sequence[ClusterGroup]) -> List[Dict[str, Any]]:
    return [
        {
            "members": g.members,
            "old_ids": g.old_ids,
            "sub_names": g.sub_names,
            "surviving": g.surviving,
            "status": g.status,
        }
        for g in groups
    ]


# ---------------------------------------------------------------------------
# Node metadata
# ---------------------------------------------------------------------------


def node_metas_from_dicts(metas: Sequence[Mapping[str, Any]]) -> List[ClusterNode]:
    return [ClusterNode(kind=m["kind"], id=m["id"]) for m in metas]
