######################################################################################################
# SPDX-FileCopyrightText: Copyright (c) 2025-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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
######################################################################################################

"""
Node Exporter CPU and system metrics collector for RTVI performance benchmarks.

Collects CPU utilization and memory metrics by scraping Node Exporter.
Runs alongside DCGM (GPU) and legacy pynvml monitoring. Data is stored
in Excel reports and printed to console.

Node Exporter metrics (Prometheus format):
- node_cpu_seconds_total{cpu="N",mode="idle|user|system|..."} - CPU time per mode
- node_memory_MemAvailable_bytes - Available memory
- node_memory_MemTotal_bytes - Total memory
"""

import logging
import threading
import time
from typing import Any, Dict, Optional

import numpy as np
import requests

NODE_CPU_SECONDS_TOTAL = "node_cpu_seconds_total"
NODE_MEMORY_AVAILABLE = "node_memory_MemAvailable_bytes"
NODE_MEMORY_TOTAL = "node_memory_MemTotal_bytes"
NODE_LOAD1 = "node_load1"
NODE_LOAD5 = "node_load5"


def _parse_prometheus_metrics(text: str) -> Dict[str, Dict[str, float]]:
    """
    Parse Prometheus exposition format into {metric_name: {labels_key: value}}.
    """
    result = {}
    for line in text.split("\n"):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            if "{" in line:
                idx = line.index("{")
                name = line[:idx].strip()
                rest = line[idx:]
                brace_end = rest.index("}")
                labels_part = rest[1:brace_end]
                value_part = rest[brace_end + 1 :].strip().split()[0] or "0"
            else:
                parts = line.rsplit(" ", 1)
                if len(parts) != 2:
                    continue
                name = parts[0].strip()
                value_part = (parts[1].strip().split()[0] or "0") if parts[1].strip() else "0"
                labels_part = ""

            try:
                value = float(value_part)
            except ValueError:
                continue

            # Build key from labels (e.g. cpu="0",mode="idle" -> "cpu_0_mode_idle")
            key_parts = []
            if labels_part:
                for kv in labels_part.split(","):
                    kv = kv.strip()
                    if "=" in kv:
                        k, v = kv.split("=", 1)
                        v_clean = v.strip().strip('"')
                        key_parts.append(f"{k.strip()}_{v_clean}")
            key = "_".join(key_parts) if key_parts else "default"

            if name not in result:
                result[name] = {}
            result[name][key] = value
        except (ValueError, IndexError):
            continue
    return result


