#!/bin/bash
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

source "$(dirname "${BASH_SOURCE[0]}")/common.sh"

need_cmd() {
    local name="$1"
    if ! command -v "$name" >/dev/null; then
        echo "pre-flight: missing command: $name" >&2
        exit 1
    fi
}

need_file() {
    local path="$1"
    if [[ ! -f "$path" ]]; then
        echo "pre-flight: missing file: $path" >&2
        echo "pre-flight: listing $(dirname "$path"):" >&2
        ls -la "$(dirname "$path")" >&2 || true
        echo "pre-flight: checkout root $REPO_ROOT:" >&2
        ls -la "$REPO_ROOT" >&2 || true
        echo "pre-flight: deploy tree:" >&2
        ls -laR "$REPO_ROOT/deploy" >&2 || true
        exit 1
    fi
}

# Jenkins GPU sparse-checkout keeps deploy/docker/services/ and perf/, but
# omits compose.yml, containers.env, and industry-profiles. Depth-1 clone
# still has those blobs; widen the cone and materialize them.
materialize_warehouse_compose() {
    local needed=(
        "$VSS_APPS_DIR/compose.yml"
        "$VSS_APPS_DIR/containers.env"
        "$WAREHOUSE_DIR/compose.yml"
        "$WAREHOUSE_DIR/overrides.env"
    )
    local path
    local missing=0
    for path in "${needed[@]}"; do
        if [[ ! -f "$path" ]]; then
            echo "pre-flight: not in worktree yet: $path"
            missing=1
        fi
    done
    [[ "$missing" -eq 0 ]] && return 0

    need_cmd git
    git -C "$REPO_ROOT" rev-parse --is-inside-work-tree >/dev/null

    local sparse
    sparse="$(git -C "$REPO_ROOT" rev-parse --git-path info/sparse-checkout)"
    echo "pre-flight: materializing deploy/docker from git"
    if [[ -f "$sparse" ]]; then
        if ! grep -qE '^/?deploy/docker/?$' "$sparse"; then
            printf '%s\n' 'deploy/docker/' >> "$sparse"
        fi
        git -C "$REPO_ROOT" read-tree -mu HEAD
    fi
    git -C "$REPO_ROOT" sparse-checkout add deploy/docker >/dev/null 2>&1 || true
    git -C "$REPO_ROOT" checkout -f HEAD -- deploy/docker
}

need_cmd docker
need_cmd python3
need_cmd nvidia-smi
if ! docker info >/dev/null 2>&1; then
    echo "pre-flight: docker info failed" >&2
    docker info >&2 || true
    exit 1
fi
if ! nvidia-smi >/dev/null 2>&1; then
    echo "pre-flight: nvidia-smi failed" >&2
    nvidia-smi >&2 || true
    exit 1
fi
need_file "$PERF_DIR/configs/kpis.yaml"
materialize_warehouse_compose
need_file "$VSS_APPS_DIR/compose.yml"
need_file "$VSS_APPS_DIR/containers.env"
need_file "$WAREHOUSE_DIR/compose.yml"
need_file "$WAREHOUSE_DIR/overrides.env"
ensure_host_dir "$OUTPUT_DIR"
echo "Warehouse industry-example pre-flight passed."
echo "  VSS_APPS_DIR=$VSS_APPS_DIR"
echo "  VSS_DATA_DIR=$VSS_DATA_DIR"
echo "  COMPOSE_PROJECT_NAME=$COMPOSE_PROJECT_NAME"
