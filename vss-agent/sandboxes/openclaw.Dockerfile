# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Sandbox image — OpenClaw harness. One image, one harness: the base carries no
# agent, so the image identifies the harness under test and an eval run pairs
# (image, agent) unambiguously.
# tags: [nemoclaw-lineage, openclaw]
ARG BASE_IMAGE=ghcr.io/nvidia-ai-blueprints/vss/vss-harness-base:latest
FROM ${BASE_IMAGE}

ARG OPENCLAW_VERSION=latest
RUN . $NVM_DIR/nvm.sh && nvm use 22 \
    && npm install -g @openclaw/openclaw@${OPENCLAW_VERSION} && openclaw --version

LABEL harness.agent="openclaw"

# Skills are COPY'd by the profile (skills/<name> → /opt/skills/<name>);
# the vss config.json lands at /home/ubuntu/.vss/config.json.
