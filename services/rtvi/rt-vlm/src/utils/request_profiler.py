# SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

import atexit
import csv
import json
import os
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from typing import Any

import pynvml

from common.logger import logger

_DEFAULT_SAMPLE_INTERVAL_SECONDS = 0.2
_DEFAULT_MAX_SAMPLES = 18_000
_DEFAULT_MAX_PENDING_EXPORTS = 16


def _positive_int_env(name: str, default: int) -> int:
    try:
        value = int(os.environ.get(name, default))
        if value <= 0:
            raise ValueError
        return value
    except ValueError:
        logger.warning("Invalid %s; using %d", name, default)
        return default


def _positive_float_env(name: str, default: float) -> float:
    try:
        value = float(os.environ.get(name, default))
        if value <= 0:
            raise ValueError
        return value
    except ValueError:
        logger.warning("Invalid %s; using %.2f", name, default)
        return default


@dataclass(frozen=True)
class GPUSample:
    timestamp: float
    nvdec_usage: tuple[float, ...]
    gpu_usage: tuple[float, ...]
    gpu_memory_usage: tuple[float, ...]


class ProcessGPUSampler:
    """Collect one bounded stream of GPU samples for the whole process."""

    def __init__(
        self,
        interval_seconds: float = _DEFAULT_SAMPLE_INTERVAL_SECONDS,
        max_samples: int = _DEFAULT_MAX_SAMPLES,
    ) -> None:
        if interval_seconds <= 0:
            raise ValueError("interval_seconds must be positive")
        if max_samples <= 0:
            raise ValueError("max_samples must be positive")
        self.interval_seconds = interval_seconds
        self._max_samples = max_samples
        self._samples = deque(maxlen=max_samples)
        self._captures: dict[int, deque[GPUSample]] = {}
        self._next_capture_id = 0
        self._samples_lock = threading.Lock()
        self._lifecycle_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread = None
        self._handles = []
        self._gpu_names = []

    def start(self) -> None:
        with self._lifecycle_lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._initialize_nvml()
            self._stop_event.clear()
            self._thread = threading.Thread(
                target=self._sample_loop,
                name="rtvi-process-gpu-sampler",
                daemon=True,
            )
            self._thread.start()

    def _initialize_nvml(self) -> None:
        if self._handles:
            return
        try:
            pynvml.nvmlInit()
            device_count = pynvml.nvmlDeviceGetCount()
            self._handles = [pynvml.nvmlDeviceGetHandleByIndex(i) for i in range(device_count)]
            self._gpu_names = [pynvml.nvmlDeviceGetName(handle) for handle in self._handles]
        except Exception:
            logger.warning("GPU profiling is unavailable because NVML initialization failed")
            self._handles = []
            self._gpu_names = []

    def _sample_loop(self) -> None:
        while not self._stop_event.is_set():
            self._sample_once()
            self._stop_event.wait(self.interval_seconds)

    def _sample_once(self) -> None:
        nvdec_usage = []
        gpu_usage = []
        gpu_memory_usage = []
        for handle in self._handles:
            try:
                nvdec_usage.append(float(pynvml.nvmlDeviceGetDecoderUtilization(handle)[0]))
            except Exception:
                nvdec_usage.append(0.0)
            try:
                gpu_usage.append(float(pynvml.nvmlDeviceGetUtilizationRates(handle).gpu))
            except Exception:
                gpu_usage.append(0.0)
            try:
                memory = pynvml.nvmlDeviceGetMemoryInfo(handle)
                gpu_memory_usage.append(float(memory.used / memory.total * 100))
            except Exception:
                gpu_memory_usage.append(0.0)

        sample = GPUSample(
            timestamp=time.time(),
            nvdec_usage=tuple(nvdec_usage),
            gpu_usage=tuple(gpu_usage),
            gpu_memory_usage=tuple(gpu_memory_usage),
        )
        with self._samples_lock:
            self._samples.append(sample)
            for capture in self._captures.values():
                capture.append(sample)

    def begin_capture(self) -> int:
        """Start a bounded request-local view without adding a sampler thread."""
        with self._samples_lock:
            capture_id = self._next_capture_id
            self._next_capture_id += 1
            self._captures[capture_id] = deque(maxlen=self._max_samples)
            return capture_id

    def end_capture(self, capture_id: int) -> deque[GPUSample]:
        """Detach a capture in O(1); the exporter owns it from this point."""
        with self._samples_lock:
            return self._captures.pop(capture_id, deque())

    def snapshot(self, start_time: float, end_time: float) -> list[GPUSample]:
        with self._samples_lock:
            return [
                sample for sample in self._samples if start_time <= sample.timestamp <= end_time
            ]

    def get_gpu_names(self) -> list[str]:
        return list(self._gpu_names)

    def stop(self) -> None:
        with self._lifecycle_lock:
            thread = self._thread
            if thread is None:
                return
            self._stop_event.set()
            self._thread = None
        thread.join(timeout=max(1.0, self.interval_seconds * 2))


