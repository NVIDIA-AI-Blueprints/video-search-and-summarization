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

"""Bounded current-queue cleanup stress; controlled work, no M7 or remote claims."""

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
import time
import weakref
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from vss.ingestion import IngestionJobState, IngestionQueue, IngestionQueueFullError
from vss.observability import collect_vss_metrics

from .artifacts import publish_result, reserve
from .common import environment, software_hash
from .resources import ResourceSampler, ResourceTrace
from .results import Environment, Evidence
from .supervisor import TrialCleanupError, isolated_trial


class StressConfiguration(Evidence):
    cycles_per_case: int = Field(default=20, ge=1, le=100)
    duration_s: float = Field(default=0, ge=0, le=1800)
    checkpoint_interval_s: float = Field(default=1, ge=1, le=10)
    sample_interval_s: Literal[1] = 1
    cases: tuple[tuple[int, int], ...] = (
        (1, 1),
        (4, 1),
        (1, 5),
        (4, 5),
        (1, 20),
        (4, 20),
    )

    @model_validator(mode="after")
    def fixed_cases(self) -> StressConfiguration:
        if self.cases != ((1, 1), (4, 1), (1, 5), (4, 5), (1, 20), (4, 20)):
            raise ValueError("case matrix is prespecified")
        return self


class Cycle(Evidence):
    case: tuple[int, int]
    elapsed_s: float = Field(ge=0)
    admitted: int = Field(gt=0)
    succeeded: int = Field(ge=0)
    failed: Literal[1] = 1
    cancelled: Literal[2] = 2
    rejected: Literal[1] = 1
    started: int = Field(gt=0)
    cleanups: int = Field(gt=0)
    maximum_active: int = Field(gt=0)
    maximum_running: int = Field(gt=0)
    maximum_pending: int = Field(gt=0)
    pending_limit: Literal[4] = 4
    retained_payloads: Literal[0] = 0
    residual_running: Literal[0] = 0
    residual_pending: Literal[0] = 0
    recovery: Literal[True] = True
    cleanup_waiter_isolated: Literal[True] = True
    cleanup_held_capacity: Literal[True] = True
    collection_complete: Literal[True] = True
    total_observations: int = Field(gt=0, le=4096)
    total_job_spans: int = Field(gt=0)

    @model_validator(mode="after")
    def coherent(self) -> Cycle:
        concurrency, factor = self.case
        if concurrency not in {1, 4} or factor not in {1, 5, 20}:
            raise ValueError("unknown stress case")
        if (
            self.admitted != concurrency + 6
            or self.succeeded + self.failed + self.cancelled != self.admitted
            or self.started != self.admitted - 1
            or self.cleanups != self.started
            or self.maximum_active > concurrency
            or self.maximum_running > concurrency
            or self.maximum_pending > 4
            or self.total_job_spans != self.admitted
        ):
            raise ValueError("queue cycle outcomes/bounds are inconsistent")
        return self


class StressResult(Evidence):
    profile: Literal["queue.controlled_soak.v1"] = "queue.controlled_soak.v1"
    schema_version: Literal["1.0"] = "1.0"
    acceptance_eligible: Literal[False] = False
    status: Literal["ok", "incomplete"]
    failure: str | None = Field(default=None, max_length=64, pattern=r"^[a-z0-9_]+$")
    configuration: StressConfiguration
    environment: Environment
    software_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    wall_s: float = Field(ge=0)
    checkpoints: tuple[Cycle, ...] = Field(max_length=3600)
    resources: ResourceTrace | None = None
    wall_clock_soak_completed: bool = False
    scope: Literal["current_queue_controlled_4KiB_payloads"] = (
        "current_queue_controlled_4KiB_payloads"
    )

    @model_validator(mode="after")
    def coherent(self) -> StressResult:
        expected_soak = (
            self.status == "ok"
            and self.configuration.duration_s >= 1800
            and self.wall_s >= 1800
        )
        if self.wall_clock_soak_completed != expected_soak:
            raise ValueError("soak declaration does not match actual elapsed time")
        if self.status == "ok":
            if (
                self.failure is not None
                or self.resources is None
                or not self.resources.complete
                or self.wall_s < self.configuration.duration_s
            ):
                raise ValueError(
                    "successful stress needs complete elapsed/resource evidence"
                )
            for case in self.configuration.cases:
                if (
                    sum(c.case == case for c in self.checkpoints)
                    < self.configuration.cycles_per_case
                ):
                    raise ValueError("stress case cleanup repetition is missing")
        elif self.failure is None:
            raise ValueError("failed stress needs failure")
        times = [c.elapsed_s for c in self.checkpoints]
        if times != sorted(times) or any(t > self.wall_s for t in times):
            raise ValueError("unordered checkpoints")
        if self.resources is not None and self.resources.interval_s != 1:
            raise ValueError("soak sampling interval must be prespecified")
        return self


