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

# Shared helpers for the alert perf stage hooks. Sourced, not executed.
#
# Inputs (environment; set by the shared perf job or by run_benchmark.sh):
#   PERF_CONFIG_ID     perf_configs id to run                      (required)
#   PERF_SCENARIO      test_scenarios key           (default: quick_test)
#   PERF_WORK_DIR      scratch dir for generated files
#                      (default: $WORKSPACE/alert-perf-work or ./alert-perf-work)
#   ARTIFACT_REF       promoted image ref; deployed as the alert service
#   HOST_IP            host IP for dev-profile.sh (default: primary route IP)
#   NGC_CLI_API_KEY    required for image and model pulls
#   LLM_ENDPOINT_URL   required when the config entry has llmMode: remote

# perf/ lives in deploy/docker/developer-profiles/dev-profile-alerts/perf
PERF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROFILE_DIR="$(cd "${PERF_DIR}/.." && pwd)"
DEPLOY_DIR="$(cd "${PROFILE_DIR}/../.." && pwd)"
REPO_ROOT="$(cd "${DEPLOY_DIR}/../.." && pwd)"
PERF_SCENARIO="${PERF_SCENARIO:-quick_test}"
PERF_WORK_DIR="${PERF_WORK_DIR:-${WORKSPACE:-${PWD}}/alert-perf-work}"
PERF_PYTHON="${PERF_PYTHON:-python3}"
COMPOSE_PROJECT="${COMPOSE_PROJECT_NAME:-vss}"
export PERF_DIR REPO_ROOT DEPLOY_DIR PROFILE_DIR PERF_SCENARIO PERF_WORK_DIR

log()  { echo "[alert-perf][$(date -u +%H:%M:%S)] $*"; }
die()  { echo "[alert-perf][ERROR] $*" >&2; exit 1; }

require_config_id() {
  [[ -n "${PERF_CONFIG_ID:-}" ]] || die "PERF_CONFIG_ID is not set"
}

# Export PERF_HW_PROFILE, PERF_CV_MODEL, ... for PERF_CONFIG_ID.
resolve_perf_config() {
  require_config_id
  local _exports
  _exports="$("${PERF_PYTHON}" "${PERF_DIR}/setup/scripts/prepare_runtime.py" resolve --config-id "${PERF_CONFIG_ID}")" \
    || die "cannot resolve perf config '${PERF_CONFIG_ID}'"
  eval "${_exports}"
}

# docker compose with the same file and env-file layering as dev-profile.sh,
# plus the perf overlay and the perf runtime env (last, so it wins).
perf_compose() {
  resolve_perf_config
  local _overlay="${REPO_ROOT}/${PERF_COMPOSE_PATH}"
  [[ -f "${_overlay}" ]] || die "compose overlay not found: ${_overlay}"
  [[ -f "${PERF_WORK_DIR}/perf-runtime.env" ]] || die "missing ${PERF_WORK_DIR}/perf-runtime.env (run pre_flight.sh)"
  (
    cd "${DEPLOY_DIR}" && docker compose \
      -p "${COMPOSE_PROJECT}" \
      -f compose.yml \
      -f "${_overlay}" \
      --env-file containers.env \
      --env-file developer-profiles/dev-profile-alerts/.env \
      --env-file developer-profiles/dev-profile-alerts/generated.env \
      --env-file "${PERF_WORK_DIR}/perf-runtime.env" \
      "$@"
  )
}

host_ip() {
  if [[ -n "${HOST_IP:-}" ]]; then
    echo "${HOST_IP}"
  else
    ip route get 1.1.1.1 2>/dev/null | awk '{for (i=1;i<=NF;i++) if ($i=="src") {print $(i+1); exit}}'
  fi
}

# Wait until a container reports healthy (or running when it has no healthcheck).
# Fails fast on exited/dead/restart loops and prints the container's last logs.
wait_container() {
  local _name="$1" _timeout_s="$2" _start _state _restarts _last_log=0
  _start="$(date +%s)"
  while true; do
    _state="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "${_name}" 2>/dev/null || echo missing)"
    _restarts="$(docker inspect -f '{{.RestartCount}}' "${_name}" 2>/dev/null || echo 0)"
    case "${_state}" in
      healthy|running)
        if (( _restarts <= 3 )); then log "${_name}: ${_state}"; return 0; fi ;;
    esac
    if [[ "${_state}" == exited || "${_state}" == dead || "${_state}" == restarting ]] || (( _restarts > 3 )); then
      echo "----- last 40 log lines of ${_name} -----" >&2
      docker logs --tail 40 "${_name}" >&2 2>&1 || true
      die "${_name} is ${_state} (restarts: ${_restarts})"
    fi
    if (( $(date +%s) - _start > _timeout_s )); then
      die "timed out after ${_timeout_s}s waiting for ${_name} (state: ${_state})"
    fi
    if (( $(date +%s) - _last_log >= 60 )); then
      log "waiting for ${_name}: ${_state} ($(( $(date +%s) - _start ))s)"
      _last_log="$(date +%s)"
    fi
    sleep 10
  done
}

wait_http() {
  local _url="$1" _timeout_s="$2" _start
  _start="$(date +%s)"
  until curl -sf -o /dev/null "${_url}"; do
    if (( $(date +%s) - _start > _timeout_s )); then
      die "timed out after ${_timeout_s}s waiting for ${_url}"
    fi
    sleep 5
  done
  log "ready: ${_url}"
}
