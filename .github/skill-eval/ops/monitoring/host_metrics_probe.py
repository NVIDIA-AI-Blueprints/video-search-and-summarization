#!/usr/bin/env python3
"""Emit one JSON snapshot of CPU, RAM, root disk, load, and uptime."""
from __future__ import annotations

import json
import os
import time


def cpu_times() -> tuple[int, int]:
    fields = [int(value) for value in open("/proc/stat").readline().split()[1:]]
    idle = fields[3] + (fields[4] if len(fields) > 4 else 0)
    return sum(fields), idle


total_1, idle_1 = cpu_times()
time.sleep(0.25)
total_2, idle_2 = cpu_times()
delta_total = max(total_2 - total_1, 1)
cpu_percent = 100.0 * (1.0 - ((idle_2 - idle_1) / delta_total))

meminfo = {}
with open("/proc/meminfo") as stream:
    for line in stream:
        key, value = line.split(":", 1)
        meminfo[key] = int(value.strip().split()[0]) * 1024
mem_total = meminfo["MemTotal"]
mem_available = meminfo.get("MemAvailable", meminfo.get("MemFree", 0))

disk = os.statvfs("/")
disk_total = disk.f_blocks * disk.f_frsize
disk_free = disk.f_bavail * disk.f_frsize

print(
    json.dumps(
        {
            "cpu_percent": round(cpu_percent, 2),
            "cpu_count": os.cpu_count(),
            "ram_total": mem_total,
            "ram_used": mem_total - mem_available,
            "ram_percent": round(100.0 * (mem_total - mem_available) / mem_total, 2),
            "disk_total": disk_total,
            "disk_used": disk_total - disk_free,
            "disk_free": disk_free,
            "disk_percent": round(100.0 * (disk_total - disk_free) / disk_total, 2),
            "load_1m": round(os.getloadavg()[0], 2),
            "uptime_seconds": int(float(open("/proc/uptime").read().split()[0])),
            "collected_at": int(time.time()),
        },
        separators=(",", ":"),
    )
)
