# Example sandbox Dockerfile — Hermes (NousResearch hermes-agent) harness.
# tags: [nemoclaw-lineage, hermes]
ARG BASE_IMAGE=ghcr.io/nvidia-ai-blueprints/vss/vss-harness-base:latest
FROM ${BASE_IMAGE}

# hermes runtime (recipe mirrors harbor's upstream adapter; base ships xz-utils +
# build-essential which the installer needs for node-pty)
RUN curl -fsSL https://raw.githubusercontent.com/NousResearch/hermes-agent/main/scripts/install.sh \
      | bash -s -- --skip-setup \
    && export PATH="$HOME/.local/bin:$PATH" && command -v hermes
ENV HERMES_HOME=/tmp/hermes