@dataclass(slots=True, weakref_slot=True)
class _Payload:
    data: bytearray


async def cycle(concurrency: int, factor: int, elapsed_s: float) -> Cycle:
    queue = IngestionQueue(max_concurrency=concurrency, max_pending=4)
    started_event, gate, cleanup_entered, cleanup_gate = (
        asyncio.Event() for _ in range(4)
    )
    active = maximum = started = cleanups = maximum_running = maximum_pending = 0
    payload_refs = []

    async def work(index: int) -> str:
        nonlocal active, maximum, started, cleanups
        payload: _Payload | None = _Payload(bytearray(4096))
        payload_refs.append(weakref.ref(payload))
        active += 1
        started += 1
        maximum = max(maximum, active)
        if started == concurrency:
            started_event.set()
        try:
            await gate.wait()
            await asyncio.sleep(factor * 0.001)
            if index == concurrency:
                raise ValueError("controlled_failure")
            return "controlled_success"
        finally:
            cleanup_entered.set()
            task = asyncio.create_task(cleanup_gate.wait())
            cancelled = False
            while not task.done():
                try:
                    await asyncio.shield(task)
                except asyncio.CancelledError:
                    cancelled = True
            task.result()
            await asyncio.sleep(factor * 0.001)
            # Explicit release also prevents a retained failure traceback from
            # keeping the controlled payload alive until cyclic GC.
            payload = None
            active -= 1
            cleanups += 1
            if cancelled:
                raise asyncio.CancelledError

    def submit(index: int):
        nonlocal maximum_running, maximum_pending
        result = queue.submit(lambda: work(index))
        maximum_running = max(maximum_running, queue.running_count)
        maximum_pending = max(maximum_pending, queue.pending_count)
        return result

    with collect_vss_metrics(max_events=4096) as metrics:
        jobs = [submit(index) for index in range(concurrency + 4)]
        try:
            await started_event.wait()
            try:
                submit(100)
                raise AssertionError("queue accepted overload")
            except IngestionQueueFullError:
                pass
            pending = jobs[-1]
            pending.cancel()
            await pending.wait_closed()
            jobs.append(submit(concurrency + 4))
            running = jobs[0]
            running.cancel()
            await cleanup_entered.wait()
            running.cancel()
            await asyncio.sleep(0)
            waiter = asyncio.create_task(running.wait_closed())
            await asyncio.sleep(0)
            waiter.cancel()
            try:
                await waiter
                raise AssertionError("cleanup waiter unexpectedly succeeded")
            except asyncio.CancelledError:
                pass
            held = (
                running.cancelled()
                and queue.running_count == concurrency
                and cleanups == 0
            )
            if not held:
                raise AssertionError(
                    "cancelled result released active capacity before cleanup"
                )
            gate.set()
            cleanup_gate.set()
            await asyncio.gather(*jobs, return_exceptions=True)
            await asyncio.gather(*(job.wait_closed() for job in jobs))
            if queue.running_count or queue.pending_count or active:
                raise AssertionError("queue did not settle")
            recovery = submit(999)
            jobs.append(recovery)
            if await recovery != "controlled_success":
                raise AssertionError("queue recovery failed")
            await recovery.wait_closed()
        finally:
            gate.set()
            cleanup_gate.set()
            for job in jobs:
                if not job.done():
                    job.cancel()
            await asyncio.gather(*jobs, return_exceptions=True)
            await asyncio.gather(*(job.wait_closed() for job in jobs))
    states = [job.state for job in jobs]
    retained = sum(ref() is not None for ref in payload_refs)
    return Cycle(
        case=(concurrency, factor),
        elapsed_s=elapsed_s,
        admitted=len(jobs),
        succeeded=states.count(IngestionJobState.SUCCEEDED),
        started=started,
        cleanups=cleanups,
        maximum_active=maximum,
        maximum_running=maximum_running,
        maximum_pending=maximum_pending,
        retained_payloads=retained,
        residual_running=queue.running_count,
        residual_pending=queue.pending_count,
        total_observations=len(metrics.observations),
        total_job_spans=sum(
            e.name == "ingestion.queue.total" for e in metrics.observations
        ),
        collection_complete=metrics.complete,
    )


