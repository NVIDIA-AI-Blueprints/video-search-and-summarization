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

"""Bounded NVML snapshots, explicit CUDA UUID and host-PID attribution."""

from __future__ import annotations

import ctypes
import hashlib
import json
import os
import threading
import time
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from .results import Evidence


class GPUIdentity(Evidence):
    uuid_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    name: str = Field(max_length=128)
    driver: str = Field(max_length=32)
    memory_bytes: int = Field(gt=0)
    image_id: str = Field(pattern=r"^sha256:[a-f0-9]{64}$")
    base_digest: str = Field(pattern=r"^sha256:[a-f0-9]{64}$")
    pid_mode: Literal["host"] = "host"
    capabilities: Literal["compute,utility,video"] = "compute,utility,video"


class GPUSample(Evidence):
    elapsed_s: float = Field(ge=0)
    device_used_bytes: int = Field(ge=0)
    worker_tree_used_bytes: int | None = Field(default=None, ge=0)
    gpu_utilization_pct: int = Field(ge=0, le=100)
    decoder_utilization_pct: int | None = Field(default=None, ge=0, le=100)
    memory_controller_utilization_pct: int | None = Field(default=None, ge=0, le=100)
    unrelated_processes: int = Field(ge=0)


class GPUTrace(Evidence):
    scope: Literal["sampled_device_and_host_worker_tree_lower_bound"] = (
        "sampled_device_and_host_worker_tree_lower_bound"
    )
    interval_s: Literal[0.25] = 0.25
    complete: bool
    samples: tuple[GPUSample, ...] = Field(max_length=6000)

    @model_validator(mode="after")
    def coherent(self):
        if self.complete:
            times = [s.elapsed_s for s in self.samples]
            if (
                len(times) < 2
                or any(
                    b <= a or b - a > 1.0
                    for a, b in zip(times, times[1:], strict=False)
                )
                or any(s.unrelated_processes for s in self.samples)
            ):
                raise ValueError("GPU trace gaps/interference")
        return self


class GPUIdleCheck(Evidence):
    pair: int = Field(ge=0, lt=100)
    phase: Literal["before", "after"]
    gpu: GPUIdentity
    elapsed_s: float = Field(ge=0, le=10)
    polls: int = Field(ge=1, le=41)
    quiescent: bool
    remaining_processes: int = Field(ge=0, le=512)

    @model_validator(mode="after")
    def coherent(self):
        if self.quiescent != (self.remaining_processes == 0):
            raise ValueError("GPU idle check contradiction")
        return self


def idle_check(pair: int, phase: str) -> GPUIdleCheck:
    """Outside timed workers; wait at most five seconds for driver PID release.

    Both process queries must work. Unknown memory still counts as activity.
    No other processes are exempted and no failed worker is retried.
    """
    sampler = GPUSampler()
    started = time.perf_counter()
    polls = 0
    try:
        while True:
            polls += 1
            pids = set()
            for query in (
                sampler.nvml.nvmlDeviceGetComputeRunningProcesses,
                sampler.nvml.nvmlDeviceGetGraphicsRunningProcesses,
            ):
                rows = query(sampler.handle)
                if len(rows) > 256:
                    raise ValueError("GPU idle process budget")
                pids.update(p.pid for p in rows)
            elapsed = time.perf_counter() - started
            if not pids or elapsed >= 5:
                return GPUIdleCheck(
                    pair=pair,
                    phase=phase,
                    gpu=sampler.identity,
                    elapsed_s=elapsed,
                    polls=polls,
                    quiescent=not pids,
                    remaining_processes=len(pids),
                )
            time.sleep(0.25)
    finally:
        sampler.close_unused()


def _text(value):
    return value.decode() if isinstance(value, bytes) else value


def cuda_uuid() -> str:
    driver = ctypes.CDLL("libcuda.so.1")
    device = ctypes.c_int()
    value = (ctypes.c_ubyte * 16)()
    driver.cuInit.argtypes = [ctypes.c_uint]
    driver.cuDeviceGet.argtypes = [ctypes.POINTER(ctypes.c_int), ctypes.c_int]
    get_uuid = getattr(driver, "cuDeviceGetUuid_v2", None) or driver.cuDeviceGetUuid
    get_uuid.argtypes = [ctypes.c_void_p, ctypes.c_int]
    if (
        driver.cuInit(0)
        or driver.cuDeviceGet(ctypes.byref(device), 0)
        or get_uuid(value, device.value)
    ):
        raise ValueError("CUDA ordinal UUID unavailable")
    raw = bytes(value).hex()
    return "GPU-" + "-".join([raw[:8], raw[8:12], raw[12:16], raw[16:20], raw[20:]])


