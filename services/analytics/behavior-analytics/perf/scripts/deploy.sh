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

# Warehouse kafka playback sets behaviorEmitOnce=true, which holds each
# behavior until behaviorStateValidInterval (default 6s). Yun's 60/120 ms
# KPI is hypothesized to be per-batch (product default false). Copy the
# integration app settings into BA_PERF_DATA_DIR and overlay that copy so
# git fixtures stay true. Restore fixture behavior with
# BA_PERF_BEHAVIOR_EMIT_ONCE=true.
BA_PERF_DATA_DIR="$(prepare_benchmark_dir "$BA_PERF_DATA_DIR")"
export BA_PERF_DATA_DIR
app_src="$MDX_SAMPLE_APPS_DIR/apps/${APP_NAME}${APP_MODE}"
app_dst="$BA_PERF_DATA_DIR/apps/${APP_NAME}${APP_MODE}"
mkdir -p "$BA_PERF_DATA_DIR/apps"
rm -rf "$app_dst"
cp -a "$app_src" "$app_dst"
export MDX_SAMPLE_APPS_DIR="$BA_PERF_DATA_DIR"
config_file="$app_dst/analytics-stream-${STREAMING_SERVICE}-config.json"
python3 "$PERF_DIR/scripts/overlay_app_config.py" \
    "$config_file" \
    --behavior-emit-once "${BA_PERF_BEHAVIOR_EMIT_ONCE:-false}" \
    --stamp "$BA_PERF_OVERLAY_STAMP"

# Kafka and Elasticsearch run as uid 1000. Keep their bind mounts under
# /tmp (BA_PERF_DATA_DIR), not the Jenkins checkout. #62 failed at GPU
# deleteDir on leftover ES lucene files from #60.
mkdir -p \
    "$BA_PERF_DATA_DIR/data_log/kafka" \
    "$BA_PERF_DATA_DIR/data_log/elastic/data" \
    "$BA_PERF_DATA_DIR/data_log/elastic/logs"
chmod -R a+rwX "$BA_PERF_DATA_DIR"

cd "$COMPOSE_DIR"
# Remove only this benchmark's Compose resources by default. Legacy global
# mdx_* cleanup is available solely through an explicit opt-in.
compose down --remove-orphans -v >/dev/null 2>&1 || true
cleanup_legacy_mdx_volumes

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
if ! compose up -d --build --force-recreate $profile_services analytics-stream; then
    echo "Compose deploy failed; dumping status and logs." >&2
    compose ps -a >&2 || true
    compose logs --tail=200 >&2 || true
    exit 1
fi
