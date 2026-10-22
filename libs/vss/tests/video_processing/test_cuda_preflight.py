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

"""Fail safely before entering native NVDEC code that can exit on cuInit errors."""

from __future__ import annotations

import ctypes
from typing import Any

import pytest


@pytest.mark.parametrize(
    "init_code,count_code,count,device,available",
    [
        (0, 0, 1, 0, True),
        (100, 0, 1, 0, False),
        (0, 100, 1, 0, False),
        (0, 0, 0, 0, False),
        (0, 0, 1, 1, False),
    ],
)
@pytest.mark.parametrize("system", ["Linux", "Windows"])
def test_cuda_driver_preflight_returns_without_native_decoder(
    processing: Any,
    monkeypatch: pytest.MonkeyPatch,
    init_code: int,
    count_code: int,
    count: int,
    device: int,
    available: bool,
    system: str,
) -> None:
    from vss.video_processing.decoders import pynv

    calls: list[str] = []

    def initialize(flags: int) -> int:
        calls.append("init")
        assert flags == 0
        return init_code

    def count_devices(pointer: Any) -> int:
        calls.append("count")
        ctypes.cast(pointer, ctypes.POINTER(ctypes.c_int))[0] = count
        return count_code

    def get_device(pointer: Any, ordinal: int) -> int:
        calls.append("get")
        ctypes.cast(pointer, ctypes.POINTER(ctypes.c_int))[0] = ordinal
        return 0

    def retain(pointer: Any, ordinal: int) -> int:
        calls.append("retain")
        ctypes.cast(pointer, ctypes.POINTER(ctypes.c_void_p))[0] = 1
        return 0

    def release(ordinal: int) -> int:
        calls.append("release")
        return 0

    class Driver:
        cuInit = staticmethod(initialize)
        cuDeviceGetCount = staticmethod(count_devices)
        cuDeviceGet = staticmethod(get_device)
        cuDevicePrimaryCtxRetain = staticmethod(retain)
        cuDevicePrimaryCtxRelease_v2 = staticmethod(release)

    monkeypatch.setattr(ctypes, "CDLL", lambda name: Driver())
    monkeypatch.setattr(ctypes, "WinDLL", lambda name: Driver(), raising=False)
    monkeypatch.setattr(pynv.platform, "system", lambda: system)
    if available:
        pynv._check_cuda_device(device)
    else:
        with pytest.raises(processing.DecoderUnavailableError, match="CUDA"):
            pynv._check_cuda_device(device)
    assert calls == (
        ["init", "count", "get", "retain", "release"]
        if available
        else (["init"] if init_code else ["init", "count"])
    )


@pytest.mark.parametrize("phase", ["get", "retain", "release"])
def test_cuda_context_failure_is_an_availability_error(
    processing: Any, monkeypatch: pytest.MonkeyPatch, phase: str
) -> None:
    from vss.video_processing.decoders import pynv

    calls: list[str] = []

    def count(pointer: Any) -> int:
        ctypes.cast(pointer, ctypes.POINTER(ctypes.c_int))[0] = 1
        return 0

    def status(name: str) -> Any:
        def invoke(*args: Any) -> int:
            calls.append(name)
            return 999 if phase == name else 0

        return invoke

    class Driver:
        cuInit = staticmethod(lambda flags: 0)
        cuDeviceGetCount = staticmethod(count)
        cuDeviceGet = staticmethod(status("get"))
        cuDevicePrimaryCtxRetain = staticmethod(status("retain"))
        cuDevicePrimaryCtxRelease = staticmethod(status("release"))

    monkeypatch.setattr(ctypes, "CDLL", lambda name: Driver())
    monkeypatch.setattr(pynv.platform, "system", lambda: "Linux")
    with pytest.raises(processing.DecoderUnavailableError, match="CUDA"):
        pynv._check_cuda_device(0)
    assert calls[-1] == phase


def test_missing_cuda_driver_is_actionable_availability_failure(
    processing: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from vss.video_processing.decoders import pynv

    def missing(name: str) -> Any:
        raise OSError("driver unavailable")

    monkeypatch.setattr(ctypes, "CDLL", missing)
    monkeypatch.setattr(pynv.platform, "system", lambda: "Linux")
    with pytest.raises(processing.DecoderUnavailableError, match="CUDA"):
        pynv._check_cuda_device(0)
