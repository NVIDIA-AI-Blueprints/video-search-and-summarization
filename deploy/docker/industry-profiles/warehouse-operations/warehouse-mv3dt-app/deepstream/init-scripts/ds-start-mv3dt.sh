#!/bin/bash

# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# RT-DETR + MV3DT pipeline start script.
# Works for both single-container and multi-container deployments.
#
# Environment variables (set by compose):
#   INSTANCE_ID     - Container index (0, 1, ...). Default: 0
#   DS_HTTP_PORT    - HTTP port for NvMultiURISrcBin stream add API. Default: 9000
#   BATCH_SIZE      - Number of cameras for this container. Default: 4
#   MQTT_BROKERS    - Comma-separated broker addresses for all instances. Default: 127.0.0.1:1884
#
# Generated files (written to /tmp/generated/, bind-mounted to deepstream/configs/generated/):
#   ds-ppl-analytics-pgie-config-b<N>.yml  - batch-specific pgie config
#   pub_sub_info_config.yml                - cross-camera pub/sub config
#   ds-main-config-instance-<N>.txt        - per-instance patched DeepStream main config

INSTANCE_ID=${INSTANCE_ID:-0}
DS_HTTP_PORT=${DS_HTTP_PORT:-9000}
BATCH_SIZE=${BATCH_SIZE:-4}
# MAX_BATCH_SIZE: largest batch across all instances — selects the correct pre-built pgie engine file.
# For uneven distributions (e.g. 10+9+9) this is always container 0's batch size.
MAX_BATCH_SIZE=${MAX_BATCH_SIZE:-${BATCH_SIZE}}
MQTT_BROKERS=${MQTT_BROKERS:-127.0.0.1:1884}

echo "##### RT-DETR + MV3DT pipeline (instance ${INSTANCE_ID}) #####"
echo "  HTTP port      : ${DS_HTTP_PORT}"
echo "  Batch size     : ${BATCH_SIZE}"
echo "  Max batch size : ${MAX_BATCH_SIZE}"
echo "  MQTT           : ${MQTT_BROKERS}"

cd /opt/nvidia/deepstream/deepstream/sources/apps/sample_apps/metropolis_perception_app
APP_DIR="$(pwd)"

GENERATED_DIR="/tmp/generated"
mkdir -p "${GENERATED_DIR}"

# ── Shared configs (same content on every instance) ──────────────────────────

PGIE_OUT="${GENERATED_DIR}/ds-ppl-analytics-pgie-config-b${MAX_BATCH_SIZE}.yml"

echo "Generating pgie config for batch=${MAX_BATCH_SIZE}..."
sed "s|_b[0-9]*_gpu[0-9]*_fp16\.engine|_b${MAX_BATCH_SIZE}_gpu0_fp16.engine|g" \
    ds-ppl-analytics-pgie-config.yml > "${PGIE_OUT}"
sed -i "s|labelfile-path: \([^/]\)|labelfile-path: ${APP_DIR}/\1|" "${PGIE_OUT}"

echo "Generating pub/sub config..."
NEIGHBOR_CRITERIA="overlap_threshold:$(python3 -c 'print("%f" % (2 / (1920 * 1080)))')"
python3 generate_pub_sub_configs.py \
    --cam_info_path /tmp/camInfo \
    --mqtt_brokers "${MQTT_BROKERS}" \
    --output_path "${GENERATED_DIR}" \
    --neighbor_criteria "${NEIGHBOR_CRITERIA}" \
    || { echo "ERROR: pub/sub config generation failed, aborting."; exit 1; }

# ── Per-instance DeepStream main config ──────────────────────────────────────

if [ "${STREAM_TYPE}" = "redis" ]; then
    BASE_CONFIG="ds-main-redis-config-mv3dt.txt"
else
    BASE_CONFIG="ds-main-config-mv3dt.txt"
fi

INSTANCE_CONFIG="${GENERATED_DIR}/ds-main-config-instance-${INSTANCE_ID}.txt"
cp "${BASE_CONFIG}" "${INSTANCE_CONFIG}"

sed -i "s/^http-port=.*/http-port=${DS_HTTP_PORT}/"   "${INSTANCE_CONFIG}"
sed -i "s/^batch-size=.*/batch-size=${BATCH_SIZE}/"   "${INSTANCE_CONFIG}"
sed -i "s/^max-batch-size=.*/max-batch-size=${BATCH_SIZE}/" "${INSTANCE_CONFIG}"

# Point pgie entry to the generated batch-specific config
sed -i "s|^config-file=ds-ppl-analytics-pgie-config\.yml$|config-file=${PGIE_OUT}|" "${INSTANCE_CONFIG}"

# Make remaining relative paths absolute (config resolves from /tmp/generated/ at runtime)
sed -i "s|^config-file=\([^/]\)|config-file=${APP_DIR}/\1|"       "${INSTANCE_CONFIG}"
sed -i "s|^ll-config-file=\([^/]\)|ll-config-file=${APP_DIR}/\1|" "${INSTANCE_CONFIG}"
sed -i "s|^msg-broker-config=\([^/]\)|msg-broker-config=${APP_DIR}/\1|" "${INSTANCE_CONFIG}"

# ── Debug output ──────────────────────────────────────────────────────────────

echo -e "\nPub/sub config:"
cat "${GENERATED_DIR}/pub_sub_info_config.yml"

echo -e "\nTracker config:"
cat ds-mv3dt-tracker-config.yml

echo -e "\nMain config (patched for instance ${INSTANCE_ID}):"
cat "${INSTANCE_CONFIG}"

# ── Launch ────────────────────────────────────────────────────────────────────

echo -e "\nStarting metropolis_perception_app (instance ${INSTANCE_ID}) with ${STREAM_TYPE} configuration..."
./metropolis_perception_app -c "${INSTANCE_CONFIG}" -m 1 -t 0 -l 5 --message-rate 1 --tracker-reid
