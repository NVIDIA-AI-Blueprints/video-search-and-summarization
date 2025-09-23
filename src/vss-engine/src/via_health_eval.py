######################################################################################################
# SPDX-FileCopyrightText: Copyright (c) 2024-2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: LicenseRef-NvidiaProprietary
#
# NVIDIA CORPORATION, its affiliates and licensors retain all intellectual
# property and proprietary rights in and to this material, related
# documentation and any modifications thereto. Any use, reproduction,
# disclosure or distribution of this material and related documentation
# without an express license agreement from NVIDIA CORPORATION or
# its affiliates is strictly prohibited.
######################################################################################################

import csv
import json
import threading
import time
from typing import Dict

import matplotlib.colors as mcolors
import matplotlib.pyplot as plt
import pynvml

try:
    pynvml.nvmlInit()
    DEVICE_COUNT = pynvml.nvmlDeviceGetCount()
    DEVICE_HANDLES = [pynvml.nvmlDeviceGetHandleByIndex(i) for i in range(DEVICE_COUNT)]
    GPU_NAMES = [pynvml.nvmlDeviceGetName(DEVICE_HANDLES[i]) for i in range(DEVICE_COUNT)]
except Exception:
    pass


class GPUMonitor:

    def __init__(self):
        self.num_gpus = DEVICE_COUNT
        self.nvdec_thread = None
        self.gpu_thread = None
        self.nvdec_running = False
        self.gpu_running = False
        self.color_list = list(mcolors.TABLEAU_COLORS.values())
        self.nvdec_plot_file_name = ""
        self.gpu_plot_file_name = ""

    def __del__(self):
        self.stop_recording_nvdec_thread()
        self.stop_recording_gpu_thread()

    def get_gpu_names(self):
        """
        Returns a list of GPU names available on the machine.

        Returns:
            list[str]: A list of GPU names (e.g. ["Tesla V100", "GeForce GTX 1080 Ti", ...])
        """
        return GPU_NAMES

    def start_recording_nvdec(
        self, interval_in_seconds: int = 5, nvdec_plot_file_name: str = "default_nvdec.csv"
    ):
        self.nvdec_plot_file_name = nvdec_plot_file_name
        self.nvdec_running = True
        self.nvdec_thread = threading.Thread(target=self._record_nvdec, args=(interval_in_seconds,))
        self.nvdec_thread.start()

    def _record_nvdec(self, interval_in_seconds: int):
        second_id = 0

        with open(self.nvdec_plot_file_name, "w", newline="") as csvfile:
            start_time = time.time()
            fieldnames = ["elapsed_time"] + [f"GPU{i}_AvgNVDEC" for i in range(self.num_gpus)]
            writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
            writer.writeheader()

            while self.nvdec_running:
                elapsed_time = time.time() - start_time
                row = {"elapsed_time": f"{elapsed_time:.2f}"}
                for i in range(self.num_gpus):
                    avg_utilization = pynvml.nvmlDeviceGetDecoderUtilization(DEVICE_HANDLES[i])[0]
                    row[f"GPU{i}_AvgNVDEC"] = avg_utilization
                writer.writerow(row)
                second_id += 1
                time.sleep(interval_in_seconds)

    def stop_recording_nvdec_thread(self):
        self.nvdec_running = False
        if self.nvdec_thread:
            self.nvdec_thread.join()
            self.nvdec_thread = None

    def stop_recording_nvdec(self, plot_graph_file: str = "plot_nvdec.png"):
        self.stop_recording_nvdec_thread()

        data = []
        with open(self.nvdec_plot_file_name, "r") as csvfile:
            reader = csv.DictReader(csvfile)
            for row in reader:
                data.append(row)

        elapsed_times = [float(row["elapsed_time"]) for row in data]
        nvdec_data = {
            key: [float(row[key]) for row in data]
            for key in data[0].keys()
            if key != "elapsed_time"
        }

        plt.figure(figsize=(12, 6))
        for gpu_id in range(self.num_gpus):
            color = self.color_list[gpu_id % len(self.color_list)]
            key = f"GPU{gpu_id}_AvgNVDEC"
            values = nvdec_data[key]
            plt.plot(elapsed_times, values, label=key, color=color)

        plt.xlabel("Second ID")
        plt.ylabel("Average NVDEC Usage (%)")
        plt.title("Average NVDEC Usage Over Time")
        plt.legend()
        plt.savefig(plot_graph_file)
        plt.close()

    def start_recording_gpu_usage(
        self, interval_in_seconds: int = 5, gpu_plot_file_name: str = "default_gpu.csv"
    ):
        self.gpu_plot_file_name = gpu_plot_file_name
        self.gpu_running = True
        self.gpu_thread = threading.Thread(
            target=self._record_gpu_usage, args=(interval_in_seconds,)
        )
        self.gpu_thread.start()

    def _record_gpu_usage(self, interval_in_seconds: int):
        second_id = 0
        start_time = time.time()

        with open(self.gpu_plot_file_name, "w", newline="") as csvfile:
            fieldnames = (
                ["elapsed_time"]
                + [f"GPU{i}_Usage" for i in range(self.num_gpus)]
                + [f"GPU{i}_MemUsage" for i in range(self.num_gpus)]
            )
            writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
            writer.writeheader()

            while self.gpu_running:
                elapsed_time = time.time() - start_time
                row = {"elapsed_time": f"{elapsed_time:.2f}"}
                for i in range(self.num_gpus):
                    handle = DEVICE_HANDLES[i]
                    utilization = pynvml.nvmlDeviceGetUtilizationRates(handle)
                    volatile_gpu_utilization = utilization.gpu
                    row[f"GPU{i}_Usage"] = volatile_gpu_utilization
                    try:
                        memory = pynvml.nvmlDeviceGetMemoryInfo(handle)
                        row[f"GPU{i}_MemUsage"] = memory.used / memory.total * 100
                    except Exception:
                        row[f"GPU{i}_MemUsage"] = 0
                writer.writerow(row)
                second_id += 1
                time.sleep(interval_in_seconds)

    def stop_recording_gpu_thread(self):
        self.gpu_running = False
        if self.gpu_thread:
            self.gpu_thread.join()
            self.gpu_thread = None

    def stop_recording_gpu(
        self,
        plot_graph_files: Dict[str, str] = {"gpu": "plot_gpu.png", "gpu_mem": "plot_gpu_mem.png"},
    ):
        self.stop_recording_gpu_thread()
        data = []
        with open(self.gpu_plot_file_name, "r") as csvfile:
            reader = csv.DictReader(csvfile)
            for row in reader:
                data.append(row)

        elapsed_times = [float(row["elapsed_time"]) for row in data]
        gpu_usage = {
            f"GPU{i}_Usage": [float(row[f"GPU{i}_Usage"]) for row in data]
            for i in range(self.num_gpus)
        }
        gpu_mem_usage = {
            f"GPU{i}_MemUsage": [float(row[f"GPU{i}_MemUsage"]) for row in data]
            for i in range(self.num_gpus)
        }

        self._plot_gpu_data(elapsed_times, gpu_usage, "GPU Usage (%)", plot_graph_files["gpu"])
        self._plot_gpu_data(
            elapsed_times, gpu_mem_usage, "GPU Memory Usage (%)", plot_graph_files["gpu_mem"]
        )

    def _plot_gpu_data(self, elapsed_times, data, ylabel, filename):
        plt.figure(figsize=(12, 6))
        for i, (key, values) in enumerate(data.items()):
            color = self.color_list[i % len(self.color_list)]
            plt.plot(elapsed_times, values, label=key, color=color)

        plt.xlabel("Elapsed time (seconds)")
        plt.ylabel(ylabel)
        plt.title(f"{ylabel} Over Time")
        plt.legend()
        plt.savefig(filename)
        plt.close()


