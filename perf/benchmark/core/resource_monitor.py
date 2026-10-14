# SPDX-FileCopyrightText: Copyright (c) 2025-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""LVS sampler adaptation with explicit failure reporting and host I/O deltas.

CPU/RAM and NVML GPU sampling adapted from NVIDIA long-video-summarization
perf/benchmark/cpu_monitor.py and gpu_monitor.py, commit
c2c854be99674d70999c69973ff0f0e0563c00d0. Plotting and CSV export omitted.
The original zero-on-exception behavior is replaced with failed collection.
Disk and network counters extend the same psutil sampler. Host aggregate
counters include other activity and are not attributed to one container.
"""

from __future__ import annotations

import math
import statistics
import threading
import time
from typing import Any

import psutil
import pynvml

SOURCE = {
    "repository": "https://gitlab-master.nvidia.com/via/long-video-summarization",
    "commit": "c2c854be99674d70999c69973ff0f0e0563c00d0",
    "files": ["perf/benchmark/cpu_monitor.py", "perf/benchmark/gpu_monitor.py"],
    "adaptations": "sampling only; collection errors fail; psutil disk/network counter deltas",
}


class CollectionError(RuntimeError):
    """An enabled resource collector did not produce valid measurements."""


def summarize(values: list[float]) -> dict[str, float | int]:
    if not values or any(not math.isfinite(value) for value in values):
        raise CollectionError("resource samples are empty or nonfinite")
    return {
        "mean": statistics.mean(values),
        "minimum": min(values),
        "maximum": max(values),
        "sample_count": len(values),
    }


class ResourceMonitor:
    """Threaded sampling for selected physical GPUs and host resources."""

    def __init__(self, gpu_ids: str, settings: dict[str, Any]):
        self.gpu_enabled = settings.get("gpu_monitoring", {}).get("enabled", True)
        self.cpu_enabled = settings.get("cpu_monitoring", {}).get("enabled", True)
        intervals = [
            settings.get(kind, {}).get("interval_seconds", 1)
            for kind in ("gpu_monitoring", "cpu_monitoring")
        ]
        if any(
            isinstance(interval, bool)
            or not isinstance(interval, (int, float))
            or not math.isfinite(interval)
            or interval <= 0
            for interval in intervals
        ):
            raise CollectionError(
                "monitor interval_seconds must be finite and positive"
            )
        self.interval = min(intervals)
        self.samples: list[dict[str, Any]] = []
        self.errors: list[str] = []
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None
        self.nvml = None
        self.nvml_initialized = False
        self.handles = {}
        self.identities = {}
        if self.gpu_enabled:
            try:
                self.nvml = pynvml
                pynvml.nvmlInit()
                self.nvml_initialized = True
                selected = [int(item.strip()) for item in gpu_ids.split(",")]
                count = pynvml.nvmlDeviceGetCount()
                if (
                    not selected
                    or len(set(selected)) != len(selected)
                    or any(index < 0 or index >= count for index in selected)
                ):
                    raise CollectionError(
                        "selected GPU indices do not exist or contain duplicates"
                    )
                for index in selected:
                    handle = pynvml.nvmlDeviceGetHandleByIndex(index)
                    self.handles[index] = handle
                    name = pynvml.nvmlDeviceGetName(handle)
                    uuid = pynvml.nvmlDeviceGetUUID(handle)
                    self.identities[index] = {
                        "model": name.decode() if isinstance(name, bytes) else name,
                        "uuid": uuid.decode() if isinstance(uuid, bytes) else uuid,
                    }
            except (pynvml.NVMLError, CollectionError, ValueError, OSError) as exc:
                self.close()
                raise CollectionError(
                    f"GPU collection initialization failed: {exc}"
                ) from exc

    def _counters(self) -> tuple[Any, Any]:
        disk, network = psutil.disk_io_counters(), psutil.net_io_counters()
        if disk is None or network is None:
            raise CollectionError("host disk/network counters are unavailable")
        return disk, network

    def start(self) -> None:
        try:
            self.started = time.monotonic()
            self.baseline_disk, self.baseline_network = self._counters()
            self.end_disk, self.end_network = self.baseline_disk, self.baseline_network
            self.elapsed = 0.0
            # Prime in the sampler thread; psutil.cpu_percent is thread-local.
            self.thread = threading.Thread(target=self._record, daemon=True)
            self.thread.start()
        except Exception:
            self.close()
            raise

    def _record(self) -> None:
        psutil.cpu_percent()
        while not self.stop_event.wait(self.interval):
            try:
                sample: dict[str, Any] = {"gpu": {}}
                if self.cpu_enabled:
                    memory = psutil.virtual_memory()
                    sample["cpu"] = {
                        "utilization_percent": psutil.cpu_percent(),
                        "memory_used_gb": memory.used / (1024**3),
                        "memory_percent": memory.percent,
                    }
                if self.gpu_enabled:
                    for index, handle in self.handles.items():
                        utilization = self.nvml.nvmlDeviceGetUtilizationRates(handle)
                        memory = self.nvml.nvmlDeviceGetMemoryInfo(handle)
                        sample["gpu"][index] = {
                            "utilization_percent": utilization.gpu,
                            "memory_used_gb": memory.used / (1024**3),
                            "memory_percent": 100 * memory.used / memory.total,
                        }
                self.end_disk, self.end_network = self._counters()
                self.elapsed = time.monotonic() - self.started
                self.samples.append(sample)
            except (
                OSError,
                psutil.Error,
                pynvml.NVMLError,
                CollectionError,
                ZeroDivisionError,
            ) as exc:
                self.errors.append(f"resource sampling failed: {exc}")
                self.stop_event.set()

    def stop(self) -> None:
        self.stop_event.set()
        if self.thread:
            self.thread.join()
        try:
            self.end_disk, self.end_network = self._counters()
            self.elapsed = time.monotonic() - self.started
        except (OSError, psutil.Error, CollectionError) as exc:
            self.errors.append(f"final resource counters failed: {exc}")
        finally:
            self.close()

    def close(self) -> None:
        if self.nvml and self.nvml_initialized:
            try:
                self.nvml.nvmlShutdown()
            except pynvml.NVMLError as exc:
                self.errors.append(f"NVML shutdown failed: {exc}")
        self.nvml_initialized = False
        self.nvml = None

    def result(self) -> dict[str, Any]:
        if self.errors:
            raise CollectionError("; ".join(self.errors))
        if not self.samples or self.elapsed <= 0:
            raise CollectionError("no resource samples in the measurement window")
        metrics: dict[str, Any] = {
            "scope": "host aggregate and selected physical GPUs",
            "duration_seconds": self.elapsed,
            "collector_source": SOURCE,
            "cpu": {},
            "gpu": {},
        }
        if self.cpu_enabled:
            for key in ("utilization_percent", "memory_used_gb", "memory_percent"):
                metrics["cpu"][key] = summarize(
                    [row["cpu"][key] for row in self.samples]
                )
        if self.gpu_enabled:
            for index in self.handles:
                metrics["gpu"][str(index)] = {**self.identities[index]}
                for key in ("utilization_percent", "memory_used_gb", "memory_percent"):
                    metrics["gpu"][str(index)][key] = summarize(
                        [row["gpu"][index][key] for row in self.samples]
                    )
        for label, before, after, fields in (
            (
                "storage",
                self.baseline_disk,
                self.end_disk,
                ["read_bytes", "write_bytes"],
            ),
            (
                "network",
                self.baseline_network,
                self.end_network,
                ["bytes_sent", "bytes_recv"],
            ),
        ):
            metrics[label] = {}
            for field in fields:
                delta = getattr(after, field) - getattr(before, field)
                if delta < 0:
                    raise CollectionError(
                        f"{label} {field} counter reset during measurement"
                    )
                metrics[label][field] = delta
                metrics[label][field + "_per_second"] = delta / self.elapsed
        return metrics
