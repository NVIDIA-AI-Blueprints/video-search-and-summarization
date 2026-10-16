#!/bin/bash
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

source "$(dirname "${BASH_SOURCE[0]}")/common.sh"

export PROFILE1=warehouse_2d
export PROFILE2=kafka
export SCRIPT_DIR="$INTEGRATION_DIR"
export BUILD_SERVICE_IMAGE=false
export CURRENT_BEHAVIOR_ANALYTICS_IMAGE="$(resolve_image)"

# generate_env.sh writes docker_compose/infra/.env for the warehouse 2D Kafka profile.
source "$INTEGRATION_DIR/generate_env.sh"
load_compose_env
docker pull "$BEHAVIOR_ANALYTICS_IMAGE"

cd "$SERVICE_DIR/docker_compose"
# analytics-stream waits for the broker health check. Playback stays stopped.
compose up -d --no-build analytics-stream