class RequestHealthMetrics:
    def __init__(self):
        self.num_gpus = 0
        self.gpu_names = []
        self.vlm_model_name = ""
        self.vlm_batch_size = 0
        self.input_video_duration = 0
        self.chunk_size = 0
        self.chunk_overlap_duration = 0
        self.num_chunks = 0
        self.e2e_latency = 0
        self.decode_latency = 0
        self.vlm_latency = 0
        self.vlm_pipeline_latency = 0
        self.ca_rag_latency = 0
        self.all_times = []
        self.health_graph_paths = []
        self.health_graph_plot_paths = []
        self.pending_add_doc_latency = 0
        self.pending_doc_start_time = 0
        self.pending_doc_end_time = 0
        self.req_start_time = 0
        self.total_vlm_input_tokens = 0
        self.total_vlm_output_tokens = 0

    def set_gpu_names(self, gpu_names):
        self.gpu_names = gpu_names

    def dump_json(self, file_name="/tmp/default_req_health_metrics.json"):
        """
        Dumps the object's attributes to a JSON file.

        Args:
            file_name (str, optional): The file name to write to.
                                       Defaults to '/tmp/default_req_health_metrics.json'.
        """
        data = {
            "num_gpus": self.num_gpus,
            "gpu_names": self.gpu_names,
            "vlm_model_name": self.vlm_model_name,
            "vlm_batch_size": self.vlm_batch_size,
            "input_video_duration": self.input_video_duration,
            "chunk_size": self.chunk_size,
            "chunk_overlap_duration": self.chunk_overlap_duration,
            "num_chunks": self.num_chunks,
            "req_start_time": self.req_start_time,
            "e2e_latency": self.e2e_latency,
            "vlm_pipeline_latency": self.vlm_pipeline_latency,
            "ca_rag_latency": self.ca_rag_latency,
            "decode_latency": self.decode_latency,
            "vlm_latency": self.vlm_latency,
            "total_vlm_input_tokens": self.total_vlm_input_tokens,
            "total_vlm_output_tokens": self.total_vlm_output_tokens,
            "pending_add_doc_latency": self.pending_add_doc_latency,
            "pending_doc_start_time": self.pending_doc_start_time,
            "pending_doc_end_time": self.pending_doc_end_time,
            "health_graph_paths": self.health_graph_paths,
            "health_graph_plot_paths": self.health_graph_plot_paths,
            "all_times": self.all_times,
        }
        with open(file_name, "w") as f:
            json.dump(data, f, indent=4)
