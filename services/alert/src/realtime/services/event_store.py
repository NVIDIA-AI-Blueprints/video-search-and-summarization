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
Durable store for consolidated realtime events.

Owns one Elasticsearch index, written only by the folder and read by the
retrieval path.  The index is deliberately *not* date-partitioned: document ids
are unique per index in Elasticsearch, so a single index is what makes an upsert
keyed on the frozen event id genuinely idempotent.  Partitioning by day would
let one event exist twice once late evidence moved its start across a boundary.

Ordering and paging use ``createdAt`` with ``Id`` as a tie-break.  Both are
frozen when the event is first stored, unlike ``end``, which advances while the
underlying condition continues, and unlike ``timestamp``, which moves *earlier*
when evidence predating the event arrives; paging on either would silently skip
and repeat rows.
"""

import logging
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

logger = logging.getLogger(__name__)

_ES_META_FIELDS = frozenset({"_id", "_seq_no", "_primary_term", "_index", "_version"})
_WINDOW_READ_CAP = 10000
_TOTAL_HITS_CAP = 10000
_LOCK_ID = "_fold_lock"
_STATE_ID = "_fold_state"
# Internal bookkeeping documents live in the same index as the events they
# guard. They are tagged on a dedicated field rather than the domain
# ``type`` field, which an event legitimately carries.
_DOC_KIND_FIELD = "_docKind"
_INTERNAL_KINDS = ["lock", "state", "alias"]
_ALIAS_PREFIX = "_alias-"
# A reference that has been superseded more than once resolves through a
# chain. Bounded so a cycle — which should be impossible, but is cheap to
# rule out — cannot spin a request.
_ALIAS_MAX_HOPS = 8


def _parse_iso(value: Any) -> Optional[float]:
    """Epoch seconds for an ISO-8601 instant, or None if it is not one."""
    if not value:
        return None
    text = str(value).strip().replace("Z", "+00:00")
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.timestamp()


def _is_missing(exc: Exception) -> bool:
    """Whether an Elasticsearch error means "not there" rather than "not working"."""
    status = getattr(exc, "status_code", None) or getattr(exc, "status", None)
    if status == 404:
        return True
    return type(exc).__name__ == "NotFoundError"


class EventStoreUnavailable(RuntimeError):
    """Raised when a read failed, as opposed to returning no results.

    The two must not look alike to a caller: a consumer that cannot tell
    an unreachable store from an empty one reads an outage as agreement.
    """


def _now_epoch() -> float:
    return time.time()


INDEX_PREFIX = "ab-"
DEFAULT_COLLECTION = "alert-realtime-events"
# Defined here rather than in the folder because both the folder and the read
# path need it, and they must not disagree: the read path derives ``status``
# from the same window the folder re-folds.
DEFAULT_FOLD_WINDOW_SECONDS = 600.0

# Explicit rather than dynamic: this index is read by other services, so the
# first document written must not be what decides the field types.
_MAPPING: Dict[str, Any] = {
    "dynamic": False,
    "properties": {
        "Id": {"type": "keyword"},
        "createdAt": {"type": "date"},
        "_docKind": {"type": "keyword"},
        "sensorId": {"type": "keyword"},
        "category": {"type": "keyword"},
        "timestamp": {"type": "date"},
        "end": {"type": "date"},
        "status": {"type": "keyword"},
        "updatedAt": {"type": "date"},
        "chunk_ids": {"type": "keyword"},
        "chunk_meta": {
            "type": "object",
            "properties": {
                "id": {"type": "keyword"},
                "timestamp": {"type": "date"},
                "end": {"type": "date"},
                "verdict": {"type": "keyword"},
                "chunkIdx": {"type": "integer"},
            },
        },
        "isAnomaly": {"type": "boolean"},
        "type": {"type": "keyword"},
        "place": {"type": "object", "enabled": False},
        "analyticsModule": {"type": "object", "enabled": False},
        "llm": {"type": "object", "enabled": False},
        "info": {
            "type": "object",
            "properties": {
                "chunkCount": {"type": "integer"},
                "chunkIdxRange": {"type": "keyword"},
                "isConsolidated": {"type": "keyword"},
                "mergedQueryCount": {"type": "integer"},
                "verdict": {"type": "keyword"},
                "requestId": {"type": "keyword"},
            },
        },
    },
}


class RealtimeEventStore:
    """Elasticsearch-backed store for consolidated realtime events."""

    def __init__(
        self,
        es_client,
        collection: str = DEFAULT_COLLECTION,
        rewrite_horizon_seconds: Optional[float] = None,
    ) -> None:
        self._es = es_client
        self._index = f"{INDEX_PREFIX}{collection}"
        # How far back a cycle can still reach — the fold window *plus* the
        # lookback margin, not the window alone. See ``_status``.
        #
        # ``None`` means "never claim an event is settled". There is
        # deliberately no numeric default: the window default would be wrong by
        # exactly the lookback margin, which is the mistake this value exists
        # to prevent, and being silently wrong here breaks the one promise a
        # consumer is invited to cache on.
        self._rewrite_horizon = (
            None if rewrite_horizon_seconds is None else float(rewrite_horizon_seconds)
        )

    @property
    def index(self) -> str:
        return self._index

    def ensure_index(self) -> bool:
        """Create the index with its explicit mapping if it does not exist.

        Returns True when the index is usable.  A failure here is not fatal to
        the service: the folder logs and retries on its next cycle rather than
        taking the process down.
        """
        try:
            client = self._es.client
            if client.indices.exists(index=self._index):
                return True
            client.indices.create(index=self._index, mappings=_MAPPING)
            logger.info("Created realtime event index %s", self._index)
            return True
        except Exception:
            logger.error("Could not ensure realtime event index %s", self._index, exc_info=True)
            return False

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------

    def upsert(
        self,
        events: Sequence[dict],
        versions: Optional[Dict[str, Tuple[int, int]]] = None,
    ) -> Tuple[List[str], List[str]]:
        """Index events by their frozen id, conditional on what was read.

        Returns ``(written_ids, failed_ids)``. The split matters: the caller
        deletes documents that a re-fold replaced, and deleting on the strength
        of a write that did not land would lose the evidence entirely. A count
        derived from the request rather than the response cannot express that.

        **Each write is fenced on the version the folder read.** The fold lock
        alone cannot do this job: a lease says a holder was entitled to proceed
        at some past instant, not that it still is when a request finally lands.
        A cycle that stalls inside ``bulk`` past its TTL, while a second writer
        takes the lease and folds the same window, would otherwise have its
        stale batch applied on top — resurrecting superseded events and undoing
        the newer fold. Conditioning on ``if_seq_no``/``if_primary_term`` makes
        the *write* the thing that is fenced, not the intention to write.

        An event the folder did not read is written with ``op_type: create``,
        which fails just as loudly if another writer created it meanwhile.
        Either failure is reported rather than raised: the caller already
        refuses to delete anything a failed write was supposed to replace, so a
        lost race costs a cycle, not a record.
        """
        if not events:
            return [], []
        body: List[dict] = []
        attempted: List[str] = []
        seen: Set[str] = set()
        for event in events:
            doc_id = event.get("Id")
            if not doc_id:
                logger.warning("Skipping event with no Id during upsert")
                continue
            if str(doc_id) in seen:
                # Two events under one id would be applied in order and the
                # first one's evidence would vanish without a trace. Refusing
                # the batch turns a silent loss into a visible failure.
                raise ValueError(
                    f"refusing a batch with duplicate event id {doc_id}"
                )
            seen.add(str(doc_id))
            # Elasticsearch rejects a document carrying a metadata field in its
            # source; the event dict holds ``_id`` because it is a clone of a
            # read hit. ``Id`` remains as the application-visible identifier.
            action: Dict[str, Any] = {"_index": self._index, "_id": doc_id}
            version = (versions or {}).get(str(doc_id))
            if version is not None:
                action["if_seq_no"], action["if_primary_term"] = version
                body.append({"index": action})
            else:
                # Never seen this cycle, so it should not exist. If it does,
                # another writer is active and this batch is already stale.
                body.append({"create": action})
            body.append({k: v for k, v in event.items() if k not in _ES_META_FIELDS})
            attempted.append(str(doc_id))
        if not body:
            return [], []
        try:
            resp = self._es.client.bulk(operations=body, refresh=False)
        except Exception:
            logger.error("Bulk upsert of realtime events failed", exc_info=True)
            return [], attempted

        failed: List[str] = []
        for item in resp.get("items", []):
            outcome = item.get("index") or item.get("create") or {}
            if outcome.get("error"):
                failed.append(str(outcome.get("_id")))
                logger.error(
                    "Event %s was not written: %s",
                    outcome.get("_id"), outcome.get("error"),
                )
        written = [doc_id for doc_id in attempted if doc_id not in set(failed)]
        return written, failed

    def delete(
        self,
        event_ids: Sequence[str],
        versions: Optional[Dict[str, Tuple[int, int]]] = None,
    ) -> int:
        """Remove events superseded by a re-fold.  Missing ids are not an error.

        Returns the number Elasticsearch confirmed, not the number requested.

        Fenced on the read version for the same reason as :meth:`upsert`: a
        delete that lands after another writer has rewritten the document would
        destroy the newer record. Passing no version deletes unconditionally,
        which is right for retention — that path selects on age, and two
        writers reading the same ages choose the same documents.
        """
        if not event_ids:
            return 0
        body: List[dict] = []
        for event_id in event_ids:
            action: Dict[str, Any] = {"_index": self._index, "_id": event_id}
            version = (versions or {}).get(str(event_id))
            if version is not None:
                action["if_seq_no"], action["if_primary_term"] = version
            body.append({"delete": action})
        try:
            resp = self._es.client.bulk(operations=body, refresh=False)
        except Exception:
            logger.error("Bulk delete of superseded events failed", exc_info=True)
            return 0
        return sum(
            1 for item in resp.get("items", [])
            if item.get("delete", {}).get("result") == "deleted"
        )

    # ------------------------------------------------------------------
    # Fold freshness
    # ------------------------------------------------------------------

    def record_fold(self, completed_at: float, duration: float, events: int) -> bool:
        """Publish when the last cycle finished.

        The folder and the API run in different processes, so freshness cannot
        be an attribute on the folder object — a reader would see nothing. It
        travels through the store instead, which both already talk to.

        Returns whether it landed. A swallowed failure here is not harmless:
        with no earlier state document the endpoint answers "no cycle has ever
        run" while the process metric says a cycle just finished, which is the
        exact ambiguity this value exists to remove.
        """
        try:
            self._es.client.index(
                index=self._index, id=_STATE_ID,
                document={
                    _DOC_KIND_FIELD: "state",
                    "completedAt": completed_at,
                    "durationSeconds": duration,
                    "eventsWritten": events,
                },
            )
        except Exception:
            # Not worth failing a cycle over, but the caller has to know: it
            # must not go on to report the cycle as visibly fresh.
            logger.error("Could not record fold completion", exc_info=True)
            return False
        return True

    def fold_lag_seconds(self, now: Optional[float] = None) -> Optional[float]:
        """Seconds since the last completed cycle, or None if none has run.

        Raises :class:`EventStoreUnavailable` if the answer could not be read.
        Reporting that as ``None`` would tell a caller "no cycle has completed"
        on the evidence that the store could not be asked — and this value
        exists precisely so a caller need not guess whether it is looking at
        fresh data.
        """
        try:
            doc = self._es.client.get(index=self._index, id=_STATE_ID)
        except Exception as exc:
            if _is_missing(exc):
                return None
            logger.error("Reading fold freshness failed", exc_info=True)
            raise EventStoreUnavailable("could not read fold freshness") from exc
        completed = doc.get("_source", {}).get("completedAt")
        if completed is None:
            return None
        return max(0.0, (now if now is not None else _now_epoch()) - float(completed))

    # ------------------------------------------------------------------
    # Single-writer lock
    # ------------------------------------------------------------------

    def acquire_lock(self, owner: str, ttl_seconds: float) -> Optional[Tuple[int, int]]:
        """Take the fold lock, or return None if another owner holds a live one.

        The lock is a document in this same index rather than a separate one:
        it needs the same availability as the store it guards, and a second
        index would be one more thing to create and keep. It is tagged on a
        dedicated field so reads can exclude it, rather than on the domain
        ``type`` field, which an event legitimately carries.

        Optimistic concurrency does the work — two processes that read the same
        version can both attempt the write, and Elasticsearch fails all but one.
        """
        now = _now_epoch()
        try:
            current = self._es.client.get(index=self._index, id=_LOCK_ID)
            source = current.get("_source", {})
            # Taken over only once the lease has actually expired. There was a
            # same-owner bypass here so a restarted process could reclaim its
            # own lease at once; it is gone, because it is only sound if two
            # live processes can never present the same owner — and nothing
            # about an identifier can guarantee that. A restart now waits at
            # most one TTL, which is the cheaper of the two failures.
            if float(source.get("expiresAt", 0)) > now:
                return None
            seq = current.get("_seq_no")
            primary = current.get("_primary_term")
        except Exception:
            seq = primary = None

        doc = {
            _DOC_KIND_FIELD: "lock",
            "owner": owner,
            "acquiredAt": now,
            "expiresAt": now + max(1.0, float(ttl_seconds)),
        }
        try:
            if seq is None:
                resp = self._es.client.index(
                    index=self._index, id=_LOCK_ID, document=doc, op_type="create",
                )
            else:
                resp = self._es.client.index(
                    index=self._index, id=_LOCK_ID, document=doc,
                    if_seq_no=seq, if_primary_term=primary,
                )
        except Exception:
            # A conflict here means someone else won the race, which is the
            # lock working rather than an error worth logging loudly.
            logger.debug("Fold lock not acquired by %s", owner)
            return None
        return resp.get("_seq_no"), resp.get("_primary_term")

    def renew_lock(
        self, owner: str, version: Tuple[int, int], ttl_seconds: float,
    ) -> Optional[Tuple[int, int]]:
        """Extend the lease mid-cycle. Returns the new version, or None if lost.

        A cycle can outlive its lease — a cold cluster, a backlog after a
        restart — and without renewal a second instance would acquire the lock
        while this one is still writing. Renewing conditionally on the version
        we hold means a lease we already lost cannot be silently reclaimed.
        """
        seq, primary = version
        now = _now_epoch()
        try:
            resp = self._es.client.index(
                index=self._index, id=_LOCK_ID,
                document={
                    _DOC_KIND_FIELD: "lock",
                    "owner": owner,
                    "acquiredAt": now,
                    "expiresAt": now + max(1.0, float(ttl_seconds)),
                },
                if_seq_no=seq, if_primary_term=primary,
            )
        except Exception:
            logger.warning("Fold lock lost by %s during a cycle", owner)
            return None
        return resp.get("_seq_no"), resp.get("_primary_term")

    def release_lock(self, owner: str, version: Optional[Tuple[int, int]]) -> None:
        """Drop the lock so the next cycle does not wait out the TTL.

        Only ever conditional on the version held. Reading the document,
        checking the owner and then deleting is a check-then-act: between the
        read and the delete the lease can expire and a successor can acquire,
        and this call would then delete *their* lock. With no version to delete
        on there is nothing safe to do, so the lease is left to expire.
        """
        if version is None:
            logger.debug("No lock version held by %s; leaving the lease to expire", owner)
            return
        seq, primary = version
        try:
            self._es.client.delete(
                index=self._index, id=_LOCK_ID,
                if_seq_no=seq, if_primary_term=primary,
            )
        except Exception:
            logger.debug("Fold lock release was a no-op for %s", owner)

    # ------------------------------------------------------------------
    # Retention
    # ------------------------------------------------------------------

    def purge_older_than(self, cutoff_iso: str, max_docs: int = 1000) -> int:
        """Delete events that ended before the cutoff. Returns how many went.

        Events outlive the raw evidence they summarise, and carry a copy of the
        representative chunk's model text, so an unbounded store keeps that text
        reachable long after the raw copy has expired. Bounded per call so a
        first run against a large backlog cannot monopolise the cycle.
        """
        query = {
            "bool": {
                "filter": [{"range": {"end": {"lt": cutoff_iso}}}],
                # Aliases age out with the events; the lock and the freshness
                # record are bookkeeping and must survive their own reaper.
                "must_not": [{"terms": {_DOC_KIND_FIELD: ["lock", "state"]}}],
            }
        }
        try:
            resp = self._es.client.search(
                index=self._index, query=query, size=max_docs,
                sort=[{"end": {"order": "asc"}}],
            )
        except Exception:
            logger.error("Retention scan failed", exc_info=True)
            return 0
        doc_ids = [hit.get("_id") for hit in resp.get("hits", {}).get("hits", []) if hit.get("_id")]
        if not doc_ids:
            return 0
        removed = self.delete(doc_ids)
        logger.info("Retention removed %d events ended before %s", removed, cutoff_iso)
        return removed

    # ------------------------------------------------------------------
    # Reads used by the folder
    # ------------------------------------------------------------------

    def page(
        self,
        *,
        sensor_id: Optional[str] = None,
        category: Optional[str] = None,
        start_time: Optional[str] = None,
        end_time: Optional[str] = None,
        limit: int = 50,
        cursor: Optional[Tuple[str, str]] = None,
        now: Optional[float] = None,
    ) -> Tuple[List[dict], Optional[Tuple[str, str]], int, bool]:
        """One page of events, newest first, plus the cursor for the next page.

        Ordered by ``createdAt`` with ``Id`` breaking ties, and paged with
        ``search_after`` on that pair. Both are fixed when the event is first
        stored, which is what makes the cursor stable. Neither of the other two
        instants is: ``end`` advances while a condition continues, and
        ``timestamp`` moves earlier when evidence predating the event arrives,
        so a cursor anchored on either would revisit rows it had already
        returned and never reach the older ones. ``offset`` has the same defect
        for a different reason — positions shift as documents are written
        beneath the reader.

        Returns the page, the cursor for the next one, the match count, and
        whether that count is a lower bound rather than exact.
        """
        filters: List[dict] = []
        if sensor_id:
            filters.append({"term": {"sensorId": sensor_id}})
        if category:
            filters.append({"term": {"category": category}})
        if start_time:
            filters.append({"range": {"end": {"gte": start_time}}})
        if end_time:
            filters.append({"range": {"timestamp": {"lte": end_time}}})

        body: Dict[str, Any] = {
            "query": {
                "bool": {
                    "filter": filters,
                    "must_not": [{"terms": {_DOC_KIND_FIELD: _INTERNAL_KINDS}}],
                }
            },
            "size": max(1, int(limit)),
            # Ordered on values fixed when the event was first stored.
            # ``timestamp`` is not one of them: it moves earlier when evidence
            # predating the event arrives, which would slide the event behind a
            # cursor already issued and have a caller see it twice.
            "sort": [{"createdAt": {"order": "desc"}}, {"Id": {"order": "desc"}}],
            # Bounded: the exact count over a whole retention period is work no
            # consumer needs, since paging is by cursor.
            "track_total_hits": _TOTAL_HITS_CAP,
        }
        if cursor:
            body["search_after"] = list(cursor)

        try:
            # A store nobody has folded into yet has no index. That is an empty
            # result, not an outage, and answering 503 for it would make the
            # endpoint report a healthy cluster as down on every deployment
            # until the first cycle lands.
            resp = self._es.client.search(
                index=self._index, ignore_unavailable=True, **body
            )
        except Exception:
            logger.error("Paging realtime events failed", exc_info=True)
            raise EventStoreUnavailable("could not page realtime events")

        hits = resp.get("hits", {})
        events: List[dict] = []
        last_sort: Optional[Sequence[Any]] = None
        stamped_at = now if now is not None else _now_epoch()
        for hit in hits.get("hits", []):
            doc = hit.get("_source", {})
            doc["_id"] = hit.get("_id")
            doc["_index"] = self._index
            doc["status"] = self._status(doc, stamped_at)
            events.append(doc)
            last_sort = hit.get("sort") or last_sort

        total = hits.get("total", {})
        if isinstance(total, dict):
            total_value = total.get("value", 0)
            # Elasticsearch stops counting at the cap and says so. Passing the
            # number on without the relation presents a floor as a measurement,
            # and a caller sizing pages from it is wrong with no way to tell.
            capped = total.get("relation") == "gte"
        else:
            total_value, capped = total, False

        next_cursor = None
        if last_sort and len(events) == body["size"]:
            next_cursor = (str(last_sort[0]), str(last_sort[1]))
        return events, next_cursor, total_value, capped

    def write_aliases(self, pairs: Sequence[Tuple[str, str, str]]) -> int:
        """Point superseded ids at whatever absorbed them.

        Identity is derived from content, so it moves when earlier evidence
        arrives and changes which chunk an event starts at. That is what makes
        it independent of arrival order — and it is also what would leave a
        caller holding a reference to a document that no longer exists. An
        alias is the reconciliation: the identity stays a pure function of the
        evidence, and the old reference still resolves.

        Stored in the same index, tagged as internal so no read returns it, and
        carrying the **target's** ``end`` rather than the current time — so the
        reaper takes the alias in the same sweep as the event it points at. An
        alias stamped with "now" outlives its target by the whole retention
        period, leaving a reference that resolves to a document that is gone.
        """
        if not pairs:
            return 0
        body: List[dict] = []
        for old_id, new_id, end_iso in pairs:
            body.append({"index": {"_index": self._index, "_id": _ALIAS_PREFIX + str(old_id)}})
            body.append({
                _DOC_KIND_FIELD: "alias",
                "from": str(old_id),
                "to": str(new_id),
                "end": end_iso,
            })
        try:
            resp = self._es.client.bulk(operations=body, refresh=False)
        except Exception:
            logger.error("Writing event aliases failed", exc_info=True)
            return 0
        return sum(
            1 for item in resp.get("items", [])
            if not (item.get("index") or {}).get("error")
        )

    def resolve(self, event_id: str) -> Tuple[Optional[dict], Optional[str]]:
        """Fetch an event by id, following aliases.

        Returns ``(event, requested_id)`` where ``requested_id`` is the id the
        caller asked for when it differed from the one that answered — so a
        consumer can see its reference has moved and update it, rather than
        silently following a redirect for ever.
        """
        requested = str(event_id)
        current = requested
        for _hop in range(_ALIAS_MAX_HOPS):
            try:
                doc = self._es.client.get(index=self._index, id=current)
            except Exception as exc:
                if _is_missing(exc):
                    alias = self._alias_target(current)
                    if alias is None:
                        return None, None
                    current = alias
                    continue
                logger.error("Reading event %s failed", current, exc_info=True)
                raise EventStoreUnavailable(f"could not read event {current}") from exc
            source = doc.get("_source", {})
            if source.get(_DOC_KIND_FIELD):
                return None, None
            source["_id"] = doc.get("_id")
            source["status"] = self._status(source, _now_epoch())
            return source, (requested if current != requested else None)
        logger.error("Alias chain for %s exceeded %d hops", requested, _ALIAS_MAX_HOPS)
        return None, None

    def _alias_target(self, event_id: str) -> Optional[str]:
        try:
            doc = self._es.client.get(index=self._index, id=_ALIAS_PREFIX + str(event_id))
        except Exception as exc:
            if _is_missing(exc):
                return None
            raise EventStoreUnavailable(f"could not read alias for {event_id}") from exc
        target = doc.get("_source", {}).get("to")
        return str(target) if target else None

    def _status(self, doc: Dict[str, Any], now: float) -> str:
        """Whether this event can still change.

        Derived on read, deliberately. An event stops changing when no cycle
        can reach it any more — but nothing is written at that moment, so a
        status stamped by the writer would be fixed at ``open`` for the whole
        retention period and never corrected. A consumer that caches on
        ``closed`` needs the answer to be true when it reads it, not when
        someone last touched the row.

        Measured against the **fetch** horizon, not the fold window. The two
        differ by the lookback margin, and events in the gap between them are
        the subtle case: they are never rewritten, because a write needs
        ``end`` inside the window — but they are still *read*, so a bridging
        chunk can merge one into an event that is written, after which the
        original is superseded and deleted. Vanishing is a change. Only past
        the fetch horizon is an event genuinely beyond reach.

        Note this is a stronger claim than "no further evidence can join": an
        event can stop growing and still be rewritten, because late evidence
        elsewhere in the group can change where the boundaries fall.
        """
        if self._rewrite_horizon is None:
            return "open"
        # The newest instant anything in this event carries, not just ``end``.
        # The fetch predicate is on each chunk's ``timestamp``, and a chunk can
        # carry an ``end`` earlier than its own ``timestamp`` — in which case
        # the event's ``end`` understates how recently it can still be read.
        newest = max(
            [
                value for value in (
                    _parse_iso(doc.get("end")),
                    _parse_iso(doc.get("timestamp")),
                    *(
                        _parse_iso(member.get("timestamp"))
                        for member in (doc.get("chunk_meta") or [])
                        if isinstance(member, dict)
                    ),
                ) if value is not None
            ] or [None]
        )
        if newest is None:
            # No usable instant, so nothing can be proven about reachability.
            # ``open`` is the conservative answer: it says the event may still
            # change, where ``closed`` would invite a consumer to cache a
            # document whose timestamps could not even be parsed.
            return "open"
        return "closed" if (now - newest) > self._rewrite_horizon else "open"

    def events_in_window(self, start_iso: str, end_iso: str) -> Tuple[List[dict], bool]:
        """Every stored event overlapping a window, with its id.

        The folder needs these to detect supersession: an event that the
        recomputed set no longer contains, whose members another event has
        absorbed, must be deleted rather than left behind.
        """
        query = {
            "bool": {
                # Filter context: these are yes/no predicates, so scoring is
                # wasted work and the results are cacheable.
                "filter": [
                    {"range": {"timestamp": {"lte": end_iso}}},
                    {"range": {"end": {"gte": start_iso}}},
                ],
                "must_not": [{"terms": {_DOC_KIND_FIELD: _INTERNAL_KINDS}}],
            }
        }
        try:
            resp = self._es.client.search(
                index=self._index, query=query, size=_WINDOW_READ_CAP,
                # Newest first: if the cap is hit, the events dropped are the
                # ones least likely to be re-folded this cycle.
                sort=[{"timestamp": {"order": "desc"}}], track_total_hits=True,
            )
        except Exception:
            logger.error("Reading events in window failed", exc_info=True)
            raise EventStoreUnavailable("could not read events in window")
        total = resp.get("hits", {}).get("total", {})
        total_value = total.get("value", 0) if isinstance(total, dict) else total
        out = []
        for hit in resp.get("hits", {}).get("hits", []):
            doc = hit.get("_source", {})
            doc["_id"] = hit.get("_id")
            # Kept so the write that replaces this document can be made
            # conditional on it — see ``upsert``. Stripped again on the way
            # back in, along with the other metadata fields.
            doc["_seq_no"] = hit.get("_seq_no")
            doc["_primary_term"] = hit.get("_primary_term")
            out.append(doc)
        # An event missing from this read cannot have its id frozen, so the
        # fold would mint a fresh one and duplicate it. The caller is told, and
        # abandons the cycle rather than writing a result it cannot trust.
        return out, total_value <= _WINDOW_READ_CAP
