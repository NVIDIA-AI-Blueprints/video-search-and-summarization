#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Best-effort CPU and memory sampling for one skill-eval invocation.

Only fixed numeric fields from Linux's procfs accounting interfaces are read.
Process lists, command lines, environments, file contents, and per-process
metrics are deliberately excluded from the collection boundary.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import shlex
import time
from pathlib import Path

import gpu_trace


INTERVAL_SEC = gpu_trace._int_env("EVAL_SYSTEM_TRACE_INTERVAL", 10)
MAX_FETCH_BYTES = gpu_trace._int_env("EVAL_SYSTEM_TRACE_MAX_BYTES", 8 * 1024 * 1024)
ENABLED = os.environ.get("EVAL_SYSTEM_TRACE", "1") not in ("0", "false", "False", "")
HARD_STOP_SLACK_SEC = 600

CSV_HEADER = (
    "timestamp_ns,cpu_count,cpu_user_pct,cpu_system_pct,cpu_iowait_pct,"
    "cpu_idle_pct,cpu_steal_pct,load_1m,load_5m,load_15m,mem_used_mib,"
    "mem_available_mib,mem_total_mib,swap_used_mib,swap_total_mib"
)
ROW_RE = re.compile(
    r"^[1-9][0-9]{15,19}(?:,-?\d+(?:\.\d+)?){14}$"
)
DIR_RE = re.compile(r"^/tmp/vss-systrace\.[A-Za-z0-9]{8}$")

# This constant is passed directly to ``python3 -c``. It reads only aggregate
# numeric counters from three fixed kernel interfaces and writes a fixed-width
# numeric CSV. No job-controlled value is interpolated into it.
SAMPLER = r"""
import os, sys, time

out = open(sys.argv[1], "w", buffering=1)
previous = None

def cpu():
    cells = open("/proc/stat", encoding="ascii").readline().split()
    values = [int(v) for v in cells[1:9]]
    return values

def memory():
    wanted = {"MemTotal", "MemAvailable", "SwapTotal", "SwapFree"}
    values = {}
    with open("/proc/meminfo", encoding="ascii") as handle:
        for line in handle:
            key, _, rest = line.partition(":")
            if key in wanted:
                values[key] = int(rest.split()[0])
    return values

while True:
    now = time.time_ns()
    current = cpu()
    mem = memory()
    load = open("/proc/loadavg", encoding="ascii").readline().split()[:3]
    percentages = [0.0] * 5
    if previous is not None:
        delta = [max(0, a - b) for a, b in zip(current, previous)]
        total = sum(delta) or 1
        user = delta[0] + delta[1]
        system = delta[2] + delta[5] + delta[6]
        percentages = [
            100.0 * user / total,
            100.0 * system / total,
            100.0 * delta[4] / total,
            100.0 * delta[3] / total,
            100.0 * delta[7] / total,
        ]
    previous = current
    total_mib = mem.get("MemTotal", 0) / 1024.0
    available_mib = mem.get("MemAvailable", 0) / 1024.0
    swap_total_mib = mem.get("SwapTotal", 0) / 1024.0
    swap_free_mib = mem.get("SwapFree", 0) / 1024.0
    row = [
        now, os.cpu_count() or 0, *percentages, *[float(v) for v in load],
        max(0.0, total_mib - available_mib), available_mib, total_mib,
        max(0.0, swap_total_mib - swap_free_mib), swap_total_mib,
    ]
    out.write(",".join(str(round(v, 3)) if isinstance(v, float) else str(v)
                       for v in row) + "\n")
    time.sleep(%INTERVAL%)
""".replace("%INTERVAL%", str(INTERVAL_SEC))


