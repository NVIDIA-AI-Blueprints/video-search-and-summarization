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

"""In-memory backend for :class:`TrackingStore`.

This is Phase 1: behavioural parity with the pre-refactor code
(``object_tracking_db`` + ``object_tracking_lock`` +
``_tracked_object_queue`` + ``_tracked_object_delete_queue`` and the
``_tracked_object_drainer`` daemon thread), repackaged behind the
``TrackingStore`` base class.

Owner vs non-owner
------------------

The store is constructed in the parent FastAPI process (the *owner*).
Child processes (Kafka consumer, maintenance worker, secondary
embedding generator, ...) inherit it via ``fork`` (or get a fresh
pickled copy under ``spawn``). The store records the owner pid at
construction time; method dispatch then routes calls based on
``os.getpid()``:

* **Owner writes** mutate the dict directly under the threading lock.
* **Non-owner writes** push the payload onto an ``mp.Queue``. The
  owner-side drainer (started by :meth:`start_drainers`, *after*
  children have been spawned) consumes the queues and applies the
  same mutations.
* **Owner reads** read the dict directly under the lock.
* **Non-owner reads** raise :class:`RuntimeError`. The current code
  path never performs cross-process reads; if a future caller needs
  one, that's the signal to graduate to Phase 2 (SQLite WAL) where
  every process can read.

This matches today's behaviour bit-for-bit:

* Kafka consumer process used ``_tracked_object_queue.put(obj)`` ->
  drained by ``_tracked_object_drainer`` -> applied via
  ``insert_tracked_object``. With this store the consumer calls
  ``store.insert(obj)``, dispatch sees it's the non-owner, queues.
* Maintenance worker used
  ``_tracked_object_delete_queue.put(mapping)`` -> drained by the
  same thread -> applied via
  ``delete_old_entries_from_tracked_object_db``. With the store the
  worker calls ``store.delete_below(sid, fid)``, dispatch queues.
* The main-process compression endpoint also called
  ``_tracked_object_delete_queue.put(...)`` even though it lived in
  the owner. With the store the same call goes through
  ``store.delete_below(...)`` and dispatch applies it inline (no
  unnecessary queue hop for owner-local deletes).
"""

from __future__ import annotations