class NodeExporterCollector:
    """
    Collects CPU and memory metrics from Node Exporter by scraping at intervals.
    """

    def __init__(
        self,
        node_exporter_url: str,
        session: Optional[requests.Session] = None,
    ):
        """
        Args:
            node_exporter_url: URL to Node Exporter /metrics (e.g. http://localhost:9101/metrics)
            session: Optional requests Session
        """
        self.logger = logging.getLogger(self.__class__.__name__)
        url = node_exporter_url.rstrip("/")
        if not url.endswith("/metrics"):
            url = url.replace("/metrics", "") + "/metrics"
        self.node_exporter_url = url
        self.session = session or requests.Session()
        self.session.headers["Accept"] = "text/plain"

        self._running = False
        self._thread = None
        self._lock = threading.Lock()

        self._cpu_idle_samples = []  # (timestamp, idle_sum) per sample
        self._cpu_total_samples = []  # (timestamp, total_sum) per sample
        self._cpu_util_samples = []  # Computed utilization % between samples
        self._mem_available_samples = []
        self._mem_total_samples = []
        self._load1_samples = []
        self._load5_samples = []
        self._elapsed_times = []
        self._start_time = 0.0

    def start(self, interval_seconds: float = 2.0):
        """Start collecting in a background thread."""
        if self._running:
            self.logger.warning("Node Exporter collector already running")
            return
        self._running = True
        self._cpu_idle_samples = []
        self._cpu_total_samples = []
        self._cpu_util_samples = []
        self._mem_available_samples = []
        self._mem_total_samples = []
        self._load1_samples = []
        self._load5_samples = []
        self._elapsed_times = []
        self._start_time = time.time()

        self._thread = threading.Thread(
            target=self._collect_loop,
            args=(interval_seconds,),
            daemon=True,
        )
        self._thread.start()
        self.logger.debug(
            f"Node Exporter collector started ({self.node_exporter_url}, "
            f"interval={interval_seconds}s)"
        )

    def _collect_loop(self, interval_seconds: float):
        """Background loop that scrapes Node Exporter."""
        prev_idle = None
        prev_total = None
        prev_ts = None

        while self._running:
            try:
                resp = self.session.get(self.node_exporter_url, timeout=10)
                resp.raise_for_status()
                parsed = _parse_prometheus_metrics(resp.text)

                ts = time.time()
                elapsed = ts - self._start_time

                with self._lock:
                    self._elapsed_times.append(round(elapsed, 2))

                    # CPU: sum idle and total across all cpus and modes
                    cpu_metrics = parsed.get(NODE_CPU_SECONDS_TOTAL, {})
                    idle_sum = sum(
                        v for k, v in cpu_metrics.items() if "_idle" in k or "mode_idle" in k
                    )
                    total_sum = sum(cpu_metrics.values())

                    if prev_idle is not None and prev_total is not None and prev_ts is not None:
                        ts_delta = ts - prev_ts
                        total_delta = total_sum - prev_total
                        if ts_delta > 0 and total_delta >= 0:
                            idle_delta = idle_sum - prev_idle
                            util_pct = (
                                100.0 * (1.0 - idle_delta / total_delta) if total_delta > 0 else 0
                            )
                            util_pct = max(0, min(100, util_pct))
                            self._cpu_util_samples.append(util_pct)

                    prev_idle = idle_sum
                    prev_total = total_sum
                    prev_ts = ts
                    self._cpu_idle_samples.append(idle_sum)
                    self._cpu_total_samples.append(total_sum)

                    # Memory (gauges - direct values)
                    mem_avail = parsed.get(NODE_MEMORY_AVAILABLE, {})
                    mem_total_metric = parsed.get(NODE_MEMORY_TOTAL, {})
                    mem_avail_val = list(mem_avail.values())[0] if mem_avail else 0
                    mem_total_val = list(mem_total_metric.values())[0] if mem_total_metric else 1
                    if mem_total_val > 0:
                        self._mem_available_samples.append(mem_avail_val)
                        self._mem_total_samples.append(mem_total_val)

                    # Load average (gauges)
                    load1 = parsed.get(NODE_LOAD1, {})
                    load5 = parsed.get(NODE_LOAD5, {})
                    if load1:
                        self._load1_samples.append(list(load1.values())[0])
                    if load5:
                        self._load5_samples.append(list(load5.values())[0])

            except requests.RequestException as e:
                self.logger.warning(f"Failed to scrape Node Exporter: {e}")
            except Exception as e:
                self.logger.warning(f"Node Exporter collector error: {e}")

            for _ in range(max(1, int(interval_seconds * 10))):
                if not self._running:
                    return
                time.sleep(0.1)

    def reset(self):
        """Clear accumulated samples without stopping collection.
        Stats computed after this will only cover samples collected from this point on."""
        with self._lock:
            self._cpu_idle_samples = []
            self._cpu_total_samples = []
            self._cpu_util_samples = []
            self._mem_available_samples = []
            self._mem_total_samples = []
            self._load1_samples = []
            self._load5_samples = []
            self._elapsed_times = []
            self._start_time = time.time()

    def stop(self) -> Dict[str, Any]:
        """Stop collecting and return aggregated stats."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)
            self._thread = None

        with self._lock:
            return self._compute_stats()

    def get_stats(self) -> Dict[str, Any]:
        """Return aggregated stats for samples collected so far without stopping."""
        with self._lock:
            return self._compute_stats()

    def _compute_stats(self) -> Dict[str, Any]:
        """Compute mean, p90 for CPU and memory."""
        result = {}

        if self._cpu_util_samples:
            result["nodeexporter_cpu_util_mean"] = round(float(np.mean(self._cpu_util_samples)), 2)
            result["nodeexporter_cpu_util_p90"] = round(
                float(np.percentile(self._cpu_util_samples, 90)), 2
            )

        if self._mem_available_samples and self._mem_total_samples:
            n = min(len(self._mem_available_samples), len(self._mem_total_samples))
            mem_used_pct = [
                100.0 * (1.0 - self._mem_available_samples[i] / self._mem_total_samples[i])
                for i in range(n)
                if self._mem_total_samples[i] > 0
            ]
            if mem_used_pct:
                result["nodeexporter_memory_used_pct_mean"] = round(float(np.mean(mem_used_pct)), 2)
                result["nodeexporter_memory_used_pct_p90"] = round(
                    float(np.percentile(mem_used_pct, 90)), 2
                )

        if self._load1_samples:
            result["nodeexporter_load1_mean"] = round(float(np.mean(self._load1_samples)), 2)
        if self._load5_samples:
            result["nodeexporter_load5_mean"] = round(float(np.mean(self._load5_samples)), 2)

        return result

    def get_raw_data(self) -> Dict[str, Any]:
        """Return raw collected data."""
        with self._lock:
            return {
                "cpu_util_samples": list(self._cpu_util_samples),
                "mem_available_samples": list(self._mem_available_samples),
                "mem_total_samples": list(self._mem_total_samples),
                "load1_samples": list(self._load1_samples),
                "load5_samples": list(self._load5_samples),
                "elapsed_times": list(self._elapsed_times),
            }
