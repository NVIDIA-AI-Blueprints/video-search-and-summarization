#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Read-only snapshot of persisted VSS unified memory in Elasticsearch."""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any, TextIO

from elasticsearch import Elasticsearch, NotFoundError


def _reexec_with_local_environment() -> None:
    venv_root = Path(__file__).resolve().parents[1] / ".venv"
    venv_python = venv_root / "bin" / "python"
    if venv_python.is_file() and Path(sys.prefix).resolve() != venv_root.resolve():
        os.execv(str(venv_python), [str(venv_python), str(Path(__file__).resolve()), *sys.argv[1:]])


_reexec_with_local_environment()

from pydantic import ValidationError  # noqa: E402

from vss_unified_memory.adapters.cli.mapper import map_validation_error  # noqa: E402
from vss_unified_memory.adapters.cli.output_models import ErrorOutput, OutputModel  # noqa: E402
from vss_unified_memory.application.observability import append_observability_log  # noqa: E402
from vss_unified_memory.config import Settings  # noqa: E402
from vss_unified_memory.domain.models import RecordType  # noqa: E402

logger = logging.getLogger(__name__)

_SUMMARY_SOURCE_FIELDS = [
    "summary_id",
    "video_id",
    "media_name",
    "created_at",
    "event_count",
    "description",
    "start_seconds",
    "end_seconds",
    "summary_chunks.chunk_id",
]
_EVENT_SOURCE_FIELDS = [
    "summary_id",
    "description",
    "start_seconds",
    "end_seconds",
    "event_chunks.chunk_id",
]
_PAGE_SIZE = 100


def _write_output(stdout: TextIO, output: OutputModel | dict[str, Any]) -> None:
    if isinstance(output, OutputModel):
        stdout.write(output.model_dump_json() + "\n")
    else:
        stdout.write(json.dumps(output, separators=(",", ":"), default=str) + "\n")


def _count_documents(client: Elasticsearch, index: str, record_type: RecordType) -> int:
    try:
        response = client.count(index=index, query={"term": {"record_type": record_type.value}})
    except NotFoundError:
        return 0
    return int(response["count"])


def _paginated_hits(
    client: Elasticsearch,
    *,
    index: str,
    query: dict[str, Any],
    source: list[str],
    sort: list[dict[str, str]],
) -> Iterator[dict[str, Any]]:
    search_after: list[Any] | None = None
    while True:
        body: dict[str, Any] = {
            "query": query,
            "_source": source,
            "size": _PAGE_SIZE,
            "sort": sort,
        }
        if search_after is not None:
            body["search_after"] = search_after
        response = client.search(index=index, body=body)
        hits = response["hits"]["hits"]
        if not hits:
            break
        yield from hits
        search_after = hits[-1]["sort"]


def _time_range(source: dict[str, Any]) -> dict[str, float] | None:
    start = source.get("start_seconds")
    end = source.get("end_seconds")
    if start is None or end is None:
        return None
    return {"start_seconds": float(start), "end_seconds": float(end)}


def _chunk_metadata(source: dict[str, Any], chunk_field: str) -> tuple[bool, int]:
    chunks = source.get(chunk_field) or []
    return bool(chunks), len(chunks)


def _collect_event_stats(client: Elasticsearch, index: str) -> dict[str, dict[str, Any]]:
    stats: dict[str, dict[str, Any]] = {}
    try:
        for hit in _paginated_hits(
            client,
            index=index,
            query={"term": {"record_type": RecordType.VIDEO_EVENT.value}},
            source=_EVENT_SOURCE_FIELDS,
            sort=[{"summary_id": "asc"}, {"ordinal": "asc"}, {"id": "asc"}],
        ):
            source = hit["_source"]
            summary_id = source["summary_id"]
            entry = stats.setdefault(
                summary_id,
                {
                    "event_chars_total": 0,
                    "has_event_chunks": False,
                    "event_chunk_count": 0,
                    "time_starts": [],
                    "time_ends": [],
                },
            )
            description = source.get("description") or ""
            entry["event_chars_total"] += len(description)
            has_chunks, chunk_count = _chunk_metadata(source, "event_chunks")
            entry["has_event_chunks"] = entry["has_event_chunks"] or has_chunks
            entry["event_chunk_count"] += chunk_count
            time_range = _time_range(source)
            if time_range is not None:
                entry["time_starts"].append(time_range["start_seconds"])
                entry["time_ends"].append(time_range["end_seconds"])
    except NotFoundError:
        return {}
    return stats


