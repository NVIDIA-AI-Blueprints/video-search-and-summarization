# SPDX-FileCopyrightText: Copyright (c) 2024-2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""GPU monitoring via NVML for per-container GPU memory and utilization tracking.

Provides a thin wrapper around pynvml that resolves container PIDs and sums
their GPU memory usage.  Falls back to zero values when no GPU is present
or pynvml is not installed.
"""

import subprocess
import sys
from dataclasses import dataclass
from typing import Optional, Set

try:
    import pynvml

    _NVML_AVAILABLE = True
except ImportError:
    _NVML_AVAILABLE = False


@dataclass
class GpuSnapshot:
    """Single point-in-time GPU reading."""

    gpu_mem_used_bytes: int = 0
    gpu_mem_total_bytes: int = 0
    gpu_utilization_pct: float = 0.0
    enc_utilization_pct: float = 0.0
    dec_utilization_pct: float = 0.0
    container_gpu_mem_bytes: int = 0


_ZERO_SNAPSHOT = GpuSnapshot()


class NvmlMonitor:
    """Per-container GPU monitor using NVML.

    Args:
        containerName: Docker container name whose GPU processes to track.
        gpuIndex: GPU device index (default 0).
    """

    def __init__(self, containerName: str, gpuIndex: int = 0):
        self.m_containerName = containerName
        self.m_gpuIndex = gpuIndex
        self.m_handle = None
        self.m_available = False
        self._Init()

    def _Init(self) -> None:
        if not _NVML_AVAILABLE:
            print("  Note: pynvml not installed, GPU stats will be 0", file=sys.stderr)
            return
        try:
            pynvml.nvmlInit()
            self.m_handle = pynvml.nvmlDeviceGetHandleByIndex(self.m_gpuIndex)
            self.m_available = True
            name = pynvml.nvmlDeviceGetName(self.m_handle)
            if isinstance(name, bytes):
                name = name.decode()
            print(f"GPU:       {name} (index {self.m_gpuIndex})")
        except pynvml.NVMLError as e:
            print(f"  Note: NVML init failed ({e}), GPU stats will be 0", file=sys.stderr)

    def Shutdown(self) -> None:
        if self.m_available:
            try:
                pynvml.nvmlShutdown()
            except pynvml.NVMLError:
                pass
            self.m_available = False

    def _GetContainerPids(self) -> Set[int]:
        """Get all PIDs running inside the target container."""
        try:
            result = subprocess.run(
                ["docker", "top", self.m_containerName, "-eo", "pid"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode != 0:
                return set()
            pids: Set[int] = set()
            for line in result.stdout.strip().splitlines()[1:]:
                line = line.strip()
                if line.isdigit():
                    pids.add(int(line))
            return pids
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            return set()

    def ReadSnapshot(self) -> GpuSnapshot:
        """Read current GPU stats. Returns zero snapshot if unavailable."""
        if not self.m_available:
            return GpuSnapshot()
        try:
            memInfo = pynvml.nvmlDeviceGetMemoryInfo(self.m_handle)
            utilization = pynvml.nvmlDeviceGetUtilizationRates(self.m_handle)

            try:
                encUtil, _ = pynvml.nvmlDeviceGetEncoderUtilization(self.m_handle)
            except pynvml.NVMLError:
                encUtil = 0
            try:
                decUtil, _ = pynvml.nvmlDeviceGetDecoderUtilization(self.m_handle)
            except pynvml.NVMLError:
                decUtil = 0

            containerPids = self._GetContainerPids()
            containerGpuMem = 0
            if containerPids:
                try:
                    procs = pynvml.nvmlDeviceGetComputeRunningProcesses(self.m_handle)
                except pynvml.NVMLError:
                    procs = []
                try:
                    graphicsProcs = pynvml.nvmlDeviceGetGraphicsRunningProcesses(self.m_handle)
                except pynvml.NVMLError:
                    graphicsProcs = []

                seenPids: Set[int] = set()
                for proc in list(procs) + list(graphicsProcs):
                    if proc.pid in containerPids and proc.pid not in seenPids:
                        seenPids.add(proc.pid)
                        if proc.usedGpuMemory is not None:
                            containerGpuMem += proc.usedGpuMemory

            return GpuSnapshot(
                gpu_mem_used_bytes=memInfo.used,
                gpu_mem_total_bytes=memInfo.total,
                gpu_utilization_pct=float(utilization.gpu),
                enc_utilization_pct=float(encUtil),
                dec_utilization_pct=float(decUtil),
                container_gpu_mem_bytes=containerGpuMem,
            )
        except pynvml.NVMLError:
            return GpuSnapshot()

    def IsAvailable(self) -> bool:
        return self.m_available
