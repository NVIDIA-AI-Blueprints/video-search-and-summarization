#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Compatibility wrapper; Jenkins uses benchmark/core/vss_perf_benchmark.py."""

from core.vss_perf_benchmark import main


if __name__ == "__main__":
    raise SystemExit(main())
