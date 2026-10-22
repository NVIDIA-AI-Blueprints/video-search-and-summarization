# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Exact frame refinement with request-local compatibility and concurrency state."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from dataclasses import field
from fnmatch import fnmatchcase
import hashlib
import json
import math
import time
from typing import Any

from ..errors import InvalidInputError

FrameResult = tuple[Any, dict[str, Any] | None, float, str] | None
SCORING_VERSION = "shifted-cosine-v2-winning-object"


@dataclass
class FrameRefinementContext:
    scoring: str = "auto"
    transport: str = "auto"
    diagnostics: list[dict[str, Any]] = field(default_factory=list)
    _condition: asyncio.Condition = field(default_factory=asyncio.Condition, repr=False)
    _available: int = field(default=16, repr=False)
    _profiles: dict[Any, asyncio.Task] = field(default_factory=dict, repr=False)

    @asynccontextmanager
    async def permits(self, count: int):
        """Acquire a batch atomically: concurrent batches cannot deadlock."""
        async with self._condition:
            await self._condition.wait_for(lambda: self._available >= count)
            self._available -= count
        try:
            yield
        finally:
            async with self._condition:
                self._available += count
                self._condition.notify_all()

    async def profile(self, es: Any, index: Any) -> dict[str, Any]:
        key = (id(es), tuple(index) if isinstance(index, list) else index)
        if key not in self._profiles:

            async def inspect():
                # Capability probing is itself a search and shares the request budget.
                async with self.permits(1):
                    return await resolve_frame_capabilities(es, index, scoring=self.scoring, transport=self.transport)

            self._profiles[key] = asyncio.create_task(inspect())
        try:
            return await asyncio.shield(self._profiles[key])
        except Exception:
            self._profiles.pop(key, None)
            raise

    async def aclose(self) -> None:
        """Cancel and drain capability reads owned by this top-level request."""
        tasks = list(self._profiles.values())
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


frame_refinement_context: ContextVar[FrameRefinementContext | None] = ContextVar(
    "frame_refinement_context", default=None
)


frame_diagnostic_sink: ContextVar[list[dict[str, Any]] | None] = ContextVar("frame_diagnostic_sink", default=None)


def record_diagnostic(context: FrameRefinementContext, row: dict[str, Any]) -> None:
    """Publish to shared request diagnostics and the owning attribute invocation."""
    context.diagnostics.append(row)
    sink = frame_diagnostic_sink.get()
    if sink is not None and sink is not context.diagnostics:
        sink.append(row)


def _property(mapping: dict, dotted: str) -> dict:
    current = mapping
    for part in dotted.split("."):
        current = current.get("properties", {}).get(part, {})
    return current


