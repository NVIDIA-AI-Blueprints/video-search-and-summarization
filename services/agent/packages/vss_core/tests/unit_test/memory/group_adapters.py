# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Example group adapters that live outside ``vss_core.memory``.

Production summarize/search mappers move to their command groups in a
follow-up PR. These fixtures prove memory only needs the Protocol + helpers,
and that external adapters can build parent/child bundles without store
changes. Nothing below is imported by production code.
"""

from __future__ import annotations

from typing import Any

from vss_core.memory.adapters import LifecycleAdapter
from vss_core.memory.adapters import RecordBundle
from vss_core.memory.adapters import build_record
from vss_core.memory.adapters import child_record
from vss_core.memory.adapters import collect_values
from vss_core.memory.adapters import resolve_child_record_id
from vss_core.memory.adapters import row_instant
from vss_core.memory.adapters import window_from_row
from vss_core.memory.models import JobStatus
from vss_core.memory.models import MemoryError
from vss_core.memory.models import MemoryGroup
from vss_core.memory.models import MemoryInput
from vss_core.memory.models import MemoryOutput
from vss_core.memory.models import OutputHandles
from vss_core.memory.models import RecordType
from vss_core.memory.models import SensorInfo
from vss_core.memory.models import TimeWindow
from vss_core.memory.models import UnifiedMemoryRecord


def _timed(row: dict[str, Any]) -> dict[str, Any]:
    """Event/incident rows need an absolute instant for windowed recall."""
    if row_instant(row) is None:
        raise ValueError("event/incident rows require a timestamp (timestamp|start_time|start|ts)")
    return row


def _child_for(
    *,
    job_id: str,
    group: MemoryGroup,
    record_type: RecordType,
    created_at: str,
    row: dict[str, Any],
    prefix: str,
    preferred_keys: tuple[str, ...],
    default_sensor_id: str | None,
    ext_keys: tuple[str, ...],
) -> UnifiedMemoryRecord:
    """One terminal child record built from a single result row."""
    record_id = resolve_child_record_id(row, preferred_keys=preferred_keys, prefix=prefix)
    sensor_id = str(row.get("sensor_id") or row.get("camera_id") or "").strip() or default_sensor_id
    answer = row.get("description") or row.get("summary") or row.get("answer")
    media_urls = collect_values([row], ("media_url", "screenshot_url", "url"))
    return child_record(
        job_id=job_id,
        group=group,
        record_id=record_id,
        record_type=record_type,
        created_at=created_at,
        input_data=MemoryInput(
            sensors=[SensorInfo(id=sensor_id)] if sensor_id else None,
            window=window_from_row(row),
        ),
        output=MemoryOutput(
            answer=str(answer) if answer is not None else None,
            handles=OutputHandles(media_urls=media_urls or None) if media_urls else None,
            ext={key: row[key] for key in ext_keys if row.get(key) is not None} or None,
        ),
    )


class SummaryAdapter(LifecycleAdapter):
    """Example summary mapper: parent job plus one ``event`` child per event."""

    group: MemoryGroup = "summary"

    @staticmethod
    def build_input(
        *,
        prompt: str | None,
        video_id: str | None,
        media_ref: dict[str, Any] | None,
        params: dict[str, Any] | None,
        window: TimeWindow | None = None,
        intent: str | None = None,
    ) -> MemoryInput:
        sensors: list[SensorInfo] | None = None
        if video_id:
            info = dict(media_ref or {})
            sensors = [SensorInfo(id=str(video_id), type=str(info.get("source") or "video"), info=info or None)]
        return MemoryInput(
            query=prompt,
            intent=intent,
            sensors=sensors,
            window=window,
            params=dict(params) if params else None,
        )

    @staticmethod
    def build_output(
        *,
        answer: str | None,
        ext: dict[str, Any] | None = None,
        media_urls: list[str] | None = None,
        related_job_ids: list[str] | None = None,
        event_count: int | None = None,
    ) -> MemoryOutput:
        payload_ext = dict(ext or {})
        if event_count is not None:
            payload_ext.setdefault("event_count", event_count)
        handles = None
        if media_urls or related_job_ids:
            handles = OutputHandles(
                media_urls=list(media_urls) if media_urls else None,
                related_job_ids=list(related_job_ids) if related_job_ids else None,
            )
        return MemoryOutput(answer=answer, handles=handles, ext=payload_ext or None)

    def terminal_bundle(
        self,
        *,
        job_id: str,
        created_at: str,
        status: JobStatus,
        input_data: MemoryInput,
        answer: str | None,
        events: list[dict[str, Any]] | None = None,
        ext: dict[str, Any] | None = None,
        media_urls: list[str] | None = None,
        related_job_ids: list[str] | None = None,
        error: MemoryError | None = None,
        backend_ref: str | None = None,
        updated_at: str | None = None,
        default_sensor_id: str | None = None,
    ) -> RecordBundle:
        rows = [_timed(dict(event)) for event in (events or [])]
        parent_output = None
        if status in {"completed", "partial"} or answer or ext or media_urls:
            parent_output = self.build_output(
                answer=answer,
                ext=ext,
                media_urls=media_urls,
                related_job_ids=related_job_ids,
                event_count=len(rows),
            )
        parent = self.terminal_record(
            job_id=job_id,
            created_at=created_at,
            status=status,
            input_data=input_data,
            output=parent_output,
            error=error,
            backend_ref=backend_ref,
            updated_at=updated_at,
        )
        fallback_sensor = default_sensor_id or (input_data.sensors[0].id if input_data.sensors else None)
        children = tuple(
            _child_for(
                job_id=job_id,
                group=self.group,
                record_type="event",
                created_at=created_at,
                row=row,
                prefix="evt",
                preferred_keys=("event_id", "id", "uuid", "_id"),
                default_sensor_id=fallback_sensor,
                ext_keys=("event_type", "confidence"),
            )
            for row in rows
        )
        return RecordBundle(parent=parent, children=children)


def alert_incident_bundle(
    *,
    job_id: str,
    created_at: str,
    input_data: MemoryInput,
    answer: str | None,
    incidents: list[dict[str, Any]],
    status: JobStatus = "completed",
    ext: dict[str, Any] | None = None,
    updated_at: str | None = None,
    default_sensor_id: str | None = None,
) -> RecordBundle:
    """Example alert mapper: parent plus one ``incident`` child per incident."""
    rows = [_timed(dict(incident)) for incident in incidents]
    parent_ext = dict(ext or {})
    parent_ext.setdefault("incident_count", len(rows))
    parent = build_record(
        job_id=job_id,
        group="alert",
        status=status,
        created_at=created_at,
        updated_at=updated_at or created_at,
        input_data=input_data,
        output=MemoryOutput(answer=answer, ext=parent_ext),
    )
    fallback_sensor = default_sensor_id or (input_data.sensors[0].id if input_data.sensors else None)
    children = tuple(
        _child_for(
            job_id=job_id,
            group="alert",
            record_type="incident",
            created_at=created_at,
            row=row,
            prefix="inc",
            preferred_keys=("incident_id", "event_id", "id", "uuid", "_id"),
            default_sensor_id=fallback_sensor,
            ext_keys=("severity", "rule_id"),
        )
        for row in rows
    )
    return RecordBundle(parent=parent, children=children)
