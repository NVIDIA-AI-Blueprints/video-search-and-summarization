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

"""Run dimensions, unavailable measurements and resource definitions stay explicit."""

import sys
import threading
import time
from types import SimpleNamespace

import pytest

from benchmarks.resources import ResourceSample, ResourceSampler, ResourceTrace
from benchmarks.scorecard import (
    INDEX_FIELDS,
    SUMMARY_FIELDS,
    native_index,
    resource_summary,
    workflow_index,
)
from benchmarks.workflow.gpu import DeviceSampler


def test_workflow_and_native_indexes_never_claim_delivered_frames_or_overlap():
    profile = SimpleNamespace(
        arm="library",
        captioning="rtvi",
        vlm_model="model",
        store_backend="file",
        chunk_duration_s=30,
        barrier_directory=None,
        caption_settings=lambda: {"num_frames_per_second_or_fixed_frames_chunk": 8},
    )
    result = workflow_index(profile)
    assert set(result) == set(INDEX_FIELDS)
    assert result["vlm_model"] == "model" and result["backend_type"] == "rtvi"
    assert result["frames_per_chunk"] == 8 and result["chunk_duration_s"] == 30
    assert (
        result["concurrent_streams"] == 1
        and result["observed_concurrent_streams"] is None
    )
    profile.barrier_directory = "barrier"
    assert workflow_index(profile)["concurrent_streams"] == 2
    assert "not_verified" in workflow_index(profile)["concurrent_streams_status"]
    profile.captioning = "replay"
    replay = workflow_index(profile)
    assert (
        replay["vlm_model"]
        is replay["frames_per_chunk"]
        is replay["chunk_duration_s"]
        is None
    )
    assert replay["vlm_model_status"] == "not_executed_replay"
    result = native_index("pynv")
    assert result["backend_type"] == "pynv" and result["vlm_model"] is None
    assert result["frames_per_chunk"] is None and result["chunk_duration_s"] is None
    assert result["frames_per_chunk_status"] == "not_applicable"


def traces():
    resources = {
        "scope": "sampled_process_tree_lower_bound",
        "complete": True,
        "self_cpu_time_s": 2,
        "cpu_observation_wall_s": 4,
        "reaped_children_cpu_time_s": 3,
        "samples": [
            {
                "elapsed_s": 0,
                "tree_rss_bytes": 10,
                "host_memory_used_bytes": 400,
                "host_memory_total_bytes": 1000,
                "host_memory_utilization_pct": 60,
            },
            {
                "elapsed_s": 2,
                "tree_rss_bytes": 20,
                "host_cpu_utilization_pct": 50,
                "host_memory_used_bytes": 500,
                "host_memory_total_bytes": 1000,
                "host_memory_utilization_pct": 70,
            },
        ],
    }
    gpu = {
        "scope": "device_wide_including_resident_models",
        "complete": True,
        "available": True,
        "device_total_bytes": 1000,
        "samples": [
            {
                "elapsed_s": 0,
                "device_used_bytes": 100,
                "gpu_utilization_pct": 0,
                "decoder_utilization_pct": None,
                "memory_controller_utilization_pct": 3,
            },
            {
                "elapsed_s": 2,
                "device_used_bytes": 300,
                "gpu_utilization_pct": 60,
                "decoder_utilization_pct": 20,
                "memory_controller_utilization_pct": 7,
            },
        ],
    }
    return resources, gpu


def test_sample_means_p90_memory_occupancy_and_cpu_scopes_are_distinct():
    result = resource_summary(*traces())
    assert set(result) == set(SUMMARY_FIELDS)
    assert result["device_gpu_sample_mean_pct"] == 30
    assert result["device_gpu_sample_p90_pct"] == 60
    assert result["device_vram_sample_mean_bytes"] == 200
    assert result["device_vram_occupancy_sample_mean_pct"] == 20
    assert result["device_memory_controller_sample_mean_pct"] == 5
    assert result["device_nvdec_sample_mean_pct"] == 20
    assert (
        result["device_nvdec_valid_samples"]
        == result["device_nvdec_missing_samples"]
        == 1
    )
    assert result["device_nvdec_availability"] == "partial"
    assert result["host_cpu_sample_mean_pct"] == 50
    assert result["host_ram_utilization_sample_mean_pct"] == 65
    assert result["worker_cpu_core_equivalents"] == 0.5
    assert result["worker_observed_span_s"] == 2
    assert result["worker_cpu_observation_wall_s"] == 4
    assert result["reaped_children_cpu_time_s"] == 3
    assert result["gpu_trace_scope"] == "device_wide_including_resident_models"


