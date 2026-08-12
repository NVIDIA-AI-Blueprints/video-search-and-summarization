# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Group adapters that map CLI/backend payloads into ``nv.vss.memory/1.0`` records.

Adapters are separate from storage: registering a future ``alert`` or ``vlm``
adapter must not require changes to the store, lifecycle engine, or common
schema models. VIOS does not write memory (no ``media`` group).
"""

from __future__ import annotations

from datetime import UTC
from datetime import datetime
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
from .models import SensorInfo
from .models import TimestampPoint
from .models import TimeWindow
from .models import UnifiedMemoryRecord
from .store import coerce_utc_instant

_ADAPTER_REGISTRY: dict[MemoryGroup, type[MemoryAdapter]] = {}


def utc_now_iso() -> str:
    """Return the current UTC instant as an ISO-8601 ``Z`` string."""
    return datetime_to_iso8601(datetime.now(UTC))


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


def _base_record(
    *,
    job_id: str,
    group: MemoryGroup,
    status: JobStatus,
    created_at: str,
    updated_at: str,
    input_data: MemoryInput,
    output: MemoryOutput | None = None,
    error: MemoryError | None = None,
    backend_ref: str | None = None,
) -> UnifiedMemoryRecord:
    return UnifiedMemoryRecord.model_validate(
        {
            "schema": SCHEMA_ID,
            "job": {
                "job_id": job_id,
                "group": group,
                "operation": "run",
                "status": status,
                "created_at": created_at,
                "updated_at": updated_at,
                "backend_ref": backend_ref,
            },
            "input": input_data.model_dump(mode="json"),
            "output": (output or MemoryOutput()).model_dump(by_alias=True, mode="json"),
            "error": error.model_dump(mode="json") if error else None,
        }
    )


class _BaseGroupAdapter:
    """Shared lifecycle helpers for concrete group adapters."""

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
        sensors: list[SensorInfo] = []
        if video_id:
            info = dict(media_ref or {})
            sensors.append(SensorInfo(id=str(video_id), type=str(info.get("source") or "video"), info=info))
        return MemoryInput(
            query=prompt,
            intent=intent,
            sensors=sensors,
            window=window,
            params=dict(params or {}),
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
    ) -> MemoryOutput:
        normalized_events = [_require_row_timestamp(dict(event), kind="summary event") for event in (events or [])]
        resolved_event_ids = list(event_ids or _event_ids_from(normalized_events))
        payload_ext = dict(ext or {})
        if "incidents" in payload_ext:
            incidents = payload_ext["incidents"]
            if not isinstance(incidents, list):
                raise ValueError("output.ext.incidents must be a list of timestamped incident dicts")
            payload_ext["incidents"] = [
                _require_row_timestamp(dict(item), kind="incident") for item in incidents
            ]
        if normalized_events:
            payload_ext.setdefault("events", normalized_events)
        if resolved_event_ids:
            payload_ext.setdefault("event_ids", resolved_event_ids)
        handles = OutputHandles(
            media_urls=list(media_urls or []),
            related_job_ids=list(related_job_ids or []),
        )
        return MemoryOutput(answer=answer, Embedding=[], handles=handles, ext=payload_ext)


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
                sensor_models.append(
                    SensorInfo(
                        id=str(item.get("id") or item.get("sensor_id") or ""),
                        type=str(item.get("type") or "video"),
                        info={k: v for k, v in item.items() if k not in {"id", "sensor_id", "type"}},
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
            sensors=sensor_models,
            window=window_model,
            params=dict(params or {}),
        )

    @staticmethod
    def build_output(
        *,
        answer: str | None,
        results: list[dict[str, Any]] | None = None,
        ext: dict[str, Any] | None = None,
        object_ids: list[str] | None = None,
        frame_ids: list[str] | None = None,
        media_urls: list[str] | None = None,
        event_ids: list[str] | None = None,
        related_job_ids: list[str] | None = None,
    ) -> MemoryOutput:
        rows = [_require_row_timestamp(dict(row), kind="search result") for row in (results or [])]
        resolved_event_ids = list(event_ids or [])
        resolved_object_ids = list(object_ids or _collect_ids(rows, ("object_ids", "object_id")))
        resolved_frame_ids = list(frame_ids or _collect_ids(rows, ("frame_ids", "frame_id")))
        handles = OutputHandles(
            media_urls=list(media_urls or _collect_ids(rows, ("screenshot_url", "media_url", "url"))),
            related_job_ids=list(related_job_ids or []),
        )
        payload_ext = dict(ext or {})
        if rows:
            payload_ext.setdefault("results", rows)
        payload_ext.setdefault("result_count", len(rows))
        if resolved_event_ids:
            payload_ext.setdefault("event_ids", resolved_event_ids)
        if resolved_object_ids:
            payload_ext.setdefault("object_ids", resolved_object_ids)
        if resolved_frame_ids:
            payload_ext.setdefault("frame_ids", resolved_frame_ids)
        return MemoryOutput(answer=answer, Embedding=[], handles=handles, ext=payload_ext)


def _event_stamp(event: dict[str, Any]) -> str | None:
    for key in ("timestamp", "start_time", "start", "ts"):
        value = event.get(key)
        if value is not None and str(value).strip():
            return str(value)
    return None


def _require_row_timestamp(event: dict[str, Any], *, kind: str) -> dict[str, Any]:
    """Normalize/require a parseable instant on events, incidents, and search results."""
    stamp = _event_stamp(event)
    if stamp is None:
        raise ValueError(
            f"{kind} rows require a timestamp field "
            "(timestamp|start_time|start|ts) for time-windowed recall"
        )
    coerce_utc_instant(stamp)
    if "timestamp" not in event:
        event = dict(event)
        event["timestamp"] = stamp
    return event


def _event_ids_from(events: list[dict[str, Any]]) -> list[str]:
    ids: list[str] = []
    for event in events:
        for key in ("event_id", "id", "uuid"):
            value = event.get(key)
            if value is not None:
                ids.append(str(value))
                break
    return ids


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
    # Preserve order, drop duplicates.
    seen: set[str] = set()
    ordered: list[str] = []
    for item in found:
        if item not in seen:
            seen.add(item)
            ordered.append(item)
    return ordered


__all__ = [
    "MemoryAdapter",
    "SearchAdapter",
    "SummaryAdapter",
    "clear_adapter_registry",
    "get_adapter",
    "register_adapter",
    "utc_now_iso",
]
