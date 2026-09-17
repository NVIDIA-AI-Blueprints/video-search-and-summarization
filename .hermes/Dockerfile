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
# Build context is this directory (.hermes); skills, workspace docs
# and the vss CLI all come from one pinned commit of this repo (VSS_REF) — or,
# dev loop, from a `.vss-src/` working-tree snapshot placed here by
# skills/vss-build-vision-ai/scripts/stage_vss_src.py, which wins over the pin
# when present.
#
#   docker build -t <registry>/vss-harness-hermes:<tag> .hermes

ARG BASE_IMAGE=ghcr.io/nvidia/nemoclaw/hermes-sandbox@sha256:32d7ed6a6f1b682991818a2acaf5636d9fb066ae33265b011837b0ba601c0970
ARG BUILDER_IMAGE=node:22-trixie-slim@sha256:db8a96a63e5264607ada2d206758876ebbed6a12be2ada7517793cbfb0c2a29c
ARG VSS_REPO=https://github.com/NVIDIA-AI-Blueprints/video-search-and-summarization
ARG VSS_REF=9dc06553d67fcc21882fe0620b33f7a094b38f72

# ── VSS source checkout: operation skills + workspace docs ─────────────────────
FROM ${BUILDER_IMAGE} AS vss-src
ARG VSS_REPO
ARG VSS_REF
ARG NGC_CLI_VERSION
ARG TARGETARCH
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates curl unzip \
    && rm -rf /var/lib/apt/lists/*
# Dev-loop override: a .vss-src/ snapshot in the build context (staged by
# stage_vss_src.py) wins over the pinned fetch, so a local skill or CLI edit
# reaches the image without a push. The Dockerfil[e] glob keeps this COPY
# valid when nothing is staged — the Dockerfile itself always matches.
COPY Dockerfil[e] .vss-sr[c] /opt/vss-src/
RUN rm -f /opt/vss-src/Dockerfile \
    && if [ -f /opt/vss-src/STAGED ]; then \
         echo "vss-src: pre-staged working-tree snapshot" && cat /opt/vss-src/STAGED; \
       else \
         git init -q /opt/vss-src \
         && git -C /opt/vss-src fetch -q --depth 1 "$VSS_REPO" "$VSS_REF" \
         && git -C /opt/vss-src checkout -q FETCH_HEAD; \
       fi
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
# The skill-selection tool, shared with the OpenClaw harness and the host:
# the harness-neutral python selector from the pinned VSS_REF checkout.
RUN cp /opt/vss-src/skills/vss-build-vision-ai/scripts/sync_skills.py /opt/vss-assets/sync_skills.py
# Workspace docs: the shared set plus the _nemoclaw overlay, overlay winning,
# flattened the way deploy_nemoclaw.ipynb uploads them for Hermes (into /sandbox).
RUN mkdir -p /opt/vss-assets/workspace \
    && cp /opt/vss-src/.openclaw/workspace/*.md /opt/vss-assets/workspace/ \
    && cp /opt/vss-src/.openclaw/workspace/_nemoclaw/*.md /opt/vss-assets/workspace/ \
    && test -f /opt/vss-assets/workspace/AGENTS.md && test -f /opt/vss-assets/workspace/ENV.md
# NGC CLI, unpacked into the staged assets so it rides the runtime stage's
# single COPY and costs no extra layer. Fetched in this builder rather than the
# managed base, which carries no guarantee of `curl` or `unzip`.
RUN set -eux; \
    case "${TARGETARCH:-amd64}" in \
      amd64) ngc_zip=ngccli_linux.zip ;; \
      arm64) ngc_zip=ngccli_arm64.zip ;; \
      *) echo "no NGC CLI build for TARGETARCH=${TARGETARCH}" >&2; exit 1 ;; \
    esac; \
    curl -fsSLo /tmp/ngccli.zip \
      "https://api.ngc.nvidia.com/v2/resources/nvidia/ngc-apps/ngc_cli/versions/${NGC_CLI_VERSION}/files/${ngc_zip}"; \
    unzip -qo /tmp/ngccli.zip -d /opt/vss-assets; \
    rm -f /tmp/ngccli.zip; \
    # The zip also carries a sibling ngc-cli.md5, computed from stdin so what it
    # covers is undocumented. Drop it rather than ship it into the COPY layer.
    rm -f /opt/vss-assets/ngc-cli.md5; \
    test -f /opt/vss-assets/ngc-cli/ngc
# CLI-source handoff for a pre-staged snapshot: it has no .git for the runtime
# stage to re-fetch, so its CLI sources and STAGED marker ride the one
# /opt/vss-assets COPY the runtime's layer budget allows (absent on the pinned
# path, where the runtime fetches). The services/ + libs/ layout is preserved
# because services/agent's editable sources point at ../../libs/vss (the CLI
# library workspace; guarded — absent on trees from before that move).
RUN if [ -f /opt/vss-src/STAGED ]; then \
      mkdir -p /opt/vss-assets/vss-cli-src/services \
      && cp -R /opt/vss-src/services/agent /opt/vss-assets/vss-cli-src/services/agent \
      && cp /opt/vss-src/STAGED /opt/vss-assets/STAGED \
      && if [ -d /opt/vss-src/libs/vss ]; then \
           mkdir -p /opt/vss-assets/vss-cli-src/libs \
           && cp -R /opt/vss-src/libs/vss /opt/vss-assets/vss-cli-src/libs/vss; \
         fi; \
    fi

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
#    3.13 ship in the base; the venv is its own, never /opt/hermes/.venv. A
#    pre-staged snapshot (STAGED in /opt/vss-assets) supplies services/agent
#    directly and a pretend version, since a snapshot has no .git to read.
#  - Skills into Hermes' canonical writable skill root (sandbox-owned, like the
#    ones `nemohermes <sb> skill install` places there); docs into /sandbox,
#    where Hermes and the notebook's verification expect them.
#  - The NGC CLI from the staged assets, on PATH for root and the sandbox user.
#    Unconditional: an operation skill that resolves a sample video by name
#    shells out to `ngc` directly and cannot install it from inside the sandbox.
#  - NemoClaw's per-shell `ulimit -u 512` hooks removed: fatal under the
#    OpenShell k8s driver (container runs as uid 0; RLIMIT_NPROC for root counts
#    every root process on the node). Process limits are the cluster's job.
RUN set -eux; \
    for d in task output logs tests solution; do mkdir -p "/$d" && chmod 777 "/$d"; done \
    && if [ -f /opt/vss-assets/STAGED ]; then \
         export SETUPTOOLS_SCM_PRETEND_VERSION="$(sed -n 's/^version=//p' /opt/vss-assets/STAGED)" \
         && test -n "$SETUPTOOLS_SCM_PRETEND_VERSION" \
         && mkdir -p /usr/local/src/vss \
         && cp -R /opt/vss-assets/vss-cli-src/services /usr/local/src/vss/services \
         && if [ -d /opt/vss-assets/vss-cli-src/libs ]; then cp -R /opt/vss-assets/vss-cli-src/libs /usr/local/src/vss/libs; fi; \
       else \
         git init -q /usr/local/src/vss \
         && git -C /usr/local/src/vss fetch -q --depth 1 "$VSS_REPO" "$VSS_REF" \
         && git -C /usr/local/src/vss checkout -q FETCH_HEAD; \
       fi \
    && uv venv --python /opt/hermes/.venv/bin/python3 /usr/local/vss \
    && VIRTUAL_ENV=/usr/local/vss uv pip install "nvidia-vss[cli] @ /usr/local/src/vss/services/agent" \
    && ln -sf /usr/local/vss/bin/vss /usr/local/bin/vss \
    # Strip to what the editable install points at: services/agent, plus
    # libs/vss when the source tree has it (the CLI library moved there; the
    # pinned VSS_REF predates the move, so the pinned path is unchanged).
    && mv /usr/local/src/vss/services/agent /usr/local/src/vss-agent \
    && if [ -d /usr/local/src/vss/libs/vss ]; then mkdir -p /usr/local/src/vss-libs && mv /usr/local/src/vss/libs/vss /usr/local/src/vss-libs/vss; fi \
    && rm -rf /usr/local/src/vss \
    && mkdir -p /usr/local/src/vss/services \
    && mv /usr/local/src/vss-agent /usr/local/src/vss/services/agent \
    && if [ -d /usr/local/src/vss-libs ]; then mkdir -p /usr/local/src/vss/libs && mv /usr/local/src/vss-libs/vss /usr/local/src/vss/libs/vss && rmdir /usr/local/src/vss-libs; fi \
    && chmod -R a+rX /usr/local/src/vss \
    && vss --version \
    && su -s /bin/sh sandbox -c "vss --version" \
    # Skill discovery, same machinery as the OpenClaw plugin, same python
    # tool: the shipped set stays read-only under /opt/vss-skills/skills, and
    # sync_skills.py fills the active set from what `vss configure` records.
    # Hermes scans /sandbox/.hermes/skills natively, so that IS the active
    # dir. --all at build activates everything; `vss-hermes-sync` re-selects
    # after `vss configure`.
    && mkdir -p /opt/vss-skills \
    && cp -R /opt/vss-assets/skills /opt/vss-skills/skills \
    && cp /opt/vss-assets/sync_skills.py /opt/vss-skills/sync_skills.py \
    && mkdir -p /sandbox/.hermes/skills \
    && chmod -R a+rX /opt/vss-skills \
    && printf '#!/bin/sh\nexec python3 /opt/vss-skills/sync_skills.py --skills-dir /opt/vss-skills/skills --active-dir /sandbox/.hermes/skills "$@"\n' > /usr/local/bin/vss-hermes-sync \
    && chmod 0755 /usr/local/bin/vss-hermes-sync \
    && python3 /opt/vss-skills/sync_skills.py --skills-dir /opt/vss-skills/skills --active-dir /sandbox/.hermes/skills --all \
    && chown -R sandbox:sandbox /sandbox/.hermes/skills \
    && su -s /bin/sh sandbox -c "vss-hermes-sync --all" \
    && cp /opt/vss-assets/workspace/*.md /sandbox/ \
    && for f in /opt/vss-assets/workspace/*.md; do chown sandbox:sandbox "/sandbox/$(basename "$f")"; done \
    && mv /opt/vss-assets/ngc-cli /usr/local/lib/ngc-cli \
    && chmod -R a+rX /usr/local/lib/ngc-cli \
    && chmod +x /usr/local/lib/ngc-cli/ngc \
    && ln -sf /usr/local/lib/ngc-cli/ngc /usr/local/bin/ngc \
    && ngc --version \
    && su -s /bin/sh sandbox -c "ngc --version" \
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
      harness.ngc-cli="true" \
      harness.workspace-root="/sandbox"
