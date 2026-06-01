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
import logging
import time
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Optional, Tuple

from ..config import ErrorCode, ResponseStatus

if TYPE_CHECKING:
    from elastic.elastic import ElasticClient

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
    ):
        self._es_client = es_client
        self._index_base = index_base

        logger.info(
            "IncidentService initialized",
            extra={"es_enabled": es_client is not None, "index_base": self._index_base},
        )

    async def list_incidents(
        self,
        sensor_id: Optional[str] = None,
        category: Optional[str] = None,
        start_time: Optional[str] = None,
        end_time: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> Tuple[dict, int]:
        """Query incidents from Elasticsearch."""
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

            logger.info(
                "Incidents query completed",
                extra={
                    **ctx,
                    "returned": len(incidents),
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
