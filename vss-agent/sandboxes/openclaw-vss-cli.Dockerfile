# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Sandbox Dockerfile — OpenClaw harness + pinned vss CLI.
# tags: [nemoclaw-lineage, openclaw, vss-cli-0.6.0]
ARG BASE_IMAGE=ghcr.io/nvidia-ai-blueprints/vss/vss-harness-base:latest
FROM ${BASE_IMAGE}

# Pin the vss CLI. `nvidia-vss` is not on public PyPI (pypi.org returns 404), so
# the index has to be supplied for this layer to resolve:
#   --build-arg VSS_PIP_INDEX_URL=https://pypi.internal.example.com/simple
#   --build-arg VSS_CLI_SPEC='nvidia-vss[cli]==0.6.0'
# Installed into its own venv so it can never perturb /sandbox/.venv, which is
# the interpreter the agent's own tooling runs from.
USER root
ARG VSS_CLI_SPEC=nvidia-vss[cli]==0.6.0
ARG VSS_PIP_INDEX_URL=
RUN uv venv /opt/vss \
    && VIRTUAL_ENV=/opt/vss uv pip install ${VSS_PIP_INDEX_URL:+--index-url "$VSS_PIP_INDEX_URL"} "$VSS_CLI_SPEC" \
    && ln -sf /opt/vss/bin/vss /usr/local/bin/vss

# install any additional CLIs/tools the agent should have, e.g.:
# RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*

# The package is `openclaw` on the public npm registry (the scoped name
# @openclaw/openclaw does not exist — that was the reason earlier builds here
# thought it was private). Pinned to the `extended-stable` dist-tag rather than
# `latest`, so an eval run's harness version does not move under it; override with
#   --build-arg OPENCLAW_NPM_SPEC='openclaw@2026.9.1'
#   --build-arg OPENCLAW_NPM_REGISTRY=https://npm.internal.example.com/
ARG OPENCLAW_NPM_SPEC=openclaw@2026.6.34
ARG OPENCLAW_NPM_REGISTRY=
RUN if [ -n "$OPENCLAW_NPM_REGISTRY" ]; then npm config set registry "$OPENCLAW_NPM_REGISTRY"; fi \
    && npm install -g "$OPENCLAW_NPM_SPEC" \
    && npm cache clean --force \
    && openclaw --version
USER sandbox

LABEL harness.agent="openclaw" \
      harness.vss-cli="true"
