#!/bin/bash
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PERF_SCENARIO="${PERF_SCENARIO:-quick_test}"
PERF_CONFIG_ID="${PERF_CONFIG_ID:-warehouse-es-dump}"
export OUTPUT_DIR="${OUTPUT_DIR:-$ROOT/perf/results/${PERF_SERVICE:-video-analytics-api}_${PERF_SCENARIO}_${PERF_CONFIG_ID}}"
exec "$ROOT/services/analytics/video-analytics-api/perf/run_benchmark.sh" "$@"
