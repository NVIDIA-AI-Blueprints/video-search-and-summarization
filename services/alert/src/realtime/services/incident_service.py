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
# Two adjacent chunks may skip at most this many indices and still count as the
# same continuous event (tolerates an occasional dropped/non-positive chunk).
_MAX_CHUNK_SKIP = 2


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

        When consolidation is active, consecutive positives from the same
        camera and alert type on the returned page are grouped into single
        events. ``consolidate`` overrides the configured default per request;
        ``total`` always reflects the raw Elasticsearch match count.
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

            hits = response.get("hits", {})
            total = hits.get("total", {})
            total_count = total.get("value", 0) if isinstance(total, dict) else total

            incidents = []
            for hit in hits.get("hits", []):
                doc = hit.get("_source", {})
                doc["_id"] = hit.get("_id")
                doc["_index"] = hit.get("_index")
                incidents.append(doc)

            items = self._consolidate(incidents) if consolidate else incidents

            logger.info(
                "Incidents query completed",
                extra={
                    **ctx,
                    "returned": len(items),
                    "raw_hits": len(incidents),
                    "consolidated": bool(consolidate),
                    "total": total_count,
                    "duration_s": round(duration, 3),
                },
            )

            return {
                "status": ResponseStatus.SUCCESS,
                "incidents": items,
                "count": len(items),
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

        # Newest first, matching the raw query's sort order.
        events.sort(
            key=lambda e: _parse_ts(e.get("end")) or _parse_ts(e.get("timestamp")) or _EPOCH,
            reverse=True,
        )
        return events

    @staticmethod
    def _is_continuous(current: List[dict], doc: dict, gap_seconds, max_duration) -> bool:
        """Whether ``doc`` extends the open event ``current``.

        Continuation requires the event to stay within the duration cap and
        either belong to the same caption session with consecutive chunk
        indices, or fall within the inter-alert gap of the previous chunk.
        """
        first = current[0]
        prev = current[-1]

        if max_duration is not None:
            start = _parse_ts(first.get("timestamp"))
            doc_end = _parse_ts(doc.get("end")) or _parse_ts(doc.get("timestamp"))
            if start and doc_end and (doc_end - start).total_seconds() > max_duration:
                return False

        prev_info = _info_of(prev)
        doc_info = _info_of(doc)
        prev_idx = _to_int(prev_info.get("chunkIdx"))
        doc_idx = _to_int(doc_info.get("chunkIdx"))
        same_session = bool(
            prev_info.get("requestId")
            and prev_info.get("requestId") == doc_info.get("requestId")
            and prev_idx is not None
            and doc_idx is not None
            and 0 < (doc_idx - prev_idx) <= _MAX_CHUNK_SKIP
        )

        within_gap = False
        prev_end = _parse_ts(prev.get("end")) or _parse_ts(prev.get("timestamp"))
        doc_start = _parse_ts(doc.get("timestamp"))
        if prev_end and doc_start:
            within_gap = (doc_start - prev_end).total_seconds() <= gap_seconds

        return same_session or within_gap

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
