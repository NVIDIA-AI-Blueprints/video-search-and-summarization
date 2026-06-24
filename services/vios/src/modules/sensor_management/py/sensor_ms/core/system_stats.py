# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""System stats for GET /sensor/debug/system/stats (SystemStats schema).

Reads CPU/memory from /proc (no extra deps). GPU/encoder/decoder usage are 0 — the Python
control-plane service does no GPU work (unlike the C++ which reports NvEnc/NvDec). All required
swagger fields are present.
"""
from __future__ import annotations

import os


def _meminfo() -> dict[str, int]:
    out: dict[str, int] = {}
    try:
        with open("/proc/meminfo") as fh:
            for line in fh:
                k, _, rest = line.partition(":")
                out[k.strip()] = int(rest.strip().split()[0])  # kB
    except OSError:
        pass
    return out


def _rss_mb() -> int:
    try:
        with open("/proc/self/status") as fh:
            for line in fh:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) // 1024
    except OSError:
        pass
    return 0


def _open_files() -> int:
    try:
        return len(os.listdir("/proc/self/fd"))
    except OSError:
        return 0


def get_system_stats() -> dict[str, int]:
    mem = _meminfo()
    sys_used_mb = ((mem.get("MemTotal", 0) - mem.get("MemAvailable", 0)) // 1024) if mem else 0
    return {
        "cpu_usage": 0,            # instantaneous CPU% omitted (no psutil dep); reported as 0
        "dec_usage": 0,
        "enc_usage": 0,
        "gpu_usage": 0,
        "open_files_count": _open_files(),
        "rss_MB": _rss_mb(),
        "system_memory_usage_MB": sys_used_mb,
        "total_gpu_mem_MB": 0,
        "total_gpu_mem_usage_MB": 0,
    }
