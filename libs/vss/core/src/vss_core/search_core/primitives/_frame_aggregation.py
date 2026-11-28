# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Query-independent frame representatives for any indexed object/embedding model.

All state is local to a retrieval call. Scores stay in the existing shifted
cosine space. No text rewriting, attribute vocabulary, or support prior is used.
"""

from __future__ import annotations

import asyncio
import math
from typing import TYPE_CHECKING
from typing import Any

from ..clients.protocols import MultiSearchIndex
from ..errors import BackendUnreachableError
from . import _attribute_helpers as ah

if TYPE_CHECKING:
    from datetime import datetime

    from ..clients.protocols import ElasticIndex


def _unit(vector: list[float], dimension: int) -> list[float]:
    if not isinstance(vector, (list, tuple)) or len(vector) != dimension:
        raise BackendUnreachableError("elasticsearch", "Indexed vector dimension does not match the query")
    try:
        values = [float(x) for x in vector]
        norm = math.hypot(*values)
    except (TypeError, ValueError, OverflowError) as exc:
        raise BackendUnreachableError("elasticsearch", "Invalid indexed vector") from exc
    if not norm or not math.isfinite(norm):
        raise BackendUnreachableError("elasticsearch", "Indexed vector must be finite and nonzero")
    return [x / norm for x in values]


def _key(candidate: dict[str, Any]) -> tuple[str, str] | None:
    source = candidate.get("_source") or {}
    sensor = (source.get("sensor") or {}).get("id")
    obj = (source.get("object") or {}).get("id")
    return (str(sensor), str(obj)) if sensor not in (None, "") and obj not in (None, "") else None


def _centers(candidates: list[dict[str, Any]], dimension: int) -> dict[tuple[str, str], list[float]]:
    vectors: dict[tuple[str, str], list[list[float]]] = {}
    for hit in candidates:
        key = _key(hit)
        if key is None:
            continue
        embeddings = (hit.get("_source") or {}).get("embeddings") or []
        if isinstance(embeddings, dict):
            embeddings = [embeddings]
        rows = []
        for embedding in embeddings:
            if not isinstance(embedding, dict):
                raise BackendUnreachableError("elasticsearch", "Invalid behavior embedding record")
            vector = embedding.get("vector")
            if vector is not None:
                rows.append(_unit(vector, dimension))
        if not rows:
            raise BackendUnreachableError("elasticsearch", "Representative scoring requires behavior vectors")
        vectors.setdefault(key, []).extend(rows)
    return {
        key: _unit([math.fsum(v[j] for v in rows) / len(rows) for j in range(dimension)], dimension)
        for key, rows in vectors.items()
    }


def _check_response(response: dict[str, Any]) -> None:
    if response.get("error") or response.get("timed_out") or response.get("_shards", {}).get("failed", 0):
        raise BackendUnreachableError("elasticsearch", "Incomplete representative frame lookup")


async def _fetch(es: ElasticIndex, index: str | list[str], bodies: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if isinstance(es, MultiSearchIndex):
        searches = [part for body in bodies for part in ({}, body)]
        result = await es.msearch(index=index, searches=searches, max_concurrent_searches=ah.FRAME_LOOKUP_CONCURRENCY)
        responses = result.get("responses", [])
        if len(responses) != len(bodies):
            raise BackendUnreachableError("elasticsearch", "Representative frame response count mismatch")
    else:
        responses = []
        for offset in range(0, len(bodies), ah.FRAME_LOOKUP_CONCURRENCY):
            tasks = [
                asyncio.create_task(es.search(index=index, body=body, allow_partial_search_results=False))
                for body in bodies[offset : offset + ah.FRAME_LOOKUP_CONCURRENCY]
            ]
            try:
                responses.extend(await asyncio.gather(*tasks))
            except BaseException:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                raise
    for response in responses:
        _check_response(response)
    return responses


async def aggregate_frame_lookups(
    candidates: list[dict[str, Any]],
    query_embedding: list[float],
    frames_index: str | list[str],
    timestamp_start: datetime,
    timestamp_end: datetime,
    es: ElasticIndex,
    representative_weight: float,
    *,
    representative_candidates: list[dict[str, Any]] | None = None,
) -> list[ah.FrameLookupResult | None]:
    """Blend representative/query-best cosines, preserving best-query evidence.

    The representative is the frame nearest the mean normalized behavior vector
    for that object in the selected candidate pool. Missing frame evidence keeps
    the existing behavior-score fallback. Partial lookups fail explicitly.
    """
    query = _unit(query_embedding, len(query_embedding))
    centers = _centers(representative_candidates if representative_candidates is not None else candidates, len(query))
    keys = list(dict.fromkeys(key for hit in candidates if (key := _key(hit)) is not None))
    start = timestamp_start.isoformat().replace("+00:00", "Z")
    end = timestamp_end.isoformat().replace("+00:00", "Z")
    by_key: dict[tuple[str, str], ah.FrameLookupResult] = {}
    prepared = ah._prepare_frame_query(query_embedding)
    for offset in range(0, len(keys), ah.FRAME_MSEARCH_BATCH_SIZE):
        batch = keys[offset : offset + ah.FRAME_MSEARCH_BATCH_SIZE]
        bodies = []
        for sensor, obj in batch:
            bodies.extend(
                [
                    ah._frame_lookup_body(
                        sensor, obj, start, end, query_embedding, prepared=prepared, require_embedding=True
                    ),
                    ah._frame_lookup_body(
                        sensor, obj, start, end, centers[(sensor, obj)], require_embedding=True, include_vector=True
                    ),
                ]
            )
        responses = await _fetch(es, frames_index, bodies)
        for i, key in enumerate(batch):
            maximum, representative = responses[2 * i : 2 * i + 2]
            best = ah._parse_frame_lookup(maximum, key[1])
            hits = representative.get("hits", {}).get("hits", [])
            if best[2] is None and not hits:
                by_key[key] = best
                continue
            if best[2] is None or not hits:
                raise BackendUnreachableError("elasticsearch", "Frame evidence changed during representative scoring")
            vector = next(
                (
                    o.get("embedding", {}).get("vector")
                    for o in hits[0]["_source"].get("objects", [])
                    if str(o.get("id")) == key[1]
                ),
                None,
            )
            if vector is None:
                raise BackendUnreachableError("elasticsearch", "Representative object has no vector")
            cosine = max(
                -1.0, min(1.0, math.fsum(x * y for x, y in zip(query, _unit(vector, len(query)), strict=True)))
            )
            score = representative_weight * (1 + cosine) / 2 + (1 - representative_weight) * best[2]
            by_key[key] = best[0], best[1], score, best[3]
    return [by_key.get(_key(hit)) for hit in candidates]
