#!/bin/bash
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

# Usage: 
#   - Run directly: ./cleanup.sh [profile1] [profile2]
#   - Source from test.sh script: source cleanup.sh
# This script performs Docker Compose down and cleanup operations

# Function to perform Docker Compose down and cleanup
cleanup_docker_environment() {
    echo "Using profile: $APP_NAME$APP_MODE $STREAMING_SERVICE"
    
    # Stop the Redis message extractor if it's running
    if [ ! -z "$EXTRACTOR_PID" ]; then
        echo "Stopping Redis message extractor (PID: $EXTRACTOR_PID)..."
        kill $EXTRACTOR_PID 2>/dev/null || echo "Extractor process already stopped"
    fi
    
    cd "$MDX_SAMPLE_APPS_DIR"
    
    echo "Docker Compose down..."
    
    # Build the docker compose command with optional profile
    COMPOSE_CMD="docker compose -f infra/compose.yml -f apps/mdx-apps.yml"
    if [ "$STREAMING_SERVICE" != "kafka" ]; then
        COMPOSE_CMD="$COMPOSE_CMD --profile $STREAMING_SERVICE"
    fi
    COMPOSE_CMD="$COMPOSE_CMD down --volumes"
    
    if $COMPOSE_CMD > /dev/null 2>&1; then
        echo "✓ Docker Compose down successfully"
    else
        echo "✗ Docker Compose failed to down"
        # Show the error by running the command again without suppressing output
        $COMPOSE_CMD
        return 1
    fi
    
    echo "Clean up..."
    docker volume prune -f
    bash ./cleanup_all_datalog.sh
    echo "Data log clean up complete"
    
    return 0
}

# If script is run directly (not sourced), execute the cleanup function
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    # Get the directory where this script is located
    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

    # Script is being executed directly, process arguments
    PROFILE1=${1:-warehouse_2d}  # Use first argument or default to warehouse_2d
    PROFILE2=${2:-kafka}  # Use second argument or default to kafka

    # Generate the .env file using the separate script
    source "$SCRIPT_DIR/generate_env.sh"

    # Source the environment file with proper path resolution
    . "$SCRIPT_DIR/docker_compose/infra/.env"

    cleanup_docker_environment
fi