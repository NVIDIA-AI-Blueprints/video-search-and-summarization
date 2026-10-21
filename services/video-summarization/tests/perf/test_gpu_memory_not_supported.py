# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

"""Tests for NVML memory handling on unified-memory GPUs."""

from types import SimpleNamespace


def _monitor(monkeypatch):
    import gpu_monitor

    monkeypatch.setattr(gpu_monitor, "DEVICE_COUNT", 1, raising=False)
    monkeypatch.setattr(gpu_monitor, "DEVICE_HANDLES", [object()], raising=False)
    monkeypatch.setattr(gpu_monitor, "GPU_NAMES", ["NVIDIA GB10"], raising=False)
    return gpu_monitor, gpu_monitor.GPUMonitor([0])


def test_unsupported_memory_does_not_discard_gpu_utilization(monkeypatch):
    gpu_monitor, monitor = _monitor(monkeypatch)
    monkeypatch.setattr(
        gpu_monitor.pynvml,
        "nvmlDeviceGetUtilizationRates",
        lambda _handle: SimpleNamespace(gpu=73),
    )

    def unsupported_memory(_handle):
        raise gpu_monitor.pynvml.NVMLError_NotSupported

    monkeypatch.setattr(gpu_monitor.pynvml, "nvmlDeviceGetMemoryInfo", unsupported_memory)
    monitor.gpu_running = True
    monkeypatch.setattr(
        gpu_monitor.time,
        "sleep",
        lambda _seconds: setattr(monitor, "gpu_running", False),
    )

    monitor._record_gpu_usage(1)

    assert monitor.gpu_usage_data["GPU0_Usage"] == [73]
    assert monitor.gpu_mem_data["GPU0_MemUsage"] == []
    assert monitor.unsupported_gpu_memory == {0}
    assert monitor.get_gpu_stats([0])["memory"]["mean"] is None


def test_unavailable_gpu_memory_is_null_in_standard_result():
    import vss_perf_lvs_adapter

    metrics = vss_perf_lvs_adapter._gpu_metrics_from_dict(
        {
            "vlm_gpu_usage_mean": 73.0,
            "vlm_gpu_memory_mean": None,
            "llm_gpu_usage_mean": 18.0,
            "llm_gpu_memory_mean": None,
        }
    ).model_dump(mode="json")

    assert metrics["vlm_gpu_usage_mean"] == 73.0
    assert metrics["vlm_gpu_memory_mean"] is None
    assert metrics["llm_gpu_usage_mean"] == 18.0
    assert metrics["llm_gpu_memory_mean"] is None


def test_unavailable_gpu_memory_can_be_written_to_result(monkeypatch, tmp_path):
    import json

    import vss_perf_common

    monkeypatch.setattr(
        vss_perf_common,
        "get_gpu_info",
        lambda: {
            "model": "GB10",
            "full_name": "NVIDIA GB10",
            "count": 1,
            "memory_gb": None,
            "driver_version": "test",
        },
    )
    output = tmp_path / "result.json"
    vss_perf_common.build_and_save(
        output,
        "LVS",
        [],
        platform=vss_perf_common.discover_platform(),
        run_id="lvs_test",
    )

    result = json.loads(output.read_text())
    assert result["metadata"]["platform"]["gpu"]["memory_gb"] is None
