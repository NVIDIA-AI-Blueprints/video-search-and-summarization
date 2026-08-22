#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

# Deploy a VSS developer profile and block until it is healthy.
#
# One call replaces the credential setup, generated.env assembly, compose
# resolution and health polling that otherwise run as separate steps.
# On failure it prints the per-container state and the tail of each unhealthy
# container's log, so a caller can diagnose without re-deriving any of it.
#
#   deploy.sh --profile base [--hardware-profile auto] [--timeout 1800]

set -uo pipefail

PROFILE=""; HW="auto"; TIMEOUT=1800; LLM="nvidia/nvidia-nemotron-nano-9b-v2"; MODE=""; VLM="nvidia/vila"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --profile) PROFILE="$2"; shift 2 ;;
    --hardware-profile) HW="$2"; shift 2 ;;
    --timeout) TIMEOUT="$2"; shift 2 ;;
    --llm) LLM="$2"; shift 2 ;;
    --mode) MODE="$2"; shift 2 ;;   # alerts: verification (2d_cv) | real-time (2d_vlm)
    --vlm)  VLM="$2";  shift 2 ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done
[[ -n "$PROFILE" ]] || { echo "ERROR: --profile is required" >&2; exit 2; }

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." 2>/dev/null && pwd)"
if [[ ! -x "${REPO}/deploy/docker/scripts/dev-profile.sh" ]]; then
  REPO="$(git -C "$(dirname "${BASH_SOURCE[0]}")" rev-parse --show-toplevel 2>/dev/null || true)"
fi
[[ -x "${REPO}/deploy/docker/scripts/dev-profile.sh" ]] || {
  echo "ERROR: cannot locate the repo from ${BASH_SOURCE[0]}" >&2; exit 2; }

# Credentials. dev-profile.sh reads NGC_CLI_API_KEY, not NGC_API_KEY.
[[ -f ~/.secrete ]] && source ~/.secrete
export NGC_CLI_API_KEY="${NGC_CLI_API_KEY:-${NGC_API_KEY:-}}"
export NVIDIA_API_KEY="${NVIDIA_API_KEY:-${NGC_API_KEY:-}}"
export LLM_ENDPOINT_URL="${LLM_ENDPOINT_URL:-https://integrate.api.nvidia.com}"
export VLM_ENDPOINT_URL="${VLM_ENDPOINT_URL:-https://integrate.api.nvidia.com}"
[[ -n "${NGC_CLI_API_KEY}" ]] || { echo "ERROR: no NGC key in env or ~/.secrete" >&2; exit 2; }

# Hardware profile. The launcher's glob misses some GPUs, and OTHER bypasses
# the GPU-match gate rather than failing validation.
if [[ "$HW" == "auto" ]]; then
  gpu="$(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | head -1)"
  case "${gpu,,}" in
    *l40s*)          HW="L40S" ;;
    *"rtx pro 6000"*|*rtxpro6000*) HW="RTXPRO6000BW" ;;
    *)               HW="OTHER" ;;
  esac
  echo "[deploy] detected GPU '${gpu:-none}' -> hardware profile ${HW}"
fi

echo "[deploy] profile=${PROFILE}${MODE:+ mode=${MODE}} hardware=${HW} llm=${LLM} (remote)"
# Remote VLM as well as remote LLM: the local cosmos NIM declares
# interval=60s/retries=2/start_period=0, so it is marked unhealthy at 120s while
# the model is still loading, and Compose then skips it as a failed optional
# dependency. Nothing downstream can reach "all healthy" while that is true.
UP_ARGS=(up --profile "${PROFILE}" --hardware-profile "${HW}"
         --use-remote-llm --llm "${LLM}" --llm-model-type nim
         --use-remote-vlm --vlm "${VLM}")
[[ -n "${MODE}" ]] && UP_ARGS+=(--mode "${MODE}")
DEPLOY_RC=0
if ! "${REPO}/deploy/docker/scripts/dev-profile.sh" "${UP_ARGS[@]}"; then
  DEPLOY_RC=1
  echo "[deploy] FAILED: dev-profile.sh up returned non-zero" >&2
fi

