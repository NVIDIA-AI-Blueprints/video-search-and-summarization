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

cd "$COMPOSE_DIR"
# Pull the promoted BA image above. `up --build` only builds Compose services
# that have a Dockerfile (broker-health-check, elasticsearch, kafka-topic-init).
# analytics-stream has `image:` only, so it keeps the pulled tag. Playback stays
# stopped until measure.sh.
compose up -d --build analytics-stream
