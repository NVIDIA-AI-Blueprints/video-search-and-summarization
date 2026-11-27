# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Best-effort, service-independent platform discovery."""

from __future__ import annotations

import csv
import re
import subprocess
from pathlib import Path
from typing import Any


def get_os_info() -> str:
    try:
        data = Path("/etc/os-release").read_text(encoding="utf-8")
        match = re.search(r'PRETTY_NAME="([^"]+)"', data)
        return match.group(1) if match else data.strip().splitlines()[0]
    except Exception:
        return ""


def get_cpu_info() -> dict[str, Any]:
    model, cores = "", 0
    try:
        for line in Path("/proc/cpuinfo").read_text(encoding="utf-8").splitlines():
            if line.startswith("model name") and not model:
                model = line.split(":", 1)[1].strip()
            if line.startswith("processor"):
                cores += 1
    except Exception:
        pass
    return {"model": model, "cores": cores}


def get_memory_gb() -> float:
    try:
        for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
            if line.startswith("MemTotal"):
                kb = float(line.split(":", 1)[1].strip().split()[0])
                return round(kb / (1024 * 1024), 2)
    except Exception:
        pass
    return 0.0


def get_cuda_version() -> str:
    try:
        output = subprocess.check_output(["nvidia-smi"], text=True)
        match = re.search(r"CUDA Version:\s*([0-9.]+)", output)
        return match.group(1) if match else ""
    except Exception:
        return ""


def _get_gpu_info_from_smi() -> dict[str, Any]:
    """Discover the same metadata when the optional NVML Python binding is absent."""
    try:
        output = subprocess.check_output(
            [
                "nvidia-smi",
                "--query-gpu=name,memory.total,driver_version",
                "--format=csv,noheader,nounits",
            ],
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=10,
        )
        rows = [row for row in csv.reader(output.splitlines(), skipinitialspace=True) if row]
        if not rows or any(len(row) != 3 for row in rows):
            raise ValueError("No valid GPU metadata returned")
        full_name, memory, driver = (value.strip() for value in rows[0])
        words = full_name.split()
        memory_gb = (
            None
            if memory in ("N/A", "[N/A]", "Not Supported", "[Not Supported]")
            else round(float(memory) / 1024, 2)
        )
        return {
            "model": (words[1] if len(words) > 1 else full_name).upper(),
            "full_name": full_name,
            "count": len(rows),
            "memory_gb": memory_gb,
            "driver_version": driver,
        }
    except Exception:
        return {"model": "", "full_name": "", "count": 0, "memory_gb": 0, "driver_version": ""}


def get_gpu_info() -> dict[str, Any]:
    try:
        import pynvml

        pynvml.nvmlInit()
        count = pynvml.nvmlDeviceGetCount()
        if count == 0:
            return {"model": "", "full_name": "", "count": 0, "memory_gb": 0, "driver_version": ""}
        handle = pynvml.nvmlDeviceGetHandleByIndex(0)
        full_name = pynvml.nvmlDeviceGetName(handle)
        if isinstance(full_name, bytes):
            full_name = full_name.decode("utf-8", errors="replace")
        driver = pynvml.nvmlSystemGetDriverVersion()
        if isinstance(driver, bytes):
            driver = driver.decode("utf-8", errors="replace")
        try:
            memory_gb = round(pynvml.nvmlDeviceGetMemoryInfo(handle).total / (1024**3), 2)
        except pynvml.NVMLError_NotSupported:
            memory_gb = None
        return {
            "model": (full_name.split()[1] if full_name else "").upper(),
            "full_name": full_name,
            "count": count,
            "memory_gb": memory_gb,
            "driver_version": driver,
        }
    except Exception:
        return _get_gpu_info_from_smi()


def discover_platform(config_id: str | None = None) -> dict[str, Any]:
    del config_id  # retained for compatibility with the LVS API
    gpu = get_gpu_info()
    cuda = get_cuda_version()
    if cuda:
        gpu["cuda_version"] = cuda
    return {
        "gpu": gpu,
        "cpu": get_cpu_info(),
        "memory_gb": get_memory_gb(),
        "os": get_os_info(),
    }
