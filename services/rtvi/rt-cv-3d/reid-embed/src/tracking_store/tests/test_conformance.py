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

"""Backend-agnostic conformance suite for :class:`TrackingStore`.

Every backend listed in ``conftest.STORE_FACTORIES`` must pass every
test here. When the SQLite/Redis backends land, they are registered in
``conftest.py`` and these tests rerun automatically.

The tests run **single-process** (no spawned children) so they
exercise the owner-side code paths of every backend. Backend-specific
cross-process behaviour is covered in ``test_memory.py`` etc.
"""

from __future__ import annotations


# ---------------------------------------------------------------------------
# insert / lookup / get_*
# ---------------------------------------------------------------------------


def test_insert_then_lookup(store, obj_factory):
    obj = obj_factory("42", "camA", 100)
    store.insert(obj)

    hit = store.lookup("42", "camA", 100)
    assert hit is not None
    assert hit.object_id == "42"
    assert hit.stream_id == "camA"
    assert hit.frame_id == 100


def test_lookup_missing_returns_none(store):
    assert store.lookup("no-such", "no-cam", 0) is None


def test_lookup_anywhere_matches_lookup_for_owner_caller(store, obj_factory):
    """Owner-side lookup_anywhere() must match plain lookup() exactly.

    The cross-process RPC path is exercised in backend-specific tests
    (``test_memory.py::test_lookup_anywhere_from_child_process``);
    here we just verify the in-process happy path so every backend
    confirms the contract.
    """
    store.insert(obj_factory("42", "camA", 100, confidence=0.7))
    direct = store.lookup("42", "camA", 100)
    anywhere = store.lookup_anywhere("42", "camA", 100)
    assert direct is not None
    assert anywhere is not None
    assert (anywhere.object_id, anywhere.stream_id, anywhere.frame_id) == (
        direct.object_id,
        direct.stream_id,
        direct.frame_id,
    )
    assert anywhere.confidence == direct.confidence
    assert store.lookup_anywhere("missing", "cam", 1) is None


def test_insert_many_then_get_by_object(store, obj_factory):
    store.insert_many(
        [
            obj_factory("42", "camA", 100),
            obj_factory("42", "camA", 105),
            obj_factory("42", "camB", 102),
            obj_factory("43", "camA", 200),
        ]
    )

    by_obj = store.get_by_object("42")
    assert set(by_obj.keys()) == {"camA", "camB"}
    assert [o.frame_id for o in by_obj["camA"]] == [100, 105]
    assert [o.frame_id for o in by_obj["camB"]] == [102]

    by_obj_stream = store.get_by_object("42", "camA")
    assert list(by_obj_stream.keys()) == ["camA"]
    assert [o.frame_id for o in by_obj_stream["camA"]] == [100, 105]

    assert store.get_by_object("missing") == {}


def test_get_by_object_sorts_by_frame_id(store, obj_factory):
    store.insert(obj_factory("1", "cam", 30))
    store.insert(obj_factory("1", "cam", 10))
    store.insert(obj_factory("1", "cam", 20))

    by_obj = store.get_by_object("1")
    assert [o.frame_id for o in by_obj["cam"]] == [10, 20, 30]


def test_get_all_returns_everything_sorted(store, obj_factory):
    store.insert_many(
        [
            obj_factory("1", "cam", 30),
            obj_factory("1", "cam", 10),
            obj_factory("2", "cam", 5),
        ]
    )

    snap = store.get_all()
    assert set(snap.keys()) == {"1", "2"}
    assert [o.frame_id for o in snap["1"]["cam"]] == [10, 30]
    assert [o.frame_id for o in snap["2"]["cam"]] == [5]


def test_snapshot_matches_get_all(store, obj_factory):
    store.insert_many(
        [
            obj_factory("1", "cam", 1),
            obj_factory("2", "cam", 2),
        ]
    )
    assert store.snapshot() == store.get_all()


# ---------------------------------------------------------------------------
# delete_below
# ---------------------------------------------------------------------------


