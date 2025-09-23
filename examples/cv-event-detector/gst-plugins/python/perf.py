######################################################################################################
# SPDX-FileCopyrightText: Copyright (c) 2019-2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: LicenseRef-NvidiaProprietary
#
# NVIDIA CORPORATION, its affiliates and licensors retain all intellectual
# property and proprietary rights in and to this material, related
# documentation and any modifications thereto. Any use, reproduction,
# disclosure or distribution of this material and related documentation
# without an express license agreement from NVIDIA CORPORATION or
# its affiliates is strictly prohibited.
######################################################################################################

import atexit
import statistics
import time
from collections import deque
from threading import Lock, Thread

import pynvml

# Initialize NVML for GPU monitoring
try:
    pynvml.nvmlInit()
    nvml_initialized = True
except Exception as e:
    print(f"WARNING: Failed to initialize NVML: {e}")
    nvml_initialized = False


def shutdown_nvml():
    """Cleanup function to shutdown NVML properly"""
    global nvml_initialized
    if nvml_initialized:
        try:
            pynvml.nvmlShutdown()
            nvml_initialized = False
        except Exception as e:
            print(f"WARNING: Failed to shutdown NVML: {e}")


# Register the shutdown function
atexit.register(shutdown_nvml)

start_time = time.time()
fps_mutex = Lock()


class GETFPS:
    def __init__(self, stream_id):
        global start_time
        self.start_time = start_time
        self.start_time_total = start_time
        self.is_first = True
        self.frame_count = 0
        self.stream_id = stream_id
        self.frame_count_total = 0

    def update_fps(self):
        end_time = time.time()
        if self.is_first:
            self.start_time = end_time
            self.start_time_total = end_time
            self.is_first = False
        else:
            global fps_mutex
            with fps_mutex:
                self.frame_count = self.frame_count + 1
                self.frame_count_total = self.frame_count_total + 1

    def get_fps(self):
        end_time = time.time()
        with fps_mutex:
            time_diff = max(end_time - self.start_time, 0.001)  # Minimum 1ms
            time_diff_total = max(end_time - self.start_time_total, 0.001)
            stream_fps = float(self.frame_count / time_diff)
            avg_fps = float(self.frame_count_total / time_diff_total)
            self.frame_count = 0
        self.start_time = end_time
        self.start_time_total = end_time
        return round(stream_fps, 2)

    def print_data(self):
        print("frame_count=", self.frame_count)
        print("start_time=", self.start_time)


class GPUUtilizationMonitor:
    def __init__(self, gpu_id=0, sampling_interval=0.5, history_size=120):
        """
        Initialize GPU utilization monitor with background thread.

        Args:
            gpu_id: GPU device ID to monitor
            sampling_interval: How often to sample GPU metrics (in seconds)
            history_size: How many samples to keep in history (for averaging)
        """
        self.gpu_id = gpu_id
        self.sampling_interval = sampling_interval
        self.available = nvml_initialized
        self.running = False
        self.thread = None
        self.lock = Lock()

        # Data structures for statistics
        self.history_size = history_size
        self.gpu_util_history = deque(maxlen=history_size)
        self.mem_util_history = deque(maxlen=history_size)
        self.power_history = deque(maxlen=history_size)

        # Peak values
        self.peak_gpu_util = 0
        self.peak_mem_util = 0
        self.peak_power = 0

        # Current values
        self.current_metrics = {"error": "Not started yet"}

        if self.available:
            try:
                self.gpu_handle = pynvml.nvmlDeviceGetHandleByIndex(gpu_id)
            except Exception as e:
                print(f"WARNING: Failed to get GPU handle for GPU {gpu_id}: {e}")
                self.available = False

        # Register cleanup on exit
        atexit.register(self.stop)

    def _monitor_loop(self):
        """Background thread function to continuously monitor GPU metrics."""
        # wait for 5 seconds to ensure the GPU is ready
        time.sleep(2)
        while self.running:
            try:
                metrics = self._get_current_utilization()

                if "error" not in metrics:
                    with self.lock:
                        self.current_metrics = metrics

                        # Update histories
                        self.gpu_util_history.append(metrics["gpu_util"])
                        self.mem_util_history.append(metrics["mem_util"])
                        self.power_history.append(metrics["power"])

                        # Update peaks
                        self.peak_gpu_util = max(self.peak_gpu_util, metrics["gpu_util"])
                        self.peak_mem_util = max(self.peak_mem_util, metrics["mem_util"])
                        self.peak_power = max(self.peak_power, metrics["power"])
            except Exception as e:
                print(f"Error in GPU monitoring thread: {e}")

            time.sleep(self.sampling_interval)

    def _get_current_utilization(self):
        """Get current GPU utilization metrics."""
        if not self.available:
            return {"error": "NVML not initialized or GPU not available"}

        try:
            # Get GPU utilization
            util = pynvml.nvmlDeviceGetUtilizationRates(self.gpu_handle)

            # Get memory information
            mem_info = pynvml.nvmlDeviceGetMemoryInfo(self.gpu_handle)

            # Get temperature
            temp = pynvml.nvmlDeviceGetTemperature(self.gpu_handle, pynvml.NVML_TEMPERATURE_GPU)

            # Get power usage
            power_usage = pynvml.nvmlDeviceGetPowerUsage(self.gpu_handle) / 1000.0

            return {
                "gpu_id": self.gpu_id,
                "gpu_util": util.gpu,
                "mem_util": util.memory,
                "mem_used": mem_info.used / (1024**2),
                "mem_total": mem_info.total / (1024**2),
                "temp": temp,
                "power": power_usage,
            }
        except Exception as e:
            return {"error": f"Failed to get GPU metrics: {e}"}

    def start(self):
        """Start the background monitoring thread."""
        if self.running or not self.available:
            return False

        self.running = True
        self.thread = Thread(target=self._monitor_loop, daemon=True)
        self.thread.start()
        return True

    def stop(self):
        """Stop the background monitoring thread."""
        self.running = False
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=2.0)

    def get_current_metrics(self):
        """Get the most recent GPU metrics."""
        with self.lock:
            return dict(self.current_metrics)

    def get_statistics(self):
        """Get statistical metrics about GPU utilization."""
        with self.lock:
            # Calculate statistics if we have data
            stats = {
                "gpu_id": self.gpu_id,
                "samples": len(self.gpu_util_history),
                "duration": len(self.gpu_util_history) * self.sampling_interval,
            }

            if self.gpu_util_history:
                stats.update(
                    {
                        # GPU utilization statistics
                        "avg_gpu_util": statistics.mean(self.gpu_util_history),
                        "peak_gpu_util": self.peak_gpu_util,
                        # Memory utilization statistics
                        "avg_mem_util": statistics.mean(self.mem_util_history),
                        "peak_mem_util": self.peak_mem_util,
                        # Power statistics
                        "avg_power": statistics.mean(self.power_history),
                        "peak_power": self.peak_power,
                        # Current values
                        "current": self.current_metrics,
                    }
                )
            else:
                stats["error"] = "No data collected yet"

            return stats