async def worker(
    config: StressConfiguration, checkpoint_path: Path | None = None
) -> StressResult:
    started = time.perf_counter()
    worker_environment = environment()
    worker_software = software_hash(Path(__file__).resolve().parents[1])
    checkpoints = []
    failure = None
    sampler = ResourceSampler(1)
    with sampler:
        try:
            index = 0
            while (
                index < len(config.cases) * config.cycles_per_case
                or time.perf_counter() - started < config.duration_s
            ):
                if len(checkpoints) >= 3600:
                    raise ValueError("checkpoint_limit")
                step = time.perf_counter()
                concurrency, factor = config.cases[index % len(config.cases)]
                evidence = await asyncio.wait_for(
                    cycle(concurrency, factor, step - started), timeout=5
                )
                checkpoints.append(evidence)
                index += 1
                if checkpoint_path is not None:
                    # Last fully validated checkpoint survives supervisor timeout.
                    temporary_checkpoint = checkpoint_path.with_suffix(".partial")
                    temporary_checkpoint.write_text(
                        json.dumps(
                            {
                                "completed_cycles": len(checkpoints),
                                "elapsed_s": time.perf_counter() - started,
                            }
                        )
                        + "\n"
                    )
                    temporary_checkpoint.replace(checkpoint_path)
                if config.duration_s:
                    await asyncio.sleep(
                        max(
                            0,
                            config.checkpoint_interval_s - (time.perf_counter() - step),
                        )
                    )
        except Exception:
            failure = "cycle_or_checkpoint"
        except asyncio.CancelledError:
            failure = "cancelled"
    wall = time.perf_counter() - started
    if not sampler.trace.complete:
        failure = "resource_collection"
    return StressResult(
        status="incomplete" if failure else "ok",
        failure=failure,
        configuration=config,
        environment=worker_environment,
        software_sha256=worker_software,
        wall_s=wall,
        checkpoints=tuple(checkpoints),
        resources=sampler.trace,
        wall_clock_soak_completed=not failure
        and config.duration_s >= 1800
        and wall >= 1800,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cycles-per-case", type=int, default=20)
    parser.add_argument("--duration", type=float, default=0)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    try:
        config = StressConfiguration(
            cycles_per_case=args.cycles_per_case, duration_s=args.duration
        )
    except ValueError:
        parser.error("invalid stress limits")
    if args.worker:
        print(
            asyncio.run(
                worker(config, args.output.with_suffix(".checkpoint.json"))
            ).model_dump_json()
        )
        return 0
    if args.output.exists():
        parser.error("outputs are immutable; use a new output name")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with reserve(args.output):
        if args.output.with_suffix(".checkpoint.json").exists():
            parser.error("checkpoint exists; use a new output name")
        failure = None
        try:
            raw = isolated_trial(
                [
                    sys.executable,
                    "-m",
                    "benchmarks.backpressure",
                    "--worker",
                    "--output",
                    str(args.output.resolve()),
                    "--cycles-per-case",
                    str(config.cycles_per_case),
                    "--duration",
                    str(config.duration_s),
                ],
                cwd=Path(__file__).resolve().parents[1],
                timeout=max(30, config.cycles_per_case * 2, config.duration_s + 60),
            )
            result = StressResult.model_validate_json(raw)
            if result.configuration != config:
                raise ValueError("worker configuration mismatch")
        except subprocess.TimeoutExpired:
            failure = "timeout"
        except TrialCleanupError:
            failure = "cleanup_failure"
        except KeyboardInterrupt:
            failure = "cancelled"
        except Exception:
            failure = "invalid_child_evidence"
        if failure is not None:
            result = StressResult(
                status="incomplete",
                failure=failure,
                configuration=config,
                environment=environment(),
                software_sha256=software_hash(Path(__file__).resolve().parents[1]),
                wall_s=0,
                checkpoints=(),
            )
        publish_result(args.output, result, reserved=True)
    print(
        json.dumps(
            {
                "status": result.status,
                "cycles": len(result.checkpoints),
                "wall_clock_soak_completed": result.wall_clock_soak_completed,
            }
        )
    )
    return 0 if result.status == "ok" else 3


if __name__ == "__main__":
    raise SystemExit(main())
