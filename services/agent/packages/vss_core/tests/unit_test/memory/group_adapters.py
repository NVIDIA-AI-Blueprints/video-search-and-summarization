# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Test-local summary/alert adapters that live outside ``vss_core.memory``.

Production summarize/alerts command groups will own the real mappers. These
fixtures prove memory only needs the Protocol + helpers, and that external
adapters can build parent/child bundles without store/service changes.
"""

from __future__ import annotations

from typing import Any

from vss_core.memory.adapters import LifecycleAdapter
from vss_core.memory.adapters import RecordBundle
from vss_core.memory.adapters import build_record
from vss_core.memory.adapters import child_record
from vss_core.memory.adapters import resolve_child_record_id
from vss_core.memory.models import JobStatus
from vss_core.memory.models import MemoryError
from vss_core.memory.models import MemoryGroup
from vss_core.memory.models import MemoryInput
from vss_core.memory.models import MemoryOutput
from vss_core.memory.models import OutputHandles
from vss_core.memory.models import SensorInfo
from vss_core.memory.models import TimestampPoint
from vss_core.memory.models import TimeWindow
from vss_core.memory.models import UnifiedMemoryRecord
from vss_core.memory.store import coerce_utc_instant


class SummaryAdapter(LifecycleAdapter):
    """Example summarization mapper used only by memory unit tests."""

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
        return MemoryOutput(
            answer=answer,
            handles=handles,
            ext=payload_ext or None,
        )

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
        normalized = [_normalize_event_row(dict(event)) for event in (events or [])]
        parent_output = None
        if status in {"completed", "partial"} or answer or ext or media_urls:
            parent_output = self.build_output(
                answer=answer,
                ext=ext,
                media_urls=media_urls,
                related_job_ids=related_job_ids,
                event_count=len(normalized),
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
        children: list[UnifiedMemoryRecord] = []
        for event in normalized:
            children.append(
                self._event_child(
                    job_id=job_id,
                    created_at=created_at,
                    event=event,
                    default_sensor_id=default_sensor_id or (input_data.sensors[0].id if input_data.sensors else None),
                )
            )
        return RecordBundle(parent=parent, children=tuple(children))

    def _event_child(
        self,
        *,
        job_id: str,
        created_at: str,
        event: dict[str, Any],
        default_sensor_id: str | None,
    ) -> UnifiedMemoryRecord:
        record_id = resolve_child_record_id(
            event,
            preferred_keys=("event_id", "id", "uuid", "_id"),
            prefix="evt",
            digest_payload=_event_digest_payload(event),
        )
        sensor_id = (
            str(event.get("sensor_id") or event.get("camera_id") or event.get("video_id") or "").strip()
            or default_sensor_id
        )
        child_input = MemoryInput(
            sensors=[SensorInfo(id=sensor_id)] if sensor_id else None,
            window=_window_from_row(event),
        )
        answer = event.get("description") or event.get("summary") or event.get("answer") or event.get("text")
        media_urls = _collect_ids([event], ("media_url", "screenshot_url", "url", "clip_url"))
        ext: dict[str, Any] = {}
        for key in ("event_type", "type", "label", "confidence", "start_pts", "end_pts"):
            if key in event and event[key] is not None:
                ext[key if key != "type" else "event_type"] = event[key]
        child_output = MemoryOutput(
            answer=str(answer) if answer is not None else None,
            handles=OutputHandles(media_urls=media_urls or None) if media_urls else None,
            ext=ext or None,
        )
        return child_record(
            job_id=job_id,
            group=self.group,
            record_id=record_id,
            record_type="event",
            created_at=created_at,
            input_data=child_input,
            output=child_output,
        )


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
    """Example alert helper: parent + ``incident`` children (tests only)."""
    parent_ext = dict(ext or {})
    parent_ext.setdefault("incident_count", len(incidents))
    parent = build_record(
        job_id=job_id,
        group="alert",
        status=status,
        created_at=created_at,
        updated_at=updated_at or created_at,
        input_data=input_data,
        output=MemoryOutput(answer=answer, ext=parent_ext),
    )
    children: list[UnifiedMemoryRecord] = []
    for incident in incidents:
        row = _normalize_event_row(dict(incident))
        record_id = resolve_child_record_id(
            row,
            preferred_keys=("incident_id", "event_id", "id", "uuid", "_id"),
            prefix="inc",
            digest_payload=_event_digest_payload(row),
        )
        sensor_id = (
            str(row.get("sensor_id") or row.get("camera_id") or "").strip()
            or default_sensor_id
            or (input_data.sensors[0].id if input_data.sensors else None)
        )
        children.append(
            child_record(
                job_id=job_id,
                group="alert",
                record_id=record_id,
                record_type="incident",
                created_at=created_at,
                input_data=MemoryInput(
                    sensors=[SensorInfo(id=sensor_id)] if sensor_id else None,
                    window=_window_from_row(row),
                ),
                output=MemoryOutput(
                    answer=str(row.get("description") or row.get("summary") or row.get("answer") or "") or None,
                    ext={k: row[k] for k in ("severity", "rule_id", "type") if k in row} or None,
                ),
            )
        )
    return RecordBundle(parent=parent, children=tuple(children))


def _event_stamp(event: dict[str, Any]) -> str | None:
    for key in ("timestamp", "start_time", "start", "ts"):
        value = event.get(key)
        if value is not None and str(value).strip():
            return str(value)
    start = event.get("start")
    if isinstance(start, dict) and start.get("timestamp"):
        return str(start["timestamp"])
    return None


def _normalize_event_row(event: dict[str, Any]) -> dict[str, Any]:
    stamp = _event_stamp(event)
    if stamp is None:
        raise ValueError(
            "event/incident rows require an absolute timestamp (timestamp|start_time|start|ts) for time-windowed recall"
        )
    coerce_utc_instant(stamp)
    if "timestamp" not in event:
        event = dict(event)
        event["timestamp"] = stamp
    return event


def _window_from_row(row: dict[str, Any]) -> TimeWindow | None:
    start_stamp = _event_stamp(row)
    if start_stamp is None:
        return None
    end_stamp = None
    for key in ("end_time", "end", "end_ts"):
        value = row.get(key)
        if isinstance(value, dict) and value.get("timestamp"):
            end_stamp = str(value["timestamp"])
            break
        if value is not None and str(value).strip() and key != "end":
            end_stamp = str(value)
            break
        if key == "end" and value is not None and not isinstance(value, dict) and str(value).strip():
            try:
                coerce_utc_instant(str(value))
                end_stamp = str(value)
            except ValueError:
                pass
    start_point = TimestampPoint(timestamp=start_stamp)
    end_point = TimestampPoint(timestamp=end_stamp) if end_stamp else None
    return TimeWindow(start=start_point, end=end_point)


def _event_digest_payload(event: dict[str, Any]) -> dict[str, Any]:
    return {
        "timestamp": _event_stamp(event),
        "end_time": event.get("end_time") or event.get("end"),
        "description": event.get("description") or event.get("summary") or event.get("answer"),
        "sensor_id": event.get("sensor_id") or event.get("camera_id") or event.get("video_id"),
        "event_type": event.get("event_type") or event.get("type"),
    }


def _collect_ids(rows: list[dict[str, Any]], keys: tuple[str, ...]) -> list[str]:
    found: list[str] = []
    for row in rows:
        for key in keys:
            value = row.get(key)
            if value is None and isinstance(row.get("metadata"), dict):
                value = row["metadata"].get(key)
            if value is None:
                continue
            if isinstance(value, list):
                found.extend(str(item) for item in value)
            else:
                found.append(str(value))
    seen: set[str] = set()
    ordered: list[str] = []
    for item in found:
        if item not in seen:
            seen.add(item)
            ordered.append(item)
    return ordered
