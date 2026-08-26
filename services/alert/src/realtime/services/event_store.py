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
_LOCK_ID = "_fold_lock"
_STATE_ID = "_fold_state"
# Internal bookkeeping documents live in the same index as the events they
# guard. They are tagged on a dedicated field rather than the domain
# ``type`` field, which an event legitimately carries.
_DOC_KIND_FIELD = "_docKind"
_INTERNAL_KINDS = ["lock", "state", "alias"]
_ALIAS_PREFIX = "_alias-"


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
    ) -> None:
        self._es = es_client
        self._index = f"{INDEX_PREFIX}{collection}"
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
