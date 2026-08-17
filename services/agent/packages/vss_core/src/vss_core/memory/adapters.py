# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Group adapters that map CLI/backend payloads into ``nv.vss.memory/1.0`` records.

Adapters are separate from storage: registering a future ``alert`` or ``vlm``
adapter must not require changes to the store, lifecycle engine, or common
schema models.

One operation produces a :class:`RecordBundle` — exactly one parent plus zero
or more terminal child result records. Complete collections are never nested
under ``output.ext.events|results|incidents``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC
from datetime import datetime
from hashlib import sha256
from typing import Any
from typing import Protocol

from vss_core._foundation.time import datetime_to_iso8601

from .models import SCHEMA_ID
from .models import JobStatus
from .models import MemoryError
from .models import MemoryGroup
from .models import MemoryInput
from .models import MemoryOutput
from .models import OutputHandles
from .models import RecordType
from .models import SensorInfo
from .models import TimestampPoint
from .models import TimeWindow
from .models import UnifiedMemoryRecord
from .store import coerce_utc_instant

_ADAPTER_REGISTRY: dict[MemoryGroup, type[MemoryAdapter]] = {}


def utc_now_iso() -> str:
    """Return the current UTC instant as an ISO-8601 ``Z`` string."""
    return datetime_to_iso8601(datetime.now(UTC))


@dataclass(frozen=True)
class RecordBundle:
    """One parent job record plus zero or more terminal child result records."""

    parent: UnifiedMemoryRecord
    children: tuple[UnifiedMemoryRecord, ...] = ()

    def __post_init__(self) -> None:
        if self.parent.job.is_child:
            raise ValueError("RecordBundle.parent must be a parent job record")
        for child in self.children:
            if not child.job.is_child:
                raise ValueError("RecordBundle.children must be child records")
            if child.job.job_id != self.parent.job.job_id:
                raise ValueError("child job_id must match parent job_id")

    @property
    def all_records(self) -> tuple[UnifiedMemoryRecord, ...]:
        return (self.parent, *self.children)


class MemoryAdapter(Protocol):
    """Per-group mapper between domain payloads and unified memory records."""

    group: MemoryGroup

    def submitted_record(
        self,
        *,
        job_id: str,
        created_at: str,
        input_data: MemoryInput,
        backend_ref: str | None = None,
    ) -> UnifiedMemoryRecord: ...

    def running_record(
        self,
        *,
        job_id: str,
        created_at: str,
        input_data: MemoryInput,
        backend_ref: str | None = None,
        updated_at: str | None = None,
    ) -> UnifiedMemoryRecord: ...

    def terminal_record(
        self,
        *,
        job_id: str,
        created_at: str,
        status: JobStatus,
        input_data: MemoryInput,
        output: MemoryOutput | None = None,
        error: MemoryError | None = None,
        backend_ref: str | None = None,
        updated_at: str | None = None,
    ) -> UnifiedMemoryRecord: ...


def register_adapter(adapter_cls: type[MemoryAdapter]) -> type[MemoryAdapter]:
    """Register a group adapter. Used by tests to prove future groups plug in cleanly."""
    group = adapter_cls.group
    _ADAPTER_REGISTRY[group] = adapter_cls
    return adapter_cls


def get_adapter(group: MemoryGroup) -> MemoryAdapter:
    """Return a fresh adapter instance for ``group``."""
    if group not in _ADAPTER_REGISTRY:
        raise KeyError(f"no memory adapter registered for group {group!r}")
    return _ADAPTER_REGISTRY[group]()


def clear_adapter_registry() -> None:
    """Reset the adapter registry (test isolation)."""
    _ADAPTER_REGISTRY.clear()
    # Re-register built-ins after a clear.
    register_adapter(SummaryAdapter)
    register_adapter(SearchAdapter)


def _dump_optional_input(input_data: MemoryInput | None) -> dict[str, Any] | None:
    if input_data is None:
        return None
    payload = input_data.model_dump(mode="json", exclude_none=True)
    return payload or None


