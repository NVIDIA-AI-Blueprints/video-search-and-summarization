# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Hermes harness image — NemoClaw's managed Hermes runtime + the VSS operation
# skills + the workspace docs + the pinned vss CLI.
# tags: [nemoclaw, hermes, vss-skills, vss-cli]
#
# Same shape as ../openclaw/Dockerfile and NemoClaw's documented custom-image
# workflow (docs.nvidia.com/nemoclaw → Install Hermes Plugins): start from the
# complete managed runtime, add image-owned files, end with USER sandbox and
# WORKDIR /sandbox. Hermes has no plugin/tool layer to add here: it drives the
# deployment through the `vss` CLI on PATH, and loads skills from its canonical
# writable root (/sandbox/.hermes/skills, per NemoClaw's agents/hermes/manifest.yaml)
# and instruction docs (SOUL.md, AGENTS.md, …) from /sandbox.
#
# NemoClaw publishes hermes-sandbox by publication cohort only (no release tag,
# NVIDIA/NemoClaw#11228). The cohort a release selects is the one its
# openclaw-sandbox release tag is labeled with:
#   openclaw-sandbox:v0.0.114 → io.nvidia.nemoclaw.managed-image.cohort=ghrun-32694475593-1
#   hermes-sandbox:cohort-ghrun-32694475593-1 → the digest below
# Move BASE_IMAGE together with ../openclaw/Dockerfile's BASE_IMAGE and the
# notebook's NEMOCLAW_INSTALL_REF: NemoClaw requires CLI and images to be one release.
#
# Build context is this directory (agent-harness/hermes); skills, workspace docs
# and the vss CLI all come from one pinned commit of this repo (VSS_REF).
#
#   docker build -t <registry>/vss-harness-hermes:<tag> agent-harness/hermes

ARG BASE_IMAGE=ghcr.io/nvidia/nemoclaw/hermes-sandbox@sha256:32d7ed6a6f1b682991818a2acaf5636d9fb066ae33265b011837b0ba601c0970
ARG BUILDER_IMAGE=node:22-trixie-slim@sha256:db8a96a63e5264607ada2d206758876ebbed6a12be2ada7517793cbfb0c2a29c
ARG VSS_REPO=https://github.com/NVIDIA-AI-Blueprints/video-search-and-summarization
ARG VSS_REF=e7ea9804a6444f5d9a9dd3256ba6ba9a2a81e89d

# ── VSS source checkout: operation skills + workspace docs ─────────────────────
FROM ${BUILDER_IMAGE} AS vss-src
ARG VSS_REPO
ARG VSS_REF
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*
RUN git init -q /opt/vss-src \
    && git -C /opt/vss-src fetch -q --depth 1 "$VSS_REPO" "$VSS_REF" \
    && git -C /opt/vss-src checkout -q FETCH_HEAD
# Operation skills: every skill whose SKILL.md frontmatter declares vss-requires
# (the same selection rule as the OpenClaw plugin's stage-assets.sh).
RUN mkdir -p /opt/vss-assets/skills \
    && find /opt/vss-src/skills -name SKILL.md | sort | while read -r f; do \
         if sed -n '2,/^---$/p' "$f" | grep -qE '^[[:space:]]*vss-requires:'; then \
           d=$(dirname "$f"); cp -R "$d" "/opt/vss-assets/skills/$(basename "$d")"; \
         fi; \
       done \
    && test "$(ls /opt/vss-assets/skills | wc -l)" -gt 0 \
    && ls /opt/vss-assets/skills
