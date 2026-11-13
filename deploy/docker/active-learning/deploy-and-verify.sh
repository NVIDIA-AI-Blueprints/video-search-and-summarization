#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Deploy the AOI active-learning stack and verify it.
#
#   1. Config checks (tests/test_config.py): config, secrets, registry access
#   2. docker compose pull + up -d (skipped with --verify-only)
#   3. Wait for health checks and setup jobs to settle (bounded by --timeout)
#   4. Deployment checks (tests/test_live_deployment.py)
#
# Exit code 0 means every check passed.

set -euo pipefail

usage() {
  cat <<'EOF'
Usage: ./deploy-and-verify.sh [--verify-only] [--no-pull] [--timeout SECONDS]

  --verify-only      Skip deploying; only run the checks against the running stack.
  --no-pull          Deploy with the images already on this host (skips the registry check).
  --timeout SECONDS  Max wait for health checks and setup jobs after deploy (default 900).
  -h, --help         Show this help.

Environment:
  AL_VERIFY_HOST     Host to reach the published ports on. Default: the address
                     each port is bound to (127.0.0.1 unless its *_BIND_ADDRESS
                     is changed; see README.md, "Published ports").
EOF
}

VERIFY_ONLY=false
PULL=true
TIMEOUT=900
while [[ $# -gt 0 ]]; do
  case "$1" in
    --verify-only) VERIFY_ONLY=true ;;
    --no-pull) PULL=false ;;
    --timeout) TIMEOUT="${2:?--timeout needs a value}"; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
  shift
done
[[ "${TIMEOUT}" =~ ^[0-9]+$ ]] || { echo "--timeout must be a number of seconds: ${TIMEOUT}" >&2; exit 2; }

cd "$(dirname "${BASH_SOURCE[0]}")"
# Same command as README.md: each bundle loads its own includes/*/.env.deft-aoi-*
# through compose.yml; .env.secrets holds the required inputs and overrides them.
COMPOSE=(docker compose --env-file .env.secrets -f compose.yml)

step() { printf '\n==> %s\n' "$*"; }
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

command -v docker >/dev/null || die "docker is not installed"
docker compose version >/dev/null 2>&1 || die "the docker compose plugin is not installed"
[[ -f .env.secrets ]] || die ".env.secrets is missing: install -m 600 .env.secrets.example .env.secrets, then fill it in"

# The config checks load the Data Flywheel template, so they need PyYAML too.
# No cache provider: keep .pytest_cache out of the checkout.
if command -v uvx >/dev/null; then
  PYTEST=(uvx --from "pytest==9.1.1" --with "pyyaml==6.0.3" pytest -p no:cacheprovider)
elif python3 -c "import pytest, yaml" 2>/dev/null; then
  PYTEST=(python3 -m pytest -p no:cacheprovider)
else
  die "pytest and PyYAML not found: install uv (https://docs.astral.sh/uv/) or run: python3 -m pip install pytest pyyaml"
fi

# Settled = no health check still "starting" and no one-shot job (restart policy
# "no" or "on-failure") still running; `up -d` reruns those jobs on every deploy.
wait_until_settled() {
  local deadline=$((SECONDS + TIMEOUT)) ids pending
  while :; do
    ids=$("${COMPOSE[@]}" ps -aq)
    pending=$([[ -z "${ids}" ]] || docker inspect \
      -f '{{.Name}} {{.State.Status}} {{.HostConfig.RestartPolicy.Name}} {{if .State.Health}}{{.State.Health.Status}}{{else}}-{{end}}' ${ids} \
      | awk '$4 == "starting" || ($3 !~ /^(always|unless-stopped)$/ && $2 ~ /^(running|restarting)$/) { sub("/", "", $1); printf "%s ", $1 }')
    if [[ -z "${pending}" ]]; then
      echo "Health checks and setup jobs have settled."
      return 0
    fi
    if (( SECONDS >= deadline )); then
      echo "Timed out after ${TIMEOUT}s; still pending: ${pending}"
      return 0  # the deployment checks report exactly what is not ready
    fi
    echo "Waiting on: ${pending}"
    sleep 10
  done
}

CONFIG_MARKERS=()
[[ "${PULL}" == true ]] || CONFIG_MARKERS=(-m "not registry")

step "Config checks"
"${PYTEST[@]}" tests/test_config.py -q -rf ${CONFIG_MARKERS[@]+"${CONFIG_MARKERS[@]}"} \
  || die "config checks failed$([[ "${VERIFY_ONLY}" == true ]] || echo '; nothing was deployed')"

UP_FAILED=false
if [[ "${VERIFY_ONLY}" == false ]]; then
  if [[ "${PULL}" == true ]]; then
    step "Pulling images"
    "${COMPOSE[@]}" pull --quiet
  fi

  # `up -d` waits for each depends_on condition (bootstrap jobs completed,
  # dependencies healthy) and exits non-zero if one fails. Keep going so the
  # deployment checks name every container that is not right, then fail.
  step "Starting the stack"
  if ! "${COMPOSE[@]}" up -d --quiet-pull; then
    UP_FAILED=true
    echo "docker compose up reported a failure; running the checks to show what is wrong."
  fi

  step "Waiting for health checks and setup jobs (up to ${TIMEOUT}s)"
  wait_until_settled
fi

step "Deployment checks"
if "${PYTEST[@]}" tests/test_live_deployment.py -q -rf && [[ "${UP_FAILED}" == false ]]; then
  step "PASSED: the deployment is up and verified"
else
  step "FAILED: see the failures above"
  exit 1
fi
