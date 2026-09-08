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

# NemoClaw v0.0.114 managed OpenClaw runtime: OpenClaw 2026.7.1, node 22.23.2,
# python 3.13, git, the nemoclaw plugin, nemoclaw-start entrypoint. v0.0.114 is
# the NEMOCLAW_INSTALL_REF deploy/docker/scripts/deploy_nemoclaw.ipynb installs,
# and NemoClaw requires the CLI, base image and plugin OpenClaw to be one release.
# Pinned by digest — a mutable tag with IfNotPresent silently serves whatever
# the node cached first. Move it deliberately, together with OPENCLAW_VERSION
# and the notebook's NEMOCLAW_INSTALL_REF.
ARG BASE_IMAGE=ghcr.io/nvidia/nemoclaw/openclaw-sandbox@sha256:25f400dee9bcd6b109aa3ab00dfda93e452159db0338530a4e6858f18fbc579a
# The OpenClaw the base carries. The plugin's devDependency must match it, so
# the compiled plugin links against the runtime it will actually load in.
ARG OPENCLAW_VERSION=2026.7.1
# Same builder image NemoClaw's Dockerfile uses for its plugin stages.
ARG BUILDER_IMAGE=node:22-trixie-slim@sha256:db8a96a63e5264607ada2d206758876ebbed6a12be2ada7517793cbfb0c2a29c
ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.12.10
# Skills and the vss CLI: one pinned commit of this repo, so an eval can say
# exactly which it measured. Must be a commit whose operation skills declare
# `vss-requires` in their SKILL.md frontmatter (staging fails loudly otherwise).
# Override with --build-arg VSS_REF=<sha>; bump the default when the skills move.
ARG VSS_REPO=https://github.com/NVIDIA-AI-Blueprints/video-search-and-summarization
ARG VSS_REF=6faf253dde917cd7e6aa71dcc1651654a55299ca

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
# Content assets: the operation skills — every skill in the pinned checkout whose
# SKILL.md frontmatter declares `vss-requires` — and the OpenClaw
# workspace instructions from this directory, staged under the plugin root.
# skills/ is the full set; the manifest points at skills-active/, which the
# plugin fills from the deployment the vss CLI recorded (see plugin/src/sync.ts).
# At build every listed skill is active, so a sandbox that has not run
# `vss configure` yet still has them all.
COPY --from=vss-src /opt/vss-src/skills /tmp/vss-skills
COPY workspace/ /tmp/vss-workspace/
RUN ./stage-assets.sh /tmp/vss-skills /tmp/vss-workspace \
    && rm -rf /tmp/vss-skills /tmp/vss-workspace \
    && node dist/sync.js --all --plugin-dir . \
    && test "$(ls skills-active | wc -l)" = "$(ls skills | wc -l)"

# ── uv, for the vss CLI venv ───────────────────────────────────────────────────
FROM ${UV_IMAGE} AS uv

# ── Managed runtime + vss CLI ──────────────────────────────────────────────────
FROM ${BASE_IMAGE} AS nemoclaw-runtime
USER root

# Harbor trial paths. Harbor and its agent adapters address /task, /output,
# /logs, /tests and /solution by name. They are REAL directories, not symlinks
# into /sandbox: the OpenShell policy engine chowns every read_write path at
# sandbox start and refuses a symlink ("read_write path '/task' is a symlink —
# refusing to chown, potential privilege escalation"), which failed every trial
# before the agent started. World-writable like the openshell-community image,
# so the harness can populate them as the sandbox user; the eval harness moves
# files in and out with exec+tar, so they do not need to live in the workspace.
RUN for d in task output logs tests solution; do mkdir -p "/$d" && chmod 777 "/$d"; done

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
    && su -s /bin/sh sandbox -c "vss --version"

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
COPY --from=vss-plugin-builder --chown=sandbox:sandbox /opt/vss-plugin/skills-active/ /opt/vss-plugin/skills-active/
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
    && test -d /sandbox/.openclaw/extensions/vss/skills-active \
    && test -f /sandbox/.openclaw/extensions/vss/workspace/AGENTS.md

# `vss-openclaw-sync`: re-select the active skills from what `vss configure`
# recorded, for operators and harnesses that configure the deployment after the
# gateway is up. The plugin runs the same selection itself at register time.
USER root
RUN printf '%s\n' '#!/bin/sh' 'exec node /sandbox/.openclaw/extensions/vss/dist/sync.js "$@"' > /usr/local/bin/vss-openclaw-sync \
    && chmod 755 /usr/local/bin/vss-openclaw-sync

