# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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
"""Prerequisite checks for local/cloud VSS dry-run workflows."""

from __future__ import annotations

from dataclasses import asdict
from dataclasses import dataclass
import shutil
import subprocess
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path


@dataclass(frozen=True)
class CommandResult:
    stdout: str
    stderr: str
    returncode: int


@dataclass(frozen=True)
class GpuInfo:
    index: int
    name: str
    driver_version: str
    memory_total_mib: int | None
    memory_total: str


@dataclass(frozen=True)
class PrereqsReport:
    gpus: list[GpuInfo]
    gpu_count: int
    driver_version: str
    docker_version: str
    compose_version: str
    container_toolkit_ok: bool
    disk_free_gib: float
    disk_total_gib: float


def run_command(
    command: Sequence[str],
    *,
    error_message: str,
    timeout_seconds: int | None = None,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
) -> CommandResult:
    try:
        result = subprocess.run(
            list(command),
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            cwd=str(cwd) if cwd else None,
            env=env,
        )
    except FileNotFoundError as exc:
        raise RuntimeError(error_message) from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"{error_message} (timed out after {timeout_seconds}s)") from exc

    if result.returncode != 0:
        details = result.stderr.strip() or result.stdout.strip() or f"exit code {result.returncode}"
        raise RuntimeError(f"{error_message}\n{details}")
    return CommandResult(
        stdout=result.stdout,
        stderr=result.stderr,
        returncode=result.returncode,
    )


def _parse_memory_total_mib(raw_value: str) -> int | None:
    normalized = raw_value.strip()
    if not normalized:
        return None
    parts = normalized.split()
    if not parts:
        return None
    try:
        return int(parts[0])
    except ValueError:
        return None


def _run_gpu_checks() -> tuple[list[GpuInfo], str]:
    gpu_result = run_command(
        [
            "nvidia-smi",
            "--query-gpu=index,name,driver_version,memory.total",
            "--format=csv,noheader",
        ],
        error_message="nvidia-smi check failed. Ensure NVIDIA drivers are installed and GPUs are visible.",
        timeout_seconds=15,
    )
    gpu_lines = [line.strip() for line in gpu_result.stdout.splitlines() if line.strip()]
    gpus: list[GpuInfo] = []
    for line in gpu_lines:
        parts = [part.strip() for part in line.split(",", 3)]
        if len(parts) != 4:
            raise RuntimeError(f"Unexpected nvidia-smi output format: '{line}'")
        index_text, name, driver_version, memory_total = parts
        try:
            index = int(index_text)
        except ValueError as exc:
            raise RuntimeError(f"Unexpected GPU index from nvidia-smi: '{index_text}'") from exc
        gpus.append(
            GpuInfo(
                index=index,
                name=name,
                driver_version=driver_version,
                memory_total_mib=_parse_memory_total_mib(memory_total),
                memory_total=memory_total,
            )
        )
    print("=== NVIDIA Driver & GPU ===")
    print(gpu_result.stdout.strip())
    print()

    print("=== GPU Count ===")
    print(f"Detected {len(gpus)} GPU(s)")
    print()
    driver_version = gpus[0].driver_version if gpus else ""
    return gpus, driver_version


def _run_docker_checks() -> tuple[str, str]:
    docker_version = run_command(
        ["docker", "--version"],
        error_message="docker --version failed. Install Docker Engine before running this dry-run.",
        timeout_seconds=15,
    )
    compose_version = run_command(
        ["docker", "compose", "version"],
        error_message="docker compose version failed. Install Docker Compose v2 before running this dry-run.",
        timeout_seconds=15,
    )
    print("=== Docker ===")
    print(docker_version.stdout.strip())
    print(compose_version.stdout.strip())
    print()
    return docker_version.stdout.strip(), compose_version.stdout.strip()


def _run_nvidia_container_toolkit_check() -> bool:
    print("=== NVIDIA Container Toolkit ===")
    try:
        toolkit_check = subprocess.run(
            [
                "docker",
                "run",
                "--rm",
                "--gpus",
                "all",
                "nvidia/cuda:12.0.0-base-ubuntu22.04",
                "nvidia-smi",
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )
        toolkit_ok = toolkit_check.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        toolkit_ok = False

    if toolkit_ok:
        print("NVIDIA Container Toolkit: OK")
    else:
        print("WARNING: NVIDIA Container Toolkit may not be installed.")
        print("Install: https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html")
    print()
    return toolkit_ok


def _run_disk_space_check() -> tuple[float, float]:
    total, _, free = shutil.disk_usage("/")
    free_gib = round(free / (1024**3), 1)
    total_gib = round(total / (1024**3), 1)
    print("=== Disk Space ===")
    print(f"Root: {free_gib:.1f} GiB available of {total_gib:.1f} GiB")
    print()
    return free_gib, total_gib


def run_prereqs_checks() -> dict[str, object]:
    print("\n=== Prerequisites Check ===")
    gpus, driver_version = _run_gpu_checks()
    docker_version, compose_version = _run_docker_checks()
    container_toolkit_ok = _run_nvidia_container_toolkit_check()
    disk_free_gib, disk_total_gib = _run_disk_space_check()
    print("Prerequisites check complete.")
    return asdict(
        PrereqsReport(
            gpus=gpus,
            gpu_count=len(gpus),
            driver_version=driver_version,
            docker_version=docker_version,
            compose_version=compose_version,
            container_toolkit_ok=container_toolkit_ok,
            disk_free_gib=disk_free_gib,
            disk_total_gib=disk_total_gib,
        )
    )
