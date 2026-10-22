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

"""In-memory backend for :class:`ClusterStatsStore` (Phase 1).

Behavioural parity with the pre-refactor ``ClusterStatsCache``: a Python
dict in the owner (FastAPI parent) process, an atomic whole-dict swap on
write, and a ``multiprocessing.Queue`` + owner-side drainer thread to
fan in cross-process writes from the maintenance worker.

Owner vs non-owner
------------------

The store is constructed in the parent process (the *owner*). Child
processes inherit it via ``fork``.

* **Owner writes** (:meth:`replace`, :meth:`clear`) build the new view
  and atomically swap it in.
* **Non-owner writes** push the payload onto ``_update_queue``; the
  owner-side drainer (started by :meth:`start_drainers`) applies them
  via the same swap. This replaces the old ``feed()`` method -- callers
  now just call :meth:`replace` from any process and dispatch is handled
  internally.
* **Reads** read the current view directly (a lock-free dict read on
  the search hot path), but are **owner-guarded**: a non-owner caller
  raises :class:`RuntimeError` rather than silently returning its
  stale, fork-time inherited view. Today only the parent reads; a child
  that needs to read is the signal to switch to the SQLite backend
  where every process reads live data.
"""

from __future__ import annotations

import logging
import multiprocessing
import os
import threading
from queue import Empty as QueueEmpty
from typing import Dict, Iterable, List, Optional

from cluster_stats.base import ClusterStatsStore
from cluster_stats.models import CachedStats, ClusterStats


logger = logging.getLogger(__name__)


# Drainer poll cadence: long enough not to burn CPU, short enough to
# notice the shutdown event promptly during interpreter teardown.
_DRAINER_POLL_TIMEOUT_S = 0.5


