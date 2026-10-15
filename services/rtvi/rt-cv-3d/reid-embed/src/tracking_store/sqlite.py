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

"""SQLite WAL backend for :class:`TrackingStore` (Phase 2).

Why SQLite WAL?
---------------

Phase 1's :class:`tracking_store.memory.InMemoryTrackingStore` works but
has a structural limit: every read has to happen in the owner process,
and every cross-process write hops through ``multiprocessing.Queue`` +
drainer thread. That meant the Kafka consumer could not look up its own
inserts (its writes were still queued), and any future code that wants
to read tracking data from the maintenance / clustering / search
workers had to be rewritten to hop through the owner.

SQLite in WAL (write-ahead log) mode removes both restrictions:

* Many concurrent readers + one concurrent writer at the **file** level
  (the WAL file is per-database). No application-side queueing.
* Every process opens its own connection to the same database file and
  participates as a first-class reader/writer; ``fork`` and ``spawn``
  semantics are both supported as long as no connection handle is
  shared across the fork boundary.
* Snapshot reads inside a single ``SELECT`` are atomic w.r.t. concurrent
  writes -- ``snapshot()`` / ``get_all()`` are free of partial-state
  hazards without any extra locking.

Performance tuning
------------------

Every connection runs the same pragma block on first use:

* ``journal_mode=WAL``      -- the whole point. WAL setting is
  persistent in the database header; re-issuing it is cheap and idempotent.
* ``synchronous=OFF``       -- skip every fsync. SQLite hands write
  buffers to the kernel and returns immediately; the OS page cache
  decides when to flush them to disk. Failure model:
    - Application crash:  safe (kernel still owns the pages and will
      flush them on its own schedule).
    - OS crash / power loss:  the last few seconds of writes can be
      lost, but the database itself stays valid (no corruption -- WAL
      keeps the main DB file consistent regardless).
  This matches the durability story of the legacy in-memory backend
  (any restart wipes everything). If you ever want fsync-on-commit
  semantics back, set this to ``NORMAL`` -- still WAL-paired, just
  pays one fsync per checkpoint instead of zero.
* ``temp_store=MEMORY``     -- keep sort/temp tables in RAM rather than
  bouncing through a tempfile.
* ``cache_size=-65536``     -- 64 MB per-connection page cache (-ve =
  size in KB). Reduces disk-page churn for hot scans.
* ``mmap_size=256 MB``      -- map the DB file via the OS page cache.
  Reads then bypass the SQLite pager for hot pages.
* ``busy_timeout=1000``     -- spin up to 1 s on ``SQLITE_BUSY`` (a
  concurrent writer holds the lock). Avoids "database is locked"
  errors under bursty load. When the full timeout is exhausted, the
  store logs a WARNING (see :meth:`_busy_warn`) before re-raising so
  writer contention surfaces in logs instead of silently slowing
  requests down. The threshold is settable via the ``busy_timeout_ms``
  constructor kwarg.

Process / thread safety
-----------------------

SQLite connections must not be shared across processes (the FD is
duplicated by ``fork`` but the in-memory pager state is not, and using
the inherited handle corrupts the database). They also must not be
shared across threads within a process by default.

The backend solves both by stashing each connection in
:class:`threading.local`, keyed implicitly by ``(pid, thread_id)``:

* :meth:`_conn` lazily opens a fresh connection on first call in any
  ``(process, thread)`` pair.
* :meth:`attach_post_fork` reinitialises ``self._tls`` so that, if a
  child happened to be a forked copy of a thread that had already
  opened a connection, the inherited handle is dropped before the next
  ``_conn()`` call creates a new one.

INSERT semantics
----------------

The Phase 1 in-memory store appends duplicates blindly on the same
``(object_id, stream_id, frame_id)`` key; ``lookup()`` returns the
*first* matching row -- i.e. the original wins. SQLite uses
``INSERT OR IGNORE`` against a ``UNIQUE`` constraint to match that
"first-write wins" behaviour. This matters when a clustering pass has
flipped ``is_clustered=True`` on a row and a stale Kafka message
re-arrives for the same composite key: the clustered flag is preserved
rather than being clobbered by the replay.

Relabel semantics
-----------------

``apply_relabel`` issues ``UPDATE OR REPLACE`` so that if the
destination ``(new_oid, stream_id, frame_id)`` already exists, it is
overwritten by the relabelled row. That mirrors the in-memory store's
"move the entry, accept whatever lands in the destination bucket"
behaviour.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import sqlite3
import threading
from typing import (
    Any,
    Callable,
    Dict,
    Iterable,
    Iterator,
    List,
    Mapping,
    Optional,
    Sequence,
    Set,
)

from tracking_store.models import TrackedObject
from tracking_store.base import (
    TrackingKey,
    TrackingRelabelUpdate,
    TrackingStore,
)


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Env / defaults
# ---------------------------------------------------------------------------

#: Default DB path. ``/tmp`` is writable in both the dev venv and the
#: NGC PyTorch container image; override via :envvar:`TRACKING_STORE_SQLITE_PATH`.
_DEFAULT_DB_PATH = "/tmp/reid_tracking.db"

#: Default busy timeout (ms). When SQLite would otherwise raise
#: ``SQLITE_BUSY`` because another connection holds the writer lock, it
#: instead spins up to this many milliseconds before giving up. One
#: second is tight enough that writer contention surfaces quickly via
#: the WARNING log emitted by :meth:`_busy_warn`. Bump it (via the
#: ``busy_timeout_ms`` constructor kwarg) if your workload has
#: legitimately long writer-held transactions.
_DEFAULT_BUSY_TIMEOUT_MS = 1000

#: When ``True`` (the default, matching Phase 1's clean-restart story),
#: the table is emptied at startup so the service comes up with the same
#: empty state as the in-memory backend. Set
#: :envvar:`TRACKING_STORE_RESET_ON_START=false` to keep prior data.
_RESET_ON_START_ENV = "TRACKING_STORE_RESET_ON_START"
_DB_PATH_ENV = "TRACKING_STORE_SQLITE_PATH"


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_SCHEMA_STATEMENTS = (
    # Column defaults intentionally mirror the pydantic defaults on
    # ``tracking_store.models.TrackedObject``:
    #   * visibility   -> 1.0  (model: ``Optional[float] = 1.0``)
    #   * version      -> NULL (model: ``Optional[str]   = None``)
    #   * kafka_raw    -> NULL (model: ``Optional[Dict]  = None``)
    #   * is_clustered -> 0    (model: ``Optional[bool]  = False``)
    # Today's INSERT path always passes every column explicitly, so
    # these defaults only fire for hand-written SQL (debug consoles,
    # future ad-hoc migrations) -- but keeping the schema in sync with
    # the model means those callers don't have to memorise the
    # contract elsewhere.
    """
    CREATE TABLE IF NOT EXISTS tracked_objects (
        object_id    TEXT    NOT NULL,
        stream_id    TEXT    NOT NULL,
        frame_id     INTEGER NOT NULL,
        timestamp    TEXT    NOT NULL,
        leftX        REAL    NOT NULL,
        topY         REAL    NOT NULL,
        rightX       REAL    NOT NULL,
        bottomY      REAL    NOT NULL,
        confidence   REAL    NOT NULL,
        visibility   REAL             DEFAULT 1.0,
        version      TEXT             DEFAULT NULL,
        kafka_raw    TEXT             DEFAULT NULL,
        is_clustered INTEGER NOT NULL DEFAULT 0,
        -- ``is_clustered`` is part of the uniqueness key so a clustered
        -- (is_clustered=1) and an un-clustered (is_clustered=0) row for the
        -- same (object_id, stream_id, frame_id) may coexist. Ingest always
        -- writes is_clustered=0, so ``INSERT OR IGNORE`` still dedups repeated
        -- detections; only the clustering relabel produces the =1 variant.
        UNIQUE (object_id, stream_id, frame_id, is_clustered)
    )
    """,
    # Covers ``delete_below(sensor_id, max_frame_id)`` and the
    # ``unclustered_bboxes_in_frame_range`` frame-window scan.
    """
    CREATE INDEX IF NOT EXISTS idx_stream_frame
    ON tracked_objects (stream_id, frame_id)
    """,
    # Backs the coverage/tracking read in ``unclustered_bboxes_in_frame_range``:
    # equality on ``is_clustered`` (leading) then a range scan on ``frame_id``,
    # so the hot per-tick query seeks the un-clustered partition and walks only
    # the frame window instead of scanning the whole table.
    """
    CREATE INDEX IF NOT EXISTS idx_isclustered_frame
    ON tracked_objects (is_clustered, frame_id)
    """,
)


# Dedup on the ``(object_id, stream_id, frame_id)`` triple, NOT on the full
# UNIQUE key. is_clustered is part of the table's UNIQUE constraint so a
# clustered and un-clustered row for one composite key can coexist (produced by
# the clustering relabel). A plain ``INSERT OR IGNORE`` would only ignore on
# that four-column key, so a stale Kafka replay (always is_clustered=0) of a
# row that clustering already flipped to =1 would insert a *second*,
# un-clustered row. The ``WHERE NOT EXISTS`` guard keeps the original
# first-write-wins semantics: if any row for the triple exists (either flag),
# the replay is dropped. SQLite serialises writers under WAL, so the existence
# check + insert are atomic against other writers.
_INSERT_SQL = """
INSERT INTO tracked_objects (
    object_id, stream_id, frame_id, timestamp,
    leftX, topY, rightX, bottomY,
    confidence, visibility, version, kafka_raw, is_clustered
)
SELECT :object_id, :stream_id, :frame_id, :timestamp,
       :leftX, :topY, :rightX, :bottomY,
       :confidence, :visibility, :version, :kafka_raw, :is_clustered
