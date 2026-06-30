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
        )
    else:
        INCIDENT_QUERY_DURATION = None
        INCIDENT_QUERY_FAILURES = None
except ImportError:
    PROMETHEUS_ENABLED = False
    INCIDENT_QUERY_DURATION = None
    INCIDENT_QUERY_FAILURES = None


_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
# Upper bound on raw chunks scanned for a consolidated query. Consolidation
# requires a bounded time window (enforced at the route), so this is only a
# safety net for an unexpectedly dense window; Elasticsearch caps from_+size
# at index.max_result_window (10000 by default).
_SCAN_CAP = 10000


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

        logger.info(
            "IncidentService initialized",
            extra={
                "es_enabled": es_client is not None,
                "index_base": self._index_base,
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
                response = await asyncio.to_thread(
                    self._es_client.client.search,
                    index=index_pattern,
                    query=query,
                    from_=0,
                    size=_SCAN_CAP,
                    sort=[{"timestamp": {"order": "asc"}}],
                )
                duration = time.monotonic() - t0
                if INCIDENT_QUERY_DURATION is not None:
                    INCIDENT_QUERY_DURATION.observe(duration)

                raw_docs = _hits_to_docs(response)
                if _es_total(response) > len(raw_docs):
                    logger.warning(
                        "Consolidation scan hit the cap; narrow the time window",
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
                        "duration_s": round(duration, 3),
                    },
                )
                return {
                    "status": ResponseStatus.SUCCESS,
                    "incidents": page,
                    "count": len(page),
                    "total": len(events),
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
    def _consolidate(self, docs: List[dict]) -> List[dict]:
        """Group consecutive same-camera, same-alert-type positives on the
        current page into single events.

        Operates only on the documents already returned for this page; the
        underlying store is never modified and raw documents stay available
        via a ``consolidate=false`` query.
        """
        cfg = self._consolidation
        gap_seconds = cfg.get("max_inter_alert_gap_seconds", 60)
        max_duration = cfg.get("max_event_duration_seconds", 300)
        representative = cfg.get("representative", "latest")

        groups: dict = {}
        for doc in docs:
            key = (doc.get("sensorId"), doc.get("category"))
            groups.setdefault(key, []).append(doc)

        events: List[dict] = []
        for items in groups.values():
            items_sorted = sorted(items, key=lambda d: _parse_ts(d.get("timestamp")) or _EPOCH)
            current: List[dict] = []
            for doc in items_sorted:
                if current and self._is_continuous(current, doc, gap_seconds, max_duration):
                    current.append(doc)
                else:
                    if current:
                        events.append(self._build_event(current, representative))
                    current = [doc]
            if current:
                events.append(self._build_event(current, representative))

        # Order: event start descending
        events.sort(
            key=lambda e: _parse_ts(e.get("timestamp")) or _EPOCH,
            reverse=True,
        )
        return events

    @staticmethod
    def _is_continuous(current: List[dict], doc: dict, gap_seconds, max_duration) -> bool:
        """Whether ``doc`` extends the open event ``current``.

        Continuation requires the event to stay within the outer duration cap
        and the next positive to arrive within ``max_inter_alert_gap_seconds``
        of the previous chunk. The time gap is authoritative — there is no
        per-session bypass. Unparseable timestamps end the current event.
        """
        first = current[0]
        prev = current[-1]

        if max_duration is not None:
            start = _parse_ts(first.get("timestamp"))
            doc_end = _parse_ts(doc.get("end")) or _parse_ts(doc.get("timestamp"))
            if start and doc_end and (doc_end - start).total_seconds() > max_duration:
                return False

        prev_end = _parse_ts(prev.get("end")) or _parse_ts(prev.get("timestamp"))
        doc_start = _parse_ts(doc.get("timestamp"))
        if prev_end is None or doc_start is None:
            return False
        return (doc_start - prev_end).total_seconds() <= gap_seconds

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
        end_value = last_chunk.get("end") or last_chunk.get("timestamp")
        if end_value:
            event["end"] = end_value

        idxs = [i for i in (_to_int(_info_of(c).get("chunkIdx")) for c in chunks) if i is not None]

        info = dict(event.get("info") or {})
        info["isConsolidated"] = "true"
        info["chunkCount"] = str(len(chunks))
        if idxs:
            info["chunkIdxRange"] = f"{min(idxs)}-{max(idxs)}"
        event["info"] = info

        merged_queries: list = []
        for chunk in chunks:
            llm = chunk.get("llm")
            if isinstance(llm, dict) and isinstance(llm.get("queries"), list):
                merged_queries.extend(llm["queries"])
        if merged_queries and isinstance(event.get("llm"), dict):
            event["llm"]["queries"] = merged_queries

        return event
