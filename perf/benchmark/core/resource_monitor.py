# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Host counters and selected GPU samples, using the LVS psutil/NVML approach."""

import statistics
import time

import psutil
import pynvml


def stats(values):
    return {
        "mean": statistics.mean(values),
        "minimum": min(values),
        "maximum": max(values),
        "sample_count": len(values),
    }


class ResourceMonitor:
    def __init__(self, gpu_ids):
        pynvml.nvmlInit()
        self.handles = {
            i: pynvml.nvmlDeviceGetHandleByIndex(int(i)) for i in gpu_ids.split(",")
        }
        self.identities = {}
        for i, handle in self.handles.items():
            self.identities[i] = {
                "model": pynvml.nvmlDeviceGetName(handle),
                "uuid": pynvml.nvmlDeviceGetUUID(handle),
            }
        self.samples = []

    def start(self):
        self.started = time.monotonic()
        self.disk, self.network = psutil.disk_io_counters(), psutil.net_io_counters()
        psutil.cpu_percent()

    def sample(self):
        memory = psutil.virtual_memory()
        cpu = {
            "utilization_percent": psutil.cpu_percent(),
            "memory_used_gb": memory.used / 1024**3,
            "memory_percent": memory.percent,
        }
        gpu = {}
        for i, handle in self.handles.items():
            usage = pynvml.nvmlDeviceGetUtilizationRates(handle)
            memory = pynvml.nvmlDeviceGetMemoryInfo(handle)
            gpu[i] = {
                "utilization_percent": usage.gpu,
                "memory_used_gb": memory.used / 1024**3,
                "memory_percent": 100 * memory.used / memory.total,
            }
        self.samples.append((cpu, gpu))

    def close(self):
        pynvml.nvmlShutdown()

    def result(self):
        if not self.samples:
            raise RuntimeError("resource collection failed: no samples")
        elapsed = time.monotonic() - self.started
        cpu = {
            key: stats([row[key] for row, _ in self.samples])
            for key in self.samples[0][0]
        }
        gpu = {}
        for i in self.handles:
            values = {
                key: stats([row[i][key] for _, row in self.samples])
                for key in self.samples[0][1][i]
            }
            gpu[i] = {**self.identities[i], **values}
        result = {
            "scope": "host aggregate and selected physical GPUs",
            "duration_seconds": elapsed,
            "cpu": cpu,
            "gpu": gpu,
        }
        for label, before, after, fields in (
            (
                "storage",
                self.disk,
                psutil.disk_io_counters(),
                ("read_bytes", "write_bytes"),
            ),
            (
                "network",
                self.network,
                psutil.net_io_counters(),
                ("bytes_sent", "bytes_recv"),
            ),
        ):
            if before is None or after is None:
                raise RuntimeError(f"{label} counters unavailable")
            result[label] = {}
            for field in fields:
                delta = getattr(after, field) - getattr(before, field)
                if delta < 0:
                    raise RuntimeError(f"{label} counter reset")
                result[label].update(
                    {field: delta, field + "_per_second": delta / elapsed}
                )
        return result
