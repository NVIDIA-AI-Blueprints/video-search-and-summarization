#!/bin/bash
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

source "$(dirname "${BASH_SOURCE[0]}")/common.sh"

command -v docker >/dev/null
docker info >/dev/null
command -v python3 >/dev/null
resolve_image >/dev/null
test -f "$PERF_DIR/configs/kpis.yaml"
test -f "$INTEGRATION_DIR/scripts/load_elasticsearch_data_dump.js"
test -f "$INTEGRATION_DIR/generate_env.sh"
test -f "$INTEGRATION_DIR/lifecycle.sh"
test -f "$INTEGRATION_DIR/cleanup.sh"
test -d "$DUMP_DIR"
shopt -s nullglob
dump_files=("$DUMP_DIR"/*.json)
if [[ ${#dump_files[@]} -eq 0 ]]; then
    echo "No Elasticsearch dumps in $DUMP_DIR" >&2
    exit 1
fi
for dump in "${dump_files[@]}"; do
    if grep -q 'git-lfs.github.com' "$dump"; then
        echo "Dump is a Git LFS pointer: $dump" >&2
        head -n 5 "$dump" >&2 || true
        exit 1
    fi
done
resolve_apps_dir "$(resolve_repo_root)" >/dev/null
ensure_host_dir "$OUTPUT_DIR"
echo "Video Analytics API query pre-flight passed."
