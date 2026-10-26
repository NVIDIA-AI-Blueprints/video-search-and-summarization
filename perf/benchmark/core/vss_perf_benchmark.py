#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Jenkins entrypoint at the VSS checkout root for Video Analytics API."""

from __future__ import annotations

import os
import runpy
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
SERVICE_CORE = (
    REPO_ROOT
    / "services"
    / "analytics"
    / "video-analytics-api"
    / "perf"
    / "benchmark"
    / "core"
    / "vss_perf_benchmark.py"
)


def _arg_value(flag: str, default: str) -> str:
    args = sys.argv[1:]
    prefix = f"{flag}="
    for index, token in enumerate(args):
        if token == flag and index + 1 < len(args):
            return args[index + 1]
        if token.startswith(prefix):
            return token[len(prefix) :]
    env_key = "PERF_SCENARIO" if flag == "--scenario" else "PERF_CONFIG_ID"
    if flag == "--config-id":
        env_key = "PERF_CONFIG_ID"
    elif flag == "--scenario":
        env_key = "PERF_SCENARIO"
    return os.environ.get(env_key, default)


def main() -> None:
    if not SERVICE_CORE.is_file():
        sys.stderr.write(f"Perf package not found: {SERVICE_CORE}\n")
        raise SystemExit(1)
    scenario = _arg_value("--scenario", "quick_test")
    config_id = _arg_value("--config-id", "warehouse-es-dump")
    os.environ.setdefault(
        "OUTPUT_DIR",
        str(REPO_ROOT / "perf" / "results" / f"video-analytics-api_{scenario}_{config_id}"),
    )
    sys.argv[0] = str(SERVICE_CORE)
    runpy.run_path(str(SERVICE_CORE), run_name="__main__")


if __name__ == "__main__":
    main()
