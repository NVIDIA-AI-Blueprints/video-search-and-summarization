# Sandbox Dockerfile — PI coding-agent harness (dev-team PICs).
# tags: [pi, dev-team, nvidia-inference]
#
# Replaces codex for dev PICs: PI talks to inference.nvidia.com (NVIDIA-internal,
# OpenAI-compatible) so the fleet has no external LLM dependency and the sandbox
# egress policy stays NVIDIA-only. Runs behind the OpenShell gateway in vss-dev.
ARG BASE_IMAGE=ghcr.io/nvidia-ai-blueprints/vss/vss-harness-base:latest
FROM ${BASE_IMAGE}

# pi runtime (same recipe as the harness provisioner's AGENT_RUNTIMES["pi"])
RUN . $NVM_DIR/nvm.sh && nvm use 22 \
    && npm install -g @mariozechner/pi-coding-agent@latest && pi --version

# Provider registration happens at sandbox start: the fleet worker writes
# ~/.pi/agent/models.json with baseUrl=$NVIDIA_INFERENCE_BASE_URL and the API key
# from the gateway credential provider. Never bake keys or models.json into the image.

LABEL harness.agent="pi"
