#!/bin/bash
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail

PERF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SERVICE_DIR="$(cd "$PERF_DIR/.." && pwd)"
INTEGRATION_DIR="$SERVICE_DIR/tests/integration"
COMPOSE_PROJECT_NAME="${COMPOSE_PROJECT_NAME:-ba-perf}"
MONITOR_NAME="${MONITOR_NAME:-ba-perf-latency-monitor}"
PERF_SCENARIO="${PERF_SCENARIO:-quick_test}"
PERF_CONFIG_ID="${PERF_CONFIG_ID:-warehouse_2d-kafka-playback}"
PERF_CONFIG_FILE="${PERF_CONFIG_FILE:-$PERF_DIR/configs/config.yaml}"
if [[ -f "$PERF_DIR/benchmark/scenario_config.py" && -f "$PERF_CONFIG_FILE" ]]; then
    # config.yaml is the run matrix. Env vars still win when already set.
    eval "$(python3 "$PERF_DIR/benchmark/scenario_config.py" \
        --config "$PERF_CONFIG_FILE" \
        --scenario "$PERF_SCENARIO")"
fi
PERF_DURATION_SECONDS="${PERF_DURATION_SECONDS:-${CONFIG_DURATION_SECONDS:-60}}"
PERF_WARMUP_SECONDS="${PERF_WARMUP_SECONDS:-${CONFIG_WARMUP_SECONDS:-10}}"
PERF_TOPICS="${PERF_TOPICS:-${CONFIG_TOPICS:-raw frames behavior events incidents}}"
PERF_PERCENTILES="${PERF_PERCENTILES:-${CONFIG_PERCENTILES:-50 95 99}}"
OUTPUT_DIR="${OUTPUT_DIR:-${PERF_OUTPUT_DIR:-$PERF_DIR/results/${PERF_SERVICE:-behavior-analytics}_${PERF_SCENARIO}_${PERF_CONFIG_ID}}}"
COMPOSE_DIR="$INTEGRATION_DIR/docker_compose"
export BA_PERF_DATA_DIR="${BA_PERF_DATA_DIR:-/tmp/ba-perf-${COMPOSE_PROJECT_NAME}-$(id -u)}"
export BA_PERF_OVERLAY_STAMP="${BA_PERF_OVERLAY_STAMP:-$BA_PERF_DATA_DIR/perf_app_overlay.json}"
BA_PERF_OWNERSHIP_MARKER=".vss-ba-perf-owned"
BA_PERF_REPO_ROOT="$(git -C "$PERF_DIR" rev-parse --show-toplevel 2>/dev/null || true)"

collect_perf_diagnostics() {
    local exit_code="${1:-1}"
    local outcome="failure"
    [[ "$exit_code" -eq 0 ]] && outcome="success"
    [[ -n "${PERF_DIAGNOSTICS_COLLECTOR:-}" ]] || return 0
    [[ -x "$PERF_DIAGNOSTICS_COLLECTOR" ]] || return 0

    "$PERF_DIAGNOSTICS_COLLECTOR" \
        --output "${PERF_DIAGNOSTICS_DIR:-$OUTPUT_DIR/diagnostics}" \
        --outcome "$outcome" \
        --service "Behavior-Analytics" \
        --since "${PERF_RUN_STARTED_AT:-}" \
        --compose-project "$COMPOSE_PROJECT_NAME" \
        --container "$MONITOR_NAME" || \
        echo "WARNING: Behavior Analytics diagnostics collection failed" >&2
}

refuse_symlink() {
    local path="$1"
    if [[ -L "$path" ]]; then
        echo "Refusing symlink $path" >&2
        return 1
    fi
}

refuse_symlink_components() {
    local path="$1"
    local current="$path"
    while [[ "$current" != "/" && "$current" != "." ]]; do
        refuse_symlink "$current"
        current="$(dirname "$current")"
    done
}

