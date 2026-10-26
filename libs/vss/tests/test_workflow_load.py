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

"""Load faults and saturation stay visible; cancellation is only cooperative."""

import asyncio
import subprocess
import sys
import time
from contextlib import asynccontextmanager

import pytest

from benchmarks.workflow.load import Load, drive, session_phase
from benchmarks.workflow.soak import drive as soak_drive


def config(**updates):
    return Load.model_validate(
        dict(
            mode="closed",
            concurrency=2,
            window_s=0.06,
            maximum_operations=20,
            operation_timeout_s=0.2,
            drain_s=0.3,
            **updates,
        )
    )


@pytest.mark.parametrize("operation_s", [1, 7])
def test_soak_default_runs_full_window_beyond_256_operations(operation_s):
    now = 0

    async def invoke(index):
        nonlocal now
        now += operation_s
        return {
            "status": "completed_diagnostic",
            "cleanup": {"closed": True},
            "workflow_to_result_s": operation_s,
        }

    report = asyncio.run(soak_drive(1800, invoke, clock=lambda: now))
    assert report["status"] == "complete_closed_loop" and report["failure"] is None
    assert report["completed_operations"] == report["admitted_operations"] > 256
    assert 1800 <= report["elapsed_s"] < 1800 + operation_s
    assert report["operations"][-1]["admitted_monotonic_s"] < 1800


@pytest.mark.parametrize("fail", [False, True])
def test_soak_preserves_explicit_operation_cap_and_failure_handling(fail):
    now = 0

    async def invoke(index):
        nonlocal now
        now += 1
        if fail and index == 1:
            raise RuntimeError("private detail")
        return {
            "status": "completed_diagnostic",
            "cleanup": {"closed": True},
            "workflow_to_result_s": 1,
        }

    report = asyncio.run(
        soak_drive(1800, invoke, maximum_operations=3, clock=lambda: now)
    )
    assert report["status"] == "incomplete"
    assert report["admitted_operations"] == (2 if fail else 3)
    assert report["failure"] == (
        "RuntimeError" if fail else "maximum_operations_before_window_complete"
    )
    assert "private detail" not in str(report)


def test_closed_loop_observed_overlap_bounded_and_no_false_tail_quantiles():
    active, peak = 0, 0

    async def invoke(index):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.02)
        active -= 1
        return True

    report = asyncio.run(drive(config(), invoke))
    assert peak == report["observed_overlap"] == 2
    assert report["tasks_settled"] and report["admitted"] == report["completed"]
    assert report["callback_wall"]["p95_s"] is None
    assert report["acceptance_eligible"] is False
    assert all(row["queue_wait_s"] >= 0 for row in report["operations"])


def test_fixed_rate_queue_rejections_visible_no_unbounded_waiting_tasks():
    async def invoke(index):
        await asyncio.sleep(0.04)
        return True

    cfg = Load(
        mode="open",
        concurrency=1,
        window_s=0.06,
        arrivals_per_s=200,
        maximum_lateness_s=0.05,
        queue_capacity=1,
        operation_timeout_s=0.2,
        drain_s=0.3,
    )
    report = asyncio.run(drive(cfg, invoke))
    assert report["offered"] == report["offered_target"] == 12
    assert report["status_counts"]["rejected_queue_full"] > 0
    assert report["observed_overlap"] == 1 and report["tasks_settled"]
    assert report["admitted"] == report["completed"]
    assert any(
        row["arrival_to_result_s"] > row["callback_wall_s"] + 0.005
        for row in report["operations"]
        if row["status"] == "completed"
    )


def test_first_failure_stops_admission_and_timeout_settles_cooperative_callback():
    async def fail(index):
        raise RuntimeError("private message")

    report = asyncio.run(drive(config(), fail))
    assert report["admitted"] <= 2 and report["completed"] == 0
    assert "private message" not in str(report)
    cancelled = []

    async def slow(index):
        try:
            await asyncio.sleep(10)
        finally:
            cancelled.append(index)

    cfg = Load(
        mode="closed",
        concurrency=1,
        window_s=0.1,
        operation_timeout_s=0.01,
        drain_s=0.1,
    )
    report = asyncio.run(drive(cfg, slow))
    assert cancelled == [0] and report["tasks_settled"]
    assert report["status_counts"] == {"failed": 1}
    assert report["operations"][0]["failure"] == "TimeoutError"