# The set of services this profile is supposed to bring up. Waiting on "whatever
# containers currently exist" is not enough: Compose creates them over tens of
# seconds, so an early poll sees a complete-looking set and returns before the
# slow ones (the NIMs) exist at all.
# The env files must be sourced, not merely passed as --env-file: every service
# is profile-gated and COMPOSE_PROFILES lives in generated.env as a nested
# variable reference, so without expanding it Compose reports zero services.
EXPECTED="$( cd "${REPO}/deploy/docker" 2>/dev/null &&
  ( set -a
    . ./containers.env 2>/dev/null || true
    . "./developer-profiles/dev-profile-${PROFILE}/.env" 2>/dev/null || true
    . "./developer-profiles/dev-profile-${PROFILE}/generated.env" 2>/dev/null || true
    export VSS_APPS_DIR="${PWD}"
    set +a
    docker compose config --services 2>/dev/null | sort -u ) )"
n_expected=$(printf '%s\n' "${EXPECTED}" | grep -c . || true)
n_expected=${n_expected:-0}
echo "[deploy] profile expects ${n_expected} services"

# Block until every expected service exists AND is healthy.
#
# A container stuck in a restart loop will never become healthy, so waiting out
# the full timeout on one wastes the whole window. Bail as soon as any container
# has restarted twice, and hand the caller the status block to diagnose from.
echo "[deploy] waiting for health (timeout ${TIMEOUT}s)..."
deadline=$(( $(date +%s) + TIMEOUT ))
FAILED=""
while :; do
  unhealthy=0; total=0
  while read -r name state restarts; do
    [[ -z "$name" ]] && continue
    total=$((total+1))
    if [[ "${restarts:-0}" -ge 2 ]]; then
      FAILED="${name}"
      break
    fi
    [[ "$state" == "healthy" || "$state" == "none-running" ]] || unhealthy=$((unhealthy+1))
  done < <(docker ps -a --format '{{.Names}}' | while read -r n; do
             st="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{if eq .State.Status "running"}}none-running{{else}}{{.State.Status}}{{end}}{{end}}' "$n" 2>/dev/null)"
             rc="$(docker inspect -f '{{.RestartCount}}' "$n" 2>/dev/null)"
             echo "$n $st ${rc:-0}"; done)
  [[ -n "$FAILED" ]] && { echo "[deploy] ABORT: ${FAILED} is in a restart loop and will not become healthy" >&2; DEPLOY_RC=1; break; }
  # Every expected service must have a container before "all healthy" means anything.
  missing=0
  if [[ "${n_expected}" -gt 0 ]]; then
    while read -r svc; do
      [[ -z "$svc" ]] && continue
      docker ps -a --filter "label=com.docker.compose.service=${svc}" --format '{{.Names}}' \
        | grep -q . || missing=$((missing+1))
    done <<< "${EXPECTED}"
  fi
  if [[ "$total" -gt 0 && "$unhealthy" -eq 0 && "$missing" -eq 0 ]]; then
    echo "[deploy] all ${total} containers healthy (${n_expected} expected services present)"; break
  fi
  [[ $(date +%s) -ge $deadline ]] && { echo "[deploy] TIMEOUT with ${unhealthy}/${total} not healthy" >&2; DEPLOY_RC=1; break; }
  sleep 5
done

# Structured status, so the caller diagnoses instead of re-deriving.
echo "[deploy] === STATUS ==="
docker ps -a --format '{{.Names}}' | while read -r n; do
  s="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$n" 2>/dev/null)"
  r="$(docker inspect -f '{{.RestartCount}}' "$n" 2>/dev/null)"
  printf '  %-34s %-12s restarts=%s\n' "$n" "$s" "$r"
  if [[ "$s" != "healthy" && "$s" != "running" ]]; then
    docker logs "$n" 2>&1 | tail -8 | sed 's/^/        | /'
  fi
done
echo "[deploy] === END STATUS ==="
# Non-zero on any failure: a caller that only reads stdout will otherwise treat
# an aborted deploy as a successful one.
if [[ "${DEPLOY_RC}" -ne 0 ]]; then
  echo "[deploy] RESULT: FAILED" >&2
  exit 1
fi
echo "[deploy] RESULT: OK"
