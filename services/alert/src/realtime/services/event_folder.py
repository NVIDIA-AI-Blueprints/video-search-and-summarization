#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

"""
Background job that folds realtime evidence into durable events.

Each cycle re-folds a sliding tail window from the raw evidence rather than
consuming a stream, which is what keeps the job stateless: nothing is lost on
restart, late evidence inside the window is absorbed without special handling,
and a cycle that dies half-done is repaired by simply running the next one.

Two properties are load-bearing and easy to break by accident:

*Scanning is per group, not per window.* A single query over every camera runs
into the same document cap that sizes a user's one-camera request, and at a few
hundred cameras a ten-minute window reaches it — after which results are
silently truncated, oldest first, which is exactly the long-running condition
this feature exists to keep whole. Groups are walked with a composite
aggregation, which paginates deterministically, and each group is fetched on its
own.

*The whole window is re-folded, per group.* Re-reading only the settled tail
would be cheaper, but it narrows how late evidence can arrive and still be
merged: a chunk older than the last closed event would never be read again, so
it would belong to no event at all. Per-group scanning already removes the
document-cap ceiling that the tail optimisation was also solving, and the full
re-fold measures under one percent of a cycle at the sizes this is built for,
so the tolerance is worth more than the saving. Evidence arriving up to
``fold_window_seconds`` after its event ended is therefore still merged into it.

*Lifecycle travels as an instant, not as a state.* Each document carries
``settlesAt`` — the moment after which no cycle can reach it — rather than an
``open``/``closed`` field. A state would be a function of *now*, and an event
becomes immutable while nothing is writing, so a stored one would say ``open``
for the record's life with no writer left to correct it. ``settlesAt`` is
recomputed on every rewrite, so the last value written is already the right
one, and a consumer in another service needs none of the bounds below to read
it.
"""

import hashlib
import logging
import os
import socket
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from .event_store import DEFAULT_FOLD_WINDOW_SECONDS
from .incident_service import (
    _parse_ts,
    _validate_consolidation,
    realtime_confirmed_clauses,
)

logger = logging.getLogger(__name__)

DEFAULT_FOLD_INTERVAL_SECONDS = 30.0
# Matches ``IncidentService.consolidation_bounds``; the two must not disagree
# about what an absent key means.
DEFAULT_MAX_EVENT_DURATION_SECONDS = 300.0
DEFAULT_RETENTION_DAYS = 7.0

# Ceiling on chunks fetched for one group in one cycle. Per-group rather than
# per-window, so it bounds a pathological single camera without capping the
# fleet; hitting it is reported, never silently absorbed.
_GROUP_FETCH_CAP = 5000

# Groups requested per composite page.
_GROUP_PAGE_SIZE = 200

try:
    from metrics import PROMETHEUS_ENABLED
    if PROMETHEUS_ENABLED:
        from metrics.prometheus_metrics import (  # type: ignore
            FOLD_CYCLES_SKIPPED,
            FOLD_DURATION,
            FOLD_EVENTS_PERSISTED,
            FOLD_EVENTS_SUPERSEDED,
            FOLD_EVENTS_PURGED,
            FOLD_ALIASES_WRITTEN,
            FOLD_FRESHNESS_UNPUBLISHED,
            FOLD_CYCLES_ABORTED,
            FOLD_LAST_COMPLETED,
            FOLD_TRUNCATED,
        )
    else:  # pragma: no cover - metrics disabled
        FOLD_CYCLES_SKIPPED = FOLD_DURATION = FOLD_EVENTS_PURGED = None
        FOLD_EVENTS_PERSISTED = FOLD_EVENTS_SUPERSEDED = FOLD_TRUNCATED = None
        FOLD_CYCLES_ABORTED = FOLD_LAST_COMPLETED = None
        FOLD_FRESHNESS_UNPUBLISHED = FOLD_ALIASES_WRITTEN = None
except Exception:  # pragma: no cover - metrics module absent
    FOLD_CYCLES_SKIPPED = FOLD_DURATION = FOLD_EVENTS_PURGED = None
    FOLD_EVENTS_PERSISTED = FOLD_EVENTS_SUPERSEDED = FOLD_TRUNCATED = None
    FOLD_CYCLES_ABORTED = FOLD_LAST_COMPLETED = None
    FOLD_FRESHNESS_UNPUBLISHED = FOLD_ALIASES_WRITTEN = None


