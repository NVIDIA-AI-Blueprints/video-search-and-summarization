# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Live uploads-index readiness, independent of cached discovery and ranking limits."""

from __future__ import annotations

from typing import Any

from vss_core.vios.client import get_name_to_stream_id_map

from .clients.elastic import ElasticClient
from .content_validation import ContentInspector
from .content_validation import validate_content_contract
from .errors import BackendUnreachableError
from .errors import IndexNotFoundError
from .errors import InvalidInputError
from .runtime import RAW_INDEX_ANCHOR
from .runtime import SearchRuntime


def _source_aliases(names: dict[str, str], sources: list[str]) -> dict[str, list[str]]:
    """Share exact registered identities and supported filename stems.

    RTVI-CV camera names strip the upload filename extension while VIOS may
    retain it. Treat that known spelling as an alias only if no other VIOS
    registration could own it. Check the entire catalog, not just this cohort.
    """
    catalog: dict[str, list[str]] = {}
    owners: dict[str, set[str]] = {}
    for name, stream_id in names.items():
        aliases = list(dict.fromkeys([name, stream_id]))
        if name.lower().endswith((".mp4", ".mkv")) and len(name) > 4:
            aliases = list(dict.fromkeys([*aliases, name[:-4]]))
        catalog[name] = aliases
        for alias in aliases:
            owners.setdefault(alias, set()).add(name)
    result = {source: catalog.get(source, []) for source in sources}
    for aliases in result.values():
        for alias in aliases:
            if len(owners[alias]) > 1:
                raise InvalidInputError(f"Ambiguous registered source alias {alias!r}: {sorted(owners[alias])!r}")
    return result


async def gallery_readiness(
    runtime: SearchRuntime,
    sources: list[str],
    *,
    require_attributes: bool = True,
    content_contract: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Count exact registered identities, preserving legitimate detection gaps.

    Counts prove indexed coverage, not model quality or ingestion completion.
    Callers still perform an actual attribute-search smoke test.
    """
    if not sources or any(not source.strip() for source in sources) or len(set(sources)) != len(sources):
        raise InvalidInputError("Readiness needs distinct, nonempty source names")
    if content_contract is not None:
        content_contract = validate_content_contract(content_contract, sources, require_attributes)
    names = await get_name_to_stream_id_map(runtime.require("vst_internal_url"))
    identities = _source_aliases(names, sources)
    es = ElasticClient.from_runtime(runtime)
    behavior_es = ElasticClient.from_runtime_behavior(runtime)
    result: dict[str, Any] = {"require_attributes": require_attributes, "source_aliases": identities}
    try:
        for kind, index, field, client in (
            ("embedding", runtime.video_embed_index, "sensor.id.keyword", es),
            ("behavior", runtime.behavior_index, "sensor.id.keyword", behavior_es),
            ("raw", RAW_INDEX_ANCHOR, "sensorId.keyword", behavior_es),
        ):
            if kind != "embedding" and not require_attributes:
                result[kind] = {
                    "checked": False,
                    "available": False,
                    "index": index,
                    "total": None,
                    "gallery_total": None,
                    "counts": {},
                }
                continue
            filters = {
                str(i): {"terms": {field: identities[source]}}
                if source in names
                else {"match_none": {}}
                for i, source in enumerate(sources)
            }
            try:
                response = await client.search(
                    index=index,
                    body={
                        "size": 0,
                        "track_total_hits": True,
                        "aggs": {"sources": {"filters": {"filters": filters}}},
                    },
                    allow_partial_search_results=False,
                )
                shards = response.get("_shards")
                if (
                    response.get("timed_out") is not False
                    or not isinstance(shards, dict)
                    or any(
                        type(shards.get(key)) is not int or shards[key] < 0 for key in ("total", "successful", "failed")
                    )
                    or shards["failed"] != 0
                    or shards["successful"] != shards["total"]
                ):
                    raise BackendUnreachableError("elasticsearch", f"Partial readiness response for {index}")
                total = response["hits"]["total"]
                if not isinstance(total, dict) or total.get("relation") != "eq":
                    raise ValueError("Inexact readiness total")
                buckets = response["aggregations"]["sources"]["buckets"]
                counts = {source: buckets[str(i)]["doc_count"] for i, source in enumerate(sources)}
                values = [total["value"], *counts.values()]
                if any(type(value) is not int or value < 0 for value in values):
                    raise ValueError("Invalid readiness count")
                result[kind] = {
                    "checked": True,
                    "available": True,
                    "index": index,
                    "total": total["value"],
                    "counts": counts,
                    "gallery_total": sum(counts.values()),
                }
            except IndexNotFoundError:
                result[kind] = {
                    "checked": True,
                    "available": False,
                    "index": index,
                    "total": 0,
                    "gallery_total": 0,
                    "counts": dict.fromkeys(sources, 0),
                }
            except (KeyError, TypeError, ValueError) as error:
                raise BackendUnreachableError("elasticsearch", f"Malformed readiness response for {index}") from error
        if content_contract is not None:
            content: dict[str, Any] = {"checked": True, "contract": content_contract, "valid": True}
            for kind, field, client, includes in (
                ("embedding", "sensor.id.keyword", es, ["sensor.id", "llm.info", "llm.visionEmbeddings"]),
                ("raw", "sensorId.keyword", behavior_es, ["sensorId", "id"]),
            ):
                if kind == "raw" and not require_attributes:
                    continue
                if not result[kind]["available"]:
                    content[kind] = {"checked": False, "exhaustive": False, "reason": "index unavailable"}
                    content["valid"] = False
                    continue
                inspector = ContentInspector(content_contract, identities, kind)
                scan = await client.visit_documents(
                    index=result[kind]["index"],
                    filters=[{"terms": {field: list(dict.fromkeys(alias for aliases in identities.values() for alias in aliases))}}],
                    source_fields=includes, document_limit=content_contract["document_limit"], visitor=inspector.visit,
                )
                content[kind] = {"checked": True, **scan, **inspector.result}
                if inspector.result["violation_count"]:
                    content["valid"] = False
                if kind == "embedding" and any(not row["documents"] for row in inspector.result["per_source"].values()):
                    content["valid"] = False
                if kind == "raw" and not inspector.result["documents_checked"]:
                    content["valid"] = False
            result["content_validation"] = content
    finally:
        await behavior_es.aclose()
        await es.aclose()
    result["missing_embeddings"] = [source for source in sources if not result["embedding"]["counts"][source]]
    result["attribute_gaps"] = [
        source
        for source in sources
        if require_attributes and any(not result[kind]["counts"][source] for kind in ("behavior", "raw"))
    ]
    result["unregistered"] = [source for source in sources if source not in names]
    result["frame_lookup_enabled"] = bool(runtime.frames_index and runtime.enable_frame_lookup)
    if require_attributes and result["raw"]["available"] and not result["frame_lookup_enabled"]:
        result["error"] = "Raw index is live but frame lookup is disabled in cached discovery; re-run vss configure."
    result["ready"] = not result["missing_embeddings"] and (
        not require_attributes
        or (
            result["frame_lookup_enabled"]
            and all(result[kind]["available"] and result[kind]["gallery_total"] > 0 for kind in ("behavior", "raw"))
        )
    )
    if "content_validation" in result:
        result["ready"] = result["ready"] and result["content_validation"]["valid"]
    return result
