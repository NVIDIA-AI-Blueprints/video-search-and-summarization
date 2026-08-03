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

#
# RT-DETR + MV3DT pipeline start script for single-container deployment.
#
# Generated files:
#   /tmp/generated/pub_sub_info_config.yml

echo "##### RT-DETR + MV3DT pipeline #####"

ARCH="$(uname -m)"
# libgomp/libGLdispatch must load first to reserve static TLS; keep any
# preloads supplied by the image or operator after them.
MV3DT_PRELOAD="/usr/lib/${ARCH}-linux-gnu/libgomp.so.1:/usr/lib/${ARCH}-linux-gnu/libGLdispatch.so.0"
export LD_PRELOAD="${MV3DT_PRELOAD}${LD_PRELOAD:+:${LD_PRELOAD}}"

MQTT_HOST=${MQTT_HOST:-localhost}
MQTT_PORT=${MQTT_PORT:-1883}
MQTT_ENDPOINT="${MQTT_HOST}:${MQTT_PORT}"
cd /opt/nvidia/deepstream/deepstream/sources/apps/sample_apps/metropolis_perception_app
APP_DIR="$(pwd)"
CONFIG_DIR="${APP_DIR}/configs"

GENERATED_DIR="/tmp/generated"
mkdir -p "${GENERATED_DIR}"
PUB_SUB_OUT="${GENERATED_DIR}/pub_sub_info_config.yml"

echo "Generating MQTT pub/sub config..."
PROVIDED_PUB_SUB=""
for candidate in "${CONFIG_DIR}/pub_sub_info_config.yml"; do
  [ -f "${candidate}" ] && PROVIDED_PUB_SUB="${candidate}" && break
done

if [ -n "${PROVIDED_PUB_SUB}" ]; then
  echo "Using provided pub/sub config: ${PROVIDED_PUB_SUB} (rewriting host:port to ${MQTT_ENDPOINT})"
  sed -E "s|[a-zA-Z0-9._-]+:[0-9]+|${MQTT_ENDPOINT}|g" "${PROVIDED_PUB_SUB}" > "${PUB_SUB_OUT}"
else
  mapfile -t CAM_NAMES < <(for f in /tmp/camInfo/*.yml; do [ -e "${f}" ] || continue; basename "${f}" .yml; done | sort -V)
  [ ${#CAM_NAMES[@]} -gt 0 ] || { echo "ERROR: No camera info files found under /tmp/camInfo"; exit 1; }

  {
    echo "pubBrokerTopicStr:"
    for cam in "${CAM_NAMES[@]}"; do
      echo "  ${cam}: ${MQTT_ENDPOINT};/trck/${cam}"
    done
    echo "subPeerBrokerTopicStrs:"
    for cam in "${CAM_NAMES[@]}"; do
      echo "  ${cam}:"
      for peer in "${CAM_NAMES[@]}"; do
        [ "${peer}" != "${cam}" ] && echo "  - ${MQTT_ENDPOINT};/trck/${peer}"
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
  ./metropolis_perception_app -c "${CONFIG_DIR}/ds-main-redis-config-mv3dt.txt" -m 1 -t 0 -l 5 --message-rate 1 --tiledtext
else
  [ "${STREAM_TYPE}" = "kafka" ] || echo "STREAM_TYPE not set or invalid. Defaulting to kafka..."
  echo -e "\nRunning metropolis_perception_app with kafka (RT-DETR + MV3DT)..."
  echo -e "\nMain config:"
  cat "${CONFIG_DIR}/ds-main-config-mv3dt.txt"
  ./metropolis_perception_app -c "${CONFIG_DIR}/ds-main-config-mv3dt.txt" -m 1 -t 0 -l 5 --message-rate 1 --tiledtext
fi
