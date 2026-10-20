# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Opt-in second look using the existing CLI transport and core policy."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import asdict
from dataclasses import replace
from datetime import datetime
from typing import TYPE_CHECKING
from typing import Any
from typing import Literal
from typing import override

from vss_cli import memory as memory_mod
from vss_cli.exits import Exit
from vss_cli.group import InvalidInput
from vss_cli.group import Result
from vss_core import vios
from vss_core._foundation.errors import BackendUnreachableError
from vss_core.memory import MemoryNotFoundError
from vss_core.memory import MemoryOutput
from vss_core.vlm import VLMAnalyzer
from vss_core.vlm import second_look

if TYPE_CHECKING:
    from vss_cli.config import VlmConfig
    from vss_cli.group import Context

    from .group import VlmGroup
    from .group import VlmInput


class _CliAnalyzer(VLMAnalyzer):
    def __init__(self, group: VlmGroup, inputs: VlmInput, ctx: Context) -> None:
        self.group = group
        self.inputs = inputs
        self.ctx = ctx
        self.reads: list[Result] = []

    @override
    async def analyze(
        self,
        *,
        sensor_id: str,
        start_timestamp: str,
        end_timestamp: str,
        prompt: str,
        time_format: Literal["iso", "offset"] = "iso",
    ) -> str:
        inputs = self.inputs.model_copy(
            update={
                "sensor": sensor_id,
                "start_time": start_timestamp,
                "end_time": end_timestamp,
                "prompt": prompt,
                "relook_start_time": None,
                "relook_end_time": None,
                "relook_fps": None,
                "relook_max_frames": None,
            }
        )
        # The synchronous CLI resolves VIOS media with its own event loop.
        result = await asyncio.to_thread(self.group.run, "run", inputs, self.ctx)
        self.reads.append(result)
        if result.exit not in (Exit.SUCCESS, Exit.PARTIAL) or not result.body.get("answer", "").strip():
            raise BackendUnreachableError("vlm", f"Read failed with exit {int(result.exit)}")
        return str(result.body["answer"])


def _reread_inputs(inputs: VlmInput, policy: VlmConfig | None) -> VlmInput:
    from .group import _ONE_OF_FPS_OR_FRAMES_BACKENDS
    from .group import _apply_vlm_policy

    if inputs.relook_fps is None and inputs.relook_max_frames is None:
        return inputs
    backend = policy.backend if policy is not None else "rt_vlm"
    if backend == "openai":
        raise InvalidInput("--relook-fps / --relook-max-frames are not supported by the openai backend")
    updates: dict[str, float | int | None] = {}
    if inputs.relook_fps is not None:
        updates["fps"] = inputs.relook_fps
    if inputs.relook_max_frames is not None:
        updates["max_frames"] = inputs.relook_max_frames
    if backend in _ONE_OF_FPS_OR_FRAMES_BACKENDS:
        if len(updates) == 2:
            raise InvalidInput(f"{backend} accepts --relook-fps or --relook-max-frames, not both")
        # Switching modes must also pass the existing lock check for the cleared setting.
        updates["max_frames" if inputs.relook_fps is not None else "fps"] = None
    return _apply_vlm_policy(inputs.model_copy(update=updates), policy)


async def _resolve_windows(origin: str, sensor: str, inputs: VlmInput) -> tuple[list[str], Literal["iso", "offset"]]:
    ref = await vios.resolve_sensor(origin, sensor)
    segments = await vios.recorded_segments(origin, ref.stream_id)
    if not segments:
        raise vios.VIOSInvalidInputError("second look requires a recorded segment")
    bounds = [
        *vios.resolve_window(segments, inputs.start_time, inputs.end_time, ref.kind),
        *vios.resolve_window(segments, inputs.relook_start_time, inputs.relook_end_time, ref.kind),
    ]
    if ref.kind == "video":
        source_start = datetime.fromisoformat(segments[0][0].replace("Z", "+00:00"))
        bounds = [
            str((datetime.fromisoformat(value.replace("Z", "+00:00")) - source_start).total_seconds())
            for value in bounds
        ]
        return bounds, "offset"
    return bounds, "iso"


