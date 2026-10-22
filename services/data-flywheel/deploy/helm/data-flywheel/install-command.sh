#!/bin/bash

# This script installs the Data Flywheel Blueprint Helm chart
# Note: This blueprint uses NeMo Platform (NMP) version 26.03.0
# For version-specific documentation, refer to:
# https://docs.nvidia.com/nemo/microservices/26.03.0/

helm upgrade --install data-flywheel . \
  --set secrets.ngcApiKey=$NGC_API_KEY \
  --set secrets.nvidiaApiKey=$NVIDIA_API_KEY \
  --set secrets.hfToken=$HF_TOKEN \
  --set secrets.llmJudgeApiKey=$LLM_JUDGE_API_KEY \
  --set secrets.embApiKey=$EMB_API_KEY \
  --namespace nv-nvidia-blueprint-data-flywheel \
  --create-namespace \
  --timeout 20m \
  --debug