def _count_abort(reason: str) -> None:
    """Record a cycle whose output cannot be trusted, and why.

    Labelled rather than one counter per cause: an operator paging on fold lag
    needs to tell lock contention from an unreachable cluster, and three label
    values carry no cardinality risk. The lock-loss paths had no counter at
    all, while the strictly less severe "the work landed but the freshness
    write did not" had its own — which is the wrong way round.
    """
    if FOLD_CYCLES_ABORTED is not None:
        FOLD_CYCLES_ABORTED.labels(reason=reason).inc()


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _at_or_after(value: Any, boundary: datetime) -> bool:
    """Whether a document timestamp is at or after an instant.

    Parsed rather than string-compared. The documents this reads arrive through
    Logstash, not through this service's own writer, so their timestamps are
    not guaranteed to carry milliseconds — and ``...T12:00:00Z`` sorts *after*
    ``...T12:00:00.000Z`` as text, putting an event on the wrong side of a
    boundary it is exactly on.
    """
    moment = _parse_ts(value)
    return moment is not None and moment >= boundary


def _positive(name: str, value: Any, allow_none: bool = False) -> Optional[float]:
    """A configuration value that must be a real number greater than zero.

    Coercion is the failure mode being avoided here rather than a convenience:
    ``retention_days: 0`` read as falsey disables the reaper silently, and a
    negative value puts the cutoff in the future and purges the whole store.
    Neither is a plausible intent, so both are refused instead of interpreted.
    """
    if value is None:
        if allow_none:
            return None
        raise ValueError(f"{name} must be set")
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{name} must be a number, got {value!r}") from None
    if number != number or number in (float("inf"), float("-inf")):
        raise ValueError(f"{name} must be finite, got {value!r}")
    if number <= 0:
        raise ValueError(
            f"{name} must be greater than zero, got {number}. Leaving it unset "
            f"applies the default; there is no value that switches it off."
        )
    return number


def validate_persistence_config(
    persistence: Dict[str, Any], consolidation: Dict[str, Any],
) -> Tuple[float, float]:
    """Reject a persistence configuration that can never work.

    Returns ``(fold_window_seconds, rewrite_horizon_seconds)``, both validated.
    The horizon is how far back a cycle can still reach — the window plus the
    lookback margin. Callers take both from here rather than re-deriving
    either: a caller that converted its own ``fold_window_seconds`` before
    calling this would raise a ``TypeError`` on ``null``, which is not the
    exception either caller is written to handle.

    Called before Elasticsearch is touched, because the two failures need
    opposite handling: an unreachable cluster is transient and worth retrying,
    while a window narrower than one event never becomes correct and must stop
    the process rather than be retried forever behind a healthy readiness probe.
    """
    # The same checks ``IncidentService`` makes, run here rather than left to
    # its constructor. That construction happens on the supervisor thread, and
    # a ValueError there kills the thread and nothing else — leaving an
    # instance that reports healthy, has persistence switched on, and folds
    # nothing for ever.
    _validate_consolidation(consolidation)
    _positive("fold_interval_seconds", persistence.get("fold_interval_seconds", 30))
    # Deliberately not optional. A persisted event carries a clone of the
    # representative chunk's model text, and it reaches the UI through the
    # Video Analytics API — which, like this service, has no authorization
    # boundary. So "keep everything for ever" is not a configuration this
    # feature can offer; unbounded retention would need the model text
    # stripped first, which is a different change.
    _positive("retention_days", persistence.get("retention_days", 7))
    # Absent and explicitly null are different answers. Every other consumer of
    # this key defaults it to 300, so an absent key must not be the one thing
    # that takes the instance down — only an operator who wrote ``null``, which
    # genuinely says "events are unbounded", is refused.
    cap = (
        consolidation["max_event_duration_seconds"]
        if "max_event_duration_seconds" in consolidation
        else DEFAULT_MAX_EVENT_DURATION_SECONDS
    )
    window = _positive("fold_window_seconds", persistence.get("fold_window_seconds", 600))
    validate_fold_bounds(window, cap, consolidation.get("max_inter_alert_gap_seconds", 60))
    return window


def validate_fold_bounds(
    fold_window_seconds: float,
    max_event_duration_seconds: Optional[float],
    max_inter_alert_gap_seconds: float,
) -> float:
    """Check the window can contain the events folded into it; return the lookback."""
    # Through ``_positive`` rather than bare ``float()``: the caller is written
    # against a ValueError contract, and a raw TypeError from ``null`` leaks
    # past ``except ValueError``, silently disabling the folder.
    # ``_positive`` is what refuses ``null`` here: an unbounded event cannot be
    # contained by any window, so persistence has no reading of it.
    cap = _positive("max_event_duration_seconds", max_event_duration_seconds)
    gap = _positive("max_inter_alert_gap_seconds", max_inter_alert_gap_seconds)
    floor = cap + gap
    window = _positive("fold_window_seconds", fold_window_seconds)
    if window <= floor:
        raise ValueError(
            f"fold_window_seconds ({fold_window_seconds}) must exceed "
            f"max_event_duration_seconds + max_inter_alert_gap_seconds ({floor})"
        )
    return floor


