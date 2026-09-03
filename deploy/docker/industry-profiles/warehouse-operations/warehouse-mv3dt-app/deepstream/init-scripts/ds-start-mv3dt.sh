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

# Supplementary groups the runtime user needs for GPU access. Tegra ships
# /dev/nvmap and /dev/nvhost-* group-restricted, so --clear-groups costs the
# app CUDA entirely (NvRmMemInitNvmap "Permission denied" -> cudaErrorNoDevice).
# gids are read off the injected nodes — names/numbers differ across L4T/SBSA/x86.
# A supplementary gid 0 is legitimate: group access to a root:root 0660 node
# without granting uid 0.
collect_gpu_device_gids() {
    local -n _gids_ref="$1"
    local node gid seen=" "

    shopt -s nullglob
    for node in /dev/nvmap /dev/nvhost-* /dev/nvgpu/*/* /dev/nvsciipc* \
                /dev/nvidia[0-9]* /dev/nvidiactl /dev/nvidia-uvm*; do
        [[ -c "$node" || -b "$node" ]] || continue
        gid="$(stat -c '%g' "$node" 2>/dev/null || true)"
        [[ -n "$gid" ]] || continue
        if [[ "$seen" == *" ${gid} "* ]]; then
            continue
        fi
        seen+="${gid} "
        _gids_ref+=("$gid")
    done
    shopt -u nullglob
}

# Confirm the dropped identity can open the GPU device nodes. Used to decide
# between --clear-groups (x86 / world-accessible) and --groups (Tegra).
# Quiet on failure — the caller logs once when neither drop path works.
runtime_user_can_reach_gpu() {
    local -a priv_opts=("$@")
    local node

    for node in /dev/nvmap /dev/nvidiactl; do
        [[ -c "$node" ]] || continue
        if ! setpriv "${priv_opts[@]}" -- test -r "$node" ||
           ! setpriv "${priv_opts[@]}" -- test -w "$node"; then
            return 1
        fi
    done
    return 0
}

# Prefer --clear-groups (prior x86 path). Only grant device groups when that
# leaves the GPU unreachable. RTVI_CV_PRIVILEGE_DROP: auto (default; fall back
# to root if neither drop works), force (exit 1 instead), off (never drop).
# Override via env / overrides.env — no compose wiring required.
exec_as_runtime_user() {
    local uid="${STORAGE_UID:-1001}"
    local gid="${STORAGE_GID:-1001}"
    local mode="${RTVI_CV_PRIVILEGE_DROP:-auto}"
    local groups_csv="" g node
    local -a gpu_gids=() priv_opts=() supp_opts=()

    if [[ "$(id -u)" -ne 0 ]]; then
        exec "$@"
    fi
    if [[ "$mode" == "off" ]]; then
        echo "##### RTVI_CV_PRIVILEGE_DROP=off; running the application as root #####"
        exec "$@"
    fi

    collect_gpu_device_gids gpu_gids
    if [[ ${#gpu_gids[@]} -gt 0 ]]; then
        groups_csv="$(IFS=,; echo "${gpu_gids[*]}")"
    fi

    if command -v setpriv >/dev/null 2>&1; then
        # Path 1: identical to pre-Tegra Option A. Succeeds on x86/SBSA where
        # /dev/nvidia* is typically world-accessible.
        priv_opts=(--reuid="$uid" --regid="$gid" --clear-groups)
        if runtime_user_can_reach_gpu "${priv_opts[@]}"; then
            echo "##### Dropping privileges to ${uid}:${gid} before application exec #####"
            exec setpriv "${priv_opts[@]}" -- "$@"
        fi

        # Path 2: Tegra — grant the gids that own the injected device nodes.
        if [[ -n "$groups_csv" ]]; then
            priv_opts=(--reuid="$uid" --regid="$gid" --groups "$groups_csv")
            if runtime_user_can_reach_gpu "${priv_opts[@]}"; then
                echo "##### Dropping privileges to ${uid}:${gid} (GPU groups: ${groups_csv}) before application exec #####"
                exec setpriv "${priv_opts[@]}" -- "$@"
            fi
        fi

        if [[ "$mode" == "force" ]]; then
            echo "ERROR: RTVI_CV_PRIVILEGE_DROP=force but ${uid}:${gid} cannot access the GPU devices" >&2
            for node in /dev/nvmap /dev/nvidiactl; do
                [[ -c "$node" ]] && ls -ld "$node" >&2 || true
            done
            exit 1
        fi
        echo "##### WARNING: ${uid}:${gid} cannot access the GPU devices; running as root instead. #####" >&2
        echo "#####          Files written to mounted volumes will be root-owned. #####" >&2
        for node in /dev/nvmap /dev/nvidiactl; do
            [[ -c "$node" ]] && ls -ld "$node" >&2 || true
        done
        exec "$@"
    fi

    # Fallbacks when setpriv is absent. runuser can take -G; gosu cannot.
    echo "##### Dropping privileges to ${uid}:${gid} before application exec #####"
    if command -v runuser >/dev/null 2>&1; then
        if [[ ${#gpu_gids[@]} -eq 0 ]]; then
            exec runuser -u "#${uid}" -g "#${gid}" -- "$@"
        fi
        for g in "${gpu_gids[@]}"; do
            supp_opts+=(-G "$g")
        done
        exec runuser -u "#${uid}" -g "#${gid}" "${supp_opts[@]}" -- "$@"
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

# The shipped configs name four cameras. A dataset with a different camera
# count needs the tracker's cameraModelFilepath map and the MQTT pub/sub topics
# rebuilt from the calibration that produced /tmp/camInfo. Set
# MV3DT_DYNAMIC_CAMERA_CONFIG=false to use the shipped configs verbatim.
MV3DT_DYNAMIC_CAMERA_CONFIG=${MV3DT_DYNAMIC_CAMERA_CONFIG:-true}

mapfile -t CAM_NAMES < <(for f in /tmp/camInfo/*.yml; do [ -e "${f}" ] || continue; basename "${f}" .yml; done | sort -V)

EFFECTIVE_CONFIG_DIR="${CONFIG_DIR}"
if [ "${MV3DT_DYNAMIC_CAMERA_CONFIG}" = "true" ]; then
  [ ${#CAM_NAMES[@]} -gt 0 ] || { echo "ERROR: No camera info files found under /tmp/camInfo"; exit 1; }
  echo "Dynamic camera config: ${#CAM_NAMES[@]} camera(s) from calibration (${CAM_NAMES[*]})."

  # Batch size is derived from NUM_STREAMS by the blueprint configurator, which
  # writes both max-batch-size here and the matching _b<n>_ engine name into
  # ds-pgie-config.yml. A calibration with more cameras than that means
  # NUM_STREAMS disagrees with the dataset, so fail fast rather than let
  # streammux silently drop the extra sources.
  MAX_BATCH=$(grep -oE "^max-batch-size=[0-9]+" "${CONFIG_DIR}/ds-main-config-mv3dt.txt" | head -1 | cut -d= -f2)
  if [ -n "${MAX_BATCH}" ] && [ ${#CAM_NAMES[@]} -gt "${MAX_BATCH}" ]; then
    echo "ERROR: calibration has ${#CAM_NAMES[@]} cameras but DS max-batch-size is ${MAX_BATCH}."
    echo "       Set NUM_STREAMS=${#CAM_NAMES[@]} in overrides.env and re-run the blueprint"
    echo "       configurator; it updates max-batch-size and the pgie engine name together."
    echo "       DeepStream builds the _b${#CAM_NAMES[@]}_ engine from the ONNX on first run if absent."
    echo "       Note NUM_STREAMS is capped by the hardware profile's max_streams_supported."
    exit 1
  fi

  # Work on a copy: the config directory is bind-mounted from the deployment
  # tree, so editing in place would modify the checked-out sources. Copying the
  # whole directory keeps every relative reference inside the main config --
  # ds-pgie-config.yml, ds-kafka-config.txt, ll-config-file -- resolving beside
  # it, so only the tracker map has to change.
  EFFECTIVE_CONFIG_DIR="${GENERATED_DIR}/configs"
  rm -rf "${EFFECTIVE_CONFIG_DIR}"
  mkdir -p "${EFFECTIVE_CONFIG_DIR}"
  cp -a "${CONFIG_DIR}/." "${EFFECTIVE_CONFIG_DIR}/"
  # The shipped pub/sub file names a fixed camera set and is regenerated below
  # at the absolute path the tracker's pubSubInfoConfigPath points to. Drop the
  # stale copy so a wrong-camera-count file cannot be picked up from here.
  rm -f "${EFFECTIVE_CONFIG_DIR}/pub_sub_info_config.yml"

  # Rebuild the tracker cameraModelFilepath map (sensor ids come from
  # calibration). pubSubInfoConfigPath and mqttProtoAdaptorConfigPath inside the
  # tracker config are absolute, so they still resolve from the copy.
  CAM_ENTRIES=""
  for cam in "${CAM_NAMES[@]}"; do
    CAM_ENTRIES+="    ${cam}: /tmp/camInfo/${cam}.yml"$'\n'
  done

  # Drop the old cameraModelFilepath block up to the next key at 2-space or
  # top-level indent, then splice in the dynamic entries.
  awk -v entries="${CAM_ENTRIES}" '
    /^  cameraModelFilepath:/ { print; printf "%s", entries; inblock=1; next }
    inblock && /^ ? ?[^ ]/ { inblock=0 }
    inblock { next }
    { print }
  ' "${CONFIG_DIR}/ds-mv3dt-tracker-config.yml" > "${EFFECTIVE_CONFIG_DIR}/ds-mv3dt-tracker-config.yml"
  grep -q "^    .*: /tmp/camInfo/" "${EFFECTIVE_CONFIG_DIR}/ds-mv3dt-tracker-config.yml" \
    || { echo "ERROR: failed to inject cameraModelFilepath entries into the tracker config"; exit 1; }

  echo -e "\nTracker cameraModelFilepath map:"
  sed -n '/^  cameraModelFilepath:/,/^  [^ ]/p' "${EFFECTIVE_CONFIG_DIR}/ds-mv3dt-tracker-config.yml"
fi

echo "Generating MQTT pub/sub config..."
PROVIDED_PUB_SUB=""
if [ "${MV3DT_DYNAMIC_CAMERA_CONFIG}" != "true" ]; then
  # Only honour the shipped file when not deriving from calibration: it names a
  # fixed camera set, which is the thing dynamic mode exists to replace.
  for candidate in "${CONFIG_DIR}/pub_sub_info_config.yml"; do
    [ -f "${candidate}" ] && PROVIDED_PUB_SUB="${candidate}" && break
  done
fi

if [ -n "${PROVIDED_PUB_SUB}" ]; then
  echo "Using provided pub/sub config: ${PROVIDED_PUB_SUB} (rewriting host:port to ${MQTT_ENDPOINT})"
  sed -E "s|[^[:space:];]+:[0-9]+;|${MQTT_ENDPOINT};|g" "${PROVIDED_PUB_SUB}" > "${PUB_SUB_OUT}"
else
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
cat "${EFFECTIVE_CONFIG_DIR}/ds-pgie-config.yml"

echo -e "\nTracker config:"
cat "${EFFECTIVE_CONFIG_DIR}/ds-mv3dt-tracker-config.yml"

echo -e "\nRunning metropolis_perception_app with ${STREAM_TYPE} (RT-DETR + MV3DT)..."
echo -e "\nMain config:"
cat "${EFFECTIVE_CONFIG_DIR}/ds-main-config-mv3dt.txt"
exec_as_runtime_user ./metropolis_perception_app -c "${EFFECTIVE_CONFIG_DIR}/ds-main-config-mv3dt.txt" -m 1 -t 0 -l 5 --message-rate 1
