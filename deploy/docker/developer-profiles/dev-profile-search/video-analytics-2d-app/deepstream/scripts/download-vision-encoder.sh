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

# Init-container script for the search profile.
#
# Downloads the selected vision encoder model from NGC into /opt/storage
# (bind-mounted to $MDX_DATA_DIR/models/ on the host). Idempotent -- skips
# download when a marker file from a previous run is found.
#
# Expected env vars (set by compose):
#   VISION_ENCODER_NGC_ORG, VISION_ENCODER_NGC_TEAM,
#   VISION_ENCODER_MODEL, VISION_ENCODER_VERSION,
#   VISION_ENCODER_ONNX_FILE,
#   NGC_CLI_API_KEY,
#   STORAGE_UID (optional, default 1001) — UID of the perception container user
#   STORAGE_GID (optional, default 1001) — GID of the perception container user

set -euo pipefail

NGC_ORG="${VISION_ENCODER_NGC_ORG:?must be set}"
NGC_TEAM="${VISION_ENCODER_NGC_TEAM:?must be set}"
MODEL="${VISION_ENCODER_MODEL:?must be set}"
VERSION="${VISION_ENCODER_VERSION:?must be set}"
ONNX_FILE="${VISION_ENCODER_ONNX_FILE:?must be set}"
TOKENIZER_DIR="${VISION_ENCODER_TOKENIZER_DIR:?must be set}"

NGC_MODEL="${NGC_ORG}/${NGC_TEAM}/${MODEL}:${VERSION}"
DEST="/opt/storage"

MARKER="${DEST}/.${MODEL}_${VERSION}.done"
if [[ -f "$MARKER" ]]; then
  echo "##### Vision encoder ${MODEL}:${VERSION} already present, skipping download #####"
  exit 0
fi

# ---------------------------------------------------------------------------
# Ensure NGC CLI is available
# ---------------------------------------------------------------------------
if ! command -v ngc &>/dev/null; then
  echo "##### NGC CLI not found, installing... #####"
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq && apt-get install -y -qq wget unzip > /dev/null
  cd /tmp
  wget -q https://ngc.nvidia.com/downloads/ngccli_linux.zip -O ngccli_linux.zip
  unzip -q ngccli_linux.zip && chmod +x ngc-cli/ngc
  export PATH="/tmp/ngc-cli:$PATH"
  cd -
  ngc --version
fi

# ---------------------------------------------------------------------------
# Download model
# ---------------------------------------------------------------------------
STAGING_DIR=$(mktemp -d)
echo "##### Downloading vision encoder: ${NGC_MODEL} -> ${STAGING_DIR} #####"
ngc registry model download-version "${NGC_MODEL}" --org "${NGC_ORG}" --dest "${STAGING_DIR}"

# NGC extracts into a subdirectory whose name can vary. Find it dynamically
# rather than guessing the format.
EXTRACT_DIR=$(find "${STAGING_DIR}" -mindepth 1 -maxdepth 1 -type d | head -1)
if [[ -z "${EXTRACT_DIR}" ]]; then
  echo "ERROR: NGC download produced no subdirectory in ${STAGING_DIR}"
  ls -la "${STAGING_DIR}"
  exit 1
fi

cp -a "${EXTRACT_DIR}"/. "${DEST}/"
rm -rf "${STAGING_DIR}"

# ---------------------------------------------------------------------------
# Validate expected artifact landed before writing the marker
# ---------------------------------------------------------------------------
if [[ ! -f "${DEST}/${ONNX_FILE}" ]]; then
  echo "ERROR: Expected ONNX file ${ONNX_FILE} not found in ${DEST} after download"
  echo "Contents of ${DEST}:"
  ls -la "${DEST}"
  exit 1
fi

if [[ ! -d "${DEST}/${TOKENIZER_DIR}" ]]; then
  echo "ERROR: Expected tokenizer directory ${TOKENIZER_DIR} not found in ${DEST} after download"
  echo "Contents of ${DEST}:"
  ls -la "${DEST}"
  exit 1
fi


STORAGE_UID="${STORAGE_UID:-1001}"
STORAGE_GID="${STORAGE_GID:-1001}"

# Make the storage tree accessible to the perception container, whose
# UID/GID are baked into the vss-rt-cv image and cannot be changed.
# Since we cannot align UIDs or inject supplementary groups, "other"
# bits are the only access channel:
#   - directories: o+rwx  (TensorRT writes engine plans here at runtime)
#   - files:       o+r    (read-only model artifacts)
chown -R "${STORAGE_UID}:${STORAGE_GID}" "${DEST}"
find "${DEST}" -type d -exec chmod 0777 {} +
find "${DEST}" -type f -exec chmod 0644 {} +

touch "${MARKER}"
chown "${STORAGE_UID}:${STORAGE_GID}" "${MARKER}"
echo "##### Vision encoder ${MODEL}:${VERSION} downloaded to ${DEST} #####"
ls -lhR "${DEST}"
echo "##### Marker: ${MARKER} #####"
ls -lh "${MARKER}"
