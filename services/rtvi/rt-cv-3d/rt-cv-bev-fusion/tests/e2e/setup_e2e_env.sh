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
# Prepare the full warehouse-3d-app-mv3dt deployment for the Tier-2 e2e test on
# the 4-camera sample dataset. This codifies the repo README's sample-data flow
# (the same steps the vss-deploy-detection-tracking-3d skill drives interactively)
# so CI can run it non-interactively.
#
# Required env:
#   NGC_CLI_API_KEY   - NGC key with access to nvidia/vss-warehouse/*
#   HOST_IP           - this host's IP (for inter-service comms / VST UI)
#   HARDWARE_PROFILE  - GPU slug, e.g. RTXPRO6000BW (RTX PRO 6000), H100, L40S
# Optional env:
#   VSS_WAREHOUSE_VERSION   (default 3.1.0)
#   WORK_DIR                (default: repo root) — where NGC assets are downloaded
#   NGC_CLI_ORG             (default: nvidia)
#
# On success prints DEPLOY_ROOT / COMPOSE_FILE / ENV_FILE for the e2e test to consume.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
WORK_DIR="${WORK_DIR:-$REPO_ROOT}"
VER="${VSS_WAREHOUSE_VERSION:-3.1.0}"
NGC_CLI_ORG="${NGC_CLI_ORG:-nvidia}"

: "${NGC_CLI_API_KEY:?set NGC_CLI_API_KEY}"
: "${HOST_IP:?set HOST_IP}"
: "${HARDWARE_PROFILE:?set HARDWARE_PROFILE}"
export NGC_CLI_API_KEY NGC_CLI_ORG

banner() { echo "==================== $* ===================="; }

COMPOSE_DIR="${WORK_DIR}/vss-warehouse-compose_v${VER}"
APP_DATA_DIR="${WORK_DIR}/vss-warehouse-app-data_v${VER}"
DEPLOY_ROOT="${COMPOSE_DIR}/deployments"
WAREHOUSE_DIR="${DEPLOY_ROOT}/warehouse"

cd "${WORK_DIR}"

banner "Step 1: Download base assets from NGC (compose + app-data)"
if [ ! -d "${COMPOSE_DIR}" ]; then
  ngc registry resource download-version "nvidia/vss-warehouse/vss-warehouse-compose:${VER}"
fi
if [ ! -d "${APP_DATA_DIR}" ]; then
  ngc registry resource download-version "nvidia/vss-warehouse/vss-warehouse-app-data:${VER}"
fi

banner "Step 2: Extract"
( cd "${COMPOSE_DIR}" && [ -d deployments ] || tar -xf deploy-warehouse-compose.tar.gz )
( cd "${APP_DATA_DIR}" && [ -d vss-warehouse-app-data ] || { tar -xf vss-warehouse-app-data.tar.gz; sudo chmod -R 777 vss-warehouse-app-data || chmod -R 777 vss-warehouse-app-data; } )

banner "Step 3: Copy warehouse-3d-app-mv3dt into the deployment"
cp -rp "${REPO_ROOT}/warehouse-3d-app-mv3dt" "${WAREHOUSE_DIR}/"

banner "Step 4: Configure repo .env and propagate via apply-env.sh"
# Edit the three required vars in the repo-root .env, then apply-env.sh copies
# them into ${WAREHOUSE_DIR}/.env (the file compose actually reads).
python3 - "$REPO_ROOT/.env" <<PY
import re, sys
path = sys.argv[1]
vals = {
    "HOST_IP": "${HOST_IP}",
    "NGC_CLI_API_KEY": "${NGC_CLI_API_KEY}",
    "HARDWARE_PROFILE": "${HARDWARE_PROFILE}",
}
lines = open(path).read().splitlines()
seen = set()
for i, ln in enumerate(lines):
    for k, v in vals.items():
        if re.match(rf"^\s*{k}\s*=", ln):
            lines[i] = f"{k}='{v}'"
            seen.add(k)
for k, v in vals.items():
    if k not in seen:
        lines.append(f"{k}='{v}'")
open(path, "w").write("\n".join(lines) + "\n")
print("updated", path)
PY
( cd "${REPO_ROOT}" && ./apply-env.sh )

banner "Step 5: Pull models (git-lfs + prepare-models.sh)"
( cd "${REPO_ROOT}" && git lfs pull || echo "WARN: git lfs pull failed (continuing)" )
echo "${NGC_CLI_API_KEY}" | docker login nvcr.io --username '$oauthtoken' --password-stdin
( cd "${REPO_ROOT}" && ./prepare-models.sh )

banner "DONE — deployment prepared"
echo "DEPLOY_ROOT=${DEPLOY_ROOT}"
echo "COMPOSE_FILE=${WAREHOUSE_DIR}/warehouse-3d-app-mv3dt/compose.yml"
echo "ENV_FILE=${WAREHOUSE_DIR}/.env"