def test_legacy_optional_counters_and_disabled_observers_do_not_become_zero():
    empty = resource_summary(None, None)
    assert empty["worker_trace_status"] == empty["gpu_trace_status"] == "not_collected"
    assert empty["device_gpu_sample_mean_pct"] is None
    worker, gpu = traces()
    del worker["cpu_observation_wall_s"]
    for row in worker["samples"]:
        for key in list(row):
            if key.startswith("host_"):
                del row[key]
    for row in gpu["samples"]:
        del row["memory_controller_utilization_pct"]
        row["decoder_utilization_pct"] = None
    del gpu["device_total_bytes"]
    result = resource_summary(worker, gpu)
    assert result["worker_tree_rss_sampled_max_bytes"] == 20
    assert result["worker_cpu_core_equivalents"] is None
    assert result["host_cpu_sample_mean_pct"] is None
    assert result["device_nvdec_sample_mean_pct"] is None
    assert result["device_vram_occupancy_sample_mean_pct"] is None
    assert result["device_memory_controller_availability"] == "not_reported"


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -1, 101, True])
def test_invalid_percentages_rejected(value):
    worker, gpu = traces()
    gpu["samples"][0]["gpu_utilization_pct"] = value
    with pytest.raises(ValueError):
        resource_summary(worker, gpu)


def test_psutil_host_cpu_uses_counter_deltas_and_memory_percent_definition(monkeypatch):
    snapshots = iter(
        [{"user": 50, "idle": 50, "guest": 10}, {"user": 70, "idle": 60, "guest": 15}]
    )
    process = SimpleNamespace(
        memory_info=lambda: SimpleNamespace(rss=5), children=lambda **kw: []
    )
    fake = SimpleNamespace(
        Process=lambda pid: process,
        Error=RuntimeError,
        NoSuchProcess=ProcessLookupError,
        cpu_times=lambda: SimpleNamespace(_asdict=lambda: next(snapshots)),
        virtual_memory=lambda: SimpleNamespace(used=400, total=1000, percent=60),
    )
    monkeypatch.setitem(sys.modules, "psutil", fake)
    sampler = ResourceSampler(1)
    sampler.started = time.perf_counter()
    sampler._sample()
    sampler._sample()
    first, second = sampler.samples
    assert first.host_cpu_utilization_pct is None
    assert second.host_cpu_utilization_pct == pytest.approx(100 * 2 / 3)
    assert (
        second.host_memory_used_bytes == 400
        and second.host_memory_utilization_pct == 60
    )


def test_device_sampler_records_controller_busy_separately_and_nvdec_unsupported():
    class Unsupported(Exception):
        pass

    sampler = DeviceSampler()
    sampler.trace = {"samples": [], "complete": True}
    sampler.stop = threading.Event()
    sampler.handle = object()
    sampler.started = time.monotonic()
    sampler.nvml = SimpleNamespace(
        nvmlDeviceGetDecoderUtilization=lambda h: (_ for _ in ()).throw(Unsupported()),
        nvmlDeviceGetMemoryInfo=lambda h: SimpleNamespace(used=80),
        nvmlDeviceGetUtilizationRates=lambda h: SimpleNamespace(gpu=20, memory=40),
        NVMLError_NotSupported=Unsupported,
    )
    sampler.sample()
    row = sampler.trace["samples"][0]
    assert row["device_used_bytes"] == 80 and row["gpu_utilization_pct"] == 20
    assert row["memory_controller_utilization_pct"] == 40
    assert row["decoder_utilization_pct"] is None and sampler.trace["complete"]


def test_resource_sample_old_schema_still_loads_without_host_fields():
    sample = ResourceSample(
        elapsed_s=0, self_rss_bytes=1, tree_rss_bytes=1, observed_processes=1
    )
    assert (
        sample.host_memory_used_bytes is None
        and sample.host_cpu_utilization_pct is None
    )


@pytest.mark.parametrize("complete", [False, True])
@pytest.mark.parametrize("times", [(0, 0), (0, 1, 1), (0, 2, 1)])
def test_resource_trace_rejects_duplicate_or_decreasing_sample_times(complete, times):
    sample = ResourceSample(
        elapsed_s=0, self_rss_bytes=1, tree_rss_bytes=1, observed_processes=1
    )
    with pytest.raises(ValueError, match="ordered start/end samples"):
        ResourceTrace(
            interval_s=1,
            complete=complete,
            samples=tuple(sample.model_copy(update={"elapsed_s": t}) for t in times),
            self_cpu_time_s=0,
            reaped_children_cpu_time_s=0,
            io_scope="unsupported",
            self_read_bytes=None,
            self_write_bytes=None,
        )


def test_cpu_wall_window_includes_final_sampling_work(monkeypatch):
    process = SimpleNamespace(
        memory_info=lambda: SimpleNamespace(rss=1),
        children=lambda **kwargs: [],
        io_counters=lambda: SimpleNamespace(read_bytes=0, write_bytes=0),
    )
    monkeypatch.setitem(
        sys.modules,
        "psutil",
        SimpleNamespace(
            Process=lambda pid: process,
            Error=RuntimeError,
            NoSuchProcess=ProcessLookupError,
        ),
    )

    def slow_host(psutil):
        deadline = time.perf_counter() + 0.05
        while time.perf_counter() < deadline:
            pass
        return {}

    sampler = ResourceSampler(1)
    monkeypatch.setattr(sampler, "_host", slow_host)
    with sampler:
        pass
    trace = sampler.trace
    summary = resource_summary(trace, None)
    sample_span = trace.samples[-1].elapsed_s - trace.samples[0].elapsed_s
    assert trace.cpu_observation_wall_s > sample_span + 0.04
    assert (
        summary["worker_cpu_core_equivalents"]
        == trace.self_cpu_time_s / trace.cpu_observation_wall_s
    )
