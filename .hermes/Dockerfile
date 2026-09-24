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
# deployment through the `vss` CLI on PATH, loads skills from $HERMES_HOME/skills
# (/sandbox/.hermes/skills, per NemoClaw's agents/hermes/manifest.yaml), which
# Hermes seeds from its bundled library, and instruction docs (SOUL.md, AGENTS.md, …) from /sandbox.
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
# uv 0.12.10, by index digest (multi-arch) — for the wheel builds in vss-src
# (the runtime uses the uv the base image ships).
ARG UV_IMAGE=ghcr.io/astral-sh/uv@sha256:2bb3ebca0a796a155094a27773d290c4b074572e6107f171d88d086682fd2500
ARG VSS_REPO=https://github.com/NVIDIA-AI-Blueprints/video-search-and-summarization
# develop at tags nightly-20260922-1 / dev-26.09.4
ARG VSS_REF=4abf68ecd10f40a74bb1978b44407990415a1ef8

# ── uv, for the vss-src wheel builds ───────────────────────────────────────────
FROM ${UV_IMAGE} AS uv

# ── VSS source checkout: skills + workspace docs + CLI wheels ──────────────────
FROM ${BUILDER_IMAGE} AS vss-src
ARG VSS_REPO
ARG VSS_REF
ARG NGC_CLI_VERSION=4.10.0
ARG TARGETARCH
# python3 (3.13 on trixie) for the wheel builds below; curl/unzip for the NGC CLI.
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates python3 curl unzip \
    && rm -rf /var/lib/apt/lists/*
COPY --from=uv /uv /usr/local/bin/uv
# Dev-loop override: a .vss-src/ snapshot in the build context (staged by
# stage_vss_src.py) wins over the pinned fetch, so a local skill or CLI edit
# reaches the image without a push. The Dockerfil[e] glob keeps this COPY
# valid when nothing is staged — the Dockerfile itself always matches.
# --chown=root:root: docker's COPY gives the files to root by default, kaniko
# keeps the build context's owner. Built by the eval harness (kaniko, context
# staged by a uid-10001 service) the directory arrived owned by 10001 while RUN
# executes as root, and `git init` refused it: "detected dubious ownership in
# repository at '/opt/vss-src'" (exit 128). Pinning the owner makes both
# engines agree and keeps git's safe.directory check intact.
COPY --chown=root:root Dockerfil[e] .vss-sr[c] /opt/vss-src/
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
# The vss CLI as wheels, riding the one /opt/vss-assets COPY the runtime's
# layer budget allows — only the CLI library workspace (libs/vss: the
# nvidia-vss-cli entry point and its nvidia-vss-core dependency). The agent
# service package under services/agent is deliberately NOT built or shipped:
# a sandbox operates a deployment through the CLI, it does not run the agent.
# Building here (where the checkout and its .git live) keeps VSS source out of
# the runtime image: the runtime installs the wheels by name. hatch-vcs reads
# the version from .git on the pinned path; a pre-staged snapshot has no .git
# and carries a PEP 440 pretend version in its STAGED marker instead.
RUN set -eux; \
    if [ -f /opt/vss-src/STAGED ]; then \
      export SETUPTOOLS_SCM_PRETEND_VERSION="$(sed -n 's/^version=//p' /opt/vss-src/STAGED)"; \
      test -n "$SETUPTOOLS_SCM_PRETEND_VERSION"; \
    fi; \
    mkdir -p /opt/vss-assets/wheels \
    && uv build --wheel --all-packages --project /opt/vss-src/libs/vss -o /opt/vss-assets/wheels \
    && ls /opt/vss-assets/wheels
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

# ── Managed Hermes runtime + vss CLI + skills + docs ───────────────────────────
# NemoClaw's Hermes image already has 124 filesystem layers, right under the
# overlay2 depth limit, so this stage adds exactly two: one COPY of the staged
# assets and one RUN for everything else. Do not split the RUN.
FROM ${BASE_IMAGE} AS nemoclaw-runtime
USER root
COPY --from=vss-src /opt/vss-assets/ /opt/vss-assets/
# In one layer:
#  - Harbor trial paths, real world-writable directories (OpenShell chowns every
#    read_write policy path at start and refuses symlinks). See ../openclaw/Dockerfile.
#  - The vss CLI from the wheels the vss-src stage built out of the same pinned
#    checkout as the skills (or the pre-staged snapshot) — installed by name,
#    so no VSS source ships in this image. uv and python 3.13 ship in the base;
#    the venv is its own, never /opt/hermes/.venv.
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
    # Installed by wheel PATH, not by name: with --find-links a bare name
    # still admits index candidates, so a future published nvidia-vss-cli
    # could outrank the wheel built from VSS_REF and silently break the
    # CLI==skills guarantee.
    && uv venv --python /opt/hermes/.venv/bin/python3 /usr/local/vss \
    && VIRTUAL_ENV=/usr/local/vss uv pip install /opt/vss-assets/wheels/*.whl \
    && ln -sf /usr/local/vss/bin/vss /usr/local/bin/vss \
    && vss --version \
    && su -s /bin/sh sandbox -c "vss --version" \
    # Skill discovery, same machinery as the OpenClaw plugin, same python
    # tool: the shipped set stays read-only under /opt/vss-skills/skills, and
    # sync_skills.py fills the active set from what `vss configure` records.
    # Hermes loads skills from $HERMES_HOME/skills only, and every
    # `hermes chat` first copies its bundled library (/opt/hermes/skills)
    # into that dir. So the VSS set ships inside the bundle, under vss/:
    # any HERMES_HOME -- the default /sandbox/.hermes or one a caller
    # picks -- gets it with nothing run beforehand. `vss-hermes-sync`
    # re-selects after `vss configure`; it owns $HERMES_HOME/skills/vss
    # only, leaving Hermes' own skills alone.
    && mkdir -p /opt/vss-skills \
    && cp -R /opt/vss-assets/skills /opt/vss-skills/skills \
    && cp /opt/vss-assets/sync_skills.py /opt/vss-skills/sync_skills.py \
    && test ! -e /opt/hermes/skills/vss \
    && python3 /opt/vss-skills/sync_skills.py --skills-dir /opt/vss-skills/skills --active-dir /opt/hermes/skills/vss --all \
    && chmod -R a+rX /opt/vss-skills /opt/hermes/skills/vss \
    && printf '#!/bin/sh\nactive="${HERMES_HOME:-/sandbox/.hermes}/skills/vss"\nmkdir -p "$active"\nexec python3 /opt/vss-skills/sync_skills.py --skills-dir /opt/vss-skills/skills --active-dir "$active" "$@"\n' > /usr/local/bin/vss-hermes-sync \
    && chmod 0755 /usr/local/bin/vss-hermes-sync \
    # Prove Hermes' own launch sync seeds a fresh HERMES_HOME, as sandbox.
    && su -s /bin/sh sandbox -c 'h=$(mktemp -d) && cd /opt/hermes && HERMES_HOME=$h /opt/hermes/.venv/bin/python3 -c "from tools.skills_sync import sync_skills; sync_skills(quiet=True)" && test -f $h/skills/vss/vss-ask-video/SKILL.md && HERMES_HOME=$h vss-hermes-sync --all && test -d $h/skills/github && rm -rf $h' \
    && cp /opt/vss-assets/workspace/*.md /sandbox/ \
    && for f in /opt/vss-assets/workspace/*.md; do chown sandbox:sandbox "/sandbox/$(basename "$f")"; done \
    && mv /opt/vss-assets/ngc-cli /usr/local/lib/ngc-cli \
    && chmod -R a+rX /usr/local/lib/ngc-cli \
    && chmod +x /usr/local/lib/ngc-cli/ngc \
    && ln -sf /usr/local/lib/ngc-cli/ngc /usr/local/bin/ngc \
    && ngc --version \
    && su -s /bin/sh sandbox -c "ngc --version" \
    && rm -rf /opt/vss-assets \
    && test -f /opt/hermes/skills/vss/vss-ask-video/SKILL.md && test -f /sandbox/AGENTS.md \
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