@contextlib.contextmanager
def trace(
    instance: str,
    results_root: Path,
    *,
    spec_stem: str = "",
    platform: str = "",
    step: int = 1,
    chain: str = "",
    skill: str = "",
    harbor_timeout_sec: int = 7800,
):
    """Sample aggregate CPU and memory counters without ever failing the leg."""
    if not ENABLED or not instance:
        yield
        return

    run_id = os.environ.get("GITHUB_RUN_ID", "local")
    token = gpu_trace._token(run_id, spec_stem, platform, step, chain)
    started_at = time.time()
    hard_stop = max(harbor_timeout_sec + HARD_STOP_SLACK_SEC, 900)
    pid: str | None = None
    remote_dir: str | None = None
    script = shlex.quote(SAMPLER)
    start_cmd = (
        "find /tmp -maxdepth 1 -name 'vss-systrace.*' -mmin +180 "
        "-exec rm -rf {} + 2>/dev/null; "
        "set -C; d=$(mktemp -d /tmp/vss-systrace.XXXXXXXX) || exit 0; "
        "echo DIR=$d; "
        f"nohup timeout -k 10 {hard_stop} python3 -c {script} \"$d/trace.csv\" "
        ">/dev/null 2>&1 </dev/null & echo PID=$!"
    )
    try:
        out = gpu_trace._remote(instance, start_cmd, attempts=1)
        for line in (out or "").splitlines():
            if line.startswith("PID="):
                candidate = line.split("=", 1)[1].strip()
                pid = candidate if gpu_trace.PID_RE.match(candidate) else None
            elif line.startswith("DIR="):
                candidate = line.split("=", 1)[1].strip()
                remote_dir = candidate if DIR_RE.match(candidate) else None
        if not (pid and remote_dir):
            print(f"[system-trace] could not start on {instance}; leg continues",
                  flush=True)
    except Exception as exc:  # noqa: BLE001 - telemetry is never load-bearing
        print(f"[system-trace] start failed ({exc!r}); leg continues", flush=True)

    try:
        yield
    finally:
        with contextlib.suppress(Exception):
            _finish(
                instance, remote_dir, pid, results_root, token,
                run_id=run_id, skill=skill, spec_stem=spec_stem,
                platform=platform, step=step, chain=chain,
                started_at=started_at,
            )


def _finish(
    instance: str,
    remote_dir: str | None,
    pid: str | None,
    results_root: Path,
    token: str,
    **meta,
) -> None:
    """Stop, validate, and persist aggregate samples. Best effort."""
    if pid and not remote_dir:
        gpu_trace._remote(
            instance,
            f"ps -o args= -p {pid} 2>/dev/null | grep -qF vss-systrace "
            f"&& kill {pid} 2>/dev/null; true",
            attempts=1,
        )
        return
    if not remote_dir:
        return

    kill = (
        f"ps -o args= -p {pid} 2>/dev/null | grep -qF -- {remote_dir} "
        f"&& kill {pid} 2>/dev/null; "
    ) if pid else ""
    out = gpu_trace._remote(
        instance,
        f"{kill}sleep 1; head -c {MAX_FETCH_BYTES} "
        f"{remote_dir}/trace.csv 2>/dev/null; rm -rf {remote_dir}",
    )
    rows = [line.strip() for line in (out or "").splitlines()
            if ROW_RE.match(line.strip())]
    if not rows:
        return

    dest = results_root / "systemtrace"
    dest.mkdir(parents=True, exist_ok=True)
    (dest / f"{token}.csv").write_text(
        CSV_HEADER + "\n" + "\n".join(rows) + "\n", encoding="utf-8"
    )
    sidecar = {
        "schema": 1,
        "instance": instance,
        "run_id": meta.get("run_id"),
        "skill": meta.get("skill"),
        "spec_stem": meta.get("spec_stem"),
        "platform": meta.get("platform"),
        "step": meta.get("step"),
        "interval_sec": INTERVAL_SEC,
        "started_at": meta.get("started_at"),
        "finished_at": time.time(),
        "samples": len(rows),
    }
    (dest / f"{token}.json").write_text(
        json.dumps(sidecar, indent=2) + "\n", encoding="utf-8"
    )
    print(f"[system-trace] {len(rows)} samples -> {dest / (token + '.csv')}",
          flush=True)
