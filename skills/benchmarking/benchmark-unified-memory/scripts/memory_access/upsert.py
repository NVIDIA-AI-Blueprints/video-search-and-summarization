# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Temporary VSS-core bridge for authoritative unified-memory persistence."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from vss_core.memory import UnifiedMemoryRecord, build_memory_service


@dataclass(frozen=True, slots=True)
class PersistedMemory:
    job_id: str
    record_id: str
    record: UnifiedMemoryRecord


def load_unified_memory_record(path: Path) -> UnifiedMemoryRecord:
    return UnifiedMemoryRecord.model_validate_json(path.read_text(encoding="utf-8"))


def projection_job_id(record: UnifiedMemoryRecord) -> str:
    if record.output is None or record.output.ext is None:
        raise ValueError(f"{record.job.job_id}: output.ext is required")
    value = record.output.ext.get("job_id")
    return str(value) if value else record.job.job_id


def video_entries(record: UnifiedMemoryRecord) -> tuple[dict[str, str], ...]:
    if record.output is None or record.output.ext is None:
        raise ValueError(f"{record.job.job_id}: output.ext is required")
    raw = record.output.ext.get("videos")
    if not isinstance(raw, list) or not raw:
        raise ValueError(f"{record.job.job_id}: output.ext.videos must be a non-empty list")
    entries: list[dict[str, str]] = []
    for item in raw:
        if (
            not isinstance(item, dict)
            or not item.get("video_id")
            or not item.get("vios_sensor")
        ):
            raise ValueError(f"{record.job.job_id}: invalid video mapping")
        entries.append(
            {"video_id": str(item["video_id"]), "vios_sensor": str(item["vios_sensor"])}
        )
    video_ids = [entry["video_id"] for entry in entries]
    if len(video_ids) != len(set(video_ids)):
        raise ValueError(f"{record.job.job_id}: duplicate video_id mapping")
    return tuple(entries)


def validate_unique_job_ids(records: Sequence[UnifiedMemoryRecord]) -> None:
    job_ids = [projection_job_id(record) for record in records]
    record_ids = [record.job.job_id for record in records]
    if len(job_ids) != len(set(job_ids)):
        raise ValueError("frozen memories contain duplicate projection job IDs")
    if any(Path(job_id).name != job_id or job_id in {".", ".."} for job_id in job_ids):
        raise ValueError("projection job IDs must be safe Markdown filenames")
    if len(record_ids) != len(set(record_ids)):
        raise ValueError("frozen memories contain duplicate authoritative record IDs")


def persist_records(service: Any, records: Sequence[UnifiedMemoryRecord]) -> dict[str, PersistedMemory]:
    """Persist records idempotently and correlate results by projection job_id."""
    persisted: dict[str, PersistedMemory] = {}
    for record in records:
        value = service.upsert(record)
        job_id = projection_job_id(value)
        persisted[job_id] = PersistedMemory(job_id, value.job.job_id, value)
    return persisted


def verify_records(
    service: Any,
    persisted_by_job: Mapping[str, PersistedMemory],
) -> dict[str, PersistedMemory]:
    """Read every record back by its actual ID; never rely on response order."""
    verified: dict[str, PersistedMemory] = {}
    for job_id, persisted in persisted_by_job.items():
        record = service.get(persisted.record_id)
        if projection_job_id(record) != job_id:
            raise ValueError(f"readback correlation failed for job {job_id}")
        verified[job_id] = PersistedMemory(job_id, record.job.job_id, record)
    return verified


def memory_service(es_endpoint: str, memory_index: str) -> Any:
    return build_memory_service(es_endpoint=es_endpoint, memory_index=memory_index)