# Workspace docs: the shared set plus the _nemoclaw overlay, overlay winning,
# flattened the way deploy_nemoclaw.ipynb uploads them for Hermes (into /sandbox).
RUN mkdir -p /opt/vss-assets/workspace \
    && cp /opt/vss-src/agent-harness/openclaw/workspace/*.md /opt/vss-assets/workspace/ \
    && cp /opt/vss-src/agent-harness/openclaw/workspace/_nemoclaw/*.md /opt/vss-assets/workspace/ \
    && test -f /opt/vss-assets/workspace/AGENTS.md && test -f /opt/vss-assets/workspace/ENV.md

# ── Managed Hermes runtime + vss CLI + skills + docs ───────────────────────────
# NemoClaw's Hermes image already has 124 filesystem layers, right under the
# overlay2 depth limit, so this stage adds exactly two: one COPY of the staged
# assets and one RUN for everything else. Do not split the RUN.
FROM ${BASE_IMAGE} AS nemoclaw-runtime
USER root
ARG VSS_REPO
ARG VSS_REF
COPY --from=vss-src /opt/vss-assets/ /opt/vss-assets/
# In one layer:
#  - Harbor trial paths, real world-writable directories (OpenShell chowns every
#    read_write policy path at start and refuses symlinks). See ../openclaw/Dockerfile.
#  - The vss CLI from the same pinned checkout as the skills: installed editable
#    (uv workspace members) on the intact checkout (setuptools-scm versioning),
#    then the tree stripped to what the editable links point at. uv and python
#    3.13 ship in the base; the venv is its own, never /opt/hermes/.venv.
#  - Skills into Hermes' canonical writable skill root (sandbox-owned, like the
#    ones `nemohermes <sb> skill install` places there); docs into /sandbox,
#    where Hermes and the notebook's verification expect them.
#  - NemoClaw's per-shell `ulimit -u 512` hooks removed: fatal under the
#    OpenShell k8s driver (container runs as uid 0; RLIMIT_NPROC for root counts
#    every root process on the node). Process limits are the cluster's job.
RUN set -eux; \
    for d in task output logs tests solution; do mkdir -p "/$d" && chmod 777 "/$d"; done \
    && git init -q /usr/local/src/vss \
    && git -C /usr/local/src/vss fetch -q --depth 1 "$VSS_REPO" "$VSS_REF" \
    && git -C /usr/local/src/vss checkout -q FETCH_HEAD \
    && uv venv --python /opt/hermes/.venv/bin/python3 /usr/local/vss \
    && VIRTUAL_ENV=/usr/local/vss uv pip install "nvidia-vss[cli] @ /usr/local/src/vss/services/agent" \
    && ln -sf /usr/local/vss/bin/vss /usr/local/bin/vss \
    && mv /usr/local/src/vss/services/agent /usr/local/src/vss-agent \
    && rm -rf /usr/local/src/vss \
    && mkdir -p /usr/local/src/vss/services \
    && mv /usr/local/src/vss-agent /usr/local/src/vss/services/agent \
    && chmod -R a+rX /usr/local/src/vss \
    && vss --version \
    && su -s /bin/sh sandbox -c "vss --version" \
    && cp -R /opt/vss-assets/skills/. /sandbox/.hermes/skills/ \
    && cp /opt/vss-assets/workspace/*.md /sandbox/ \
    && chown -R sandbox:sandbox /sandbox/.hermes/skills \
    && for f in /opt/vss-assets/workspace/*.md; do chown sandbox:sandbox "/sandbox/$(basename "$f")"; done \
    && rm -rf /opt/vss-assets \
    && test -f /sandbox/.hermes/skills/vss-ask-video/SKILL.md && test -f /sandbox/AGENTS.md \
    && rm -f /etc/profile.d/nemoclaw-rlimits.sh \
    && sed -i '/sandbox-rlimits/d; /NemoClaw sandbox resource limits/d' /etc/bash.bashrc \
    && ! grep -rq "sandbox-rlimits" /etc/profile.d /etc/bash.bashrc

# Final USER is the non-root default OpenShell requires; WORKDIR /sandbox is the
# OpenShell workspace. The runtime's CMD (/bin/bash) exits without a TTY, so the
# eval harness's exec-driven sandbox needs a command that stays alive.
USER sandbox
WORKDIR /sandbox
CMD ["sleep", "infinity"]

LABEL harness.base="nemoclaw-hermes-sandbox" \
      harness.contract="harbor-trial-v1" \
      harness.agent="hermes" \
      harness.vss-cli="true" \
      harness.workspace-root="/sandbox"