import logging
import os
import threading
from queue import Empty as QueueEmpty
import multiprocessing
from typing import (
    Any,
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
from tracking_store.base import (
    SensorIdToMaxFrameIdMap,
    TrackingKey,
    TrackingRelabelUpdate,
    TrackingStore,
)


logger = logging.getLogger(__name__)


# Drainer poll cadence: long enough not to burn CPU, short enough to
# notice the shutdown event promptly during interpreter teardown.
_DRAINER_POLL_TIMEOUT_S = 0.5


class InMemoryTrackingStore(TrackingStore):
    """Dict-and-lock backend with multiprocessing-queue write fan-in.

    Concrete :class:`tracking_store.base.TrackingStore`. See the
    module docstring for the owner/non-owner dispatch rules.
    """

    def __init__(self) -> None:
        # Bucket: object_id -> stream_id -> list of TrackedObjects.
        # Insert path preserves arrival order; reads sort by frame_id.
        self._db: Dict[str, Dict[str, List[TrackedObject]]] = {}
        self._lock = threading.Lock()

        # Cross-process write fan-in. Created in the parent before any
        # child is spawned so children inherit usable queue handles.
        self._insert_queue: multiprocessing.Queue = multiprocessing.Queue()
        self._delete_queue: multiprocessing.Queue = multiprocessing.Queue()
        # Relabel queue is reserved for the merge-ready branch (clustering
        # process). Wired in now so the migration doesn't reshape the
        # protocol later; on main it stays empty.
        self._relabel_queue: multiprocessing.Queue = multiprocessing.Queue()

        # Lookup-RPC request queue. Non-owner callers post
        # ``((object_id, sensor_id, frame_id), reply_queue)`` here; the
        # owner-side ``ts-lookup-drainer`` thread does the dict lookup
        # under the lock and puts the result on the caller-supplied
        # reply queue. Lets producers in maintenance/search workers
        # call :meth:`lookup_anywhere` without bouncing through an
        # ad-hoc resolver thread in main.py.
        self._lookup_request_queue: multiprocessing.Queue = multiprocessing.Queue()

        # Dispatch root: anyone running in this pid is the owner.
        self._owner_pid: int = os.getpid()

        # Drainer threads created lazily by start_drainers(). Kept as
        # a list so shutdown() can join them uniformly.
        self._drainer_threads: List[threading.Thread] = []
        self._shutdown_event = threading.Event()
        self._lifecycle_lock = threading.Lock()
        self._drainers_started = False
        self._shutdown_done = False

    # ------------------------------------------------------------------
    # Dispatch helpers
    # ------------------------------------------------------------------

    def _is_owner(self) -> bool:
        """True iff the caller runs in the process that built the store.

        Re-checked on every call instead of cached so the in-memory
        store can never accidentally treat a forked child as the
        owner (cached booleans survive ``fork`` and lie).
        """
        return os.getpid() == self._owner_pid

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def attach_post_fork(self) -> None:
        # The in-memory backend has no per-process resources to open.
        # Children just keep using the inherited mp.Queue handles.
        return

    def start_drainers(self) -> None:
        """Start the queue-draining threads.

        Idempotent. Must be called from the owner process **after** any
        child processes have been spawned. (Starting threads before
        forking children is the well-known "thread-after-fork" footgun;
        we replicate the existing main.py ordering: spawn first, then
        drainers.)
        """
        if not self._is_owner():
            # Children must not start drainer threads against the
            # owner's queues; quietly no-op so callers can be
            # symmetric across processes.
            return

        with self._lifecycle_lock:
            if self._drainers_started:
                return
            self._drainers_started = True

            # Four small daemon threads, one per queue. Per-queue
            # threads avoid the starvation pattern of the old single
            # thread that did blocking get() on inserts and only
            # polled deletes when an insert happened to arrive.
            for name, queue, handler in (
                ("ts-insert-drainer", self._insert_queue, self._apply_insert_payload),
                ("ts-delete-drainer", self._delete_queue, self._apply_delete_payload),
                ("ts-relabel-drainer", self._relabel_queue, self._apply_relabel_payload),
                ("ts-lookup-drainer", self._lookup_request_queue, self._handle_lookup_request),
            ):
                thread = threading.Thread(
                    target=self._drain_loop,
                    args=(name, queue, handler),
                    name=name,
                    daemon=True,
                )
                thread.start()
                self._drainer_threads.append(thread)
            logger.info(
                "InMemoryTrackingStore drainer threads started (owner pid=%d)",
                self._owner_pid,
            )

    def shutdown(self) -> None:
        with self._lifecycle_lock:
            if self._shutdown_done:
                return
            self._shutdown_done = True
            self._shutdown_event.set()

        # Daemon threads will die with the interpreter anyway; the join
        # is best-effort and bounded so a stuck drainer can't block
        # shutdown.
        for thread in self._drainer_threads:
            thread.join(timeout=_DRAINER_POLL_TIMEOUT_S * 2)

    # ------------------------------------------------------------------
    # Drainer plumbing
    # ------------------------------------------------------------------

    def _drain_loop(
        self,
        name: str,
        queue: "multiprocessing.Queue",
        handler: Callable[[Any], None],
    ) -> None:
        logger.debug("%s starting", name)
        while not self._shutdown_event.is_set():
            try:
                payload = queue.get(timeout=_DRAINER_POLL_TIMEOUT_S)
            except QueueEmpty:
                continue
            except (OSError, EOFError):
                # mp.Queue gets closed during interpreter teardown.
                logger.debug("%s queue closed", name)
                return
            except Exception:  # pragma: no cover -- defensive
                logger.exception("%s unexpected error fetching payload", name)
                continue

            try:
                handler(payload)
            except Exception:
                logger.exception("%s failed to apply payload (dropping): %r", name, payload)
        logger.debug("%s exiting", name)

    def _apply_insert_payload(self, payload: TrackedObject) -> None:
        self._insert_direct(payload)

    def _apply_delete_payload(self, payload: Tuple[bool, SensorIdToMaxFrameIdMap]) -> None:
        clustered_only, mapping = payload
        for sensor_id, max_frame_id in mapping.items():
            self._delete_below_direct(sensor_id, max_frame_id, clustered_only)

    def _apply_relabel_payload(self, payload: Tuple[bool, Sequence[TrackingRelabelUpdate]]) -> None:
        source_clustered, updates = payload
        self._apply_relabel_direct(updates, source_clustered)

    def _handle_lookup_request(self, payload: Tuple[Tuple[str, str, int], Any]) -> None:
        """Owner-side handler for :meth:`lookup_anywhere` RPC requests.

        Payload is ``((object_id, sensor_id, frame_id), reply_conn)``
        where ``reply_conn`` is the write-end of a
        :class:`multiprocessing.Connection` (one side of a Pipe). The
        handler runs ``lookup()`` and sends ``("ok", TrackedObject |
        None)`` back through the connection, or ``("err", repr(exc))``
        if something blew up. The caller-side :meth:`lookup_anywhere`
        translates that into a return value or a ``RuntimeError``.

        We use a Pipe rather than a Queue because :class:`mp.Queue`
        instances cannot be pickled across another Queue (they're
        only shareable via fork inheritance), whereas
        :class:`mp.Connection` is explicitly designed to be sent
        between processes.

        Failure handling: if posting the response fails (caller died
        and closed its end), we swallow the error so the drainer
        keeps servicing other requests.
        """
        try:
            key, reply_conn = payload
            object_id, sensor_id, frame_id = key
        except Exception:
            logger.exception("ts-lookup-drainer: malformed payload, skipping")
            return

        try:
            try:
                result = self.lookup(object_id, sensor_id, frame_id)
                reply_conn.send(("ok", result))
            except Exception as e:
                logger.exception(
                    "ts-lookup-drainer: lookup(%s, %s, %s) failed",
                    object_id,
                    sensor_id,
                    frame_id,
                )
                try:
                    reply_conn.send(("err", repr(e)))
                except Exception:  # pragma: no cover -- caller's conn is gone
                    pass
        finally:
            try:
                reply_conn.close()
            except Exception:  # pragma: no cover -- best effort
                pass

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------

    def insert(self, obj: TrackedObject) -> None:
        if self._is_owner():
            self._insert_direct(obj)
            return
        # Child process: queue and let the owner's drainer apply.
        # put() (blocking) instead of put_nowait() so a slow owner
        # cannot silently drop tracking history; matches the existing
        # ``_tracked_object_queue.put(...)`` semantics in main.py.
        self._insert_queue.put(obj)

    def insert_many(self, objs: Sequence[TrackedObject]) -> None:
        if self._is_owner():
            # Single lock acquisition for the batch; matches the
            # existing /load/tracking implementation which held the
            # lock once around the whole rebuild loop.
            with self._lock:
                for obj in objs:
                    self._insert_locked(obj)
            return
        for obj in objs:
            self._insert_queue.put(obj)

    def delete_below(self, sensor_id: str, max_frame_id: int, clustered_only: bool = False) -> int:
        if self._is_owner():
            return self._delete_below_direct(sensor_id, max_frame_id, clustered_only)
        # Match today's payload shape: a {sensor_id: max_frame_id}
        # mapping rather than per-key tuples. Lets future callers
        # batch multiple sensors in one queue hop if they want, the
        # way ``_perform_compression`` already does today. The
        # ``clustered_only`` flag rides alongside the mapping so the
        # owner-side drainer applies the same predicate.
        self._delete_queue.put((clustered_only, {sensor_id: max_frame_id}))
        # Non-owner cannot know the post-apply count.
        return -1

    def apply_relabel(
        self,
        updates: Sequence[TrackingRelabelUpdate],
        source_clustered: bool = False,
    ) -> int:
        if self._is_owner():
            return self._apply_relabel_direct(updates, source_clustered)
        # ``source_clustered`` rides alongside the updates so the owner-side
        # drainer applies the same source-state filter.
        self._relabel_queue.put((source_clustered, list(updates)))
        return -1

    def clear(self, object_id: Optional[str] = None) -> None:
        if not self._is_owner():
            # Clearing from a child process would race with the
            # drainer; today's code path never does this, so we keep
            # it explicit rather than introducing yet another queue.
            raise RuntimeError(
                "InMemoryTrackingStore.clear() must be called from the "
                "owner process; use the FastAPI /reset endpoint."
            )
        with self._lock:
            if object_id is None:
                self._db.clear()
            else:
                self._db.pop(object_id, None)

    # ------------------------------------------------------------------
    # Direct mutation helpers (always run on the owner thread, lock held)
    # ------------------------------------------------------------------

    def _insert_direct(self, obj: TrackedObject) -> None:
        with self._lock:
            self._insert_locked(obj)

    def _insert_locked(self, obj: TrackedObject) -> None:
        streams = self._db.setdefault(obj.object_id, {})
        bucket = streams.setdefault(obj.stream_id, [])
        bucket.append(obj)

    def _delete_below_direct(self, sensor_id: str, max_frame_id: int, clustered_only: bool = False) -> int:
        removed = 0
        with self._lock:
            # Mirrors the existing
            # ``delete_old_entries_from_tracked_object_db`` semantics:
            # keep only entries with frame_id >= max_frame_id. (Yes,
            # the helper's name says "below" but its predicate is
            # ``>= max_frame_id``; preserving that exact behaviour
            # here so the migration is a no-op.)
            #
            # ``clustered_only`` additionally keeps every un-clustered row
            # regardless of frame_id, so only clustered history below the
            # threshold is dropped.
            for streams in self._db.values():
                if sensor_id not in streams:
                    continue
                old = streams[sensor_id]
                kept = [obj for obj in old if obj.frame_id >= max_frame_id or (clustered_only and not obj.is_clustered)]
                removed += len(old) - len(kept)
                streams[sensor_id] = kept
        return removed

    def _apply_relabel_direct(
        self,
        updates: Sequence[TrackingRelabelUpdate],
        source_clustered: bool = False,
    ) -> int:
        moved = 0
        missed = 0
        with self._lock:
            for old_oid, stream_id, frame_id, new_oid in updates:
                src_streams = self._db.get(old_oid)
                src_bucket = src_streams.get(stream_id) if src_streams else None
                # Find the first matching frame in the eligible source state
                # and move it. Matches ``cluster_manager.apply_tracking_relabel``
                # behaviour of moving (not copying) the single matching entry.
                # ``source_clustered`` selects the pass: un-clustered rows for
                # fresh clustering, already-clustered rows for the retired-rep
                # cascade.
                hit_idx: Optional[int] = None
                if src_bucket:
                    for idx, obj in enumerate(src_bucket):
                        if obj.frame_id == frame_id and bool(obj.is_clustered) == source_clustered:
                            hit_idx = idx
                            break
                if hit_idx is None:
                    # No eligible entry at this key: skip, don't abort the batch.
                    missed += 1
                    continue
                hit = src_bucket.pop(hit_idx)
                hit.is_clustered = True
                hit.object_id = new_oid
                dst_streams = self._db.setdefault(new_oid, {})
                dst_bucket = dst_streams.setdefault(stream_id, [])
                # Mirror the SQLite ``UPDATE OR REPLACE`` on the (object_id,
                # stream_id, frame_id, is_clustered=1) UNIQUE key: drop any
                # existing clustered row at the destination frame before adding
                # the moved (now clustered) one.
                dst_bucket[:] = [o for o in dst_bucket if not (o.frame_id == frame_id and o.is_clustered)]
                dst_bucket.append(hit)
                moved += 1
        if missed:
            logger.warning(
                "apply_relabel: %d/%d updates matched no %s source row (skipped)",
                missed,
                len(updates),
                "clustered" if source_clustered else "un-clustered",
            )
        return moved

    # ------------------------------------------------------------------
    # Reads (owner only)
    # ------------------------------------------------------------------

    def _require_owner(self, op: str) -> None:
        if not self._is_owner():
            raise RuntimeError(
                f"InMemoryTrackingStore.{op}() can only run in the owner "
                "process (pid {owner}); current pid {here}. Switch to "
                "a server-backed store (SQLite/Redis) to read from "
                "child processes.".format(owner=self._owner_pid, here=os.getpid())
            )

    def lookup(
        self,
        object_id: str,
        sensor_id: str,
        frame_id: int,
    ) -> Optional[TrackedObject]:
        self._require_owner("lookup")
        with self._lock:
            streams = self._db.get(object_id)
            if not streams:
                return None
            bucket = streams.get(sensor_id)
            if not bucket:
                return None
            # Linear scan to preserve today's behaviour
            # (``_lookup_tracked_object``). Buckets are typically short.
            # A clustered and un-clustered entry can coexist for the same
            # frame_id; prefer the clustered one (mirrors the SQLite backend's
            # ``ORDER BY is_clustered DESC``), falling back to un-clustered.
            fallback: Optional[TrackedObject] = None
            for obj in bucket:
                if obj.frame_id == frame_id:
                    if obj.is_clustered:
                        return obj
                    if fallback is None:
                        fallback = obj
            return fallback

    def lookup_anywhere(
        self,
        object_id: str,
        sensor_id: str,
        frame_id: int,
        timeout: float = 10.0,
    ) -> Optional[TrackedObject]:
        """Cross-process lookup that survives owner-only-reads.

        Owner-process callers shortcut directly to :meth:`lookup` --
        no detour. Non-owner callers create a fresh per-call
        :class:`multiprocessing.Pipe`, post the request together with
        the pipe's write-end on ``_lookup_request_queue``, and block
        reading from the pipe's read-end until the owner-side
        ``ts-lookup-drainer`` thread responds.

        Pipe (not Queue) is used for the reply channel because Python
        forbids pickling a Queue over another Queue (queues can only
        be shared via fork inheritance), whereas Connection objects
        are designed to be transferred between processes.
        """
        if self._is_owner():
            return self.lookup(object_id, sensor_id, frame_id)

        # duplex=False: the caller only reads, the owner only writes.
        read_conn, write_conn = multiprocessing.Pipe(duplex=False)
        try:
            self._lookup_request_queue.put(((object_id, sensor_id, frame_id), write_conn))
            # IMPORTANT: do NOT close write_conn here. mp.Queue.put()
            # is asynchronous -- a background feeder thread pickles the
            # payload onto the pipe to the owner, and pickling a
            # Connection requires ``os.dup(fd)`` on the underlying fd.
            # Closing our handle before the feeder finishes the dup
            # races with it and raises ``OSError: [Errno 9] Bad file
            # descriptor`` from the feeder. The handle is released
            # implicitly when this function returns, by which point
            # the owner has already processed the message (we just
            # recv'd its response below), so the feeder's dup is long
            # done.

            if not read_conn.poll(timeout):
                raise TimeoutError(
                    f"InMemoryTrackingStore.lookup_anywhere: owner drainer "
                    f"did not respond within {timeout}s for "
                    f"({object_id!r}, {sensor_id!r}, {frame_id})"
                )
            status, payload = read_conn.recv()
            if status == "ok":
                return payload
            raise RuntimeError(f"InMemoryTrackingStore.lookup_anywhere: owner-side lookup " f"failed: {payload}")
        finally:
            try:
                read_conn.close()
            except Exception:  # pragma: no cover -- best effort
                pass
            try:
                # Safe to close now -- the feeder thread has finished
                # pickling (proven by the fact that the owner replied),
                # and the owner-side dup of the fd is independent.
                write_conn.close()
            except Exception:  # pragma: no cover -- best effort
                pass

    def get_by_object(
        self,
        object_id: str,
        stream_id: Optional[str] = None,
    ) -> Dict[str, List[TrackedObject]]:
        self._require_owner("get_by_object")
        with self._lock:
            streams = self._db.get(object_id)
            if not streams:
                return {}
            if stream_id is not None:
                bucket = streams.get(stream_id)
                if not bucket:
                    return {}
                return {stream_id: sorted(bucket, key=lambda o: o.frame_id)}
            return {sid: sorted(list(bucket), key=lambda o: o.frame_id) for sid, bucket in streams.items()}

    def get_all(self) -> Dict[str, Dict[str, List[TrackedObject]]]:
        self._require_owner("get_all")
        with self._lock:
            return {
                oid: {sid: sorted(list(bucket), key=lambda o: o.frame_id) for sid, bucket in streams.items()}
                for oid, streams in self._db.items()
            }

    def snapshot(self) -> Dict[str, Dict[str, List[TrackedObject]]]:
        # Same shape as get_all() for the in-memory backend; the SQL
        # backends will use a real read transaction to obtain a
        # consistent view.
        return self.get_all()

    # ------------------------------------------------------------------
    # Clustering scans (used only by the merge-ready branch on main today)
    # ------------------------------------------------------------------

    def unclustered_keys(self) -> Set[TrackingKey]:
        self._require_owner("unclustered_keys")
        keys: Set[TrackingKey] = set()
        with self._lock:
            for oid, streams in self._db.items():
                try:
                    oid_int = int(oid)
                except (TypeError, ValueError):
                    # Skip non-numeric ids rather than poisoning the
                    # whole scan; matches the defensive coercion used
                    # in the merge-ready branch.
                    continue
                for sid, bucket in streams.items():
                    for obj in bucket:
                        if obj.is_clustered:
                            continue
                        keys.add((oid_int, sid, obj.frame_id))
        return keys

    def unclustered_bboxes_in_frame_range(
        self,
        min_frame: int,
        max_frame: int,
    ) -> Dict[TrackingKey, Dict[str, float]]:
        self._require_owner("unclustered_bboxes_in_frame_range")
        result: Dict[TrackingKey, Dict[str, float]] = {}
        with self._lock:
            for oid, streams in self._db.items():
                try:
                    oid_int = int(oid)
                except (TypeError, ValueError):
                    continue
                for sid, bucket in streams.items():
                    for obj in bucket:
                        if obj.is_clustered:
                            continue
                        if not (min_frame <= obj.frame_id <= max_frame):
                            continue
                        result[(oid_int, sid, obj.frame_id)] = {
                            "leftX": obj.leftX,
                            "topY": obj.topY,
                            "rightX": obj.rightX,
                            "bottomY": obj.bottomY,
                        }
        return result

    def build_retired_relabels(
        self,
        retired_to_surviving: Mapping[int, int],
        cluster_id_to_oid: Callable[[int], str],
    ) -> List[TrackingRelabelUpdate]:
        self._require_owner("build_retired_relabels")
        # Pre-resolve the retired -> new_oid mapping so the per-entry
        # loop is just a dict lookup.
        resolved: Dict[str, str] = {}
        for retired_cid, surviving_cid in retired_to_surviving.items():
            try:
                old_oid = cluster_id_to_oid(int(retired_cid))
                new_oid = cluster_id_to_oid(int(surviving_cid))
            except Exception:
                logger.exception(
                    "build_retired_relabels: cluster_id_to_oid failed for " "%r -> %r",
                    retired_cid,
                    surviving_cid,
                )
                continue
            if old_oid == new_oid:
                continue
            resolved[old_oid] = new_oid

        updates: List[TrackingRelabelUpdate] = []
        with self._lock:
            for old_oid, new_oid in resolved.items():
                streams = self._db.get(old_oid)
                if not streams:
                    continue
                for sid, bucket in streams.items():
                    for obj in bucket:
                        if not obj.is_clustered:
                            # Only clustered members move on a merge; fresh
                            # rows are left for the next clustering tick.
                            continue
                        updates.append((old_oid, sid, obj.frame_id, new_oid))
        return updates

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------

    def stats(self) -> Dict[str, int]:
        self._require_owner("stats")
        with self._lock:
            total_objects = len(self._db)
            total_streams = sum(len(streams) for streams in self._db.values())
            total_entries = sum(len(bucket) for streams in self._db.values() for bucket in streams.values())
        return {
            "total_objects": total_objects,
            "total_streams": total_streams,
            "total_entries": total_entries,
        }

    # ------------------------------------------------------------------
    # Health check
    # ------------------------------------------------------------------

    def health_check(self) -> Dict[str, str]:
        # Only the owner can answer for the drainer threads (they only
        # exist there). Non-owners just report that they're up.
        if not self._is_owner():
            return {}
        if not self._drainers_started:
            # Owner that never called start_drainers() -- legitimate
            # during early startup, but if /health/ready hits this it
            # signals a wiring bug.
            return {"tracking_store:drainers": "not started"}
        result: Dict[str, str] = {}
        for thread in self._drainer_threads:
            key = f"tracking_store:{thread.name}"
            result[key] = "ok" if thread.is_alive() else "dead"
        return result

    # ------------------------------------------------------------------
    # Test-only introspection helpers. Not part of the protocol; only
    # the in-memory tests touch these.
    # ------------------------------------------------------------------

    def _owner_pid_for_tests(self) -> int:
        return self._owner_pid

    def _force_owner_for_tests(self) -> None:
        """Re-stamp the owner pid to the current process.

        Lets the conformance tests reuse the same store across forked
        children when needed.
        """
        self._owner_pid = os.getpid()


__all__ = ["InMemoryTrackingStore"]
