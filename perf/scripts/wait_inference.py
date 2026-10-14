#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Wait for measured inference progress before starting benchmark warm-up."""

import importlib.util
import os
import subprocess
import time
from pathlib import Path


def main():
    root = Path(os.environ["VSS_PERF_ROOT"])
    spec = importlib.util.spec_from_file_location(
        "compose_base_benchmark", root / "benchmark/core/vss_perf_benchmark.py"
    )
    benchmark = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(benchmark)
    docker = ["docker"]
    if os.environ.get("DOCKER_USE_SUDO") == "true":
        docker = ["sudo", "-n", "docker"]
    container = "vss-rtvi-cv"
    deadline = time.monotonic() + float(os.environ["POST_DEPLOY_TIMEOUT_SECONDS"])
    while time.monotonic() < deadline:
        state = subprocess.run(
            docker + ["inspect", "--format", "{{.State.Status}}", container],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        if state not in ("running", "restarting"):
            raise RuntimeError(
                f"RT-CV stopped before inference was ready (state={state})"
            )
        logs = subprocess.run(
            docker + ["logs", "--tail", "300", container],
            check=True,
            capture_output=True,
            text=True,
        )
        samples = benchmark.parse_fps(logs.stdout + "\n" + logs.stderr)
        if samples and any(value > 0 for value in samples[-1]):
            print("RT-CV readiness confirmed by positive measured inference FPS")
            return
        time.sleep(5)
    raise TimeoutError(
        "RT-CV did not produce positive FPS before its readiness deadline"
    )


if __name__ == "__main__":
    main()