def runner_identity(nvml, handle) -> GPUIdentity:
    path = Path(os.environ.get("M8_RUNNER_MANIFEST", "/run/m8-runner.json"))
    if path.stat().st_size > 16384:
        raise ValueError("runner manifest budget")
    manifest = json.loads(path.read_bytes())
    actual = _text(nvml.nvmlDeviceGetUUID(handle))
    if actual.startswith("MIG-") or actual.lower() != cuda_uuid().lower():
        raise ValueError("CUDA/NVML identity mismatch")
    try:
        mode, _ = nvml.nvmlDeviceGetMigMode(handle)
        if mode:
            raise ValueError("MIG not admitted")
    except nvml.NVMLError_NotSupported:
        pass
    if (
        manifest["pid_mode"] != "host"
        or manifest["capabilities"] != "compute,utility,video"
    ):
        raise ValueError("actual Docker host PID/video policy required")
    if (
        manifest["gpu_uuid_sha256"]
        != hashlib.sha256(actual.lower().encode()).hexdigest()
    ):
        raise ValueError("manifest GPU identity mismatch")
    return GPUIdentity(
        uuid_sha256=manifest["gpu_uuid_sha256"],
        name=_text(nvml.nvmlDeviceGetName(handle)),
        driver=_text(nvml.nvmlSystemGetDriverVersion()),
        memory_bytes=nvml.nvmlDeviceGetMemoryInfo(handle).total,
        image_id=manifest["image_id"],
        base_digest=manifest["base_digest"],
    )


class GPUSampler:
    def __init__(self):
        import psutil
        import pynvml

        self.nvml = pynvml
        pynvml.nvmlInit()
        try:
            self.handle = pynvml.nvmlDeviceGetHandleByUUID(cuda_uuid())
            self.identity = runner_identity(pynvml, self.handle)
            self.process = psutil.Process(os.getpid())
        except BaseException:
            pynvml.nvmlShutdown()
            raise
        self.closed = False
        self.samples = []
        self.complete = True
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)

    def _sample(self):
        n = self.nvml
        try:
            if len(self.samples) >= 6000:
                raise ValueError("GPU sample budget")
            children = self.process.children(recursive=True)
            if len(children) > 128:
                raise ValueError("worker tree budget")
            owned = {self.process.pid, *(p.pid for p in children)}
            rows = {}
            process_ids = set()
            available = True
            for query in (
                n.nvmlDeviceGetComputeRunningProcesses,
                n.nvmlDeviceGetGraphicsRunningProcesses,
            ):
                try:
                    values = query(self.handle)
                except n.NVMLError_NotSupported:
                    available = False
                    self.complete = False
                    continue
                if len(values) > 256:
                    raise ValueError("GPU process budget")
                for p in values:
                    process_ids.add(p.pid)
                    used = p.usedGpuMemory
                    if used is None or used == 2**64 - 1:
                        available = False
                    else:
                        rows[p.pid] = max(rows.get(p.pid, 0), used)
            try:
                decoder, _ = n.nvmlDeviceGetDecoderUtilization(self.handle)
            except n.NVMLError_NotSupported:
                decoder = None
            utilization = n.nvmlDeviceGetUtilizationRates(self.handle)
            sample = GPUSample(
                elapsed_s=time.perf_counter() - self.started,
                device_used_bytes=n.nvmlDeviceGetMemoryInfo(self.handle).used,
                worker_tree_used_bytes=sum(v for p, v in rows.items() if p in owned)
                if available
                else None,
                gpu_utilization_pct=utilization.gpu,
                memory_controller_utilization_pct=getattr(utilization, "memory", None),
                decoder_utilization_pct=decoder,
                unrelated_processes=len(process_ids - owned),
            )
            self.samples.append(sample)
            if sample.unrelated_processes:
                self.complete = False
        except Exception:
            self.complete = False
            self.stop.set()

    def _run(self):
        while not self.stop.wait(0.25):
            self._sample()

    def __enter__(self):
        self.started = time.perf_counter()
        self._sample()
        try:
            self.thread.start()
        except BaseException:
            self.close_unused()
            raise
        return self

    def __exit__(self, *_):
        self.stop.set()
        self.thread.join(timeout=3)
        self.complete &= not self.thread.is_alive()
        if not self.thread.is_alive():
            self._sample()
            self.nvml.nvmlShutdown()
            self.closed = True
        times = [s.elapsed_s for s in self.samples]
        self.complete &= len(times) >= 2 and all(
            0 < b - a <= 1 for a, b in zip(times, times[1:], strict=False)
        )
        self.trace = GPUTrace(complete=self.complete, samples=tuple(self.samples))

    def close_unused(self):
        if not self.closed and not self.thread.is_alive():
            self.nvml.nvmlShutdown()
            self.closed = True
