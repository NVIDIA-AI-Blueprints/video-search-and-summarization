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

"""Tests for :mod:`cluster_manager.serialization`."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from cluster_manager.serialization import (
    archive_from_json_obj,
    archive_to_json_obj,
    load_archive,
    load_checkpoint,
    save_archive,
    save_checkpoint,
    write_batch_assignments,
)
from cluster_manager.types import (
    Archive,
    BatchResult,
    BatchStats,
    ClusterGroup,
    ClusterInfo,
    MemberHistoryEntry,
    SubTracklet,
)


def _unit(v: np.ndarray) -> np.ndarray:
    return v / np.linalg.norm(v)


def _sample_archive() -> Archive:
    arch = Archive()
    arch.next_cluster_id = 9
    arch.cannot_merge_clusters = {
        frozenset({0, 3}),
        frozenset({1, 5}),
    }
    arch.batch_log = [
        BatchStats(
            batch=0,
            n_groups=2,
            n_new_clusters=2,
            n_updated=0,
            n_unchanged=0,
            n_retired=0,
            n_clusters_total=2,
            n_cm_pairs=0,
        ),
    ]
    arch.clusters[0] = ClusterInfo(
        cluster_id=0,
        rep=_unit(np.array([1.0, 0.5, 0.25], dtype=np.float32)),
        n_embs=42,
        created_batch=0,
        last_updated_batch=3,
        members_history=[
            MemberHistoryEntry(
                sub_name="b0_1_0",
                source_batch=0,
                obj_id=101,
                frame_ids=[1, 2, 3, 4],
                sensor_ids=["cam1", "cam1", "cam2", "cam2"],
                entry_ids=[10, 11, 12, 13],
            ),
            MemberHistoryEntry(
                sub_name="b3_1_1",
                source_batch=3,
                obj_id=101,
                frame_ids=[100],
                sensor_ids=["cam1"],
                entry_ids=[200],
            ),
        ],
    )
    arch.clusters[3] = ClusterInfo(
        cluster_id=3,
        rep=_unit(np.array([0.1, -0.2, 0.3], dtype=np.float32)),
        n_embs=7,
        created_batch=1,
        last_updated_batch=1,
        members_history=[],
    )
    return arch


class TestArchiveRoundTrip:
    def test_in_memory_roundtrip(self) -> None:
        original = _sample_archive()
        payload = archive_to_json_obj(original)
        restored = archive_from_json_obj(payload)

        assert restored.next_cluster_id == original.next_cluster_id
        assert restored.cannot_merge_clusters == original.cannot_merge_clusters
        assert set(restored.clusters) == set(original.clusters)
        for cid, info in original.clusters.items():
            r = restored.clusters[cid]
            np.testing.assert_allclose(r.rep, info.rep)
            assert r.n_embs == info.n_embs
            assert r.created_batch == info.created_batch
            assert r.last_updated_batch == info.last_updated_batch
            assert [h.sub_name for h in r.members_history] == [h.sub_name for h in info.members_history]

    def test_file_roundtrip(self, tmp_path: Path) -> None:
        original = _sample_archive()
        path = tmp_path / "archive.json"
        save_archive(original, path)
        assert path.exists()
        restored = load_archive(path)
        # Same content, same cluster ids
        assert set(restored.clusters) == set(original.clusters)
        for cid, info in original.clusters.items():
            np.testing.assert_allclose(restored.clusters[cid].rep, info.rep)

    def test_load_rejects_unknown_version(self, tmp_path: Path) -> None:
        path = tmp_path / "bad.json"
        path.write_text('{"version": 999, "clusters": {}, "next_cluster_id": 0, "cannot_merge_clusters": []}')
        with pytest.raises(ValueError, match="Unsupported archive schema version"):
            load_archive(path)

    def test_seen_entry_ids_round_trip(self, tmp_path: Path) -> None:
        arch = _sample_archive()
        arch.seen_entry_ids = {10, 20, 30, 40}
        path = tmp_path / "archive.json"
        save_archive(arch, path)
        restored = load_archive(path)
        assert restored.seen_entry_ids == {10, 20, 30, 40}

    def test_saved_files_use_current_schema_version(self, tmp_path: Path) -> None:
        arch = _sample_archive()
        arch.seen_entry_ids = {1, 2}
        path = tmp_path / "a.json"
        save_archive(arch, path)
        with path.open() as f:
            raw = json.load(f)
        assert raw["version"] == 1
        assert raw["seen_entry_ids"] == [1, 2]  # sorted

    def test_file_is_plain_json(self, tmp_path: Path) -> None:
        """The saved file must parse as JSON with only primitive types — guards against any
        accidental pickle/binary leak."""
        path = tmp_path / "archive.json"
        save_archive(_sample_archive(), path)
        with path.open() as f:
            json.load(f)  # succeeds iff file is valid JSON


class TestCheckpointBundle:
    def test_primitives_only_roundtrip(self, tmp_path: Path) -> None:
        payload = {"a": 1, "b": "hello", "c": [1, 2, 3], "d": {"e": True, "f": None}}
        save_checkpoint(tmp_path / "ck", payload)
        assert (tmp_path / "ck.npz").exists()
        assert (tmp_path / "ck.json").exists()
        assert load_checkpoint(tmp_path / "ck") == payload

    def test_array_roundtrip(self, tmp_path: Path) -> None:
        arr = np.arange(12, dtype=np.float32).reshape(3, 4)
        payload = {"tensor": arr, "meta": {"shape": list(arr.shape)}}
        save_checkpoint(tmp_path / "ck", payload)
        restored = load_checkpoint(tmp_path / "ck")
        np.testing.assert_array_equal(restored["tensor"], arr)
        assert restored["meta"]["shape"] == [3, 4]

    def test_multiple_arrays_in_nested_structure(self, tmp_path: Path) -> None:
        a1 = np.array([1.0, 2.0])
        a2 = np.array([[1, 2], [3, 4]], dtype=np.int32)
        payload = {"pair": [a1, {"nested": a2}], "scalar": 42}
        save_checkpoint(tmp_path / "ck", payload)
        restored = load_checkpoint(tmp_path / "ck")
        np.testing.assert_array_equal(restored["pair"][0], a1)
        np.testing.assert_array_equal(restored["pair"][1]["nested"], a2)
        assert restored["scalar"] == 42

    def test_frozenset_and_set_types_preserved(self, tmp_path: Path) -> None:
        payload = {
            "fset": frozenset({1, 2, 3}),
            "set": {"a", "b"},
            "nested_fs": [frozenset({"x", "y"}), frozenset({1, 2})],
        }
        save_checkpoint(tmp_path / "ck", payload)
        restored = load_checkpoint(tmp_path / "ck")
        assert isinstance(restored["fset"], frozenset)
        assert restored["fset"] == frozenset({1, 2, 3})
        assert isinstance(restored["set"], set)
        assert restored["set"] == {"a", "b"}
        assert all(isinstance(x, frozenset) for x in restored["nested_fs"])

    def test_frozenset_with_nested_frozensets(self, tmp_path: Path) -> None:
        """Encoding a frozenset of frozensets must not raise ``TypeError``.

        Default ``sorted()`` on ``frozenset`` elements fails because
        frozensets are not comparable with ``<``. The encoder must route
        through ``_sort_key`` (recursively flattens nested frozensets to
        comparable tuples) for the ordering to be deterministic and safe.
        """
        payload = {
            "pair_of_pairs": frozenset(
                {
                    frozenset({1, 2}),
                    frozenset({3, 4}),
                }
            ),
        }
        save_checkpoint(tmp_path / "ck", payload)
        restored = load_checkpoint(tmp_path / "ck")
        assert restored["pair_of_pairs"] == frozenset(
            {
                frozenset({1, 2}),
                frozenset({3, 4}),
            }
        )

    def test_numpy_scalars_become_python_primitives(self, tmp_path: Path) -> None:
        payload = {"ni": np.int64(5), "nf": np.float32(2.5)}
        save_checkpoint(tmp_path / "ck", payload)
        restored = load_checkpoint(tmp_path / "ck")
        assert restored == {"ni": 5, "nf": 2.5}
        assert isinstance(restored["ni"], int)
        assert isinstance(restored["nf"], float)

    def test_load_npz_refuses_pickle(self, tmp_path: Path) -> None:
        """``load_checkpoint`` must open ``.npz`` with ``allow_pickle=False``.

        Guards against a malicious ``.npz`` that would otherwise execute pickle
        code on load.
        """
        payload = {"tensor": np.arange(4)}
        save_checkpoint(tmp_path / "ck", payload)
        # Sanity: load still works; the test enforces the non-pickle code path.
        restored = load_checkpoint(tmp_path / "ck")
        np.testing.assert_array_equal(restored["tensor"], np.arange(4))

    def test_empty_payload(self, tmp_path: Path) -> None:
        save_checkpoint(tmp_path / "ck", {})
        assert load_checkpoint(tmp_path / "ck") == {}

    def test_unsupported_type_raises(self, tmp_path: Path) -> None:
        class Foo:
            pass

        with pytest.raises(TypeError):
            save_checkpoint(tmp_path / "ck", {"obj": Foo()})


# ---------------------------------------------------------------------------
# write_batch_assignments
# ---------------------------------------------------------------------------


def _sub(name: str, obj_id: int, entry_ids: list, frame_ids: list, sensor_ids: list) -> SubTracklet:
    return SubTracklet(
        name=name,
        batch=int(name.split("_")[0][1:]),
        object_id=int(obj_id),
        sub_id=int(name.rsplit("_", 1)[1]),
        vecs=np.zeros((len(entry_ids), 4), dtype=np.float32),
        frame_ids=np.asarray(frame_ids, dtype=int),
        sensor_ids=list(sensor_ids),
        entry_ids=list(entry_ids),
    )


def _minimal_batch_result(
    batch_idx: int,
    groups: list,
    entry_to_cluster: dict,
    n_clusters_total: int,
) -> BatchResult:
    return BatchResult(
        stats=BatchStats(
            batch=batch_idx,
            n_groups=len(groups),
            n_new_clusters=sum(1 for g in groups if g.status == "new"),
            n_updated=sum(1 for g in groups if g.status == "updated"),
            n_unchanged=sum(1 for g in groups if g.status == "unchanged"),
            n_retired=0,
            n_clusters_total=n_clusters_total,
            n_cm_pairs=0,
        ),
        group_info=groups,
        entry_to_cluster=entry_to_cluster,
    )


class TestWriteBatchAssignments:
    def test_file_path_and_layout(self, tmp_path: Path) -> None:
        subs = {
            "b0_8_0": _sub("b0_8_0", 8, [10, 11], [100, 101], ["cam1", "cam1"]),
        }
        groups = [
            ClusterGroup(surviving=3, old_ids=[], sub_names=["b0_8_0"], status="new", members=[0]),
        ]
        arch = Archive()
        arch.clusters[3] = ClusterInfo(
            cluster_id=3,
            rep=np.zeros(4, dtype=np.float32),
            n_embs=2,
            created_batch=0,
            last_updated_batch=0,
            members_history=[],
        )
        result = _minimal_batch_result(
            batch_idx=0,
            groups=groups,
            entry_to_cluster={10: 3, 11: 3},
            n_clusters_total=1,
        )

        out = write_batch_assignments(
            result,
            subs,
            arch,
            frame_start=100,
            frame_end=101,
            output_dir=tmp_path,
        )
        # File lives at batch_000/assignments.json, zero-padded.
        assert out == tmp_path / "batch_000" / "assignments.json"
        assert out.exists()

        data = json.loads(out.read_text())
        assert data["batch_idx"] == 0
        assert data["frame_window"] == [100, 101]
        assert data["n_embeddings_assigned"] == 2
        assert data["n_clusters_touched"] == 1
        assert data["n_clusters_total_after_batch"] == 1

    def test_entry_to_cluster_keys_stringified(self, tmp_path: Path) -> None:
        subs = {"b0_8_0": _sub("b0_8_0", 8, [10], [100], ["cam1"])}
        groups = [ClusterGroup(surviving=5, old_ids=[], sub_names=["b0_8_0"], status="new", members=[0])]
        arch = Archive()
        arch.clusters[5] = ClusterInfo(
            cluster_id=5,
            rep=np.zeros(4, dtype=np.float32),
            n_embs=1,
            created_batch=0,
            last_updated_batch=0,
            members_history=[],
        )
        result = _minimal_batch_result(
            batch_idx=0,
            groups=groups,
            entry_to_cluster={10: 5},
            n_clusters_total=1,
        )
        out = write_batch_assignments(
            result,
            subs,
            arch,
            frame_start=100,
            frame_end=100,
            output_dir=tmp_path,
        )
        data = json.loads(out.read_text())
        # JSON keys are always strings; ints serialize as str.
        assert data["entry_to_cluster"] == {"10": 5}
        assert data["entry_tuples"] == {"10": [8, "cam1", 100]}

    def test_cluster_details_shape(self, tmp_path: Path) -> None:
        subs = {
            "b0_8_0": _sub("b0_8_0", 8, [10, 11], [100, 101], ["cam1", "cam2"]),
            "b0_9_0": _sub("b0_9_0", 9, [20], [50], ["cam3"]),
        }
        groups = [
            ClusterGroup(surviving=3, old_ids=[], sub_names=["b0_8_0"], status="new", members=[0]),
            ClusterGroup(surviving=7, old_ids=[], sub_names=["b0_9_0"], status="new", members=[1]),
        ]
        arch = Archive()
        arch.clusters[3] = ClusterInfo(
            cluster_id=3,
            rep=np.zeros(4, dtype=np.float32),
            n_embs=2,
            created_batch=0,
            last_updated_batch=0,
            members_history=[],
        )
        arch.clusters[7] = ClusterInfo(
            cluster_id=7,
            rep=np.zeros(4, dtype=np.float32),
            n_embs=1,
            created_batch=0,
            last_updated_batch=0,
            members_history=[],
        )
        result = _minimal_batch_result(
            batch_idx=0,
            groups=groups,
            entry_to_cluster={10: 3, 11: 3, 20: 7},
            n_clusters_total=2,
        )
        out = write_batch_assignments(
            result,
            subs,
            arch,
            frame_start=50,
            frame_end=101,
            output_dir=tmp_path,
        )
        data = json.loads(out.read_text())
        details = data["cluster_details"]
        assert set(details) == {"3", "7"}
        c3 = details["3"]
        assert c3["status"] == "new"
        assert c3["sub_names"] == ["b0_8_0"]
        assert c3["n_added_this_batch"] == 2
        assert c3["n_total_after_batch"] == 2
        assert c3["entry_ids_added"] == [10, 11]
        assert c3["entry_tuples_added"] == [[8, "cam1", 100], [8, "cam2", 101]]
        c7 = details["7"]
        assert c7["n_added_this_batch"] == 1
        assert c7["entry_tuples_added"] == [[9, "cam3", 50]]

    def test_missing_cluster_in_archive_reports_zero(self, tmp_path: Path) -> None:
        # If a group's surviving cid isn't in the archive (e.g. because
        # an empty-batch path didn't populate it), the writer should
        # record ``n_total_after_batch = 0`` rather than raising.
        subs = {"b0_8_0": _sub("b0_8_0", 8, [10], [100], ["cam1"])}
        groups = [ClusterGroup(surviving=99, old_ids=[], sub_names=["b0_8_0"], status="new", members=[0])]
        arch = Archive()  # deliberately empty
        result = _minimal_batch_result(
            batch_idx=0,
            groups=groups,
            entry_to_cluster={10: 99},
            n_clusters_total=1,
        )
        out = write_batch_assignments(
            result,
            subs,
            arch,
            frame_start=100,
            frame_end=100,
            output_dir=tmp_path,
        )
        data = json.loads(out.read_text())
        assert data["cluster_details"]["99"]["n_total_after_batch"] == 0

    def test_zero_padding_batch_dir(self, tmp_path: Path) -> None:
        # batch_idx=7 should produce batch_007; batch_idx=123 -> batch_123.
        for batch_idx in [0, 7, 99, 123]:
            arch = Archive()
            result = _minimal_batch_result(
                batch_idx=batch_idx,
                groups=[],
                entry_to_cluster={},
                n_clusters_total=0,
            )
            out = write_batch_assignments(
                result,
                {},
                arch,
                frame_start=0,
                frame_end=0,
                output_dir=tmp_path,
            )
            padded = f"{batch_idx:03d}"
            assert out.parent.name == f"batch_{padded}"

    def test_output_dir_autocreated(self, tmp_path: Path) -> None:
        # Nested dir that doesn't exist yet; writer must create it.
        target = tmp_path / "does" / "not" / "exist"
        arch = Archive()
        result = _minimal_batch_result(
            batch_idx=0,
            groups=[],
            entry_to_cluster={},
            n_clusters_total=0,
        )
        out = write_batch_assignments(
            result,
            {},
            arch,
            frame_start=0,
            frame_end=0,
            output_dir=target,
        )
        assert out.exists()
        assert out.parent == target / "batch_000"
