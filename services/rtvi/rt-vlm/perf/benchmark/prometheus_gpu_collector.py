######################################################################################################
# SPDX-FileCopyrightText: Copyright (c) 2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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
Prometheus/DCGM GPU metrics collector for RTVI performance benchmarks.

Collects GPU metrics by scraping DCGM Exporter. Metrics align with
perf/dcgm/dcgm-metrics-config.csv. Runs alongside legacy pynvml-based GPUMonitor.
Data is stored in Excel reports and printed to console.

DCGM metrics (from dcgm-metrics-config.csv):
- DCGM_FI_DEV_GPU_UTIL - GPU utilization (%)
- DCGM_FI_DEV_FB_USED_PERCENT - Framebuffer memory used (%)
- DCGM_FI_DEV_FB_USED, DCGM_FI_DEV_FB_FREE - Fallback for memory %
- DCGM_FI_DEV_DEC_UTIL - Decoder/NVDEC utilization (%)
- DCGM_FI_DEV_ENC_UTIL - Encoder utilization (%)
- DCGM_FI_DEV_MEM_COPY_UTIL - Memory copy utilization (%)
- DCGM_FI_DEV_POWER_USAGE - Power draw (W)
- DCGM_FI_DEV_GPU_TEMP, DCGM_FI_DEV_MEMORY_TEMP - Temperatures (C)
"""

import json
import logging
import os
import threading
import time
from typing import Any, Dict, List, Optional

import numpy as np
import requests

# DCGM metric names (from perf/dcgm/dcgm-metrics-config.csv)
DCGM_GPU_UTIL = "DCGM_FI_DEV_GPU_UTIL"
DCGM_FB_USED_PERCENT = "DCGM_FI_DEV_FB_USED_PERCENT"
DCGM_FB_USED = "DCGM_FI_DEV_FB_USED"
DCGM_FB_FREE = "DCGM_FI_DEV_FB_FREE"
DCGM_DEC_UTIL = "DCGM_FI_DEV_DEC_UTIL"
DCGM_ENC_UTIL = "DCGM_FI_DEV_ENC_UTIL"
DCGM_MEM_COPY_UTIL = "DCGM_FI_DEV_MEM_COPY_UTIL"
DCGM_POWER_USAGE = "DCGM_FI_DEV_POWER_USAGE"
DCGM_GPU_TEMP = "DCGM_FI_DEV_GPU_TEMP"
DCGM_MEMORY_TEMP = "DCGM_FI_DEV_MEMORY_TEMP"


def _parse_prometheus_metrics(text: str) -> Dict[str, Dict[str, float]]:
    """
    Parse Prometheus exposition format text into a nested dict.
    Returns: {metric_name: {labels_str: value}}
    """
    result = {}
    for line in text.split("\n"):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            # Format: metric_name{label="value",...} value
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

            # Extract gpu label for grouping
            gpu_id = ""
            if labels_part:
                for kv in labels_part.split(","):
                    kv = kv.strip()
                    if kv.startswith('gpu="') or kv.startswith("gpu='"):
                        gpu_id = kv.split("=", 1)[1].strip("'\"")
                        break

            key = f"gpu_{gpu_id}" if gpu_id else "default"
            if name not in result:
                result[name] = {}
            result[name][key] = value
        except (ValueError, IndexError):
            continue
    return result


class PrometheusGPUCollector:
    """
    Collects GPU and NVDEC metrics from DCGM Exporter (and optionally Prometheus)
    by scraping at configurable intervals. Compatible with legacy GPUMonitor output.
    """

    def __init__(
        self,
        dcgm_exporter_url: str,
        gpu_ids: Optional[List[int]] = None,
        session: Optional[requests.Session] = None,
    ):
        """
        Initialize the Prometheus/DCGM GPU collector.

        Args:
            dcgm_exporter_url: URL to DCGM Exporter /metrics endpoint
                (e.g. http://localhost:9400/metrics)
            gpu_ids: List of GPU indices to monitor (e.g. [0] for VLM GPU).
                     If None, all GPUs from metrics are used.
            session: Optional requests Session for HTTP calls
        """
        self.logger = logging.getLogger(self.__class__.__name__)
        self.dcgm_exporter_url = dcgm_exporter_url.rstrip("/").replace("/metrics", "") + "/metrics"
        self.gpu_ids = gpu_ids
        self.session = session or requests.Session()
        self.session.headers["Accept"] = "text/plain"

        self._running = False
        self._thread = None
        self._lock = threading.Lock()

        # Storage: {metric: {gpu_key: [values]}}
        self._gpu_util = {}
        self._mem_used_pct = {}
        self._mem_used = {}
        self._mem_free = {}
        self._dec_util = {}
        self._elapsed_times = []
        self._start_time = 0.0

    def start(self, interval_seconds: float = 2.0):
        """Start collecting metrics in a background thread."""
        if self._running:
            self.logger.warning("Prometheus GPU collector already running")
            return
        self._running = True
        self._gpu_util = {}
        self._mem_used_pct = {}
        self._mem_used = {}
        self._mem_free = {}
        self._dec_util = {}
        self._enc_util = {}
        self._mem_copy_util = {}
        self._power_usage = {}
        self._gpu_temp = {}
        self._memory_temp = {}
        self._elapsed_times = []
        self._start_time = time.time()

        self._thread = threading.Thread(
            target=self._collect_loop,
            args=(interval_seconds,),
            daemon=True,
        )
        self._thread.start()
        self.logger.debug(
            f"Prometheus GPU collector started (DCGM: {self.dcgm_exporter_url}, "
            f"interval={interval_seconds}s)"
        )

    def _collect_loop(self, interval_seconds: float):
        """Background loop that scrapes DCGM Exporter at regular intervals."""
        while self._running:
            try:
                resp = self.session.get(self.dcgm_exporter_url, timeout=10)
                resp.raise_for_status()
                parsed = _parse_prometheus_metrics(resp.text)

                elapsed = time.time() - self._start_time
                with self._lock:
                    self._elapsed_times.append(round(elapsed, 2))

                    for gpu_key, val in parsed.get(DCGM_GPU_UTIL, {}).items():
                        if gpu_key not in self._gpu_util:
                            self._gpu_util[gpu_key] = []
                        self._gpu_util[gpu_key].append(val)

                    # Memory: prefer DCGM_FI_DEV_FB_USED_PERCENT, else compute from FB_USED/(FB_USED+FB_FREE)
                    for gpu_key, val in parsed.get(DCGM_FB_USED_PERCENT, {}).items():
                        if gpu_key not in self._mem_used_pct:
                            self._mem_used_pct[gpu_key] = []
                        self._mem_used_pct[gpu_key].append(val)
                    for gpu_key, val in parsed.get(DCGM_FB_USED, {}).items():
                        if gpu_key not in self._mem_used:
                            self._mem_used[gpu_key] = []
                        self._mem_used[gpu_key].append(val)
                    for gpu_key, val in parsed.get(DCGM_FB_FREE, {}).items():
                        if gpu_key not in self._mem_free:
                            self._mem_free[gpu_key] = []
                        self._mem_free[gpu_key].append(val)

                    for gpu_key, val in parsed.get(DCGM_DEC_UTIL, {}).items():
                        if gpu_key not in self._dec_util:
                            self._dec_util[gpu_key] = []
                        self._dec_util[gpu_key].append(val)

                    for gpu_key, val in parsed.get(DCGM_ENC_UTIL, {}).items():
                        if gpu_key not in self._enc_util:
                            self._enc_util[gpu_key] = []
                        self._enc_util[gpu_key].append(val)

                    for gpu_key, val in parsed.get(DCGM_MEM_COPY_UTIL, {}).items():
                        if gpu_key not in self._mem_copy_util:
                            self._mem_copy_util[gpu_key] = []
                        self._mem_copy_util[gpu_key].append(val)

                    for gpu_key, val in parsed.get(DCGM_POWER_USAGE, {}).items():
                        if gpu_key not in self._power_usage:
                            self._power_usage[gpu_key] = []
                        self._power_usage[gpu_key].append(val)

                    for gpu_key, val in parsed.get(DCGM_GPU_TEMP, {}).items():
                        if gpu_key not in self._gpu_temp:
                            self._gpu_temp[gpu_key] = []
                        self._gpu_temp[gpu_key].append(val)

                    for gpu_key, val in parsed.get(DCGM_MEMORY_TEMP, {}).items():
                        if gpu_key not in self._memory_temp:
                            self._memory_temp[gpu_key] = []
                        self._memory_temp[gpu_key].append(val)
            except requests.RequestException as e:
                self.logger.warning(f"Failed to scrape DCGM exporter: {e}")
            except Exception as e:
                self.logger.warning(f"Prometheus collector error: {e}")

            # Sleep in small increments to allow quick shutdown
            for _ in range(max(1, int(interval_seconds * 10))):
                if not self._running:
                    return
                time.sleep(0.1)

    def reset(self):
        """Clear accumulated samples without stopping collection.
        Stats computed after this will only cover samples collected from this point on."""
        with self._lock:
            self._gpu_util = {}
            self._mem_used_pct = {}
            self._mem_used = {}
            self._mem_free = {}
            self._dec_util = {}
            self._enc_util = {}
            self._mem_copy_util = {}
            self._power_usage = {}
            self._gpu_temp = {}
            self._memory_temp = {}
            self._elapsed_times = []
            self._start_time = time.time()

    def get_latest_stats(self) -> Dict[str, float]:
        """Return the latest sampled GPU and NVDEC utilization for monitored GPUs."""
        with self._lock:
            gpu_indices = self.gpu_ids
            if gpu_indices is None:
                gpu_indices = []
                for key in set(self._gpu_util) | set(self._dec_util):
                    try:
                        gpu_indices.append(int(key.replace("gpu_", "")))
                    except ValueError:
                        continue
                gpu_indices = sorted(set(gpu_indices)) if gpu_indices else [0]

            latest_gpu_util = []
            latest_dec_util = []
            for gpu_id in gpu_indices:
                gpu_key = f"gpu_{gpu_id}"
                if self._gpu_util.get(gpu_key):
                    latest_gpu_util.append(self._gpu_util[gpu_key][-1])
                if self._dec_util.get(gpu_key):
                    latest_dec_util.append(self._dec_util[gpu_key][-1])

            stats = {}
            if latest_gpu_util:
                stats["gpu_usage"] = round(float(np.mean(latest_gpu_util)), 2)
            if latest_dec_util:
                stats["nvdec_usage"] = round(float(np.mean(latest_dec_util)), 2)
            return stats

    def stop(self) -> Dict[str, Any]:
        """
        Stop collecting and return aggregated stats compatible with legacy format.
        Returns dict with prometheus_vlm_gpu_usage_mean, prometheus_vlm_gpu_memory_mean,
        prometheus_vlm_gpu_usage_p90, prometheus_vlm_nvdec_usage_mean, etc.
        """
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
        """Compute mean, p90 etc. for monitored GPUs (metrics from dcgm-metrics-config.csv)."""
        result = {}
        gpu_indices = self.gpu_ids
        if gpu_indices is None:
            gpu_indices = []
            for key in self._gpu_util.keys():
                try:
                    idx = int(key.replace("gpu_", ""))
                    gpu_indices.append(idx)
                except ValueError:
                    gpu_indices.append(0)
            gpu_indices = sorted(set(gpu_indices)) if gpu_indices else [0]

        all_usage = []
        all_memory = []
        all_nvdec = []
        all_enc = []
        all_mem_copy = []
        all_power = []
        all_gpu_temp = []
        all_mem_temp = []

        for gpu_id in gpu_indices:
            gpu_key = f"gpu_{gpu_id}"
            if gpu_key in self._gpu_util and self._gpu_util[gpu_key]:
                all_usage.extend(self._gpu_util[gpu_key])
            # Memory: DCGM_FI_DEV_FB_USED_PERCENT or compute from FB_USED/(FB_USED+FB_FREE)
            if gpu_key in self._mem_used_pct and self._mem_used_pct[gpu_key]:
                all_memory.extend(self._mem_used_pct[gpu_key])
            elif gpu_key in self._mem_used and gpu_key in self._mem_free:
                used = self._mem_used[gpu_key]
                free = self._mem_free[gpu_key]
                n = min(len(used), len(free))
                if n > 0:
                    total = [used[i] + free[i] for i in range(n)]
                    mem_pct = [100.0 * used[i] / total[i] if total[i] > 0 else 0 for i in range(n)]
                    all_memory.extend(mem_pct)
            if gpu_key in self._dec_util and self._dec_util[gpu_key]:
                all_nvdec.extend(self._dec_util[gpu_key])
            if gpu_key in self._enc_util and self._enc_util[gpu_key]:
                all_enc.extend(self._enc_util[gpu_key])
            if gpu_key in self._mem_copy_util and self._mem_copy_util[gpu_key]:
                all_mem_copy.extend(self._mem_copy_util[gpu_key])
            if gpu_key in self._power_usage and self._power_usage[gpu_key]:
                all_power.extend(self._power_usage[gpu_key])
            if gpu_key in self._gpu_temp and self._gpu_temp[gpu_key]:
                all_gpu_temp.extend(self._gpu_temp[gpu_key])
            if gpu_key in self._memory_temp and self._memory_temp[gpu_key]:
                all_mem_temp.extend(self._memory_temp[gpu_key])

        if all_usage:
            result["prometheus_vlm_gpu_usage_mean"] = round(float(np.mean(all_usage)), 2)
            result["prometheus_vlm_gpu_usage_p90"] = round(float(np.percentile(all_usage, 90)), 2)
        if all_memory:
            result["prometheus_vlm_gpu_memory_mean"] = round(float(np.mean(all_memory)), 2)
            result["prometheus_vlm_gpu_memory_p90"] = round(float(np.percentile(all_memory, 90)), 2)
        if all_nvdec:
            result["prometheus_vlm_nvdec_usage_mean"] = round(float(np.mean(all_nvdec)), 2)
            result["prometheus_vlm_nvdec_usage_std"] = round(float(np.std(all_nvdec)), 2)
            result["prometheus_vlm_nvdec_usage_p90"] = round(float(np.percentile(all_nvdec, 90)), 2)
        if all_enc:
            result["prometheus_vlm_enc_usage_mean"] = round(float(np.mean(all_enc)), 2)
            result["prometheus_vlm_enc_usage_p90"] = round(float(np.percentile(all_enc, 90)), 2)
        if all_mem_copy:
            result["prometheus_vlm_mem_copy_usage_mean"] = round(float(np.mean(all_mem_copy)), 2)
            result["prometheus_vlm_mem_copy_usage_p90"] = round(
                float(np.percentile(all_mem_copy, 90)), 2
            )
        if all_power:
            result["prometheus_vlm_power_mean_watts"] = round(float(np.mean(all_power)), 2)
            result["prometheus_vlm_power_p90_watts"] = round(float(np.percentile(all_power, 90)), 2)
        if all_gpu_temp:
            result["prometheus_vlm_gpu_temp_mean_c"] = round(float(np.mean(all_gpu_temp)), 2)
            result["prometheus_vlm_gpu_temp_p90_c"] = round(
                float(np.percentile(all_gpu_temp, 90)), 2
            )
        if all_mem_temp:
            result["prometheus_vlm_memory_temp_mean_c"] = round(float(np.mean(all_mem_temp)), 2)
            result["prometheus_vlm_memory_temp_p90_c"] = round(
                float(np.percentile(all_mem_temp, 90)), 2
            )

        return result

    def get_raw_data(self) -> Dict[str, Any]:
        """Return raw collected data for export/storage."""
        with self._lock:
            return {
                "gpu_util": {k: list(v) for k, v in self._gpu_util.items()},
                "mem_used_pct": {k: list(v) for k, v in self._mem_used_pct.items()},
                "mem_used": {k: list(v) for k, v in self._mem_used.items()},
                "mem_free": {k: list(v) for k, v in self._mem_free.items()},
                "dec_util": {k: list(v) for k, v in self._dec_util.items()},
                "enc_util": {k: list(v) for k, v in self._enc_util.items()},
                "mem_copy_util": {k: list(v) for k, v in self._mem_copy_util.items()},
                "power_usage": {k: list(v) for k, v in self._power_usage.items()},
                "gpu_temp": {k: list(v) for k, v in self._gpu_temp.items()},
                "memory_temp": {k: list(v) for k, v in self._memory_temp.items()},
                "elapsed_times": list(self._elapsed_times),
            }

    def export_data(self, output_dir: str, base_filename: str = "prometheus_gpu_metrics") -> str:
        """
        Export collected data and stats to JSON. Returns path to stats file.
        """
        os.makedirs(output_dir, exist_ok=True)
        stats = self._compute_stats()
        raw = self.get_raw_data()
        payload = {"stats": stats, "raw": raw}
        stats_path = os.path.join(output_dir, f"{base_filename}_stats.json")
        with open(stats_path, "w") as f:
            json.dump(payload, f, indent=2)
        return stats_path
