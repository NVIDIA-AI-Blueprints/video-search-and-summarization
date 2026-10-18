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
#   - Run directly: ./generate_env.sh [profile1] [profile2]
#   - Source from the test.sh script or cleanup.sh: source generate_env.sh
# This script generates the .env file for the MDX Docker Compose setup

# If script is run directly (not sourced), get SCRIPT_DIR and PROFILE1, PROFILE2
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    # Get the directory where this script is located
    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

    # Script is being executed directly, process arguments
    PROFILE1=${1:-warehouse_2d}  # Use first argument or default to warehouse_2d
    PROFILE2=${2:-kafka}  # Use second argument or default to kafka
fi

# Parse APP_NAME and APP_MODE from PROFILE1
if [[ "$PROFILE1" == *"_2d" ]]; then
    APP_NAME="${PROFILE1%_2d}"
    APP_MODE="_2d"
elif [[ "$PROFILE1" == *"_3d" ]]; then
    APP_NAME="${PROFILE1%_3d}"
    APP_MODE="_3d"
else
    # For profiles without _2d or _3d suffix (like "smart_city")
    APP_NAME="$PROFILE1"
    APP_MODE=""
fi

# Map test-profile APP_NAME to the source-tree app dir/filename prefix.
# Test fixture dirs stay named after APP_NAME (warehouse_2d, etc.), but the
# Python source lives under apps/<APP_DIR>/main_<APP_DIR><APP_MODE>_app.py.
if [[ "$APP_NAME" = "warehouse" ]]; then
    APP_DIR="analytics"
else
    APP_DIR="$APP_NAME"
fi

# Logstash reads whichever broker the app writes to. mqtt is bridged into kafka.
if [[ "$PROFILE2" = "redis" ]]; then
    STREAM_TYPE="redis"
else
    STREAM_TYPE="kafka"
fi

COMPOSE_PROFILES="elasticsearch,elasticsearch-init-container,kibana,logstash,broker-health-check"
if [[ "$STREAM_TYPE" = "redis" ]]; then
    COMPOSE_PROFILES="$COMPOSE_PROFILES,redis"
else
    COMPOSE_PROFILES="$COMPOSE_PROFILES,kafka,kafka-topic-init-container"
fi
# mosquitto is the deployment's broker; mqtt selects this suite's own bridge.
if [[ "$PROFILE2" = "mqtt" ]]; then
    COMPOSE_PROFILES="$COMPOSE_PROFILES,mosquitto,mqtt"
fi

# Set PLAYBACK_MODE based on profile: '--playback-from-json' for 'smart_city', empty otherwise
if [[ "$APP_NAME" = "smart_city" ]]; then
    PLAYBACK_MODE="--playback-from-json"
else
    PLAYBACK_MODE=""
fi

# The stack runs on the Compose bridge network. Kafka advertises two listeners:
# INTERNAL (kafka:29092) for in-network clients, and EXTERNAL (HOST_IP:9092) for
# the host-side harness reaching the published port. HOST_IP therefore only ever
# names the host, never a container.
HOST_IP="localhost"

# Create/populate the .env file based on profiles
ENV_FILE="$SCRIPT_DIR/docker_compose/infra/.env"
echo "Generating environment variables at $ENV_FILE"

# Resolve paths in this shell first. The unquoted heredoc below interpolates
# ${REPO_ROOT} from the caller; with set -u (Jenkins deploy.sh) that is fatal
# if the assignment only exists as text inside the .env body.
REPO_ROOT="$(git -C "${SCRIPT_DIR}" rev-parse --show-toplevel 2>/dev/null || true)"
if [[ -z "${REPO_ROOT}" ]]; then
    REPO_ROOT="$(cd "${SCRIPT_DIR}/../../../../.." && pwd)"
fi
# Full clones use deploy/. Jenkins GPU sparse-checkout may omit it; compose/docker
# is the playback infra mirror on the GitHub branch.
if [[ -f "${REPO_ROOT}/deploy/docker/services/infra/compose.yml" ]]; then
    VSS_APPS_DIR="${REPO_ROOT}/deploy/docker"
