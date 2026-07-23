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
Service for querying incidents from Elasticsearch.
"""

import asyncio
import copy
import hashlib
import logging
import time
from datetime import datetime, timezone
from typing import TYPE_CHECKING, List, Optional, Tuple

from ..config import ErrorCode, ResponseStatus

if TYPE_CHECKING:
    from clients.elastic import ElasticClient

logger = logging.getLogger(__name__)

try:
    from metrics import PROMETHEUS_ENABLED
    if PROMETHEUS_ENABLED:
        from metrics.prometheus_metrics import (
            INCIDENT_QUERY_DURATION,
            INCIDENT_QUERY_FAILURES,
            DEDUP_CHUNKS_IN,
            DEDUP_EVENTS_OUT,
            DEDUP_SPLIT_REASON,
            DEDUP_DURATION,
        )
    else:
        INCIDENT_QUERY_DURATION = None
        INCIDENT_QUERY_FAILURES = None
        DEDUP_CHUNKS_IN = None
        DEDUP_EVENTS_OUT = None
        DEDUP_SPLIT_REASON = None
        DEDUP_DURATION = None
except ImportError:
    PROMETHEUS_ENABLED = False
    INCIDENT_QUERY_DURATION = None
    INCIDENT_QUERY_FAILURES = None
    DEDUP_CHUNKS_IN = None
    DEDUP_EVENTS_OUT = None
    DEDUP_SPLIT_REASON = None
    DEDUP_DURATION = None


_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
# Upper bound on raw chunks scanned for a consolidated query. Consolidation
# requires a bounded time window (enforced at the route), so this is only a
# safety net for an unexpectedly dense window; Elasticsearch caps from_+size
# at index.max_result_window (10000 by default).
_SCAN_CAP = 10000
# Cap on VLM queries merged onto one consolidated event. A dense window can
# hold thousands of chunks each carrying full reasoning; the true total is kept
# in info.mergedQueryCount.
_MAX_MERGED_QUERIES = 200


def _parse_ts(value) -> Optional[datetime]:
    """Parse an ISO-8601 timestamp string into an aware datetime, or None."""
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _to_int(value) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _info_of(doc: dict) -> dict:
    info = doc.get("info")
    return info if isinstance(info, dict) else {}


_CONSOLIDATION_BOUND_MIN = 1
_CONSOLIDATION_BOUND_MAX = 3600


def _validate_consolidation(cfg: dict) -> None:
    """Validate consolidation tuning, rejecting out-of-range values.

    ``max_inter_alert_gap_seconds`` must be an integer in [1, 3600].
    ``max_event_duration_seconds`` must be an integer in [1, 3600] or null
    (unbounded). ``representative`` must be a known strategy. Raises
    ``ValueError`` so a misconfiguration fails fast rather than misbehaving at
    request time.
    """
    if not cfg:
        return

    def _bounded_int(name, value):
        if isinstance(value, bool) or not isinstance(value, int) or not (
            _CONSOLIDATION_BOUND_MIN <= value <= _CONSOLIDATION_BOUND_MAX
        ):
            raise ValueError(
                f"rtvi_vlm.consolidation.{name} must be an integer in "
                f"[{_CONSOLIDATION_BOUND_MIN}, {_CONSOLIDATION_BOUND_MAX}], got {value!r}"
            )

    _bounded_int("max_inter_alert_gap_seconds", cfg.get("max_inter_alert_gap_seconds", 60))

    duration = cfg.get("max_event_duration_seconds", 300)
    if duration is not None:
        _bounded_int("max_event_duration_seconds", duration)

    representative = cfg.get("representative", "latest")
    if representative not in ("latest", "longest_reasoning"):
        raise ValueError(
            "rtvi_vlm.consolidation.representative must be 'latest' or "
            f"'longest_reasoning', got {representative!r}"
        )


class ChunkCombiner:
    """Strategy deciding whether a candidate chunk extends the open event.

    The consolidator's grouping, fold loop, and event construction hold **no**
    combination policy — it lives entirely in a combiner, so how chunks are
    checked and combined can be extended or replaced independently.
    ``should_extend`` returns ``(extend, reason)``: ``reason`` is ``""`` when
    the chunk extends the event, otherwise the split cause reported to the
    ``dedup_split_reason_total`` metric. Future strategies can combine on signals
    other than time — e.g. a visual-change combiner that compares consecutive
    chunks' vision embeddings / VLM reasoning to detect that the alert condition
    changed — and combiners may be composed.
    """

    def should_extend(self, event: List[dict], candidate: dict) -> Tuple[bool, str]:
        raise NotImplementedError


class TimeBoundCombiner(ChunkCombiner):
    """v1 strategy: extend while the next positive arrives within the inter-alert
    gap and the event stays within the outer duration cap (both end-based and
    inclusive). The time gap is authoritative — there is no per-session bypass.
    """

    def __init__(self, gap_seconds, max_duration):
        self._gap_seconds = gap_seconds
        self._max_duration = max_duration

    def should_extend(self, event: List[dict], candidate: dict) -> Tuple[bool, str]:
        first = event[0]
        prev = event[-1]
        if self._max_duration is not None:
            start = _parse_ts(first.get("timestamp"))
            cand_end = _parse_ts(candidate.get("end")) or _parse_ts(candidate.get("timestamp"))
            if start and cand_end and (cand_end - start).total_seconds() > self._max_duration:
                return (False, "outer")  # outer duration cap breached
        prev_end = _parse_ts(prev.get("end")) or _parse_ts(prev.get("timestamp"))
        cand_start = _parse_ts(candidate.get("timestamp"))
        if prev_end is None or cand_start is None:
            return (False, "malformed")
        if (cand_start - prev_end).total_seconds() <= self._gap_seconds:
            return (True, "")
        return (False, "gap")


class IncidentService:
    """Service for querying incidents from Elasticsearch.

    Requires an ElasticClient injected at construction time. The caller
    (e.g. the FastAPI dependency in ``realtime_routes``) owns client
    creation, configuration, and lifecycle — this service is only
    responsible for building and executing queries.
    """

    def __init__(
        self,
        es_client: Optional["ElasticClient"] = None,
        index_base: str = "mdx-vlm-incidents",
        consolidation: Optional[dict] = None,
    ):
        self._es_client = es_client
        self._index_base = index_base
        self._consolidation = consolidation or {}
        _validate_consolidation(self._consolidation)

        logger.info(
            "IncidentService initialized",
            extra={
                "es_enabled": es_client is not None,
                "index_base": self._index_base,
                # Active consolidation bounds, logged so operators can correlate
                # the dedup_* metrics with the configuration in effect.
                "consolidation_max_inter_alert_gap_seconds": self._consolidation.get(
                    "max_inter_alert_gap_seconds", 60
                ),
                "consolidation_max_event_duration_seconds": self._consolidation.get(
                    "max_event_duration_seconds", 300
                ),
                "consolidation_representative": self._consolidation.get(
                    "representative", "latest"
                ),
            },
        )

    async def list_incidents(
        self,
        sensor_id: Optional[str] = None,
        category: Optional[str] = None,
        start_time: Optional[str] = None,
        end_time: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
        consolidate: Optional[bool] = None,
    ) -> Tuple[dict, int]:
        """Query incidents from Elasticsearch.

        With ``consolidate`` true the whole matched window is grouped into
        events and ``offset``/``limit`` paginate the events, so an event is
        never split across pages; ``total`` is the number of events. With
        ``consolidate`` false the raw chunk documents are returned with
        Elasticsearch-side pagination and ``total`` is the raw match count.
        """
        now = datetime.now(timezone.utc).isoformat()
        ctx = {
            "sensor_id": sensor_id,
            "category": category,
            "limit": limit,
            "offset": offset,
        }

        if self._es_client is None:
            return {
                "status": ResponseStatus.ERROR,
                "error": ErrorCode.ELASTICSEARCH_UNAVAILABLE,
                "message": "Elasticsearch is not available",
                "timestamp": now,
            }, 503

        t0 = time.monotonic()
        try:
            must_clauses = []

            if sensor_id:
                must_clauses.append({"term": {"sensorId.keyword": sensor_id}})

            if category:
                must_clauses.append({"term": {"category.keyword": category}})

            if start_time or end_time:
                range_query: dict = {"range": {"timestamp": {}}}
                if start_time:
                    range_query["range"]["timestamp"]["gte"] = start_time
                if end_time:
                    range_query["range"]["timestamp"]["lte"] = end_time
                must_clauses.append(range_query)

            if consolidate:
                # Realtime-only discriminator (read-time filter — never mutates
                # ES). mdx-vlm-incidents-* is shared: the realtime RT-VLM path
                # sets info.chunkIdx per chunk (rtvi rt-vlm stream handler),
                # whereas verifier-path incidents (detection modules, enriched by
                # vlm_enhanced_sink) never set it. Requiring info.chunkIdx keeps
                # the consolidated view to genuine RT-VLM chunks so verifier docs
                # are never folded into an event. Raw view (consolidate=false) is
                # unfiltered.
                must_clauses.append({"exists": {"field": "info.chunkIdx"}})
                # Only CONFIRMED positives consolidate; rejected or
                # missing-verdict chunks stay queryable via the raw view.
                must_clauses.append(
                    {"term": {"info.verdict.keyword": "confirmed"}}
                )

            query = {"bool": {"must": must_clauses}} if must_clauses else {"match_all": {}}
            index_pattern = f"{self._index_base}-*"

            def _hits_to_docs(resp):
                out = []
                for hit in resp.get("hits", {}).get("hits", []):
                    doc = hit.get("_source", {})
                    doc["_id"] = hit.get("_id")
                    doc["_index"] = hit.get("_index")
                    out.append(doc)
                return out

            def _es_total(resp):
                t = resp.get("hits", {}).get("total", {})
                return t.get("value", 0) if isinstance(t, dict) else t

            if consolidate:
                # Scan newest-first: if the window exceeds the cap we keep the
                # most recent chunks (the operationally relevant ones) and drop
                # the oldest. The consolidator re-sorts each bucket ascending, so
                # scan order does not affect grouping correctness.
                response = await asyncio.to_thread(
                    self._es_client.client.search,
                    index=index_pattern,
                    query=query,
                    from_=0,
                    size=_SCAN_CAP,
                    sort=[{"timestamp": {"order": "desc"}}],
                    # Force an exact match count; without this Elasticsearch caps
                    # hits.total at 10000 and the truncation check below (total >
                    # scanned) could never fire when size == _SCAN_CAP.
                    track_total_hits=True,
                )
                duration = time.monotonic() - t0
                if INCIDENT_QUERY_DURATION is not None:
                    INCIDENT_QUERY_DURATION.observe(duration)

                raw_docs = _hits_to_docs(response)
                # track_total_hits above makes the total exact, so this alone is
                # correct: N <= cap -> all fetched -> not truncated; N > cap ->
                # only _SCAN_CAP fetched -> truncated.
                truncated = _es_total(response) > len(raw_docs)
                if truncated:
                    logger.warning(
                        "Consolidation scan hit the cap; oldest chunks dropped — "
                        "narrow the time window",
                        extra={**ctx, "scanned": len(raw_docs), "cap": _SCAN_CAP},
                    )

                events = self._consolidate(raw_docs)
                page = events[offset:offset + limit]

                logger.info(
                    "Incidents query completed",
                    extra={
                        **ctx,
                        "returned": len(page),
                        "raw_scanned": len(raw_docs),
                        "events": len(events),
                        "consolidated": True,
                        "truncated": truncated,
                        "duration_s": round(duration, 3),
                    },
                )
                return {
                    "status": ResponseStatus.SUCCESS,
                    "incidents": page,
                    "count": len(page),
                    "total": len(events),
                    "truncated": truncated,
                    "timestamp": now,
                }, 200

            response = await asyncio.to_thread(
                self._es_client.client.search,
                index=index_pattern,
                query=query,
                from_=offset,
                size=limit,
                sort=[{"timestamp": {"order": "desc"}}],
            )
            duration = time.monotonic() - t0
            if INCIDENT_QUERY_DURATION is not None:
                INCIDENT_QUERY_DURATION.observe(duration)

            incidents = _hits_to_docs(response)
            total_count = _es_total(response)

            logger.info(
                "Incidents query completed",
                extra={
                    **ctx,
                    "returned": len(incidents),
                    "consolidated": False,
                    "total": total_count,
                    "duration_s": round(duration, 3),
                },
            )
            return {
                "status": ResponseStatus.SUCCESS,
                "incidents": incidents,
                "count": len(incidents),
                "total": total_count,
                "timestamp": now,
            }, 200

        except Exception as exc:
            duration = time.monotonic() - t0
            if INCIDENT_QUERY_DURATION is not None:
                INCIDENT_QUERY_DURATION.observe(duration)
            if INCIDENT_QUERY_FAILURES is not None:
                INCIDENT_QUERY_FAILURES.inc()

            logger.error(
                "Elasticsearch query failed",
                extra={
                    **ctx,
                    "error": str(exc),
                    "error_type": type(exc).__name__,
                    "duration_s": round(duration, 3),
                },
                exc_info=True,
            )
            return {
                "status": ResponseStatus.ERROR,
                "error": ErrorCode.ELASTICSEARCH_QUERY_FAILED,
                "message": f"Elasticsearch query failed: {str(exc)}",
                "timestamp": now,
            }, 500

    # ------------------------------------------------------------------
    # Consolidation
    # ------------------------------------------------------------------
    def _combiner(self) -> ChunkCombiner:
        """The chunk-combination strategy — the single seam for *how* chunks are
        checked and combined. v1 uses the configured time bounds; replace or
        compose to combine on other signals (e.g. visual change)."""
        cfg = self._consolidation
        return TimeBoundCombiner(
            cfg.get("max_inter_alert_gap_seconds", 60),
            cfg.get("max_event_duration_seconds", 300),
        )

    def _consolidate(self, docs: List[dict]) -> List[dict]:
        """Group consecutive same-camera, same-alert-type positives on the
        current page into single events.

        Operates only on the documents already returned for this page; the
        underlying store is never modified and raw documents stay available
        via a ``consolidate=false`` query.
        """
        representative = self._consolidation.get("representative", "latest")
        combiner = self._combiner()

        t_fold = time.monotonic()
        # Aggregate-only counter (no per-sensor/category labels): everything fed
        # into the consolidator, including chunks dropped below as malformed.
        if DEDUP_CHUNKS_IN is not None and docs:
            DEDUP_CHUNKS_IN.inc(len(docs))
        groups: dict = {}
        for doc in docs:
            # Drop malformed chunks — a chunk missing its grouping keys, or with
            # a missing/unparseable ordering timestamp, cannot be placed in an
            # event.
            if not doc.get("sensorId") or not doc.get("category"):
                continue
            if _parse_ts(doc.get("timestamp")) is None:
                continue
            # Only confirmed positives participate in events.
            if _info_of(doc).get("verdict") != "confirmed":
                continue
            key = (doc.get("sensorId"), doc.get("category"))
            groups.setdefault(key, []).append(doc)

        events: List[dict] = []
        for items in groups.values():
            # Sort by (timestamp, id) so equal-timestamp chunks order
            # deterministically regardless of ES scan direction.
            items_sorted = sorted(
                items,
                key=lambda d: (
                    _parse_ts(d.get("timestamp")) or _EPOCH,
                    str(d.get("Id") or d.get("_id") or ""),
                ),
            )
            group_events: List[dict] = []
            current: List[dict] = []
            for doc in items_sorted:
                extend, reason = combiner.should_extend(current, doc) if current else (True, "")
                if current and extend:
                    current.append(doc)
                else:
                    if current:
                        group_events.append(self._build_event(current, representative))
                        if DEDUP_SPLIT_REASON is not None and reason:
                            DEDUP_SPLIT_REASON.labels(reason=reason).inc()
                    current = [doc]
            if current:
                group_events.append(self._build_event(current, representative))
            events.extend(group_events)
            if DEDUP_EVENTS_OUT is not None:
                DEDUP_EVENTS_OUT.inc(len(group_events))

        # Order: event start descending
        events.sort(
            key=lambda e: _parse_ts(e.get("timestamp")) or _EPOCH,
            reverse=True,
        )
        if DEDUP_DURATION is not None:
            DEDUP_DURATION.observe(time.monotonic() - t_fold)
        return events

    @staticmethod
    def _split_reason(current: List[dict], doc: dict, gap_seconds, max_duration) -> str:
        """The split cause (``outer`` / ``gap`` / ``malformed``) when ``doc`` does
        not extend ``current``; empty string when it extends. Thin wrapper over
        ``TimeBoundCombiner`` kept for direct unit testing of the classification."""
        return TimeBoundCombiner(gap_seconds, max_duration).should_extend(current, doc)[1]

    @staticmethod
    def _build_event(chunks: List[dict], representative: str) -> dict:
        """Build one consolidated event from its chunks.

        The event is a clone of the representative chunk — a real document, so
        the result keeps the same shape as a raw incident — with its own stable
        identity, the span widened to cover all chunks, and consolidation
        metadata added under ``info``. The underlying raw chunks remain
        retrievable via a ``consolidate=false`` query over the same window.
        """
        if representative == "longest_reasoning":
            rep = max(chunks, key=lambda c: len(_info_of(c).get("reasoning") or ""))
        else:
            rep = max(chunks, key=lambda c: _parse_ts(c.get("timestamp")) or _EPOCH)

        event = copy.deepcopy(rep)

        first_chunk = min(chunks, key=lambda c: _parse_ts(c.get("timestamp")) or _EPOCH)
        last_chunk = max(
            chunks,
            key=lambda c: _parse_ts(c.get("end")) or _parse_ts(c.get("timestamp")) or _EPOCH,
        )

        event_key = "|".join((
            str(rep.get("sensorId", "")),
            str(rep.get("category", "")),
            str(_info_of(first_chunk).get("requestId", "")),
            str(first_chunk.get("timestamp", "")),
        ))
        event_id = "evt-" + hashlib.sha1(event_key.encode("utf-8")).hexdigest()
        event["Id"] = event_id
        event["_id"] = event_id

        if first_chunk.get("timestamp"):
            event["timestamp"] = first_chunk["timestamp"]
        # Use the chunk's end only if it parses; a present-but-unparseable end
        # falls back to its timestamp so the event never carries a garbage end.
        end_value = last_chunk.get("end") if _parse_ts(last_chunk.get("end")) else last_chunk.get("timestamp")
        if end_value:
            event["end"] = end_value

        idxs = [i for i in (_to_int(_info_of(c).get("chunkIdx")) for c in chunks) if i is not None]

        # Raw chunk ids underlying the event, as a real list (traceability).
        chunk_ids = [str(cid) for cid in (c.get("Id") or c.get("_id") for c in chunks) if cid]
        event["chunk_ids"] = chunk_ids

        info = dict(event.get("info") or {})
        info["isConsolidated"] = "true"
        info["chunkCount"] = str(len(chunks))
        if idxs:
            info["chunkIdxRange"] = f"{min(idxs)}-{max(idxs)}"
        event["info"] = info

        # Merge chunk queries, but cap the stored payload — a dense window can
        # hold thousands of chunks, each with full VLM reasoning. Count the true
        # total for observability while bounding what we accumulate.
        merged_queries: list = []
        total_queries = 0
        for chunk in chunks:
            llm = chunk.get("llm")
            if isinstance(llm, dict) and isinstance(llm.get("queries"), list):
                qs = llm["queries"]
                total_queries += len(qs)
                if len(merged_queries) < _MAX_MERGED_QUERIES:
                    merged_queries.extend(qs[: _MAX_MERGED_QUERIES - len(merged_queries)])
        if total_queries:
            info["mergedQueryCount"] = str(total_queries)
            if not isinstance(event.get("llm"), dict):
                event["llm"] = {}
            event["llm"]["queries"] = merged_queries

        return event
