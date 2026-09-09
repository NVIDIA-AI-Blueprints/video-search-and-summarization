#!/bin/bash

# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

# Clean up Docker Compose and volumes for video-analytics-api integration tests.
# Usage: ./cleanup.sh   or  source cleanup.sh  (after generate_env.sh and .env)

cleanup_docker_environment() {
    echo "Stopping video-analytics-api integration stack..."
    cd "$INTEGRATION_TEST_DIR/docker_compose"

    # No --rmi: the Elasticsearch image is the deployment's own, shared with any
    # stack the developer is running outside this suite. --volumes still drops
    # this project's data and log volumes, so each run starts on an empty index.
    COMPOSE_CMD="docker compose --project-directory $INFRA_DIR -f $INFRA_DIR/compose.yml -f infra/video-analytics-api-infra.yml -f apps/video-analytics-api-app.yml down --volumes"
    if $COMPOSE_CMD; then
        echo "✓ Docker Compose down successfully"
    else
        echo "✗ Docker Compose down failed"
        return 1
    fi

    # Scoped to this project rather than `docker volume prune -f`, which would
    # reap unrelated volumes on a developer machine running other stacks.
    docker volume ls -q --filter "label=com.docker.compose.project=${COMPOSE_PROJECT_NAME:-video-analytics-api-integration}" \
        | xargs -r docker volume rm -f >/dev/null 2>&1 || true
    echo "✓ Cleanup complete"
    return 0
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    INTEGRATION_TEST_DIR="$SCRIPT_DIR"
    source "$SCRIPT_DIR/generate_env.sh"
    . "$SCRIPT_DIR/docker_compose/infra/.env"
    cleanup_docker_environment
fi
