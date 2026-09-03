# Example sandbox Dockerfile — OpenClaw harness + pinned vss CLI.
# tags: [nemoclaw-lineage, openclaw, vss-cli-0.6.0]
ARG BASE_IMAGE=ghcr.io/nvidia-ai-blueprints/vss/vss-harness-base:latest
FROM ${BASE_IMAGE}

# pin the vss CLI (swap the version/index for your release plane)
USER root
RUN python3 -m venv /opt/vss \
    && /opt/vss/bin/pip install "nvidia-vss[cli]==0.6.0" \
    && ln -sf /opt/vss/bin/vss /usr/local/bin/vss
USER ubuntu

# install any additional CLIs/tools the agent should have, e.g.:
# USER root
# RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*
# USER ubuntu
