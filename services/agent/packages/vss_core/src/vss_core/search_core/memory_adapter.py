# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Search-group mapper from archive-search payloads into ``nv.vss.memory/1.0``.

Lives with search_core (not ``vss_core.memory``) so memory stays a contract/
protocol package and command groups own domain translation.
"""

from __future__ import annotations

from typing import Any

from vss_core.memory.adapters import LifecycleAdapter
from vss_core.memory.adapters import RecordBundle
from vss_core.memory.adapters import child_record
from vss_core.memory.adapters import collect_values
from vss_core.memory.adapters import resolve_child_record_id
from vss_core.memory.adapters import row_instant
from vss_core.memory.adapters import utc_instant
from vss_core.memory.adapters import window_from_row
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

#: Media-ish handle keys carried by a search hit.
_MEDIA_KEYS: tuple[str, ...] = ("media_url", "screenshot_url", "url", "clip_url")


class SearchAdapter(LifecycleAdapter):
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
                start_ts = start["timestamp"] if isinstance(start, dict) else start
                end_ts = end["timestamp"] if isinstance(end, dict) else end
                window_model = TimeWindow(
                    start=TimestampPoint(timestamp=utc_instant(start_ts)),
                    end=TimestampPoint(timestamp=utc_instant(end_ts)),
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
        rows = [dict(row) for row in (results or [])]
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
            window=window_from_row(row),
        )
        answer = row.get("description") or row.get("caption") or row.get("answer") or row.get("text")
        media_urls = collect_values([row], _MEDIA_KEYS)
        object_ids = collect_values([row], ("object_ids", "object_id"))
        frame_ids = collect_values([row], ("frame_ids", "frame_id"))
        ext: dict[str, Any] = {"rank": rank}
        if row.get("score") is not None:
            ext["score"] = row["score"]
        elif row.get("similarity") is not None:
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


def _search_digest_payload(row: dict[str, Any]) -> dict[str, Any]:
    """Identity-bearing fields for a deterministic hit id (no upstream id)."""
    stamp = row_instant(row)
    return {
        "timestamp": stamp.isoformat() if stamp is not None else None,
        "end_time": row.get("end_time") or row.get("end"),
        "description": row.get("description") or row.get("caption") or row.get("answer"),
        "sensor_id": row.get("sensor_id") or row.get("camera_id") or row.get("stream_id"),
        "score": row.get("score") or row.get("similarity"),
        "object_ids": row.get("object_ids") or row.get("object_id"),
        "frame_ids": row.get("frame_ids") or row.get("frame_id"),
        "media_url": row.get("media_url") or row.get("screenshot_url") or row.get("url"),
    }


__all__ = ["SearchAdapter"]
