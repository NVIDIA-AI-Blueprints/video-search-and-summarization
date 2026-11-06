# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import subprocess
import sys
from types import SimpleNamespace

import pytest

from vss_perf_reporting import build_result, validate_result
from vss_perf_reporting import platform


@pytest.mark.parametrize(
    "name,memory,expected_model,expected_memory",
    [
        ("NVIDIA RTX PRO 6000 Blackwell Server Edition", "97887", "RTX", 95.59),
        ("NVIDIA H100 80GB HBM3", "81920", "H100", 80.0),
        ("NVIDIA GB10", "[N/A]", "GB10", None),
    ],
)
def test_discovery_without_nvml_uses_smi_and_existing_schema(
    monkeypatch, name, memory, expected_model, expected_memory
):
    monkeypatch.setitem(sys.modules, "pynvml", None)

    def query(command, **kwargs):
        if "--query-gpu=name,memory.total,driver_version" in command:
            assert kwargs["timeout"] == 10
            return f"{name}, {memory}, 595.58.03\n{name}, {memory}, 595.58.03\n"
        return "CUDA Version: 13.2"

    monkeypatch.setattr(platform.subprocess, "check_output", query)
    discovered = platform.discover_platform()
    assert discovered["gpu"] == {
        "model": expected_model,
        "full_name": name,
        "count": 2,
        "memory_gb": expected_memory,
        "driver_version": "595.58.03",
        "cuda_version": "13.2",
    }
    report = build_result(
        service="Alerts-Verification",
        run_id="alerts-platform-test",
        platform=discovered,
        test_cases=[{"test_case_id": "latency", "status": "PASS", "metrics": {}}],
    )
    validate_result(report)


def test_nvml_initialization_failure_uses_smi(monkeypatch):
    def unavailable():
        raise RuntimeError("NVML unavailable")

    monkeypatch.setitem(sys.modules, "pynvml", SimpleNamespace(nvmlInit=unavailable))
    monkeypatch.setattr(
        platform.subprocess, "check_output",
        lambda *a, **k: "NVIDIA H100, 81920, 595.58.03\n",
    )
    assert platform.get_gpu_info()["count"] == 1
    assert platform.get_gpu_info()["driver_version"] == "595.58.03"


@pytest.mark.parametrize("unsupported_memory", [False, True])
def test_working_nvml_remains_preferred(monkeypatch, unsupported_memory):
    class NotSupported(Exception):
        pass

    def memory(handle):
        if unsupported_memory:
            raise NotSupported()
        return SimpleNamespace(total=80 * 1024**3)

    monkeypatch.setitem(sys.modules, "pynvml", SimpleNamespace(
        nvmlInit=lambda: None,
        nvmlDeviceGetCount=lambda: 1,
        nvmlDeviceGetHandleByIndex=lambda _: object(),
        nvmlDeviceGetName=lambda _: b"NVIDIA H100",
        nvmlSystemGetDriverVersion=lambda: b"595.58.03",
        nvmlDeviceGetMemoryInfo=memory,
        NVMLError_NotSupported=NotSupported,
    ))

    def unexpected_query(*args, **kwargs):
        pytest.fail("Working NVML must not use the fallback")

    monkeypatch.setattr(platform.subprocess, "check_output", unexpected_query)
    assert platform.get_gpu_info() == {
        "model": "H100", "full_name": "NVIDIA H100", "count": 1,
        "memory_gb": None if unsupported_memory else 80.0,
        "driver_version": "595.58.03",
    }


@pytest.mark.parametrize("failure", ["missing", "timeout", "command", "empty", "malformed"])
def test_unavailable_or_invalid_smi_remains_best_effort(monkeypatch, failure):
    monkeypatch.setitem(sys.modules, "pynvml", None)

    def query(command, **kwargs):
        if failure == "missing":
            raise FileNotFoundError("nvidia-smi")
        if failure == "timeout":
            raise subprocess.TimeoutExpired(command, 10)
        if failure == "command":
            raise subprocess.CalledProcessError(1, command)
        return "" if failure == "empty" else "invalid metadata"

    monkeypatch.setattr(platform.subprocess, "check_output", query)
    assert platform.get_gpu_info() == {
        "model": "", "full_name": "", "count": 0,
        "memory_gb": 0, "driver_version": "",
    }
