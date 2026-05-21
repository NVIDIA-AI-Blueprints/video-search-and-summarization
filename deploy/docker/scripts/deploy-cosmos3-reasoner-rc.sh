#!/bin/bash
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Deploy VSS dev-profile-base with Cosmos3 Reasoner RC as the VLM.
# Mirrors deploy_vss_launchable.ipynb flow; see COSMOS3-REASONER-RC-TESTING.md.

set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
deployment_directory="$(cd "${script_dir}/.." && pwd)"
profile_dir="${deployment_directory}/developer-profiles/dev-profile-base"
profile_env="${profile_dir}/.env.cosmos3-reasoner-rc"
profile_dotenv="${profile_dir}/.env"
override_file="${profile_dir}/cosmos3-reasoner-rc.override.yml"

export COMPOSE_FILE="${deployment_directory}/compose.yml:${override_file}"

if [[ -z "${NGC_CLI_API_KEY:-}" ]]; then
  echo "[ERROR] NGC_CLI_API_KEY is required. Export it or set it in ${profile_env}"
  exit 1
fi

if [[ ! -f "${profile_env}" ]]; then
  echo "[ERROR] Profile env not found: ${profile_env}"
  exit 1
fi

# dev-profile.sh always reads dev-profile-base/.env — symlink RC template without changing dev-profile.sh
if [[ -e "${profile_dotenv}" ]] && [[ ! -L "${profile_dotenv}" ]]; then
  _backup="${profile_dir}/.env.bak.$(date +%s)"
  echo "[INFO] Backing up existing .env to ${_backup}"
  mv "${profile_dotenv}" "${_backup}"
fi
ln -sf ".env.cosmos3-reasoner-rc" "${profile_dotenv}"

echo "[INFO] Cosmos3 Reasoner RC deploy"
echo "[INFO]   profile .env -> .env.cosmos3-reasoner-rc (symlink)"
echo "[INFO]   compose:     ${COMPOSE_FILE}"
echo "[INFO]   NIM_MODEL_SIZE from .env.cosmos3-reasoner-rc (default nano)"

exec "${script_dir}/dev-profile.sh" up --profile base "$@"