def test_delete_below_drops_older_frames(store, obj_factory):
    store.insert_many(
        [
            obj_factory("1", "camA", 10),
            obj_factory("1", "camA", 20),
            obj_factory("1", "camA", 30),
            obj_factory("1", "camB", 5),
            obj_factory("2", "camA", 15),
        ]
    )

    removed = store.delete_below("camA", 20)
    assert removed == 2  # objs at frame 10 (oid=1) and 15 (oid=2)

    snap = store.get_all()
    assert [o.frame_id for o in snap["1"]["camA"]] == [20, 30]
    assert [o.frame_id for o in snap["1"]["camB"]] == [5]
    # Backends differ on whether an emptied bucket leaves a ghost key
    # behind: the in-memory store rewrites the list in place and ends
    # up with ``snap["2"]["camA"] == []``, while SQL backends just have
    # zero rows for (2, camA) so the key never appears. Either is
    # acceptable; what we actually care about is that no frames remain.
    assert snap.get("2", {}).get("camA", []) == []


def test_delete_below_unknown_sensor_is_noop(store, obj_factory):
    store.insert(obj_factory("1", "camA", 10))
    removed = store.delete_below("no-such-cam", 9999)
    assert removed == 0
    assert store.lookup("1", "camA", 10) is not None


# ---------------------------------------------------------------------------
# clear
# ---------------------------------------------------------------------------


def test_clear_specific_object(store, obj_factory):
    store.insert_many(
        [
            obj_factory("1", "cam", 1),
            obj_factory("2", "cam", 1),
        ]
    )
    store.clear("1")
    assert store.get_by_object("1") == {}
    assert store.get_by_object("2") != {}


def test_clear_all(store, obj_factory):
    store.insert_many(
        [
            obj_factory("1", "cam", 1),
            obj_factory("2", "cam", 1),
        ]
    )
    store.clear()
    assert store.get_all() == {}


# ---------------------------------------------------------------------------
# apply_relabel (clustering)
# ---------------------------------------------------------------------------


def test_apply_relabel_moves_entry_and_sets_clustered_flag(store, obj_factory):
    store.insert_many(
        [
            obj_factory("100", "camA", 5),
            obj_factory("100", "camA", 6),
            obj_factory("200", "camA", 10),
        ]
    )

    moved = store.apply_relabel([("100", "camA", 5, "200")])
    assert moved == 1

    snap = store.get_all()
    # old bucket lost the relabelled frame
    assert [o.frame_id for o in snap["100"]["camA"]] == [6]
    # new bucket gained it, sorted
    assert [o.frame_id for o in snap["200"]["camA"]] == [5, 10]

    relabelled = next(o for o in snap["200"]["camA"] if o.frame_id == 5)
    assert relabelled.is_clustered is True
    assert relabelled.object_id == "200"


def test_apply_relabel_skips_missing_entry(store, obj_factory):
    # A miss is skipped (not fatal); moved reflects only real movement.
    store.insert(obj_factory("1", "cam", 1))
    moved = store.apply_relabel([("does-not-exist", "cam", 1, "1")])
    assert moved == 0
    # The pre-existing entry is untouched.
    hit = store.lookup("1", "cam", 1)
    assert hit is not None


def test_apply_relabel_default_pass_skips_already_clustered_source(store, obj_factory):
    # Fresh-clustering pass (source_clustered=False) only relabels un-clustered
    # rows; an already-clustered source is skipped, not moved.
    store.insert(obj_factory("100", "camA", 5, is_clustered=True))
    store.insert(obj_factory("200", "camA", 10))
    moved = store.apply_relabel([("100", "camA", 5, "200")])
    assert moved == 0
    # The clustered source stays where it was.
    assert store.lookup("100", "camA", 5) is not None


def test_apply_relabel_source_clustered_migrates_clustered_rows(store, obj_factory):
    # Retired-cluster cascade (source_clustered=True) migrates already-clustered
    # rows from a retired rep to the surviving rep.
    store.insert(obj_factory("100", "camA", 5, is_clustered=True))
    moved = store.apply_relabel([("100", "camA", 5, "200")], source_clustered=True)
    assert moved == 1
    assert store.get_by_object("100").get("camA", []) == []
    hit = store.lookup("200", "camA", 5)
    assert hit is not None
    assert hit.object_id == "200"
    assert hit.is_clustered is True


def test_apply_relabel_source_clustered_skips_unclustered_rows(store, obj_factory):
    # With source_clustered=True, an un-clustered source is not eligible.
    store.insert(obj_factory("100", "camA", 5))  # un-clustered
    moved = store.apply_relabel([("100", "camA", 5, "200")], source_clustered=True)
    assert moved == 0
    assert store.lookup("100", "camA", 5) is not None


