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

"""Unit tests for the compression-labeling helpers.

These cover the pure-Python partitioning and relabel logic that
``_perform_compression`` now relies on, so we don't need a live Milvus
to catch regressions.

Updated for the rep-based labeling: ``cluster_id_to_object_id(cid,
archive)`` returns the cluster's representative tracker_id as a
digit-string sourced from ``archive.rep_object_id``. The "c"-prefix
convention is gone; tests previously asserting ``"c{cid}"`` now use
the rep value supplied via the test FakeArchive.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Set

from cluster_manager.compression_labeling import (
    DISPOSITION_DEFER_UNSEEN,
    DISPOSITION_PROCESS,
    DISPOSITION_SKIP_FILTERED,
    apply_compressed_relabel,
    classify_row,
    cluster_id_to_object_id,
    entry_to_cluster_map_from_archive,
    partition_rows,
    retired_to_surviving_from_groups,
)


# ---------------------------------------------------------------------------
# Stand-in dataclasses shaped like the real Archive / ClusterGroup bits we use.
# Keeps tests independent of numpy / cluster_manager imports.
# ---------------------------------------------------------------------------


@dataclass
class FakeHistory:
    entry_ids: List[int]


@dataclass
class FakeClusterInfo:
    members_history: List[FakeHistory]


@dataclass
class FakeArchive:
    clusters: Dict[int, FakeClusterInfo] = field(default_factory=dict)
    seen_entry_ids: Set[int] = field(default_factory=set)
    rep_object_id: Dict[int, int] = field(default_factory=dict)
    min_frame_id: Dict[int, int] = field(default_factory=dict)


@dataclass
class FakeGroup:
    surviving: int
    old_ids: List[int]


def _archive_with(clusters: Dict[int, List[List[int]]], reps: Dict[int, int] | None = None) -> FakeArchive:
    """Tiny factory: ``{cid: [[entry_ids...], ...]}`` -> FakeArchive.

    ``reps`` supplies the cluster_id -> rep_object_id mapping that
    cluster_id_to_object_id resolves through. When omitted, defaults to
    ``cid -> cid * 100`` so each test cluster has a distinct rep.
    """
    if reps is None:
        reps = {cid: cid * 100 for cid in clusters}
    return FakeArchive(
        clusters={
            cid: FakeClusterInfo(members_history=[FakeHistory(entry_ids=list(eids)) for eids in histories])
            for cid, histories in clusters.items()
        },
        rep_object_id=dict(reps),
    )


# ---------------------------------------------------------------------------
# entry_to_cluster_map_from_archive
# ---------------------------------------------------------------------------


class TestEntryToClusterMap:
    def test_empty_archive(self) -> None:
        assert entry_to_cluster_map_from_archive(FakeArchive()) == {}

    def test_single_cluster_single_history(self) -> None:
        arch = _archive_with({7: [[1, 2, 3]]})
        assert entry_to_cluster_map_from_archive(arch) == {1: 7, 2: 7, 3: 7}

    def test_multiple_clusters_and_histories(self) -> None:
        arch = _archive_with({0: [[10, 11], [12]], 5: [[20]]})
        assert entry_to_cluster_map_from_archive(arch) == {
            10: 0,
            11: 0,
            12: 0,
            20: 5,
        }

    def test_later_history_overrides_same_entry(self) -> None:
        # An entry_id shouldn't really appear in two clusters, but if it did
        # (e.g., data corruption), the last cluster wins. This is a contract
        # check, not an endorsement.
        arch = _archive_with({1: [[100]], 2: [[100]]})
        mapping = entry_to_cluster_map_from_archive(arch)
        assert mapping[100] in {1, 2}


# ---------------------------------------------------------------------------
# retired_to_surviving_from_groups
# ---------------------------------------------------------------------------


class TestRetiredToSurviving:
    def test_no_groups(self) -> None:
        assert retired_to_surviving_from_groups([]) == {}

    def test_group_with_no_old_ids_emits_nothing(self) -> None:
        # A brand-new cluster from a single sub-tracklet has old_ids=[].
        groups = [FakeGroup(surviving=42, old_ids=[])]
        assert retired_to_surviving_from_groups(groups) == {}

    def test_unchanged_group_emits_nothing(self) -> None:
        # Old cluster that survives untouched (surviving == its only old_id).
        groups = [FakeGroup(surviving=3, old_ids=[3])]
        assert retired_to_surviving_from_groups(groups) == {}

    def test_two_cluster_merge(self) -> None:
        groups = [FakeGroup(surviving=3, old_ids=[3, 7])]
        assert retired_to_surviving_from_groups(groups) == {7: 3}

    def test_transitive_three_way_merge(self) -> None:
        # A, B, D all land in one Stage-4 group; surviving = min.
        groups = [FakeGroup(surviving=1, old_ids=[1, 2, 5])]
        assert retired_to_surviving_from_groups(groups) == {2: 1, 5: 1}

    def test_multiple_independent_merges(self) -> None:
        groups = [
            FakeGroup(surviving=1, old_ids=[1, 4]),
            FakeGroup(surviving=2, old_ids=[2, 9]),
            FakeGroup(surviving=10, old_ids=[]),  # new cluster, no retirement
        ]
        assert retired_to_surviving_from_groups(groups) == {4: 1, 9: 2}


# ---------------------------------------------------------------------------
# classify_row
# ---------------------------------------------------------------------------


class TestClassifyRow:
    def test_clustering_disabled_uses_raw_object_id(self) -> None:
        eff, disp = classify_row(
            entry_id=42,
            raw_object_id="10007",
            entry_to_cluster={42: 1},  # would map if enabled
            seen_entry_ids={42},  # would be filtered if seen-not-mapped
            clustering_enabled=False,
        )
        assert disp == DISPOSITION_PROCESS
        assert eff == "10007"

    def test_clustered_entry_uses_rep_string(self) -> None:
        # cluster 3 has rep tracker_id 555 in the archive
        archive = FakeArchive(rep_object_id={3: 555})
        eff, disp = classify_row(
            entry_id=42,
            raw_object_id="10007",
            entry_to_cluster={42: 3},
            seen_entry_ids={42},
            clustering_enabled=True,
            archive=archive,
        )
        assert disp == DISPOSITION_PROCESS
        assert eff == "555"

    def test_seen_but_not_clustered_is_filtered(self) -> None:
        archive = FakeArchive()
        eff, disp = classify_row(
            entry_id=99,
            raw_object_id="10007",
            entry_to_cluster={},  # not clustered
            seen_entry_ids={99},  # but seen (filtered by Stage 2A)
            clustering_enabled=True,
            archive=archive,
        )
        assert disp == DISPOSITION_SKIP_FILTERED
        assert eff is None

    def test_unseen_is_deferred(self) -> None:
        archive = FakeArchive()
        eff, disp = classify_row(
            entry_id=500,
            raw_object_id="10007",
            entry_to_cluster={},
            seen_entry_ids=set(),  # clustering hasn't looked at it
            clustering_enabled=True,
            archive=archive,
        )
        assert disp == DISPOSITION_DEFER_UNSEEN
        assert eff is None


# ---------------------------------------------------------------------------
# partition_rows
# ---------------------------------------------------------------------------


def _row(entry_id: int, object_id: str) -> dict:
    # Shape matches what _perform_compression reads from Milvus.
    return {
        "entry_id": entry_id,
        "object_id": object_id,
        "sensor_id": f"cam_{object_id}",
        "frame_id": 100 + entry_id,
        "vector": [0.0, 1.0, 0.0],
    }


class TestPartitionRows:
    def test_clustering_disabled_passthrough(self) -> None:
        rows = [_row(1, "a"), _row(2, "b"), _row(3, "a")]
        keep, filtered, deferred = partition_rows(
            rows,
            entry_to_cluster={1: 99},  # ignored when disabled
            seen_entry_ids={1, 2},  # ignored when disabled
            clustering_enabled=False,
        )
        assert filtered == [] and deferred == []
        assert [r["entry_id"] for r in keep] == [1, 2, 3]
        assert [r["object_id"] for r in keep] == ["a", "b", "a"]  # untouched

    def test_three_way_partition(self) -> None:
        # cluster 3 -> rep "555", cluster 7 -> rep "777"
        archive = FakeArchive(rep_object_id={3: 555, 7: 777})
        rows = [
            _row(1, "raw1"),  # clustered -> "555"
            _row(2, "raw2"),  # clustered -> "555"
            _row(3, "raw3"),  # filtered
            _row(4, "raw4"),  # unseen -> deferred
            _row(5, "raw5"),  # clustered -> "777"
        ]
        keep, filtered, deferred = partition_rows(
            rows,
            entry_to_cluster={1: 3, 2: 3, 5: 7},
            seen_entry_ids={1, 2, 3, 5},  # 4 is NOT seen
            clustering_enabled=True,
            archive=archive,
        )
        assert [r["entry_id"] for r in keep] == [1, 2, 5]
        assert [r["object_id"] for r in keep] == ["555", "555", "777"]
        assert filtered == [3]
        assert deferred == [4]

    def test_mutates_object_id_in_place(self) -> None:
        archive = FakeArchive(rep_object_id={42: 1234})
        row = _row(1, "raw-original")
        keep, _, _ = partition_rows(
            [row],
            entry_to_cluster={1: 42},
            seen_entry_ids={1},
            clustering_enabled=True,
            archive=archive,
        )
        assert keep[0] is row  # same object
        assert row["object_id"] == "1234"

    def test_empty_input(self) -> None:
        keep, filtered, deferred = partition_rows(
            [],
            entry_to_cluster={},
            seen_entry_ids=set(),
            clustering_enabled=True,
            archive=FakeArchive(),
        )
        assert keep == [] and filtered == [] and deferred == []


# ---------------------------------------------------------------------------
# apply_compressed_relabel
# ---------------------------------------------------------------------------


def _citem(entry_id: int, object_id: str, *, weight: int = 1) -> dict:
    return {
        "entry_id": entry_id,
        "object_id": object_id,
        "sensor_id": "cam1",
        "frame_id": 100 + entry_id,
        "weight": weight,
        "vector": [0.0, 1.0, 0.0],
    }


class TestApplyCompressedRelabel:
    def test_no_retirements_is_noop(self) -> None:
        archive = FakeArchive(rep_object_id={1: 100, 2: 200})
        state = {"100": [_citem(10, "100")], "200": [_citem(20, "200")]}
        before = {k: list(v) for k, v in state.items()}
        moved = apply_compressed_relabel(state, {}, archive)
        assert moved == 0
        assert state == before

    def test_retired_key_missing_is_skipped(self) -> None:
        # retired cid 9 doesn't exist in state -- shouldn't error, just skip.
        archive = FakeArchive(rep_object_id={1: 100, 9: 900})
        state = {"100": [_citem(10, "100")]}
        moved = apply_compressed_relabel(state, {9: 1}, archive)
        assert moved == 0
        assert set(state.keys()) == {"100"}

    def test_simple_merge_moves_items_and_updates_object_id(self) -> None:
        # cluster 7 (rep 777) merges into cluster 3 (rep 333).
        archive = FakeArchive(rep_object_id={3: 333, 7: 777})
        state = {"777": [_citem(70, "777"), _citem(71, "777")]}
        moved = apply_compressed_relabel(state, {7: 3}, archive)
        assert moved == 2
        assert "777" not in state
        assert len(state["333"]) == 2
        assert all(item["object_id"] == "333" for item in state["333"])

    def test_merge_into_existing_surviving_key_appends(self) -> None:
        archive = FakeArchive(rep_object_id={3: 333, 7: 777})
        state = {"333": [_citem(30, "333")], "777": [_citem(70, "777")]}
        moved = apply_compressed_relabel(state, {7: 3}, archive)
        assert moved == 1
        assert "777" not in state
        # Appended; both now carry rep "333".
        assert [item["entry_id"] for item in state["333"]] == [30, 70]
        assert all(item["object_id"] == "333" for item in state["333"])

    def test_multiple_retirements_converging_on_same_surviving(self) -> None:
        # Transitive merge (user's A,D,B example): retire 7 and 9 into 1.
        archive = FakeArchive(rep_object_id={1: 100, 7: 700, 9: 900})
        state = {
            "100": [_citem(10, "100")],
            "700": [_citem(70, "700")],
            "900": [_citem(90, "900"), _citem(91, "900")],
        }
        moved = apply_compressed_relabel(state, {7: 1, 9: 1}, archive)
        assert moved == 3
        assert set(state.keys()) == {"100"}
        assert [item["entry_id"] for item in state["100"]] == [10, 70, 90, 91]
        assert all(item["object_id"] == "100" for item in state["100"])


# ---------------------------------------------------------------------------
# cluster_id_to_object_id
# ---------------------------------------------------------------------------


class TestClusterIdToObjectId:
    def test_returns_digit_string(self) -> None:
        archive = FakeArchive(rep_object_id={5: 42})
        result = cluster_id_to_object_id(5, archive)
        assert result == "42"
        assert result.isdigit()

    def test_no_c_prefix(self) -> None:
        # Explicit guard against accidentally bringing back the c-prefix.
        archive = FakeArchive(rep_object_id={3: 7})
        assert cluster_id_to_object_id(3, archive) == "7"

    def test_rep_can_be_large_uint64(self) -> None:
        # uint64 TargetIds up to 2^63 - 1 should round-trip cleanly.
        big = (1 << 63) - 1
        archive = FakeArchive(rep_object_id={1: big})
        assert cluster_id_to_object_id(1, archive) == str(big)

    def test_coerces_int_input(self) -> None:
        archive = FakeArchive(rep_object_id={1: 9, 42: 100})
        assert cluster_id_to_object_id(True, archive) == "9"  # int(True) == 1
        assert cluster_id_to_object_id(42, archive) == "100"

    def test_unknown_cluster_id_raises_keyerror(self) -> None:
        archive = FakeArchive(rep_object_id={1: 100})
        try:
            cluster_id_to_object_id(99, archive)
        except KeyError:
            pass
        else:
            assert False, "expected KeyError for unknown cluster_id"
