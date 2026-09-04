# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Sandbox Dockerfile — Codex CLI harness (dev-team PIC agents; strong at coding).
# tags: [nemoclaw-lineage, codex, dev-team]
ARG BASE_IMAGE=ghcr.io/nvidia-ai-blueprints/vss/vss-harness-base:latest
FROM ${BASE_IMAGE}

# The community base already carries codex (0.117.0 at the pinned digest); this
# layer is what pins the version an eval run measures, so the result never
# silently changes when the base is refreshed.
# node 22 lives at /usr/bin/node — there is no nvm to source. A global npm
# install writes to /usr/lib/node_modules, so this layer needs root; the image
# drops back to `sandbox` (the uid every trial runs as) immediately after.
USER root
RUN npm install -g @openai/codex@latest \
    && npm cache clean --force \
    && codex --version
USER sandbox

# auth note: codex needs OPENAI_API_KEY (or ChatGPT device login) — deliver via an
# OpenShell credential provider, never baked into the image.

LABEL harness.agent="codex"
