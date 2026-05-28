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

    COMPOSE_CMD="docker compose -f infra/video-analytics-api-infra.yml -f apps/video-analytics-api-app.yml down --volumes --rmi all"
    if $COMPOSE_CMD; then
        echo "✓ Docker Compose down successfully"
    else
        echo "✗ Docker Compose down failed"
        return 1
    fi

    docker volume prune -f 2>/dev/null || true
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
