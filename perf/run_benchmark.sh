#!/bin/bash
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PERF_SCENARIO="${PERF_SCENARIO:-quick_test}"
PERF_CONFIG_ID="${PERF_CONFIG_ID:-warehouse_2d-kafka-playback}"
export OUTPUT_DIR="${OUTPUT_DIR:-$ROOT/results/${PERF_SERVICE:-behavior-analytics}_${PERF_SCENARIO}_${PERF_CONFIG_ID}}"
exec "$ROOT/services/analytics/behavior-analytics/perf/run_benchmark.sh" "$@"