def _dump_optional_output(output: MemoryOutput | None) -> dict[str, Any] | None:
    if output is None:
        return None
    payload = output.model_dump(by_alias=True, mode="json", exclude_none=True)
    return payload or None


def _base_record(
    *,
    job_id: str,
    group: MemoryGroup,
    status: JobStatus,
    created_at: str,
    updated_at: str | None,
    input_data: MemoryInput | None,
    output: MemoryOutput | None = None,
    error: MemoryError | None = None,
    backend_ref: str | None = None,
    record_id: str | None = None,
    record_type: RecordType | None = None,
) -> UnifiedMemoryRecord:
    job: dict[str, Any] = {
        "job_id": job_id,
        "group": group,
        "operation": "run",
        "status": status,
        "created_at": created_at,
    }
    if updated_at is not None:
        job["updated_at"] = updated_at
    if backend_ref is not None:
        job["backend_ref"] = backend_ref
    if record_id is not None:
        job["record_id"] = record_id
    if record_type is not None:
        job["record_type"] = record_type
    payload: dict[str, Any] = {
        "schema": SCHEMA_ID,
        "job": job,
    }
    input_payload = _dump_optional_input(input_data)
    if input_payload is not None:
        payload["input"] = input_payload
    output_payload = _dump_optional_output(output)
    if output_payload is not None:
        payload["output"] = output_payload
    if error is not None:
        payload["error"] = error.model_dump(mode="json", exclude_none=True)
    return UnifiedMemoryRecord.model_validate(payload)


def child_record(
    *,
    job_id: str,
    group: MemoryGroup,
    record_id: str,
    record_type: RecordType,
    created_at: str,
    input_data: MemoryInput | None = None,
    output: MemoryOutput | None = None,
    status: JobStatus = "completed",
) -> UnifiedMemoryRecord:
    """Construct a terminal child result record."""
    if status in {"submitted", "running"}:
        raise ValueError("child records must be terminal")
    return _base_record(
        job_id=job_id,
        group=group,
        status=status,
        created_at=created_at,
        updated_at=None,
        input_data=input_data,
        output=output,
        record_id=record_id,
        record_type=record_type,
    )


def deterministic_record_id(*, prefix: str, payload: dict[str, Any]) -> str:
    """Stable digest-based child id when no upstream identifier exists."""
    canonical = _canonical_json(payload)
    digest = sha256(canonical.encode("utf-8")).hexdigest()[:16]
    return f"{prefix}-{digest}"


def resolve_child_record_id(
    row: dict[str, Any],
    *,
    preferred_keys: tuple[str, ...] = ("event_id", "id", "uuid", "_id"),
    prefix: str,
    digest_payload: dict[str, Any] | None = None,
) -> str:
    """Prefer a stable upstream id; otherwise derive a deterministic digest id."""
    for key in preferred_keys:
        value = row.get(key)
        if value is None and isinstance(row.get("metadata"), dict):
            value = row["metadata"].get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return deterministic_record_id(prefix=prefix, payload=digest_payload or row)


def _canonical_json(value: Any) -> str:
    import json

    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


class _BaseGroupAdapter:
    """Shared lifecycle helpers for concrete group adapters (parent only)."""

    group: MemoryGroup

    def submitted_record(
        self,
        *,
        job_id: str,
        created_at: str,
        input_data: MemoryInput,
        backend_ref: str | None = None,
    ) -> UnifiedMemoryRecord:
        return _base_record(
            job_id=job_id,
            group=self.group,
            status="submitted",
            created_at=created_at,
            updated_at=created_at,
            input_data=input_data,
            backend_ref=backend_ref,
        )

    def running_record(
        self,
        *,
        job_id: str,
        created_at: str,
        input_data: MemoryInput,
        backend_ref: str | None = None,
        updated_at: str | None = None,
    ) -> UnifiedMemoryRecord:
        stamp = updated_at or utc_now_iso()
        return _base_record(
            job_id=job_id,
            group=self.group,
            status="running",
            created_at=created_at,
            updated_at=stamp,
            input_data=input_data,
            backend_ref=backend_ref,
        )

    def terminal_record(
        self,
        *,
        job_id: str,
        created_at: str,
        status: JobStatus,
        input_data: MemoryInput,
        output: MemoryOutput | None = None,
        error: MemoryError | None = None,
        backend_ref: str | None = None,
        updated_at: str | None = None,
    ) -> UnifiedMemoryRecord:
        if status not in {"completed", "failed", "partial", "timeout"}:
            raise ValueError(f"status {status!r} is not terminal")
        stamp = updated_at or utc_now_iso()
        return _base_record(
            job_id=job_id,
            group=self.group,
            status=status,
            created_at=created_at,
            updated_at=stamp,
            input_data=input_data,
            output=output,
            error=error,
            backend_ref=backend_ref,
        )


