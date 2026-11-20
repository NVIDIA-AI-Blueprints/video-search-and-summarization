#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
# Copied to VST_DIR/deploy.sh by stage_vst.py; retains setup/teardown's interface.
set -euo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
project="$(cat "${root}/.compose-project")"
if [[ ! "$project" =~ ^[a-z0-9][a-z0-9_-]*$ ]] || \
   [[ "${COMPOSE_PROJECT_NAME:-$project}" != "$project" ]]; then
    echo 'Compose project must match the staged deployment' >&2
    exit 2
fi
action="${1:-}"
target="${2:-}"
case "$target" in
    vst) directory="${root}/stream-processing" ;;
    nvstreamer) directory="${root}/nvstreamer" ;;
    *) echo 'Usage: bash deploy.sh {up|down|config} {vst|nvstreamer}' >&2; exit 2 ;;
esac
case "$action" in
    up) args=(up -d) ;;
    down) args=(down) ;;
    config) args=(config) ;;
    *) echo 'Unsupported deployment action' >&2; exit 2 ;;
esac
# Profiles come from each target's compose.env, never an unrelated parent shell.
unset COMPOSE_PROFILES
exec docker compose --project-name "${project}-${target}" \
    --project-directory "$directory" --env-file "${directory}/compose.env" \
    -f "${directory}/docker-compose.yaml" "${args[@]}"