canonical_benchmark_dir() {
    local requested="${1:-}"
    local allowed_root="${BA_PERF_ALLOWED_ROOT:-/tmp}"
    local canonical_path canonical_root relative_path first_component

    if [[ -z "$requested" || "$requested" != /* ]]; then
        echo "BA_PERF_DATA_DIR must be a non-empty absolute path" >&2
        return 1
    fi
    refuse_symlink_components "$requested"
    refuse_symlink_components "$allowed_root"
    canonical_path="$(realpath -m -- "$requested")"
    canonical_root="$(realpath -m -- "$allowed_root")"

    case "$canonical_path" in
        /|/tmp|"$BA_PERF_REPO_ROOT"|"${WORKSPACE:-__unset_workspace__}")
            echo "Refusing unsafe BA_PERF_DATA_DIR: $canonical_path" >&2
            return 1
            ;;
    esac
    if [[ "$canonical_root" == "/" || "$canonical_path" != "$canonical_root"/* ]]; then
        echo "BA_PERF_DATA_DIR must be below BA_PERF_ALLOWED_ROOT ($canonical_root)" >&2
        return 1
    fi

    relative_path="${canonical_path#"$canonical_root"/}"
    first_component="${relative_path%%/*}"
    if [[ "$canonical_root" == "/tmp" && "$first_component" != ba-perf-* ]]; then
        echo "BA_PERF_DATA_DIR below /tmp must start with ba-perf-" >&2
        return 1
    fi
    printf '%s\n' "$canonical_path"
}

prepare_benchmark_dir() {
    local path marker expected_marker
    path="$(canonical_benchmark_dir "${1:-}")"
    marker="$path/$BA_PERF_OWNERSHIP_MARKER"
    expected_marker="vss-ba-perf:$COMPOSE_PROJECT_NAME"

    if [[ -e "$path" ]]; then
        [[ -d "$path" ]] || { echo "Refusing non-directory $path" >&2; return 1; }
        [[ -f "$marker" ]] || {
            echo "Refusing existing unowned benchmark directory $path" >&2
            return 1
        }
        [[ "$(cat "$marker")" == "$expected_marker" ]] || {
            echo "Refusing benchmark directory with an invalid ownership marker: $path" >&2
            return 1
        }
    else
        mkdir -p -- "$path"
        printf '%s\n' "$expected_marker" > "$marker"
    fi
    printf '%s\n' "$path"
}

remove_root_tree() {
    local path marker expected_marker
    path="$(canonical_benchmark_dir "${1:-}")"
    [[ -e "$path" || -L "$path" ]] || return 0
    [[ -d "$path" ]] || { echo "Refusing to delete non-directory $path" >&2; return 1; }
    marker="$path/$BA_PERF_OWNERSHIP_MARKER"
    expected_marker="vss-ba-perf:$COMPOSE_PROJECT_NAME"
    [[ -f "$marker" && "$(cat "$marker")" == "$expected_marker" ]] || {
        echo "Refusing to delete unowned benchmark directory $path" >&2
        return 1
    }
    docker run --rm -u 0 -v "$path:/data" alpine:3.20 \
        sh -c 'chmod -R a+rwX /data 2>/dev/null; rm -rf /data/..?* /data/.[!.]* /data/*' \
        >/dev/null 2>&1 || {
            echo "Container-assisted cleanup failed for $path" >&2
            return 1
        }
    rmdir -- "$path"
}


cleanup_legacy_mdx_volumes() {
    case "${BA_PERF_CLEAN_LEGACY_MDX_VOLUMES:-false}" in
        false|"")
            return 0
            ;;
        true)
            echo "WARNING: removing legacy global mdx_* volumes by explicit request" >&2
            docker volume rm -f \
                mdx_elastic-data mdx_elastic-logs mdx_kafka-data mdx_logstash-libs || true
            ;;
        *)
            echo "BA_PERF_CLEAN_LEGACY_MDX_VOLUMES must be true or false" >&2
            return 1
            ;;
    esac
}


resolve_image() {
    if [[ -n "${ARTIFACT_REF:-}" ]]; then
        printf '%s\n' "$ARTIFACT_REF"
    elif [[ -n "${IMAGE_TAG:-}" ]]; then
        printf '%s\n' "$IMAGE_TAG"
    elif [[ -n "${BEHAVIOR_ANALYTICS_IMAGE:-}" ]]; then
        printf '%s\n' "$BEHAVIOR_ANALYTICS_IMAGE"
    elif [[ -n "${CURRENT_BEHAVIOR_ANALYTICS_IMAGE:-}" ]]; then
        printf '%s\n' "$CURRENT_BEHAVIOR_ANALYTICS_IMAGE"
    else
        echo "Set ARTIFACT_REF to the promoted Behavior Analytics image." >&2
        return 1
    fi
}

compose() {
    docker compose -p "$COMPOSE_PROJECT_NAME" --env-file infra/.env \
        -f infra/compose.yml -f apps/mdx-apps.yml "$@"
}

load_compose_env() {
    set -a
    # shellcheck disable=SC1091
    source "$INTEGRATION_DIR/docker_compose/infra/.env"
    set +a
    export BEHAVIOR_ANALYTICS_IMAGE="$(resolve_image)"
    export COMPOSE_PROJECT_NAME
}
