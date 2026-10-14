#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
set -euo pipefail
export VSS_PERF_STAGE=teardown
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/common.sh"
if [[ "${VSS_PERF_BOUNDED_STAGE:-}" != "$VSS_PERF_STAGE" ]]; then
    export VSS_PERF_BOUNDED_STAGE="$VSS_PERF_STAGE"
    stage_status=0
    "$PYTHON" "$VSS_PERF_ROOT/scripts/run_stage.py" "$PERF_STAGE_TIMEOUT_SECONDS" "${BASH_SOURCE[0]}" '' || stage_status=$?
    exit "$stage_status"
fi
# Runtime files and model/engine caches persist for diagnosis. No global Docker cleanup.
# Explicit deployment targets activate profiles only for that command. Select
# every profile when removing this project so those services cannot be omitted.
"${COMPOSE_CMD[@]}" --profile '*' down --timeout 30
remaining_containers="$("${DOCKER_CMD[@]}" ps --all --quiet --filter "label=com.docker.compose.project=$COMPOSE_PROJECT_NAME")"
remaining_networks="$("${DOCKER_CMD[@]}" network ls --quiet --filter "label=com.docker.compose.project=$COMPOSE_PROJECT_NAME")"
if [[ -n "$remaining_containers" || -n "$remaining_networks" ]]; then
    echo 'Benchmark Compose project still owns containers or networks after teardown' >&2
    exit 1
fi
# VIOS persists registered sensors in PostgreSQL, but its stream plane does not
# restore those registrations on restart. Reset only this benchmark database.
# Read names from Docker's project and logical-volume labels; do not assemble IDs.
sensor_databases="$("${DOCKER_CMD[@]}" volume ls --quiet --filter "label=com.docker.compose.project=$COMPOSE_PROJECT_NAME" --filter 'label=com.docker.compose.volume=vios_pg_data')"
sensor_database_volumes=()
while IFS= read -r volume; do
    [[ -z "$volume" ]] || sensor_database_volumes+=("$volume")
done <<< "$sensor_databases"
if [[ "${#sensor_database_volumes[@]}" -gt 0 ]]; then
    "${DOCKER_CMD[@]}" volume rm "${sensor_database_volumes[@]}"
fi