async def resolve_frame_capabilities(
    es: Any, index: str | list[str], *, scoring="auto", transport="auto"
) -> dict[str, Any]:
    """Resolve deterministic mapping identity; transport/auth errors propagate."""
    if scoring not in {"auto", "source", "indexed"} or transport not in {"auto", "single", "msearch"}:
        raise InvalidInputError("Invalid frame scoring or transport mode")
    inspect = getattr(es, "frame_capabilities", None)
    verified = callable(inspect)
    raw = await inspect(index=index) if verified else None
    server_version = None
    if isinstance(raw, dict) and "indices" in raw and "server_version" in raw:
        server_version = raw["server_version"]
        raw = raw["indices"]
    if verified and not isinstance(raw, dict):
        raise InvalidInputError("Frame mapping inspection returned unavailable metadata")
    indices = []
    indexed = bool(raw)
    source = bool(raw)
    dimensions = set()
    for name, item in sorted((raw or {}).items()):
        mapping = item.get("mappings")
        settings = item.get("settings", {}).get("index", {})
        uuid = settings.get("uuid")
        if not isinstance(mapping, dict) or not isinstance(uuid, str) or not uuid:
            raise InvalidInputError("Frame mapping inspection omitted mapping or index UUID")
        vector = _property(mapping, "objects.embedding.vector")
        dims = vector.get("dims")
        nested = _property(mapping, "objects").get("type") == "nested"
        indexed = (
            indexed
            and nested
            and vector.get("type") == "dense_vector"
            and vector.get("element_type", "float") == "float"
            and type(dims) is int
        )
        if type(dims) is int:
            dimensions.add(dims)
        source_config = mapping.get("_source", {})
        excluded = source_config.get("excludes", [])
        included = source_config.get("includes", [])
        excluded = [excluded] if isinstance(excluded, str) else excluded
        included = [included] if isinstance(included, str) else included
        required_fields = ("objects.id", "objects.embedding.vector", "objects.bbox")

        def retained(field, excluded=excluded, included=included):
            parents = [field, field.rsplit(".", 1)[0], "objects"]
            return not any(fnmatchcase(parent, pattern) for pattern in excluded for parent in parents) and (
                not included or any(fnmatchcase(parent, pattern) for pattern in included for parent in parents)
            )

        exclude_vectors = settings.get("mapping", {}).get(
            "exclude_source_vectors", settings.get("mapping.exclude_source_vectors", False)
        )
        source_ok = (
            nested
            and source_config.get("enabled", True)
            and str(exclude_vectors).lower() != "true"
            and all(retained(field) for field in required_fields)
        )
        source = source and source_ok
        indices.append(
            {
                "name": name,
                "uuid": uuid,
                "dimensions": dims,
                "nested": nested,
                "source_compatible": bool(source_ok),
                "mapping_hash": hashlib.sha256(
                    json.dumps(mapping, sort_keys=True, separators=(",", ":")).encode()
                ).hexdigest(),
            }
        )
    indexed = indexed and len(dimensions) == 1
    enabled = not verified or bool(indices)
    reasons = []
    if scoring == "indexed" and not indexed:
        raise InvalidInputError("Frame indices do not have compatible nested float dense vectors")
    selected = "indexed" if scoring != "source" and indexed else "source"
    if enabled and selected == "source" and verified and not source:
        raise InvalidInputError("Frame indices do not retain compatible source vectors")
    if not enabled:
        reasons.append("no concrete frame indices resolved")
    elif not verified:
        reasons.append("mapping inspection unavailable; source compatibility unverified")
    elif selected == "source" and scoring == "auto":
        reasons.append("indexed mappings incompatible; source scoring selected")
    has_multi = callable(getattr(es, "msearch", None))
    probe = getattr(es, "frame_transport_capability", None)
    if enabled and has_multi and transport != "single" and callable(probe):
        has_multi = await probe(index=[item["name"] for item in indices] if verified else index)
    if transport == "msearch" and not has_multi:
        raise InvalidInputError("Client or backend does not support multi-search")
    selected_transport = "msearch" if transport != "single" and has_multi else "single"
    if transport == "auto" and not has_multi:
        reasons.append("multi-search unavailable on client or backend")
    profile = {
        "enabled": enabled,
        "requested_scoring": scoring,
        "requested_transport": transport,
        "selected_scoring": selected,
        "selected_transport": selected_transport,
        "transport_policy": {"auto_msearch_min_unique": 16},
        "indices": indices,
        "dimensions": sorted(dimensions),
        "verified": verified,
        "reasons": reasons,
        "scoring_version": SCORING_VERSION,
        "server_version": server_version,
    }
    profile["fingerprint"] = hashlib.sha256(
        json.dumps(profile, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return profile


def _validate_vector(vector: list[float], dims: list[int]) -> None:
    if (
        not vector
        or any(not isinstance(v, (int, float)) or isinstance(v, bool) or not math.isfinite(v) for v in vector)
        or not any(v != 0 for v in vector)
    ):
        raise InvalidInputError("Frame query vector must be finite, nonempty and nonzero")
    if dims and any(dim != len(vector) for dim in dims):
        raise InvalidInputError("Frame query vector dimensions do not match resolved indices")


# Shared source kernel computes the score AND winning object from identical data.
_SOURCE_KERNEL = """
double best = -2.0; def winner = null;
if (params._source.containsKey('objects')) {
 for (def obj : params._source.objects) {
  if (obj.id != params.target_id || !obj.containsKey('embedding') || !obj.embedding.containsKey('vector')) continue;
  def v = obj.embedding.vector;
  if (v.size() != params.query_vector.size()) throw new IllegalArgumentException('frame vector dimension mismatch');
  double dot = 0; double norm = 0; double qnorm = 0;
  for (int j=0; j<v.size(); j++) { dot += v[j]*params.query_vector[j]; norm += v[j]*v[j]; qnorm += params.query_vector[j]*params.query_vector[j]; }
  if (norm <= 0) continue;
  double cosine = dot / (Math.sqrt(norm)*Math.sqrt(qnorm));
  if (Double.isNaN(cosine) || Double.isInfinite(cosine)) throw new IllegalArgumentException('invalid frame vector');
  if (cosine > best) { best = cosine; winner = obj; }
 }
}
"""


def frame_lookup_body(sensor_id, object_id, start_time, end_time, query_embedding, *, scoring):
    filters = [
        {"term": {"sensorId.keyword": sensor_id}},
        {"range": {"timestamp": {"gte": start_time, **({"lte": end_time} if end_time else {})}}},
    ]
    params = {"query_vector": query_embedding, "target_id": object_id}
    if scoring == "indexed":
        nested = {
            "path": "objects",
            "score_mode": "max",
            "query": {
                "script_score": {
                    "query": {
                        "bool": {
                            "filter": [
                                {"term": {"objects.id.keyword": object_id}},
                                {"exists": {"field": "objects.embedding.vector"}},
                            ]
                        }
                    },
                    "script": {
                        "source": "(cosineSimilarity(params.query_vector, 'objects.embedding.vector') + 1.0) / 2.0",
                        "params": {"query_vector": query_embedding},
                    },
                }
            },
            "inner_hits": {
                "name": "winning_object",
                "size": 1,
                "sort": [{"_score": "desc"}],
                "_source": ["objects.id", "objects.bbox"],
            },
        }
        query = {"bool": {"filter": filters, "must": [{"nested": nested}]}}
    else:
        query = {
            "function_score": {
                "query": {
                    "bool": {
                        "filter": filters,
                        "must": [{"nested": {"path": "objects", "query": {"term": {"objects.id.keyword": object_id}}}}],
                    }
                },
                "script_score": {
                    "script": {
                        "source": _SOURCE_KERNEL + "return winner == null ? 0.0 : (best+1.0)/2.0;",
                        "params": params,
                    }
                },
                "boost_mode": "replace",
            }
        }
    body = {"query": query, "size": 1, "_source": ["id", "timestamp", "sensorId"]}
    if scoring == "source":
        body["script_fields"] = {
            "winning_object": {
                "script": {
                    "source": _SOURCE_KERNEL
                    + "return winner == null ? null : ['id':winner.id, 'bbox':winner.containsKey('bbox') ? winner.bbox : null];",
                    "params": params,
                }
            }
        }
    return body


def parse_frame_response(response: Any, object_id: str, *, scoring: str) -> FrameResult:
    if (
        not hasattr(response, "get")
        or response.get("error")
        or response.get("timed_out")
        or response.get("_shards", {}).get("failed", 0)
    ):
        raise ValueError("Failed or partial frame response")
    hits = response.get("hits", {}).get("hits")
    if not isinstance(hits, list):
        raise ValueError("Malformed frame hits")
    if not hits:
        return None
    hit = hits[0]
    score = float(hit["_score"])
    if not math.isfinite(score) or not -1e-6 <= score <= 1.000001:
        raise ValueError("Invalid shifted cosine score")
    source = hit["_source"]
    if scoring == "indexed":
        winners = hit.get("inner_hits", {}).get("winning_object", {}).get("hits", {}).get("hits", [])
        if not winners:
            raise ValueError("Indexed score omitted winning nested object")
        winner = winners[0]["_source"]
    else:
        winners = hit.get("fields", {}).get("winning_object", [])
        # Legacy injected clients may return the original projected source shape.
        if not winners:
            winners = [obj for obj in source.get("objects", []) if obj.get("id") == object_id]
            if len(winners) > 1:
                raise ValueError("Legacy frame response has ambiguous duplicate object identity")
        if not winners or winners[0] is None:
            return None
        winner = winners[0]
    if str(winner.get("id")) != str(object_id):
        raise ValueError("Frame winner identity differs from requested object")
    return source.get("id"), winner.get("bbox"), score, source.get("timestamp", "")


async def refine_frames(
    *, es, frames_index, keys, start_time, end_time, query_embedding, context: FrameRefinementContext
) -> list[FrameResult]:
    if not start_time or not end_time:
        record_diagnostic(
            context, {"kind": "lookup", "status": "skipped", "count": len(keys), "reason": "missing time bounds"}
        )
        return [None] * len(keys)
    profile = await context.profile(es, frames_index)
    record_diagnostic(context, {"kind": "capabilities", **profile})
    if not profile["enabled"]:
        record_diagnostic(
            context, {"kind": "lookup", "status": "skipped", "count": len(keys), "reason": "no frame indices"}
        )
        return [None] * len(keys)
    _validate_vector(query_embedding, profile["dimensions"])
    unique = list(dict.fromkeys(keys))
    result = {}
    scoring = profile["selected_scoring"]
    execution_index = [item["name"] for item in profile["indices"]] if profile["verified"] else frames_index
    selected_transport = profile["selected_transport"]
    if context.transport == "auto" and len(unique) < profile["transport_policy"]["auto_msearch_min_unique"]:
        selected_transport = "single"

    def record(key, response=None, error=None, elapsed=0):
        try:
            if error is not None:
                raise error
            value = parse_frame_response(response, key[1], scoring=scoring)
            status = "succeeded" if value is not None else "no_match"
            result[key] = value
        except Exception as exc:
            status = "failed"
            result[key] = None
            error = exc
        record_diagnostic(
            context,
            {
                "kind": "lookup",
                "sensor_id": key[0],
                "object_id": key[1],
                "status": status,
                "elapsed_seconds": elapsed,
                "scoring": scoring,
                "transport": selected_transport,
                **({"reason": str(error)} if error else {}),
            },
        )

    def body(key):
        return frame_lookup_body(*key, start_time, end_time, query_embedding, scoring=scoring)

    if selected_transport == "msearch":
        for offset in range(0, len(unique), 16):
            batch = unique[offset : offset + 16]
            started = time.perf_counter()
            try:
                async with context.permits(len(batch)):
                    searches = []
                    for key in batch:
                        searches.extend([{}, body(key)])
                    response = await es.msearch(index=execution_index, searches=searches, max_concurrent_searches=16)
                responses = response.get("responses")
                if not isinstance(responses, list) or len(responses) != len(batch):
                    raise ValueError("Multi-search response count does not match lookup count")
                for key, item in zip(batch, responses, strict=True):
                    record(key, response=item, elapsed=time.perf_counter() - started)
            except Exception as exc:
                for key in batch:
                    record(key, error=exc, elapsed=time.perf_counter() - started)
    else:

        async def worker(queue):
            while queue:
                key = queue.pop()
                started = time.perf_counter()
                try:
                    async with context.permits(1):
                        response = await es.search(index=execution_index, body=body(key))
                    record(key, response=response, elapsed=time.perf_counter() - started)
                except Exception as exc:
                    record(key, error=exc, elapsed=time.perf_counter() - started)

        queue = list(reversed(unique))
        tasks = [asyncio.create_task(worker(queue)) for _ in range(min(16, len(unique)))]
        try:
            await asyncio.gather(*tasks)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
    return [result[key] for key in keys]
