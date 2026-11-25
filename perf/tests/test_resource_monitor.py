# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Collector failures must never become zero-valued successful measurements."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "benchmark/core"))
from resource_monitor import ResourceMonitor


def test_selected_gpu_is_not_silently_replaced(monkeypatch):
    import pynvml

    monkeypatch.setattr(pynvml, "nvmlInit", lambda: None)
    monkeypatch.setattr(
        pynvml,
        "nvmlDeviceGetHandleByIndex",
        lambda index: (_ for _ in ()).throw(pynvml.NVMLError_InvalidArgument()),
    )
    with pytest.raises(pynvml.NVMLError):
        ResourceMonitor("7")


def test_missing_samples_fail(monkeypatch):
    monitor = object.__new__(ResourceMonitor)
    monitor.samples = []
    with pytest.raises(RuntimeError, match="no samples"):
        monitor.result()
