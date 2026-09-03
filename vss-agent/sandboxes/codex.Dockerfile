# Sandbox Dockerfile — Codex CLI harness (dev-team PIC agents; strong at coding).
# tags: [nemoclaw-lineage, codex, dev-team]
ARG BASE_IMAGE=ghcr.io/nvidia-ai-blueprints/vss/vss-harness-base:latest
FROM ${BASE_IMAGE}

# codex runtime (recipe mirrors harbor's upstream adapter: @openai/codex via node 22)
RUN . $NVM_DIR/nvm.sh && nvm use 22 \
    && npm install -g @openai/codex@latest && codex --version

# auth note: codex needs OPENAI_API_KEY (or ChatGPT device login) — deliver via an
# OpenShell credential provider, never baked into the image.
