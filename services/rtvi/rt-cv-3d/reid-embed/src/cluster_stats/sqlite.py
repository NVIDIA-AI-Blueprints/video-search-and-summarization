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

"""SQLite WAL backend for :class:`ClusterStatsStore` (Phase 2).

Why SQLite WAL?
---------------

Phase 1's :class:`cluster_stats.memory.InMemoryClusterStatsStore` keeps
the stats dict in the FastAPI parent and fans cross-process writes in
over a ``multiprocessing.Queue`` + drainer. That works while only the
parent reads, but it cannot serve a multi-process search tier: a single
queue can't broadcast updates to N reader processes, and an HTTP
handler's ``replace()`` only mutates *its own* process's dict.

SQLite in WAL mode removes both limits: every process opens its own
connection to one database file and reads/writes directly, with many
concurrent readers + one concurrent writer at the file level.

Read hot path
-------------

Cluster stats change only at compression / stats-recompute cadence
(seconds apart), but :meth:`snapshot_for` / :meth:`is_empty` run on
*every* ``/search``. So this backend does **not** hit SQLite per read.
Instead each process keeps an in-memory view (``_by_oid``) plus the
``epoch`` it was built from, and refreshes it lazily:

* Reads serve straight from ``_by_oid`` (a plain dict read -- the same
  cost as the in-memory backend).
* At most once per ``poll_interval`` a reader checks the shared
  ``epoch`` (one tiny ``SELECT``); only if it bumped does it reload the
  whole set and atomically swap ``_by_oid``.

So the staleness bound is ``poll_interval`` (default 0.25 s), which is
far tighter than the stats update cadence, and the per-request cost
stays a dict lookup.

Write path
----------

:meth:`replace` is a whole-set swap: in one ``IMMEDIATE`` transaction it
deletes every row, bulk-inserts the new set, and bumps ``epoch``. It is
safe to call from any process (maintenance worker, FastAPI parent);
WAL's single-writer lock + ``busy_timeout`` serialise concurrent writers.

Centroid storage
----------------

Centroids are stored as raw ``float32`` bytes (BLOB). Embeddings are
float32 in Milvus, so the centroid round-trips at the precision the
search actually uses. (Today ``distance_to_centroid`` is disabled in
``main.py`` so the centroid is write-only, but we persist it so the
feature can be re-enabled without a schema change.)
"""

from __future__ import annotations

import contextlib
import logging
import os
import sqlite3
import threading
import time
from typing import Dict, Iterable, Iterator, List, Optional

import numpy as np

from cluster_stats.base import ClusterStatsStore
from cluster_stats.models import CachedStats, ClusterStats


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Env / defaults
# ---------------------------------------------------------------------------

#: Base DB path. The ``label`` is spliced in before the extension so the
#: main and compressed stores live in separate files
#: (``/tmp/reid_cluster_stats_main.db`` etc.). ``/tmp`` is writable in
#: both the dev venv and the container image; override via
#: :envvar:`CLUSTER_STATS_SQLITE_PATH`.
_DEFAULT_DB_PATH = "/tmp/reid_cluster_stats.db"
_DB_PATH_ENV = "CLUSTER_STATS_SQLITE_PATH"

#: When ``True`` (default, matching Phase 1's clean-restart story) the
#: table is emptied at startup. Set
#: :envvar:`CLUSTER_STATS_RESET_ON_START=false` to keep prior data.
_RESET_ON_START_ENV = "CLUSTER_STATS_RESET_ON_START"

#: How often (seconds) a reader is allowed to check the shared epoch.
#: Bounds read staleness; override via
#: :envvar:`CLUSTER_STATS_SQLITE_POLL_INTERVAL_S`.
_DEFAULT_POLL_INTERVAL_S = 0.25
_POLL_INTERVAL_ENV = "CLUSTER_STATS_SQLITE_POLL_INTERVAL_S"

_DEFAULT_BUSY_TIMEOUT_MS = 1000


