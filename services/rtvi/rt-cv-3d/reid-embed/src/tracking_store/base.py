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

"""TrackingStore abstract base class and shared types.

Every backend (in-memory, SQLite, Redis, ...) inherits from
:class:`TrackingStore` and implements its ``@abstractmethod`` hooks.
``main.py`` and any other caller only depends on this module; it
never imports a concrete backend directly.

Design notes
------------

* The base class is a plain :mod:`abc.ABC`.

* :class:`tracking_store.models.TrackedObject` is the row schema. It
  lives in this package precisely so this module can reference it
  without a circular dependency on ``main.py``.

* :meth:`TrackingStore.build_retired_relabels` takes a
  ``Callable[[int], str]`` rather than importing
  ``cluster_manager.Archive``. The merge-ready branch passes
  ``lambda cid: cluster_id_to_object_id(cid, archive)``; the main
  branch never calls this method but the method is part of the API so
  the merge does not require another protocol revision.

* Reads (``lookup``, ``get_by_object``, ``get_all``, ``snapshot``,
  ``unclustered_*``) are only meaningful in the process that owns the
  store. For :class:`InMemoryTrackingStore` "owner" means the
  ``os.getpid()`` that constructed the store (typically the FastAPI
  parent). For server-backed stores (SQLite WAL, Redis) every process
  is an owner.

* Writes (``insert``, ``insert_many``, ``apply_relabel``,
  ``delete_below``, ``clear``) work from any process. The in-memory
  backend forwards non-owner writes onto an ``mp.Queue`` that the
  owner-side drainer thread applies; SQLite / Redis backends issue
  them directly against the shared store.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import (
    Callable,
    Dict,
    List,
    Mapping,
    Optional,
    Sequence,
    Set,
    Tuple,
)

from tracking_store.models import TrackedObject


# ---------------------------------------------------------------------------
# Type aliases shared by every backend and every caller. Keeping these in one
# place avoids drift when the merge-ready branch lands and a second module
# starts producing :data:`TrackingRelabelUpdate` tuples.
# ---------------------------------------------------------------------------

#: ``(old_object_id, stream_id, frame_id, new_object_id)``. Tuple shape
#: matches the merge-ready branch's ``cluster_manager.tracking_relabel``
#: contract so payloads are interoperable across both branches.
TrackingRelabelUpdate = Tuple[str, str, int, str]


#: Join key used by clustering's coverage check. The ints reflect the
#: existing main-branch behaviour (``object_id`` coerced to int).
TrackingKey = Tuple[int, str, int]


#: Mapping ``sensor_id -> max_frame_id_already_processed``; payload
#: shape currently carried by ``_tracked_object_delete_queue``.
SensorIdToMaxFrameIdMap = Dict[str, int]


class TrackingStore(ABC):
    """Cross-process facade over the tracked-objects database.

    Every method is process-safe: the backend handles whatever
    coordination is needed (in-memory queues, file locks, network).

    Lifecycle
    ~~~~~~~~~

    1. Construct the store in the parent process *before* any child
       processes are spawned. The store captures whatever it needs to
       route cross-process traffic.
    2. Spawn child processes. They inherit the store via ``fork`` (or
       receive it by pickling on ``spawn``).
    3. Inside each child's entry function, call
       :meth:`attach_post_fork` to (re)initialise per-process state
       (e.g. open a SQLite connection, create a Redis client).
    4. In the parent, call :meth:`start_drainers` once children are
       spawned. Backends that don't need a parent-side drainer can
       leave it as a no-op; in-memory uses it to launch the
       queue-draining threads without falling into the
       "thread-then-fork" trap.
    5. On shutdown call :meth:`shutdown` from the parent to stop
       drainers and release resources.

    All lifecycle hooks are abstract on purpose: even a "do nothing"
    implementation should be a conscious choice rather than an
    accidental inherited default, because forgetting to open a
    per-process connection (or to close drainer threads) silently
    corrupts data.
    """

    # ------------------------------------------------------------------
    # Lifecycle hooks
    # ------------------------------------------------------------------

    @abstractmethod
    def attach_post_fork(self) -> None:
        """Initialise per-process state inside a forked child.

        Called from inside the worker's entry function, after fork and
        any other ``_ignore_sigint_in_worker`` style setup. Must be
        idempotent. Backends that have nothing to do here should still
        override with ``pass`` so the choice is explicit.
        """

    @abstractmethod
    def start_drainers(self) -> None:
        """Start any owner-side helper threads.

        Must be called *after* all child processes have been spawned,
        to avoid the well-known fork-after-thread interaction. Must be
        idempotent.
        """

    @abstractmethod
    def shutdown(self) -> None:
        """Stop drainer threads and release any backend resources.

        Safe to call multiple times. Safe to call from the parent
        process during interpreter teardown.
        """

    # ------------------------------------------------------------------
    # Writes -- safe to call from any process
    # ------------------------------------------------------------------

    @abstractmethod
    def insert(self, obj: TrackedObject) -> None:
        """Record a single tracked object.

        Idempotency on ``(object_id, stream_id, frame_id)`` is **not**
        guaranteed by the protocol; today the in-memory backend
        appends duplicates (matches existing behaviour). SQL backends
        may de-duplicate via ``INSERT OR REPLACE`` at their
        discretion. Callers must not depend on either.
        """

    def insert_many(self, objs: Sequence[TrackedObject]) -> None:
        """Record many tracked objects in one batch.

        Default implementation is a simple loop over :meth:`insert`,
        which is functionally correct for every backend. Backends that
        can amortise commit costs (``executemany``, pipelined writes,
        a single lock acquisition for the whole batch) should override.
        """
        for obj in objs:
            self.insert(obj)

    @abstractmethod
    def delete_below(self, sensor_id: str, max_frame_id: int, clustered_only: bool = False) -> int:
        """Drop all entries for ``sensor_id`` with ``frame_id < max_frame_id``.

        When ``clustered_only`` is True the delete is additionally
        restricted to rows with ``is_clustered == True``, leaving
        un-clustered tracking history intact. This is the clustering-path
        cleanup: only history that has already been folded into a cluster
        rep is superseded, whereas raw un-clustered rows may still be
        needed by a future clustering pass.

        Returns the number of entries actually removed (best-effort;
        backends that cannot cheaply count may return ``-1``).
        """

    @abstractmethod
    def apply_relabel(
        self,
        updates: Sequence[TrackingRelabelUpdate],
        source_clustered: bool = False,
    ) -> int:
        """Move tracked objects between buckets per cluster assignments.

        For each ``(old_oid, stream_id, frame_id, new_oid)`` tuple,
        locate the matching source object, rewrite its ``object_id`` to
        ``new_oid``, set ``is_clustered=True``, and re-bucket it.
        Returns the number of entries actually moved.

        ``source_clustered`` selects which source rows are eligible,
        matching the two clustering passes:

        * ``False`` (default) -- the fresh-clustering pass: only rows
          currently ``is_clustered == False`` are relabelled (raw
          entries being folded into a cluster rep for the first time).
        * ``True`` -- the retired-cluster cascade: only rows already
          ``is_clustered == True`` are migrated from a retired rep to the
          surviving rep.

        Updates that match no eligible row are skipped (not fatal); the
        batch is **not** aborted. The return value counts only the rows
        actually moved, so ``moved < len(updates)`` signals skipped
        misses.
        """

    @abstractmethod
    def clear(self, object_id: Optional[str] = None) -> None:
        """Remove tracking entries.

        If ``object_id`` is provided, drop only that object's data.
        Otherwise drop everything.
        """

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    @abstractmethod
    def lookup(
        self,
        object_id: str,
        sensor_id: str,
        frame_id: int,
    ) -> Optional[TrackedObject]:
        """Fetch a single tracked object by exact composite key.

        Returns ``None`` if no matching entry exists. Matches the
        existing ``_lookup_tracked_object`` helper's semantics.

        Backends that enforce an owner-only-reads rule (currently
        :class:`InMemoryTrackingStore`) may raise from non-owner
        processes; callers that genuinely need cross-process lookups
        should use :meth:`lookup_anywhere` instead.
        """

    def lookup_anywhere(
        self,
        object_id: str,
        sensor_id: str,
        frame_id: int,
        timeout: float = 10.0,
    ) -> Optional[TrackedObject]:
        """Like :meth:`lookup` but safe to call from any process.

        Backends where :meth:`lookup` already works from any process
        (SQLite, Redis, etc.) inherit this default, which is a direct
        delegate. The in-memory backend overrides this with a
        synchronous request/reply RPC over its internal owner-side
        drainer so callers in the maintenance worker / search worker
        processes can resolve tracking metadata without going through
        an ad-hoc bouncer thread in the FastAPI parent.

        ``timeout`` only matters for the RPC path; SQL backends ignore
        it because the lookup is local to the caller's connection.
        Raises :class:`TimeoutError` if the owner drainer doesn't
        respond within ``timeout`` seconds.
        """
        return self.lookup(object_id, sensor_id, frame_id)

    @abstractmethod
    def get_by_object(
        self,
        object_id: str,
        stream_id: Optional[str] = None,
    ) -> Dict[str, List[TrackedObject]]:
        """Return all tracked entries for ``object_id``.

        If ``stream_id`` is provided, narrows to that stream. Per-stream
        lists are sorted by ``frame_id`` ascending. Returns ``{}`` for
        unknown ``object_id``.
        """

    @abstractmethod
    def get_all(self) -> Dict[str, Dict[str, List[TrackedObject]]]:
        """Return everything, sorted by ``frame_id`` within each stream.

        Used by ``/tracked_objects`` debug endpoint and the
        ``/dump/tracking`` exporter.
        """

    @abstractmethod
    def snapshot(self) -> Dict[str, Dict[str, List[TrackedObject]]]:
        """Return a serialisable snapshot for dumping.

        For in-memory backends this is the same view as :meth:`get_all`.
        SQL backends may materialise a consistent snapshot via a single
        ``SELECT`` so the result represents one point in time even
        under concurrent writes.
        """

    # ------------------------------------------------------------------
    # Clustering-side scans (used by the merge-ready branch only)
    # ------------------------------------------------------------------

    @abstractmethod
    def unclustered_keys(self) -> Set[TrackingKey]:
        """Return ``(int(object_id), stream_id, frame_id)`` for every
        un-clustered entry currently held.

        Backends should skip entries whose ``object_id`` is not a
        decimal integer (those have never been promoted to a cluster
        bucket) to preserve the ``tracking_coverage`` semantics the
        clustering loop expects.
        """

    @abstractmethod
    def unclustered_bboxes_in_frame_range(
        self,
        min_frame: int,
        max_frame: int,
    ) -> Dict[TrackingKey, Dict[str, float]]:
        """Return bbox dicts (``leftX``, ``topY``, ``rightX``,
        ``bottomY``) for every un-clustered entry whose ``frame_id``
        falls within ``[min_frame, max_frame]`` (inclusive both ends).

        Matches the per-tick ``tracking`` dict built by
        ``_run_online_clustering_batch`` on the merge-ready branch.
        """

    @abstractmethod
    def build_retired_relabels(
        self,
        retired_to_surviving: Mapping[int, int],
        cluster_id_to_oid: Callable[[int], str],
    ) -> List[TrackingRelabelUpdate]:
        """Translate cluster-merge cascades into per-entry relabel ops.

        ``retired_to_surviving`` maps retired cluster ids to the
        surviving cluster id they merged into.
        ``cluster_id_to_oid`` resolves a cluster id to the representative
        ``object_id`` (string of digits) used as a bucket key. The
        merge-ready branch passes
        ``lambda cid: cluster_id_to_object_id(cid, archive)``; the main
        branch never calls this method.

        Returns updates ready to feed into :meth:`apply_relabel`. The
        store implementation owns this so it can scan the buckets it
        owns directly (no need to ship the dict to a free function).
        """

    # ------------------------------------------------------------------
    # Diagnostics
    # ------------------------------------------------------------------

    @abstractmethod
    def stats(self) -> Dict[str, int]:
        """Return cheap counters for ``/tracked_objects/stats/summary``.

        Required keys:
          * ``total_objects`` -- number of distinct ``object_id`` buckets.
          * ``total_streams`` -- total number of ``(object_id, stream_id)``
            pairs across the store.
        Backends may include additional implementation-specific keys.
        """

    def health_check(self) -> Dict[str, str]:
        """Return one entry per backend resource the store wants the
        ``/health/ready`` probe to assert is alive.

        Each value is either ``"ok"`` or a short error string. The
        default returns ``{}`` -- "no backend-managed resources to
        check" -- which is correct for stores that don't run their own
        threads or own connections (the SQLite backend will look like
        that once we open per-process connections lazily inside
        :meth:`attach_post_fork`). Backends with their own daemon
        threads should override; in-memory does, reporting per-drainer
        liveness.
        """
        return {}
