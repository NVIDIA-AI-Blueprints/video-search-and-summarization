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

"""Explicit run indexes and descriptive resource sample summaries.

These projections neither establish effective model settings nor qualify a
comparison. Resource p90 is a sample quantile, never request latency p90.
"""

import math
import statistics

INDEX_FIELDS = (
    "index_schema_version",
    "application_type",
    "vlm_model",
    "vlm_model_status",
    "backend_type",
    "storage_backend",
    "frames_per_chunk",
    "frames_per_chunk_status",
    "chunk_duration_s",
    "chunk_duration_status",
    "concurrent_streams",
    "concurrent_streams_status",
    "observed_concurrent_streams",
)


def workflow_index(profile):
    replay = profile.captioning == "replay"
    streams = getattr(profile, "concurrent_streams", 1)
    if streams == 1 and profile.barrier_directory is not None:
        streams = 2
    return dict(
        zip(
            INDEX_FIELDS,
            (
                "benchmark_run_index.1",
                profile.arm,
                None if replay else profile.vlm_model,
                "not_executed_replay"
                if replay
                else "declared_requires_runtime_witness",
                profile.captioning,
                profile.store_backend,
                None
                if replay
                else profile.caption_settings()[
                    "num_frames_per_second_or_fixed_frames_chunk"
                ],
                "not_executed_replay" if replay else "requested_not_verified_delivery",
                None if replay else profile.chunk_duration_s,
                "not_executed_replay" if replay else "requested",
                streams,
                "worker_barrier_target_not_verified_overlap"
                if profile.barrier_directory is not None
                else "single_workflow_target",
                None,
            ),
            strict=True,
        )
    )


def native_index(backend):
    if backend not in {"opencv", "pynv"}:
        raise ValueError("native backend")
    return dict(
        zip(
            INDEX_FIELDS,
            (
                "benchmark_run_index.1",
                "native_decode",
                None,
                "not_applicable",
                backend,
                None,
                None,
                "not_applicable",
                None,
                "not_applicable",
                1,
                "single_video_worker",
                None,
            ),
            strict=True,
        )
    )


def _body(value):
    return value.model_dump(mode="json") if hasattr(value, "model_dump") else value


def _summary(samples, field, *, percentage=False):
    values = [row[field] for row in samples if row.get(field) is not None]
    if any(
        type(value) not in (float, int)
        or not math.isfinite(value)
        or value < 0
        or (percentage and value > 100)
        for value in values
    ):
        raise ValueError("invalid resource sample")
    count = len(values)
    return {
        "sample_mean": statistics.mean(values) if count else None,
        "sample_p90": sorted(values)[math.ceil(0.9 * count) - 1] if count else None,
        "sampled_max": max(values) if count else None,
        "valid_samples": count,
        "missing_samples": len(samples) - count,
        "availability": "not_reported"
        if not count
        else "partial"
        if count != len(samples)
        else "measured",
    }


METRICS = (
    ("gpu", "gpu_utilization_pct", "device_gpu", "pct"),
    ("gpu", "device_used_bytes", "device_vram", "bytes"),
    ("gpu", "vram_occupancy_pct", "device_vram_occupancy", "pct"),
    ("gpu", "decoder_utilization_pct", "device_nvdec", "pct"),
    ("gpu", "memory_controller_utilization_pct", "device_memory_controller", "pct"),
    ("worker", "tree_rss_bytes", "worker_tree_rss", "bytes"),
    ("worker", "host_cpu_utilization_pct", "host_cpu", "pct"),
    ("worker", "host_memory_used_bytes", "host_ram_used", "bytes"),
    ("worker", "host_memory_utilization_pct", "host_ram_utilization", "pct"),
)

