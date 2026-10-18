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

# Kafka and Elasticsearch run as uid 1000 and bind-mount apps_data/data_log
# (755 in git). Jenkins #59: kafka could not write cluster_id; ES could not
# write gc.log. Match tests/integration/test.sh.
mkdir -p \
    "$MDX_DATA_DIR/data_log/kafka" \
    "$MDX_DATA_DIR/data_log/elastic/data" \
    "$MDX_DATA_DIR/data_log/elastic/logs"
chmod -R a+rwX "$MDX_DATA_DIR"

cd "$COMPOSE_DIR"
# Pull the promoted BA image above. `up --build` only builds Compose services
# that have a Dockerfile (broker-health-check, elasticsearch, kafka-topic-init).
# analytics-stream has `image:` only, so it keeps the pulled tag.
#
# `up analytics-stream` only starts that service and its depends_on
# (broker-health-check). Kafka is selected by COMPOSE_PROFILES, not depends_on,
# so Jenkins #58 left the broker down and the health check exited 1. Name the
# profile services plus analytics-stream. A bare `up` would also start
# analytics-stream-playback; that stays stopped until measure.sh.
profile_services="${COMPOSE_PROFILES//,/ }"
if ! compose up -d --build $profile_services analytics-stream; then
    echo "Compose deploy failed; dumping status and logs." >&2
    compose ps -a >&2 || true
    compose logs --tail=200 >&2 || true
    exit 1
fi
