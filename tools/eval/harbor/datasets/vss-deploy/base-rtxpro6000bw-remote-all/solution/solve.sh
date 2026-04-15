#!/bin/bash
# Gold solution: deploy base on RTXPRO6000BW — remote LLM, remote VLM.
set -euo pipefail

REPO=/home/ubuntu/video-search-and-summarization
PROFILE=base
ENV_FILE=$REPO/deployments/developer-workflow/dev-profile-$PROFILE/.env

# Setup prerequisites
cd $REPO
bash environment/setup.sh 2>/dev/null || true

# Apply env overrides
sed -i "s|^HARDWARE_PROFILE=.*|HARDWARE_PROFILE=RTXPRO6000BW|" "$ENV_FILE"
sed -i "s|^MDX_SAMPLE_APPS_DIR=.*|MDX_SAMPLE_APPS_DIR=/home/ubuntu/video-search-and-summarization/deployments|" "$ENV_FILE"
sed -i "s|^MDX_DATA_DIR=.*|MDX_DATA_DIR=/home/ubuntu/video-search-and-summarization/data|" "$ENV_FILE"
sed -i "s|^HOST_IP=.*|HOST_IP=$(hostname -I | awk '{{print $1}}')|" "$ENV_FILE"
sed -i "s|^LLM_MODE=.*|LLM_MODE=remote|" "$ENV_FILE"
sed -i "s|^LLM_BASE_URL=.*|LLM_BASE_URL=https://integrate.api.nvidia.com/v1|" "$ENV_FILE"
sed -i "s|^VLM_MODE=.*|VLM_MODE=remote|" "$ENV_FILE"
sed -i "s|^VLM_BASE_URL=.*|VLM_BASE_URL=https://integrate.api.nvidia.com/v1|" "$ENV_FILE"

# Resolve compose
cd $REPO/deployments
docker compose --env-file $ENV_FILE config > resolved.yml

# Deploy
docker compose -f resolved.yml up -d --force-recreate

# Wait for Agent API to be healthy (up to 15 min)
echo "Waiting for containers..."
for i in $(seq 1 90); do
    if curl -sf -o /dev/null --max-time 5 http://localhost:8000/docs 2>/dev/null; then
        echo "Agent API is up after $((i*10))s"
        break
    fi
    sleep 10
done
