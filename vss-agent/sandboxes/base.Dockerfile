# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# vss-harness-base — the substrate every sandbox image in this catalog builds on.
# Published by CI to ghcr.io/<owner>/vss/vss-harness-base so nothing in the eval
# harness is built from a file outside this repo: the agent variants here just add
# their runtime on top of a pulled, tagged base.
#
# Apptainer-clean rules: no USER-dependent file ownership, no entrypoint reliance,
# agent state relocatable via env, run-time writes to tmpfs/binds only.

FROM ubuntu:24.04

RUN apt-get update && apt-get install -y --no-install-recommends \
      python3 python3-venv ca-certificates curl jq git nodejs npm ripgrep \
      iproute2 xz-utils build-essential \
    && rm -rf /var/lib/apt/lists/*

# Harness contract dirs (Harbor trial layout): the agent writes its answer to
# /output, logs to /logs, and works in /task; /solution and /tests belong to the
# oracle and verifier planes.
RUN mkdir -p /task /output /logs/agent /logs/verifier /logs/artifacts /solution /tests \
    && chmod -R 777 /task /output /logs /solution /tests
WORKDIR /task

# OpenShell requires a declared OCI USER; keep file perms user-independent.
RUN useradd -m -u 1000 -s /bin/bash ubuntu 2>/dev/null || true
USER ubuntu
ENV HOME=/home/ubuntu

# Node toolchain for the agent runtimes layered on top (nvm keeps versions
# switchable inside the sandbox without root).
ENV NVM_DIR=/home/ubuntu/.nvm
RUN mkdir -p $NVM_DIR \
    && curl -fsSL https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.1/install.sh | bash \
    && . $NVM_DIR/nvm.sh && nvm install 22 && nvm alias default 22

CMD ["sleep", "infinity"]
