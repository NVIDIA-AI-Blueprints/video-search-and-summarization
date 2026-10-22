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

"""SQLite backend-specific tests.

The cross-backend behaviour is already covered by
``test_conformance.py``; this file targets the bits of
:class:`SqliteTrackingStore` that the conformance suite does not
exercise:

* WAL + pragma block is actually applied to every fresh connection.
* Connections are per-(process, thread); a second thread does not
  blow up on "SQLite objects from a different thread".
* ``attach_post_fork`` discards inherited TLS state.
* ``reset_on_start`` semantics: ``True`` wipes, ``False`` preserves.
* ``INSERT OR IGNORE`` keeps ``is_clustered=True`` against a stale
  Kafka-style replay (the in-memory store does the same via "first
  arrival wins" lookup ordering).
* ``apply_relabel`` + ``UPDATE OR REPLACE`` handles destination
  collisions without crashing on the UNIQUE constraint.
* ``shutdown()`` is idempotent and survives running twice.
"""

from __future__ import annotations

import logging
import multiprocessing
import sqlite3
import threading
import time

import pytest

from tracking_store import SqliteTrackingStore
from tracking_store.tests.conftest import make_obj


# ---------------------------------------------------------------------------
# Pragma / connection plumbing
# ---------------------------------------------------------------------------


def test_wal_mode_and_pragmas_applied(tmp_path):
    store = SqliteTrackingStore(path=str(tmp_path / "t.db"))
    try:
        conn = store._conn()
        assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        # synchronous: 0 = OFF, 1 = NORMAL, 2 = FULL, 3 = EXTRA. We
        # intentionally pick OFF for max write throughput; the DB stays
        # valid through application or OS crashes, only at-risk data is
        # the last few seconds of writes on power loss (acceptable for
        # ephemeral tracking data).
        assert conn.execute("PRAGMA synchronous").fetchone()[0] == 0  # OFF
        # busy_timeout is a SQLite-side value (ms). Assert it matches
        # whatever the store decided to apply rather than hardcoding a
        # number that lives in sqlite.py -- this keeps the test stable
        # when the default is tuned.
        assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == store._busy_timeout_ms
    finally:
        store.shutdown()


def test_per_thread_connections(tmp_path):
    """Different threads must each get their own connection.

    Calling a method from a second thread used to raise
    ``sqlite3.ProgrammingError: SQLite objects created in a thread can
    only be used in that same thread`` before we wired up
    ``threading.local``; this test guards the regression.
    """
    store = SqliteTrackingStore(path=str(tmp_path / "t.db"))
    try:
        store.insert(make_obj("1", "cam", 1))

        results = {}

        def reader():
            try:
                results["snap"] = store.snapshot()
            except sqlite3.ProgrammingError as e:  # pragma: no cover -- regression guard
                results["err"] = repr(e)

        t = threading.Thread(target=reader)
        t.start()
        t.join(timeout=5)
        assert "err" not in results, results.get("err")
        assert "1" in results["snap"]
    finally:
        store.shutdown()


def test_attach_post_fork_clears_tls(tmp_path):
    """attach_post_fork() must drop any inherited TLS state.

    We can't easily verify the post-fork connection is "fresh" without
    spawning a real process (see the multiprocessing test below); the
    cheap check is that ``self._tls`` is replaced with a new object so
    nothing is reused by identity.
    """
    store = SqliteTrackingStore(path=str(tmp_path / "t.db"))
    try:
        _ = store._conn()  # populate tls.conn / tls.pid
        old_tls = store._tls
        assert getattr(old_tls, "conn", None) is not None

        store.attach_post_fork()

        assert store._tls is not old_tls
        # New TLS has no cached conn yet; first _conn() will open a fresh one.
        assert getattr(store._tls, "conn", None) is None
    finally:
        store.shutdown()


# ---------------------------------------------------------------------------
# reset_on_start semantics
# ---------------------------------------------------------------------------