WHERE NOT EXISTS (
    SELECT 1 FROM tracked_objects
    WHERE object_id = :object_id AND stream_id = :stream_id
      AND frame_id = :frame_id
)
"""


# ---------------------------------------------------------------------------
# Row <-> object helpers
# ---------------------------------------------------------------------------

# Column order used by every SELECT in this module. Centralised so that
# changes to the schema flow through one tuple instead of seven hand-rolled
# unpacks.
_SELECT_COLUMNS = (
    "object_id",
    "stream_id",
    "frame_id",
    "timestamp",
    "leftX",
    "topY",
    "rightX",
    "bottomY",
    "confidence",
    "visibility",
    "version",
    "kafka_raw",
    "is_clustered",
)
_SELECT_LIST = ", ".join(_SELECT_COLUMNS)


def _row_to_obj(row: Sequence[Any]) -> TrackedObject:
    """Materialise a ``TrackedObject`` from a row tuple in ``_SELECT_COLUMNS`` order."""
    (
        object_id,
        stream_id,
        frame_id,
        timestamp,
        leftX,
        topY,
        rightX,
        bottomY,
        confidence,
        visibility,
        version,
        kafka_raw,
        is_clustered,
    ) = row
    return TrackedObject(
        object_id=object_id,
        stream_id=stream_id,
        frame_id=frame_id,
        timestamp=timestamp,
        leftX=leftX,
        topY=topY,
        rightX=rightX,
        bottomY=bottomY,
        confidence=confidence,
        visibility=visibility,
        version=version,
        kafka_raw=json.loads(kafka_raw) if kafka_raw else None,
        is_clustered=bool(is_clustered),
    )


def _obj_to_row(obj: TrackedObject) -> Dict[str, Any]:
    """Flatten a ``TrackedObject`` into the named-parameter dict for
    ``_INSERT_SQL``.

    Named (``:name``) rather than positional (``?``) binding so the SQL can
    reference ``object_id`` / ``stream_id`` / ``frame_id`` in both the
    ``SELECT`` and the ``WHERE NOT EXISTS`` dedup guard without duplicating
    the values.
    """
    return {
        "object_id": obj.object_id,
        "stream_id": obj.stream_id,
        "frame_id": int(obj.frame_id),
        "timestamp": obj.timestamp,
        "leftX": float(obj.leftX),
        "topY": float(obj.topY),
        "rightX": float(obj.rightX),
        "bottomY": float(obj.bottomY),
        "confidence": float(obj.confidence),
        # The model defaults visibility to 1.0; coerce ``None`` defensively
        # so the NOT NULL-less column still gets a reasonable value when
        # legacy payloads omit it.
        "visibility": None if obj.visibility is None else float(obj.visibility),
        "version": obj.version,
        "kafka_raw": json.dumps(obj.kafka_raw) if obj.kafka_raw is not None else None,
        "is_clustered": 1 if obj.is_clustered else 0,
    }


# ---------------------------------------------------------------------------
# The backend
# ---------------------------------------------------------------------------


class SqliteTrackingStore(TrackingStore):
    """SQLite + WAL backend.

    Every process (parent FastAPI, Kafka consumer, maintenance worker,
    search workers, ...) opens its own connection on first method call,
    cached per-thread. The parent process performs schema setup +
    optional reset in :meth:`__init__`; children only ever see the
    already-initialised database.
    """

    def __init__(
        self,
        path: Optional[str] = None,
        reset_on_start: Optional[bool] = None,
        busy_timeout_ms: int = _DEFAULT_BUSY_TIMEOUT_MS,
    ) -> None:
        self._path: str = path or os.getenv(_DB_PATH_ENV, _DEFAULT_DB_PATH)
        # Kept as an instance attribute (rather than a module-level
        # constant) so tests can dial it down to milliseconds without
        # monkeypatching globals.
        self._busy_timeout_ms: int = int(busy_timeout_ms)

        # Per-(process, thread) connection cache. threading.local() is
        # naturally per-thread; we additionally check ``_owner_pid`` on
        # each call so an inherited handle from a forked parent never
        # gets reused.
        self._tls = threading.local()
        # Track every connection we hand out so ``shutdown()`` can close
        # them; guarded by ``_conn_lock``.
        self._open_conns: List[sqlite3.Connection] = []
        self._conn_lock = threading.Lock()

        # Pid that owns this Python object. We don't gate reads/writes
        # on it (every process is an equal participant on SQLite), but
        # it lets us decide whether ``shutdown()`` should run the final
        # ``wal_checkpoint`` and whether ``attach_post_fork()`` needs to
        # do real work (parent calling it is a no-op).
        self._owner_pid: int = os.getpid()
        self._shutdown_done = False

        # Resolve reset_on_start: explicit kwarg wins over env var,
        # default ``True`` to match the in-memory store's "fresh on each
        # process start" semantics.
        if reset_on_start is None:
            reset_on_start = os.getenv(_RESET_ON_START_ENV, "true").strip().lower() in ("true", "1", "yes")

        # Eager schema init in the parent. Running it once here -- before
        # any child is forked -- avoids two children racing to create the
        # same schema (SQLite handles the race fine via ``IF NOT EXISTS``
        # but the log noise and the optional ``DELETE`` would be confusing).
        self._init_schema(reset=reset_on_start)
        logger.info(
            "SqliteTrackingStore initialised at %s (reset_on_start=%s, pid=%d)",
            self._path,
            reset_on_start,
            self._owner_pid,
        )

    # ------------------------------------------------------------------
    # Connection management
    # ------------------------------------------------------------------

    def _open_connection(self) -> sqlite3.Connection:
        """Open a new connection and apply our pragma block.

        Called from ``_conn()``. Every returned connection is registered
        in ``_open_conns`` so ``shutdown()`` can close it later. Pragmas
        are applied once per connection; their effect is per-connection
        (except ``journal_mode``, which is per-database but cheap to set
        repeatedly).
        """
        # ``isolation_level=None`` puts the driver in autocommit mode;
        # we manage transactions explicitly with BEGIN/COMMIT/ROLLBACK
        # for the batched paths. ``check_same_thread=False`` is intentionally
        # NOT set: each thread gets its own connection via TLS, so the
        # default same-thread enforcement is fine and catches misuse.
        conn = sqlite3.connect(self._path, isolation_level=None, timeout=5.0)

        # Apply pragma block. ``journal_mode`` is read-back to confirm
        # WAL stuck; logging this once per process is helpful when
        # debugging filesystems that silently refuse WAL (e.g. some NFS
        # mounts, though we already warn against those above).
        cur = conn.cursor()
        cur.execute("PRAGMA journal_mode = WAL")
        mode = cur.fetchone()
        if mode and isinstance(mode[0], str) and mode[0].lower() != "wal":
            logger.warning(
                "SqliteTrackingStore: requested WAL but DB reports journal_mode=%r "
                "(filesystem may not support WAL); falling back gracefully",
                mode[0],
            )
        cur.execute("PRAGMA synchronous = OFF")
        cur.execute("PRAGMA temp_store = MEMORY")
        cur.execute("PRAGMA cache_size = -65536")  # 64 MB
        cur.execute("PRAGMA mmap_size = 268435456")  # 256 MB
        cur.execute(f"PRAGMA busy_timeout = {self._busy_timeout_ms}")
        cur.close()

        with self._conn_lock:
            self._open_conns.append(conn)
        return conn

    def _conn(self) -> sqlite3.Connection:
        """Return the connection for the current (process, thread).

        Implements the cache-with-pid-check pattern: a connection cached
        on TLS in process A is unsafe to use in process B (post-fork),
        so we re-open if the cached pid disagrees with the current pid.
        ``attach_post_fork()`` already clears TLS for the common
        explicit-fork case; this check defends against threads that
        forked their parent process without calling it.
        """
        cached = getattr(self._tls, "conn", None)
        cached_pid = getattr(self._tls, "pid", None)
        if cached is not None and cached_pid == os.getpid():
            return cached
        conn = self._open_connection()
        self._tls.conn = conn
        self._tls.pid = os.getpid()
        return conn

    def _init_schema(self, reset: bool) -> None:
        """Create the schema (idempotent) and optionally wipe the table.

        Runs once in the parent process during ``__init__``. Children
        never call this; they just inherit an already-initialised file.
        """
        conn = self._conn()
        cur = conn.cursor()
        try:
            cur.execute("BEGIN IMMEDIATE")
            for stmt in _SCHEMA_STATEMENTS:
                cur.execute(stmt)
            if reset:
                # DELETE FROM rather than DROP+CREATE so concurrent
                # readers in other processes don't trip over a missing
                # table (only matters in unit tests today, but cheap
                # insurance).
                cur.execute("DELETE FROM tracked_objects")
            cur.execute("COMMIT")
        except Exception:
            cur.execute("ROLLBACK")
            raise
        finally:
            cur.close()

    # ------------------------------------------------------------------
    # Lifecycle hooks
    # ------------------------------------------------------------------

    def attach_post_fork(self) -> None:
        """Discard inherited connection state and reset TLS in the child.

        SQLite connection handles are not safe to use after ``fork()``
        (the FD is duplicated but the SQLite pager's in-memory state
        is not). We drop the TLS entirely so the next ``_conn()`` call
        opens a fresh connection in the child process.
        """
        self._tls = threading.local()
        # The shared _open_conns list still holds handles inherited from
        # the parent. We don't close them here -- the OS already gave
        # the child its own FDs and closing them would race with the
        # parent. Leak is bounded and the parent will close its own
        # copies on shutdown.

    def start_drainers(self) -> None:
        """No-op.

        SQLite WAL exposes the database directly to every process; no
        owner-side drainer thread is needed. The override keeps the
        choice explicit (the ABC marks this abstract on purpose so a
        backend can't silently inherit "do nothing").
        """
        return

    def shutdown(self) -> None:
        """Checkpoint the WAL (best effort) and close known connections.

        Only the owner runs the checkpoint -- children calling
        ``shutdown()`` would race the parent and trip ``SQLITE_BUSY``
        on the checkpoint write. Closing the TLS-cached connections is
        idempotent and safe from any caller.
        """
        if self._shutdown_done:
            return
        self._shutdown_done = True

        if os.getpid() == self._owner_pid:
            try:
                # TRUNCATE shrinks the WAL file to zero on success; PASSIVE
                # would leave it sized. We don't care about hot-restart
                # speed enough to keep a warm WAL.
                conn = self._conn()
                conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            except Exception:  # pragma: no cover -- best-effort
                logger.exception("SqliteTrackingStore: final WAL checkpoint failed")

        with self._conn_lock:
            conns = list(self._open_conns)
            self._open_conns.clear()
        for conn in conns:
            try:
                conn.close()
            except Exception:  # pragma: no cover -- best-effort
                pass

    # ------------------------------------------------------------------
    # Writer-contention warning
    # ------------------------------------------------------------------

    @contextlib.contextmanager
    def _busy_warn(self, op_name: str) -> Iterator[None]:
        """Log a warning if a write trips ``SQLITE_BUSY``.

        SQLite waits up to ``self._busy_timeout_ms`` for a concurrent
        writer before giving up and raising
        ``sqlite3.OperationalError`` with a message containing
        ``"database is locked"``. That happens precisely when another
        process held the writer lock for longer than the timeout, which
        is the kind of contention we want surfaced in logs (not just
        silently slowing down the request).

        We log once per occurrence and re-raise -- the caller's normal
        exception flow continues unchanged. Read paths don't acquire
        the writer lock under WAL, so this guard wraps writes only.
        """
        try:
            yield
        except sqlite3.OperationalError as e:
            if "locked" in str(e).lower() or "busy" in str(e).lower():
                logger.warning(
                    "SqliteTrackingStore: busy timeout (%d ms) exceeded "
                    "during %s; another writer held the lock too long "
                    "(error=%s). This usually means write throughput is "
                    "bottlenecked -- consider batching writes or "
                    "profiling the slow writer.",
                    self._busy_timeout_ms,
                    op_name,
                    e,
                )
            raise

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------

    def insert(self, obj: TrackedObject) -> None:
        # Autocommit single-row insert.
        with self._busy_warn("insert"):
            self._conn().execute(_INSERT_SQL, _obj_to_row(obj))

    def insert_many(self, objs: Sequence[TrackedObject]) -> None:
        # One transaction for the whole batch turns N implicit
        # autocommit transactions into one, which buys:
        #   * atomicity (a failure rolls back the entire batch
        #     instead of leaving the first K rows committed),
        #   * a single writer-lock acquire instead of N,
        #   * a single WAL frame + checkpoint marker instead of N.
        # ``BEGIN IMMEDIATE`` (vs the default DEFERRED) takes the
        # writer lock up front so we fail fast if another writer is
        # active, rather than doing K inserts and then losing the
        # transaction to a snapshot conflict. Note that with
        # ``synchronous=OFF`` no fsyncs happen on the write path at
        # all, so the win here is lock + WAL traffic, not fsync.
        if not objs:
            return
        conn = self._conn()
        with self._busy_warn("insert_many"):
            try:
                conn.execute("BEGIN IMMEDIATE")
                conn.executemany(
                    _INSERT_SQL,
                    (_obj_to_row(o) for o in objs),
                )
                conn.execute("COMMIT")
            except Exception:
                # ROLLBACK is best-effort; if the conn is already out
                # of a transaction (e.g. BEGIN itself failed) sqlite
                # raises "no transaction is active" which we swallow.
                try:
                    conn.execute("ROLLBACK")
                except sqlite3.OperationalError:
                    pass
                raise

    def delete_below(self, sensor_id: str, max_frame_id: int, clustered_only: bool = False) -> int:
        # Preserves the legacy main.py predicate exactly:
        # ``delete_old_entries_from_tracked_object_db`` *kept* rows with
        # ``frame_id >= max_frame_id`` and dropped the rest, despite the
        # method being named "delete_below". The in-memory backend
        # already documents this; we replicate it here.
        #
        # ``clustered_only`` adds ``AND is_clustered = 1`` so the clustering
        # path prunes only history already folded into a cluster rep and
        # leaves raw un-clustered rows for a future clustering pass. The
        # ``idx_isclustered_frame`` index backs this predicate.
        sql = "DELETE FROM tracked_objects WHERE stream_id = ? AND frame_id < ?"
        if clustered_only:
            sql += " AND is_clustered = 1"
        with self._busy_warn("delete_below"):
            cur = self._conn().execute(sql, (sensor_id, int(max_frame_id)))
        return cur.rowcount if cur.rowcount is not None else -1

    def apply_relabel(
        self,
        updates: Sequence[TrackingRelabelUpdate],
        source_clustered: bool = False,
    ) -> int:
        if not updates:
            return 0
        conn = self._conn()
        moved = 0
        missed = 0
        # Which source rows are eligible: the fresh-clustering pass relabels
        # un-clustered rows (=0); the retired-cluster cascade migrates rows
        # that were already clustered in a prior batch (=1).
        src_flag = 1 if source_clustered else 0
        with self._busy_warn("apply_relabel"):
            try:
                conn.execute("BEGIN IMMEDIATE")
                for old_oid, stream_id, frame_id, new_oid in updates:
                    if old_oid == new_oid:
                        # Self-relabel: only the is_clustered flag needs
                        # flipping. ``OR REPLACE`` because is_clustered is part
                        # of the UNIQUE key: setting it to 1 collides with any
                        # pre-existing clustered row at the same (object_id,
                        # stream_id, frame_id), which REPLACE resolves.
                        cur = conn.execute(
                            "UPDATE OR REPLACE tracked_objects SET is_clustered = 1 "
                            "WHERE object_id = ? AND stream_id = ? "
                            "AND frame_id = ? AND is_clustered = ?",
                            (old_oid, stream_id, int(frame_id), src_flag),
                        )
                    else:
                        # ``UPDATE OR REPLACE`` semantics: if a row already
                        # exists at (new_oid, stream_id, frame_id, is_clustered=1)
                        # it is replaced by the relabelled row. Mirrors the
                        # in-memory "move the entry, overwrite the destination"
                        # behaviour.
                        cur = conn.execute(
                            "UPDATE OR REPLACE tracked_objects "
                            "SET object_id = ?, is_clustered = 1 "
                            "WHERE object_id = ? AND stream_id = ? "
                            "AND frame_id = ? AND is_clustered = ?",
                            (new_oid, old_oid, stream_id, int(frame_id), src_flag),
                        )
                    # An update that matches no eligible source row is skipped,
                    # not fatal -- the other updates in the batch still apply.
                    if cur.rowcount:
                        moved += cur.rowcount
                    else:
                        missed += 1
                conn.execute("COMMIT")
            except Exception:
                try:
                    conn.execute("ROLLBACK")
                except sqlite3.OperationalError:
                    pass
                raise
        if missed:
            logger.warning(
                "apply_relabel: %d/%d updates matched no %s source row (skipped)",
                missed,
                len(updates),
                "clustered" if source_clustered else "un-clustered",
            )
        return moved

    def clear(self, object_id: Optional[str] = None) -> None:
        conn = self._conn()
        with self._busy_warn("clear"):
            if object_id is None:
                conn.execute("DELETE FROM tracked_objects")
            else:
                conn.execute(
                    "DELETE FROM tracked_objects WHERE object_id = ?",
                    (object_id,),
                )

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    def lookup(
        self,
        object_id: str,
        sensor_id: str,
        frame_id: int,
    ) -> Optional[TrackedObject]:
        # A clustered (is_clustered=1) and an un-clustered (=0) row can coexist
        # for one composite id (is_clustered is part of the UNIQUE key).
        # ``ORDER BY is_clustered DESC`` returns the clustered row when present
        # and falls back to the un-clustered one otherwise.
        cur = self._conn().execute(
            f"SELECT {_SELECT_LIST} FROM tracked_objects "
            "WHERE object_id = ? AND stream_id = ? AND frame_id = ? "
            "ORDER BY is_clustered DESC "
            "LIMIT 1",
            (object_id, sensor_id, int(frame_id)),
        )
        row = cur.fetchone()
        if row is None:
            return None
        return _row_to_obj(row)

    def get_by_object(
        self,
        object_id: str,
        stream_id: Optional[str] = None,
    ) -> Dict[str, List[TrackedObject]]:
        if stream_id is None:
            cur = self._conn().execute(
                f"SELECT {_SELECT_LIST} FROM tracked_objects " "WHERE object_id = ? " "ORDER BY stream_id, frame_id",
                (object_id,),
            )
        else:
            cur = self._conn().execute(
                f"SELECT {_SELECT_LIST} FROM tracked_objects "
                "WHERE object_id = ? AND stream_id = ? "
                "ORDER BY frame_id",
                (object_id, stream_id),
            )
        return self._group_by_stream(cur)

    def get_all(self) -> Dict[str, Dict[str, List[TrackedObject]]]:
        # Single SELECT ordered by the keys we need so the Python-side
        # grouping is a linear walk rather than a sort-per-bucket.
        cur = self._conn().execute(
            f"SELECT {_SELECT_LIST} FROM tracked_objects " "ORDER BY object_id, stream_id, frame_id"
        )
        result: Dict[str, Dict[str, List[TrackedObject]]] = {}
        for row in cur:
            obj = _row_to_obj(row)
            streams = result.setdefault(obj.object_id, {})
            bucket = streams.setdefault(obj.stream_id, [])
            bucket.append(obj)
        return result

    def snapshot(self) -> Dict[str, Dict[str, List[TrackedObject]]]:
        # SQLite WAL gives the connection a stable read view inside a
        # single SELECT; ``get_all()`` already issues one SELECT, so the
        # result is a consistent snapshot. No extra work needed.
        return self.get_all()

    @staticmethod
    def _group_by_stream(
        rows: Iterable[Sequence[Any]],
    ) -> Dict[str, List[TrackedObject]]:
        """Group a stream-ordered cursor into ``{stream_id: [obj, ...]}``.

        Caller must guarantee the cursor is already ordered by
        (stream_id, frame_id) so the resulting per-stream lists are
        sorted by ``frame_id``.
        """
        out: Dict[str, List[TrackedObject]] = {}
        for row in rows:
            obj = _row_to_obj(row)
            out.setdefault(obj.stream_id, []).append(obj)
        return out

    # ------------------------------------------------------------------
    # Clustering scans
    # ------------------------------------------------------------------

    def unclustered_keys(self) -> Set[TrackingKey]:
        # ``CAST(object_id AS INTEGER)`` returns 0 for non-numeric ids
        # in SQLite, which would collide with a real id 0. To match the
        # in-memory backend's defensive int() filter we do the
        # validation in Python instead.
        cur = self._conn().execute(
            "SELECT object_id, stream_id, frame_id " "FROM tracked_objects WHERE is_clustered = 0"
        )
        keys: Set[TrackingKey] = set()
        for oid_str, sid, fid in cur:
            try:
                oid_int = int(oid_str)
            except (TypeError, ValueError):
                continue
            keys.add((oid_int, sid, fid))
        return keys

    def unclustered_bboxes_in_frame_range(
        self,
        min_frame: int,
        max_frame: int,
    ) -> Dict[TrackingKey, Dict[str, float]]:
        cur = self._conn().execute(
            "SELECT object_id, stream_id, frame_id, leftX, topY, rightX, bottomY "
            "FROM tracked_objects "
            "WHERE is_clustered = 0 AND frame_id BETWEEN ? AND ?",
            (int(min_frame), int(max_frame)),
        )
        result: Dict[TrackingKey, Dict[str, float]] = {}
        for oid_str, sid, fid, lx, ty, rx, by in cur:
            try:
                oid_int = int(oid_str)
            except (TypeError, ValueError):
                continue
            result[(oid_int, sid, fid)] = {
                "leftX": float(lx),
                "topY": float(ty),
                "rightX": float(rx),
                "bottomY": float(by),
            }
        return result

    def build_retired_relabels(
        self,
        retired_to_surviving: Mapping[int, int],
        cluster_id_to_oid: Callable[[int], str],
    ) -> List[TrackingRelabelUpdate]:
        # Pre-resolve cluster id -> object_id so the per-row loop is a
        # dict lookup. Matches the in-memory backend's flow exactly so
        # the merge-ready branch sees identical results regardless of
        # which backend is wired in.
        resolved: Dict[str, str] = {}
        for retired_cid, surviving_cid in retired_to_surviving.items():
            try:
                old_oid = cluster_id_to_oid(int(retired_cid))
                new_oid = cluster_id_to_oid(int(surviving_cid))
            except Exception:
                logger.exception(
                    "build_retired_relabels: cluster_id_to_oid failed " "for %r -> %r",
                    retired_cid,
                    surviving_cid,
                )
                continue
            if old_oid == new_oid:
                continue
            resolved[old_oid] = new_oid

        if not resolved:
            return []

        updates: List[TrackingRelabelUpdate] = []
        # Clustered rows only: a merge relabels the members it owns. One
        # SELECT for all retired buckets; cheaper than N round-trips.
        placeholders = ",".join("?" * len(resolved))
        cur = self._conn().execute(
            f"SELECT object_id, stream_id, frame_id FROM tracked_objects "
            f"WHERE is_clustered = 1 AND object_id IN ({placeholders})",
            tuple(resolved.keys()),
        )
        for old_oid, sid, fid in cur:
            new_oid = resolved.get(old_oid)
            if new_oid is None:
                continue  # pragma: no cover -- defensive
            updates.append((old_oid, sid, fid, new_oid))
        return updates

    # ------------------------------------------------------------------
    # Diagnostics
    # ------------------------------------------------------------------

    def stats(self) -> Dict[str, int]:
        # All three counts in one SELECT: cheaper than three round-trips
        # and atomic w.r.t. concurrent writers.
        cur = self._conn().execute(
            "SELECT "
            "  COUNT(DISTINCT object_id), "
            "  COUNT(DISTINCT object_id || '|' || stream_id), "
            "  COUNT(*) "
            "FROM tracked_objects"
        )
        row = cur.fetchone()
        total_objects, total_streams, total_entries = row or (0, 0, 0)
        return {
            "total_objects": int(total_objects or 0),
            "total_streams": int(total_streams or 0),
            "total_entries": int(total_entries or 0),
        }

    def health_check(self) -> Dict[str, str]:
        # Cheap liveness probe: ping the DB through a fresh statement.
        # If the file got moved or the FS errored, this surfaces it on
        # /health/ready instead of waiting for the next real query to
        # blow up.
        try:
            self._conn().execute("SELECT 1").fetchone()
            return {"tracking_store:sqlite": "ok"}
        except Exception as e:
            return {"tracking_store:sqlite": f"error: {e}"}


__all__ = ["SqliteTrackingStore"]