def _label_path(base_path: str, label: str) -> str:
    """Splice ``label`` into ``base_path`` before the extension."""
    root, ext = os.path.splitext(base_path)
    return f"{root}_{label}{ext or '.db'}"


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_SCHEMA_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS cluster_stats (
        object_id TEXT    PRIMARY KEY,
        centroid  BLOB    NOT NULL,
        variance  REAL    NOT NULL,
        count     INTEGER NOT NULL
    )
    """,
    # Single-row meta table holding the monotonically increasing epoch.
    # Readers compare their cached epoch against this to decide whether
    # to reload. Seeded to 0 in _init_schema.
    """
    CREATE TABLE IF NOT EXISTS cluster_stats_meta (
        k TEXT PRIMARY KEY,
        v INTEGER NOT NULL
    )
    """,
)

_CENTROID_DTYPE = np.float32


def _centroid_to_blob(centroid: np.ndarray) -> bytes:
    return np.asarray(centroid, dtype=_CENTROID_DTYPE).tobytes()


def _blob_to_centroid(blob: bytes) -> np.ndarray:
    # Read-only view over the bytes; callers only read the centroid.
    return np.frombuffer(blob, dtype=_CENTROID_DTYPE)


class SqliteClusterStatsStore(ClusterStatsStore):
    """SQLite + WAL backend with a lazily-refreshed in-process view.

    One instance per Milvus collection; ``label`` selects the DB file
    and names the store in logs.
    """

    def __init__(
        self,
        label: str,
        path: Optional[str] = None,
        reset_on_start: Optional[bool] = None,
        poll_interval_s: Optional[float] = None,
        busy_timeout_ms: int = _DEFAULT_BUSY_TIMEOUT_MS,
    ) -> None:
        self._label = label
        base = path or os.getenv(_DB_PATH_ENV, _DEFAULT_DB_PATH)
        self._path = _label_path(base, label)
        self._busy_timeout_ms = int(busy_timeout_ms)

        if poll_interval_s is None:
            poll_interval_s = float(os.getenv(_POLL_INTERVAL_ENV, str(_DEFAULT_POLL_INTERVAL_S)))
        self._poll_interval = poll_interval_s

        # Per-(process, thread) connection cache (see tracking_store.sqlite
        # for the rationale; SQLite handles are not fork- or thread-safe).
        self._tls = threading.local()
        self._open_conns: List[sqlite3.Connection] = []
        self._conn_lock = threading.Lock()

        # Lazily-refreshed in-process read view. ``_epoch == -1`` forces
        # the first read in this process to reload. ``_refresh_lock``
        # guards the (rare) reload; steady-state reads are lock-free.
        self._by_oid: Dict[str, CachedStats] = {}
        self._epoch: int = -1
        self._last_check: float = 0.0
        self._refresh_lock = threading.Lock()

        self._owner_pid = os.getpid()
        self._shutdown_done = False

        if reset_on_start is None:
            reset_on_start = os.getenv(_RESET_ON_START_ENV, "true").strip().lower() in ("true", "1", "yes")

        self._init_schema(reset=reset_on_start)
        logger.info(
            "SqliteClusterStatsStore(%s) initialised at %s " "(reset_on_start=%s, poll_interval=%.3fs, pid=%d)",
            self._label,
            self._path,
            reset_on_start,
            self._poll_interval,
            self._owner_pid,
        )

    # ------------------------------------------------------------------
    # Connection management
    # ------------------------------------------------------------------

    def _open_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._path, isolation_level=None, timeout=5.0)
        cur = conn.cursor()
        cur.execute("PRAGMA journal_mode = WAL")
        mode = cur.fetchone()
        if mode and isinstance(mode[0], str) and mode[0].lower() != "wal":
            logger.warning(
                "SqliteClusterStatsStore(%s): requested WAL but DB reports "
                "journal_mode=%r (filesystem may not support WAL)",
                self._label,
                mode[0],
            )
        cur.execute("PRAGMA synchronous = OFF")
        cur.execute("PRAGMA temp_store = MEMORY")
        cur.execute("PRAGMA cache_size = -16384")  # 16 MB
        cur.execute("PRAGMA mmap_size = 134217728")  # 128 MB
        cur.execute(f"PRAGMA busy_timeout = {self._busy_timeout_ms}")
        cur.close()
        with self._conn_lock:
            self._open_conns.append(conn)
        return conn

    def _conn(self) -> sqlite3.Connection:
        cached = getattr(self._tls, "conn", None)
        cached_pid = getattr(self._tls, "pid", None)
        if cached is not None and cached_pid == os.getpid():
            return cached
        conn = self._open_connection()
        self._tls.conn = conn
        self._tls.pid = os.getpid()
        return conn

    def _init_schema(self, reset: bool) -> None:
        conn = self._conn()
        cur = conn.cursor()
        try:
            cur.execute("BEGIN IMMEDIATE")
            for stmt in _SCHEMA_STATEMENTS:
                cur.execute(stmt)
            if reset:
                cur.execute("DELETE FROM cluster_stats")
                cur.execute("INSERT OR REPLACE INTO cluster_stats_meta (k, v) " "VALUES ('epoch', 0)")
            else:
                cur.execute("INSERT OR IGNORE INTO cluster_stats_meta (k, v) " "VALUES ('epoch', 0)")
            cur.execute("COMMIT")
        except Exception:
            cur.execute("ROLLBACK")
            raise
        finally:
            cur.close()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def attach_post_fork(self) -> None:
        # Drop inherited connection handles and invalidate the inherited
        # view so the next read in this child opens a fresh connection
        # and reloads from the shared DB.
        self._tls = threading.local()
        self._by_oid = {}
        self._epoch = -1
        self._last_check = 0.0

    def start_drainers(self) -> None:
        # No owner-side thread: reads refresh lazily, writes go straight
        # to the shared DB. Explicit override keeps the choice visible.
        return

    def shutdown(self) -> None:
        if self._shutdown_done:
            return
        self._shutdown_done = True
        if os.getpid() == self._owner_pid:
            try:
                self._conn().execute("PRAGMA wal_checkpoint(TRUNCATE)")
            except Exception:  # pragma: no cover -- best effort
                logger.exception(
                    "SqliteClusterStatsStore(%s): final WAL checkpoint failed",
                    self._label,
                )
        with self._conn_lock:
            conns = list(self._open_conns)
            self._open_conns.clear()
        for conn in conns:
            try:
                conn.close()
            except Exception:  # pragma: no cover -- best effort
                pass

    # ------------------------------------------------------------------
    # Writer-contention warning
    # ------------------------------------------------------------------

    @contextlib.contextmanager
    def _busy_warn(self, op_name: str) -> Iterator[None]:
        try:
            yield
        except sqlite3.OperationalError as e:
            if "locked" in str(e).lower() or "busy" in str(e).lower():
                logger.warning(
                    "SqliteClusterStatsStore(%s): busy timeout (%d ms) "
                    "exceeded during %s; another writer held the lock too "
                    "long (error=%s).",
                    self._label,
                    self._busy_timeout_ms,
                    op_name,
                    e,
                )
            raise

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------

    def replace(self, stats: Iterable[ClusterStats]) -> None:
        rows = [(s.object_id, _centroid_to_blob(s.centroid), float(s.variance), int(s.count)) for s in stats]
        conn = self._conn()
        with self._busy_warn("replace"):
            try:
                conn.execute("BEGIN IMMEDIATE")
                conn.execute("DELETE FROM cluster_stats")
                if rows:
                    conn.executemany(
                        "INSERT INTO cluster_stats " "(object_id, centroid, variance, count) " "VALUES (?, ?, ?, ?)",
                        rows,
                    )
                conn.execute("UPDATE cluster_stats_meta SET v = v + 1 WHERE k = 'epoch'")
                conn.execute("COMMIT")
            except Exception:
                try:
                    conn.execute("ROLLBACK")
                except sqlite3.OperationalError:
                    pass
                raise
        # Refresh this process's view immediately so the writer sees its
        # own write without waiting for the poll interval.
        self._reload(force=True)
        logger.info(
            "SqliteClusterStatsStore(%s): replaced with %d clusters",
            self._label,
            len(rows),
        )

    def clear(self) -> None:
        self.replace([])

    # ------------------------------------------------------------------
    # Read-view refresh
    # ------------------------------------------------------------------

    def _db_epoch(self) -> int:
        cur = self._conn().execute("SELECT v FROM cluster_stats_meta WHERE k = 'epoch'")
        row = cur.fetchone()
        return int(row[0]) if row else 0

    def _reload(self, force: bool) -> None:
        """Reload the in-process view if the shared epoch changed.

        ``force`` skips the throttle (used right after a local write).
        Guarded by ``_refresh_lock`` with a double-checked throttle so
        concurrent readers don't all reload at once.
        """
        with self._refresh_lock:
            now = time.monotonic()
            if not force and (now - self._last_check) < self._poll_interval:
                return
            self._last_check = now
            db_epoch = self._db_epoch()
            if not force and db_epoch == self._epoch:
                return
            cur = self._conn().execute("SELECT object_id, centroid, variance, count FROM cluster_stats")
            new: Dict[str, CachedStats] = {
                oid: CachedStats(
                    centroid=_blob_to_centroid(blob),
                    variance=variance,
                    count=count,
                )
                for oid, blob, variance, count in cur
            }
            # Atomic swap (attribute assignment is atomic under the GIL);
            # lock-free readers see either the old or the new dict whole.
            self._by_oid = new
            self._epoch = db_epoch

    def _maybe_reload(self) -> None:
        # Lock-free fast path: only enter the locked reload when the poll
        # interval has elapsed. ``_epoch == -1`` forces an initial load.
        if self._epoch == -1 or (time.monotonic() - self._last_check) >= self._poll_interval:
            self._reload(force=False)

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    def snapshot_for(self, object_ids: Iterable[str]) -> Dict[str, CachedStats]:
        self._maybe_reload()
        view = self._by_oid
        return {oid: entry for oid in object_ids if (entry := view.get(oid)) is not None}

    def is_empty(self) -> bool:
        self._maybe_reload()
        return not self._by_oid

    def get(self, object_id: str) -> Optional[ClusterStats]:
        self._maybe_reload()
        entry = self._by_oid.get(object_id)
        if entry is None:
            return None
        return ClusterStats(
            object_id=object_id,
            centroid=entry.centroid,
            variance=entry.variance,
            count=entry.count,
        )

    def values(self) -> List[ClusterStats]:
        self._maybe_reload()
        view = self._by_oid
        return [
            ClusterStats(
                object_id=oid,
                centroid=entry.centroid,
                variance=entry.variance,
                count=entry.count,
            )
            for oid, entry in view.items()
        ]

    # ------------------------------------------------------------------
    # Health check
    # ------------------------------------------------------------------

    def health_check(self) -> Dict[str, str]:
        try:
            self._conn().execute("SELECT 1").fetchone()
            return {f"cluster_stats:{self._label}:sqlite": "ok"}
        except Exception as e:
            return {f"cluster_stats:{self._label}:sqlite": f"error: {e}"}


__all__ = ["SqliteClusterStatsStore"]