def test_reset_on_start_true_wipes_existing_data(tmp_path):
    db_path = str(tmp_path / "persist.db")

    seeder = SqliteTrackingStore(path=db_path, reset_on_start=True)
    try:
        seeder.insert_many(
            [
                make_obj("1", "cam", 1),
                make_obj("1", "cam", 2),
            ]
        )
        assert seeder.stats()["total_entries"] == 2
    finally:
        seeder.shutdown()

    fresh = SqliteTrackingStore(path=db_path, reset_on_start=True)
    try:
        assert fresh.stats()["total_entries"] == 0
    finally:
        fresh.shutdown()


def test_reset_on_start_false_preserves_data(tmp_path):
    db_path = str(tmp_path / "persist.db")

    seeder = SqliteTrackingStore(path=db_path, reset_on_start=True)
    try:
        seeder.insert_many(
            [
                make_obj("1", "cam", 1),
                make_obj("1", "cam", 2),
            ]
        )
    finally:
        seeder.shutdown()

    reopened = SqliteTrackingStore(path=db_path, reset_on_start=False)
    try:
        snap = reopened.get_by_object("1")
        assert [o.frame_id for o in snap["cam"]] == [1, 2]
    finally:
        reopened.shutdown()


# ---------------------------------------------------------------------------
# INSERT OR IGNORE / UPDATE OR REPLACE collision semantics
# ---------------------------------------------------------------------------


def test_insert_or_ignore_preserves_clustered_flag(tmp_path):
    """Stale Kafka replays must not un-cluster a clustered row.

    After clustering flips ``is_clustered=True`` on a row, an
    ``INSERT OR REPLACE`` would overwrite that to 0 (the default
    on the new row). ``INSERT OR IGNORE`` is the safer choice and
    this test guards it.
    """
    store = SqliteTrackingStore(path=str(tmp_path / "t.db"))
    try:
        store.insert(make_obj("1", "cam", 5))
        # Pretend clustering relabelled this row in place
        # (apply_relabel(same -> same) just flips the flag).
        store.apply_relabel([("1", "cam", 5, "1")])
        before = store.lookup("1", "cam", 5)
        assert before is not None and before.is_clustered is True

        # Stale Kafka replay for the same composite key, with
        # is_clustered defaulted to False on the model.
        store.insert(make_obj("1", "cam", 5, confidence=0.42))
        after = store.lookup("1", "cam", 5)
        assert after is not None
        # The clustered flag survived the replay.
        assert after.is_clustered is True
        # And the replay did not clobber the row's other fields
        # (INSERT OR IGNORE -> the first write wins).
        assert after.confidence != 0.42
    finally:
        store.shutdown()


def test_apply_relabel_coexists_with_unclustered_destination(tmp_path):
    """is_clustered is part of the UNIQUE key, so relabelling onto an
    *un-clustered* destination at the same composite key no longer replaces
    it -- the rep's own un-clustered detection is preserved alongside the
    folded-in clustered row."""
    store = SqliteTrackingStore(path=str(tmp_path / "t.db"))
    try:
        store.insert_many(
            [
                make_obj("100", "cam", 5, confidence=0.1),
                make_obj("200", "cam", 5, confidence=0.9),  # un-clustered dest
            ]
        )
        moved = store.apply_relabel([("100", "cam", 5, "200")])
        assert moved == 1

        snap = store.get_all()
        # Source bucket emptied; destination now holds BOTH rows.
        assert snap.get("100", {}).get("cam", []) == []
        rows = snap["200"]["cam"]
        assert [o.frame_id for o in rows] == [5, 5]
        flags = sorted(o.is_clustered for o in rows)
        assert flags == [False, True]
    finally:
        store.shutdown()


def test_apply_relabel_replaces_clustered_destination_collision(tmp_path):
    """UPDATE OR REPLACE must replace, not crash, when the destination row is
    already clustered (same four-column UNIQUE key)."""
    store = SqliteTrackingStore(path=str(tmp_path / "t.db"))
    try:
        store.insert_many(
            [
                make_obj("100", "cam", 5, confidence=0.1),
                make_obj("101", "cam", 5, confidence=0.5),
            ]
        )
        # Fold 101 -> 200 first so the destination (200, cam, 5) is clustered.
        assert store.apply_relabel([("101", "cam", 5, "200")]) == 1
        # Now fold 100 -> 200 at the same frame: collides with the clustered
        # (200, cam, 5, is_clustered=1) row -> REPLACE keeps a single row.
        assert store.apply_relabel([("100", "cam", 5, "200")]) == 1

        snap = store.get_all()
        assert snap.get("100", {}).get("cam", []) == []
        assert snap.get("101", {}).get("cam", []) == []
        rows = snap["200"]["cam"]
        assert [o.frame_id for o in rows] == [5]
        assert rows[0].is_clustered is True
    finally:
        store.shutdown()


