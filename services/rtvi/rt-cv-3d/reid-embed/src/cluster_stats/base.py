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

"""ClusterStatsStore abstract base class.

Every backend (in-memory, SQLite, Redis, ...) inherits from
:class:`ClusterStatsStore` and implements its ``@abstractmethod`` hooks.
``main.py`` and any other caller only depends on this module; it never
imports a concrete backend directly (it goes through
:func:`cluster_stats.factory.make_cluster_stats_store`).

This mirrors the design of :class:`tracking_store.base.TrackingStore`
so the two stores feel identical to operate.

Access model
------------

A cluster-stats store holds per-object ``(centroid, variance, count)``
summaries for **one** Milvus collection (the ``label`` distinguishes the
main and compressed instances). The data is a *derived* artifact: it can
always be recomputed from Milvus, it changes only at compression /
stats-recompute cadence (seconds apart), and it is replaced **as a whole
set** rather than mutated row-by-row. That shapes the API:

* **Writes** (:meth:`replace`, :meth:`clear`) are whole-set operations
  and are safe to call from any process. The in-memory backend applies
  owner-process writes inline and fans non-owner writes in over a
  ``multiprocessing.Queue`` + drainer thread; server-backed backends
  (SQLite WAL) write to the shared store directly from any process.
* **Reads** (:meth:`snapshot_for`, :meth:`is_empty`, :meth:`get`,
  :meth:`values`) are on the search hot path, so they must be cheap.
  Backends keep an in-process view and serve reads from it without a
  per-read round trip (the SQLite backend refreshes that view lazily
  when a shared epoch counter bumps).

Lifecycle (identical contract to ``TrackingStore``)
---------------------------------------------------

1. Construct in the parent process *before* any child is spawned.
2. Spawn children; they inherit the store via ``fork``.
3. In each child entry function call :meth:`attach_post_fork`.
4. In the parent, after children are spawned, call :meth:`start_drainers`.
5. On shutdown call :meth:`shutdown` from the parent.

All lifecycle hooks are abstract on purpose: even a "do nothing"
implementation should be a conscious choice rather than an accidental
inherited default.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Dict, Iterable, List, Optional

from cluster_stats.models import CachedStats, ClusterStats


class ClusterStatsStore(ABC):
    """Cross-process facade over one collection's cluster statistics."""

    # ------------------------------------------------------------------
    # Lifecycle hooks
    # ------------------------------------------------------------------

    @abstractmethod
    def attach_post_fork(self) -> None:
        """Initialise per-process state inside a forked child.

        Called from inside a worker's entry function, after fork. Must
        be idempotent. Backends with nothing to do here should still
        override with ``pass`` so the choice is explicit.
        """

    @abstractmethod
    def start_drainers(self) -> None:
        """Start any owner-side helper threads.

        Must be called *after* all child processes have been spawned
        (the fork-after-thread footgun). Must be idempotent.
        """

    @abstractmethod
    def shutdown(self) -> None:
        """Stop helper threads and release backend resources.

        Safe to call multiple times and during interpreter teardown.
        """

    # ------------------------------------------------------------------
    # Writes -- safe to call from any process
    # ------------------------------------------------------------------

    @abstractmethod
    def replace(self, stats: Iterable[ClusterStats]) -> None:
        """Atomically replace the entire set of cluster stats.

        This is the single write path. The maintenance worker calls it
        with freshly computed stats each cycle; HTTP handlers call it on
        cache-miss / refresh. Backends must make the swap atomic for
        concurrent readers (no partial-state window).
        """

    @abstractmethod
    def clear(self) -> None:
        """Drop all cluster stats (equivalent to ``replace([])``)."""

    # ------------------------------------------------------------------
    # Reads (search hot path -- keep cheap)
    # ------------------------------------------------------------------

    @abstractmethod
    def snapshot_for(self, object_ids: Iterable[str]) -> Dict[str, CachedStats]:
        """Bulk snapshot for the search hot path.

        Returns ``{oid: CachedStats(centroid, variance, count)}`` for
        every ``oid`` in ``object_ids`` present in the store. Missing
        ids are silently skipped (matches ``if oid in cache`` semantics).
        """

    @abstractmethod
    def is_empty(self) -> bool:
        """True iff the store currently holds no cluster stats."""

    @abstractmethod
    def get(self, object_id: str) -> Optional[ClusterStats]:
        """Return the :class:`ClusterStats` for ``object_id`` or ``None``."""

    @abstractmethod
    def values(self) -> List[ClusterStats]:
        """Return every :class:`ClusterStats` currently held."""

    # ------------------------------------------------------------------
    # Diagnostics
    # ------------------------------------------------------------------

    def health_check(self) -> Dict[str, str]:
        """Return one entry per backend resource the readiness probe
        should assert is alive.

        Each value is ``"ok"`` or a short error string. Default returns
        ``{}`` ("nothing to check"). Backends with their own drainer
        threads or connections override this.
        """
        return {}


__all__ = ["ClusterStatsStore"]