# Enabling the plugin changes openclaw.json after the managed runtime hashed it;
# nemoclaw-start verifies that hash, so refresh it.
#
# Harbor (the eval runner) OWNS ~/.openclaw/openclaw.json at trial time: its
# openclaw adapter writes a fresh config from its own baseline plus an overlay
# and copies it over this file. Anything this image needs in that config must
# therefore be published where the harness can find it, not only baked here.
# /etc/openclaw-harness/config-overlay.json carries exactly that: the plugins
# block (nemoclaw + vss enabled) lifted from the config built above. The eval
# harness deep-merges it into Harbor's config inside the sandbox, so the plugin,
# its skills and workspace files load under Harbor's config too. The hash below
# is left unlocked (mutable default) for the same reason: two writers, one file.
USER root
RUN chown sandbox:sandbox /sandbox/.openclaw/openclaw.json \
    && chmod 660 /sandbox/.openclaw/openclaw.json \
    && sha256sum /sandbox/.openclaw/openclaw.json > /sandbox/.openclaw/.config-hash \
    && chown sandbox:sandbox /sandbox/.openclaw/.config-hash \
    && chmod 660 /sandbox/.openclaw/.config-hash \
    && mkdir -p /etc/openclaw-harness \
    && node -e 'const c=JSON.parse(require("fs").readFileSync("/sandbox/.openclaw/openclaw.json","utf8")); \
                const o={plugins:c.plugins||{}}; \
                require("fs").writeFileSync("/etc/openclaw-harness/config-overlay.json", JSON.stringify(o,null,2)+"\n");' \
    && chmod 644 /etc/openclaw-harness/config-overlay.json \
    && node -e 'const o=JSON.parse(require("fs").readFileSync("/etc/openclaw-harness/config-overlay.json","utf8")); if(!o.plugins.entries.vss.enabled) process.exit(1)'

# Workspace-variant selector for the plugin's register-time seeding: apply the
# _nemoclaw overlay (ENV.md, sandbox host alias, proxy notes) in this image.
ENV VSS_WORKSPACE_VARIANT=nemoclaw

# NemoClaw's shell hooks apply a hard `ulimit -u 512` in every shell
# (/etc/profile.d/nemoclaw-rlimits.sh and /etc/bash.bashrc, sourcing
# /usr/local/lib/nemoclaw/sandbox-rlimits.sh). That is harmless when the shell
# runs as the `sandbox` user, which owns a handful of processes. The OpenShell
# k8s driver runs the sandbox container as uid 0, and RLIMIT_NPROC for root
# counts every root-owned process on the node, thousands on a k3s host, so the
# first fork in the upload's ssh session fails with EAGAIN and the sandbox dies
# (exit 254) before Harbor's first exec. Process limits for eval sandboxes are
# the cluster's job (pod pids limits), not the shell's.
USER root
RUN rm -f /etc/profile.d/nemoclaw-rlimits.sh \
    && sed -i '/sandbox-rlimits/d; /NemoClaw sandbox resource limits/d' /etc/bash.bashrc \
    && ! grep -rq "sandbox-rlimits" /etc/profile.d /etc/bash.bashrc

# nvm shim. Harbor's openclaw adapter bootstraps node with nvm (`. ~/.nvm/nvm.sh
# && nvm use 22 && node -v`); this image pins node, so the shim answers `use`
# and `install` for the pinned major and refuses any other, exactly as the
# openshell-community image did. Without it every trial fails at agent setup
# with "/sandbox/.nvm/nvm.sh: No such file or directory".
# (Kaniko does not support heredocs in RUN; the shim ships as a context file.)
COPY --chown=sandbox:sandbox nvm-shim.sh /sandbox/.nvm/nvm.sh

# Final USER is the non-root default OpenShell requires. WORKDIR /sandbox makes
# /sandbox the OpenShell workspace (OpenShell derives it from the OCI WorkingDir).
# Entrypoint and health check are inherited from the managed runtime. The command
# is not: the runtime's CMD is /bin/bash, which reads EOF and exits as soon as
# nemoclaw-start hands over when the container has no TTY, so an OpenShell sandbox
# built from this image went to phase Error before the first exec. The eval
# harness drives the agent with exec, so the container only has to stay alive.
USER sandbox
WORKDIR /sandbox
CMD ["sleep", "infinity"]

LABEL harness.base="nemoclaw-openclaw-sandbox" \
      harness.contract="harbor-trial-v1" \
      harness.agent="openclaw" \
      harness.vss-cli="true" \
      harness.vss-plugin="true" \
      harness.workspace-root="/sandbox"
