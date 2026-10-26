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

"""Bounded, non-atomic process-tree RSS samples; never presented as exact peaks."""

from __future__ import annotations

import os
import threading
import time
from typing import Literal

from pydantic import Field, model_validator

from .results import Evidence


class ResourceSample(Evidence):
    elapsed_s: float = Field(ge=0)
    self_rss_bytes: int = Field(ge=0)
    tree_rss_bytes: int = Field(ge=0)
    observed_processes: int = Field(ge=1)
    host_cpu_utilization_pct: float | None = Field(default=None, ge=0, le=100)
    host_memory_used_bytes: int | None = Field(default=None, ge=0)
    host_memory_total_bytes: int | None = Field(default=None, gt=0)
    host_memory_utilization_pct: float | None = Field(default=None, ge=0, le=100)

    @model_validator(mode="after")
    def coherent(self) -> ResourceSample:
        if self.tree_rss_bytes < self.self_rss_bytes:
            raise ValueError("tree RSS must include the worker")
        return self


class ResourceTrace(Evidence):
    scope: Literal["sampled_process_tree_lower_bound"] = (
        "sampled_process_tree_lower_bound"
    )
    interval_s: float = Field(gt=0)
    complete: bool
    samples: tuple[ResourceSample, ...] = Field(max_length=6000)
    self_cpu_time_s: float = Field(ge=0)
    cpu_observation_wall_s: float | None = Field(default=None, gt=0)
    # Includes reaped tool children, unlike summing only live process samples.
    reaped_children_cpu_time_s: float = Field(ge=0)
    self_read_bytes: int | None = Field(default=None, ge=0)
    self_write_bytes: int | None = Field(default=None, ge=0)
    io_scope: Literal["worker_only", "unsupported"]

    @model_validator(mode="after")
    def coherent(self) -> ResourceTrace:
        times = [s.elapsed_s for s in self.samples]
        if any(b <= a for a, b in zip(times, times[1:], strict=False)) or (
            self.complete and len(times) < 2
        ):
            raise ValueError("trace needs ordered start/end samples")
        absent = self.self_read_bytes is None or self.self_write_bytes is None
        if (self.io_scope == "unsupported") != absent or (
            absent
            and (self.self_read_bytes is not None or self.self_write_bytes is not None)
        ):
            raise ValueError("I/O availability contradicts values")
        return self


class ResourceSampler:
    def __init__(self, interval_s: float) -> None:
        import psutil

        self.process = psutil.Process(os.getpid())
        self.interval = interval_s
        self.samples: list[ResourceSample] = []
        self.complete = True
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.host_cpu_previous = None

    def _host(self, psutil):
        """Host/shared counters; never confuse these with a container quota."""
        values = {}
        try:
            counters = psutil.cpu_times()._asdict()
            # Linux guest times are already included in user/nice.
            total = sum(counters.values()) - sum(
                counters.get(k, 0) for k in ("guest", "guest_nice")
            )
            idle = sum(counters.get(k, 0) for k in ("idle", "iowait"))
            previous = self.host_cpu_previous
            self.host_cpu_previous = (total, idle)
            if previous is not None:
                dt, di = total - previous[0], idle - previous[1]
                if dt > 0 and 0 <= di <= dt:
                    values["host_cpu_utilization_pct"] = 100 * (dt - di) / dt
        except (psutil.Error, OSError, AttributeError, NotImplementedError):
            self.host_cpu_previous = None
        try:
            memory = psutil.virtual_memory()
            values.update(
                host_memory_used_bytes=memory.used,
                host_memory_total_bytes=memory.total,
                host_memory_utilization_pct=memory.percent,
            )
        except (psutil.Error, OSError, AttributeError, NotImplementedError):
            pass
        return values

    def _sample(self) -> None:
        import psutil

        if len(self.samples) >= 6000:
            self.complete = False
            self.stop.set()
            return
        try:
            self_rss = self.process.memory_info().rss
            tree_rss, count = self_rss, 1
            for child in self.process.children(recursive=True):
                try:
                    tree_rss += child.memory_info().rss
                    count += 1
                except psutil.NoSuchProcess:
                    # Exiting between enumeration and sampling is expected;
                    # short-lived children/peaks may be missed by this method.
                    continue
            self.samples.append(
                ResourceSample(
                    elapsed_s=time.perf_counter() - self.started,
                    self_rss_bytes=self_rss,
                    tree_rss_bytes=tree_rss,
                    observed_processes=count,
                    **self._host(psutil),
                )
            )
        except (psutil.Error, OSError):
            self.complete = False

    def _run(self) -> None:
        while not self.stop.wait(self.interval):
            self._sample()

    def _io(self) -> tuple[int, int] | None:
        import psutil

        try:
            value = self.process.io_counters()
            return value.read_bytes, value.write_bytes
        except (psutil.Error, OSError, AttributeError, NotImplementedError):
            return None

    def __enter__(self) -> ResourceSampler:
        self.started = time.perf_counter()
        self.cpu_started = time.perf_counter()
        self.cpu_start = os.times()
        self.io_start = self._io()
        self._sample()
        self.thread.start()
        return self

    def __exit__(self, *_: object) -> None:
        self.stop.set()
        self.thread.join()
        self._sample()
        cpu_end = os.times()
        cpu_wall_s = time.perf_counter() - self.cpu_started
        io_end = self._io()
        known_io = self.io_start is not None and io_end is not None
        self.trace = ResourceTrace(
            interval_s=self.interval,
            complete=self.complete,
            samples=tuple(self.samples),
            cpu_observation_wall_s=cpu_wall_s,
            self_cpu_time_s=max(
                0,
                cpu_end.user
                + cpu_end.system
                - self.cpu_start.user
                - self.cpu_start.system,
            ),
            reaped_children_cpu_time_s=max(
                0,
                cpu_end.children_user
                + cpu_end.children_system
                - self.cpu_start.children_user
                - self.cpu_start.children_system,
            ),
            self_read_bytes=io_end[0] - self.io_start[0] if known_io else None,
            self_write_bytes=io_end[1] - self.io_start[1] if known_io else None,
            io_scope="worker_only" if known_io else "unsupported",
        )
