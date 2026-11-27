#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
# Copied to VST_DIR/deploy.sh by stage_vst.py; retains setup/teardown's interface.
set -euo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
project="$(cat "${root}/.compose-project")"
if [[ ! "$project" =~ ^[a-z0-9][a-z0-9_-]*$ ]]; then
    echo 'Invalid staged Compose project' >&2
    exit 2
fi
action="${1:-}"
target="${2:-}"
case "$target" in
    vst) directory="${root}/stream-processing" ;;
    nvstreamer) directory="${root}/nvstreamer" ;;
    *) echo 'Usage: bash deploy.sh {up|down|config} {vst|nvstreamer}' >&2; exit 2 ;;
esac
component_project="${project}-${target}"
if [[ "${COMPOSE_PROJECT_NAME:-$project}" != "$project" &&
      "${COMPOSE_PROJECT_NAME:-$project}" != "$component_project" ]] ||
   [[ "${PERF_VST_COMPONENT_PROJECT:-$component_project}" != "$component_project" ]]; then
    echo 'Compose project must match the staged component' >&2
    exit 2
fi
case "$action" in
    up) args=(up -d) ;;
    down) args=(down) ;;
    config) args=(config) ;;
    *) echo 'Unsupported deployment action' >&2; exit 2 ;;
esac
# Profiles come from each target's compose.env, never an unrelated parent shell.
unset COMPOSE_PROFILES
compose_files=(-f "${directory}/docker-compose.yaml")
if [[ -f "${directory}/thor-runtime.json" ]]; then
    compose_files+=(-f "${directory}/thor-runtime.json")
fi
exec docker compose --project-name "$component_project" \
    --project-directory "$directory" --env-file "${directory}/compose.env" \
    "${compose_files[@]}" "${args[@]}"
