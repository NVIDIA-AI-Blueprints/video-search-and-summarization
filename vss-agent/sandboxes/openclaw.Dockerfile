# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Sandbox image — OpenClaw harness. One image, one harness: the base carries no
# agent, so the image identifies the harness under test and an eval run pairs
# (image, agent) unambiguously.
# tags: [nemoclaw-lineage, openclaw]
ARG BASE_IMAGE=ghcr.io/nvidia-ai-blueprints/vss/vss-harness-base:latest
FROM ${BASE_IMAGE}

# @openclaw/openclaw is not published on the public npm registry
# (registry.npmjs.org returns 404 for it), so this build needs to be told where
# to get it. Both knobs are build args, nothing is hard-coded to a private host:
#   --build-arg OPENCLAW_NPM_REGISTRY=https://npm.internal.example.com/
#   --build-arg OPENCLAW_NPM_SPEC='@openclaw/openclaw@1.4.2'
#   --build-arg OPENCLAW_NPM_SPEC='https://…/openclaw-1.4.2.tgz'   (tarball URL)
# Leave OPENCLAW_NPM_REGISTRY empty to use whatever registry npm is configured
# with. CI (.github/workflows/sandbox-images.yml) passes neither today, so this
# variant only builds where the default registry serves the package.
ARG OPENCLAW_NPM_SPEC=@openclaw/openclaw@latest
ARG OPENCLAW_NPM_REGISTRY=
RUN . $NVM_DIR/nvm.sh && nvm use 22 \
    && if [ -n "$OPENCLAW_NPM_REGISTRY" ]; then npm config set registry "$OPENCLAW_NPM_REGISTRY"; fi \
    && npm install -g "$OPENCLAW_NPM_SPEC" && openclaw --version

LABEL harness.agent="openclaw"

# Skills are COPY'd by the profile (skills/<name> → /opt/skills/<name>);
# the vss config.json lands at /home/ubuntu/.vss/config.json.