@dataclass(frozen=True)
class ProfileExportJob:
    request_id: str
    sample_start_time: float
    sample_end_time: float
    metrics: dict[str, Any]
    output_dir: str = "/tmp/rtvi-logs"
    capture_id: int | None = None
    samples: deque[GPUSample] | tuple[GPUSample, ...] | None = None


class RequestProfileExporter:
    """Export request profiles without blocking request completion."""

    def __init__(
        self,
        sampler: ProcessGPUSampler | None = None,
        max_pending_exports: int = _DEFAULT_MAX_PENDING_EXPORTS,
    ) -> None:
        if max_pending_exports <= 0:
            raise ValueError("max_pending_exports must be positive")
        self._sampler = sampler
        self._executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="rtvi-profile-export",
        )
        self._slots = threading.BoundedSemaphore(max_pending_exports)
        self.max_workers = 1

    def submit(self, job: ProfileExportJob) -> bool:
        if self._sampler is not None and job.capture_id is not None and job.samples is None:
            # Detaching the request-owned deque is O(1). CSV/plot conversion
            # remains entirely on the background worker, while queued jobs can
            # no longer lose samples to process-ring eviction.
            job = replace(job, samples=self._sampler.end_capture(job.capture_id))
        if not self._slots.acquire(blocking=False):
            return False
        try:
            future = self._executor.submit(self._export, job)
        except Exception:
            self._slots.release()
            raise
        future.add_done_callback(lambda _future: self._slots.release())
        return True

    def _export(self, job: ProfileExportJob) -> None:
        try:
            os.makedirs(job.output_dir, exist_ok=True)
            samples = (
                [
                    sample
                    for sample in job.samples
                    if job.sample_start_time <= sample.timestamp <= job.sample_end_time
                ]
                if job.samples is not None
                else []
            )
            paths = _profile_paths(job.output_dir, job.request_id)
            _write_nvdec_csv(paths["nvdec_csv"], samples, job.sample_start_time)
            _write_gpu_csv(paths["gpu_csv"], samples, job.sample_start_time)
            _write_plots(paths, samples, job.sample_start_time)
            with open(paths["metrics_json"], "w") as metrics_file:
                json.dump(job.metrics, metrics_file, indent=4)
            logger.info("Request Metrics Summary written to %s", paths["metrics_json"])
        except Exception:
            logger.exception("Failed to export request profile %s", job.request_id)

    def shutdown(self) -> None:
        self._executor.shutdown(wait=True)


def _profile_paths(output_dir: str, request_id: str) -> dict[str, str]:
    return {
        "nvdec_csv": os.path.join(output_dir, f"nvdec_usage_{request_id}.csv"),
        "gpu_csv": os.path.join(output_dir, f"gpu_usage_{request_id}.csv"),
        "nvdec_plot": os.path.join(output_dir, f"plot_nvdec_{request_id}.png"),
        "gpu_plot": os.path.join(output_dir, f"plot_gpu_{request_id}.png"),
        "gpu_mem_plot": os.path.join(output_dir, f"plot_gpu_mem_{request_id}.png"),
        "metrics_json": os.path.join(output_dir, f"request_metrics_{request_id}.json"),
    }


def _write_nvdec_csv(path: str, samples: list[GPUSample], start_time: float) -> None:
    num_gpus = len(samples[0].nvdec_usage) if samples else 0
    fieldnames = ["elapsed_time"] + [f"GPU{i}_AvgNVDEC" for i in range(num_gpus)]
    with open(path, "w", newline="") as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
        writer.writeheader()
        for sample in samples:
            row = {"elapsed_time": f"{sample.timestamp - start_time:.2f}"}
            row.update({f"GPU{i}_AvgNVDEC": value for i, value in enumerate(sample.nvdec_usage)})
            writer.writerow(row)


