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

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

info()  { echo -e "${GREEN}[✓]${NC} $*"; }
warn()  { echo -e "${YELLOW}[!]${NC} $*"; }
error() { echo -e "${RED}[✗]${NC} $*"; }
prompt() { echo -en "${YELLOW}$*${NC}"; }

# --- Step 1: .env file ---
if [ -f .env ]; then
    info ".env file already exists"
else
    warn ".env file not found — creating from template"
    prompt "NGC_API_KEY: "; read -r ngc_key
    prompt "NVIDIA_API_KEY (enter to reuse NGC_API_KEY): "; read -r nvidia_key
    nvidia_key="${nvidia_key:-$ngc_key}"
    prompt "LOCAL_NIM_CACHE path: "; read -r nim_cache
    prompt "HF_TOKEN (enter to skip): "; read -r hf_token
    prompt "Artifactory username: "; read -r artif_user
    prompt "Artifactory API key: "; read -r artif_key

    cat > .env <<EOF
NGC_API_KEY=${ngc_key}
NVIDIA_API_KEY=${nvidia_key}
LOCAL_NIM_CACHE=${nim_cache}
HF_TOKEN=${hf_token}
ARTIFACTORY_USER=${artif_user}
ARTIFACTORY_TOKEN=${artif_key}
EOF
    info "Created .env"
fi

# --- Step 2: NIM cache permissions ---
source .env
if [ -n "${LOCAL_NIM_CACHE:-}" ]; then
    if [ -d "$LOCAL_NIM_CACHE" ]; then
        info "NIM cache directory exists: $LOCAL_NIM_CACHE"
    else
        warn "Creating NIM cache directory: $LOCAL_NIM_CACHE"
        mkdir -p "$LOCAL_NIM_CACHE"
    fi
    prompt "Set NIM cache ownership to 1000:1000? (y/N): "; read -r yn
    if [[ "$yn" =~ ^[Yy]$ ]]; then
        sudo chown -R 1000:1000 "$LOCAL_NIM_CACHE"
        info "Set permissions on $LOCAL_NIM_CACHE"
    fi
else
    error "LOCAL_NIM_CACHE not set in .env"
fi

# --- Step 3: Artifactory credentials ---
if [ -n "${ARTIFACTORY_USER:-}" ] && [ -n "${ARTIFACTORY_TOKEN:-}" ]; then
    info "Artifactory credentials already set in .env"
else
    warn "ARTIFACTORY_USER/TOKEN not found in .env — adding"
    prompt "Artifactory username: "; read -r artif_user
    prompt "Artifactory API key: "; read -r artif_key

    if grep -q '^ARTIFACTORY_USER=' .env; then
        sed -i "s|^ARTIFACTORY_USER=.*|ARTIFACTORY_USER=${artif_user}|" .env
    else
        echo "ARTIFACTORY_USER=${artif_user}" >> .env
    fi

    if grep -q '^ARTIFACTORY_TOKEN=' .env; then
        sed -i "s|^ARTIFACTORY_TOKEN=.*|ARTIFACTORY_TOKEN=${artif_key}|" .env
    else
        echo "ARTIFACTORY_TOKEN=${artif_key}" >> .env
    fi

    export ARTIFACTORY_USER="${artif_user}"
    export ARTIFACTORY_TOKEN="${artif_key}"
    info "Saved Artifactory credentials to .env"
fi

# --- Step 4: Shared media volume ---
if docker volume inspect via-media-data &>/dev/null; then
    info "Docker volume 'via-media-data' already exists"
else
    docker volume create via-media-data
    info "Created Docker volume 'via-media-data'"
fi

# --- Summary ---
echo ""
info "Setup complete. To start a stack:"
echo "  docker compose -f <compose-file>.yaml up -d"
echo ""
echo "Available compose files:"
for f in *.yaml; do
    [[ "$f" == "media-server.yaml" || "$f" == "otel-stack.yaml" ]] && continue
    echo "  - $f"
done