def _dedupe_derived_ids(events: List[dict]) -> List[dict]:
    """Make sure no two events in one batch carry the same id.

    Two events of one group derive different ids because they start at
    different chunks — unless two chunks share a timestamp, which nothing
    forbids. Applied in one bulk request, the second would silently overwrite
    the first and take its evidence with it. The later event is re-minted from
    its own first member, which keeps the result a function of the evidence
    and so still independent of the order it arrived in.
    """
    seen: Set[str] = set()
    for event in events:
        event_id = str(event.get("Id") or "")
        if event_id and event_id in seen:
            first_chunk = (event.get("chunk_ids") or [""])[0]
            event_id = "evt-" + hashlib.sha1(
                f"{event_id}|{first_chunk}".encode("utf-8")
            ).hexdigest()
            event["Id"] = event_id
            event["_id"] = event_id
        seen.add(event_id)
    return events


class EvidenceUnavailable(RuntimeError):
    """Raised when a group's evidence could not be read.

    Distinct from an empty result: a group with no evidence is folded to
    nothing, while a group whose evidence is unreadable must be left alone.
    """


class FoldResult:
    """What one cycle did. Returned so callers and tests can assert on it."""

    __slots__ = (
        "groups", "chunks", "events", "failed", "superseded", "purged",
        "truncated_groups", "aborted", "freshness_unpublished", "aliases",
        "duration",
    )

    def __init__(self) -> None:
        self.groups = 0
        self.chunks = 0
        self.events = 0
        self.failed = 0
        self.superseded = 0
        self.purged = 0
        self.aliases = 0
        self.aborted = False
        self.freshness_unpublished = False
        self.truncated_groups: List[str] = []
        self.duration = 0.0

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"FoldResult(groups={self.groups}, chunks={self.chunks}, "
            f"events={self.events}, failed={self.failed}, superseded={self.superseded}, "
            f"truncated={len(self.truncated_groups)}, aliases={self.aliases}, "
            f"duration={self.duration:.3f}s)"
        )