class InMemoryClusterStatsStore(ClusterStatsStore):
    """Dict-and-swap backend with multiprocessing-queue write fan-in.

    Concrete :class:`cluster_stats.base.ClusterStatsStore`. One instance
    per Milvus collection; the ``label`` names it in logs and the
    drainer thread name.
    """

    def __init__(self, label: str) -> None:
        self._label = label
        self._write_lock = threading.Lock()
        self._by_oid: Dict[str, CachedStats] = {}

        # Cross-process write fan-in. Created in the parent before any
        # child is spawned so children inherit a usable handle.
        self._update_queue: multiprocessing.Queue = multiprocessing.Queue()

        # Dispatch root: anyone running in this pid is the owner.
        self._owner_pid: int = os.getpid()

        # Drainer created lazily by start_drainers().
        self._drainer_thread: Optional[threading.Thread] = None
        self._shutdown_event = threading.Event()
        self._lifecycle_lock = threading.Lock()
        self._drainer_started = False
        self._shutdown_done = False

    # ------------------------------------------------------------------
    # Dispatch helpers
    # ------------------------------------------------------------------

    def _is_owner(self) -> bool:
        # Re-checked on every call (a cached bool would survive fork and
        # lie about a child being the owner).
        return os.getpid() == self._owner_pid

    def _require_owner(self, op: str) -> None:
        """Raise if a read is attempted from a non-owner process.

        Reads serve from ``self._by_oid``, which a forked child only sees
        as a stale copy frozen at fork time (the owner-side drainer's
        ``replace()`` swaps never reach the child). Rather than silently
        returning empty/stale stats, fail loudly so a non-owner reader is
        caught immediately -- the signal to switch to the SQLite backend,
        where every process reads live data.
        """
        if not self._is_owner():
            raise RuntimeError(
                f"InMemoryClusterStatsStore({self._label}).{op}() can only "
                f"run in the owner process (pid {self._owner_pid}); current "
                f"pid {os.getpid()}. Set CLUSTER_STATS_BACKEND=sqlite to read "
                "cluster stats from child processes."
            )

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def attach_post_fork(self) -> None:
        # No per-process resources to open; children keep using the
        # inherited mp.Queue handle.
        return

    def start_drainers(self) -> None:
        """Start the owner-side drainer thread. Idempotent; owner-only."""
        if not self._is_owner():
            return
        with self._lifecycle_lock:
            if self._drainer_started:
                return
            self._drainer_started = True
            self._drainer_thread = threading.Thread(
                target=self._drain_loop,
                name=f"cs-{self._label}-drainer",
                daemon=True,
            )
            self._drainer_thread.start()
            logger.info(
                "InMemoryClusterStatsStore(%s) drainer thread started " "(owner pid=%d)",
                self._label,
                self._owner_pid,
            )

    def shutdown(self) -> None:
        with self._lifecycle_lock:
            if self._shutdown_done:
                return
            self._shutdown_done = True
            self._shutdown_event.set()
        if self._drainer_thread is not None:
            self._drainer_thread.join(timeout=_DRAINER_POLL_TIMEOUT_S * 2)

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------

    def replace(self, stats: Iterable[ClusterStats]) -> None:
        if self._is_owner():
            self._replace_direct(stats)
            return
        # Child process: materialise (exhaust any generator and avoid
        # shipping a lazy iterable across the process boundary) and let
        # the owner-side drainer apply it.
        self._update_queue.put(list(stats))

    def clear(self) -> None:
        # clear is replace([]); reuse the same dispatch so a child can
        # clear without a separate queue.
        self.replace([])

    # ------------------------------------------------------------------
    # Direct mutation (owner thread)
    # ------------------------------------------------------------------

    def _replace_direct(self, stats: Iterable[ClusterStats]) -> None:
        # Build the new view outside the lock, swap under it. Readers
        # grab a reference to the view without locking; the swap is
        # atomic so they always see a fully-formed dict.
        new = {
            s.object_id: CachedStats(
                centroid=s.centroid,
                variance=s.variance,
                count=s.count,
            )
            for s in stats
        }
        with self._write_lock:
            self._by_oid = new
        logger.info(
            "InMemoryClusterStatsStore(%s): replaced with %d clusters",
            self._label,
            len(new),
        )

    # ------------------------------------------------------------------
    # Drainer plumbing
    # ------------------------------------------------------------------

    def _drain_loop(self) -> None:
        name = f"cs-{self._label}-drainer"
        logger.debug("%s starting", name)
        while not self._shutdown_event.is_set():
            try:
                stats = self._update_queue.get(timeout=_DRAINER_POLL_TIMEOUT_S)
            except QueueEmpty:
                continue
            except (OSError, EOFError):
                logger.debug("%s queue closed", name)
                return
            except Exception:  # pragma: no cover -- defensive
                logger.exception("%s unexpected error fetching payload", name)
                continue
            try:
                self._replace_direct(stats)
            except Exception:
                logger.exception(
                    "%s failed to apply payload (dropping %d items)",
                    name,
                    len(stats) if hasattr(stats, "__len__") else -1,
                )
        logger.debug("%s exiting", name)

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    def snapshot_for(self, object_ids: Iterable[str]) -> Dict[str, CachedStats]:
        self._require_owner("snapshot_for")
        view = self._by_oid
        return {oid: entry for oid in object_ids if (entry := view.get(oid)) is not None}

    def is_empty(self) -> bool:
        self._require_owner("is_empty")
        return not self._by_oid

    def get(self, object_id: str) -> Optional[ClusterStats]:
        self._require_owner("get")
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
        self._require_owner("values")
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
        # Only the owner runs the drainer thread.
        if not self._is_owner():
            return {}
        if not self._drainer_started:
            return {f"cluster_stats:{self._label}:drainer": "not started"}
        alive = self._drainer_thread is not None and self._drainer_thread.is_alive()
        return {f"cluster_stats:{self._label}:drainer": "ok" if alive else "dead"}


__all__ = ["InMemoryClusterStatsStore"]
