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

"""Optional device-wide GPU samples; explicitly not worker memory attribution."""

import threading
import time


class DeviceSampler:
    def __init__(self, interval_s=0.25):
        if not 0.25 <= interval_s <= 5:
            raise ValueError("GPU observation interval")
        self.interval_s = interval_s

    def __enter__(self):
        self.trace = {
            "scope": "device_wide_including_resident_models",
            "available": False,
            "complete": False,
            "samples": [],
        }
        self.stop = threading.Event()
        self.thread = None
        self.nvml = None
        try:
            import pynvml

            pynvml.nvmlInit()
            self.nvml = pynvml
            if pynvml.nvmlDeviceGetCount() != 1:
                raise ValueError("single GPU required")
            self.handle = pynvml.nvmlDeviceGetHandleByIndex(0)
            self.trace["device_total_bytes"] = pynvml.nvmlDeviceGetMemoryInfo(
                self.handle
            ).total
            self.started = time.monotonic()
            self.trace.update(available=True, complete=True, interval_s=self.interval_s)
            self.sample()
            self.thread = threading.Thread(target=self.run, daemon=True)
            self.thread.start()
        except Exception:
            self.trace["complete"] = False
        return self

    def sample(self):
        try:
            if len(self.trace["samples"]) >= 4000:
                raise ValueError("GPU sample budget")
            n = self.nvml
            try:
                decoder, _ = n.nvmlDeviceGetDecoderUtilization(self.handle)
            except n.NVMLError_NotSupported:
                decoder = None
            utilization = n.nvmlDeviceGetUtilizationRates(self.handle)
            self.trace["samples"].append(
                {
                    "elapsed_s": time.monotonic() - self.started,
                    "device_used_bytes": n.nvmlDeviceGetMemoryInfo(self.handle).used,
                    "gpu_utilization_pct": utilization.gpu,
                    "memory_controller_utilization_pct": getattr(
                        utilization, "memory", None
                    ),
                    "decoder_utilization_pct": decoder,
                }
            )
        except Exception:
            self.trace["complete"] = False
            self.stop.set()

    def run(self):
        while not self.stop.wait(self.interval_s):
            self.sample()

    def __exit__(self, *_):
        self.stop.set()
        if self.thread:
            self.thread.join(timeout=2)
            self.trace["complete"] &= not self.thread.is_alive()
            if self.thread.is_alive():
                return
            self.sample()
            times = [sample["elapsed_s"] for sample in self.trace["samples"]]
            self.trace["complete"] &= len(times) >= 2 and all(
                0 < b - a <= 4 * self.interval_s
                for a, b in zip(times, times[1:], strict=False)
            )
        if self.nvml:
            self.nvml.nvmlShutdown()
