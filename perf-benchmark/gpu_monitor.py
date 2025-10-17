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

import csv
import json
import logging
import os
import threading
import time
from typing import Dict, List, Optional

import matplotlib.colors as mcolors
import matplotlib.pyplot as plt
import numpy as np
import pynvml

# Constants
DECIMAL_PLACES = 2


def _round(value):
    """Round a numeric value to the standard decimal places."""
    return round(value, DECIMAL_PLACES)


try:
    pynvml.nvmlInit()
    DEVICE_COUNT = pynvml.nvmlDeviceGetCount()
    DEVICE_HANDLES = [pynvml.nvmlDeviceGetHandleByIndex(i) for i in range(DEVICE_COUNT)]
    GPU_NAMES = [pynvml.nvmlDeviceGetName(DEVICE_HANDLES[i]) for i in range(DEVICE_COUNT)]
except Exception:
    pass


class GPUMonitor:

    def __init__(self, gpu_ids: Optional[List[int]] = None):
        """
        Initialize GPU monitor

        Args:
            gpu_ids: List of specific GPU IDs to monitor. If None, all GPUs will be monitored.
        """
        self.logger = logging.getLogger(self.__class__.__name__)
        self.num_gpus = DEVICE_COUNT

        # Validate and store the specific GPU IDs to monitor
        if gpu_ids is not None:
            # Filter to only valid GPU indices
            self.monitor_gpu_ids = [i for i in gpu_ids if 0 <= i < self.num_gpus]
            if len(self.monitor_gpu_ids) == 0:
                self.logger.warning("No valid GPU IDs provided. Monitoring all GPUs.")
                self.monitor_gpu_ids = list(range(self.num_gpus))
            elif len(self.monitor_gpu_ids) != len(gpu_ids):
                self.logger.warning(
                    f"Some GPU IDs were out of range (0-{self.num_gpus-1}) and will be ignored."
                )
        else:
            # Default to all GPUs
            self.monitor_gpu_ids = list(range(self.num_gpus))

        self.nvdec_thread = None
        self.gpu_thread = None
        self.nvdec_running = False
        self.gpu_running = False
        self.color_list = list(mcolors.TABLEAU_COLORS.values())

        # In-memory storage for metrics - only for monitored GPUs
        self.nvdec_data = {f"GPU{i}_AvgNVDEC": [] for i in self.monitor_gpu_ids}
        self.gpu_usage_data = {f"GPU{i}_Usage": [] for i in self.monitor_gpu_ids}
        self.gpu_mem_data = {f"GPU{i}_MemUsage": [] for i in self.monitor_gpu_ids}

        # Separate elapsed times for each monitoring type
        self.nvdec_elapsed_times = []
        self.gpu_elapsed_times = []

        # Store GPU names for reference
        self.monitored_gpu_names = {i: GPU_NAMES[i] for i in self.monitor_gpu_ids}

    def __del__(self):
        self.stop_recording_nvdec_thread()
        self.stop_recording_gpu_thread()

    def get_gpu_names(self):
        return self.monitored_gpu_names

    def start_recording_nvdec(self, interval_in_seconds: int = 5):
        self.nvdec_running = True
        # Clear previous data but only for NVDEC
        self.nvdec_elapsed_times = []
        self.nvdec_data = {f"GPU{i}_AvgNVDEC": [] for i in self.monitor_gpu_ids}

        self.nvdec_thread = threading.Thread(target=self._record_nvdec, args=(interval_in_seconds,))
        self.nvdec_thread.start()

    def _record_nvdec(self, interval_in_seconds: int):
        second_id = 0
        start_time = time.time()

        while self.nvdec_running:
            elapsed_time = time.time() - start_time

            self.nvdec_elapsed_times.append(_round(elapsed_time))

            for i in self.monitor_gpu_ids:
                key = f"GPU{i}_AvgNVDEC"
                try:
                    avg_utilization = pynvml.nvmlDeviceGetDecoderUtilization(DEVICE_HANDLES[i])[0]
                    self.nvdec_data[key].append(_round(avg_utilization))
                except Exception as e:
                    # Handle errors during monitoring
                    self.logger.error(f"Error getting NVDEC utilization for GPU{i}: {e}")
                    self.nvdec_data[key].append(0)  # Use 0 as fallback

            second_id += 1
            time.sleep(interval_in_seconds)

    def stop_recording_nvdec_thread(self):
        self.nvdec_running = False
        if self.nvdec_thread:
            self.nvdec_thread.join()
            self.nvdec_thread = None

    def stop_recording_nvdec(self):
        self.stop_recording_nvdec_thread()

    def start_recording_gpu_usage(self, interval_in_seconds: int = 5):
        self.gpu_running = True

        # Clear previous data but only for GPU
        self.gpu_elapsed_times = []
        self.gpu_usage_data = {f"GPU{i}_Usage": [] for i in self.monitor_gpu_ids}
        self.gpu_mem_data = {f"GPU{i}_MemUsage": [] for i in self.monitor_gpu_ids}

        self.gpu_thread = threading.Thread(
            target=self._record_gpu_usage, args=(interval_in_seconds,)
        )
        self.gpu_thread.start()

    def _record_gpu_usage(self, interval_in_seconds: int):
        second_id = 0
        start_time = time.time()

        while self.gpu_running:
            elapsed_time = time.time() - start_time

            self.gpu_elapsed_times.append(_round(elapsed_time))

            for i in self.monitor_gpu_ids:
                try:
                    handle = DEVICE_HANDLES[i]
                    utilization = pynvml.nvmlDeviceGetUtilizationRates(handle)
                    memory = pynvml.nvmlDeviceGetMemoryInfo(handle)

                    volatile_gpu_utilization = utilization.gpu
                    mem_usage_pct = memory.used / memory.total * 100

                    # Store in memory
                    self.gpu_usage_data[f"GPU{i}_Usage"].append(_round(volatile_gpu_utilization))
                    self.gpu_mem_data[f"GPU{i}_MemUsage"].append(_round(mem_usage_pct))
                except Exception as e:
                    # Handle errors during monitoring
                    self.logger.error(f"Error getting GPU stats for GPU{i}: {e}")
                    self.gpu_usage_data[f"GPU{i}_Usage"].append(0)
                    self.gpu_mem_data[f"GPU{i}_MemUsage"].append(0)

            second_id += 1
            time.sleep(interval_in_seconds)

    def stop_recording_gpu_thread(self):
        self.gpu_running = False
        if self.gpu_thread:
            self.gpu_thread.join()
            self.gpu_thread = None

    def stop_recording_gpu(self):
        self.stop_recording_gpu_thread()

    def export_data(
        self,
        output_dir: str,
        base_filename: str = "gpu_metrics",
        export_csv: bool = False,
        export_plots: bool = False,
    ) -> Dict[str, str]:
        """
        Export all collected data to CSV files and/or generate plots

        Args:
            output_dir: Directory to save files
            base_filename: Base name for all generated files
            export_csv: Whether to export data to CSV files
            export_plots: Whether to generate plot images

        Returns:
            Dictionary with paths to created files
        """
        os.makedirs(output_dir, exist_ok=True)
        created_files = {}

        # Export GPU utilization and memory data
        if self.gpu_usage_data and self.monitor_gpu_ids:
            first_gpu_id = self.monitor_gpu_ids[0]
            first_gpu_key = f"GPU{first_gpu_id}_Usage"

            if len(self.gpu_usage_data.get(first_gpu_key, [])) > 0:
                # Export to CSV
                if export_csv:
                    gpu_csv_path = os.path.join(output_dir, f"{base_filename}_gpu.csv")
                    with open(gpu_csv_path, "w", newline="") as csvfile:
                        # Create fieldnames for the CSV
                        fieldnames = ["elapsed_time"]
                        for i in self.monitor_gpu_ids:
                            fieldnames.append(f"GPU{i}_Usage")
                        for i in self.monitor_gpu_ids:
                            fieldnames.append(f"GPU{i}_MemUsage")

                        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
                        writer.writeheader()

                        # Write each data point
                        for idx, elapsed_time in enumerate(self.gpu_elapsed_times):
                            if idx >= len(self.gpu_usage_data[first_gpu_key]):
                                break

                            row = {"elapsed_time": f"{elapsed_time:.2f}"}

                            # Add GPU usage data
                            for i in self.monitor_gpu_ids:
                                key = f"GPU{i}_Usage"
                                if idx < len(self.gpu_usage_data[key]):
                                    row[key] = self.gpu_usage_data[key][idx]

                            # Add GPU memory data
                            for i in self.monitor_gpu_ids:
                                key = f"GPU{i}_MemUsage"
                                if idx < len(self.gpu_mem_data[key]):
                                    row[key] = self.gpu_mem_data[key][idx]

                            writer.writerow(row)
                    created_files["gpu_csv"] = gpu_csv_path

                # Create plots
                if export_plots:
                    # GPU usage plot
                    gpu_plot_path = os.path.join(output_dir, f"{base_filename}_gpu_usage.png")
                    self._plot_gpu_data(
                        self.gpu_elapsed_times, self.gpu_usage_data, "GPU Usage (%)", gpu_plot_path
                    )
                    created_files["gpu_usage_plot"] = gpu_plot_path

                    # GPU memory plot
                    gpu_mem_plot_path = os.path.join(output_dir, f"{base_filename}_gpu_memory.png")
                    self._plot_gpu_data(
                        self.gpu_elapsed_times,
                        self.gpu_mem_data,
                        "GPU Memory Usage (%)",
                        gpu_mem_plot_path,
                    )
                    created_files["gpu_memory_plot"] = gpu_mem_plot_path

        # Export NVDEC data if available
        if self.nvdec_data and self.monitor_gpu_ids:
            first_gpu_id = self.monitor_gpu_ids[0]
            first_gpu_key = f"GPU{first_gpu_id}_AvgNVDEC"

            if len(self.nvdec_data.get(first_gpu_key, [])) > 0:
                # Export to CSV
                if export_csv:
                    nvdec_csv_path = os.path.join(output_dir, f"{base_filename}_nvdec.csv")
                    with open(nvdec_csv_path, "w", newline="") as csvfile:
                        fieldnames = ["elapsed_time"] + [
                            f"GPU{i}_AvgNVDEC" for i in self.monitor_gpu_ids
                        ]
                        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
                        writer.writeheader()

                        # Write each data point
                        for idx, elapsed_time in enumerate(self.nvdec_elapsed_times):
                            if idx >= len(self.nvdec_data[first_gpu_key]):
                                break

                            row = {"elapsed_time": f"{elapsed_time:.2f}"}

                            # Add NVDEC data
                            for i in self.monitor_gpu_ids:
                                key = f"GPU{i}_AvgNVDEC"
                                if idx < len(self.nvdec_data[key]):
                                    row[key] = self.nvdec_data[key][idx]

                            writer.writerow(row)
                    created_files["nvdec_csv"] = nvdec_csv_path

                # Create plot
                if export_plots:
                    nvdec_plot_path = os.path.join(output_dir, f"{base_filename}_nvdec.png")
                    plt.figure(figsize=(12, 6))
                    for i, gpu_id in enumerate(self.monitor_gpu_ids):
                        color = self.color_list[i % len(self.color_list)]
                        key = f"GPU{gpu_id}_AvgNVDEC"
                        values = self.nvdec_data[key]
                        plt.plot(
                            self.nvdec_elapsed_times[: len(values)], values, label=key, color=color
                        )

                    plt.xlabel("Elapsed time (seconds)")
                    plt.ylabel("Average NVDEC Usage (%)")
                    plt.title("Average NVDEC Usage Over Time")
                    plt.legend()
                    plt.savefig(nvdec_plot_path)
                    plt.close()
                    created_files["nvdec_plot"] = nvdec_plot_path

        # Export summary statistics
        stats_path = os.path.join(output_dir, f"{base_filename}_stats.json")
        stats = {"gpu_stats": {}, "nvdec_stats": self.get_nvdec_stats()}

        # Get stats for all monitored GPUs combined
        if self.monitor_gpu_ids:
            stats["gpu_stats"]["monitored"] = self.get_gpu_stats()

        # Add stats for individual GPUs
        for i in self.monitor_gpu_ids:
            gpu_stats = self.get_gpu_stats([i])
            stats["gpu_stats"][f"GPU{i}"] = gpu_stats

        with open(stats_path, "w") as f:
            json.dump(stats, f, indent=2)
        created_files["stats_json"] = stats_path

        return created_files

    def _plot_gpu_data(self, elapsed_times, data, ylabel, filename):
        plt.figure(figsize=(12, 6))
        for i, (key, values) in enumerate(data.items()):
            color = self.color_list[i % len(self.color_list)]
            plt.plot(elapsed_times[: len(values)], values, label=key, color=color)

        plt.xlabel("Elapsed time (seconds)")
        plt.ylabel(ylabel)
        plt.title(f"{ylabel} Over Time")
        plt.legend()
        plt.savefig(filename)
        plt.close()

    def get_gpu_stats(self, gpu_indices: Optional[List[int]] = None) -> Dict[str, Dict[str, float]]:
        """
        Get statistics for GPU usage and memory usage.

        Args:
            gpu_indices: Optional list of GPU indices to include in statistics.
                         If None, includes all monitored GPUs.

        Returns:
            Dict with keys 'usage' and 'memory', each containing a Dict with
            'mean', 'std', 'p90' for the specified GPUs.
        """
        if gpu_indices is None:
            # Use all monitored GPUs if no specific indices provided
            gpu_indices = self.monitor_gpu_ids
        else:
            # Filter to only those GPUs that are both in the monitored set and requested
            gpu_indices = [i for i in gpu_indices if i in self.monitor_gpu_ids]

        stats = {
            "usage": {"mean": 0.0, "std": 0.0, "p90": 0.0},
            "memory": {"mean": 0.0, "std": 0.0, "p90": 0.0},
        }

        # If no valid GPU indices, return empty stats
        if not gpu_indices:
            return stats

        # Collect all values for the specified GPUs
        all_usage = []
        all_memory = []

        for idx in gpu_indices:
            gpu_key = f"GPU{idx}_Usage"
            mem_key = f"GPU{idx}_MemUsage"

            if gpu_key in self.gpu_usage_data and self.gpu_usage_data[gpu_key]:
                all_usage.extend(self.gpu_usage_data[gpu_key])

            if mem_key in self.gpu_mem_data and self.gpu_mem_data[mem_key]:
                all_memory.extend(self.gpu_mem_data[mem_key])

        # Calculate statistics if we have data
        if all_usage:
            stats["usage"]["mean"] = _round(float(np.mean(all_usage)))
            stats["usage"]["std"] = _round(float(np.std(all_usage)))
            stats["usage"]["p90"] = _round(float(np.percentile(all_usage, 90)))

        if all_memory:
            stats["memory"]["mean"] = _round(float(np.mean(all_memory)))
            stats["memory"]["std"] = _round(float(np.std(all_memory)))
            stats["memory"]["p90"] = _round(float(np.percentile(all_memory, 90)))

        # Add elapsed time stats
        if self.gpu_elapsed_times:
            stats["elapsed_time"] = {
                "total_seconds": _round(
                    float(self.gpu_elapsed_times[-1] if self.gpu_elapsed_times else 0)
                ),
                "samples": len(self.gpu_elapsed_times),
            }

        return stats

    def get_gpu_usage_mean(self, gpu_indices: Optional[List[int]] = None) -> float:
        """Get mean GPU usage for specified GPUs"""
        stats = self.get_gpu_stats(gpu_indices)
        return stats["usage"]["mean"]

    def get_gpu_memory_mean(self, gpu_indices: Optional[List[int]] = None) -> float:
        """Get mean GPU memory usage for specified GPUs"""
        stats = self.get_gpu_stats(gpu_indices)
        return stats["memory"]["mean"]

    def get_nvdec_stats(self, gpu_indices: Optional[List[int]] = None) -> Dict[str, float]:
        """
        Get statistics for NVDEC usage.

        Args:
            gpu_indices: Optional list of GPU indices to include in statistics.
                         If None, includes all monitored GPUs.

        Returns:
            Dict with 'mean', 'std', 'p90' for NVDEC usage of specified GPUs.
        """
        if gpu_indices is None:
            # Use all monitored GPUs if no specific indices provided
            gpu_indices = self.monitor_gpu_ids
        else:
            # Filter to only those GPUs that are both in the monitored set and requested
            gpu_indices = [i for i in gpu_indices if i in self.monitor_gpu_ids]

        stats = {"mean": 0.0, "std": 0.0, "p90": 0.0}

        # If no valid GPU indices, return empty stats
        if not gpu_indices:
            return stats

        # Collect all values for the specified GPUs
        all_nvdec = []

        for idx in gpu_indices:
            nvdec_key = f"GPU{idx}_AvgNVDEC"

            if nvdec_key in self.nvdec_data and self.nvdec_data[nvdec_key]:
                all_nvdec.extend(self.nvdec_data[nvdec_key])

        # Calculate statistics if we have data
        if all_nvdec:
            stats["mean"] = _round(float(np.mean(all_nvdec)))
            stats["std"] = _round(float(np.std(all_nvdec)))
            stats["p90"] = _round(float(np.percentile(all_nvdec, 90)))

        # Add elapsed time stats
        if self.nvdec_elapsed_times:
            stats["elapsed_time"] = {
                "total_seconds": _round(
                    float(self.nvdec_elapsed_times[-1] if self.nvdec_elapsed_times else 0)
                ),
                "samples": len(self.nvdec_elapsed_times),
            }

        return stats