def test_delayed_open_arrivals_are_missed_without_catchup():
    async def invoke(index):
        # A blocking client simulates an event-loop stall; arrivals stay on grid.
        time.sleep(0.04)
        return True

    cfg = Load(
        mode="open",
        concurrency=1,
        window_s=0.1,
        arrivals_per_s=100,
        maximum_lateness_s=0.005,
        queue_capacity=1,
        operation_timeout_s=0.2,
        drain_s=0.3,
    )
    report = asyncio.run(drive(cfg, invoke))
    assert report["status_counts"]["missed_arrival"] > 0
    assert report["observed_overlap"] == 1 and report["tasks_settled"]
    assert report["completed"] < report["offered"]


def test_large_lateness_tolerance_still_cannot_replay_elapsed_grid_slots():
    async def invoke(index):
        if index == 0:
            time.sleep(0.026)
        await asyncio.sleep(0)
        return True

    cfg = Load(
        mode="open",
        concurrency=16,
        window_s=0.06,
        arrivals_per_s=200,
        maximum_lateness_s=0.05,
        queue_capacity=50,
        operation_timeout_s=0.2,
        drain_s=0.3,
    )
    report = asyncio.run(drive(cfg, invoke))
    assert report["status_counts"]["missed_arrival"] >= 4
    assert report["completed"] <= report["offered"] - 4


def test_operation_cap_does_not_become_completed_sustained_window():
    async def invoke(index):
        return True

    cfg = Load(
        mode="closed",
        concurrency=1,
        window_s=1,
        maximum_operations=1,
        operation_timeout_s=0.1,
        drain_s=0.1,
    )
    report = asyncio.run(drive(cfg, invoke))
    assert report["completed"] == 1 and report["status"] == "incomplete"
    assert "operation_limit_may_truncate_window" in report["issues"]


def test_warmup_and_measurement_share_single_context_then_cleanup():
    events = []

    @asynccontextmanager
    async def session():
        events.append("open")

        async def invoke(index):
            events.append(index)
            await asyncio.sleep(0.01)
            return True

        try:
            yield invoke
        finally:
            events.append("close")

    report = asyncio.run(session_phase(config(), session, warmups=1))
    assert events[0:2] == ["open", -1] and events[-1] == "close"
    assert events.count("open") == events.count("close") == 1
    assert report["runtime_binding"].startswith("pending")


def test_cancellation_resistant_callback_requires_owned_process_supervisor():
    # The subprocess supervisor, not an asyncio timeout, enforces this bound.
    script = """
import asyncio
from benchmarks.workflow.load import Load, drive
async def resistant(index):
    while True:
        try:
            await asyncio.sleep(0.01)
        except asyncio.CancelledError:
            pass
async def main():
    config = Load(mode="closed", concurrency=1, window_s=.01,
        operation_timeout_s=.01, drain_s=.01, cancellation_grace_s=.01)
    report = await drive(config, resistant)
    print(report["tasks_settled"], flush=True)
asyncio.run(main())
"""
    with pytest.raises(subprocess.TimeoutExpired) as caught:
        subprocess.run([sys.executable, "-c", script], capture_output=True, timeout=2)
    assert b"False" in caught.value.stdout


@pytest.mark.parametrize(
    "settings",
    [
        dict(mode="closed", concurrency=1, arrivals_per_s=2),
        dict(mode="open", concurrency=1),
        dict(mode="closed", concurrency=100),
    ],
)
def test_invalid_load_shape(settings):
    with pytest.raises(ValueError):
        Load(**settings, window_s=1, operation_timeout_s=1, drain_s=1)
