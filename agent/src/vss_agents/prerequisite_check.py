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

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence


@dataclass(frozen=True)
class CommandResult:
    stdout: str
    stderr: str
    returncode: int


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


def _run_gpu_checks() -> None:
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
    print("=== NVIDIA Driver & GPU ===")
    print(gpu_result.stdout.strip())
    print()

    print("=== GPU Count ===")
    print(f"Detected {len(gpu_lines)} GPU(s)")
    print()


def _run_docker_checks() -> None:
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


def _run_nvidia_container_toolkit_check() -> None:
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
        print(
            "Install: "
            "https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html"
        )
    print()


def _run_disk_space_check() -> None:
    total, _, free = shutil.disk_usage("/")
    print("=== Disk Space ===")
    print(f"Root: {free / (1024 ** 3):.1f} GiB available of {total / (1024 ** 3):.1f} GiB")
    print()


def run_prerequisite_checks() -> None:
    print("\n=== Prerequisites Check ===")
    _run_gpu_checks()
    _run_docker_checks()
    _run_nvidia_container_toolkit_check()
    _run_disk_space_check()
    print("Prerequisites check complete.")
