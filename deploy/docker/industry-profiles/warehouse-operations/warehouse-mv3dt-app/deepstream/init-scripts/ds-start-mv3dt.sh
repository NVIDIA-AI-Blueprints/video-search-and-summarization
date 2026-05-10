#!/bin/bash

# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# RT-DETR + MV3DT pipeline start script for single-container deployment.
#
# Generated files:
#   /tmp/generated/pub_sub_info_config.yml

echo "##### RT-DETR + MV3DT pipeline #####"

MQTT_BROKER=${MQTT_BROKER:-127.0.0.1:1883}
cd /opt/nvidia/deepstream/deepstream/sources/apps/sample_apps/metropolis_perception_app
APP_DIR="$(pwd)"
CONFIG_DIR="${APP_DIR}/configs"

GENERATED_DIR="/tmp/generated"
mkdir -p "${GENERATED_DIR}"
PUB_SUB_OUT="${GENERATED_DIR}/pub_sub_info_config.yml"
PRIMARY_PORT="${MQTT_BROKER##*:}"

echo "Generating MQTT pub/sub config..."
PROVIDED_PUB_SUB=""
for candidate in "${CONFIG_DIR}/pub_sub_info_config.yml"; do
  [ -f "${candidate}" ] && PROVIDED_PUB_SUB="${candidate}" && break
done

if [ -n "${PROVIDED_PUB_SUB}" ]; then
  echo "Using provided pub/sub config: ${PROVIDED_PUB_SUB} (replacing port only)"
  sed -E "s|127\.0\.0\.1:[0-9]+|127.0.0.1:${PRIMARY_PORT}|g" "${PROVIDED_PUB_SUB}" > "${PUB_SUB_OUT}"
else
  mapfile -t CAM_NAMES < <(for f in /tmp/camInfo/*.yml; do [ -e "${f}" ] || continue; basename "${f}" .yml; done | sort -V)
  [ ${#CAM_NAMES[@]} -gt 0 ] || { echo "ERROR: No camera info files found under /tmp/camInfo"; exit 1; }

  {
    echo "pubBrokerTopicStr:"
    for cam in "${CAM_NAMES[@]}"; do
      echo "  ${cam}: ${MQTT_BROKER};/trck/${cam}"
    done
    echo "subPeerBrokerTopicStrs:"
    for cam in "${CAM_NAMES[@]}"; do
      echo "  ${cam}:"
      for peer in "${CAM_NAMES[@]}"; do
        [ "${peer}" != "${cam}" ] && echo "  - ${MQTT_BROKER};/trck/${peer}"
      done
    done
  } > "${PUB_SUB_OUT}"
fi

echo -e "\nPub/sub config:"
cat "${PUB_SUB_OUT}"

echo -e "\nPGIE config:"
cat "${CONFIG_DIR}/ds-pgie-config.yml"

echo -e "\nTracker config:"
cat "${CONFIG_DIR}/ds-mv3dt-tracker-config.yml"

if [ "${STREAM_TYPE}" = "redis" ]; then
  echo -e "\nRunning metropolis_perception_app with redis (RT-DETR + MV3DT)..."
  echo -e "\nMain config:"
  cat "${CONFIG_DIR}/ds-main-redis-config-mv3dt.txt"
  ./metropolis_perception_app -c "${CONFIG_DIR}/ds-main-redis-config-mv3dt.txt" -m 1 -t 0 -l 5 --message-rate 1
else
  [ "${STREAM_TYPE}" = "kafka" ] || echo "STREAM_TYPE not set or invalid. Defaulting to kafka..."
  echo -e "\nRunning metropolis_perception_app with kafka (RT-DETR + MV3DT)..."
  echo -e "\nMain config:"
  cat "${CONFIG_DIR}/ds-main-config-mv3dt.txt"
  ./metropolis_perception_app -c "${CONFIG_DIR}/ds-main-config-mv3dt.txt" -m 1 -t 0 -l 5 --message-rate 1
fi