def test_apply_relabel_self_relabel_flips_only_the_flag(tmp_path):
    store = SqliteTrackingStore(path=str(tmp_path / "t.db"))
    try:
        store.insert(make_obj("1", "cam", 5, confidence=0.7))
        moved = store.apply_relabel([("1", "cam", 5, "1")])
        # Self-relabel reports 1 move (the flag flipped) but the row
        # stays in place.
        assert moved == 1
        hit = store.lookup("1", "cam", 5)
        assert hit is not None
        assert hit.is_clustered is True
        assert hit.confidence == 0.7
    finally:
        store.shutdown()


# ---------------------------------------------------------------------------
# Busy-timeout warning
# ---------------------------------------------------------------------------


def test_busy_timeout_logs_warning_and_reraises(tmp_path, caplog):
    """When a competing writer holds the lock past busy_timeout_ms, the
    store must (a) raise the underlying sqlite3.OperationalError and
    (b) log a WARNING naming the operation, so writer contention shows
    up in /health/ready logs instead of silently slowing requests.

    Strategy: open a *parallel* sqlite3 connection to the same file,
    take an exclusive writer lock with ``BEGIN IMMEDIATE``, then try
    to ``insert()`` through the store with a 50 ms busy_timeout. The
    store will exhaust the timeout almost immediately and we can
    verify both the exception and the log line.
    """
    db_path = str(tmp_path / "busy.db")
    store = SqliteTrackingStore(path=db_path, busy_timeout_ms=50)
    try:
        # Make the store touch the DB once so it isn't fighting init.
        store.insert(make_obj("seed", "cam", 0))

        # Parallel connection grabs the writer lock and sits on it.
        # ``IMMEDIATE`` (rather than DEFERRED) takes RESERVED right
        # away; subsequent writers can only wait.
        blocker = sqlite3.connect(db_path, isolation_level=None, timeout=1.0)
        blocker.execute("PRAGMA busy_timeout = 50")
        blocker.execute("BEGIN IMMEDIATE")

        try:
            with caplog.at_level(logging.WARNING, logger="tracking_store.sqlite"):
                with pytest.raises(sqlite3.OperationalError):
                    store.insert(make_obj("1", "cam", 1))

            # At least one WARNING was emitted, and it names the op
            # (so log readers can tell what kind of write was blocked).
            warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
            assert any(
                "busy timeout" in r.getMessage().lower() and "insert" in r.getMessage() for r in warnings
            ), f"expected busy-timeout warning, got: {[r.getMessage() for r in warnings]}"
        finally:
            blocker.execute("ROLLBACK")
            blocker.close()
    finally:
        store.shutdown()


# ---------------------------------------------------------------------------
# Schema defaults (mirror TrackedObject pydantic defaults)
# ---------------------------------------------------------------------------


def test_schema_defaults_for_optional_columns(tmp_path):
    """Optional columns must default to the same values the model uses.

    The store's own ``insert()`` always provides every column, so the
    SQL DEFAULT clauses only fire for hand-written SQL (debug
    consoles, migrations). Hit them directly with a raw INSERT and
    verify ``_row_to_obj()``-equivalent reads pick up the model
    defaults: visibility=1.0, version=None, kafka_raw=None,
    is_clustered=False.
    """
    store = SqliteTrackingStore(path=str(tmp_path / "defaults.db"))
    try:
        conn = store._conn()
        # Partial INSERT: only the NOT NULL columns are supplied.
        conn.execute(
            "INSERT INTO tracked_objects "
            "(object_id, stream_id, frame_id, timestamp, "
            " leftX, topY, rightX, bottomY, confidence) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("1", "cam", 1, "0", 0.0, 0.0, 1.0, 1.0, 0.5),
        )
        hit = store.lookup("1", "cam", 1)
        assert hit is not None
        assert hit.visibility == 1.0  # SQL DEFAULT 1.0 fired
        assert hit.version is None  # SQL DEFAULT NULL
        assert hit.kafka_raw is None  # SQL DEFAULT NULL
        assert hit.is_clustered is False  # SQL DEFAULT 0
    finally:
        store.shutdown()


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


