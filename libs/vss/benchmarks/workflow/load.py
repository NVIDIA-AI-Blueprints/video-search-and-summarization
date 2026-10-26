# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Cooperative load scheduling core, not yet a live workflow adapter.

An owned outer process must supervise runtime use: Python cancellation cannot
force an arbitrary client or its session cleanup to settle. Do not bind the
legacy workflow operation, which recreates clients and mutates environment.
"""

import asyncio
import math
import time
from typing import Literal

from pydantic import Field, model_validator

from benchmarks.e2e.config import Strict


class Load(Strict):
    mode: Literal["closed", "open"]
    concurrency: int = Field(ge=1, le=32)
    window_s: float = Field(gt=0, le=1800)
    maximum_operations: int = Field(default=1000, ge=1, le=10000)
    operation_timeout_s: float = Field(gt=0, le=1800)
    drain_s: float = Field(gt=0, le=1800)
    cancellation_grace_s: float = Field(default=1, gt=0, le=30)
    queue_capacity: int = Field(default=1, ge=1, le=1024)
    arrivals_per_s: float | None = Field(default=None, gt=0, le=1000)
    maximum_lateness_s: float = Field(default=0.05, ge=0, le=1)

    @model_validator(mode="after")
    def coherent(self):
        if (self.mode == "open") != (self.arrivals_per_s is not None):
            raise ValueError("only open load has an arrival rate")
        return self


def _quantiles(values):
    ordered = sorted(values)
    return {
        f"p{percent}_s": ordered[math.ceil(percent / 100 * len(ordered)) - 1]
        if len(ordered) >= minimum
        else None
        for percent, minimum in ((50, 2), (90, 100), (95, 100))
    }


async def drive(config: Load, invoke):
    """invoke(index) returns literal True after operation and local cleanup.

    Fixed workers bound task count. Open arrivals use a fixed grid, drop late
    slots and reject a full queue; they never create a catch-up request burst.
    Failure stops new admissions; already started calls get a bounded soft
    drain. An unsettled task requires the caller's process supervisor to act.
    """
    started = time.monotonic()
    rows, halted = [], asyncio.Event()
    producer_done = asyncio.Event()
    queue = asyncio.Queue(maxsize=config.queue_capacity)
    active, overlap = 0, 0

    def elapsed():
        return time.monotonic() - started

    async def execute(row):
        nonlocal active, overlap
        if halted.is_set():
            row["status"] = "cancelled_before_start"
            return
        row.update(start_s=elapsed(), status="running")
        active += 1
        overlap = max(overlap, active)
        try:
            result = await asyncio.wait_for(
                invoke(row["index"]), config.operation_timeout_s
            )
            if result is not True:
                raise ValueError("operation_or_local_cleanup_unconfirmed")
            row["status"] = "completed"
        except asyncio.CancelledError:
            row["status"] = "cancelled_in_flight_remote_settlement_unknown"
            halted.set()
            raise
        except Exception as error:
            row.update(status="failed", failure=type(error).__name__)
            halted.set()
        finally:
            row["end_s"] = elapsed()
            active -= 1

    async def closed_worker():
        while not halted.is_set() and elapsed() < config.window_s:
            if len(rows) >= config.maximum_operations:
                break
            now = elapsed()
            row = {"index": len(rows), "scheduled_s": now, "admitted_s": now}
            rows.append(row)
            await execute(row)

    async def open_worker():
        while not producer_done.is_set() or not queue.empty():
            try:
                row = await asyncio.wait_for(queue.get(), 0.02)
            except TimeoutError:
                continue
            try:
                await execute(row)
            finally:
                queue.task_done()

    workers = [
        asyncio.create_task(
            closed_worker() if config.mode == "closed" else open_worker()
        )
        for _ in range(config.concurrency)
    ]
    stopped_admission_s = None
    try:
        if config.mode == "open":
            offered_target = math.ceil(config.window_s * config.arrivals_per_s)
            for index in range(min(offered_target, config.maximum_operations)):
                scheduled = index / config.arrivals_per_s
                if scheduled >= config.window_s or halted.is_set():
                    break
                delay = scheduled - elapsed()
                if delay > 0:
                    try:
                        await asyncio.wait_for(halted.wait(), delay)
                    except TimeoutError:
                        pass
                if halted.is_set():
                    break
                now = elapsed()
                row = {"index": index, "scheduled_s": scheduled}
                rows.append(row)
                if (
                    now >= config.window_s
                    or now - scheduled > config.maximum_lateness_s
                    or now - scheduled >= 1 / config.arrivals_per_s
                ):
                    row["status"] = "missed_arrival"
                elif queue.full():
                    row["status"] = "rejected_queue_full"
                else:
                    row.update(admitted_s=now, status="queued")
                    queue.put_nowait(row)
            stopped_admission_s = elapsed()
            producer_done.set()
            _, pending = await asyncio.wait(workers, timeout=config.drain_s)
        else:
            offered_target = None
            # Covers the admission window plus final in-flight calls.
            _, pending = await asyncio.wait(
                workers, timeout=config.window_s + config.drain_s
            )
            stopped_admission_s = min(elapsed(), config.window_s)
    finally:
        halted.set()
        producer_done.set()
        pending = {task for task in workers if not task.done()}
        for task in pending:
            task.cancel()
        if pending:
            _, pending = await asyncio.wait(
                pending, timeout=config.cancellation_grace_s
            )
        # Retrieve exceptions without awaiting cancellation-resistant callbacks.
        for task in workers:
            if task.done() and not task.cancelled():
                error = task.exception()
                if error is not None:
                    raise error
    finished = elapsed()
    completed = [row for row in rows if row["status"] == "completed"]
    for row in rows:
        if "start_s" in row:
            row["queue_wait_s"] = row["start_s"] - row["admitted_s"]
        if row["status"] == "completed":
            row["callback_wall_s"] = row["end_s"] - row["start_s"]
            row["arrival_to_result_s"] = row["end_s"] - row["scheduled_s"]
        elif row["status"] == "queued":
            row["status"] = "cancelled_before_start"
    issues = set()
    if pending:
        issues.add("unsettled_tasks_require_process_supervisor")
    if any(
        row["status"] not in {"completed", "rejected_queue_full", "missed_arrival"}
        for row in rows
    ):
        issues.add("failed_cancelled_or_unsettled_operations")
    if len(rows) == config.maximum_operations and (
        config.mode == "closed" or offered_target > config.maximum_operations
    ):
        issues.add("operation_limit_may_truncate_window")
    return {
        "schema_version": "cooperative_load.1",
        "status": "incomplete" if issues else "completed_diagnostic",
        "issues": sorted(issues),
        "config": config.model_dump(mode="json"),
        "runtime_binding": "pending_supervised_persistent_client_adapter",
        "remote_settlement": "not_proven_by_local_completion",
        "acceptance_eligible": False,
        "offered_target": offered_target,
        "offered": len(rows),
        "admitted": sum("admitted_s" in row for row in rows),
        "completed": len(completed),
        "status_counts": {
            status: sum(row["status"] == status for row in rows)
            for status in sorted({row["status"] for row in rows})
        },
        "observed_overlap": overlap,
        "tasks_settled": not pending,
        "maximum_operations_reached": len(rows) == config.maximum_operations,
        "admission_stopped_s": stopped_admission_s,
        "elapsed_including_drain_s": finished,
        "completed_per_elapsed_s": len(completed) / finished,
        "completed_inside_window_per_s": sum(
            row["end_s"] <= config.window_s for row in completed
        )
        / config.window_s,
        "latency_population": "completed_only_report_failures_and_censoring_alongside",
        "callback_wall": _quantiles([row["callback_wall_s"] for row in completed]),
        "arrival_to_result": _quantiles(
            [row["arrival_to_result_s"] for row in completed]
        ),
        "operations": rows,
    }


async def session_phase(config: Load, session_factory, *, warmups=1):
    """One cooperative session for warmup and measurement; no reuse claim.

    The eventual live factory must prove persistent, concurrency-safe SDK
    clients, cleanup and request controls. Context exit needs outer supervision.
    """
    if type(warmups) is not int or not 0 <= warmups <= 10:
        raise ValueError("warmup count")
    async with session_factory() as invoke:
        for index in range(warmups):
            if (
                await asyncio.wait_for(invoke(-index - 1), config.operation_timeout_s)
                is not True
            ):
                raise ValueError("warmup_or_local_cleanup_unconfirmed")
        return await drive(config, invoke)
