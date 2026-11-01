#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Start the local experiment vss-agent (plan D14 (b)) on 127.0.0.1 with a minimal, explicit environment:
# the model endpoint and key only, INCIDENT_EXPERIMENTS_ENABLED=1, and no Supabase, database or R2 setting,
# so nothing is persisted and no evidence image can be uploaded. It never touches the shared VM agent.
#
#   experiments/agent/start-experiment-agent.sh                       # real model endpoint (paid calls on use)
#   experiments/agent/start-experiment-agent.sh --upstream http://127.0.0.1:8899   # local fake: no cost
#
# Env: NAT_BIN (default services/agent/.venv/bin/nat), EXPERIMENT_AGENT_PORT (default 8765),
#      EXPERIMENT_AGENT_DRY_RUN=1 (print the command and environment names instead of starting).
# The agent always runs THIS checkout's code (PYTHONPATH=<checkout>/services/agent/src), whatever an editable install
# in the venv points at, so the commit recorded in its run provenance is the code that ran. ffmpeg and ffprobe come
# from the checkout's gitignored tools dir (.scripts/media-tools.sh install) when they are there, else /usr/bin.
set -euo pipefail

repo="$(git -C "$(dirname "$0")" rev-parse --show-toplevel)"
profile="${repo}/deploy/docker/developer-profiles/dev-profile-incident"
config="${profile}/eval/experiments/agent/experiment-agent.yml"
port="${EXPERIMENT_AGENT_PORT:-8765}"
nat_bin="${NAT_BIN:-${repo}/services/agent/.venv/bin/nat}"
upstream=""
if [ "${1:-}" = "--upstream" ]; then
  upstream="${2:?--upstream needs a URL}"
fi

# The agent's sitecustomize loads services/agent/.env_file; it could carry Supabase or R2 settings.
if [ -e "${repo}/services/agent/.env_file" ]; then
  echo "REFUSED: ${repo}/services/agent/.env_file exists and would be loaded; move it away first" >&2
  exit 2
fi
if [ ! -x "${nat_bin}" ]; then
  echo "REFUSED: no nat at ${nat_bin} (set NAT_BIN)" >&2
  exit 2
fi

read_env() {  # one value from the profile .env.local, quotes stripped; never printed
  local value
  value="$(grep -E "^$1=" "${profile}/.env.local" | tail -n 1 | cut -d= -f2-)"
  value="${value%\"}"; value="${value#\"}"; value="${value%\'}"; value="${value#\'}"
  printf '%s' "${value}"
}

if [ -n "${upstream}" ]; then
  base="${upstream%/}"
  key="local-fake"
else
  base="$(read_env VLM_GATEWAY_BASE_URL)"
  key="$(read_env VLM_GATEWAY_API_KEY)"
  base="${base%/}"
  base="${base%/v1}"  # the agent appends /v1/chat/completions itself (bare base URL rule)
  if [ -z "${base}" ] || [ -z "${key}" ]; then
    echo "REFUSED: VLM_GATEWAY_BASE_URL or VLM_GATEWAY_API_KEY is missing from .env.local" >&2
    exit 2
  fi
fi

tools="${repo}/.tools/ffmpeg/bin"
src="${repo}/services/agent/src"
echo "experiment agent: http://127.0.0.1:${port}  model endpoint: ${base}  (no Supabase, no R2, experiments on)"
echo "  code: ${src} at $(git -C "${repo}" rev-parse --short HEAD)  ffmpeg: $(PATH="${tools}:/usr/bin:/bin" command -v ffmpeg || echo none)"
if [ "${EXPERIMENT_AGENT_DRY_RUN:-}" = 1 ]; then
  echo "dry run: env -i HOME PATH=${tools}:/usr/bin:/bin PYTHONPATH=${src} VLM_BASE_URL VLM_NAME LLM_BASE_URL OPENAI_API_KEY INCIDENT_EXPERIMENTS_ENABLED=1 ${nat_bin} serve --config_file ${config} --host 127.0.0.1 --port ${port}"
  exit 0
fi
cd "${repo}"
exec env -i HOME="${HOME}" PATH="${tools}:/usr/bin:/bin" PYTHONPATH="${src}" \
  VLM_BASE_URL="${base}" VLM_NAME="nvidia/cosmos-3-super-reasoner" LLM_BASE_URL="${base}" \
  OPENAI_API_KEY="${key}" INCIDENT_EXPERIMENTS_ENABLED=1 \
  "${nat_bin}" serve --config_file "${config}" --host 127.0.0.1 --port "${port}"
