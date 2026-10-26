#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# One portable entrypoint. Services and credentials are configured externally.
set -euo pipefail
perf_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd -- "$perf_root"
perf_python="${PERF_PYTHON:-python3}"
exec "$perf_python" -m benchmarks.matrix "$@"