def _save_metadata(
    memory: memory_mod.Memory | None,
    reads: list[Result],
    selected: Result,
    relationship: dict[str, Any],
    details: dict[str, Any],
) -> bool:
    if memory is None:
        return True
    saved = True
    persistence_errors: tuple[type[BaseException], ...] = (MemoryNotFoundError, *memory_mod.write_failures())
    for index, read in enumerate(reads):
        if not read.body.get("persisted", read.extra.get("marker", {}).get("persisted", False)):
            continue
        metadata = relationship | {"role": "first" if index == 0 else "targeted", "selected": False}
        if read is selected:
            metadata = details
        try:
            record = memory.service.get(read.job_id)
            if record.output is None and read.exit in (Exit.SUCCESS, Exit.PARTIAL):
                raise MemoryNotFoundError("Completed read has no saved output")
            output = record.output or MemoryOutput()
            output = output.model_copy(update={"ext": {**(output.ext or {}), "second_look": metadata}})
            memory.service.upsert(record.model_copy(update={"output": output}))
        except persistence_errors:
            saved = False
    return saved


def run_second_look(group: VlmGroup, inputs: VlmInput, ctx: Context, *, policy: VlmConfig | None) -> Result:
    from .group import _vios_exit_for

    assert ctx.deployment is not None and inputs.sensor is not None
    second_inputs = _reread_inputs(inputs, policy)
    origin = ctx.deployment.base_url.rstrip("/")
    sensor = inputs.sensor
    if "vst" not in ctx.deployment.services:
        from vss_cli.config import ConfigError

        raise ConfigError("second look requires a VSS deployment with VIOS")

    try:
        bounds, time_format = asyncio.run(_resolve_windows(origin, sensor, inputs))
    except (vios.VIOSInvalidInputError, vios.VIOSNotFoundError, vios.VIOSTimeoutError, BackendUnreachableError) as exc:
        code, status = _vios_exit_for(exc)
        return Result(body={"status": status, "error": str(exc)}, exit=code)

    memory = None
    # Each read already reports an unavailable configured store as partial.
    with suppress(memory_mod.MemoryUnavailable):
        memory = group.persist_memory(ctx, no_persist=bool(ctx.extra.get("no_persist")))
    # Remuxing may start at an earlier keyframe and shift the source-time context.
    read_ctx = replace(
        ctx, memory=memory, extra={**ctx.extra, "relook_container": None, "_second_look_exact_clip": True}
    )
    analyzer = _CliAnalyzer(group, inputs, read_ctx)
    target_ctx = replace(
        read_ctx, extra={**read_ctx.extra, "_second_look_container": ctx.extra.get("relook_container")}
    )
    target_analyzer = _CliAnalyzer(group, second_inputs, target_ctx)
    try:
        try:
            outcome = asyncio.run(
                second_look(
                    analyzer,
                    sensor_id=sensor,
                    start_timestamp=bounds[0],
                    end_timestamp=bounds[1],
                    second_start_timestamp=bounds[2],
                    second_end_timestamp=bounds[3],
                    prompt=inputs.prompt,
                    time_format=time_format,
                    second_analyzer=target_analyzer,
                )
            )
        except ValueError as exc:
            raise InvalidInput(str(exc)) from exc
        except BackendUnreachableError:
            if not analyzer.reads:
                raise
            return analyzer.reads[0]

        reads = analyzer.reads + target_analyzer.reads
        selected = reads[0] if outcome.fallback_reason else reads[-1]
        relationship = {
            "group_id": reads[0].job_id,
            "job_ids": [read.job_id for read in reads],
            "selected_job_id": selected.job_id,
        }
        details = (
            asdict(outcome)
            | relationship
            | {
                "role": "first" if outcome.fallback_reason else "targeted",
                "selected": True,
                "reads": [
                    {
                        "job_id": read.job_id,
                        "persisted": read.body.get("persisted", read.extra.get("marker", {}).get("persisted", False)),
                        **({"persist_error": read.body["persist_error"]} if read.body.get("persist_error") else {}),
                    }
                    for read in reads
                ],
            }
        )
        body = dict(selected.body, answer=outcome.answer, second_look=details)
        partial = bool(outcome.fallback_reason) or any(read.exit != Exit.SUCCESS for read in reads)
        if not _save_metadata(memory, reads, selected, relationship, details):
            body["persisted"] = False
            body["persist_error"] = "Second-look metadata could not be saved."
            partial = True
        return Result(
            body=body,
            job_id=selected.job_id,
            exit=Exit.PARTIAL if partial else Exit.SUCCESS,
            extra={"marker": {"status": body["status"], "persisted": body.get("persisted", False)}},
        )
    finally:
        if memory is not None and memory is not ctx.memory:
            memory.close()
