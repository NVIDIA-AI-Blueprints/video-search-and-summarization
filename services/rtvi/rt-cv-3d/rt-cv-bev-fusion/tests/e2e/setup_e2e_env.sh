#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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
# Prepare the warehouse mv3dt deployment for the Tier-2 e2e test, non-interactively.
#
# Deploys via deploy/docker/scripts/blueprint-deploy.sh, which resolves the env layering.
#
# Required env:
#   NGC_CLI_API_KEY   - NGC key with access to nvstaging/vss-warehouse/*
#   HARDWARE_PROFILE  - GPU slug, e.g. RTXPRO6000BW (RTX PRO 6000), H100, L40S
# Optional env:
#   HOST_IP               (default: primary IP, resolved by blueprint-deploy.sh)
#   VSS_DATA_DIR          (default: the extracted app-data bundle)
#   VSS_WAREHOUSE_APP_DATA_VERSION  (default: v3.3.0-09152026)
#   BP_PROFILE            (default: bp_wh_kafka)
#   SAMPLE_VIDEO_DATASET  (default: the profile's own default)
#
# On success prints DEPLOY_ROOT / COMPOSE_FILE / ENV_FILE / DATA_DIR for the test.
set -euo pipefail

DOCKER_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../../../.." && pwd)/deploy/docker"
WAREHOUSE_DIR="${DOCKER_DIR}/industry-profiles/warehouse-operations"
APP_DATA_VER="${VSS_WAREHOUSE_APP_DATA_VERSION:-v3.3.0-09152026}"
# Videos, playback and calibration come from the NGC app-data resource.
DATA_DIR="${VSS_DATA_DIR:-${WORK_DIR:-$DOCKER_DIR}/vss-warehouse-app-data_v${APP_DATA_VER}/vss-warehouse-app-data}"
BP_PROFILE="${BP_PROFILE:-bp_wh_kafka}"

: "${NGC_CLI_API_KEY:?set NGC_CLI_API_KEY}"
: "${HARDWARE_PROFILE:?set HARDWARE_PROFILE}"
export NGC_CLI_API_KEY

banner() { echo "==================== $* ===================="; }

banner "Fetch warehouse app data (videos, playback, calibration)"
if [ -d "${DATA_DIR}/videos" ]; then
  echo "already present: ${DATA_DIR}"
else
  ( cd "${WORK_DIR:-$DOCKER_DIR}" \
    && ngc registry resource download-version "nvstaging/vss-warehouse/vss-warehouse-app-data:${APP_DATA_VER}" \
    && cd "vss-warehouse-app-data_v${APP_DATA_VER}" \
    && tar -xf vss-warehouse-app-data.tar.gz )
  mkdir -p "${DATA_DIR}/models" && chmod 0777 "${DATA_DIR}/models"
fi
[ -d "${DATA_DIR}/videos" ] || { echo "no videos under ${DATA_DIR}; nvstreamer will have nothing to serve" >&2; exit 1; }

banner "Deploy the warehouse mv3dt stack"
opts=(up -d warehouse -m mv3dt -p "${BP_PROFILE}" -D "${DATA_DIR}" -H "${HARDWARE_PROFILE}")
if [ -n "${HOST_IP:-}" ]; then opts+=(-i "${HOST_IP}"); fi
if [ -n "${SAMPLE_VIDEO_DATASET:-}" ]; then opts+=(-s "${SAMPLE_VIDEO_DATASET}"); fi
"${DOCKER_DIR}/scripts/blueprint-deploy.sh" "${opts[@]}"

banner "DONE - deployment prepared"
echo "DEPLOY_ROOT=${DOCKER_DIR}"
echo "COMPOSE_FILE=${DOCKER_DIR}/compose.yml"
echo "ENV_FILE=${WAREHOUSE_DIR}/generated.env"
echo "DATA_DIR=${DATA_DIR}"
