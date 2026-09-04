# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Sandbox Dockerfile — Hermes (NousResearch hermes-agent) harness.
# tags: [nemoclaw-lineage, hermes]
ARG BASE_IMAGE=ghcr.io/nvidia-ai-blueprints/vss/vss-harness-base:latest
FROM ${BASE_IMAGE}

# The installer drops hermes into $HOME/.local/bin, and HOME is /sandbox here —
# so this installs as `sandbox` and needs no root. --skip-setup keeps the
# interactive provider/API-key wizard out of the build.
RUN curl -fsSL https://raw.githubusercontent.com/NousResearch/hermes-agent/main/scripts/install.sh \
      | bash -s -- --skip-setup \
    && PATH="$HOME/.local/bin:$PATH" command -v hermes
ENV PATH=/sandbox/.local/bin:$PATH
# hermes writes session state under HERMES_HOME; /task is the trial's own
# writable dir, whereas /tmp may be a tmpfs the policy remounts between steps.
ENV HERMES_HOME=/sandbox/.hermes

LABEL harness.agent="hermes"
