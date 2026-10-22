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

"""Tests for :mod:`cluster_manager.tracking_relabel`."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any, Dict, List

import pytest

from cluster_manager.tracking_relabel import (
    apply_tracking_relabel,
    build_retired_cluster_tracking_updates,
)


@dataclass
class _FakeArchive:
    """Minimal archive stand-in: only ``rep_object_id`` is touched by
    ``build_retired_cluster_tracking_updates`` (via
    ``cluster_id_to_object_id``).
    """

    rep_object_id: Dict[int, int] = field(default_factory=dict)


def _tobj(object_id: str, frame_id: int, **kw: Any) -> SimpleNamespace:
    """Minimal ``TrackedObject`` stand-in: whatever has mutable
    ``object_id`` and ``is_clustered`` and readable ``frame_id`` works
    with ``apply_tracking_relabel``. Tests use ``SimpleNamespace`` so we
    don't depend on the service's pydantic model. ``is_clustered``
    defaults to False (matches the production model's default on
    insert).
    """
    kw.setdefault("is_clustered", False)
    return SimpleNamespace(object_id=object_id, frame_id=frame_id, **kw)


def _ctobj(object_id: str, frame_id: int, **kw: Any) -> SimpleNamespace:
    """Clustered ``_tobj``: retired-cascade rows are the merge's already-
    clustered members, so these are the ones that migrate.
    """
    kw.setdefault("is_clustered", True)
    return _tobj(object_id, frame_id, **kw)


class TestApplyTrackingRelabel:
    def test_empty_updates_is_noop(self) -> None:
        db: Dict[str, Dict[str, List[Any]]] = {"8": {"cam1": [_tobj("8", 100)]}}
        before = {k: {s: list(v) for s, v in streams.items()} for k, streams in db.items()}
        moved = apply_tracking_relabel(db, threading.Lock(), [])
        assert moved == 0
        assert db == before

    def test_simple_single_move(self) -> None:
        t = _tobj("8", 100)
        db: Dict[str, Dict[str, List[Any]]] = {"8": {"cam1": [t]}}
        moved = apply_tracking_relabel(db, threading.Lock(), [("8", "cam1", 100, "c5")])
        assert moved == 1
        assert db["8"]["cam1"] == []
        assert db["c5"]["cam1"] == [t]
        assert t.object_id == "c5"
        assert t.frame_id == 100  # unchanged
        assert t.is_clustered is True  # marked on move

    def test_moved_entry_is_marked_clustered(self) -> None:
        """Per-entry is_clustered flag is set on every moved object so
        coverage / per-batch tracking filters can tell apart cluster-
        relabeled entries from fresh raw-tracker arrivals when the bucket
        key collides with a cluster rep id.
        """
        moved_obj = _tobj("8", 100)
        kept_obj = _tobj("8", 101)
        db: Dict[str, Dict[str, List[Any]]] = {"8": {"cam1": [moved_obj, kept_obj]}}
        apply_tracking_relabel(db, threading.Lock(), [("8", "cam1", 100, "c5")])
        # The relabeled object carries the flag; the unaffected one does not.
        assert moved_obj.is_clustered is True
        assert kept_obj.is_clustered is False

    def test_multiple_frames_from_same_bucket(self) -> None:
        db: Dict[str, Dict[str, List[Any]]] = {"8": {"cam1": [_tobj("8", 100), _tobj("8", 101), _tobj("8", 102)]}}
        updates = [
            ("8", "cam1", 100, "c5"),
            ("8", "cam1", 102, "c5"),
        ]
        moved = apply_tracking_relabel(db, threading.Lock(), updates)
        assert moved == 2
        # Frame 101 stays behind
        assert [t.frame_id for t in db["8"]["cam1"]] == [101]
        assert [t.frame_id for t in db["c5"]["cam1"]] == [100, 102]
        assert all(t.object_id == "c5" for t in db["c5"]["cam1"])
        assert db["8"]["cam1"][0].object_id == "8"

    def test_bucket_scanned_once_even_with_many_updates(self) -> None:
        # Eight frames under one (old_oid, stream) -> all routed to
        # the same new_oid. The function should batch them so the
        # list is walked once.
        db: Dict[str, Dict[str, List[Any]]] = {"8": {"cam1": [_tobj("8", f) for f in range(8)]}}
        updates = [("8", "cam1", f, "c5") for f in range(0, 8, 2)]
        moved = apply_tracking_relabel(db, threading.Lock(), updates)
        assert moved == 4
        assert sorted(t.frame_id for t in db["8"]["cam1"]) == [1, 3, 5, 7]
        assert sorted(t.frame_id for t in db["c5"]["cam1"]) == [0, 2, 4, 6]

    def test_different_streams_isolated(self) -> None:
        db: Dict[str, Dict[str, List[Any]]] = {
            "8": {
                "cam1": [_tobj("8", 100)],
                "cam2": [_tobj("8", 100)],  # same frame, different stream
            }
        }
        moved = apply_tracking_relabel(db, threading.Lock(), [("8", "cam1", 100, "c5")])
        assert moved == 1
        assert db["8"]["cam1"] == []
        assert len(db["8"]["cam2"]) == 1  # untouched
        assert db["c5"]["cam1"][0].object_id == "c5"
        assert db["8"]["cam2"][0].object_id == "8"

    def test_unknown_old_oid_skipped_silently(self) -> None:
        db: Dict[str, Dict[str, List[Any]]] = {"8": {"cam1": [_tobj("8", 100)]}}
        moved = apply_tracking_relabel(
            db,
            threading.Lock(),
            [("99", "cam1", 100, "c5")],  # "99" doesn't exist
        )
        assert moved == 0
        assert db == {"8": {"cam1": [db["8"]["cam1"][0]]}}

    def test_unknown_stream_skipped_silently(self) -> None:
        db: Dict[str, Dict[str, List[Any]]] = {"8": {"cam1": [_tobj("8", 100)]}}
        moved = apply_tracking_relabel(
            db,
            threading.Lock(),
            [("8", "cam_missing", 100, "c5")],
        )
        assert moved == 0
        assert list(db["8"]) == ["cam1"]

    def test_unknown_frame_skipped_silently(self) -> None:
        db: Dict[str, Dict[str, List[Any]]] = {"8": {"cam1": [_tobj("8", 100)]}}
        moved = apply_tracking_relabel(
            db,
            threading.Lock(),
            [("8", "cam1", 999, "c5")],
        )
        assert moved == 0
        # Object stayed put, no c5 bucket created.
        assert db["8"]["cam1"][0].frame_id == 100
        assert "c5" not in db

    def test_target_bucket_preexists_and_gets_appended(self) -> None:
        db: Dict[str, Dict[str, List[Any]]] = {
            "8": {"cam1": [_tobj("8", 100)]},
            "c5": {"cam1": [_tobj("c5", 50)]},  # preexisting target
        }
        moved = apply_tracking_relabel(db, threading.Lock(), [("8", "cam1", 100, "c5")])
        assert moved == 1
        # c5 bucket had one entry; after move, has two.
        assert [t.frame_id for t in db["c5"]["cam1"]] == [50, 100]

    def test_idempotent_under_repeated_invocation(self) -> None:
        # Second apply with the same updates should be a no-op
        # (frame 100 no longer in bucket "8" after the first move).
        db: Dict[str, Dict[str, List[Any]]] = {"8": {"cam1": [_tobj("8", 100)]}}
        moved_first = apply_tracking_relabel(db, threading.Lock(), [("8", "cam1", 100, "c5")])
        moved_second = apply_tracking_relabel(db, threading.Lock(), [("8", "cam1", 100, "c5")])
        assert moved_first == 1
        assert moved_second == 0
        assert len(db["c5"]["cam1"]) == 1

    def test_accepts_any_lock_like(self) -> None:
        # threading.RLock works interchangeably with threading.Lock.
        db: Dict[str, Dict[str, List[Any]]] = {"8": {"cam1": [_tobj("8", 100)]}}
        moved = apply_tracking_relabel(db, threading.RLock(), [("8", "cam1", 100, "c5")])
        assert moved == 1

    def test_string_coercion_for_update_fields(self) -> None:
        # Even if caller passes int old_oid / new_oid (common mistake),
        # the helper's str() coercion lands them under string keys.
        db: Dict[str, Dict[str, List[Any]]] = {"8": {"cam1": [_tobj("8", 100)]}}
        moved = apply_tracking_relabel(
            db,
            threading.Lock(),
            [(8, "cam1", 100, 5)],  # type: ignore[list-item]
        )
        assert moved == 1
        assert "5" in db  # coerced to "5", not 5
        assert db["5"]["cam1"][0].object_id == "5"

    def test_non_integer_frame_id_logs_warning_and_keeps(self, caplog: pytest.LogCaptureFixture) -> None:
        # A tracking object with a non-integer ``frame_id`` (e.g., from a
        # malformed upstream message) must not crash the relabel. The
        # helper logs a warning, keeps the object in its current bucket,
        # and continues with the well-formed objects.
        good = _tobj("8", 100)
        bad = _tobj("8", "not_an_int")  # type: ignore[arg-type]
        db: Dict[str, Dict[str, List[Any]]] = {"8": {"cam1": [good, bad]}}
        with caplog.at_level("WARNING", logger="cluster_manager.tracking_relabel"):
            moved = apply_tracking_relabel(db, threading.Lock(), [("8", "cam1", 100, "c5")])
        # Only the well-formed frame moved; the malformed one stayed.
        assert moved == 1
        assert [t.object_id for t in db["8"]["cam1"]] == ["8"]
        assert db["8"]["cam1"][0] is bad
        assert db["c5"]["cam1"] == [good]
        # Warning surfaced with enough context to identify the bad entry.
        warnings = [r for r in caplog.records if r.levelname == "WARNING"]
        assert len(warnings) == 1
        msg = warnings[0].getMessage()
        assert "non-integer frame_id" in msg
        assert "not_an_int" in msg

    def test_none_frame_id_handled_same_way(self, caplog: pytest.LogCaptureFixture) -> None:
        # ``None`` triggers ``TypeError`` inside ``int(...)`` rather than
        # ``ValueError``; the same except clause should catch it.
        bad = _tobj("8", None)  # type: ignore[arg-type]
        db: Dict[str, Dict[str, List[Any]]] = {"8": {"cam1": [bad]}}
        with caplog.at_level("WARNING", logger="cluster_manager.tracking_relabel"):
            moved = apply_tracking_relabel(db, threading.Lock(), [("8", "cam1", 100, "c5")])
        assert moved == 0
        assert db["8"]["cam1"] == [bad]  # still in place
        assert any(r.levelname == "WARNING" for r in caplog.records)


class TestBuildRetiredClusterTrackingUpdates:
    # Reps used throughout: cluster 1 -> "100", cluster 2 -> "200",
    # cluster 3 -> "300", cluster 4 -> "400". Digit-string keys reflect
    # the production schema (object_id is a digit-only VARCHAR holding
    # the cluster's representative tracker_id).
    _ARCHIVE = _FakeArchive(rep_object_id={1: 100, 2: 200, 3: 300, 4: 400})

    def test_empty_retired_map_returns_empty(self) -> None:
        db: Dict[str, Dict[str, List[Any]]] = {"100": {"cam1": [_tobj("100", 100)]}}
        updates = build_retired_cluster_tracking_updates(db, threading.Lock(), {}, self._ARCHIVE)
        assert updates == []

    def test_retired_key_missing_in_db(self) -> None:
        # Cluster 1 retired into 2 but no entries under cluster 1's rep ("100") in the DB.
        db: Dict[str, Dict[str, List[Any]]] = {"200": {"cam1": [_tobj("200", 100)]}}
        updates = build_retired_cluster_tracking_updates(db, threading.Lock(), {1: 2}, self._ARCHIVE)
        assert updates == []

    def test_single_retired_single_frame(self) -> None:
        db: Dict[str, Dict[str, List[Any]]] = {"100": {"cam1": [_ctobj("100", 100)]}}
        updates = build_retired_cluster_tracking_updates(db, threading.Lock(), {1: 2}, self._ARCHIVE)
        assert updates == [("100", "cam1", 100, "200")]

    def test_multiple_frames_under_one_retired_key(self) -> None:
        db: Dict[str, Dict[str, List[Any]]] = {
            "100": {"cam1": [_ctobj("100", 100), _ctobj("100", 101), _ctobj("100", 102)]}
        }
        updates = build_retired_cluster_tracking_updates(db, threading.Lock(), {1: 2}, self._ARCHIVE)
        # All three frames migrate; order follows the bucket iteration order.
        assert sorted(updates) == [
            ("100", "cam1", 100, "200"),
            ("100", "cam1", 101, "200"),
            ("100", "cam1", 102, "200"),
        ]

    def test_multiple_streams_under_one_retired_key(self) -> None:
        db: Dict[str, Dict[str, List[Any]]] = {
            "100": {
                "cam1": [_ctobj("100", 100)],
                "cam2": [_ctobj("100", 100), _ctobj("100", 200)],
            }
        }
        updates = build_retired_cluster_tracking_updates(db, threading.Lock(), {1: 2}, self._ARCHIVE)
        assert sorted(updates) == [
            ("100", "cam1", 100, "200"),
            ("100", "cam2", 100, "200"),
            ("100", "cam2", 200, "200"),
        ]

    def test_multiple_retired_clusters(self) -> None:
        db: Dict[str, Dict[str, List[Any]]] = {
            "100": {"cam1": [_ctobj("100", 100)]},
            "300": {"cam1": [_ctobj("300", 200)]},
        }
        updates = build_retired_cluster_tracking_updates(db, threading.Lock(), {1: 2, 3: 4}, self._ARCHIVE)
        assert sorted(updates) == [
            ("100", "cam1", 100, "200"),
            ("300", "cam1", 200, "400"),
        ]

    def test_retired_and_surviving_share_rep_is_skipped(self) -> None:
        # Edge case: cluster 1 (rep=100) merges into cluster 2 but cluster 2's
        # rep also happens to be 100 (e.g. cluster 2 absorbed cluster 1's earlier
        # rep). Nothing to move -- retired and surviving keys are identical.
        archive = _FakeArchive(rep_object_id={1: 100, 2: 100})
        db: Dict[str, Dict[str, List[Any]]] = {"100": {"cam1": [_tobj("100", 100)]}}
        updates = build_retired_cluster_tracking_updates(db, threading.Lock(), {1: 2}, archive)
        assert updates == []

    def test_non_integer_frame_id_skipped(self, caplog: pytest.LogCaptureFixture) -> None:
        bad = _ctobj("100", "not_an_int")  # type: ignore[arg-type]
        good = _ctobj("100", 100)
        db: Dict[str, Dict[str, List[Any]]] = {"100": {"cam1": [bad, good]}}
        with caplog.at_level("WARNING", logger="cluster_manager.tracking_relabel"):
            updates = build_retired_cluster_tracking_updates(db, threading.Lock(), {1: 2}, self._ARCHIVE)
        # Only the good entry survives; bad one is logged + skipped.
        assert updates == [("100", "cam1", 100, "200")]
        assert any(r.levelname == "WARNING" for r in caplog.records)

    def test_round_trip_with_apply_tracking_relabel(self) -> None:
        # The whole point of returning per-frame updates is that
        # ``apply_tracking_relabel`` can consume them directly.
        t = _ctobj("100", 100)
        db: Dict[str, Dict[str, List[Any]]] = {"100": {"cam1": [t]}}
        lock = threading.Lock()
        updates = build_retired_cluster_tracking_updates(db, lock, {1: 2}, self._ARCHIVE)
        moved = apply_tracking_relabel(db, lock, updates)
        assert moved == 1
        assert db["100"]["cam1"] == []
        assert db["200"]["cam1"] == [t]
        assert t.object_id == "200"

    def test_lock_is_held_during_enumeration(self) -> None:
        # Use a recording lock so we can assert the function entered it.
        class _RecordingLock:
            def __init__(self) -> None:
                self.entered = 0
                self._lock = threading.Lock()

            def __enter__(self) -> "_RecordingLock":
                self.entered += 1
                self._lock.__enter__()
                return self

            def __exit__(self, *a: Any) -> None:
                self._lock.__exit__(*a)

        db: Dict[str, Dict[str, List[Any]]] = {"100": {"cam1": [_ctobj("100", 100)]}}
        rec = _RecordingLock()
        build_retired_cluster_tracking_updates(db, rec, {1: 2}, self._ARCHIVE)
        assert rec.entered == 1

    def test_unclustered_entries_are_skipped(self) -> None:
        # A merge only relabels the clustered members it owns; fresh
        # (is_clustered=False) rows sharing the retired rep's bucket are left
        # for the next clustering tick.
        db: Dict[str, Dict[str, List[Any]]] = {"100": {"cam1": [_tobj("100", 100), _ctobj("100", 101)]}}
        updates = build_retired_cluster_tracking_updates(db, threading.Lock(), {1: 2}, self._ARCHIVE)
        assert updates == [("100", "cam1", 101, "200")]
