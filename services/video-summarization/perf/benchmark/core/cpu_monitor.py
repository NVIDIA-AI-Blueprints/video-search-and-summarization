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

import csv
import json
import logging
import os
import threading
import time
from typing import Dict, List, Optional

import matplotlib.pyplot as plt
import numpy as np
import psutil

DECIMAL_PLACES = 2


def _round(value):
    return round(value, DECIMAL_PLACES)


class CPUMonitor:

    def __init__(self, per_cpu: bool = False):
        """
        Initialize CPU/RAM monitor.

        Args:
            per_cpu: If True, collect per-core CPU utilization in addition to overall.
        """
        self.logger = logging.getLogger(self.__class__.__name__)
        self.per_cpu = per_cpu
        self.num_cpus = psutil.cpu_count(logical=True)

        self._thread: Optional[threading.Thread] = None
        self._running = False

        self.elapsed_times: List[float] = []
        self.cpu_percent_data: List[float] = []
        self.ram_percent_data: List[float] = []
        self.ram_used_gb_data: List[float] = []

        # per-core data (only populated when per_cpu=True)
        self.per_cpu_data: Dict[str, List[float]] = {}
        if self.per_cpu:
            self.per_cpu_data = {f"CPU{i}": [] for i in range(self.num_cpus)}

    def __del__(self):
        self.stop_recording()

    def start_recording(self, interval_in_seconds: int = 2):
        if self._running:
            return
        self._running = True
        self.elapsed_times = []
        self.cpu_percent_data = []
        self.ram_percent_data = []
        self.ram_used_gb_data = []
        if self.per_cpu:
            self.per_cpu_data = {f"CPU{i}": [] for i in range(self.num_cpus)}

        # Prime psutil so the first real sample isn't 0
        psutil.cpu_percent(percpu=self.per_cpu)

        self._thread = threading.Thread(
            target=self._record, args=(interval_in_seconds,), daemon=True
        )
        self._thread.start()

    def _record(self, interval_in_seconds: int):
        start_time = time.time()

        while self._running:
            elapsed = time.time() - start_time
            self.elapsed_times.append(_round(elapsed))

            try:
                if self.per_cpu:
                    per_core = psutil.cpu_percent(percpu=True)
                    overall = sum(per_core) / len(per_core) if per_core else 0.0
                    for i, pct in enumerate(per_core):
                        key = f"CPU{i}"
                        if key in self.per_cpu_data:
                            self.per_cpu_data[key].append(_round(pct))
                else:
                    overall = psutil.cpu_percent()

                self.cpu_percent_data.append(_round(overall))

                mem = psutil.virtual_memory()
                self.ram_percent_data.append(_round(mem.percent))
                self.ram_used_gb_data.append(_round(mem.used / (1024**3)))
            except Exception as e:
                self.logger.error(f"Error sampling CPU/RAM metrics: {e}")
                self.cpu_percent_data.append(0.0)
                self.ram_percent_data.append(0.0)
                self.ram_used_gb_data.append(0.0)

            time.sleep(interval_in_seconds)

    def stop_recording(self):
        self._running = False
        if self._thread is not None:
            self._thread.join()
            self._thread = None

    # ------------------------------------------------------------------
    # Statistics
    # ------------------------------------------------------------------

    def get_cpu_stats(self) -> Dict[str, float]:
        stats = {"mean": 0.0, "std": 0.0, "p90": 0.0}
        if self.cpu_percent_data:
            arr = np.array(self.cpu_percent_data)
            stats["mean"] = _round(float(np.mean(arr)))
            stats["std"] = _round(float(np.std(arr)))
            stats["p90"] = _round(float(np.percentile(arr, 90)))
        if self.elapsed_times:
            stats["elapsed_time"] = {
                "total_seconds": _round(float(self.elapsed_times[-1])),
                "samples": len(self.elapsed_times),
            }
        return stats

    def get_ram_stats(self) -> Dict[str, float]:
        stats = {
            "percent": {"mean": 0.0, "std": 0.0, "p90": 0.0},
            "used_gb": {"mean": 0.0, "std": 0.0, "p90": 0.0, "max": 0.0},
        }
        if self.ram_percent_data:
            arr = np.array(self.ram_percent_data)
            stats["percent"]["mean"] = _round(float(np.mean(arr)))
            stats["percent"]["std"] = _round(float(np.std(arr)))
            stats["percent"]["p90"] = _round(float(np.percentile(arr, 90)))
        if self.ram_used_gb_data:
            arr = np.array(self.ram_used_gb_data)
            stats["used_gb"]["mean"] = _round(float(np.mean(arr)))
            stats["used_gb"]["std"] = _round(float(np.std(arr)))
            stats["used_gb"]["p90"] = _round(float(np.percentile(arr, 90)))
            stats["used_gb"]["max"] = _round(float(np.max(arr)))
        return stats

    def get_stats(self) -> Dict[str, any]:
        return {
            "cpu_stats": self.get_cpu_stats(),
            "ram_stats": self.get_ram_stats(),
        }

    # ------------------------------------------------------------------
    # Export
    # ------------------------------------------------------------------

    def export_data(
        self,
        output_dir: str,
        base_filename: str = "cpu_metrics",
        export_csv: bool = False,
        export_plots: bool = False,
    ) -> Dict[str, str]:
        os.makedirs(output_dir, exist_ok=True)
        created_files: Dict[str, str] = {}

        if not self.cpu_percent_data:
            return created_files

        # --- CSV ---
        if export_csv:
            csv_path = os.path.join(output_dir, f"{base_filename}.csv")
            fieldnames = ["elapsed_time", "cpu_percent", "ram_percent", "ram_used_gb"]
            if self.per_cpu:
                fieldnames += [f"CPU{i}" for i in range(self.num_cpus)]

            with open(csv_path, "w", newline="") as csvfile:
                writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
                writer.writeheader()
                for idx, elapsed in enumerate(self.elapsed_times):
                    if idx >= len(self.cpu_percent_data):
                        break
                    row = {
                        "elapsed_time": f"{elapsed:.2f}",
                        "cpu_percent": self.cpu_percent_data[idx],
                        "ram_percent": self.ram_percent_data[idx],
                        "ram_used_gb": self.ram_used_gb_data[idx],
                    }
                    if self.per_cpu:
                        for i in range(self.num_cpus):
                            key = f"CPU{i}"
                            if idx < len(self.per_cpu_data.get(key, [])):
                                row[key] = self.per_cpu_data[key][idx]
                    writer.writerow(row)
            created_files["cpu_csv"] = csv_path

        # --- Plots ---
        if export_plots:
            cpu_plot_path = os.path.join(output_dir, f"{base_filename}_cpu_usage.png")
            self._plot_timeseries(
                self.elapsed_times,
                {"CPU %": self.cpu_percent_data},
                "CPU Usage (%)",
                cpu_plot_path,
            )
            created_files["cpu_usage_plot"] = cpu_plot_path

            ram_plot_path = os.path.join(output_dir, f"{base_filename}_ram_usage.png")
            self._plot_timeseries(
                self.elapsed_times,
                {"RAM %": self.ram_percent_data},
                "RAM Usage (%)",
                ram_plot_path,
            )
            created_files["ram_usage_plot"] = ram_plot_path

        # --- Stats JSON ---
        stats_path = os.path.join(output_dir, f"{base_filename}_stats.json")
        with open(stats_path, "w") as f:
            json.dump(self.get_stats(), f, indent=2)
        created_files["stats_json"] = stats_path

        return created_files

    # ------------------------------------------------------------------
    # Plotting helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _plot_timeseries(elapsed_times, series_dict, ylabel, filename):
        plt.figure(figsize=(12, 6))
        for label, values in series_dict.items():
            plt.plot(elapsed_times[: len(values)], values, label=label)
        plt.xlabel("Elapsed time (seconds)")
        plt.ylabel(ylabel)
        plt.title(f"{ylabel} Over Time")
        plt.legend()
        plt.savefig(filename)
        plt.close()