SUMMARY_FIELDS = (
    "resource_summary_schema_version",
    "resource_summary_window",
    "resource_summary_method",
    "worker_trace_status",
    "gpu_trace_status",
    "worker_trace_scope",
    "gpu_trace_scope",
    "host_trace_scope",
    "worker_observed_span_s",
    "gpu_observed_span_s",
    "worker_cpu_time_s",
    "worker_cpu_observation_wall_s",
    "reaped_children_cpu_time_s",
    "worker_cpu_core_equivalents",
    "device_memory_capacity_bytes",
    "host_memory_capacity_bytes",
) + tuple(
    name
    for _, _, prefix, unit in METRICS
    for name in (
        f"{prefix}_sample_mean_{unit}",
        f"{prefix}_sample_p90_{unit}",
        f"{prefix}_sampled_max_{unit}",
        f"{prefix}_valid_samples",
        f"{prefix}_missing_samples",
        f"{prefix}_availability",
    )
)


def resource_summary(resources, gpu, *, gpu_capacity_bytes=None):
    resources, gpu = _body(resources), _body(gpu)
    worker_samples = resources.get("samples", []) if resources else []
    gpu_samples = [dict(row) for row in gpu.get("samples", [])] if gpu else []
    capacity = (
        gpu_capacity_bytes
        if gpu_capacity_bytes is not None
        else (gpu.get("device_total_bytes") if gpu else None)
    )
    if capacity is not None and (type(capacity) is not int or capacity <= 0):
        raise ValueError("GPU capacity")
    for row in gpu_samples:
        if capacity is not None:
            row["vram_occupancy_pct"] = 100 * row["device_used_bytes"] / capacity

    def span(samples):
        times = [row["elapsed_s"] for row in samples]
        if any(
            type(t) not in (float, int) or not math.isfinite(t) or t < 0 for t in times
        ) or any(b <= a for a, b in zip(times, times[1:], strict=False)):
            raise ValueError("resource sample clocks")
        return times[-1] - times[0] if len(times) >= 2 else None

    worker_span, gpu_span = span(worker_samples), span(gpu_samples)
    result = dict.fromkeys(SUMMARY_FIELDS)
    result.update(
        resource_summary_schema_version="benchmark_resources.1",
        resource_summary_window="observer_lifecycle_including_possible_setup_cleanup_not_operation_cropped",
        resource_summary_method="arithmetic_sample_mean_nearest_rank_resource_p90",
        worker_trace_status="not_collected"
        if resources is None
        else "complete"
        if resources.get("complete")
        else "incomplete",
        gpu_trace_status="not_collected"
        if gpu is None
        else "unavailable"
        if gpu.get("available") is False
        else "complete"
        if gpu.get("complete")
        else "incomplete",
        worker_trace_scope=resources.get("scope") if resources else None,
        gpu_trace_scope=gpu.get("scope") if gpu else None,
        host_trace_scope="host_system_shared_not_worker_or_cgroup_utilization"
        if any(
            row.get("host_memory_total_bytes") is not None
            or row.get("host_cpu_utilization_pct") is not None
            for row in worker_samples
        )
        else "not_reported",
        worker_observed_span_s=worker_span,
        gpu_observed_span_s=gpu_span,
        worker_cpu_time_s=resources.get("self_cpu_time_s") if resources else None,
        reaped_children_cpu_time_s=resources.get("reaped_children_cpu_time_s")
        if resources
        else None,
        worker_cpu_observation_wall_s=resources.get("cpu_observation_wall_s")
        if resources
        else None,
        worker_cpu_core_equivalents=resources["self_cpu_time_s"]
        / resources["cpu_observation_wall_s"]
        if resources and resources.get("cpu_observation_wall_s")
        else None,
        device_memory_capacity_bytes=capacity,
        host_memory_capacity_bytes=next(
            (
                row["host_memory_total_bytes"]
                for row in worker_samples
                if row.get("host_memory_total_bytes") is not None
            ),
            None,
        ),
    )
    for scope, field, prefix, unit in METRICS:
        stats = _summary(
            gpu_samples if scope == "gpu" else worker_samples,
            field,
            percentage=unit == "pct",
        )
        for key, value in stats.items():
            result[
                f"{prefix}_{key}_{unit}"
                if key in {"sample_mean", "sample_p90", "sampled_max"}
                else f"{prefix}_{key}"
            ] = value
    return result
