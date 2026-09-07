# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# OpenClaw harness image — NemoClaw's managed OpenClaw runtime + the VSS OpenClaw
# plugin (vss CLI tool + VSS skills + workspace instructions) + the pinned vss CLI.
# tags: [nemoclaw, openclaw, vss-plugin, vss-cli]
#
# Layout follows NemoClaw's documented custom-image workflow
# (docs.nvidia.com/nemoclaw → Install OpenClaw Plugins): take the complete
# managed runtime, name it `nemoclaw-runtime`, build the plugin in a separate
# stage from its lockfile, then extend the runtime with `openclaw plugins
# install` as the sandbox user and refresh the managed config hash.
#
# The doc starts from NemoClaw's stock Dockerfile with a version-matched source
# checkout as the build context. That Dockerfile COPYs from a dozen places in
# the NemoClaw tree, so it cannot be vendored here. NemoClaw's managed-images
# workflow publishes the image that Dockerfile produces, so we `FROM` that
# image at an immutable digest instead: same layers, no foreign checkout.
#
# Build context is this directory (agent-harness/openclaw): the plugin source is
# COPYd from ./plugin and the OpenClaw workspace instructions from ./workspace;
# the skills and the vss CLI come from a pinned commit of this repo so the two
# always match.
#
#   docker build -t <registry>/vss-harness-openclaw:<tag> agent-harness/openclaw

# NemoClaw v0.0.99 managed OpenClaw runtime: OpenClaw 2026.7.1, node 22.23.1,
# python 3.13, git, the nemoclaw plugin, nemoclaw-start entrypoint.
# Pinned by digest — a mutable tag with IfNotPresent silently serves whatever
# the node cached first. Move it deliberately, together with OPENCLAW_VERSION.
ARG BASE_IMAGE=ghcr.io/nvidia/nemoclaw/openclaw-sandbox@sha256:5a134d92a4f1eba037c125d21aff11ce1d6689dbf211607f5e0bb5552d5e5ba0
# The OpenClaw the base carries. The plugin's devDependency must match it, so
# the compiled plugin links against the runtime it will actually load in.
ARG OPENCLAW_VERSION=2026.7.1
# Same builder image NemoClaw's Dockerfile uses for its plugin stages.
ARG BUILDER_IMAGE=node:22-trixie-slim@sha256:db8a96a63e5264607ada2d206758876ebbed6a12be2ada7517793cbfb0c2a29c
ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.12.10
# Skills and the vss CLI: one pinned commit of this repo, so an eval can say
# exactly which it measured. Override with --build-arg VSS_REF=<sha>.
ARG VSS_REPO=https://github.com/NVIDIA-AI-Blueprints/video-search-and-summarization
ARG VSS_REF=a7cd4bc9d5ad513acfe38bc8724e9c37e64cd2cf

# ── VSS source checkout ────────────────────────────────────────────────────────
FROM ${BUILDER_IMAGE} AS vss-src
ARG VSS_REPO
ARG VSS_REF
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*
RUN git init -q /opt/vss-src \
    && git -C /opt/vss-src fetch -q --depth 1 "$VSS_REPO" "$VSS_REF" \
    && git -C /opt/vss-src checkout -q FETCH_HEAD

# ── Plugin build (from its lockfile) ───────────────────────────────────────────
FROM ${BUILDER_IMAGE} AS vss-plugin-builder
ARG OPENCLAW_VERSION
ENV NPM_CONFIG_AUDIT=false NPM_CONFIG_FUND=false NPM_CONFIG_UPDATE_NOTIFIER=false
WORKDIR /opt/vss-plugin
COPY plugin/package.json plugin/package-lock.json plugin/tsconfig.json ./
# The lockfile pins the openclaw devDependency; it has to be the base's version.
RUN node -e 'const l=require("./package-lock.json"); const v=l.packages["node_modules/openclaw"].version; if (v!==process.argv[1]) { console.error(`plugin lockfile pins openclaw ${v}, base image carries ${process.argv[1]}`); process.exit(1); }' "$OPENCLAW_VERSION"
RUN npm ci --ignore-scripts --no-audit --no-fund
COPY plugin/openclaw.plugin.json plugin/stage-assets.sh ./
COPY plugin/src/ ./src/
RUN npm run build \
    && npm prune --omit=dev --omit=peer --ignore-scripts --no-audit --no-fund \
    && test ! -e node_modules/openclaw
# Content assets: the VSS skills (one directory per SKILL.md regardless of how
# the repo groups them) from the pinned checkout, and the OpenClaw workspace
# instructions from this directory, staged under the plugin root where the
# manifest's `skills: ["./skills"]` and the register-time seeding look.
COPY --from=vss-src /opt/vss-src/skills /tmp/vss-skills
COPY workspace/ /tmp/vss-workspace/
RUN ./stage-assets.sh /tmp/vss-skills /tmp/vss-workspace \
    && rm -rf /tmp/vss-skills /tmp/vss-workspace

# ── uv, for the vss CLI venv ───────────────────────────────────────────────────
FROM ${UV_IMAGE} AS uv

# ── Managed runtime + vss CLI ──────────────────────────────────────────────────
FROM ${BASE_IMAGE} AS nemoclaw-runtime
USER root

