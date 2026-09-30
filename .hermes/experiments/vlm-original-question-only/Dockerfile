# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Hermes harness experiment arm — the published harness image (../../Dockerfile,
# built by CI) with one VLM-query rule appended to /sandbox/AGENTS.md: send the
# VLM only the original question.
# tags: [nemoclaw, hermes, vss-skills, vss-cli, experiment]
#
# Needs no build context: everything but the rule comes from HARNESS_IMAGE.
# The harness image unpacks to 125 layers; this arm adds one (126), under
# overlay2's limit of 128. HARNESS_IMAGE tracks develop's tip, the image CI
# republishes as develop-latest after every develop build; build with --pull
# so a cached copy does not stand in for it.
#
#   docker build --pull -t <registry>/vss-harness-hermes-vlm-original-question-only:<tag> - < Dockerfile

ARG HARNESS_IMAGE=ghcr.io/nvidia-ai-blueprints/vss/vss-harness-hermes:develop-latest

FROM ${HARNESS_IMAGE}
USER root
# Appending keeps the file's sandbox ownership and the workspace rules above it.
# The rule is the requester's wording, verbatim; the build fails unless that
# exact line lands in AGENTS.md.
RUN set -eu; \
    rule='Only send the original question to VLM'; \
    printf '\n## VLM query rule\n\n%s\n\n%s\n' "$rule" \
      'This rule overrides any other instruction in this workspace about how to query the VLM.' \
      >> /sandbox/AGENTS.md; \
    grep -qxF "$rule" /sandbox/AGENTS.md
USER sandbox
WORKDIR /sandbox

LABEL harness.experiment="vlm-original-question-only"
