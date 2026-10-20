#!/bin/bash
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

source "$(dirname "${BASH_SOURCE[0]}")/common.sh"

command -v docker >/dev/null
docker info >/dev/null
resolve_image >/dev/null
test -f "$PERF_DIR/configs/kpis.yaml"
playback="$INTEGRATION_DIR/docker_compose/apps_data/playback/warehouse_2d_playback_data.txt"
test -f "$playback"
# Jenkins GitHub checkout without git-lfs leaves a 100-byte pointer. Playback
# then publishes nothing and the latency report fails after several minutes.
if grep -q 'git-lfs.github.com' "$playback" || [[ "$(wc -c < "$playback")" -lt 1000000 ]]; then
    echo "Playback data is missing Git LFS content: $playback" >&2
    head -n 5 "$playback" >&2 || true
    exit 1
fi
ensure_host_dir "$OUTPUT_DIR"
echo "Behavior Analytics playback pre-flight passed."
