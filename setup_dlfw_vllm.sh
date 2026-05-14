#!/usr/bin/env bash
#
# Start qwen-vllm (Qwen3.6-35B-A3B-FP8) on port 8010.
# Idempotent: removes any existing container first.

set -euo pipefail

VLLM_IMAGE_TAG="${VLLM_IMAGE_TAG:-26.04-py3}"
HF_MODEL_HANDLE="${HF_MODEL_HANDLE:-Qwen/Qwen3.6-35B-A3B-FP8}"
VLLM_API_KEY="${VLLM_API_KEY:-nemoclaw-local-qwen}"
GPU_MEMORY_UTILIZATION="${GPU_MEMORY_UTILIZATION:-0.50}"
MAX_MODEL_LEN="${MAX_MODEL_LEN:-131072}"

docker pull "nvcr.io/nvidia/vllm:${VLLM_IMAGE_TAG}"

docker rm -f qwen-vllm 2>/dev/null || true

mkdir -p "${HOME}/.cache/huggingface"

docker run -d --gpus all \
  --name qwen-vllm \
  --ipc=host \
  --ulimit memlock=-1 \
  --ulimit stack=67108864 \
  -p 8010:8010 \
  -v "${HOME}/.cache/huggingface:/root/.cache/huggingface" \
  -e HF_TOKEN="${HF_TOKEN:-}" \
  "nvcr.io/nvidia/vllm:${VLLM_IMAGE_TAG}" \
  vllm serve "${HF_MODEL_HANDLE}" \
    --host 0.0.0.0 --port 8010 \
    --api-key "${VLLM_API_KEY}" \
    --dtype auto \
    --max-model-len "${MAX_MODEL_LEN}" \
    --reasoning-parser qwen3 \
    --enable-auto-tool-choice \
    --tool-call-parser qwen3_coder \
    --gpu-memory-utilization "${GPU_MEMORY_UTILIZATION}" \
    --enable-prefix-caching \
    --enforce-eager

echo "[INFO] qwen-vllm started (image=${VLLM_IMAGE_TAG}, util=${GPU_MEMORY_UTILIZATION}, max_model_len=${MAX_MODEL_LEN}, prefix-caching=on, enforce-eager=on)."
echo "[INFO] Probe readiness: curl -fsS -H 'Authorization: Bearer ${VLLM_API_KEY}' http://localhost:8010/v1/models"
