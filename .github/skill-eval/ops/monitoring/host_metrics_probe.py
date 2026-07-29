#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Emit one JSON snapshot of CPU, RAM, root disk, load, and uptime."""

from __future__ import annotations

import json
import os
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path

PROBE_VERSION = 6
MAX_FUTURE_SKEW_SEC = 60


def cpu_times() -> tuple[int, int]:
    fields = [
        int(value)
        for value in Path("/proc/stat")
        .read_text(encoding="utf-8")
        .splitlines()[0]
        .split()[1:]
    ]
    idle = fields[3] + (fields[4] if len(fields) > 4 else 0)
    return sum(fields), idle


def unit_property(unit: str, property_name: str) -> str:
    process = subprocess.run(
        ["systemctl", "show", unit, f"--property={property_name}", "--value"],
        capture_output=True,
        check=False,
        text=True,
        timeout=3,
    )
    if process.returncode != 0:
        return "not-found"
    return process.stdout.strip() or "unknown"


def unit_state(unit: str, property_name: str) -> str:
    if unit_property(unit, "LoadState") != "loaded":
        return "not-installed"
    return unit_property(unit, property_name)


def run_output(
    command: list[str],
    timeout: int = 8,
    *,
    include_stderr: bool = False,
) -> str:
    process = subprocess.run(
        command,
        capture_output=True,
        check=False,
        text=True,
        timeout=timeout,
    )
    if process.returncode != 0:
        raise RuntimeError((process.stderr or process.stdout).strip()[-300:])
    if include_stderr:
        return process.stdout + process.stderr
    return process.stdout


def timestamp_age(path: str) -> int | None:
    try:
        value = run_output(["sudo", "-n", "cat", path], timeout=3).strip()
        timestamp = datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
    except (OSError, RuntimeError, subprocess.SubprocessError, ValueError):
        return None
    age = int((datetime.now(UTC) - timestamp).total_seconds())
    if age < -MAX_FUTURE_SKEW_SEC:
        return None
    return max(0, age)


def patroni_health() -> dict[str, int | str | None]:
    if unit_state("vss-postgres-ha.service", "ActiveState") != "active":
        return {
            "patroni_cluster": "not-installed",
            "patroni_leaders": None,
            "patroni_sync_standbys": None,
        }
    try:
        members = json.loads(
            run_output(
                [
                    "sudo",
                    "-n",
                    "-u",
                    "postgres",
                    "patronictl",
                    "-c",
                    "/etc/vss-postgres-ha/patroni.yml",
                    "list",
                    "--format",
                    "json",
                ]
            )
        )
        leaders = sum(member.get("Role") == "Leader" for member in members)
        sync_standbys = sum(member.get("Role") == "Sync Standby" for member in members)
        states_ok = all(
            member.get("State") in {"running", "streaming"} for member in members
        )
        healthy = (
            len(members) == 3 and leaders == 1 and sync_standbys >= 1 and states_ok
        )
        return {
            "patroni_cluster": "healthy" if healthy else "unhealthy",
            "patroni_leaders": leaders,
            "patroni_sync_standbys": sync_standbys,
        }
    except (OSError, RuntimeError, subprocess.SubprocessError, ValueError):
        return {
            "patroni_cluster": "unhealthy",
            "patroni_leaders": 0,
            "patroni_sync_standbys": 0,
        }


def etcd_health() -> dict[str, int | str | None]:
    if unit_state("etcd.service", "ActiveState") != "active":
        return {"etcd_quorum": "not-installed", "etcd_healthy_endpoints": None}
    try:
        output = run_output(
            [
                "sudo",
                "-n",
                "env",
                "ETCDCTL_API=3",
                "etcdctl",
                "--endpoints=https://10.203.142.1:2379,https://10.203.142.2:2379,https://10.203.142.3:2379",
                "--cacert=/etc/vss-postgres-ha/ca.crt",
                "--cert=/etc/vss-postgres-ha/patroni-etcd-client.crt",
                "--key=/etc/vss-postgres-ha/patroni-etcd-client.key",
                "endpoint",
                "health",
                "--cluster",
            ],
            include_stderr=True,
        )
        healthy_endpoints = sum(" is healthy" in line for line in output.splitlines())
        return {
            "etcd_quorum": "healthy" if healthy_endpoints == 3 else "unhealthy",
            "etcd_healthy_endpoints": healthy_endpoints,
        }
    except (OSError, RuntimeError, subprocess.SubprocessError):
        return {"etcd_quorum": "unhealthy", "etcd_healthy_endpoints": 0}


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
services = {
    "postgres_ha": unit_state("vss-postgres-ha.service", "ActiveState"),
    "etcd": unit_state("etcd.service", "ActiveState"),
    "backup_timer": unit_state("vss-postgres-ha-backup.timer", "ActiveState"),
    "restore_test_timer": unit_state(
        "vss-postgres-ha-restore-test.timer",
        "ActiveState",
    ),
    "backup_result": unit_state("vss-postgres-ha-backup.service", "Result"),
    "restore_test_result": unit_state(
        "vss-postgres-ha-restore-test.service",
        "Result",
    ),
    "backup_age_seconds": timestamp_age(
        "/var/backups/vss-postgres-ha/logical/last-success"
    ),
    "restore_test_age_seconds": timestamp_age(
        "/var/backups/vss-postgres-ha/logical/last-restore-test"
    ),
    **patroni_health(),
    **etcd_health(),
}

print(
    json.dumps(
        {
            "probe_version": PROBE_VERSION,
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
            "uptime_seconds": int(
                float(Path("/proc/uptime").read_text(encoding="utf-8").split()[0])
            ),
            "services": services,
            "collected_at": int(time.time()),
        },
        separators=(",", ":"),
    )
)
