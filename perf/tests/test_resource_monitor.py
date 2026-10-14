# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Sampler failure and counter contract tests use fixtures, not measured hardware."""

import sys
import unittest
from collections import namedtuple
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "benchmark/core"))
from resource_monitor import CollectionError, ResourceMonitor, summarize

Disk = namedtuple("Disk", ["read_bytes", "write_bytes"])
Network = namedtuple("Network", ["bytes_sent", "bytes_recv"])


class ResourceTests(unittest.TestCase):
    def monitor(self):
        monitor = ResourceMonitor(
            "0",
            {"gpu_monitoring": {"enabled": False}, "cpu_monitoring": {"enabled": True}},
        )
        monitor.baseline_disk, monitor.end_disk = Disk(10, 20), Disk(110, 120)
        monitor.baseline_network, monitor.end_network = (
            Network(100, 200),
            Network(200, 600),
        )
        monitor.elapsed = 10
        monitor.samples = [
            {
                "cpu": {
                    "utilization_percent": 25,
                    "memory_used_gb": 12,
                    "memory_percent": 50,
                },
                "gpu": {},
            }
        ]
        return monitor

    def test_deltas_and_sample_statistics_are_measured_not_placeholder(self):
        stats = self.monitor().result()
        self.assertEqual(stats["storage"]["read_bytes"], 100)
        self.assertEqual(stats["network"]["bytes_recv_per_second"], 40)
        self.assertEqual(stats["cpu"]["utilization_percent"]["mean"], 25)
        self.assertEqual(
            stats["collector_source"]["commit"],
            "c2c854be99674d70999c69973ff0f0e0563c00d0",
        )

    def test_failed_sampler_never_reports_fake_zeros(self):
        monitor = self.monitor()
        monitor.errors.append("NVML collection failed")
        with self.assertRaisesRegex(CollectionError, "NVML"):
            monitor.result()

    def test_no_samples_or_counter_reset_fails(self):
        monitor = self.monitor()
        monitor.samples = []
        with self.assertRaises(CollectionError):
            monitor.result()
        monitor = self.monitor()
        monitor.end_network = Network(10, 20)
        with self.assertRaisesRegex(CollectionError, "counter reset"):
            monitor.result()

    def test_unavailable_host_counters_fail(self):
        with (
            patch("resource_monitor.psutil.disk_io_counters", return_value=None),
            self.assertRaisesRegex(CollectionError, "unavailable"),
        ):
            self.monitor()._counters()

    def test_selected_gpu_mismatch_fails_without_fallback_to_all_devices(self):
        with (
            patch("resource_monitor.pynvml.nvmlInit"),
            patch("resource_monitor.pynvml.nvmlShutdown") as shutdown,
            patch("resource_monitor.pynvml.nvmlDeviceGetCount", return_value=1),
            patch("resource_monitor.pynvml.nvmlDeviceGetHandleByIndex") as handle,
            self.assertRaisesRegex(CollectionError, "selected GPU indices"),
        ):
            ResourceMonitor("1", {"gpu_monitoring": {"enabled": True}})
        shutdown.assert_called_once()
        handle.assert_not_called()

    def test_nvml_unavailable_preserves_error_without_shutdown_masking(self):
        from pynvml import NVMLError_DriverNotLoaded

        with (
            patch(
                "resource_monitor.pynvml.nvmlInit",
                side_effect=NVMLError_DriverNotLoaded(),
            ),
            patch("resource_monitor.pynvml.nvmlShutdown") as shutdown,
            self.assertRaisesRegex(CollectionError, "initialization failed"),
        ):
            ResourceMonitor("0", {"gpu_monitoring": {"enabled": True}})
        shutdown.assert_not_called()

    def test_nonfinite_statistics_fail(self):
        for values in ([], [float("nan")], [float("inf")]):
            with self.assertRaises(CollectionError):
                summarize(values)


if __name__ == "__main__":
    unittest.main()
