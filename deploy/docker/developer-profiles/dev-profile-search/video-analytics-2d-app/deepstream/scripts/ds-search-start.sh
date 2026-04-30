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

# Search-profile wrapper for DeepStream perception entrypoint.
#
# Patches [text-embedder] and [visionencoder] sections in ds-main-*.txt
# configs to point at the vision encoder model downloaded by the
# perception-2d-init container, then delegates to the shared ds-start.sh.

set -euo pipefail

VISION_ENCODER_MODEL="${VISION_ENCODER_MODEL:?must be set}"
VISION_ENCODER_VERSION="${VISION_ENCODER_VERSION:?must be set}"
VISION_ENCODER_ONNX_FILE="${VISION_ENCODER_ONNX_FILE:?must be set}"
VISION_ENCODER_TOKENIZER_DIR="${VISION_ENCODER_TOKENIZER_DIR:?must be set}"
VISION_ENCODER_DS_MODEL_NAME="${VISION_ENCODER_DS_MODEL_NAME:?must be set}"
VISION_ENCODER_STORAGE="/opt/storage"
DS_APP_DIR="/opt/nvidia/deepstream/deepstream/sources/apps/sample_apps/metropolis_perception_app"

# ---------------------------------------------------------------------------
# Verify model artifacts exist (downloaded by perception-2d-init container)
# ---------------------------------------------------------------------------
MARKER="${VISION_ENCODER_STORAGE}/.${VISION_ENCODER_MODEL}_${VISION_ENCODER_VERSION}.done"
if [[ ! -f "$MARKER" ]]; then
  echo "ERROR: Vision encoder marker not found at ${MARKER}"
  echo "The perception-2d-init container may not have completed successfully."
  exit 1
fi

if [[ ! -f "${VISION_ENCODER_STORAGE}/${VISION_ENCODER_ONNX_FILE}" ]]; then
  echo "ERROR: Expected ONNX model not found at ${VISION_ENCODER_STORAGE}/${VISION_ENCODER_ONNX_FILE}"
  exit 1
fi

# ---------------------------------------------------------------------------
# Stage config files from the read-only bind mount into the DeepStream app
# directory. The shared ds-start.sh expects config files and the
# metropolis_perception_app binary in the current working directory.
# ---------------------------------------------------------------------------
CONFIGS_RO="/opt/ds-configs-ro"
CONFIGS_DIR="${DS_APP_DIR}"
if [[ ! -d "${CONFIGS_RO}" ]]; then
  echo "ERROR: Read-only configs mount not found at ${CONFIGS_RO}"
  echo "Ensure the compose volume mounts the host configs to ${CONFIGS_RO}:ro"
  exit 1
fi
mkdir -p "${CONFIGS_DIR}"
cp -a "${CONFIGS_RO}/." "${CONFIGS_DIR}/"
echo "##### Staged config files from ${CONFIGS_RO} -> ${CONFIGS_DIR} #####"

# ---------------------------------------------------------------------------
# Patch ds-main-*.txt config files with vision encoder paths.
# ---------------------------------------------------------------------------
for cfg in "${CONFIGS_DIR}/ds-main-config.txt" \
           "${CONFIGS_DIR}/ds-main-redis-config.txt"; do
  [[ -f "$cfg" ]] || continue
  echo "##### Patching vision encoder paths in $(basename "$cfg") #####"

  # [text-embedder] section
  sed -i "/^\[text-embedder\]/,/^\[/{s|^model-name=.*|model-name=${VISION_ENCODER_DS_MODEL_NAME}|;}" "$cfg"
  sed -i "/^\[text-embedder\]/,/^\[/{s|^onnx-model-path=.*|onnx-model-path=${VISION_ENCODER_STORAGE}/${VISION_ENCODER_ONNX_FILE}|;}" "$cfg"
  sed -i "/^\[text-embedder\]/,/^\[/{s|^tokenizer-dir=.*|tokenizer-dir=${VISION_ENCODER_STORAGE}/${VISION_ENCODER_TOKENIZER_DIR}/|;}" "$cfg"

  # [visionencoder] section
  sed -i "/^\[visionencoder\]/,/^\[/{s|^onnx-model=.*|onnx-model=${VISION_ENCODER_STORAGE}/${VISION_ENCODER_ONNX_FILE}|;}" "$cfg"
  sed -i "/^\[visionencoder\]/,/^\[/{s|^tensorrt-engine=.*|tensorrt-engine=${VISION_ENCODER_STORAGE}/${VISION_ENCODER_ONNX_FILE}_batch16.plan|;}" "$cfg"
done

# ---------------------------------------------------------------------------
# Delegate to the shared ds-start.sh
# ---------------------------------------------------------------------------
cd "${DS_APP_DIR}"
exec bash "${DS_APP_DIR}/ds-start.sh"