# Harbor trial paths. Harbor and its agent adapters address /task, /output,
# /logs, /tests and /solution by name, but everything physically lives under
# /sandbox — the OpenShell workspace (WORKDIR below), which the sandbox user
# owns and `openshell sandbox download` serves. Nothing is created here: the
# harness creates the directories at trial start as the sandbox user; these are
# only root-level names pointing into the workspace.
RUN for d in task output logs tests solution; do ln -s "/sandbox/$d" "/$d"; done

# The vss CLI. nvidia-vss is on no reachable index, so it is installed from the
# same pinned checkout the skills came from. Its packages are uv workspace
# members (installed editable, so the checkout stays) and take their version
# from git metadata (so the install runs before .git is stripped). Own venv, so
# it never touches the runtime's python.
ARG VSS_REPO
ARG VSS_REF
COPY --from=uv /uv /usr/local/bin/uv
RUN set -eux; \
    git init -q /usr/local/src/vss \
    && git -C /usr/local/src/vss fetch -q --depth 1 "$VSS_REPO" "$VSS_REF" \
    && git -C /usr/local/src/vss checkout -q FETCH_HEAD \
    && uv venv --python /usr/bin/python3 /usr/local/vss \
    && VIRTUAL_ENV=/usr/local/vss uv pip install "nvidia-vss[cli] @ /usr/local/src/vss/services/agent" \
    && ln -sf /usr/local/vss/bin/vss /usr/local/bin/vss \
    && mv /usr/local/src/vss/services/agent /usr/local/src/vss-agent \
    && rm -rf /usr/local/src/vss \
    && mkdir -p /usr/local/src/vss/services \
    && mv /usr/local/src/vss-agent /usr/local/src/vss/services/agent \
    && chmod -R a+rX /usr/local/src/vss \
    && vss --version \
    && gosu sandbox vss --version

# ── Extend the managed runtime with the VSS plugin ─────────────────────────────
FROM nemoclaw-runtime AS vss-runtime
# Re-declared here because build args are stage-scoped and this is the final stage.
ARG NEMOCLAW_TOOL_DISCLOSURE=progressive
ENV NEMOCLAW_TOOL_DISCLOSURE=${NEMOCLAW_TOOL_DISCLOSURE}
COPY --from=vss-plugin-builder --chown=sandbox:sandbox \
    /opt/vss-plugin/package.json \
    /opt/vss-plugin/package-lock.json \
    /opt/vss-plugin/openclaw.plugin.json \
    /opt/vss-plugin/
COPY --from=vss-plugin-builder --chown=sandbox:sandbox /opt/vss-plugin/dist/ /opt/vss-plugin/dist/
COPY --from=vss-plugin-builder --chown=sandbox:sandbox /opt/vss-plugin/node_modules/ /opt/vss-plugin/node_modules/
COPY --from=vss-plugin-builder --chown=sandbox:sandbox /opt/vss-plugin/skills/ /opt/vss-plugin/skills/
COPY --from=vss-plugin-builder --chown=sandbox:sandbox /opt/vss-plugin/workspace/ /opt/vss-plugin/workspace/

# Staged outside the managed extensions dir and installed (copied) into it, per
# the NemoClaw doc. The pre-check fails the build if npm kept a private
# node_modules/openclaw that would stop OpenClaw creating its runtime link; the
# post-checks prove the link exists and resolves to the image's own runtime.
USER sandbox
RUN test ! -e /opt/vss-plugin/node_modules/openclaw \
    && HOME=/sandbox openclaw plugins install /opt/vss-plugin \
    && test -L /sandbox/.openclaw/extensions/vss/node_modules/openclaw \
    && test "$(realpath /sandbox/.openclaw/extensions/vss/node_modules/openclaw)" = "$(realpath /usr/local/lib/node_modules/openclaw)" \
    && HOME=/sandbox openclaw plugins enable vss \
    && HOME=/sandbox openclaw plugins inspect vss --json > /dev/null \
    && test -d /sandbox/.openclaw/extensions/vss/skills \
    && test -f /sandbox/.openclaw/extensions/vss/workspace/AGENTS.md

# Enabling the plugin changes openclaw.json after the managed runtime hashed it;
# nemoclaw-start verifies that hash, so refresh it.
USER root
RUN chown sandbox:sandbox /sandbox/.openclaw/openclaw.json \
    && chmod 660 /sandbox/.openclaw/openclaw.json \
    && sha256sum /sandbox/.openclaw/openclaw.json > /sandbox/.openclaw/.config-hash \
    && chown sandbox:sandbox /sandbox/.openclaw/.config-hash \
    && chmod 660 /sandbox/.openclaw/.config-hash

# Workspace-variant selector for the plugin's register-time seeding: apply the
# _nemoclaw overlay (ENV.md, sandbox host alias, proxy notes) in this image.
ENV VSS_WORKSPACE_VARIANT=nemoclaw

# Final USER is the non-root default OpenShell requires. WORKDIR /sandbox makes
# /sandbox the OpenShell workspace (OpenShell derives it from the OCI WorkingDir).
# Entrypoint, command and health check are inherited from the managed runtime.
USER sandbox
WORKDIR /sandbox

LABEL harness.base="nemoclaw-openclaw-sandbox" \
      harness.contract="harbor-trial-v1" \
      harness.agent="openclaw" \
      harness.vss-cli="true" \
      harness.vss-plugin="true" \
      harness.workspace-root="/sandbox"
