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

# Stage 2: bring up dev-profile-alerts in verification mode with the perf overlay.
#
# A plain `docker compose up` is not enough: dev-profile.sh derives
# generated.env (paths, model slugs, device IDs, hardware env files). We run it
# with --dry-run so it only writes generated.env, then start Compose ourselves
# with the perf overlay and perf-runtime.env layered on top. This keeps the
# deployment identical to the shipped profile apart from what the overlay adds.
set -euo pipefail
# shellcheck source=_common.sh
source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"

resolve_perf_config
log "deploy: ${PERF_CONFIG_ID} (hw=${PERF_HW_PROFILE}, cv=${PERF_CV_MODEL}@${PERF_CV_GPUS}, vlm=${PERF_VLM_PRECISION}@${PERF_VLM_GPUS}, llm=${PERF_LLM_MODE})"

_ip="$(host_ip)"
[[ -n "${_ip}" ]] || die "cannot determine HOST_IP; export HOST_IP"

_args=(up -p alerts -m verification -H "${PERF_HW_PROFILE}" -i "${_ip}" --dry-run)
case "${PERF_HW_PROFILE}" in
  DGX-SPARK|IGX-THOR|AGX-THOR) ;;  # single GPU; dev-profile.sh rejects device IDs
  *)
    _vlm_dev="${PERF_VLM_GPUS%%,*}"
    _args+=(--vlm-device-id "${_vlm_dev}")
    [[ "${PERF_LLM_MODE}" == "remote" ]] || _args+=(--llm-device-id "${_vlm_dev}")
    ;;
esac
[[ "${PERF_LLM_MODE}" == "remote" ]] && _args+=(--use-remote-llm)

log "generating generated.env: dev-profile.sh ${_args[*]}"
( cd "${DEPLOY_DIR}" && bash scripts/dev-profile.sh "${_args[@]}" ) > "${PERF_WORK_DIR}/dev-profile-dry-run.log" 2>&1 \
  || { tail -50 "${PERF_WORK_DIR}/dev-profile-dry-run.log" >&2; die "dev-profile.sh --dry-run failed"; }
[[ -f "${PROFILE_DIR}/generated.env" ]] || die "dev-profile.sh did not write ${PROFILE_DIR}/generated.env"

# Steps dev-profile.sh skips under --dry-run.
_data_dir="$(grep -E '^VSS_DATA_DIR=' "${PROFILE_DIR}/generated.env" | tail -1 | cut -d= -f2- | tr -d "'\"")"
mkdir -p "${_data_dir}/data_log/vss_video_analytics_api" \
         "${DEPLOY_DIR}/engines/gdino" "${DEPLOY_DIR}/engines/rtdetr-its"
chmod -R 777 "${DEPLOY_DIR}/engines" "${_data_dir}/data_log" || true
if sudo -n true 2>/dev/null; then
  sudo -n bash -c "printf '%s\n' \
    'net.core.rmem_max = 5242880' 'net.core.wmem_max = 5242880' \
    'net.ipv4.tcp_rmem = 4096 87380 16777216' 'net.ipv4.tcp_wmem = 4096 65536 16777216' \
    > /etc/sysctl.d/99-vss.conf && sysctl --system >/dev/null"
  log "applied VSS kernel settings"
else
  log "WARNING: no passwordless sudo; skipping VSS kernel settings"
fi

log "resolved images:"
perf_compose config --images | sort -u

log "docker compose up"
perf_compose up --detach --pull always --force-recreate --build
log "deploy done"
