#!/bin/bash

# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
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

# Phase 0: manifest-driven NGC model acquisition (replaces Compose/Helm download init).
ensure_models_from_manifest() {
    local mode="${DS_MODEL_DOWNLOAD:-auto}"
    [[ "$mode" == "never" ]] && return 0

    local manifest="${MODELS_MANIFEST_PATH:-}"
    if [[ -z "$manifest" || ! -f "$manifest" ]]; then
        if [[ "$mode" == "auto" ]]; then
            return 0
        fi
        echo "ERROR: MODELS_MANIFEST_PATH must point to an existing manifest when DS_MODEL_DOWNLOAD=${mode}" >&2
        exit 1
    fi

    if [[ "$(id -u)" -ne 0 ]]; then
        echo "ERROR: model download requires root (Option A); start the container as UID 0" >&2
        exit 1
    fi

    local script="${DOWNLOAD_MODELS_SCRIPT:-}"
    if [[ -z "$script" || ! -f "$script" ]]; then
        for candidate in /opt/scripts/download-models.sh /startup-script/download-models.sh; do
            if [[ -f "$candidate" ]]; then
                script="$candidate"
                break
            fi
        done
    fi
    if [[ -z "$script" || ! -f "$script" ]]; then
        echo "ERROR: download-models.sh not found (expected /opt/scripts or /startup-script)" >&2
        exit 1
    fi

    echo "##### Model download phase (manifest=${manifest}, script=${script}) #####"
    bash "$script"
}

exec_as_runtime_user() {
    local uid="${STORAGE_UID:-1001}"
    local gid="${STORAGE_GID:-1001}"
    if [[ "$(id -u)" -ne 0 ]]; then
        exec "$@"
    fi
    echo "##### Dropping privileges to ${uid}:${gid} before application exec #####"
    if command -v setpriv >/dev/null 2>&1; then
        exec setpriv --reuid="$uid" --regid="$gid" --clear-groups -- "$@"
    fi
    if command -v runuser >/dev/null 2>&1; then
        exec runuser -u "#${uid}" -g "#${gid}" -- "$@"
    fi
    if command -v gosu >/dev/null 2>&1; then
        exec gosu "${uid}:${gid}" "$@"
    fi
    echo "ERROR: no privilege-drop tool found (need setpriv, runuser, or gosu)" >&2
    exit 1
}

ensure_models_from_manifest

MQTT_HOST=${MQTT_HOST:-mosquitto}
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
  sed -E "s|[^[:space:];]+:[0-9]+;|${MQTT_ENDPOINT};|g" "${PROVIDED_PUB_SUB}" > "${PUB_SUB_OUT}"
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
  exec_as_runtime_user ./metropolis_perception_app -c "${CONFIG_DIR}/ds-main-redis-config-mv3dt.txt" -m 1 -t 0 -l 5 --message-rate 1
else
  [ "${STREAM_TYPE}" = "kafka" ] || echo "STREAM_TYPE not set or invalid. Defaulting to kafka..."
  echo -e "\nRunning metropolis_perception_app with kafka (RT-DETR + MV3DT)..."
  echo -e "\nMain config:"
  cat "${CONFIG_DIR}/ds-main-config-mv3dt.txt"
  exec_as_runtime_user ./metropolis_perception_app -c "${CONFIG_DIR}/ds-main-config-mv3dt.txt" -m 1 -t 0 -l 5 --message-rate 1
fi