def _write_gpu_csv(path: str, samples: list[GPUSample], start_time: float) -> None:
    num_gpus = len(samples[0].gpu_usage) if samples else 0
    fieldnames = (
        ["elapsed_time"]
        + [f"GPU{i}_Usage" for i in range(num_gpus)]
        + [f"GPU{i}_MemUsage" for i in range(num_gpus)]
    )
    with open(path, "w", newline="") as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
        writer.writeheader()
        for sample in samples:
            row = {"elapsed_time": f"{sample.timestamp - start_time:.2f}"}
            row.update({f"GPU{i}_Usage": value for i, value in enumerate(sample.gpu_usage)})
            row.update(
                {f"GPU{i}_MemUsage": value for i, value in enumerate(sample.gpu_memory_usage)}
            )
            writer.writerow(row)


def _write_plots(paths: dict[str, str], samples: list[GPUSample], start_time: float) -> None:
    if not samples:
        return
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure

    elapsed_times = [sample.timestamp - start_time for sample in samples]

    def plot(values, labels, ylabel, path):
        figure = Figure(figsize=(12, 6))
        axes = figure.subplots()
        for label, series in zip(labels, values):
            axes.plot(elapsed_times, series, label=label)
        axes.set_xlabel("Elapsed time (seconds)")
        axes.set_ylabel(ylabel)
        axes.set_title(f"{ylabel} Over Time")
        if values:
            axes.legend()
        FigureCanvasAgg(figure).print_png(path)

    num_gpus = len(samples[0].gpu_usage)
    plot(
        [[sample.nvdec_usage[i] for sample in samples] for i in range(num_gpus)],
        [f"GPU{i}_AvgNVDEC" for i in range(num_gpus)],
        "Average NVDEC Usage (%)",
        paths["nvdec_plot"],
    )
    plot(
        [[sample.gpu_usage[i] for sample in samples] for i in range(num_gpus)],
        [f"GPU{i}_Usage" for i in range(num_gpus)],
        "GPU Usage (%)",
        paths["gpu_plot"],
    )
    plot(
        [[sample.gpu_memory_usage[i] for sample in samples] for i in range(num_gpus)],
        [f"GPU{i}_MemUsage" for i in range(num_gpus)],
        "GPU Memory Usage (%)",
        paths["gpu_mem_plot"],
    )


class RequestMetrics:
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
        self.all_times = []
        self.resource_usage_graph_paths = []
        self.resource_usage_graph_plot_paths = []
        self.req_start_time = 0
        self.total_vlm_input_tokens = 0
        self.total_vlm_output_tokens = 0

    def set_gpu_names(self, gpu_names):
        self.gpu_names = gpu_names

    def to_dict(self) -> dict[str, Any]:
        return dict(vars(self))

    def dump_json(self, file_name):
        with open(file_name, "w") as output_file:
            json.dump(self.to_dict(), output_file, indent=4)


_PROCESS_SAMPLER = None
_PROFILE_EXPORTER = None
_SINGLETON_LOCK = threading.Lock()


def get_process_gpu_sampler() -> ProcessGPUSampler:
    global _PROCESS_SAMPLER
    with _SINGLETON_LOCK:
        if _PROCESS_SAMPLER is None:
            interval = _positive_float_env(
                "RTVI_PROFILE_GPU_SAMPLE_INTERVAL_SECONDS",
                _DEFAULT_SAMPLE_INTERVAL_SECONDS,
            )
            max_samples = _positive_int_env(
                "RTVI_PROFILE_MAX_GPU_SAMPLES",
                _DEFAULT_MAX_SAMPLES,
            )
            _PROCESS_SAMPLER = ProcessGPUSampler(interval, max_samples)
        _PROCESS_SAMPLER.start()
        return _PROCESS_SAMPLER


def get_request_profile_exporter(sampler: ProcessGPUSampler) -> RequestProfileExporter:
    global _PROFILE_EXPORTER
    with _SINGLETON_LOCK:
        if _PROFILE_EXPORTER is None:
            max_pending = _positive_int_env(
                "RTVI_PROFILE_MAX_PENDING_EXPORTS",
                _DEFAULT_MAX_PENDING_EXPORTS,
            )
            _PROFILE_EXPORTER = RequestProfileExporter(sampler, max_pending)
        return _PROFILE_EXPORTER


def _shutdown_profiling() -> None:
    if _PROFILE_EXPORTER is not None:
        _PROFILE_EXPORTER.shutdown()
    if _PROCESS_SAMPLER is not None:
        _PROCESS_SAMPLER.stop()


atexit.register(_shutdown_profiling)