class PERF_DATA:
    def __init__(self, num_streams=1, gpu_id=0, chunk_list=None):
        self.end = False
        self.perf_dict = {}
        self.all_stream_fps = {}
        if chunk_list is None or len(chunk_list) == 0:
            chunk_list = [0] * num_streams

        for i in range(num_streams):
            self.all_stream_fps["stream{0}-{1}".format(i, chunk_list[i])] = GETFPS(i)

        # Replace simple GPU utilization with background monitor
        self.gpu_monitor = GPUUtilizationMonitor(gpu_id)
        # Start the monitoring in background
        self.gpu_monitor.start()
        self.monitoring_start_time = time.time()

    def perf_print_callback(self):
        if not self.end:
            # Get FPS metrics
            self.perf_dict = {
                stream_index: stream.get_fps()
                for (stream_index, stream) in self.all_stream_fps.items()
            }

            # Get GPU statistics
            gpu_stats = self.gpu_monitor.get_statistics()
            current = self.gpu_monitor.get_current_metrics()

            # Print combined metrics
            print("**PERF: (FPS) ", self.perf_dict, flush=True)

            if "error" not in gpu_stats:
                monitoring_duration = time.time() - self.monitoring_start_time
                print(
                    f"**GPU[{gpu_stats['gpu_id']}] - CURRENT: "
                    f"Util={current['gpu_util']}%, "
                    f"Mem={current['mem_used']:.0f}/{current['mem_total']:.0f}MB"
                ,flush=True)

                print(
                    f"**GPU[{gpu_stats['gpu_id']}] - STATS ({monitoring_duration:.1f}s): "
                    f"Avg Util={gpu_stats['avg_gpu_util']:.1f}%, "
                    f"Peak Util={gpu_stats['peak_gpu_util']}%, "
                    f"Avg Mem={gpu_stats['avg_mem_util']:.1f}%, "
                    f"Peak Mem={gpu_stats['peak_mem_util']}%, "
                    f"Avg Power={gpu_stats['avg_power']:.1f}W, "
                    f"Peak Power={gpu_stats['peak_power']:.1f}W"
                ,flush=True)
            else:
                print(f"**GPU: {gpu_stats['error']}")

        return not self.end

    def update_fps(self, stream_index):
        self.all_stream_fps[stream_index].update_fps()

    def get_gpu_statistics(self):
        """Return the GPU statistics."""
        return self.gpu_monitor.get_statistics()

    def set_end(self):
        self.end = True
        # Stop the GPU monitoring
        self.gpu_monitor.stop()

        # Print final statistics
        final_stats = self.gpu_monitor.get_statistics()
        if "error" not in final_stats:
            total_duration = time.time() - self.monitoring_start_time
            print("\n===== FINAL GPU UTILIZATION STATISTICS =====")
            print("**PERF: (FPS) ", self.perf_dict, flush=True)
            print(f"GPU[{final_stats['gpu_id']}] monitored for {total_duration:.1f} seconds:",flush=True)
            print(f"- Average GPU utilization: {final_stats['avg_gpu_util']:.1f}%",flush=True)
            print(f"- Peak GPU utilization: {final_stats['peak_gpu_util']}%",flush=True)
            print(f"- Average memory utilization: {final_stats['avg_mem_util']:.1f}%",flush=True)
            print(f"- Peak memory utilization: {final_stats['peak_mem_util']}%",flush=True)
            print(f"- Average power consumption: {final_stats['avg_power']:.1f}W",flush=True)
            print(f"- Peak power consumption: {final_stats['peak_power']:.1f}W",flush=True)
            print("=======================================",flush=True)

        # Clean up NVML when finished
        # if nvml_initialized:
        #     try:
        #         pynvml.nvmlShutdown()
        #     except Exception as e:
        #         print(f"WARNING: Failed to shutdown NVML: {e}")
        #         pass
