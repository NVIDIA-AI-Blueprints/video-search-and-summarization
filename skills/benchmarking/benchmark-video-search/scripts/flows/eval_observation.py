# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Read-only GPU sampling and token accounting for offline search evaluation."""

from __future__ import annotations

import csv
import json
import math
import statistics
import subprocess
import threading
import time
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path


def token_summary(routes):
    """Sum actual calls once; missing values are not zero and details are subsets."""
    result = {"routing_calls": len(routes), "calls_with_complete_usage": 0}
    for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
        values = [
            (r.get("llm_metadata") or {}).get("usage", {}).get(key) for r in routes
        ]
        known = [v for v in values if type(v) is int and v >= 0]
        result[key] = {
            "known_sum": sum(known) if known else None,
            "calls_reported": len(known),
            "calls_missing": len(values) - len(known),
        }
    for r in routes:
        usage = (r.get("llm_metadata") or {}).get("usage", {})
        result["calls_with_complete_usage"] += all(
            type(usage.get(k)) is int
            for k in ("prompt_tokens", "completion_tokens", "total_tokens")
        )
    for detail, field in (
        ("prompt_tokens_details", "cached_tokens"),
        ("completion_tokens_details", "reasoning_tokens"),
    ):
        values = []
        for r in routes:
            raw = (r.get("llm_metadata") or {}).get("raw_usage")
            value = (
                (raw.get(detail) or {}).get(field) if isinstance(raw, dict) else None
            )
            if type(value) is int and value >= 0:
                values.append(value)
        result[field] = {
            "known_sum": sum(values) if values else None,
            "calls_reported": len(values),
            "included_in_parent_counter": True,
        }
    return result


class GpuSampler:
    """One persistent nvidia-smi child. Observations are not exclusive GPU time."""

    def __init__(self, output_dir: Path, *, enabled=False, sample_ms=200):
        self.output_dir = output_dir
        self.enabled = enabled
        self.sample_ms = sample_ms
        self.samples = []
        self.status = "disabled"
        self.error = None
        self.process = None
        self.thread = None
        self._stderr = None
        self._output = None
        self.inventory = []
        self.allocations = []
        self.parse_errors = 0

    def __enter__(self):
        if not self.enabled:
            return self
        self.output_dir.mkdir(parents=True, exist_ok=True)
        try:
            info = subprocess.run(
                [
                    "nvidia-smi",
                    "--query-gpu=index,uuid,name,driver_version",
                    "--format=csv,noheader",
                ],
                capture_output=True,
                text=True,
                check=True,
            )
            self.inventory = list(
                csv.reader(info.stdout.splitlines(), skipinitialspace=True)
            )
            try:
                names = subprocess.check_output(
                    ["docker", "ps", "--format", "{{.Names}}"], text=True
                ).splitlines()
                if names:
                    template = '{"name":{{json .Name}},"devices":{{json .HostConfig.DeviceRequests}}}'
                    allocation = subprocess.check_output(
                        ["docker", "inspect", "--format", template, *names], text=True
                    )
                    self.allocations = [
                        json.loads(line) for line in allocation.splitlines()
                    ]
            except (OSError, subprocess.SubprocessError, ValueError):
                self.allocations = []
            self._stderr = (self.output_dir / "gpu-stderr.log").open("w")
            self._output = (self.output_dir / "gpu-samples.jsonl").open(
                "w", buffering=1
            )
            self.process = subprocess.Popen(
                [
                    "nvidia-smi",
                    "--query-gpu=timestamp,index,uuid,utilization.gpu,utilization.memory,memory.used,power.draw",
                    "--format=csv,noheader,nounits",
                    f"--loop-ms={self.sample_ms}",
                ],
                stdout=subprocess.PIPE,
                stderr=self._stderr,
                text=True,
            )
            self.status = "sampling"
            self.thread = threading.Thread(
                target=self._read, name="eval-gpu-sampler", daemon=True
            )
            self.thread.start()
        except (OSError, subprocess.SubprocessError) as exc:
            self.status = "unavailable"
            self.error = str(exc)
            self.close()
        return self

    def _read(self):
        try:
            for fields in csv.reader(self.process.stdout, skipinitialspace=True):
                if len(fields) != 7:
                    self.parse_errors += 1
                    continue

                def number(value):
                    try:
                        parsed = float(value)
                        return parsed if math.isfinite(parsed) else None
                    except ValueError:
                        return None

                row = {
                    "received_monotonic_s": time.perf_counter(),
                    "received_utc": datetime.now(UTC).isoformat(),
                    "driver_timestamp": fields[0],
                    "index": fields[1],
                    "gpu_uuid": fields[2],
                    "gpu_util_percent": number(fields[3]),
                    "memory_controller_util_percent": number(fields[4]),
                    "vram_mib": number(fields[5]),
                    "power_watts": number(fields[6]),
                }
                self.samples.append(row)
                self._output.write(json.dumps(row) + "\n")
        except (OSError, ValueError) as exc:
            self.error = str(exc)
            self.status = "partial"

    def window_summary(self, intervals):
        grouped = defaultdict(list)
        for row in self.samples:
            if any(
                start <= row["received_monotonic_s"] <= end for start, end in intervals
            ):
                grouped[row["gpu_uuid"]].append(row)
        devices = {}
        for uuid, rows in grouped.items():
            summary = {"samples": len(rows)}
            for key in (
                "gpu_util_percent",
                "memory_controller_util_percent",
                "vram_mib",
                "power_watts",
            ):
                values = [r[key] for r in rows if r[key] is not None]
                summary[key] = {
                    "sample_mean": statistics.mean(values) if values else None,
                    "sample_peak": max(values) if values else None,
                }
            devices[uuid] = summary
        return {
            "status": "observed" if devices else "no_samples",
            "devices": devices,
            "interpretation": "device-level rolling-window observations; not exclusive per-query compute",
        }

    def close(self):
        if self.process is not None:
            running = self.process.poll() is None
            if running:
                self.process.terminate()
                try:
                    self.process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait()
            if self.thread:
                self.thread.join(timeout=5)
            if self.process.stdout and not self.thread.is_alive():
                self.process.stdout.close()
            if not running and self.process.returncode and self.samples:
                self.status = "partial"
                self.error = (
                    f"nvidia-smi exited unexpectedly: {self.process.returncode}"
                )
            if self.status == "sampling":
                self.status = "available" if self.samples else "unavailable"
                if not self.samples:
                    self.error = (
                        "nvidia-smi produced no valid samples; inspect gpu-stderr.log"
                    )
        for stream in (self._output, self._stderr):
            if stream:
                stream.close()
        return self

    def __exit__(self, *_exc):
        self.close()
        if self.enabled:
            (self.output_dir / "gpu-metadata.json").write_text(
                json.dumps(self.metadata(), indent=2) + "\n"
            )

    def metadata(self):
        return {
            "status": self.status,
            "error": self.error,
            "sample_ms": self.sample_ms,
            "samples": len(self.samples),
            "parse_errors": self.parse_errors,
            "inventory_index_uuid_name_driver": self.inventory,
            "container_device_allocations": self.allocations,
            "sampling_limit": "driver utilization windows span approximately 1/6 to 1 second; overlapping k/phase observations",
        }
