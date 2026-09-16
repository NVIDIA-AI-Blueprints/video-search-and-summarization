# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""VLM-group mapper from VLM completions into ``nv.vss.memory/1.0`` records.

Lives with the ``vss vlm`` command group: memory owns the contract (protocol,
helpers) and the command group owns the translation from its backend's shapes.

``vss vlm run`` is a **point call** — one bounded synchronous inference. The
lifecycle is simpler than ``vss summarize``: no submitted/running intermediate
states are needed. Exactly one record is written, with a terminal status.

``VLMAdapter`` is the runner/introspection shape: a fully resolved sensor window
with stream identity. The CLI point-call uses ``VlmAdapter``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from vss_core.memory.adapters import LifecycleAdapter
from vss_core.memory.models import MemoryGroup
from vss_core.memory.models import MemoryInput
from vss_core.memory.models import MemoryOutput
from vss_core.memory.models import OutputHandles
from vss_core.memory.models import SensorInfo
from vss_core.memory.models import TimestampPoint
from vss_core.memory.models import TimeWindow


def _time_window(start_time: str | None, end_time: str | None) -> TimeWindow | None:
    if not start_time:
        return None
    from datetime import UTC
    from datetime import datetime as datetime_cls

    def _parse(value: str) -> TimestampPoint:
        dt = datetime_cls.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)
        return TimestampPoint(timestamp=dt)

    return TimeWindow(
        start=_parse(start_time),
        end=_parse(end_time) if end_time else None,
    )


class VlmAdapter(LifecycleAdapter):
    """Map VLM requests/answers into unified memory records (group ``vlm``)."""

    group: MemoryGroup = "vlm"

    @staticmethod
    def build_input(
        *,
        prompt: str,
        sensor: str | None,
        start_time: str | None,
        end_time: str | None,
        media_url: str | None,
        intent: str | None,
        model_params: dict[str, Any] | None,
    ) -> MemoryInput:
        sensors: list[SensorInfo] | None = None
        if sensor:
            sensors = [SensorInfo(id=sensor, type="video")]
        window = _time_window(start_time, end_time)
        params: dict[str, Any] = {}
        if model_params:
            params.update(model_params)
        if media_url:
            params["media_url"] = media_url
        return MemoryInput(
            query=prompt,
            intent=intent,
            sensors=sensors,
            window=window,
            params=params or None,
        )

    @staticmethod
    def build_output(
        *,
        answer: str,
        model: str,
        media_url: str | None,
        intent: str | None,
        completion_id: str | None,
    ) -> MemoryOutput:
        ext: dict[str, Any] = {"model": model}
        if intent:
            ext["intent"] = intent
        if completion_id:
            ext["completion_id"] = completion_id
        handles: OutputHandles | None = OutputHandles(media_urls=[media_url]) if media_url else None
        return MemoryOutput(answer=answer, handles=handles, ext=ext)


class VLMAdapter(LifecycleAdapter):
    """Parent adapter for the shared ``run_vlm_job`` runner (group ``vlm``)."""

    group: MemoryGroup = "vlm"

    @staticmethod
    def build_input(
        *,
        prompt: str,
        intent: str,
        sensor_name: str,
        sensor_type: str,
        sensor_id: str,
        stream_id: str,
        start_time: str,
        end_time: str,
        params: dict[str, Any],
    ) -> MemoryInput:
        return MemoryInput(
            query=prompt,
            intent=intent,
            sensors=[
                SensorInfo(
                    id=sensor_name,
                    type=sensor_type,
                    info={"sensor_id": sensor_id, "stream_id": stream_id},
                )
            ],
            window=TimeWindow(
                start=TimestampPoint(timestamp=datetime.fromisoformat(start_time.replace("Z", "+00:00"))),
                end=TimestampPoint(timestamp=datetime.fromisoformat(end_time.replace("Z", "+00:00"))),
            ),
            params=params,
        )

    @staticmethod
    def build_output(
        *,
        answer: str,
        model: str,
        media_urls: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> MemoryOutput:
        ext = {"model": model, **(metadata or {})}
        return MemoryOutput(
            answer=answer,
            handles=OutputHandles(media_urls=media_urls) if media_urls else None,
            ext=ext,
        )


__all__ = ["VLMAdapter", "VlmAdapter"]
