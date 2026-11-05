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

"""Role-mapped GPU and host CPU sampling for one measurement window.

Full CPU/GPU/storage/network collection is done by the shared perf library.
This only adds what that library cannot know: which GPU runs RTVI-CV and
which runs RT-VLM, so results carry cv_gpu_* and vlm_gpu_* metrics.
"""

from __future__ import annotations

import logging
import shutil
import statistics
import subprocess
import threading
import time
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


def _parse_gpu_list(value: str) -> List[int]:
    return [int(x) for x in str(value).split(",") if x.strip().isdigit()]


def _read_cpu_times() -> Optional[List[int]]:
    try:
        with open("/proc/stat", encoding="utf-8") as fh:
            return [int(x) for x in fh.readline().split()[1:]]
    except OSError:
        return None


class SystemMonitor:
    def __init__(self, cv_gpus: str, vlm_gpus: str, interval_s: float = 1.0, gpu: bool = True, cpu: bool = True):
        self.roles = {"cv": _parse_gpu_list(cv_gpus), "vlm": _parse_gpu_list(vlm_gpus)}
        self.interval_s = max(0.2, float(interval_s))
        self.gpu_enabled = gpu and shutil.which("nvidia-smi") is not None
        self.cpu_enabled = cpu
        self._util: Dict[int, List[float]] = {}
        self._mem: Dict[int, List[float]] = {}
        self._cpu: List[float] = []
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def __enter__(self) -> "SystemMonitor":
        self._thread = threading.Thread(target=self._run, name="alert-perf-sysmon", daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=10)

    def _sample_gpus(self) -> None:
        try:
            out = subprocess.run(
                ["nvidia-smi", "--query-gpu=index,utilization.gpu,memory.used",
                 "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=10, check=True,
            ).stdout
        except (subprocess.SubprocessError, OSError) as exc:
            logger.debug("nvidia-smi failed: %s", exc)
            return
        for line in out.strip().splitlines():
            parts = [p.strip() for p in line.split(",")]
            if len(parts) != 3:
                continue
            try:
                idx, util, mem = int(parts[0]), float(parts[1]), float(parts[2])
            except ValueError:
                continue  # [N/A] on unified-memory parts
            self._util.setdefault(idx, []).append(util)
            self._mem.setdefault(idx, []).append(mem)

    def _run(self) -> None:
        prev = _read_cpu_times() if self.cpu_enabled else None
        while not self._stop.wait(self.interval_s):
            if self.gpu_enabled:
                self._sample_gpus()
            if self.cpu_enabled and prev:
                cur = _read_cpu_times()
                if cur:
                    total = sum(cur) - sum(prev)
                    idle = (cur[3] + cur[4]) - (prev[3] + prev[4])
                    if total > 0:
                        self._cpu.append(100.0 * (total - idle) / total)
                    prev = cur

    def summary(self) -> Dict[str, Dict[str, float]]:
        gpu: Dict[str, float] = {}
        for role, ids in self.roles.items():
            utils = [v for i in ids for v in self._util.get(i, [])]
            mems = [v for i in ids for v in self._mem.get(i, [])]
            if utils:
                gpu[f"{role}_gpu_usage_mean"] = round(statistics.fmean(utils), 2)
                gpu[f"{role}_gpu_usage_max"] = round(max(utils), 2)
            if mems:
                gpu[f"{role}_gpu_memory_used_mb_max"] = round(max(mems), 1)
        cpu: Dict[str, float] = {}
        if self._cpu:
            cpu["cpu_usage_mean"] = round(statistics.fmean(self._cpu), 2)
            cpu["cpu_usage_max"] = round(max(self._cpu), 2)
        return {"gpu": gpu, "cpu": cpu}
