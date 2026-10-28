#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
if [[ -n "${PERF_OUTPUT_DIR:-}" ]]; then
    export OUTPUT_DIR="$PERF_OUTPUT_DIR"
fi
if [[ -z "${ARTIFACT_REF:-}" && -n "${IMAGE_TAG:-}" ]]; then
    export ARTIFACT_REF="$IMAGE_TAG"
fi
exec "$ROOT/services/analytics/video-analytics-api/perf/scripts/measure.sh" "$@"