@register_adapter
class SummaryAdapter(_BaseGroupAdapter):
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
        ext: dict[str, Any] | None = None,
        media_urls: list[str] | None = None,
        related_job_ids: list[str] | None = None,
        event_count: int | None = None,
    ) -> MemoryOutput:
        """Build parent summary output — no nested event collections."""
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
        """Build one parent plus one ``event`` child per event."""
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


@register_adapter
class SearchAdapter(_BaseGroupAdapter):
    """Map archive-search requests/results into unified memory records."""

    group: MemoryGroup = "search"

    @staticmethod
    def build_input(
        *,
        query: str | None,
        sensors: list[dict[str, Any]] | list[SensorInfo] | None,
        window: TimeWindow | dict[str, Any] | None,
        params: dict[str, Any] | None,
        intent: str | None = None,
    ) -> MemoryInput:
        sensor_models: list[SensorInfo] = []
        for item in sensors or []:
            if isinstance(item, SensorInfo):
                sensor_models.append(item)
            else:
                sensor_id = str(item.get("id") or item.get("sensor_id") or "").strip()
                if not sensor_id:
                    raise ValueError("search sensors require a non-empty id")
                sensor_models.append(
                    SensorInfo(
                        id=sensor_id,
                        type=str(item.get("type") or "video") or None,
                        info={k: v for k, v in item.items() if k not in {"id", "sensor_id", "type"}} or None,
                    )
                )
        window_model: TimeWindow | None = None
        if isinstance(window, TimeWindow):
            window_model = window
        elif isinstance(window, dict):
            has_start = bool(window.get("start"))
            has_end = bool(window.get("end"))
            if has_start ^ has_end:
                raise ValueError(
                    "input.window requires both start and end; a single bound is not "
                    "silently dropped (resolve the covering segment or reject upstream)"
                )
            if has_start and has_end:
                start = window["start"]
                end = window["end"]
                start_ts = start["timestamp"] if isinstance(start, dict) else str(start)
                end_ts = end["timestamp"] if isinstance(end, dict) else str(end)
                window_model = TimeWindow(
                    start=TimestampPoint(timestamp=start_ts),
                    end=TimestampPoint(timestamp=end_ts),
                )
        return MemoryInput(
            query=query,
            intent=intent,
            sensors=sensor_models or None,
            window=window_model,
            params=dict(params) if params else None,
        )

    @staticmethod
    def build_output(
        *,
        answer: str | None,
        ext: dict[str, Any] | None = None,
        result_count: int | None = None,
        media_urls: list[str] | None = None,
        related_job_ids: list[str] | None = None,
    ) -> MemoryOutput:
        """Build parent search output — no nested result collections."""
        payload_ext = dict(ext or {})
        if result_count is not None:
            payload_ext.setdefault("result_count", result_count)
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
        results: list[dict[str, Any]] | None = None,
        ext: dict[str, Any] | None = None,
        error: MemoryError | None = None,
        backend_ref: str | None = None,
        updated_at: str | None = None,
        default_sensor_id: str | None = None,
    ) -> RecordBundle:
        """Build one parent plus one ``search_hit`` child per result."""
        rows = [_normalize_search_row(dict(row)) for row in (results or [])]
        parent_ext = dict(ext or {})
        if input_data.params and "search_mode" in input_data.params:
            parent_ext.setdefault("search_mode", input_data.params["search_mode"])
        parent_output = None
        if status in {"completed", "partial"} or answer or parent_ext:
            parent_output = self.build_output(
                answer=answer,
                ext=parent_ext,
                result_count=len(rows),
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
        children = tuple(
            self._hit_child(
                job_id=job_id,
                created_at=created_at,
                row=row,
                rank=index,
                default_sensor_id=default_sensor_id or (input_data.sensors[0].id if input_data.sensors else None),
            )
            for index, row in enumerate(rows, start=1)
        )
        return RecordBundle(parent=parent, children=children)

    def _hit_child(
        self,
        *,
        job_id: str,
        created_at: str,
        row: dict[str, Any],
        rank: int,
        default_sensor_id: str | None,
    ) -> UnifiedMemoryRecord:
        record_id = resolve_child_record_id(
            row,
            preferred_keys=("hit_id", "result_id", "event_id", "id", "uuid", "_id"),
            prefix="hit",
            digest_payload=_search_digest_payload(row),
        )
        sensor_id = (
            str(row.get("sensor_id") or row.get("camera_id") or row.get("stream_id") or "").strip() or default_sensor_id
        )
        child_input = MemoryInput(
            sensors=[SensorInfo(id=sensor_id)] if sensor_id else None,
            window=_window_from_row(row),
        )
        answer = row.get("description") or row.get("caption") or row.get("answer") or row.get("text")
        media_urls = _collect_ids([row], ("media_url", "screenshot_url", "url", "clip_url"))
        object_ids = _collect_ids([row], ("object_ids", "object_id"))
        frame_ids = _collect_ids([row], ("frame_ids", "frame_id"))
        ext: dict[str, Any] = {"rank": rank}
        if "score" in row and row["score"] is not None:
            ext["score"] = row["score"]
        elif "similarity" in row and row["similarity"] is not None:
            ext["score"] = row["similarity"]
        if object_ids:
            ext["object_ids"] = object_ids
        if frame_ids:
            ext["frame_ids"] = frame_ids
        child_output = MemoryOutput(
            answer=str(answer) if answer is not None else None,
            handles=OutputHandles(media_urls=media_urls or None) if media_urls else None,
            ext=ext,
        )
        return child_record(
            job_id=job_id,
            group=self.group,
            record_id=record_id,
            record_type="search_hit",
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
    """Future-facing alert adapter helper: parent + ``incident`` children."""
    parent_ext = dict(ext or {})
    parent_ext.setdefault("incident_count", len(incidents))
    parent = _base_record(
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
    """Require a parseable absolute instant on event/incident rows."""
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


def _normalize_search_row(row: dict[str, Any]) -> dict[str, Any]:
    stamp = _event_stamp(row)
    if stamp is None:
        # Search hits without timestamps are still persistable; window stays omitted.
        return dict(row)
    coerce_utc_instant(stamp)
    if "timestamp" not in row:
        row = dict(row)
        row["timestamp"] = stamp
    return row


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
            # Avoid treating nested start/end objects already handled above.
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


def _search_digest_payload(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "timestamp": _event_stamp(row),
        "end_time": row.get("end_time") or row.get("end"),
        "description": row.get("description") or row.get("caption") or row.get("answer"),
        "sensor_id": row.get("sensor_id") or row.get("camera_id") or row.get("stream_id"),
        "score": row.get("score") or row.get("similarity"),
        "object_ids": row.get("object_ids") or row.get("object_id"),
        "frame_ids": row.get("frame_ids") or row.get("frame_id"),
        "media_url": row.get("media_url") or row.get("screenshot_url") or row.get("url"),
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


__all__ = [
    "MemoryAdapter",
    "RecordBundle",
    "SearchAdapter",
    "SummaryAdapter",
    "alert_incident_bundle",
    "child_record",
    "clear_adapter_registry",
    "deterministic_record_id",
    "get_adapter",
    "register_adapter",
    "resolve_child_record_id",
    "utc_now_iso",
]
