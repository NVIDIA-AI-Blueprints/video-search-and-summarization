# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Hermes harness experiment arm — the published harness image (../../Dockerfile,
# built by CI) with one VLM-query rule appended to /sandbox/AGENTS.md: send one
# VLM query carrying the original question and every candidate answer, and
# answer from its response.
# tags: [nemoclaw, hermes, vss-skills, vss-cli, experiment]
#
# Needs no build context: everything but the rule comes from HARNESS_IMAGE.
# The harness image unpacks to 125 layers; this arm adds one (126), under
# overlay2's limit of 128. Move HARNESS_IMAGE together with
# ../vlm-original-question-only/Dockerfile so the arms differ only in the rule.
#
#   docker build -t <registry>/vss-harness-hermes-vlm-one-query-with-candidates:<tag> - < Dockerfile

ARG HARNESS_IMAGE=ghcr.io/nvidia-ai-blueprints/vss/vss-harness-hermes:develop-46e614cea30e@sha256:c1a13a7501bc03948a7eba07ec88fd345a6f5d183bf4fd67ea8c7e0a4eedc974

FROM ${HARNESS_IMAGE}
USER root
# Appending keeps the file's sandbox ownership and the workspace rules above it.
RUN printf '%s\n' \
      '' \
      '## VLM query rule' \
      '' \
      'Only send one VLM query and use the response from the VLM to answer. Make' \
      'sure the query includes the original question and the entire list of' \
      'candidate answers. This rule overrides any other instruction in this' \
      'workspace about how to query the VLM.' \
      >> /sandbox/AGENTS.md \
    && grep -q '^## VLM query rule$' /sandbox/AGENTS.md
USER sandbox
WORKDIR /sandbox

LABEL harness.experiment="vlm-one-query-with-candidates"