class RealtimeEventFolder:
    """Periodically folds realtime evidence into the durable event store."""

    def __init__(
        self,
        incident_service,
        event_store,
        es_client,
        raw_index_pattern: str,
        *,
        fold_interval_seconds: float = DEFAULT_FOLD_INTERVAL_SECONDS,
        fold_window_seconds: float = DEFAULT_FOLD_WINDOW_SECONDS,
        retention_days: float = DEFAULT_RETENTION_DAYS,
        owner: Optional[str] = None,
    ) -> None:
        self._svc = incident_service
        self._store = event_store
        self._es = es_client
        self._raw_index = raw_index_pattern
        self._interval = _positive("fold_interval_seconds", fold_interval_seconds)
        self._window = _positive("fold_window_seconds", fold_window_seconds)
        self._retention_days = _positive("retention_days", retention_days)
        # Unique per process, including two processes on one host. A
        # host-derived identity was tried so a restart could reclaim its own
        # lease immediately, but it made the two co-located processes of a
        # rolling restart indistinguishable to the lock — so the newcomer stole
        # a live lease from the outgoing one mid-cycle, which is the failure the
        # lock exists to prevent. Waiting a TTL after a restart is the cheaper
        # of the two.
        self._owner = owner or f"folder-{socket.gethostname()}-{os.getpid()}-{uuid.uuid4().hex[:8]}"

        # Fail at construction rather than silently splitting events later. An
        # unbounded event cannot fit in any window, and a window narrower than
        # one event guarantees the folder cuts through events it is folding.
        #
        # Reading back by the returned floor guarantees any event ending inside
        # the window was fetched whole: an event spans at most the duration cap,
        # so one ending at the window start began no earlier than this.
        self._lookback = validate_fold_bounds(
            self._window, self._max_duration(), self._gap_seconds(),
        )
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        # Guards against a cycle that outlives its interval. A slow cycle
        # skips the next one rather than running two folds over the same
        # window concurrently, which would have them race on the same ids.
        self._cycle_lock = threading.Lock()
        self._last_completed_at: Optional[float] = None
        self._lock_version: Optional[Tuple[int, int]] = None
        self._renewed_at: float = 0.0

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def _settles_at(self, event: dict) -> Optional[str]:
        """The instant after which no cycle can reach this event again.

        Written rather than a ``status``, and the difference matters. Lifecycle
        state is a function of *now*, so a stored one would be fixed at "open"
        for the record's life — an event becomes immutable at a moment when
        nothing is writing, so no writer is there to correct it. This instant is
        not a function of now: it is recomputed whenever the event is rewritten,
        and once nothing rewrites it any more the last value written is already
        the right one.

        It also spares the reader the fold's configuration. A consumer decides
        lifecycle with ``now > settlesAt``, without being told the window, the
        duration cap or the gap — which is what a consumer in another service
        would otherwise have to be given and kept in step with.

        Measured from the newest instant anything in the event carries, not from
        ``end``: the fetch predicate is on each chunk's ``timestamp``, and a
        chunk can carry an ``end`` earlier than its own timestamp.
        """
        instants = [
            _parse_ts(event.get("end")),
            _parse_ts(event.get("timestamp")),
            *(
                _parse_ts(m.get("timestamp"))
                for m in (event.get("chunk_meta") or [])
                if isinstance(m, dict)
            ),
        ]
        newest = max([i for i in instants if i is not None] or [None])
        if newest is None:
            return None
        return _iso(newest + timedelta(seconds=self.rewrite_horizon_seconds))

    def _renew_due(self, now_monotonic: float) -> bool:
        """Whether the lease is close enough to expiry to be worth rewriting.

        Renewing once per group looked safe and was not: it is one synchronous
        write to a single document per group, so at five hundred groups it is a
        thousand round trips to one shard in a thirty-second cycle — an order of
        magnitude above the whole cycle's measured cost, and a write hotspot on
        one document. Renewal is driven by the lease clock instead, which is
        O(cycle duration / TTL) writes and keeps the property the second
        renewal was added for.
        """
        ttl = self._interval * 3
        return (now_monotonic - self._renewed_at) > (ttl / 3.0)

    @property
    def rewrite_horizon_seconds(self) -> float:
        """How far back a cycle can still reach and change something.

        The window plus the lookback margin. An event older than this is read
        by no cycle, which is the only point at which it is genuinely fixed.
        """
        return self._window + self._lookback

    @property
    def last_completed_at(self) -> Optional[float]:
        """Epoch seconds of the last finished cycle, or None before the first."""
        return self._last_completed_at

    def start(self) -> bool:
        """Start the folding thread.

        Folding belongs to the instance, not to a pipeline process, so the
        caller starts this only on the instance leader — the same rule the
        verdict-retention reaper follows. The Elasticsearch lock below covers
        the remaining case of more than one instance sharing a cluster.
        """
        if self._thread is not None and self._thread.is_alive():
            return True
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="realtime-event-folder", daemon=True,
        )
        self._thread.start()
        logger.info(
            "Realtime event folder started (interval=%ss, window=%ss)",
            self._interval, self._window,
        )
        return True

    def stop(self, timeout: Optional[float] = None) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
            self._thread = None

    def _run(self) -> None:
        """Fold on a fixed cadence, counting the ticks a slow cycle costs.

        Sleeping for a whole interval *after* each cycle would space cycles by
        interval plus duration, so a cycle that overran would quietly delay the
        next one instead of registering as a skip. The overrun is the signal
        worth having — it is what says the fold can no longer keep up with the
        fleet — so the schedule is kept against fixed deadlines and missed
        deadlines are counted.
        """
        next_due = time.monotonic() + self._interval
        while not self._stop.wait(max(0.0, next_due - time.monotonic())):
            try:
                self.run_once()
            except Exception:
                # A failed cycle is recoverable by construction: the next one
                # re-folds the same window from the same evidence.
                logger.error("Realtime fold cycle failed", exc_info=True)
            now = time.monotonic()
            missed = 0
            while next_due <= now:
                next_due += self._interval
                missed += 1
            if missed > 1:
                logger.warning(
                    "Fold cycle overran its interval; %d scheduled cycle(s) skipped",
                    missed - 1,
                )
                if FOLD_CYCLES_SKIPPED is not None:
                    FOLD_CYCLES_SKIPPED.inc(missed - 1)

    # ------------------------------------------------------------------
    # One cycle
    # ------------------------------------------------------------------

    def run_once(self, now: Optional[datetime] = None) -> Optional[FoldResult]:
        """Fold the current tail window. Returns None when the cycle was skipped."""
        if not self._cycle_lock.acquire(blocking=False):
            logger.warning("Fold cycle skipped: previous cycle still running")
            if FOLD_CYCLES_SKIPPED is not None:
                FOLD_CYCLES_SKIPPED.inc()
            return None
        try:
            # Before the lock: the lock is itself a document, so acquiring it
            # first would let Elasticsearch auto-create the index with a dynamic
            # mapping and the explicit one would never be applied.
            if not self._store.ensure_index():
                logger.error("Fold cycle abandoned: the event index is not usable")
                return None
            lock = self._store.acquire_lock(self._owner, ttl_seconds=self._interval * 3)
            # Recorded before any work, not part-way through it: every path out
            # of the cycle releases what is recorded here, and an abandoned
            # cycle that left the lease behind would lock a second instance out
            # for the whole TTL — on every cycle, if the cluster is unhealthy.
            self._lock_version = lock
            if lock is None:
                logger.info("Fold cycle skipped: another instance holds the lock")
                if FOLD_CYCLES_SKIPPED is not None:
                    FOLD_CYCLES_SKIPPED.inc()
                return None
            try:
                return self._fold(now or datetime.now(timezone.utc), lock)
            finally:
                self._store.release_lock(self._owner, self._lock_version)
                self._lock_version = None
        finally:
            self._cycle_lock.release()

    def _fold(self, now: datetime, lock: Tuple[int, int]) -> FoldResult:
        started = time.monotonic()
        result = FoldResult()

        ttl = self._interval * 3
        self._renewed_at = time.monotonic()
        window_start = now - timedelta(seconds=self._window)
        end_iso = _iso(now)
        # Fetch further back than the window so an event that ends inside it is
        # read whole. Writing an event assembled from part of its evidence would
        # overwrite the complete record with a shorter one.
        fetch_iso = _iso(window_start - timedelta(seconds=self._lookback))

        existing, complete = self._store.events_in_window(fetch_iso, end_iso)
        if not complete:
            logger.error(
                "Fold cycle abandoned: the stored event set is incomplete, "
                "so ids could not be frozen reliably"
            )
            _count_abort("events_incomplete")
            result.aborted = True
            result.duration = time.monotonic() - started
            return result
        by_group: Dict[Tuple[str, str], List[dict]] = {}
        # The version each event was read at. Every write and every supersession
        # delete is conditioned on it, so a batch that lands after another
        # writer has moved the document is refused by Elasticsearch rather than
        # applied on top of a newer fold. The lease says this cycle was entitled
        # to start; only this says it is still entitled to land.
        versions: Dict[str, Tuple[int, int]] = {}
        for event in existing:
            key = (str(event.get("sensorId") or ""), str(event.get("category") or ""))
            by_group.setdefault(key, []).append(event)
            seq, primary = event.get("_seq_no"), event.get("_primary_term")
            if seq is not None and primary is not None:
                versions[str(event.get("Id") or event.get("_id") or "")] = (seq, primary)

        # Enumerated over the range evidence is *read* from, not the range
        # events are written for. A group whose only recent chunk predates the
        # window still owns an event that ends inside it, and enumerating on
        # the narrower range would drop that group's fold entirely.
        groups, groups_complete = self._groups(fetch_iso, end_iso)
        if not groups_complete:
            # Some of the fleet was never walked. Purging and publishing
            # freshness now would report a partial cycle as a whole one.
            logger.error(
                "Fold cycle abandoned: group enumeration was incomplete, so part "
                "of the fleet was not folded"
            )
            _count_abort("groups_incomplete")
            result.aborted = True
            result.duration = time.monotonic() - started
            return result
        for sensor_id, category in groups:
            result.groups += 1
            if self._renew_due(now_monotonic=time.monotonic()):
                self._lock_version = self._store.renew_lock(
                    self._owner, self._lock_version, ttl_seconds=ttl,
                )
                self._renewed_at = time.monotonic()
            if self._lock_version is None:
                # Returning rather than breaking: without the lease another
                # instance may already be folding, so this one must not go on
                # to delete anything or publish itself as a completed cycle.
                logger.warning("Fold cycle abandoned: lock lost mid-cycle")
                _count_abort("lock_lost")
                result.aborted = True
                result.duration = time.monotonic() - started
                return result
            stored = by_group.get((sensor_id, category), [])
            try:
                chunks, truncated = self._chunks(sensor_id, category, fetch_iso, end_iso)
            except EvidenceUnavailable:
                # Indistinguishable from "no evidence" if it were swallowed, and
                # the two call for opposite handling: one group is skipped, and
                # the cycle says so rather than reporting a clean fold.
                result.truncated_groups.append(f"{sensor_id}|{category}")
                logger.error(
                    "Fold skipped %s/%s: its evidence could not be read",
                    sensor_id, category,
                )
                if FOLD_TRUNCATED is not None:
                    FOLD_TRUNCATED.inc()
                continue
            if truncated:
                # A partial read cannot produce a whole event, and writing one
                # would overwrite a complete record with a fragment. Skip the
                # group entirely — no writes, no deletions — and say so.
                result.truncated_groups.append(f"{sensor_id}|{category}")
                logger.error(
                    "Fold skipped %s/%s: the fetch hit the per-group cap, so no "
                    "event could be assembled from complete evidence",
                    sensor_id, category,
                )
                if FOLD_TRUNCATED is not None:
                    FOLD_TRUNCATED.inc()
                continue
            if not chunks:
                continue
            result.chunks += len(chunks)

            events = self._svc.consolidate(chunks)
            # Only events proven whole. Anything ending before the window began
            # was assembled from evidence that may be cut off at the fetch
            # boundary, and was settled by an earlier cycle anyway.
            # Compared as instants rather than as strings: an ``end`` carrying
            # ``Z`` or a numeric offset instead of milliseconds sorts to the
            # wrong side of the boundary lexicographically, and this path does
            # not own the formatting of the documents it reads.
            events = [e for e in events if _at_or_after(e.get("end"), window_start)]
            if not events:
                continue

            events = self._reconcile_ids(events, stored)
            for event in events:
                event["updatedAt"] = _iso(now)
                event.setdefault("createdAt", event.get("timestamp"))
                event["settlesAt"] = self._settles_at(event)

            # Proven immediately before the writes, not merely before the
            # work that precedes them: reading and consolidating a
            # five-thousand-document group can outlast the lease on its own,
            # and the writes are what another instance must not be racing.
            if self._renew_due(now_monotonic=time.monotonic()):
                self._lock_version = self._store.renew_lock(
                    self._owner, self._lock_version, ttl_seconds=ttl,
                )
                self._renewed_at = time.monotonic()
            if self._lock_version is None:
                logger.warning(
                    "Fold cycle abandoned before writing %s/%s: lock lost",
                    sensor_id, category,
                )
                _count_abort("lock_lost")
                result.aborted = True
                result.duration = time.monotonic() - started
                return result

            written, failed = self._store.upsert(events, versions)
            # Written only for events that actually landed: an alias pointing
            # at a document that was never created is a reference that resolves
            # to nothing, which is worse than one that resolves to the old id.
            result.events += len(written)
            result.failed += len(failed)
            if failed:
                logger.error(
                    "Fold cycle wrote %d of %d events for %s/%s",
                    len(written), len(written) + len(failed), sensor_id, category,
                )

            superseded, aliases = self._superseded(events, stored, set(written))
            dropped: Set[str] = set()
            if superseded:
                dropped = set(self._store.delete(superseded, versions))
                result.superseded += len(dropped)
                refused = set(superseded) - dropped
                if refused:
                    # The record is still there, so it and its replacement are
                    # both visible. Claiming otherwise with an alias would
                    # report the same evidence twice; the next cycle re-reads
                    # and settles it.
                    logger.warning(
                        "Superseded events not removed for %s/%s: %s",
                        sensor_id, category, sorted(refused),
                    )
            # Written after the delete: an alias only means anything once the
            # record it stands in for is actually gone. It ages out with the
            # event it points at, so it carries that event's end rather than
            # the moment it was minted.
            ends = {str(e.get("Id")): str(e.get("end") or "") for e in events}
            live = [
                (old_id, new_id, ends[new_id])
                for old_id, new_id in aliases
                if ends.get(new_id) and old_id in dropped
            ]
            if live:
                written_aliases = self._store.write_aliases(live)
                result.aliases += written_aliases
                if FOLD_ALIASES_WRITTEN is not None and written_aliases:
                    FOLD_ALIASES_WRITTEN.inc(written_aliases)

        # Unconditional: retention is a mandatory bound, not an optional one.
        # Persisted events clone the representative chunk's model text and
        # reach the UI through a service with no authorization boundary, so
        # there is no configuration in which the reaper does not run.
        cutoff = now - timedelta(days=self._retention_days)
        result.purged = self._store.purge_older_than(_iso(cutoff))
        if FOLD_EVENTS_PURGED is not None and result.purged:
            FOLD_EVENTS_PURGED.inc(result.purged)

        result.duration = time.monotonic() - started
        completed_at = time.time()

        # Counted for what actually happened. These describe writes already
        # committed to Elasticsearch, so a failure to publish freshness — one
        # bookkeeping document — must not delete the record of them. An earlier
        # version returned before this point and left the counters permanently
        # short against the index, which made them unusable as an SLI.
        if FOLD_DURATION is not None:
            FOLD_DURATION.observe(result.duration)
        if FOLD_EVENTS_PERSISTED is not None and result.events:
            FOLD_EVENTS_PERSISTED.inc(result.events)
        if FOLD_EVENTS_SUPERSEDED is not None and result.superseded:
            FOLD_EVENTS_SUPERSEDED.inc(result.superseded)

        # Freshness is a separate question from whether the work was done, and
        # gets its own signal rather than being folded into ``aborted`` — which
        # means "this cycle's output is not trustworthy", and here it is.
        # Set before the freshness write, and unconditionally. This gauge is
        # now the only live freshness signal, and it describes work that
        # happened — the same argument the counters above are set on. Gating it
        # on the bookkeeping document meant a transient failure on one write
        # silenced the SLI for a cycle whose events are all in the index.
        self._last_completed_at = completed_at
        if FOLD_LAST_COMPLETED is not None:
            FOLD_LAST_COMPLETED.set(completed_at)
        if not self._store.record_fold(completed_at, result.duration, result.events):
            result.freshness_unpublished = True
            if FOLD_FRESHNESS_UNPUBLISHED is not None:
                FOLD_FRESHNESS_UNPUBLISHED.inc()
            logger.warning(
                "Fold cycle completed but its freshness record could not be "
                "written; the metric is still accurate"
            )
        logger.info("Fold cycle complete: %r", result)
        return result

    # ------------------------------------------------------------------
    # Pieces
    # ------------------------------------------------------------------

    def _groups(self, start_iso: str, end_iso: str) -> Tuple[List[Tuple[str, str]], bool]:
        """Every (sensorId, category) with foldable evidence in the range.

        A composite aggregation rather than ``terms``: it pages without a size
        guess, so a fleet larger than one page is walked rather than truncated.

        Returns the groups and whether the walk finished. A partial walk is not
        a smaller fleet — it is an unknown one, and the caller has to be able to
        tell those apart before it purges anything or publishes the cycle.
        """
        query = {
            "bool": {
                "must": realtime_confirmed_clauses() + [
                    {"range": {"timestamp": {"gte": start_iso, "lte": end_iso}}},
                ]
            }
        }
        sources = [
            {"sensorId": {"terms": {"field": "sensorId.keyword"}}},
            {"category": {"terms": {"field": "category.keyword"}}},
        ]
        groups: List[Tuple[str, str]] = []
        after: Optional[dict] = None
        while True:
            composite: Dict[str, Any] = {"size": _GROUP_PAGE_SIZE, "sources": sources}
            if after:
                composite["after"] = after
            try:
                resp = self._es.client.search(
                    index=self._raw_index, query=query, size=0,
                    aggregations={"groups": {"composite": composite}},
                )
            except Exception:
                logger.error("Group enumeration failed", exc_info=True)
                return groups, False
            agg = resp.get("aggregations", {}).get("groups", {})
            buckets = agg.get("buckets", [])
            for bucket in buckets:
                key = bucket.get("key", {})
                groups.append((str(key.get("sensorId") or ""), str(key.get("category") or "")))
            after = agg.get("after_key")
            if not after or not buckets:
                break
        return groups, True

    def _chunks(
        self, sensor_id: str, category: str, start_iso: str, end_iso: str,
    ) -> Tuple[List[dict], bool]:
        query = {
            "bool": {
                "must": realtime_confirmed_clauses() + [
                    {"term": {"sensorId.keyword": sensor_id}},
                    {"term": {"category.keyword": category}},
                    {"range": {"timestamp": {"gte": start_iso, "lte": end_iso}}},
                ]
            }
        }
        try:
            resp = self._es.client.search(
                index=self._raw_index, query=query, size=_GROUP_FETCH_CAP,
                # Newest first, so a group that overflows the cap loses its
                # oldest chunks rather than its most recent — matching the
                # retrieval path, and keeping the events a consumer is most
                # likely to be looking at.
                sort=[{"timestamp": {"order": "desc"}}], track_total_hits=True,
            )
        except Exception as exc:
            logger.error("Chunk fetch failed for %s/%s", sensor_id, category, exc_info=True)
            raise EvidenceUnavailable(
                f"could not read evidence for {sensor_id}/{category}"
            ) from exc
        hits = resp.get("hits", {})
        docs = []
        for hit in hits.get("hits", []):
            doc = hit.get("_source", {})
            doc.setdefault("Id", hit.get("_id"))
            doc["_id"] = hit.get("_id")
            docs.append(doc)
        total = hits.get("total", {})
        total_value = total.get("value", 0) if isinstance(total, dict) else total
        return docs, total_value > len(docs)

    @staticmethod
    def _reconcile_ids(
        events: List[dict], stored: Sequence[dict],
    ) -> List[dict]:
        """Settle identity against what is already stored.

        Identity is the **content-derived** id, not the id an event was first
        stored under. That is the whole point: the derivation is already
        invariant under the order evidence arrives in — fold the same set in
        either order and it produces the same value — and freezing was what
        broke that. Freezing also put this service's two surfaces at odds,
        since ``?consolidate=true`` derives while the store remembered.

        What freezing was protecting is a real concern, though: a caller
        holding a reference should not find it dangling because earlier
        evidence turned up. So when a recomputed event absorbs a stored one
        under a different id, an alias is written from the old id to the new.
        That happens in :meth:`_superseded`, where records are actually
        dropped — more than this match can express, since a merge absorbs
        several stored events into one.

        One thing is inherited from the event being succeeded rather than
        recomputed: ``createdAt``, so an ordering key does not move when an
        event's start does. Matching is by shared
        membership, strongest claim first, and each stored event is succeeded
        at most once.
        """
        for event in events:
            event.setdefault("createdAt", event.get("timestamp"))
        if not stored:
            return _dedupe_derived_ids(events)

        owner_of_chunk: Dict[str, str] = {}
        by_id: Dict[str, dict] = {}
        for event in stored:
            event_id = str(event.get("Id") or event.get("_id") or "")
            if not event_id:
                continue
            by_id[event_id] = event
            for chunk_id in event.get("chunk_ids") or []:
                owner_of_chunk.setdefault(str(chunk_id), event_id)

        # (overlap, candidate index, stored id), strongest claim first.
        claims: List[Tuple[int, int, str]] = []
        for position, event in enumerate(events):
            counts: Dict[str, int] = {}
            for chunk_id in event.get("chunk_ids") or []:
                previous = owner_of_chunk.get(str(chunk_id))
                if previous:
                    counts[previous] = counts.get(previous, 0) + 1
            for stored_id, overlap in counts.items():
                claims.append((overlap, position, stored_id))
        claims.sort(key=lambda c: (-c[0], c[1]))

        succeeded: Dict[int, str] = {}
        taken: Set[str] = set()
        for _overlap, position, stored_id in claims:
            if position in succeeded or stored_id in taken:
                continue
            succeeded[position] = stored_id
            taken.add(stored_id)

        for position, stored_id in succeeded.items():
            previous = by_id.get(stored_id, {})
            if previous.get("createdAt"):
                events[position]["createdAt"] = previous["createdAt"]
        return _dedupe_derived_ids(events)

    def _gap_seconds(self) -> float:
        return float(self._svc.consolidation_bounds()["max_inter_alert_gap_seconds"])

    def _max_duration(self) -> Optional[float]:
        value = self._svc.consolidation_bounds()["max_event_duration_seconds"]
        return None if value is None else float(value)

    @staticmethod
    def _superseded(
        events: Sequence[dict],
        stored: Sequence[dict],
        written_ids: Set[str],
    ) -> Tuple[List[str], List[Tuple[str, str]]]:
        """Stored events the re-fold has genuinely replaced, and where each went.

        A boundary shift can merge two events into one; the absorbed document
        has to go, or the same evidence is reported twice.

        Eligibility is deliberately narrow, because the cost of being wrong is
        destroying a durable record. A stored event qualifies only if this cycle
        the replacement that covers every one of its members was confirmed
        written. An event whose chunks were outside the fetched range
        was never recomputed, so its absence from the result says nothing about
        whether it should still exist.

        There is deliberately only one membership test, not two. Checking that
        the members were re-read this cycle *as well* reads like defence in
        depth but is implied: ``covered`` is built from events that were
        written, those are assembled only from chunks just fetched, so anything
        covered was fetched. A guard that cannot fail on its own is a guard no
        test can hold to account, and this function is not the place for code
        whose behaviour nobody checks.

        Note there is also no test on *when* the stored event ended.
        The two membership tests already prove the record is safe to drop, and
        an end-time test is not merely redundant: the fetch reaches further back
        than the window, so an event ending behind the window can be absorbed by
        one ending inside it, and refusing to drop it there would strand a
        duplicate that no later cycle could ever reclaim.
        """
        kept = {str(e.get("Id")) for e in events if e.get("Id")}
        covered: Set[str] = set()
        # Which written event absorbed each chunk, so a dropped record can be
        # pointed at whatever now holds its evidence.
        owner: Dict[str, str] = {}
        for event in events:
            event_id = str(event.get("Id"))
            if event_id in written_ids:
                for chunk_id in event.get("chunk_ids") or []:
                    covered.add(str(chunk_id))
                    owner.setdefault(str(chunk_id), event_id)

        drop: List[str] = []
        aliases: List[Tuple[str, str]] = []
        for event in stored:
            event_id = str(event.get("Id") or event.get("_id") or "")
            if not event_id or event_id in kept:
                continue
            members = {str(c) for c in event.get("chunk_ids") or []}
            if not members:
                continue
            if not members.issubset(covered):
                continue          # its evidence is not safely stored elsewhere yet
            drop.append(event_id)
            # Every dropped record gets an alias, not only the one that also
            # supplied ``createdAt``. A merge absorbs two stored events into
            # one, and the ``createdAt`` match picks a single predecessor — so
            # tying aliases to that match orphaned every other id the merge
            # consumed, in exactly the case the alias exists for.
            counts: Dict[str, int] = {}
            for chunk_id in members:
                holder = owner.get(chunk_id)
                if holder:
                    counts[holder] = counts.get(holder, 0) + 1
            if counts:
                target = max(sorted(counts), key=lambda k: counts[k])
                if target != event_id:
                    aliases.append((event_id, target))
        return drop, aliases