def test_shutdown_is_idempotent(tmp_path):
    store = SqliteTrackingStore(path=str(tmp_path / "t.db"))
    store.insert(make_obj("1", "cam", 1))
    store.shutdown()
    store.shutdown()  # second call must not raise


def test_start_drainers_is_a_noop(tmp_path):
    # SQLite needs no drainer threads; the override must be a no-op
    # but still callable (and idempotent).
    store = SqliteTrackingStore(path=str(tmp_path / "t.db"))
    try:
        store.start_drainers()
        store.start_drainers()
    finally:
        store.shutdown()


# ---------------------------------------------------------------------------
# Diagnostics
# ---------------------------------------------------------------------------


def test_health_check_reports_ok(tmp_path):
    store = SqliteTrackingStore(path=str(tmp_path / "t.db"))
    try:
        assert store.health_check() == {"tracking_store:sqlite": "ok"}
    finally:
        store.shutdown()


def test_stats_returns_correct_counts(tmp_path):
    store = SqliteTrackingStore(path=str(tmp_path / "t.db"))
    try:
        store.insert_many(
            [
                make_obj("1", "camA", 1),
                make_obj("1", "camA", 2),
                make_obj("1", "camB", 1),
                make_obj("2", "camA", 1),
            ]
        )
        stats = store.stats()
        assert stats["total_objects"] == 2
        assert stats["total_streams"] == 3  # (1,A), (1,B), (2,A)
        assert stats["total_entries"] == 4
    finally:
        store.shutdown()


# ---------------------------------------------------------------------------
# Real cross-process behaviour via fork(). Skipped where fork isn't
# available (some Windows CI runners). This is the big payoff of Phase
# 2: children read AND write directly against the DB, no queue hop.
# ---------------------------------------------------------------------------


def _child_inserts_and_reads(db_path: str, out_queue) -> None:
    """Run in a forked child: open our own conn, insert + read back.

    Crucially the child performs a *read* that would have been
    illegal under the InMemoryTrackingStore owner-only-reads rule.
    """
    try:
        store = SqliteTrackingStore(path=db_path, reset_on_start=False)
        store.attach_post_fork()
        for fid in (10, 20, 30):
            store.insert(make_obj("c", "cam", fid))
        # Read our own writes back -- this is the new Phase 2 capability.
        snap = store.get_by_object("c")
        out_queue.put([o.frame_id for o in snap.get("cam", [])])
        store.shutdown()
    except Exception as e:  # pragma: no cover -- only fires on regression
        out_queue.put(f"err: {e!r}")


@pytest.mark.skipif(
    "fork" not in multiprocessing.get_all_start_methods(),
    reason="fork() not available on this platform",
)
def test_child_process_can_read_its_own_writes(tmp_path):
    db_path = str(tmp_path / "fork.db")
    parent = SqliteTrackingStore(path=db_path, reset_on_start=True)
    try:
        parent.insert(make_obj("p", "cam", 1))

        ctx = multiprocessing.get_context("fork")
        q: multiprocessing.Queue = ctx.Queue()
        proc = ctx.Process(target=_child_inserts_and_reads, args=(db_path, q))
        proc.start()
        proc.join(timeout=10)
        assert proc.exitcode == 0, "child failed"

        child_view = q.get(timeout=2)
        assert child_view == [10, 20, 30], child_view

        # And the parent now sees the child's writes too.
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            snap = parent.get_by_object("c").get("cam", [])
            if [o.frame_id for o in snap] == [10, 20, 30]:
                break
            time.sleep(0.02)
        snap = parent.get_by_object("c").get("cam", [])
        assert [o.frame_id for o in snap] == [10, 20, 30]
    finally:
        parent.shutdown()