elif [[ -f "${REPO_ROOT}/compose/docker/services/infra/compose.yml" ]]; then
    VSS_APPS_DIR="${REPO_ROOT}/compose/docker"
else
    echo "Missing deploy/docker/services/infra/compose.yml (and compose/docker fallback)." >&2
    exit 1
fi
INFRA_DIR="${VSS_APPS_DIR}/services/infra"
# Playback fixtures stay in git (MDX_DATA_DIR). Kafka/ES bind mounts use
# VSS_DATA_DIR. Jenkins #62: Elasticsearch lucene files under the GPU
# checkout cannot be deleteDir'd (uid 1000, Operation not permitted).
if [[ -n "${BA_PERF_DATA_DIR:-}" ]]; then
    VSS_DATA_DIR="$BA_PERF_DATA_DIR"
    COMPOSE_PROJECT_NAME="${COMPOSE_PROJECT_NAME:-ba-perf}"
else
    VSS_DATA_DIR="${SCRIPT_DIR}/docker_compose/apps_data"
    COMPOSE_PROJECT_NAME="${COMPOSE_PROJECT_NAME:-mdx}"
fi
PROJ_ROOT_DIR="$(cd "${SCRIPT_DIR}/../.." && pwd)"
MDX_SAMPLE_APPS_DIR="${SCRIPT_DIR}/docker_compose"
MDX_DATA_DIR="${SCRIPT_DIR}/docker_compose/apps_data"
# Compose `include:` reads these from the process environment, not only --env-file.
export REPO_ROOT VSS_APPS_DIR INFRA_DIR VSS_DATA_DIR MDX_SAMPLE_APPS_DIR COMPOSE_PROJECT_NAME

mkdir -p "$(dirname "$ENV_FILE")"
cat > "$ENV_FILE" << EOF
# Auto-generated environment variables for MDX Docker Compose setup
# Generated by generate_env.sh on $(date)

# Application configuration from PROFILE1: $PROFILE1
APP_NAME="$APP_NAME"
APP_MODE="$APP_MODE"
APP_DIR="$APP_DIR"

# Streaming service from PROFILE2: $PROFILE2
STREAMING_SERVICE="$PROFILE2"

# Which broker logstash consumes from, selecting the shared pipeline set in
# deploy/docker/services/infra/elk/logstash/pipelines/. The mqtt profile bridges
# into kafka, so only the redis profile changes the pipeline.
STREAM_TYPE="$STREAM_TYPE"

# Playback mode configuration
PLAYBACK_MODE="$PLAYBACK_MODE"

# Path configurations based on SCRIPT_DIR
PROJ_ROOT_DIR="$PROJ_ROOT_DIR"
MDX_SAMPLE_APPS_DIR="$MDX_SAMPLE_APPS_DIR"
MDX_DATA_DIR="$MDX_DATA_DIR"

# Shared infra assets (ELK/Kafka/Redis configs, ES init scripts, protobuf
# definitions) are single-sourced from the deployment tree rather than
# duplicated under tests/. See deploy/docker/services/infra/.
REPO_ROOT="$REPO_ROOT"
INFRA_DIR="$INFRA_DIR"
VSS_APPS_DIR="$VSS_APPS_DIR"
VSS_DATA_DIR="$VSS_DATA_DIR"

# Service selection. The deployment tags every service with its own name, so the
# suite names exactly what it needs; HAProxy, the SDR controller and the rest of
# the deployment stay defined but unstarted. mqtt additionally brings up its own
# broker and bridge, and still feeds the kafka pipeline.
COMPOSE_PROFILES="$COMPOSE_PROFILES"

# Host configuration
HOST_IP="$HOST_IP"

# Volume Creation Variable for appending the volume name
COMPOSE_PROJECT_NAME="$COMPOSE_PROJECT_NAME"
EOF

echo "Generated environment file:"
echo "--------------------------------"
cat "$ENV_FILE"
echo "--------------------------------" 