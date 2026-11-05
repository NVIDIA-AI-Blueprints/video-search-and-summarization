#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

# Stage 1: prepare the machine and generate the per-run files.
set -euo pipefail
# shellcheck source=_common.sh
source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"

log "pre_flight: config=${PERF_CONFIG_ID:-<unset>} scenario=${PERF_SCENARIO} work=${PERF_WORK_DIR}"
require_config_id

for _bin in docker curl "${PERF_PYTHON}"; do
  command -v "${_bin}" >/dev/null 2>&1 || die "'${_bin}' not found"
done
docker compose version >/dev/null 2>&1 || die "docker compose plugin not found"
command -v nvidia-smi >/dev/null 2>&1 || die "nvidia-smi not found (NVIDIA driver missing?)"
nvidia-smi --query-gpu=index,name,driver_version,memory.used --format=csv,noheader

"${PERF_PYTHON}" - <<'EOF' || die "missing Python packages; pip install -r ${PERF_DIR}/benchmark/requirements.txt"
import requests, yaml, pydantic, minio  # noqa: F401
EOF

resolve_perf_config
[[ "${PERF_CONFIG_ENABLED}" == "true" ]] || log "WARNING: ${PERF_CONFIG_ID} has enabled: false; running anyway because it was requested explicitly"
if [[ "${PERF_LLM_MODE}" == "remote" ]]; then
  [[ -n "${LLM_ENDPOINT_URL:-}" ]] || die "llmMode=remote needs LLM_ENDPOINT_URL"
fi
[[ -n "${NGC_CLI_API_KEY:-}" ]] || die "NGC_CLI_API_KEY is not set"

# GPUs must be idle: a leftover stack from an earlier entry skews the numbers.
_busy="$(nvidia-smi --query-compute-apps=pid --format=csv,noheader | grep -c . || true)"
if (( _busy > 0 )); then
  log "WARNING: ${_busy} compute process(es) on the GPUs; tearing down any previous '${COMPOSE_PROJECT}' stack"
  docker compose -p "${COMPOSE_PROJECT}" down --remove-orphans >/dev/null 2>&1 || true
fi

echo "${NGC_CLI_API_KEY}" | docker login --username '$oauthtoken' --password-stdin nvcr.io >/dev/null
log "logged in to nvcr.io"

mkdir -p "${PERF_WORK_DIR}"
"${PERF_PYTHON}" "${PERF_DIR}/setup/scripts/prepare_runtime.py" prepare \
  --config-id "${PERF_CONFIG_ID}" \
  --scenario "${PERF_SCENARIO}" \
  --work-dir "${PERF_WORK_DIR}" \
  --artifact-ref "${ARTIFACT_REF:-}"

log "pre_flight done"