def test_lookup_prefers_clustered_entry_when_both_exist(store, obj_factory):
    # Relabelling 100 -> 200 at frame 5 leaves the destination with both an
    # un-clustered row (200's own detection) and the folded-in clustered row.
    store.insert(obj_factory("100", "camA", 5, confidence=0.1))
    store.insert(obj_factory("200", "camA", 5, confidence=0.9))
    store.apply_relabel([("100", "camA", 5, "200")])

    hit = store.lookup("200", "camA", 5)
    assert hit is not None
    # The clustered entry wins over the un-clustered fallback.
    assert hit.is_clustered is True


def test_lookup_returns_unclustered_when_no_clustered_entry(store, obj_factory):
    store.insert(obj_factory("7", "camA", 3))
    hit = store.lookup("7", "camA", 3)
    assert hit is not None
    assert hit.is_clustered is False


# ---------------------------------------------------------------------------
# unclustered scans
# ---------------------------------------------------------------------------


def test_unclustered_keys_skips_clustered_entries(store, obj_factory):
    store.insert_many(
        [
            obj_factory("1", "camA", 10),
            obj_factory("1", "camA", 11, is_clustered=True),
            obj_factory("2", "camB", 20),
        ]
    )

    keys = store.unclustered_keys()
    assert (1, "camA", 10) in keys
    assert (2, "camB", 20) in keys
    assert (1, "camA", 11) not in keys


def test_unclustered_keys_skips_non_numeric_object_ids(store, obj_factory):
    store.insert(obj_factory("not-a-number", "cam", 1))
    store.insert(obj_factory("7", "cam", 1))
    keys = store.unclustered_keys()
    assert (7, "cam", 1) in keys
    assert all(isinstance(k[0], int) for k in keys)


def test_unclustered_bboxes_in_frame_range(store, obj_factory):
    store.insert_many(
        [
            obj_factory("1", "cam", 10, leftX=1.0, topY=2.0, rightX=3.0, bottomY=4.0),
            obj_factory("1", "cam", 50, leftX=5.0),
            obj_factory("1", "cam", 100),
            obj_factory("1", "cam", 60, is_clustered=True),  # excluded
        ]
    )

    rows = store.unclustered_bboxes_in_frame_range(10, 60)
    assert (1, "cam", 10) in rows
    assert (1, "cam", 50) in rows
    assert (1, "cam", 100) not in rows
    assert (1, "cam", 60) not in rows  # clustered, excluded

    assert rows[(1, "cam", 10)] == {
        "leftX": 1.0,
        "topY": 2.0,
        "rightX": 3.0,
        "bottomY": 4.0,
    }


# ---------------------------------------------------------------------------
# build_retired_relabels
# ---------------------------------------------------------------------------


def test_build_retired_relabels_emits_one_update_per_clustered_entry(
    store,
    obj_factory,
):
    # Two clusters: cluster 100 retired -> cluster 200 surviving.
    # ``cluster_id_to_oid`` is a simple stringifier in this test.
    store.insert_many(
        [
            obj_factory("100", "camA", 1, is_clustered=True),
            obj_factory("100", "camA", 2, is_clustered=True),
            obj_factory("100", "camB", 5),  # unclustered -> excluded
            obj_factory("200", "camA", 10, is_clustered=True),
        ]
    )

    updates = store.build_retired_relabels(
        retired_to_surviving={100: 200},
        cluster_id_to_oid=lambda cid: str(cid),
    )

    # The two clustered entries from old bucket "100" migrate to "200";
    # the unclustered one is left for the next clustering tick.
    moved_frames = sorted(u[2] for u in updates if u[0] == "100" and u[3] == "200")
    assert moved_frames == [1, 2]


def test_build_retired_relabels_skips_self_merges(store, obj_factory):
    store.insert(obj_factory("100", "cam", 1))
    # Same id on both sides should be a no-op rather than a self-move.
    updates = store.build_retired_relabels(
        retired_to_surviving={100: 100},
        cluster_id_to_oid=lambda cid: str(cid),
    )
    assert updates == []


# ---------------------------------------------------------------------------
# stats
# ---------------------------------------------------------------------------


def test_stats_counts_objects_and_streams(store, obj_factory):
    store.insert_many(
        [
            obj_factory("1", "camA", 1),
            obj_factory("1", "camB", 1),
            obj_factory("2", "camA", 1),
            obj_factory("2", "camA", 2),
        ]
    )

    stats = store.stats()
    assert stats["total_objects"] == 2
    assert stats["total_streams"] == 3  # (1,A), (1,B), (2,A)
    # Most backends will also report total_entries; if they do, it
    # should be 4. (Optional key, so guarded.)
    if "total_entries" in stats:
        assert stats["total_entries"] == 4
