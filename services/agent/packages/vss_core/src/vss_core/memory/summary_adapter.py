# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Transitional summary-group mapper for ``nv.vss.memory/1.0``.

Still exported from ``vss_core.memory`` so develop's ``vss summarize`` keeps
importing. The follow-up command-group PR moves this module next to the
summarize CLI and deletes the re-export.

One summarize run that uses :meth:`SummaryAdapter.terminal_bundle` persists
one parent plus one ``event`` child per LVS event. Nested ``output.ext.events``
collections are rejected by the schema.
"""

from __future__ import annotations

from typing import Any

from vss_core.memory.adapters import LifecycleAdapter
from vss_core.memory.adapters import RecordBundle
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
from vss_core.memory.models import SensorInfo
from vss_core.memory.models import TimeWindow
from vss_core.memory.models import UnifiedMemoryRecord

#: Media-ish handle keys an LVS event may carry.
_MEDIA_KEYS: tuple[str, ...] = ("media_url", "screenshot_url", "url", "clip_url")

#: Event fields promoted onto a child's ``output.ext``.
_EVENT_EXT_KEYS: tuple[str, ...] = ("event_type", "type", "label", "confidence", "start_pts", "end_pts")


class SummaryAdapter(LifecycleAdapter):
    """Map summarization requests/results into unified memory records."""

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
        events: list[dict[str, Any]] | None = None,
        ext: dict[str, Any] | None = None,
        event_ids: list[str] | None = None,
        media_urls: list[str] | None = None,
        related_job_ids: list[str] | None = None,
        event_count: int | None = None,
    ) -> MemoryOutput:
        """Build summary output.

        Transitional: when ``events`` is passed (develop summarize CLI), nest
        them under ``output.ext.events`` so existing callers keep working.
        Prefer :meth:`terminal_bundle` for parent/child persistence; the
        follow-up command-group PR switches summarize to that path and stops
        nesting.
        """
        payload_ext = dict(ext or {})
        rows = [dict(event) for event in (events or [])]
        if rows:
            # Promote start_time → timestamp when missing (windowed recall).
            normalized: list[dict[str, Any]] = []
            for row in rows:
                stamp = row_instant(row)
                if stamp is None:
                    raise ValueError(
                        "event rows require an absolute timestamp "
                        "(timestamp|start_time|start|ts) for time-windowed recall"
                    )
                event = dict(row)
                if "timestamp" not in event:
                    # Prefer the caller's absolute string over a re-serialized instant
                    # so wire form stays identical to start_time when that was given.
                    raw = event.get("start_time") or event.get("start") or event.get("ts")
                    event["timestamp"] = str(raw) if raw is not None else stamp.isoformat().replace("+00:00", "Z")
                normalized.append(event)
            payload_ext.setdefault("events", normalized)
            payload_ext.setdefault("event_count", event_count if event_count is not None else len(normalized))
            if event_ids is None:
                ids = []
                for event in normalized:
                    for key in ("event_id", "id", "uuid"):
                        if event.get(key) is not None:
                            ids.append(str(event[key]))
                            break
                if ids:
                    payload_ext.setdefault("event_ids", ids)
            else:
                payload_ext.setdefault("event_ids", list(event_ids))
        elif event_count is not None:
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
        """Build one parent plus one ``event`` child per event."""
        rows = [_require_timed_event(dict(event)) for event in (events or [])]
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
            self._event_child(
                job_id=job_id,
                created_at=created_at,
                event=row,
                default_sensor_id=fallback_sensor,
            )
            for row in rows
        )
        return RecordBundle(parent=parent, children=children)

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
            window=window_from_row(event),
        )
        answer = event.get("description") or event.get("summary") or event.get("answer") or event.get("text")
        media_urls = collect_values([event], _MEDIA_KEYS)
        ext: dict[str, Any] = {}
        for key in _EVENT_EXT_KEYS:
            if event.get(key) is not None:
                ext["event_type" if key == "type" else key] = event[key]
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


def _require_timed_event(event: dict[str, Any]) -> dict[str, Any]:
    """Event rows must carry an absolute instant, or windowed recall cannot find them."""
    stamp = row_instant(event)
    if stamp is None:
        raise ValueError(
            "event rows require an absolute timestamp (timestamp|start_time|start|ts) for time-windowed recall"
        )
    return event


def _event_digest_payload(event: dict[str, Any]) -> dict[str, Any]:
    """Identity-bearing fields for a deterministic event id (no upstream id)."""
    stamp = row_instant(event)
    return {
        "timestamp": stamp.isoformat() if stamp is not None else None,
        "end_time": event.get("end_time") or event.get("end"),
        "description": event.get("description") or event.get("summary") or event.get("answer"),
        "sensor_id": event.get("sensor_id") or event.get("camera_id") or event.get("video_id"),
        "event_type": event.get("event_type") or event.get("type"),
    }


__all__ = ["SummaryAdapter"]
