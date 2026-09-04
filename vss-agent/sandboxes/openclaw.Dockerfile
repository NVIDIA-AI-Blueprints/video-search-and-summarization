# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Sandbox image — OpenClaw harness. One image, one harness: the image names the
# harness under test and pins its version, so an eval run pairs (image, agent)
# unambiguously even though the base ships a couple of agents of its own.
# tags: [nemoclaw-lineage, openclaw]
ARG BASE_IMAGE=ghcr.io/nvidia-ai-blueprints/vss/vss-harness-base:latest
FROM ${BASE_IMAGE}

# The package is `openclaw` on the public npm registry (the scoped name
# @openclaw/openclaw does not exist — that was the reason earlier builds here
# thought it was private). Pinned to the `extended-stable` dist-tag rather than
# `latest`, so an eval run's harness version does not move under it; override with
#   --build-arg OPENCLAW_NPM_SPEC='openclaw@2026.9.1'
#   --build-arg OPENCLAW_NPM_REGISTRY=https://npm.internal.example.com/
ARG OPENCLAW_NPM_SPEC=openclaw@2026.6.34
ARG OPENCLAW_NPM_REGISTRY=
USER root
RUN if [ -n "$OPENCLAW_NPM_REGISTRY" ]; then npm config set registry "$OPENCLAW_NPM_REGISTRY"; fi \
    && npm install -g "$OPENCLAW_NPM_SPEC" \
    && npm cache clean --force \
    && openclaw --version
USER sandbox

LABEL harness.agent="openclaw"

# The VSS skills come from the base at /opt/skills, symlinked into
# /sandbox/.agents/skills; the vss config.json lands at /sandbox/.vss/config.json.
