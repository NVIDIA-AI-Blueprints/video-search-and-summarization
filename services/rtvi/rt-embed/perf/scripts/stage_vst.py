#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Stage the shared VIOS deployment for this service's benchmark."""
from pathlib import Path
import runpy

if __name__ == "__main__":
    helper = Path(__file__).resolve().parents[5] / "libs/vss-perf-tools/stage_vst.py"
    runpy.run_path(str(helper), run_name="__main__")