def build_snapshot(client: Elasticsearch, index: str) -> dict[str, Any]:
    summary_count = _count_documents(client, index, RecordType.VIDEO_SUMMARY)
    event_count = _count_documents(client, index, RecordType.VIDEO_EVENT)
    event_stats = _collect_event_stats(client, index)

    summaries: list[dict[str, Any]] = []
    try:
        for hit in _paginated_hits(
            client,
            index=index,
            query={"term": {"record_type": RecordType.VIDEO_SUMMARY.value}},
            source=_SUMMARY_SOURCE_FIELDS,
            sort=[{"created_at": "asc"}, {"summary_id": "asc"}],
        ):
            source = hit["_source"]
            summary_id = source["summary_id"]
            has_summary_chunks, summary_chunk_count = _chunk_metadata(source, "summary_chunks")
            related = event_stats.get(summary_id, {})
            event_chars_total = int(related.get("event_chars_total", 0))
            has_event_chunks = bool(related.get("has_event_chunks", False))
            event_chunk_count = int(related.get("event_chunk_count", 0))
            chunk_count_estimate = summary_chunk_count + event_chunk_count
            summary_time_range = _time_range(source)
            if summary_time_range is None and related.get("time_starts"):
                summary_time_range = {
                    "start_seconds": min(related["time_starts"]),
                    "end_seconds": max(related["time_ends"]),
                }
            summaries.append(
                {
                    "summary_id": summary_id,
                    "video_id": source.get("video_id"),
                    "media_name": source.get("media_name"),
                    "created_at": source.get("created_at"),
                    "event_count": source.get("event_count", 0),
                    "summary_chars": len(source.get("description") or ""),
                    "event_chars_total": event_chars_total,
                    "time_range": summary_time_range,
                    "has_summary_chunks": has_summary_chunks,
                    "has_event_chunks": has_event_chunks,
                    "chunk_count_estimate": chunk_count_estimate if chunk_count_estimate else None,
                }
            )
    except NotFoundError:
        summaries = []

    return {
        "index": index,
        "total_documents": summary_count + event_count,
        "summary_count": summary_count,
        "event_count": event_count,
        "summaries": summaries,
    }


def run_cli(
    stdout: TextIO,
    *,
    settings: Settings,
) -> int:
    started = time.perf_counter()
    try:
        client = Elasticsearch(str(settings.elasticsearch_endpoint), request_timeout=settings.request_timeout_seconds)
        snapshot = build_snapshot(client, settings.elasticsearch_index)
        observability = {
            "summary_count": snapshot["summary_count"],
            "event_count": snapshot["event_count"],
            "total_documents": snapshot["total_documents"],
            "latency_ms": {"total": (time.perf_counter() - started) * 1000.0},
        }
        _write_output(stdout, snapshot)
        append_observability_log(
            settings.observability_log,
            tool_name="inspect_memory",
            status="complete",
            observability=observability,
        )
        return 0
    except Exception:
        logger.exception("inspect_memory failed")
        _write_output(
            stdout,
            ErrorOutput(
                error_code="inspect_failed",
                message="failed to inspect Elasticsearch memory index",
                retryable=True,
            ),
        )
        append_observability_log(
            settings.observability_log,
            tool_name="inspect_memory",
            status="failed",
        )
        return 5


def main() -> int:
    logging.basicConfig(level=logging.INFO, stream=sys.stderr)
    if len(sys.argv) != 1:
        _write_output(
            sys.stdout,
            ErrorOutput(
                error_code="invalid_invocation",
                message="command-line arguments are not accepted",
                retryable=False,
            ),
        )
        return 2
    try:
        settings = Settings()
    except ValidationError as error:
        _write_output(sys.stdout, map_validation_error(error, error_code="invalid_configuration"))
        return 2
    return run_cli(stdout=sys.stdout, settings=settings)


if __name__ == "__main__":
    raise SystemExit(main())
